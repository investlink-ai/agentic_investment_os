"""Run one universe-snapshot Advance call in an isolated process for recovery tests."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from agentic_investment_os.application.lifecycle import Advance
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.entrypoints.configuration import ConfigurationSource
from agentic_investment_os.entrypoints.lifecycle import configure_advance
from tests._universe import recorded_universe, runtime_configuration

_ARGUMENT_ERROR = "expected state root, session, mode, and idempotency key"
_ARGUMENT_COUNT = 5
_CONFIGURATION_ERROR = "fresh-process lifecycle configuration was refused"
_RECEIPT_ERROR = "fresh-process Advance returned an invalid receipt"


@dataclass(frozen=True)
class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 21, 22, 0, tzinfo=UTC)


def main() -> None:
    if len(sys.argv) != _ARGUMENT_COUNT:
        raise RuntimeError(_ARGUMENT_ERROR)
    state_root, session, mode, idempotency_key = sys.argv[1:]
    repository_root = Path(__file__).resolve().parents[2]
    configured = configure_advance(
        (
            ConfigurationSource(
                "fresh-process-test",
                runtime_configuration(Path(state_root)),
            ),
        ),
        repository_root=repository_root,
        recorded_universe=recorded_universe(),
        clock=FixedClock(),
    )
    if not isinstance(configured, Advance):
        raise RuntimeError(_CONFIGURATION_ERROR)
    receipt = configured(
        cycle=(
            MarketSession(date.fromisoformat(session)).to_payload()
            if session != "invalid"
            else session
        ),
        mode=mode,
        idempotency_key=idempotency_key,
    )
    if receipt.pinned_run_identity is None:
        if receipt.failure_reason is None:
            raise RuntimeError(_RECEIPT_ERROR)
        result = receipt.failure_reason.value
    else:
        result = receipt.pinned_run_identity.run_id
    fields = (
        receipt.disposition.value,
        "" if receipt.completed_phase is None else receipt.completed_phase.phase.value,
        "" if receipt.recovery is None else receipt.recovery.value,
        result,
    )
    sys.stdout.write("\t".join(fields))


if __name__ == "__main__":
    main()
