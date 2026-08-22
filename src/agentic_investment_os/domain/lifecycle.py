"""Define immutable lifecycle values and the authoritative ledger port."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, TypeGuard, assert_never

from agentic_investment_os.domain.identity import (
    CryptoDecisionWindow,
    DecisionCycleIdentity,
    MarketSession,
    canonical_cycle_bytes,
    parse_decision_cycle_identity,
)
from agentic_investment_os.domain.universe import is_data_regime

if TYPE_CHECKING:
    from agentic_investment_os.domain.universe import UniverseInputIdentity, UniverseSnapshot

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
    "InputRefusalCode",
    "InvalidLifecycleStateError",
    "LifecycleCheckpoint",
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
    "parse_advance_receipt",
    "parse_lifecycle_checkpoint",
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
_INVALID_CHECKPOINT_PHASE = "invalid lifecycle checkpoint phase"
_INVALID_EVENT_TIME = "lifecycle event time must be timezone-aware"
_LIFECYCLE_ENVELOPE_SCHEMA_VERSION = 1
_LIFECYCLE_PAYLOAD_SCHEMA_VERSION = 1
_LIFECYCLE_AUTHORITY_SCOPE = "investment_operating_system"
_CHECKPOINT_FIELDS = frozenset(
    {
        "envelope_schema_version",
        "record_kind",
        "payload_discriminator",
        "payload_schema_version",
        "authority_scope",
        "payload",
        "content_hash",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "envelope_schema_version",
        "record_kind",
        "payload_discriminator",
        "payload_schema_version",
        "cycle",
        "relevant_at",
        "available_at",
        "data_regime",
        "authority_scope",
        "material_fingerprints",
        "payload",
        "content_hash",
    }
)
_RECEIPT_PAYLOAD_FIELDS = frozenset(
    {
        "disposition",
        "completed_checkpoint",
        "pinned_run_identity",
        "failure_reason",
        "recovery",
        "universe_snapshot_id",
    }
)
_PINNED_IDENTITY_FIELDS = frozenset(
    {
        "run_id",
        "cycle",
        "configuration_version",
        "configuration_hash",
        "data_regime",
        "evidence_cutoff",
        "instrument_snapshot_hash",
        "position_snapshot_hash",
        "eligibility_policy_hash",
    }
)
_MATERIAL_FINGERPRINT_FIELDS = frozenset(
    {"configuration", "instrument_snapshot", "position_snapshot", "eligibility_policy"}
)


class LifecyclePersistenceError(RuntimeError):
    """Report that a durable checkpoint could not be written or reconstructed."""


class InvalidLifecycleStateError(LifecyclePersistenceError):
    """Report that durable lifecycle rows cannot reconstruct a valid state."""


class SessionMode(StrEnum):
    """Identify the only implemented production lifecycle authority."""

    CHAMPION = "champion"


class LifecyclePhase(StrEnum):
    """Name lifecycle checkpoints implemented through universe snapshotting."""

    RECONCILE_PRIOR_STATE = "ReconcilePriorState"
    PIN_RUN_INPUTS = "PinRunInputs"
    SNAPSHOT_UNIVERSE = "SnapshotUniverse"


@dataclass(frozen=True, slots=True)
class LifecycleCheckpoint:
    """Wrap one asset-owned lifecycle phase in the stable public checkpoint contract."""

    phase: LifecyclePhase

    def __post_init__(self) -> None:
        if type(self.phase) is not LifecyclePhase:
            raise ValueError(_INVALID_CHECKPOINT_PHASE)

    @classmethod
    def equity(cls, phase: LifecyclePhase) -> LifecycleCheckpoint:
        return cls(phase)

    def to_payload(self) -> dict[str, object]:
        material = {
            "envelope_schema_version": _LIFECYCLE_ENVELOPE_SCHEMA_VERSION,
            "record_kind": "lifecycle_checkpoint",
            "payload_discriminator": "equity_market_session_phase",
            "payload_schema_version": _LIFECYCLE_PAYLOAD_SCHEMA_VERSION,
            "authority_scope": _LIFECYCLE_AUTHORITY_SCOPE,
            "payload": {"phase": self.phase.value},
        }
        return {**material, "content_hash": _content_hash(material)}


def parse_lifecycle_checkpoint(  # noqa: PLR0911, PLR0912 - validate hostile fields separately.
    value: object,
) -> LifecycleCheckpoint | None:
    """Validate one hostile checkpoint envelope before exposing its equity phase."""
    if type(value) is not dict:
        return None
    if any(type(key) is not str for key in value):
        return None
    if set(value) != _CHECKPOINT_FIELDS:
        return None
    if type(value["envelope_schema_version"]) is not int:
        return None
    if type(value["record_kind"]) is not str:
        return None
    if type(value["payload_discriminator"]) is not str:
        return None
    if type(value["payload_schema_version"]) is not int:
        return None
    if type(value["authority_scope"]) is not str:
        return None
    if type(value["content_hash"]) is not str:
        return None
    phase_payload = value["payload"]
    if type(phase_payload) is not dict:
        return None
    if set(phase_payload) != {"phase"}:
        return None
    phase = phase_payload["phase"]
    if type(phase) is not str:
        return None
    try:
        checkpoint = LifecycleCheckpoint.equity(LifecyclePhase(phase))
    except ValueError:
        return None
    return checkpoint if checkpoint.to_payload() == value else None


class LifecycleEventKind(StrEnum):
    """Preserve the durable lifecycle event representation."""

    ADVANCE_REQUESTED = "advance_requested"
    PHASE_COMPLETED = "phase_completed"
    RUN_INPUTS_PINNED = "run_inputs_pinned"
    UNIVERSE_SNAPSHOTTED = "universe_snapshotted"


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
    MISSING_UNIVERSE_INPUT = "missing_universe_input"
    INVALID_UNIVERSE_INPUT = "invalid_universe_input"
    STALE_UNIVERSE_INPUT = "stale_universe_input"
    CONTRADICTORY_UNIVERSE_INPUT = "contradictory_universe_input"


class AdvanceFailureReason(StrEnum):
    """Bound every durable fail-closed receipt to a known lifecycle reason."""

    INVALID_SESSION = "invalid_session"
    UNSUPPORTED_CYCLE = "unsupported_cycle"
    INVALID_MODE = "invalid_mode"
    INVALID_IDEMPOTENCY_KEY = "invalid_idempotency_key"
    MISSING_UNIVERSE_INPUT = "missing_universe_input"
    INVALID_UNIVERSE_INPUT = "invalid_universe_input"
    STALE_UNIVERSE_INPUT = "stale_universe_input"
    CONTRADICTORY_UNIVERSE_INPUT = "contradictory_universe_input"
    SESSION_STREAM_CONFLICT = "session_stream_conflict"
    IDEMPOTENCY_KEY_CONFLICT = "idempotency_key_conflict"
    INVALID_DURABLE_STATE = "invalid_durable_state"


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    """Carry a validated stable key for one operator request."""

    value: str

    @classmethod
    def parse(cls, value: object) -> IdempotencyKey | None:
        """Validate an untrusted stable request key without retaining invalid input."""
        if type(value) is not str or _IDEMPOTENCY_KEY.fullmatch(value) is None:
            return None
        return cls(value)


@dataclass(frozen=True, slots=True)
class InputRefusal:
    """Return a bounded validation reason and any independently valid request key."""

    code: InputRefusalCode
    idempotency_key: IdempotencyKey | None
    cycle: MarketSession | None = None


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
        parsed_session = _parse_market_session(session)
        if key is None:
            return InputRefusal(InputRefusalCode.INVALID_IDEMPOTENCY_KEY, None, parsed_session)
        if parsed_session is None:
            return InputRefusal(InputRefusalCode.INVALID_SESSION, key)
        if type(mode) is not str or mode != SessionMode.CHAMPION.value:
            return InputRefusal(InputRefusalCode.INVALID_MODE, key, parsed_session)
        return cls(parsed_session, SessionMode.CHAMPION, key)

    @property
    def stream_id(self) -> str:
        return _fingerprint((self.mode.value, self.session.isoformat()))


@dataclass(frozen=True, slots=True)
class PinnedRunIdentity:
    """Identify the exact session and non-secret configuration pinned for a run."""

    run_id: str
    cycle: DecisionCycleIdentity
    configuration_version: int
    configuration_hash: str
    data_regime: str
    evidence_cutoff: datetime
    instrument_snapshot_hash: str
    position_snapshot_hash: str
    eligibility_policy_hash: str

    @classmethod
    def create(
        cls,
        request: AdvanceRequest,
        *,
        configuration_version: int,
        configuration_hash: str,
        universe_inputs: UniverseInputIdentity,
    ) -> PinnedRunIdentity:
        return cls(
            run_id=_fingerprint(
                (
                    configuration_hash,
                    configuration_version,
                    request.mode.value,
                    canonical_cycle_bytes(request.session).decode(),
                    universe_inputs.data_regime,
                    universe_inputs.evidence_cutoff.isoformat(),
                    universe_inputs.instrument_snapshot_hash,
                    universe_inputs.position_snapshot_hash,
                    universe_inputs.eligibility_policy_hash,
                )
            ),
            cycle=request.session,
            configuration_version=configuration_version,
            configuration_hash=configuration_hash,
            data_regime=universe_inputs.data_regime,
            evidence_cutoff=universe_inputs.evidence_cutoff,
            instrument_snapshot_hash=universe_inputs.instrument_snapshot_hash,
            position_snapshot_hash=universe_inputs.position_snapshot_hash,
            eligibility_policy_hash=universe_inputs.eligibility_policy_hash,
        )


@dataclass(frozen=True, slots=True)
class AdvanceReceipt:
    """Report durable lifecycle facts and how this call observed their completion."""

    disposition: AdvanceDisposition
    completed_phase: LifecycleCheckpoint | None
    pinned_run_identity: PinnedRunIdentity | None
    failure_reason: AdvanceFailureReason | None
    recovery: AdvanceRecovery | None = None
    universe_snapshot_id: str | None = None
    refused_cycle: DecisionCycleIdentity | None = None
    recorded_at: datetime | None = None

    @property
    def cycle(self) -> DecisionCycleIdentity | None:
        """Return the exact accepted or recognized-but-disabled Decision Cycle identity."""
        if self.pinned_run_identity is not None:
            return self.pinned_run_identity.cycle
        return self.refused_cycle

    def to_payload(self) -> dict[str, object]:
        """Serialize the result through the common versioned receipt envelope."""
        self.__post_init__()
        identity = self.pinned_run_identity
        cycle = self.cycle
        recorded_at = self.recorded_at
        recorded_at_text = recorded_at.isoformat() if type(recorded_at) is datetime else None
        if identity is not None:
            discriminator = "equity_market_session_advance_receipt"
            relevant_at = recorded_at_text
            available_at = recorded_at_text
            data_regime: str | None = identity.data_regime
            fingerprints: dict[str, object] = _material_fingerprints(identity)
        elif type(cycle) is CryptoDecisionWindow:
            discriminator = "unsupported_cycle_advance_receipt"
            relevant_at = cycle.starts_at.isoformat()
            available_at = None
            data_regime = None
            fingerprints = {}
        else:
            discriminator = "bounded_advance_refusal_receipt"
            relevant_at = None
            available_at = None
            data_regime = None
            fingerprints = {}
        material = {
            "envelope_schema_version": _LIFECYCLE_ENVELOPE_SCHEMA_VERSION,
            "record_kind": "lifecycle_receipt",
            "payload_discriminator": discriminator,
            "payload_schema_version": _LIFECYCLE_PAYLOAD_SCHEMA_VERSION,
            "cycle": None if cycle is None else cycle.to_payload(),
            "relevant_at": relevant_at,
            "available_at": available_at,
            "data_regime": data_regime,
            "authority_scope": _LIFECYCLE_AUTHORITY_SCOPE,
            "material_fingerprints": fingerprints,
            "payload": {
                "disposition": self.disposition.value,
                "completed_checkpoint": (
                    None if self.completed_phase is None else self.completed_phase.to_payload()
                ),
                "pinned_run_identity": (
                    None if identity is None else _pinned_identity_payload(identity)
                ),
                "failure_reason": (
                    None if self.failure_reason is None else self.failure_reason.value
                ),
                "recovery": None if self.recovery is None else self.recovery.value,
                "universe_snapshot_id": self.universe_snapshot_id,
            },
        }
        return {**material, "content_hash": _content_hash(material)}

    def __post_init__(self) -> None:
        if self.disposition is AdvanceDisposition.ADVANCED:
            if (
                self.completed_phase != LifecycleCheckpoint.equity(LifecyclePhase.SNAPSHOT_UNIVERSE)
                or self.pinned_run_identity is None
                or self.failure_reason is not None
                or self.recovery is None
                or self.universe_snapshot_id is None
                or self.refused_cycle is not None
                or type(self.pinned_run_identity.cycle) is not MarketSession
                or type(self.recorded_at) is not datetime
                or self.recorded_at.utcoffset() is None
                or self.recorded_at < self.pinned_run_identity.evidence_cutoff
            ):
                raise ValueError(_INVALID_ADVANCED_RECEIPT)
            return
        if self.disposition is AdvanceDisposition.FAILED_CLOSED:
            if (
                self.completed_phase is not None
                or self.pinned_run_identity is not None
                or self.failure_reason is None
                or self.recovery is not None
                or self.universe_snapshot_id is not None
                or self.recorded_at is not None
                or (
                    (self.failure_reason is AdvanceFailureReason.UNSUPPORTED_CYCLE)
                    != (type(self.refused_cycle) is CryptoDecisionWindow)
                )
                or (
                    self.refused_cycle is not None
                    and type(self.refused_cycle) not in (CryptoDecisionWindow, MarketSession)
                )
            ):
                raise ValueError(_INVALID_FAILED_RECEIPT)
            return
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(self.disposition)  # pragma: no cover  # pragma: no mutate

    @classmethod
    def advanced(
        cls,
        identity: PinnedRunIdentity,
        snapshot: UniverseSnapshot,
        recovery: AdvanceRecovery,
        recorded_at: datetime,
    ) -> AdvanceReceipt:
        return cls(
            AdvanceDisposition.ADVANCED,
            LifecycleCheckpoint.equity(LifecyclePhase.SNAPSHOT_UNIVERSE),
            identity,
            None,
            recovery,
            snapshot.snapshot_id,
            recorded_at=recorded_at,
        )

    @classmethod
    def failed_closed(
        cls,
        reason: AdvanceFailureReason,
        *,
        cycle: DecisionCycleIdentity | None = None,
    ) -> AdvanceReceipt:
        return cls(AdvanceDisposition.FAILED_CLOSED, None, None, reason, refused_cycle=cycle)


def parse_advance_receipt(  # noqa: PLR0911 - reject each hostile envelope field directly.
    value: object,
) -> AdvanceReceipt | None:
    """Validate one hostile common receipt envelope and reconstruct its typed result."""
    root = _exact_mapping(value, _RECEIPT_FIELDS)
    if root is None:
        return None
    payload = _exact_mapping(root["payload"], _RECEIPT_PAYLOAD_FIELDS)
    fingerprints = _exact_mapping(root["material_fingerprints"], None)
    if payload is None or fingerprints is None:
        return None
    if not (
        _is_schema_version(root["envelope_schema_version"])
        and _is_schema_version(root["payload_schema_version"])
        and type(root["record_kind"]) is str
        and type(root["payload_discriminator"]) is str
        and type(root["authority_scope"]) is str
        and is_sha256(root["content_hash"])
    ):
        return None
    if set(fingerprints) not in (set(), set(_MATERIAL_FINGERPRINT_FIELDS)):
        return None
    if any(not is_sha256(fingerprint) for fingerprint in fingerprints.values()):
        return None
    relevant_at = root["relevant_at"]
    available_at = root["available_at"]
    parsed_relevant_at = None if relevant_at is None else _parse_aware_timestamp(relevant_at)
    parsed_available_at = None if available_at is None else _parse_aware_timestamp(available_at)
    data_regime = root["data_regime"]
    if (
        (relevant_at is not None and parsed_relevant_at is None)
        or (available_at is not None and parsed_available_at is None)
        or (data_regime is not None and not is_data_regime(data_regime))
    ):
        return None
    cycle_value = root["cycle"]
    cycle = None if cycle_value is None else parse_decision_cycle_identity(cycle_value)
    if cycle_value is not None and cycle is None:
        return None
    disposition = _enum_or_none(AdvanceDisposition, payload["disposition"])
    valid_failure_reason, failure_reason = _optional_enum(
        AdvanceFailureReason, payload["failure_reason"]
    )
    valid_recovery, recovery = _optional_enum(AdvanceRecovery, payload["recovery"])
    checkpoint_value = payload["completed_checkpoint"]
    checkpoint = None if checkpoint_value is None else parse_lifecycle_checkpoint(checkpoint_value)
    if checkpoint_value is not None and checkpoint is None:
        return None
    identity_value = payload["pinned_run_identity"]
    identity = None if identity_value is None else _parse_pinned_identity(identity_value)
    if identity_value is not None and identity is None:
        return None
    snapshot_id = payload["universe_snapshot_id"]
    if snapshot_id is not None and not is_sha256(snapshot_id):
        return None
    if disposition is None or not valid_failure_reason or not valid_recovery:
        return None
    try:
        receipt = AdvanceReceipt(
            disposition=disposition,
            completed_phase=checkpoint,
            pinned_run_identity=identity,
            failure_reason=failure_reason,
            recovery=recovery,
            universe_snapshot_id=snapshot_id,
            refused_cycle=cycle if identity is None else None,
            recorded_at=parsed_available_at if identity is not None else None,
        )
    except ValueError:
        return None
    return receipt if receipt.to_payload() == root else None


@dataclass(frozen=True, slots=True)
class LifecycleProgress:
    """Represent a validated partial or complete authoritative stream."""

    request: AdvanceRequest
    pinned_run_identity: PinnedRunIdentity
    completed_phase: LifecyclePhase | None
    sequence: int
    universe_snapshot: UniverseSnapshot | None = None

    @property
    def is_complete(self) -> bool:
        phase = self.completed_phase
        if phase is None:
            return False
        if phase is LifecyclePhase.RECONCILE_PRIOR_STATE:
            return False
        if phase is LifecyclePhase.PIN_RUN_INPUTS:
            return False
        if phase is LifecyclePhase.SNAPSHOT_UNIVERSE:
            return True
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(phase)  # pragma: no cover  # pragma: no mutate

    def require_prepared_universe_snapshot(self) -> UniverseSnapshot:
        """Return the pinned snapshot or reject access before its durable checkpoint."""
        snapshot = self.universe_snapshot
        if snapshot is None:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
        return snapshot


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """Carry one typed append-only lifecycle checkpoint."""

    stream_id: str
    sequence: int
    request: AdvanceRequest
    pinned_run_identity: PinnedRunIdentity
    event_kind: LifecycleEventKind
    completed_phase: LifecycleCheckpoint | None
    prepared_universe_snapshot: UniverseSnapshot | None = None
    published_universe_snapshot_id: str | None = None

    @property
    def universe_snapshot_id(self) -> str | None:
        """Return the prepared or published snapshot reference carried by this event."""
        if self.prepared_universe_snapshot is not None:
            return self.prepared_universe_snapshot.snapshot_id
        return self.published_universe_snapshot_id

    def to_envelope(self, recorded_at: datetime) -> dict[str, object]:
        """Serialize one equity event through the stable hashed lifecycle envelope."""
        if type(recorded_at) is not datetime or recorded_at.utcoffset() is None:
            raise ValueError(_INVALID_EVENT_TIME)
        identity = self.pinned_run_identity
        material = {
            "envelope_schema_version": _LIFECYCLE_ENVELOPE_SCHEMA_VERSION,
            "record_kind": "lifecycle_event",
            "payload_discriminator": "equity_market_session_lifecycle_event",
            "payload_schema_version": _LIFECYCLE_PAYLOAD_SCHEMA_VERSION,
            "cycle": identity.cycle.to_payload(),
            "event_at": recorded_at.isoformat(),
            "available_at": recorded_at.isoformat(),
            "data_regime": identity.data_regime,
            "authority_scope": _LIFECYCLE_AUTHORITY_SCOPE,
            "material_fingerprints": {
                "configuration": identity.configuration_hash,
                "instrument_snapshot": identity.instrument_snapshot_hash,
                "position_snapshot": identity.position_snapshot_hash,
                "eligibility_policy": identity.eligibility_policy_hash,
            },
            "payload": {
                "stream_id": self.stream_id,
                "sequence": self.sequence,
                "idempotency_key": self.request.idempotency_key.value,
                "mode": self.request.mode.value,
                "configuration_version": identity.configuration_version,
                "run_id": identity.run_id,
                "event_kind": self.event_kind.value,
                "completed_phase": (
                    None if self.completed_phase is None else self.completed_phase.to_payload()
                ),
                "universe_snapshot_id": (self.universe_snapshot_id),
            },
        }
        return {**material, "content_hash": _content_hash(material)}


@dataclass(frozen=True, slots=True)
class AdvanceCommand:
    """Request one deterministic lifecycle transition for validated inputs."""

    request: AdvanceRequest
    pinned_run_identity: PinnedRunIdentity
    universe_snapshot: UniverseSnapshot


@dataclass(frozen=True, slots=True)
class AdvanceAttempt:
    """Track only progress observed or appended during one Advance call."""

    recovery: AdvanceRecovery | None = None
    last_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class LifecycleStatus:
    """Report operator-visible facts rebuilt only from authoritative lifecycle history."""

    active_phase: LifecycleCheckpoint | None
    last_completed_cycle: DecisionCycleIdentity | None
    universe_snapshot_cycle: DecisionCycleIdentity | None
    pinned_run_identity: PinnedRunIdentity | None
    liveness: LifecycleLiveness
    durable_reason: AdvanceFailureReason | None
    universe_snapshot_id: str | None

    @classmethod
    def not_started(cls) -> LifecycleStatus:
        return cls(None, None, None, None, LifecycleLiveness.NOT_STARTED, None, None)


@dataclass(frozen=True, slots=True)
class DurableAdvanceRefusal:
    """Represent one validated refusal reconstructed in durable append order."""

    sequence: int
    idempotency_key: IdempotencyKey | None
    reason: AdvanceFailureReason
    cycle: MarketSession | None = None


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
    (LifecycleEventKind.ADVANCE_REQUESTED, None, False, False),
    (LifecycleEventKind.PHASE_COMPLETED, LifecyclePhase.RECONCILE_PRIOR_STATE, False, False),
    (LifecycleEventKind.RUN_INPUTS_PINNED, LifecyclePhase.PIN_RUN_INPUTS, True, False),
    (LifecycleEventKind.UNIVERSE_SNAPSHOTTED, LifecyclePhase.SNAPSHOT_UNIVERSE, False, True),
)


def decide_advance(
    history: LifecycleHistory,
    command: LifecycleCommand,
    attempt: AdvanceAttempt,
    recorded_at: datetime,
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
        return _decide_valid_advance(
            history,
            progress_by_key,
            command,
            attempt,
            recorded_at,
        )
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(command)  # pragma: no cover  # pragma: no mutate


def decide_terminal_refusal(
    refusals: tuple[DurableAdvanceRefusal, ...],
    command: LifecycleCommand,
) -> AdvanceReceipt | None:
    """Return a previously durable terminal refusal without reading unrelated history."""
    if isinstance(command, AdvanceCommand):
        return _terminal_advance_refusal(refusals, command)
    if isinstance(command, InputRefusal):
        return _terminal_input_refusal(refusals, command)
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(command)  # pragma: no cover  # pragma: no mutate


def _terminal_advance_refusal(
    refusals: tuple[DurableAdvanceRefusal, ...],
    command: AdvanceCommand,
) -> AdvanceReceipt | None:
    current_cycle = command.request.session
    request_key = command.request.idempotency_key
    refusal = next(
        (item for item in refusals if item.idempotency_key == request_key),
        None,
    )
    if refusal is None:
        return None
    if refusal.cycle != current_cycle or refusal.reason in _INPUT_FAILURE_REASONS:
        return AdvanceReceipt.failed_closed(
            AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
            cycle=current_cycle,
        )
    return AdvanceReceipt.failed_closed(refusal.reason, cycle=current_cycle)


def _terminal_input_refusal(
    refusals: tuple[DurableAdvanceRefusal, ...],
    command: InputRefusal,
) -> AdvanceReceipt | None:
    refusal_key = command.idempotency_key
    current_reason = _input_failure_reason(command.code)
    refusal = next(
        (
            item
            for item in refusals
            if item.idempotency_key == refusal_key
            and (
                refusal_key is not None
                or (item.cycle == command.cycle and item.reason is current_reason)
            )
        ),
        None,
    )
    if refusal is None:
        return None
    if refusal_key is not None and (
        refusal.cycle != command.cycle or refusal.reason is not current_reason
    ):
        return AdvanceReceipt.failed_closed(
            AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
            cycle=command.cycle,
        )
    return AdvanceReceipt.failed_closed(refusal.reason, cycle=command.cycle)


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
                command.cycle,
            )
        return _append_refusal(
            history,
            command.idempotency_key,
            AdvanceFailureReason.INVALID_DURABLE_STATE,
            command.cycle,
        )
    if isinstance(command, AdvanceCommand):
        return _append_refusal(
            history,
            command.request.idempotency_key,
            AdvanceFailureReason.INVALID_DURABLE_STATE,
            command.request.session,
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
    unkeyed_identities: set[tuple[AdvanceFailureReason, MarketSession | None]] = set()
    for expected_sequence, refusal in enumerate(refusals, start=1):
        if refusal.sequence != expected_sequence:
            raise InvalidLifecycleStateError(_INVALID_REFUSAL_ORDER)
        key = refusal.idempotency_key
        if (key is None) != (refusal.reason is AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY):
            raise InvalidLifecycleStateError(_INVALID_UNKEYED_REASON)
        if key is None:
            identity = (refusal.reason, refusal.cycle)
            if identity in unkeyed_identities:
                raise InvalidLifecycleStateError(_INVALID_REFUSAL_UNIQUENESS)
            unkeyed_identities.add(identity)
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


def _reconstruct_stream(  # noqa: PLR0912 - validate each durable checkpoint invariant independently.
    events: tuple[LifecycleEvent, ...],
) -> LifecycleProgress:
    first = events[0]
    request = first.request
    identity = first.pinned_run_identity
    expected_run_id = _fingerprint(
        (
            identity.configuration_hash,
            identity.configuration_version,
            request.mode.value,
            canonical_cycle_bytes(request.session).decode(),
            identity.data_regime,
            identity.evidence_cutoff.isoformat(),
            identity.instrument_snapshot_hash,
            identity.position_snapshot_hash,
            identity.eligibility_policy_hash,
        )
    )
    if identity.configuration_version != 1:
        raise InvalidLifecycleStateError(_UNSUPPORTED_CONFIGURATION_VERSION)
    if identity.cycle != request.session:
        raise InvalidLifecycleStateError(_INVALID_DERIVED_IDENTITY)
    if first.stream_id != request.stream_id or identity.run_id != expected_run_id:
        raise InvalidLifecycleStateError(_INVALID_DERIVED_IDENTITY)
    if len(events) > len(_EVENT_SEQUENCE):
        raise InvalidLifecycleStateError(_UNSUPPORTED_LATER_PHASES)
    prepared_snapshot: UniverseSnapshot | None = None
    for sequence, event in enumerate(events):
        if event.sequence != sequence:
            raise InvalidLifecycleStateError(_NONCONTIGUOUS_SEQUENCE)
        if (
            event.stream_id != first.stream_id
            or event.request != request
            or event.pinned_run_identity != identity
        ):
            raise InvalidLifecycleStateError(_CHANGED_PINNED_FACTS)
        expected_kind, expected_phase, prepares_snapshot, publishes_snapshot = _EVENT_SEQUENCE[
            sequence
        ]
        expected_checkpoint = (
            None if expected_phase is None else LifecycleCheckpoint.equity(expected_phase)
        )
        if (event.event_kind, event.completed_phase) != (expected_kind, expected_checkpoint):
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
        if (event.prepared_universe_snapshot is not None) is not prepares_snapshot:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
        if (event.published_universe_snapshot_id is not None) is not publishes_snapshot:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
        snapshot = event.prepared_universe_snapshot
        if snapshot is not None and (
            snapshot.run_id != identity.run_id
            or snapshot.inputs.data_regime != identity.data_regime
            or snapshot.inputs.evidence_cutoff != identity.evidence_cutoff
            or snapshot.cycle != identity.cycle
            or snapshot.inputs.instrument_snapshot.material_fingerprint
            != identity.instrument_snapshot_hash
            or snapshot.inputs.position_snapshot.fingerprint != identity.position_snapshot_hash
            or snapshot.policy.fingerprint != identity.eligibility_policy_hash
        ):
            raise InvalidLifecycleStateError(_CHANGED_PINNED_FACTS)
        if snapshot is not None:
            prepared_snapshot = snapshot
        if event.published_universe_snapshot_id is not None and (
            prepared_snapshot is None
            or event.published_universe_snapshot_id != prepared_snapshot.snapshot_id
        ):
            raise InvalidLifecycleStateError(_CHANGED_PINNED_FACTS)
    return LifecycleProgress(
        request,
        identity,
        None if events[-1].completed_phase is None else events[-1].completed_phase.phase,
        len(events) - 1,
        prepared_snapshot,
    )


def _decide_valid_advance(
    history: LifecycleHistory,
    progress_by_key: dict[str, LifecycleProgress],
    command: AdvanceCommand,
    attempt: AdvanceAttempt,
    recorded_at: datetime,
) -> LifecycleDecision:
    request = command.request
    key = request.idempotency_key
    progress = progress_by_key.get(key.value)
    if progress is None:
        return _decide_new_stream(history, tuple(progress_by_key.values()), command, attempt)
    if (
        progress.request != request
        or progress.pinned_run_identity != command.pinned_run_identity
        or (
            progress.universe_snapshot is not None
            and progress.universe_snapshot.snapshot_id != command.universe_snapshot.snapshot_id
        )
    ):
        return _decide_idempotency_conflict(history, progress, key, request.session)
    recovery = _recovery_for_progress(progress, attempt)
    if progress.is_complete:
        return AdvanceReceipt.advanced(
            progress.pinned_run_identity,
            progress.require_prepared_universe_snapshot(),
            AdvanceRecovery.PREVIOUSLY_COMPLETED
            if progress.sequence != attempt.last_sequence
            else recovery,
            recorded_at,
        )
    return _append_next_event(progress, command, recovery, recorded_at)


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
            request.session,
        )
    if request.stream_id in history.occupied_stream_ids or any(
        existing.request.stream_id == request.stream_id for existing in progresses
    ):
        return _append_refusal(
            history,
            key,
            AdvanceFailureReason.SESSION_STREAM_CONFLICT,
            request.session,
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
    reason = _input_failure_reason(refusal.code)
    if key is None:
        return _append_refusal(history, None, reason, refusal.cycle)
    progress = progress_by_key.get(key.value)
    if progress is None:
        return _append_refusal(history, key, reason, refusal.cycle)
    return _decide_idempotency_conflict(history, progress, key, refusal.cycle)


def _input_failure_reason(code: InputRefusalCode) -> AdvanceFailureReason:
    if code is InputRefusalCode.INVALID_SESSION:
        reason = AdvanceFailureReason.INVALID_SESSION
    elif code is InputRefusalCode.INVALID_MODE:
        reason = AdvanceFailureReason.INVALID_MODE
    elif code is InputRefusalCode.INVALID_IDEMPOTENCY_KEY:
        reason = AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY
    elif code is InputRefusalCode.MISSING_UNIVERSE_INPUT:
        reason = AdvanceFailureReason.MISSING_UNIVERSE_INPUT
    elif code is InputRefusalCode.INVALID_UNIVERSE_INPUT:
        reason = AdvanceFailureReason.INVALID_UNIVERSE_INPUT
    elif code is InputRefusalCode.STALE_UNIVERSE_INPUT:
        reason = AdvanceFailureReason.STALE_UNIVERSE_INPUT
    elif code is InputRefusalCode.CONTRADICTORY_UNIVERSE_INPUT:
        reason = AdvanceFailureReason.CONTRADICTORY_UNIVERSE_INPUT
    else:
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(code)  # pragma: no cover  # pragma: no mutate
    return reason


_INPUT_FAILURE_REASONS = frozenset(_input_failure_reason(code) for code in InputRefusalCode)


def _decide_idempotency_conflict(
    history: LifecycleHistory,
    progress: LifecycleProgress,
    key: IdempotencyKey,
    cycle: MarketSession | None,
) -> LifecycleDecision:
    reason = AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
    if not progress.is_complete:
        return _append_refusal(history, key, reason, cycle)
    conflict = next(
        (item for item in history.conflicts if item.idempotency_key == key),
        None,
    )
    if conflict is not None:
        return AdvanceReceipt.failed_closed(conflict.reason, cycle=cycle)
    return AppendTerminalLifecycleRecord(
        DurableAdvanceConflict(key, reason),
        AdvanceReceipt.failed_closed(reason, cycle=cycle),
    )


def _append_next_event(
    progress: LifecycleProgress,
    command: AdvanceCommand,
    recovery: AdvanceRecovery,
    recorded_at: datetime,
) -> AppendLifecycleRecord | AppendTerminalLifecycleRecord:
    sequence = progress.sequence + 1
    event_kind, phase, prepares_snapshot, publishes_snapshot = _EVENT_SEQUENCE[sequence]
    prepared_snapshot = command.universe_snapshot if prepares_snapshot else None
    published_snapshot = (
        progress.require_prepared_universe_snapshot() if publishes_snapshot else None
    )
    published_snapshot_id = None if published_snapshot is None else published_snapshot.snapshot_id
    event = LifecycleEvent(
        stream_id=command.request.stream_id,
        sequence=sequence,
        request=command.request,
        pinned_run_identity=command.pinned_run_identity,
        event_kind=event_kind,
        completed_phase=None if phase is None else LifecycleCheckpoint.equity(phase),
        prepared_universe_snapshot=prepared_snapshot,
        published_universe_snapshot_id=published_snapshot_id,
    )
    next_attempt = AdvanceAttempt(recovery, sequence)
    # Existing progress contains event zero, so its next phase cannot be absent.
    if phase is None:  # pragma: no cover
        return AppendLifecycleRecord(event, next_attempt)  # pragma: no mutate
    if phase is LifecyclePhase.RECONCILE_PRIOR_STATE:
        return AppendLifecycleRecord(event, next_attempt)
    if phase is LifecyclePhase.PIN_RUN_INPUTS:
        return AppendLifecycleRecord(event, next_attempt)
    if phase is LifecyclePhase.SNAPSHOT_UNIVERSE:
        return AppendTerminalLifecycleRecord(
            event,
            AdvanceReceipt.advanced(
                progress.pinned_run_identity,
                progress.require_prepared_universe_snapshot(),
                recovery,
                recorded_at,
            ),
        )
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(phase)  # pragma: no cover  # pragma: no mutate


def _append_refusal(
    history: LifecycleHistory,
    key: IdempotencyKey | None,
    reason: AdvanceFailureReason,
    cycle: MarketSession | None = None,
) -> AppendTerminalLifecycleRecord:
    receipt = AdvanceReceipt.failed_closed(reason, cycle=cycle)
    sequence = (
        len(history.refusals) + 1
        if history.next_refusal_sequence is None
        else history.next_refusal_sequence
    )
    return AppendTerminalLifecycleRecord(
        DurableAdvanceRefusal(sequence, key, reason, cycle),
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
            last_completed_cycle=None,
            universe_snapshot_cycle=None,
            pinned_run_identity=None,
            liveness=LifecycleLiveness.FAILED_CLOSED,
            durable_reason=history.refusals[-1].reason,
            universe_snapshot_id=None,
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
            last_completed_cycle=None,
            universe_snapshot_cycle=_latest_universe_cycle(progresses),
            pinned_run_identity=current.pinned_run_identity,
            liveness=LifecycleLiveness.FAILED_CLOSED,
            durable_reason=matching_refusal.reason,
            universe_snapshot_id=_latest_universe_snapshot_id(progresses),
        )

    next_phase = None if current.is_complete else _EVENT_SEQUENCE[current.sequence + 1][1]
    active_phase = None if next_phase is None else LifecycleCheckpoint.equity(next_phase)
    return LifecycleStatus(
        active_phase=active_phase,
        last_completed_cycle=None,
        universe_snapshot_cycle=_latest_universe_cycle(progresses),
        pinned_run_identity=current.pinned_run_identity,
        liveness=LifecycleLiveness.ACTIVE,
        durable_reason=_reported_reason(
            refusals=history.refusals,
            conflicts=history.conflicts,
        ),
        universe_snapshot_id=_latest_universe_snapshot_id(progresses),
    )


def _latest_universe_progress(
    progresses: tuple[LifecycleProgress, ...],
) -> LifecycleProgress | None:
    completed = tuple(progress for progress in progresses if progress.is_complete)
    if not completed:
        return None
    return max(completed, key=lambda progress: progress.request.session.trading_date)


def _latest_universe_cycle(
    progresses: tuple[LifecycleProgress, ...],
) -> DecisionCycleIdentity | None:
    progress = _latest_universe_progress(progresses)
    return None if progress is None else progress.pinned_run_identity.cycle


def _latest_universe_snapshot_id(progresses: tuple[LifecycleProgress, ...]) -> str | None:
    progress = _latest_universe_progress(progresses)
    if progress is None or progress.universe_snapshot is None:
        return None
    return progress.universe_snapshot.snapshot_id


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
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _exact_mapping(
    value: object,
    fields: frozenset[str] | None,
) -> dict[str, object] | None:
    if type(value) is not dict:
        return None
    result: dict[str, object] = {}
    for key, item in value.items():
        if type(key) is not str:
            return None
        result[key] = item
    if fields is not None and set(result) != fields:
        return None
    return result


def _is_schema_version(value: object) -> bool:
    return type(value) is int and value == _LIFECYCLE_ENVELOPE_SCHEMA_VERSION


def _enum_or_none[T: StrEnum](enum_type: type[T], value: object) -> T | None:
    if type(value) is not str:
        return None
    try:
        return enum_type(value)
    except ValueError:
        return None


def _optional_enum[T: StrEnum](
    enum_type: type[T],
    value: object,
) -> tuple[bool, T | None]:
    if value is None:
        return True, None
    parsed = _enum_or_none(enum_type, value)
    return parsed is not None, parsed


def _parse_aware_timestamp(value: object) -> datetime | None:
    if type(value) is not str:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    if parsed.isoformat() != value:
        return None
    return parsed


def _pinned_identity_payload(identity: PinnedRunIdentity) -> dict[str, object]:
    return {
        "run_id": identity.run_id,
        "cycle": identity.cycle.to_payload(),
        "configuration_version": identity.configuration_version,
        "configuration_hash": identity.configuration_hash,
        "data_regime": identity.data_regime,
        "evidence_cutoff": identity.evidence_cutoff.isoformat(),
        "instrument_snapshot_hash": identity.instrument_snapshot_hash,
        "position_snapshot_hash": identity.position_snapshot_hash,
        "eligibility_policy_hash": identity.eligibility_policy_hash,
    }


def _material_fingerprints(identity: PinnedRunIdentity) -> dict[str, object]:
    return {
        "configuration": identity.configuration_hash,
        "instrument_snapshot": identity.instrument_snapshot_hash,
        "position_snapshot": identity.position_snapshot_hash,
        "eligibility_policy": identity.eligibility_policy_hash,
    }


def _parse_pinned_identity(value: object) -> PinnedRunIdentity | None:
    fields = _exact_mapping(value, _PINNED_IDENTITY_FIELDS)
    if fields is None:
        return None
    cycle = parse_decision_cycle_identity(fields["cycle"])
    cutoff = _parse_aware_timestamp(fields["evidence_cutoff"])
    data_regime = fields["data_regime"]
    if (
        cycle is None
        or cutoff is None
        or type(fields["configuration_version"]) is not int
        or fields["configuration_version"] != 1
        or not is_data_regime(data_regime)
        or not is_sha256(fields["run_id"])
        or not is_sha256(fields["configuration_hash"])
        or not is_sha256(fields["instrument_snapshot_hash"])
        or not is_sha256(fields["position_snapshot_hash"])
        or not is_sha256(fields["eligibility_policy_hash"])
    ):
        return None
    identity = PinnedRunIdentity(
        run_id=fields["run_id"],
        cycle=cycle,
        configuration_version=fields["configuration_version"],
        configuration_hash=fields["configuration_hash"],
        data_regime=data_regime,
        evidence_cutoff=cutoff,
        instrument_snapshot_hash=fields["instrument_snapshot_hash"],
        position_snapshot_hash=fields["position_snapshot_hash"],
        eligibility_policy_hash=fields["eligibility_policy_hash"],
    )
    expected_run_id = _fingerprint(
        (
            identity.configuration_hash,
            identity.configuration_version,
            SessionMode.CHAMPION.value,
            canonical_cycle_bytes(identity.cycle).decode(),
            identity.data_regime,
            identity.evidence_cutoff.isoformat(),
            identity.instrument_snapshot_hash,
            identity.position_snapshot_hash,
            identity.eligibility_policy_hash,
        )
    )
    return identity if identity.run_id == expected_run_id else None


def _content_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _parse_idempotency_key(value: object) -> IdempotencyKey | None:
    return IdempotencyKey.parse(value)


def _parse_market_session(value: object) -> MarketSession | None:
    if type(value) is MarketSession:
        return value
    if type(value) is not str:
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
