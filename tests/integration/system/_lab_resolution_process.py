"""Run one isolated Research Lab resolution action in a fresh process."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentic_investment_os.adapters.recorded_model import (
    RecordedEvidenceCollector,
    RecordedModelFixture,
)
from agentic_investment_os.application.replay import Replay
from agentic_investment_os.entrypoints.lab import configure_replay
from agentic_investment_os.research.model import ModelCallDisposition
from tests._replay import resolution_bytes, resolution_replay_request

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ARGUMENT_COUNT = 3
AUTHORITY_SENTINEL_NAMES = frozenset(
    {
        "LAB_JOURNEY_BROKER_SENTINEL",
        "LAB_JOURNEY_CHAMPION_SENTINEL",
        "LAB_JOURNEY_CREDENTIAL_SENTINEL",
        "LAB_JOURNEY_PACKET_SENTINEL",
    }
)
INVALID_ARGUMENTS = "expected resolve or replay and one Lab root"
CONFIGURATION_REFUSED = "Research Lab resolution system journey configuration was refused"
MISSING_CIO = "Research Lab resolution system journey did not reach CIO"


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime = datetime(2026, 8, 24, 20, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


def _model(action: str) -> RecordedEvidenceCollector:
    fixtures = (
        ()
        if action == "replay"
        else tuple(
            RecordedModelFixture(
                ModelCallDisposition.RESPONDED,
                payload,
                "codex-subscription/test-model",
                input_tokens=10,
                output_tokens=20,
                turns=1,
                elapsed_milliseconds=5,
            )
            for payload in resolution_bytes()
        )
    )
    return RecordedEvidenceCollector(fixtures)


def main() -> None:
    if len(sys.argv) != ARGUMENT_COUNT or sys.argv[1] not in ("resolve", "replay"):
        raise RuntimeError(INVALID_ARGUMENTS)
    action = sys.argv[1]
    lab_root = Path(sys.argv[2])
    model = _model(action)
    capability = configure_replay(
        namespace="lab.synthetic.aapl",
        lab_state_root=str(lab_root),
        production_state_roots=(lab_root.parent / "production",),
        repository_root=REPOSITORY_ROOT,
        model=model,
        clock=FixedClock(),
    )
    if not isinstance(capability, Replay):
        raise RuntimeError(CONFIGURATION_REFUSED)
    receipt = capability(resolution_replay_request())
    if receipt.cio_resolution is None:
        raise RuntimeError(MISSING_CIO)
    fields = (
        receipt.disposition.value,
        receipt.cio_resolution.stance.value,
        str(len(receipt.role_calls)),
        str(model.unique_effect_count),
        str(AUTHORITY_SENTINEL_NAMES.isdisjoint(os.environ)).lower(),
    )
    sys.stdout.write("\t".join(fields))


if __name__ == "__main__":
    main()
