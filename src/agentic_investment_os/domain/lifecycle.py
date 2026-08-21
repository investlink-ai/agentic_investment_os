"""Define immutable lifecycle values and the authoritative ledger port."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol, TypeGuard

_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INVALID_ADVANCED_RECEIPT = "advanced receipt requires completed recovery facts"
_INVALID_FAILED_RECEIPT = "failed receipt requires one bounded reason"
_CHANGED_PINNED_FACTS = "lifecycle stream changed pinned request facts"
_INVALID_REFUSAL_ASSOCIATION = "lifecycle stream has invalid refusal association"
_INVALID_CONFLICT_ASSOCIATION = "lifecycle stream has invalid conflict association"


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


class CheckpointWrite(StrEnum):
    """Distinguish a checkpoint appended by this call from committed progress it observed."""

    APPENDED = "appended"
    OBSERVED = "observed"


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
        if (
            self.completed_phase is not None
            or self.pinned_run_identity is not None
            or self.failure_reason is None
            or self.recovery is not None
        ):
            raise ValueError(_INVALID_FAILED_RECEIPT)

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
        return self.completed_phase is LifecyclePhase.PIN_RUN_INPUTS

    def receipt(self, recovery: AdvanceRecovery) -> AdvanceReceipt | None:
        if not self.is_complete:
            return None
        return AdvanceReceipt.advanced(self.pinned_run_identity, recovery)


@dataclass(frozen=True, slots=True)
class CheckpointResult:
    """Return committed progress and whether this operation appended its checkpoint."""

    progress: LifecycleProgress
    write: CheckpointWrite


@dataclass(frozen=True, slots=True)
class StreamConflict:
    """Signal that a session already has a different authoritative request stream."""


LifecycleState = LifecycleProgress | AdvanceReceipt | None
StartResult = CheckpointResult | AdvanceReceipt | StreamConflict


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

    idempotency_key: IdempotencyKey | None
    reason: AdvanceFailureReason


@dataclass(frozen=True, slots=True)
class DurableAdvanceConflict:
    """Represent one validated conflict against a completed request stream."""

    idempotency_key: IdempotencyKey
    reason: AdvanceFailureReason


def derive_lifecycle_status(
    progresses: tuple[LifecycleProgress, ...],
    refusals: tuple[DurableAdvanceRefusal, ...],
    conflicts: tuple[DurableAdvanceConflict, ...],
) -> LifecycleStatus:
    """Derive operator status from fully validated authoritative facts."""
    progress_by_key: dict[str, LifecycleProgress] = {}
    for progress in progresses:
        key = progress.request.idempotency_key.value
        if key in progress_by_key:
            raise InvalidLifecycleStateError(_CHANGED_PINNED_FACTS)
        progress_by_key[key] = progress

    for refusal in refusals:
        if refusal.idempotency_key is None:
            continue
        associated_progress = progress_by_key.get(refusal.idempotency_key.value)
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

    for conflict in conflicts:
        associated_progress = progress_by_key.get(conflict.idempotency_key.value)
        if (
            conflict.reason is not AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
            or associated_progress is None
            or not associated_progress.is_complete
        ):
            raise InvalidLifecycleStateError(_INVALID_CONFLICT_ASSOCIATION)

    if not progress_by_key:
        if not refusals and not conflicts:
            return LifecycleStatus.not_started()
        return LifecycleStatus(
            active_phase=None,
            last_completed_session=None,
            pinned_run_identity=None,
            liveness=LifecycleLiveness.FAILED_CLOSED,
            durable_reason=refusals[-1].reason,
        )

    current = max(
        progress_by_key.values(),
        key=lambda progress: progress.request.session.trading_date,
    )
    matching_refusal = next(
        (
            refusal
            for refusal in reversed(refusals)
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

    matching_conflict = next(
        (
            conflict
            for conflict in conflicts
            if conflict.idempotency_key == current.request.idempotency_key
        ),
        None,
    )
    active_phase = {
        None: LifecyclePhase.RECONCILE_PRIOR_STATE,
        LifecyclePhase.RECONCILE_PRIOR_STATE: LifecyclePhase.PIN_RUN_INPUTS,
        LifecyclePhase.PIN_RUN_INPUTS: None,
    }[current.completed_phase]
    return LifecycleStatus(
        active_phase=active_phase,
        last_completed_session=None,
        pinned_run_identity=current.pinned_run_identity,
        liveness=LifecycleLiveness.ACTIVE,
        durable_reason=_reported_reason(
            current_conflict=matching_conflict,
            refusals=refusals,
            conflicts=conflicts,
            progress_by_key=progress_by_key,
        ),
    )


def _reported_reason(
    *,
    current_conflict: DurableAdvanceConflict | None,
    refusals: tuple[DurableAdvanceRefusal, ...],
    conflicts: tuple[DurableAdvanceConflict, ...],
    progress_by_key: dict[str, LifecycleProgress],
) -> AdvanceFailureReason | None:
    if current_conflict is not None:
        return current_conflict.reason
    if refusals:
        return refusals[-1].reason
    if not conflicts:
        return None
    latest_conflict = max(
        conflicts,
        key=lambda conflict: (
            progress_by_key[conflict.idempotency_key.value].request.session.trading_date,
            conflict.idempotency_key.value,
        ),
    )
    return latest_conflict.reason


class LifecycleLedger(Protocol):
    """Append and reconstruct Stage 1 lifecycle checkpoints."""

    def load_by_idempotency_key(self, key: IdempotencyKey) -> LifecycleState: ...

    def start(
        self,
        request: AdvanceRequest,
        identity: PinnedRunIdentity,
        recorded_at: datetime,
    ) -> StartResult: ...

    def complete_reconciliation(
        self, key: IdempotencyKey, recorded_at: datetime
    ) -> CheckpointResult | AdvanceReceipt: ...

    def pin_run_inputs(
        self, key: IdempotencyKey, recorded_at: datetime
    ) -> CheckpointResult | AdvanceReceipt: ...

    def record_refusal(
        self,
        key: IdempotencyKey | None,
        reason_code: AdvanceFailureReason,
        recorded_at: datetime,
    ) -> AdvanceReceipt: ...


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
