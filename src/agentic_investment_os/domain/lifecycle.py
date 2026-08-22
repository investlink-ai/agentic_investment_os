"""Define immutable lifecycle values and the authoritative ledger port."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol, TypeGuard, assert_never

__all__ = (
    "AdvanceAttempt",
    "AdvanceCommand",
    "AdvanceFailureReason",
    "AdvanceReceipt",
    "AdvanceRequest",
    "AppendLifecycleRecord",
    "AppendTerminalLifecycleRecord",
    "DurableAdvanceConflict",
    "DurableAdvanceRefusal",
    "IdempotencyKey",
    "InputRefusal",
    "InvalidLifecycleStateError",
    "LifecycleCommand",
    "LifecycleDecision",
    "LifecycleEvent",
    "LifecycleEventKind",
    "LifecycleHistory",
    "LifecycleLedger",
    "LifecyclePersistenceError",
    "LifecyclePhase",
    "LifecycleRecord",
    "LifecycleStatus",
    "LifecycleStatusProjection",
    "PinnedRunIdentity",
    "decide_advance",
    "decide_invalid_history",
    "decide_terminal_refusal",
    "derive_lifecycle_status",
    "is_sha256",
)

_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INVALID_ADVANCED_RECEIPT = "advanced receipt requires completed recovery facts"
_INVALID_FAILED_RECEIPT = "failed receipt requires one bounded reason"
_CHANGED_PINNED_FACTS = "lifecycle stream changed pinned request facts"
_INVALID_REFUSAL_ASSOCIATION = "lifecycle stream has invalid refusal association"
_INVALID_CONFLICT_ASSOCIATION = "lifecycle stream has invalid conflict association"
_INVALID_REFUSAL_ORDER = "lifecycle refusal order is invalid"
_INVALID_REFUSAL_UNIQUENESS = "lifecycle refusal uniqueness is invalid"
_INVALID_UNKEYED_REASON = "unkeyed lifecycle refusal reason is invalid"
_INVALID_CONFLICT_UNIQUENESS = "lifecycle conflict uniqueness is invalid"
_UNSUPPORTED_CONFIGURATION_VERSION = "unsupported configuration_version in lifecycle ledger"
_INVALID_DERIVED_IDENTITY = "lifecycle stream derived identity is invalid"
_UNSUPPORTED_LATER_PHASES = "lifecycle stream contains unsupported later phases"
_NONCONTIGUOUS_SEQUENCE = "lifecycle stream sequence is not contiguous"
_INVALID_CHECKPOINT_ORDER = "lifecycle stream checkpoint order is invalid"


class LifecyclePersistenceError(RuntimeError):
    """Report that a durable checkpoint could not be written or reconstructed."""


class InvalidLifecycleStateError(LifecyclePersistenceError):
    """Report that durable lifecycle rows cannot reconstruct a valid state."""


class SessionMode(StrEnum):
    """Identify the only production lifecycle authority available in Stage 1."""

    CHAMPION = "champion"


class LifecyclePhase(StrEnum):
    """Name lifecycle checkpoints implemented by the Stage 1 slice."""

    RECONCILE_PRIOR_STATE = "ReconcilePriorState"
    PIN_RUN_INPUTS = "PinRunInputs"


class LifecycleEventKind(StrEnum):
    """Preserve the durable Stage 1 event representation."""

    ADVANCE_REQUESTED = "advance_requested"
    PHASE_COMPLETED = "phase_completed"
    RUN_INPUTS_PINNED = "run_inputs_pinned"


class LifecycleLiveness(StrEnum):
    """Classify whether authoritative history can continue safely."""

    NOT_STARTED = "not_started"
    ACTIVE = "active"
    FAILED_CLOSED = "failed_closed"


class AdvanceDisposition(StrEnum):
    """Describe an operator-visible Advance outcome."""

    ADVANCED = "advanced"
    FAILED_CLOSED = "failed_closed"


class AdvanceRecovery(StrEnum):
    """Identify how the current call reached its durable completed checkpoint."""

    FRESH = "fresh"
    RESUMED = "resumed"
    PREVIOUSLY_COMPLETED = "previously_completed"


class InputRefusalCode(StrEnum):
    """Classify hostile or incomplete Advance arguments without retaining their values."""

    INVALID_SESSION = "invalid_session"
    INVALID_MODE = "invalid_mode"
    INVALID_IDEMPOTENCY_KEY = "invalid_idempotency_key"


class AdvanceFailureReason(StrEnum):
    """Bound every durable fail-closed receipt to a known lifecycle reason."""

    INVALID_SESSION = "invalid_session"
    INVALID_MODE = "invalid_mode"
    INVALID_IDEMPOTENCY_KEY = "invalid_idempotency_key"
    SESSION_STREAM_CONFLICT = "session_stream_conflict"
    IDEMPOTENCY_KEY_CONFLICT = "idempotency_key_conflict"
    INVALID_DURABLE_STATE = "invalid_durable_state"


@dataclass(frozen=True, slots=True)
class MarketSession:
    """Represent one canonical NYSE trading date without inferring calendar eligibility."""

    trading_date: date

    def isoformat(self) -> str:
        return self.trading_date.isoformat()


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    """Carry a validated stable key for one operator request."""

    value: str

    @classmethod
    def parse(cls, value: object) -> IdempotencyKey | None:
        """Validate an untrusted stable request key without retaining invalid input."""
        if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
            return None
        return cls(value)


@dataclass(frozen=True, slots=True)
class InputRefusal:
    """Return a bounded validation reason and any independently valid request key."""

    code: InputRefusalCode
    idempotency_key: IdempotencyKey | None


@dataclass(frozen=True, slots=True)
class AdvanceRequest:
    """Carry validated untrusted arguments into lifecycle orchestration."""

    session: MarketSession
    mode: SessionMode
    idempotency_key: IdempotencyKey

    @classmethod
    def parse(
        cls,
        *,
        session: object,
        mode: object,
        idempotency_key: object,
    ) -> AdvanceRequest | InputRefusal:
        key = _parse_idempotency_key(idempotency_key)
        if key is None:
            return InputRefusal(InputRefusalCode.INVALID_IDEMPOTENCY_KEY, None)
        parsed_session = _parse_market_session(session)
        if parsed_session is None:
            return InputRefusal(InputRefusalCode.INVALID_SESSION, key)
        if mode != SessionMode.CHAMPION.value:
            return InputRefusal(InputRefusalCode.INVALID_MODE, key)
        return cls(parsed_session, SessionMode.CHAMPION, key)

    @property
    def stream_id(self) -> str:
        return _fingerprint((self.mode.value, self.session.isoformat()))


@dataclass(frozen=True, slots=True)
class PinnedRunIdentity:
    """Identify the exact session and non-secret configuration pinned for a run."""

    run_id: str
    configuration_version: int
    configuration_hash: str

    @classmethod
    def create(
        cls,
        request: AdvanceRequest,
        *,
        configuration_version: int,
        configuration_hash: str,
    ) -> PinnedRunIdentity:
        return cls(
            run_id=_fingerprint(
                (
                    configuration_hash,
                    configuration_version,
                    request.mode.value,
                    request.session.isoformat(),
                )
            ),
            configuration_version=configuration_version,
            configuration_hash=configuration_hash,
        )


@dataclass(frozen=True, slots=True)
class AdvanceReceipt:
    """Report durable lifecycle facts and how this call observed their completion."""

    disposition: AdvanceDisposition
    completed_phase: LifecyclePhase | None
    pinned_run_identity: PinnedRunIdentity | None
    failure_reason: AdvanceFailureReason | None
    recovery: AdvanceRecovery | None = None

    def __post_init__(self) -> None:
        if self.disposition is AdvanceDisposition.ADVANCED:
            if (
                self.completed_phase is not LifecyclePhase.PIN_RUN_INPUTS
                or self.pinned_run_identity is None
                or self.failure_reason is not None
                or self.recovery is None
            ):
                raise ValueError(_INVALID_ADVANCED_RECEIPT)
            return
        if self.disposition is AdvanceDisposition.FAILED_CLOSED:
            if (
                self.completed_phase is not None
                or self.pinned_run_identity is not None
                or self.failure_reason is None
                or self.recovery is not None
            ):
                raise ValueError(_INVALID_FAILED_RECEIPT)
            return
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(self.disposition)  # pragma: no cover  # pragma: no mutate

    @classmethod
    def advanced(
        cls,
        identity: PinnedRunIdentity,
        recovery: AdvanceRecovery,
    ) -> AdvanceReceipt:
        return cls(
            AdvanceDisposition.ADVANCED,
            LifecyclePhase.PIN_RUN_INPUTS,
            identity,
            None,
            recovery,
        )

    @classmethod
    def failed_closed(cls, reason: AdvanceFailureReason) -> AdvanceReceipt:
        return cls(AdvanceDisposition.FAILED_CLOSED, None, None, reason)


@dataclass(frozen=True, slots=True)
class LifecycleProgress:
    """Represent a validated partial or complete authoritative stream."""

    request: AdvanceRequest
    pinned_run_identity: PinnedRunIdentity
    completed_phase: LifecyclePhase | None
    sequence: int

    @property
    def is_complete(self) -> bool:
        phase = self.completed_phase
        if phase is None:
            return False
        if phase is LifecyclePhase.RECONCILE_PRIOR_STATE:
            return False
        if phase is LifecyclePhase.PIN_RUN_INPUTS:
            return True
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(phase)  # pragma: no cover  # pragma: no mutate


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """Carry one typed append-only lifecycle checkpoint."""

    stream_id: str
    sequence: int
    request: AdvanceRequest
    pinned_run_identity: PinnedRunIdentity
    event_kind: LifecycleEventKind
    completed_phase: LifecyclePhase | None


@dataclass(frozen=True, slots=True)
class AdvanceCommand:
    """Request one deterministic lifecycle transition for validated inputs."""

    request: AdvanceRequest
    pinned_run_identity: PinnedRunIdentity


@dataclass(frozen=True, slots=True)
class AdvanceAttempt:
    """Track only progress observed or appended during one Advance call."""

    recovery: AdvanceRecovery | None = None
    last_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class LifecycleStatus:
    """Report operator-visible facts rebuilt only from authoritative lifecycle history."""

    active_phase: LifecyclePhase | None
    last_completed_session: MarketSession | None
    pinned_run_identity: PinnedRunIdentity | None
    liveness: LifecycleLiveness
    durable_reason: AdvanceFailureReason | None

    @classmethod
    def not_started(cls) -> LifecycleStatus:
        return cls(None, None, None, LifecycleLiveness.NOT_STARTED, None)


@dataclass(frozen=True, slots=True)
class DurableAdvanceRefusal:
    """Represent one validated refusal reconstructed in durable append order."""

    sequence: int
    idempotency_key: IdempotencyKey | None
    reason: AdvanceFailureReason


@dataclass(frozen=True, slots=True)
class DurableAdvanceConflict:
    """Represent one validated conflict against a completed request stream."""

    idempotency_key: IdempotencyKey
    reason: AdvanceFailureReason


LifecycleRecord = LifecycleEvent | DurableAdvanceRefusal | DurableAdvanceConflict
LifecycleCommand = AdvanceCommand | InputRefusal


@dataclass(frozen=True, slots=True)
class LifecycleHistory:
    """Contain typed authoritative records in their durable append order."""

    events: tuple[LifecycleEvent, ...] = ()
    refusals: tuple[DurableAdvanceRefusal, ...] = ()
    conflicts: tuple[DurableAdvanceConflict, ...] = ()
    occupied_stream_ids: frozenset[str] = frozenset()
    next_refusal_sequence: int | None = None

    def append(self, record: LifecycleRecord) -> LifecycleHistory:
        """Return history with one record appended to its owning ledger."""
        if isinstance(record, LifecycleEvent):
            return LifecycleHistory(
                events=(*self.events, record),
                refusals=self.refusals,
                conflicts=self.conflicts,
                occupied_stream_ids=self.occupied_stream_ids | {record.stream_id},
                next_refusal_sequence=self.next_refusal_sequence,
            )
        if isinstance(record, DurableAdvanceRefusal):
            return LifecycleHistory(
                events=self.events,
                refusals=(*self.refusals, record),
                conflicts=self.conflicts,
                occupied_stream_ids=self.occupied_stream_ids,
                next_refusal_sequence=record.sequence + 1,
            )
        if isinstance(record, DurableAdvanceConflict):
            return LifecycleHistory(
                events=self.events,
                refusals=self.refusals,
                conflicts=(*self.conflicts, record),
                occupied_stream_ids=self.occupied_stream_ids,
                next_refusal_sequence=self.next_refusal_sequence,
            )
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(record)  # pragma: no cover  # pragma: no mutate


@dataclass(frozen=True, slots=True)
class AppendLifecycleRecord:
    """Direct persistence to append one record before the next transition decision."""

    record: LifecycleRecord
    attempt: AdvanceAttempt


@dataclass(frozen=True, slots=True)
class AppendTerminalLifecycleRecord:
    """Direct persistence to append one record and return its terminal receipt."""

    record: LifecycleRecord
    receipt: AdvanceReceipt


LifecycleDecision = AppendLifecycleRecord | AppendTerminalLifecycleRecord | AdvanceReceipt


_EVENT_SEQUENCE = (
    (LifecycleEventKind.ADVANCE_REQUESTED, None),
    (LifecycleEventKind.PHASE_COMPLETED, LifecyclePhase.RECONCILE_PRIOR_STATE),
    (LifecycleEventKind.RUN_INPUTS_PINNED, LifecyclePhase.PIN_RUN_INPUTS),
)


def decide_advance(
    history: LifecycleHistory,
    command: LifecycleCommand,
    attempt: AdvanceAttempt,
) -> LifecycleDecision:
    """Reconstruct authoritative history and choose one durable transition or receipt."""
    terminal = decide_terminal_refusal(history.refusals, command)
    if terminal is not None:
        return terminal
    try:
        progresses = reconstruct_lifecycle(history)
    except InvalidLifecycleStateError:
        return _invalid_history_decision(history, command)

    progress_by_key = {progress.request.idempotency_key.value: progress for progress in progresses}
    if isinstance(command, InputRefusal):
        return _decide_input_refusal(history, progress_by_key, command)
    if isinstance(command, AdvanceCommand):
        return _decide_valid_advance(history, progresses, progress_by_key, command, attempt)
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(command)  # pragma: no cover  # pragma: no mutate


def decide_terminal_refusal(
    refusals: tuple[DurableAdvanceRefusal, ...],
    command: LifecycleCommand,
) -> AdvanceReceipt | None:
    """Return a previously durable terminal refusal without reading unrelated history."""
    if isinstance(command, AdvanceCommand):
        request_key = command.request.idempotency_key
        refusal = next(
            (item for item in refusals if item.idempotency_key == request_key),
            None,
        )
        return None if refusal is None else AdvanceReceipt.failed_closed(refusal.reason)
    if isinstance(command, InputRefusal):
        refusal_key = command.idempotency_key
        if refusal_key is not None:
            refusal = next(
                (item for item in refusals if item.idempotency_key == refusal_key),
                None,
            )
            return None if refusal is None else AdvanceReceipt.failed_closed(refusal.reason)
        reason = AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY
        refusal = next(
            (item for item in refusals if item.idempotency_key is None and item.reason is reason),
            None,
        )
        return None if refusal is None else AdvanceReceipt.failed_closed(refusal.reason)
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(command)  # pragma: no cover  # pragma: no mutate


def decide_invalid_history(
    refusals: tuple[DurableAdvanceRefusal, ...],
    command: LifecycleCommand,
    *,
    next_refusal_sequence: int | None = None,
) -> LifecycleDecision:
    """Fail closed after boundary validation cannot produce typed authoritative history."""
    terminal = decide_terminal_refusal(refusals, command)
    if terminal is not None:
        return terminal
    history = LifecycleHistory(next_refusal_sequence=next_refusal_sequence)
    if isinstance(command, InputRefusal):
        if command.idempotency_key is None:
            return _append_refusal(
                history,
                None,
                AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
            )
        return _append_refusal(
            history,
            command.idempotency_key,
            AdvanceFailureReason.INVALID_DURABLE_STATE,
        )
    if isinstance(command, AdvanceCommand):
        return _append_refusal(
            history,
            command.request.idempotency_key,
            AdvanceFailureReason.INVALID_DURABLE_STATE,
        )
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(command)  # pragma: no cover  # pragma: no mutate


def reconstruct_lifecycle(history: LifecycleHistory) -> tuple[LifecycleProgress, ...]:
    """Validate typed lifecycle history and rebuild every request stream."""
    streams: dict[str, list[LifecycleEvent]] = {}
    for event in history.events:
        streams.setdefault(event.stream_id, []).append(event)

    progresses = tuple(_reconstruct_stream(tuple(events)) for events in streams.values())
    progress_by_key: dict[str, LifecycleProgress] = {}
    for progress in progresses:
        key = progress.request.idempotency_key.value
        if key in progress_by_key:
            raise InvalidLifecycleStateError(_CHANGED_PINNED_FACTS)
        progress_by_key[key] = progress

    _validate_refusals(history.refusals, progress_by_key)
    _validate_conflicts(history.conflicts, progress_by_key)
    return progresses


def _validate_refusals(
    refusals: tuple[DurableAdvanceRefusal, ...],
    progress_by_key: dict[str, LifecycleProgress],
) -> None:
    keyed_refusals: set[str] = set()
    unkeyed_reasons: set[AdvanceFailureReason] = set()
    for expected_sequence, refusal in enumerate(refusals, start=1):
        if refusal.sequence != expected_sequence:
            raise InvalidLifecycleStateError(_INVALID_REFUSAL_ORDER)
        key = refusal.idempotency_key
        if (key is None) != (refusal.reason is AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY):
            raise InvalidLifecycleStateError(_INVALID_UNKEYED_REASON)
        if key is None:
            if refusal.reason in unkeyed_reasons:
                raise InvalidLifecycleStateError(_INVALID_REFUSAL_UNIQUENESS)
            unkeyed_reasons.add(refusal.reason)
            continue
        if key.value in keyed_refusals:
            raise InvalidLifecycleStateError(_INVALID_REFUSAL_UNIQUENESS)
        keyed_refusals.add(key.value)
        associated_progress = progress_by_key.get(key.value)
        if associated_progress is None and refusal.reason in (
            AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
            AdvanceFailureReason.INVALID_DURABLE_STATE,
        ):
            raise InvalidLifecycleStateError(_INVALID_REFUSAL_ASSOCIATION)
        if associated_progress is not None and (
            associated_progress.is_complete
            or refusal.reason is not AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
        ):
            raise InvalidLifecycleStateError(_INVALID_REFUSAL_ASSOCIATION)


def _validate_conflicts(
    conflicts: tuple[DurableAdvanceConflict, ...],
    progress_by_key: dict[str, LifecycleProgress],
) -> None:
    conflict_keys: set[str] = set()
    for conflict in conflicts:
        key = conflict.idempotency_key.value
        if key in conflict_keys:
            raise InvalidLifecycleStateError(_INVALID_CONFLICT_UNIQUENESS)
        associated_progress = progress_by_key.get(key)
        if (
            conflict.reason is not AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
            or associated_progress is None
            or not associated_progress.is_complete
        ):
            raise InvalidLifecycleStateError(_INVALID_CONFLICT_ASSOCIATION)
        conflict_keys.add(key)


def _reconstruct_stream(events: tuple[LifecycleEvent, ...]) -> LifecycleProgress:
    first = events[0]
    request = first.request
    identity = first.pinned_run_identity
    expected_identity = PinnedRunIdentity.create(
        request,
        configuration_version=identity.configuration_version,
        configuration_hash=identity.configuration_hash,
    )
    if identity.configuration_version != 1:
        raise InvalidLifecycleStateError(_UNSUPPORTED_CONFIGURATION_VERSION)
    if first.stream_id != request.stream_id or identity != expected_identity:
        raise InvalidLifecycleStateError(_INVALID_DERIVED_IDENTITY)
    if len(events) > len(_EVENT_SEQUENCE):
        raise InvalidLifecycleStateError(_UNSUPPORTED_LATER_PHASES)
    for sequence, event in enumerate(events):
        if event.sequence != sequence:
            raise InvalidLifecycleStateError(_NONCONTIGUOUS_SEQUENCE)
        if (
            event.stream_id != first.stream_id
            or event.request != request
            or event.pinned_run_identity != identity
        ):
            raise InvalidLifecycleStateError(_CHANGED_PINNED_FACTS)
        if (event.event_kind, event.completed_phase) != _EVENT_SEQUENCE[sequence]:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
    return LifecycleProgress(request, identity, events[-1].completed_phase, len(events) - 1)


def _decide_valid_advance(
    history: LifecycleHistory,
    progresses: tuple[LifecycleProgress, ...],
    progress_by_key: dict[str, LifecycleProgress],
    command: AdvanceCommand,
    attempt: AdvanceAttempt,
) -> LifecycleDecision:
    request = command.request
    key = request.idempotency_key
    progress = progress_by_key.get(key.value)
    if progress is None:
        return _decide_new_stream(history, progresses, command, attempt)
    if progress.request != request or progress.pinned_run_identity != command.pinned_run_identity:
        return _decide_idempotency_conflict(history, progress, key)
    recovery = _recovery_for_progress(progress, attempt)
    if progress.is_complete:
        return AdvanceReceipt.advanced(
            progress.pinned_run_identity,
            AdvanceRecovery.PREVIOUSLY_COMPLETED
            if progress.sequence != attempt.last_sequence
            else recovery,
        )
    return _append_next_event(progress, command, recovery)


def _decide_new_stream(
    history: LifecycleHistory,
    progresses: tuple[LifecycleProgress, ...],
    command: AdvanceCommand,
    attempt: AdvanceAttempt,
) -> LifecycleDecision:
    request = command.request
    key = request.idempotency_key
    if attempt.recovery is not None:
        return _append_refusal(
            history,
            key,
            AdvanceFailureReason.INVALID_DURABLE_STATE,
        )
    if request.stream_id in history.occupied_stream_ids or any(
        existing.request.stream_id == request.stream_id for existing in progresses
    ):
        return _append_refusal(
            history,
            key,
            AdvanceFailureReason.SESSION_STREAM_CONFLICT,
        )
    record = LifecycleEvent(
        stream_id=request.stream_id,
        sequence=0,
        request=request,
        pinned_run_identity=command.pinned_run_identity,
        event_kind=LifecycleEventKind.ADVANCE_REQUESTED,
        completed_phase=None,
    )
    return AppendLifecycleRecord(
        record,
        AdvanceAttempt(AdvanceRecovery.FRESH, record.sequence),
    )


def _decide_input_refusal(
    history: LifecycleHistory,
    progress_by_key: dict[str, LifecycleProgress],
    refusal: InputRefusal,
) -> LifecycleDecision:
    key = refusal.idempotency_key
    reason = AdvanceFailureReason(refusal.code.value)
    if key is None:
        return _append_refusal(history, None, reason)
    progress = progress_by_key.get(key.value)
    if progress is None:
        return _append_refusal(history, key, reason)
    return _decide_idempotency_conflict(history, progress, key)


def _decide_idempotency_conflict(
    history: LifecycleHistory,
    progress: LifecycleProgress,
    key: IdempotencyKey,
) -> LifecycleDecision:
    reason = AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
    if not progress.is_complete:
        return _append_refusal(history, key, reason)
    conflict = next(
        (item for item in history.conflicts if item.idempotency_key == key),
        None,
    )
    if conflict is not None:
        return AdvanceReceipt.failed_closed(conflict.reason)
    return AppendTerminalLifecycleRecord(
        DurableAdvanceConflict(key, reason),
        AdvanceReceipt.failed_closed(reason),
    )


def _append_next_event(
    progress: LifecycleProgress,
    command: AdvanceCommand,
    recovery: AdvanceRecovery,
) -> AppendLifecycleRecord | AppendTerminalLifecycleRecord:
    sequence = progress.sequence + 1
    event_kind, phase = _EVENT_SEQUENCE[sequence]
    event = LifecycleEvent(
        stream_id=command.request.stream_id,
        sequence=sequence,
        request=command.request,
        pinned_run_identity=command.pinned_run_identity,
        event_kind=event_kind,
        completed_phase=phase,
    )
    next_attempt = AdvanceAttempt(recovery, sequence)
    # Existing progress contains event zero, so its next phase cannot be absent.
    if phase is None:  # pragma: no cover
        return AppendLifecycleRecord(event, next_attempt)  # pragma: no mutate
    if phase is LifecyclePhase.RECONCILE_PRIOR_STATE:
        return AppendLifecycleRecord(event, next_attempt)
    if phase is LifecyclePhase.PIN_RUN_INPUTS:
        return AppendTerminalLifecycleRecord(
            event,
            AdvanceReceipt.advanced(command.pinned_run_identity, recovery),
        )
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(phase)  # pragma: no cover  # pragma: no mutate


def _append_refusal(
    history: LifecycleHistory,
    key: IdempotencyKey | None,
    reason: AdvanceFailureReason,
) -> AppendTerminalLifecycleRecord:
    receipt = AdvanceReceipt.failed_closed(reason)
    sequence = (
        len(history.refusals) + 1
        if history.next_refusal_sequence is None
        else history.next_refusal_sequence
    )
    return AppendTerminalLifecycleRecord(
        DurableAdvanceRefusal(sequence, key, reason),
        receipt,
    )


def _invalid_history_decision(
    history: LifecycleHistory,
    command: LifecycleCommand,
) -> LifecycleDecision:
    return decide_invalid_history(
        history.refusals,
        command,
        next_refusal_sequence=history.next_refusal_sequence,
    )


def _recovery_for_progress(
    progress: LifecycleProgress,
    attempt: AdvanceAttempt,
) -> AdvanceRecovery:
    if attempt.recovery is None:
        return AdvanceRecovery.RESUMED
    if attempt.last_sequence is None or progress.sequence > attempt.last_sequence:
        return AdvanceRecovery.RESUMED
    return attempt.recovery


def derive_lifecycle_status(history: LifecycleHistory) -> LifecycleStatus:
    """Validate authoritative history and derive its operator projection."""
    progresses = reconstruct_lifecycle(history)
    if not progresses:
        if not history.refusals and not history.conflicts:
            return LifecycleStatus.not_started()
        return LifecycleStatus(
            active_phase=None,
            last_completed_session=None,
            pinned_run_identity=None,
            liveness=LifecycleLiveness.FAILED_CLOSED,
            durable_reason=history.refusals[-1].reason,
        )

    current = max(
        progresses,
        key=lambda progress: progress.request.session.trading_date,
    )
    matching_refusal = next(
        (
            refusal
            for refusal in reversed(history.refusals)
            if refusal.idempotency_key == current.request.idempotency_key
        ),
        None,
    )
    if matching_refusal is not None:
        return LifecycleStatus(
            active_phase=None,
            last_completed_session=None,
            pinned_run_identity=current.pinned_run_identity,
            liveness=LifecycleLiveness.FAILED_CLOSED,
            durable_reason=matching_refusal.reason,
        )

    active_phase = None if current.is_complete else _EVENT_SEQUENCE[current.sequence + 1][1]
    return LifecycleStatus(
        active_phase=active_phase,
        last_completed_session=None,
        pinned_run_identity=current.pinned_run_identity,
        liveness=LifecycleLiveness.ACTIVE,
        durable_reason=_reported_reason(
            refusals=history.refusals,
            conflicts=history.conflicts,
        ),
    )


def _reported_reason(
    *,
    refusals: tuple[DurableAdvanceRefusal, ...],
    conflicts: tuple[DurableAdvanceConflict, ...],
) -> AdvanceFailureReason | None:
    if refusals:
        return refusals[-1].reason
    return conflicts[-1].reason if conflicts else None


class LifecycleLedger(Protocol):
    """Apply one domain-selected lifecycle step in an atomic append transaction."""

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        recorded_at: datetime,
    ) -> LifecycleDecision: ...


class LifecycleStatusProjection(Protocol):
    """Rebuild the disposable operator projection from authoritative history."""

    def rebuild_status(self) -> LifecycleStatus: ...


def is_sha256(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _parse_idempotency_key(value: object) -> IdempotencyKey | None:
    return IdempotencyKey.parse(value)


def _parse_market_session(value: object) -> MarketSession | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if parsed.isoformat() != value:
        return None
    return MarketSession(parsed)


def _fingerprint(values: tuple[str | int, ...]) -> str:
    encoded = json.dumps(values).encode()
    return hashlib.sha256(encoded).hexdigest()
