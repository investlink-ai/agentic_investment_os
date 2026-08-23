"""Advance and report a Market Session through durable lifecycle capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, assert_never

from agentic_investment_os.domain.lifecycle import (
    AdvanceAttempt,
    AdvanceCommand,
    AdvanceReceipt,
    AdvanceRequest,
    AppendLifecycleRecord,
    AppendTerminalLifecycleRecord,
    InputRefusal,
    InvalidLifecycleStateError,
    LifecycleCommand,
    LifecycleLedger,
    LifecyclePersistenceError,
    LifecycleStatus,
    LifecycleStatusProjection,
    PinnedRunIdentity,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ("Advance", "Clock", "Status")


_INCOMPLETE_CHECKPOINT_RESULT = "lifecycle ledger returned an incomplete checkpoint result"
_CLOCK_INVALID = "lifecycle clock must return a timezone-aware instant representable in UTC"


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
        command: LifecycleCommand
        if isinstance(parsed, InputRefusal):
            command = parsed
        elif isinstance(parsed, AdvanceRequest):
            command = AdvanceCommand(
                parsed,
                PinnedRunIdentity.create(
                    parsed,
                    configuration_version=self.configuration_version,
                    configuration_hash=self.configuration_hash,
                ),
            )
        else:
            # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
            assert_never(parsed)  # pragma: no cover  # pragma: no mutate
        attempt = AdvanceAttempt()
        while True:
            try:
                recorded_at = UtcInstant.from_datetime(self.clock.now())
            except InvalidUtcInstantError as error:
                raise LifecyclePersistenceError(_CLOCK_INVALID) from error
            decision = self.ledger.advance_step(command, attempt, recorded_at)
            if isinstance(decision, AdvanceReceipt):
                return decision
            if isinstance(decision, AppendTerminalLifecycleRecord):
                return decision.receipt
            if isinstance(decision, AppendLifecycleRecord):
                if decision.attempt.last_sequence is None or (
                    attempt.last_sequence is not None
                    and decision.attempt.last_sequence <= attempt.last_sequence
                ):
                    raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
                attempt = decision.attempt
                continue
            # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
            assert_never(decision)  # pragma: no cover  # pragma: no mutate


@dataclass(frozen=True, slots=True)
class Status:
    """Rebuild and return lifecycle status without advancing authoritative history."""

    projection: LifecycleStatusProjection

    def __call__(self) -> LifecycleStatus:
        return self.projection.rebuild_status()
