"""Run one lifecycle system-journey action in a fresh process."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from agentic_investment_os.application.lifecycle import Advance, Status
from agentic_investment_os.domain.lifecycle import (
    AdvanceAttempt,
    AppendLifecycleRecord,
    LifecycleCommand,
    LifecycleDecision,
    LifecycleEvent,
    LifecycleLedger,
    LifecyclePhase,
)
from agentic_investment_os.entrypoints.configuration import ConfigurationSource
from agentic_investment_os.entrypoints.lifecycle import configure_advance, configure_status

if TYPE_CHECKING:
    from agentic_investment_os.domain.temporal import UtcInstant

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
INTERRUPTED_EXIT_CODE = 75
ARGUMENT_COUNT = 3
SESSION = "2026-08-21"
MODE = "champion"
IDEMPOTENCY_KEY = "lifecycle-system-journey"
AUTHORITY_SENTINEL_NAMES = frozenset(
    {
        "SYSTEM_JOURNEY_BROKER_SENTINEL",
        "SYSTEM_JOURNEY_MODEL_SENTINEL",
        "SYSTEM_JOURNEY_MUTABLE_ACCOUNT_SENTINEL",
    }
)
ADVANCE_CONFIGURATION_REFUSED = "system journey Advance configuration was refused"
STATUS_CONFIGURATION_REFUSED = "system journey Status configuration was refused"
ADVANCE_IDENTITY_MISSING = "system journey Advance did not return a pinned identity"
STATUS_IDENTITY_MISSING = "system journey Status did not return a pinned identity"
INTERRUPTION_NOT_REACHED = "system journey did not reach the interruption point"
INVALID_ARGUMENTS = "expected action and state root"
UNKNOWN_ACTION = "unknown system journey action"


@dataclass(frozen=True, slots=True)
class FixedClock:
    instant: datetime = datetime(2026, 8, 21, 22, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.instant


@dataclass(frozen=True, slots=True)
class InterruptAfterReconcileLedger:
    delegate: LifecycleLedger

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        recorded_at: UtcInstant,
    ) -> LifecycleDecision:
        decision = self.delegate.advance_step(command, attempt, recorded_at)
        if (
            isinstance(decision, AppendLifecycleRecord)
            and isinstance(decision.record, LifecycleEvent)
            and decision.record.completed_phase is LifecyclePhase.RECONCILE_PRIOR_STATE
        ):
            os._exit(INTERRUPTED_EXIT_CODE)
        return decision


def _sources(state_root: Path) -> tuple[ConfigurationSource, ...]:
    return (
        ConfigurationSource(
            "lifecycle-system-journey",
            {"schema_version": 1, "state_root": str(state_root)},
        ),
    )


def _advance(state_root: Path) -> Advance:
    capability = configure_advance(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        clock=FixedClock(),
    )
    if not isinstance(capability, Advance):
        raise RuntimeError(ADVANCE_CONFIGURATION_REFUSED)
    return capability


def _status(state_root: Path) -> Status:
    capability = configure_status(_sources(state_root), repository_root=REPOSITORY_ROOT)
    if not isinstance(capability, Status):
        raise RuntimeError(STATUS_CONFIGURATION_REFUSED)
    return capability


def _ambient_authority_absent() -> str:
    return str(AUTHORITY_SENTINEL_NAMES.isdisjoint(os.environ)).lower()


def _emit_advance(state_root: Path) -> None:
    receipt = _advance(state_root)(
        session=SESSION,
        mode=MODE,
        idempotency_key=IDEMPOTENCY_KEY,
    )
    identity = receipt.pinned_run_identity
    if identity is None:
        raise RuntimeError(ADVANCE_IDENTITY_MISSING)
    fields = (
        "advance",
        receipt.disposition.value,
        "" if receipt.completed_phase is None else receipt.completed_phase.value,
        "" if receipt.recovery is None else receipt.recovery.value,
        identity.run_id,
        str(identity.configuration_version),
        identity.configuration_hash,
        _ambient_authority_absent(),
    )
    sys.stdout.write("\t".join(fields))


def _emit_status(state_root: Path) -> None:
    status = _status(state_root)()
    identity = status.pinned_run_identity
    if identity is None:
        raise RuntimeError(STATUS_IDENTITY_MISSING)
    fields = (
        "status",
        "" if status.active_phase is None else status.active_phase.value,
        (
            ""
            if status.last_completed_session is None
            else status.last_completed_session.isoformat()
        ),
        status.liveness.value,
        "" if status.durable_reason is None else status.durable_reason.value,
        identity.run_id,
        str(identity.configuration_version),
        identity.configuration_hash,
        _ambient_authority_absent(),
    )
    sys.stdout.write("\t".join(fields))


def _interrupt_after_reconcile(state_root: Path) -> None:
    configured = _advance(state_root)
    interrupted = Advance(
        ledger=InterruptAfterReconcileLedger(configured.ledger),
        configuration_version=configured.configuration_version,
        configuration_hash=configured.configuration_hash,
        clock=configured.clock,
    )
    interrupted(session=SESSION, mode=MODE, idempotency_key=IDEMPOTENCY_KEY)
    raise RuntimeError(INTERRUPTION_NOT_REACHED)


def main() -> None:
    if len(sys.argv) != ARGUMENT_COUNT:
        raise RuntimeError(INVALID_ARGUMENTS)
    action = sys.argv[1]
    state_root = Path(sys.argv[2])
    if action == "interrupt-after-reconcile":
        _interrupt_after_reconcile(state_root)
        return
    if action == "advance":
        _emit_advance(state_root)
        return
    if action == "status":
        _emit_status(state_root)
        return
    raise RuntimeError(UNKNOWN_ACTION)


if __name__ == "__main__":
    main()
