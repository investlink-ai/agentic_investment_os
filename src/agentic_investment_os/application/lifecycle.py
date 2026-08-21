"""Advance a Market Session through the implemented durable checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from agentic_investment_os.domain.lifecycle import (
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRequest,
    InputRefusal,
    LifecycleLedger,
    LifecyclePhase,
    PinnedRunIdentity,
    StreamConflict,
)

if TYPE_CHECKING:
    from datetime import datetime


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
        existing = self.ledger.resolve_for_advance(
            parsed.idempotency_key,
            self.clock.now(),
        )
        if isinstance(existing, AdvanceReceipt):
            return existing

        identity = PinnedRunIdentity.create(
            parsed,
            configuration_version=self.configuration_version,
            configuration_hash=self.configuration_hash,
        )
        progress = existing
        if progress is None:
            started = self.ledger.start(parsed, identity, self.clock.now())
            if isinstance(started, StreamConflict):
                return self.ledger.record_refusal(
                    parsed.idempotency_key,
                    AdvanceFailureReason.SESSION_STREAM_CONFLICT,
                    self.clock.now(),
                )
            if isinstance(started, AdvanceReceipt):
                return started
            progress = started
        if progress.completed_phase is None:
            reconciled = self.ledger.complete_reconciliation(
                parsed.idempotency_key, self.clock.now()
            )
            if isinstance(reconciled, AdvanceReceipt):
                return reconciled
            progress = reconciled
        if progress.completed_phase is LifecyclePhase.RECONCILE_PRIOR_STATE:
            return self.ledger.pin_run_inputs(parsed.idempotency_key, self.clock.now())
        return AdvanceReceipt(
            disposition=AdvanceDisposition.ADVANCED,
            completed_phase=LifecyclePhase.PIN_RUN_INPUTS,
            pinned_run_identity=progress.pinned_run_identity,
            failure_reason=None,
        )
