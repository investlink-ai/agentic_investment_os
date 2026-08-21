"""Advance a Market Session through the implemented durable checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from agentic_investment_os.domain.lifecycle import (
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRecovery,
    AdvanceRequest,
    CheckpointResult,
    CheckpointWrite,
    InputRefusal,
    InvalidLifecycleStateError,
    LifecycleLedger,
    LifecyclePhase,
    LifecycleProgress,
    PinnedRunIdentity,
    StreamConflict,
)

if TYPE_CHECKING:
    from datetime import datetime

_INCOMPLETE_CHECKPOINT_RESULT = "lifecycle ledger returned an incomplete checkpoint result"


class Clock(Protocol):
    """Supply an aware timestamp at the composition boundary."""

    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class Advance:
    """Advance or resume one session to the pinned-input checkpoint."""

    ledger: LifecycleLedger
    configuration_version: int
    configuration_hash: str
    clock: Clock

    def __call__(
        self,
        *,
        session: object,
        mode: object,
        idempotency_key: object,
    ) -> AdvanceReceipt:
        parsed = AdvanceRequest.parse(
            session=session,
            mode=mode,
            idempotency_key=idempotency_key,
        )
        if isinstance(parsed, InputRefusal):
            return self.ledger.record_refusal(
                parsed.idempotency_key,
                AdvanceFailureReason(parsed.code.value),
                self.clock.now(),
            )
        return self._advance_valid_request(parsed)

    def _advance_valid_request(self, parsed: AdvanceRequest) -> AdvanceReceipt:
        identity = PinnedRunIdentity.create(
            parsed,
            configuration_version=self.configuration_version,
            configuration_hash=self.configuration_hash,
        )
        started = self._start_or_load(parsed, identity)
        if isinstance(started, AdvanceReceipt):
            return started
        progress = started.progress
        recovery = (
            AdvanceRecovery.FRESH
            if started.write is CheckpointWrite.APPENDED
            else AdvanceRecovery.RESUMED
        )
        if progress.is_complete:
            return _completed_receipt(progress, AdvanceRecovery.PREVIOUSLY_COMPLETED)
        if progress.completed_phase is None:
            reconciled = self.ledger.complete_reconciliation(
                parsed.idempotency_key, self.clock.now()
            )
            if isinstance(reconciled, AdvanceReceipt):
                return reconciled
            progress = reconciled.progress
            if reconciled.write is CheckpointWrite.OBSERVED:
                recovery = AdvanceRecovery.RESUMED
            if progress.is_complete:
                return _completed_receipt(progress, AdvanceRecovery.PREVIOUSLY_COMPLETED)
        if progress.completed_phase is LifecyclePhase.RECONCILE_PRIOR_STATE:
            pinned = self.ledger.pin_run_inputs(parsed.idempotency_key, self.clock.now())
            if isinstance(pinned, AdvanceReceipt):
                return pinned
            progress = pinned.progress
            if pinned.write is CheckpointWrite.OBSERVED:
                recovery = AdvanceRecovery.PREVIOUSLY_COMPLETED
        return _completed_receipt(progress, recovery)

    def _start_or_load(
        self,
        request: AdvanceRequest,
        identity: PinnedRunIdentity,
    ) -> CheckpointResult | AdvanceReceipt:
        started = self.ledger.start(request, identity, self.clock.now())
        if isinstance(started, StreamConflict):
            return self.ledger.record_refusal(
                request.idempotency_key,
                AdvanceFailureReason.SESSION_STREAM_CONFLICT,
                self.clock.now(),
            )
        return started


def _completed_receipt(
    progress: LifecycleProgress,
    recovery: AdvanceRecovery,
) -> AdvanceReceipt:
    receipt = progress.receipt(recovery)
    if receipt is None:
        raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
    return receipt
