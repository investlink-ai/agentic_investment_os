"""Run one scheduler system-journey action in a fresh process."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentic_investment_os.application.scheduler import Scheduler
from agentic_investment_os.domain.scheduler import PINNED_XNYS_CALENDAR_ID
from agentic_investment_os.entrypoints.scheduler import configure_scheduler
from tests._universe import runtime_configuration
from tests.integration.system._lifecycle_process import _advance, _status

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ARGUMENT_COUNT = 2
INVALID_ARGUMENTS = "expected scheduler state root"
CONFIGURATION_REFUSED = "scheduler system journey configuration was refused"


@dataclass(frozen=True, slots=True)
class FixedSchedulerClock:
    instant: datetime = datetime(2026, 8, 21, 12, 45, tzinfo=UTC)

    def now(self) -> datetime:
        return self.instant


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_type": "market_session_advance",
        "asset_class": "us_equity",
        "calendar_id": PINNED_XNYS_CALENDAR_ID,
        "first_session": "2026-08-21",
        "advance_minutes_before_open": 60,
        "maximum_lateness_minutes": 30,
        "recovery_delay_seconds": 300,
        "maximum_actions_per_run": 20,
    }


def main() -> None:
    if len(sys.argv) != ARGUMENT_COUNT:
        raise RuntimeError(INVALID_ARGUMENTS)
    state_root = Path(sys.argv[1])
    configured = configure_scheduler(
        runtime_configuration(state_root),
        scheduler_policy=_policy(),
        repository_root=REPOSITORY_ROOT,
        advance=_advance(state_root),
        status=_status(state_root),
        clock=FixedSchedulerClock(),
    )
    if not isinstance(configured, Scheduler):
        raise RuntimeError(CONFIGURATION_REFUSED)
    receipt = configured()
    fields = (
        receipt.policy_id,
        ",".join(item.disposition.value for item in receipt.sessions),
        receipt.recorded_at.isoformat(),
        "" if receipt.next_scheduled_at is None else receipt.next_scheduled_at.isoformat(),
    )
    sys.stdout.write("\t".join(fields))


if __name__ == "__main__":
    main()
