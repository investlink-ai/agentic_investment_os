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


def is_sha256(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _parse_idempotency_key(value: object) -> IdempotencyKey | None:
    if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        return None
    return IdempotencyKey(value)


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
