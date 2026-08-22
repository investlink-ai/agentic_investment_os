"""Advance and report a Market Session through durable lifecycle capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from agentic_investment_os.domain.lifecycle import (
    AdvanceAttempt,
    AdvanceCommand,
    AdvanceReceipt,
    AdvanceRequest,
    AppendTerminalLifecycleRecord,
    InputRefusal,
    InvalidLifecycleStateError,
    LifecycleLedger,
    LifecycleStatus,
    LifecycleStatusProjection,
    PinnedRunIdentity,
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
        command = (
            parsed
            if isinstance(parsed, InputRefusal)
            else AdvanceCommand(
                parsed,
                PinnedRunIdentity.create(
                    parsed,
                    configuration_version=self.configuration_version,
                    configuration_hash=self.configuration_hash,
                ),
            )
        )
        attempt = AdvanceAttempt()
        while True:
            decision = self.ledger.advance_step(command, attempt, self.clock.now())
            if isinstance(decision, AdvanceReceipt):
                return decision
            if isinstance(decision, AppendTerminalLifecycleRecord):
                return decision.receipt
            if decision.attempt.last_sequence is None or (
                attempt.last_sequence is not None
                and decision.attempt.last_sequence <= attempt.last_sequence
            ):
                raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
            attempt = decision.attempt


@dataclass(frozen=True, slots=True)
class Status:
    """Rebuild and return lifecycle status without advancing authoritative history."""

    projection: LifecycleStatusProjection

    def __call__(self) -> LifecycleStatus:
        return self.projection.rebuild_status()
