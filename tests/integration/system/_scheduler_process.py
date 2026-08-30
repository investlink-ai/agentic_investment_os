"""Run one scheduler system-journey action in a fresh process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agentic_investment_os.application.scheduler import Scheduler
from agentic_investment_os.domain.identity import parse_decision_cycle_identity
from agentic_investment_os.domain.lifecycle import (
    AdvanceFailureReason,
    AdvanceReceipt,
    LifecycleLiveness,
    LifecycleStatus,
    PinnedRunIdentity,
    parse_advance_receipt,
)
from agentic_investment_os.domain.scheduler import PINNED_XNYS_CALENDAR_ID
from agentic_investment_os.entrypoints.scheduler import configure_scheduler
from tests._universe import runtime_configuration

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
LIFECYCLE_PROCESS_MODULE = "tests.integration.system._scheduler_lifecycle_process"
ARGUMENT_COUNT = 2
INVALID_ARGUMENTS = "expected scheduler state root"
CONFIGURATION_REFUSED = "scheduler system journey configuration was refused"
INVALID_LIFECYCLE_RESULT = "scheduler lifecycle process returned an invalid public result"
UNPERMITTED_SCHEDULER_MODULES = frozenset(
    {
        "agentic_investment_os.adapters.decision_signing",
        "agentic_investment_os.entrypoints.lifecycle",
        "tests._decision",
        "tests._production_research",
    }
)


@dataclass(frozen=True, slots=True)
class FixedSchedulerClock:
    instant: datetime = datetime(2026, 8, 24, 13, 15, tzinfo=UTC)

    def now(self) -> datetime:
        return self.instant


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_type": "market_session_advance",
        "asset_class": "us_equity",
        "calendar_id": PINNED_XNYS_CALENDAR_ID,
        "first_session": "2026-08-24",
        "advance_minutes_before_open": 15,
        "maximum_lateness_minutes": 14,
        "recovery_delay_seconds": 300,
        "maximum_actions_per_run": 20,
    }


@dataclass(slots=True)
class LifecycleProcessClient:
    """Invoke public lifecycle capabilities without importing their composition authority."""

    state_root: Path
    last_identity: PinnedRunIdentity | None = field(init=False, default=None)

    def advance(
        self,
        *,
        cycle: object,
        mode: object,
        idempotency_key: object,
    ) -> AdvanceReceipt:
        request = json.dumps(
            {"cycle": cycle, "mode": mode, "idempotency_key": idempotency_key},
            sort_keys=True,
            separators=(",", ":"),
        )
        payload = self._run("advance", request)
        receipt = parse_advance_receipt(payload)
        if receipt is None:
            raise RuntimeError(INVALID_LIFECYCLE_RESULT)
        self.last_identity = receipt.pinned_run_identity
        return receipt

    def status(self) -> LifecycleStatus:
        payload = self._run("status")
        identity = self.last_identity
        if type(payload) is not dict or set(payload) != {
            "cycle",
            "durable_reason",
            "liveness",
            "run_id",
        }:
            raise RuntimeError(INVALID_LIFECYCLE_RESULT)
        cycle = parse_decision_cycle_identity(payload["cycle"])
        try:
            liveness = LifecycleLiveness(payload["liveness"])
            durable_reason = (
                None
                if payload["durable_reason"] is None
                else AdvanceFailureReason(payload["durable_reason"])
            )
        except (TypeError, ValueError) as error:
            raise RuntimeError(INVALID_LIFECYCLE_RESULT) from error
        if (
            identity is None
            or type(payload["run_id"]) is not str
            or payload["run_id"] != identity.run_id
            or cycle != identity.cycle
        ):
            raise RuntimeError(INVALID_LIFECYCLE_RESULT)
        return LifecycleStatus(
            None,
            cycle,
            cycle,
            identity,
            liveness,
            durable_reason,
            None,
        )

    def _run(self, action: str, request: str | None = None) -> object:
        arguments = [
            sys.executable,
            "-m",
            LIFECYCLE_PROCESS_MODULE,
            action,
            str(self.state_root),
        ]
        if request is not None:
            arguments.append(request)
        completed = subprocess.run(  # noqa: S603
            arguments,
            cwd=REPOSITORY_ROOT,
            env={"LC_ALL": "C", "PATH": os.defpath, "PYTHONHASHSEED": "0"},
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(INVALID_LIFECYCLE_RESULT)
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(INVALID_LIFECYCLE_RESULT) from error


def main() -> None:
    if len(sys.argv) != ARGUMENT_COUNT:
        raise RuntimeError(INVALID_ARGUMENTS)
    state_root = Path(sys.argv[1])
    lifecycle = LifecycleProcessClient(state_root)
    configured = configure_scheduler(
        runtime_configuration(state_root),
        scheduler_policy=_policy(),
        repository_root=REPOSITORY_ROOT,
        advance=lifecycle.advance,
        status=lifecycle.status,
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
        str(UNPERMITTED_SCHEDULER_MODULES.isdisjoint(sys.modules)).lower(),
    )
    sys.stdout.write("\t".join(fields))


if __name__ == "__main__":
    main()
