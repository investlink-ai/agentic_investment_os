"""Run one Belief Record system-journey action in a fresh process."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from agentic_investment_os.adapters.filesystem_evidence import FilesystemEvidenceVault
from agentic_investment_os.application.lifecycle import Advance
from agentic_investment_os.application.memory import Record
from agentic_investment_os.domain.identity import EquityInstrumentIdentity, MarketSession
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.entrypoints.configuration import ConfigurationSource
from agentic_investment_os.entrypoints.memory import configure_record
from agentic_investment_os.memory.admission import (
    BeliefClaimKind,
    BeliefEvent,
    BeliefEvidenceArtifact,
    BeliefEvidenceReference,
    BeliefStatus,
    RecordRefusalCode,
)
from agentic_investment_os.memory.beliefs import (
    BeliefEvidenceResolver,
    BeliefGraph,
    BeliefGraphQuery,
)
from tests._decision import configure_advance
from tests._governance import RecordedSessionEligibility
from tests._production_research import ValidProductionModel, production_recorded_evidence
from tests._universe import recorded_portfolio, recorded_universe, runtime_configuration

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
INTERRUPTED_EXIT_CODE = 75
ARGUMENT_COUNT = 3
AUTHORITY_SENTINEL_NAMES = frozenset(
    {
        "BELIEF_JOURNEY_BROKER_SENTINEL",
        "BELIEF_JOURNEY_MODEL_SENTINEL",
        "BELIEF_JOURNEY_MUTABLE_ACCOUNT_SENTINEL",
    }
)
INVALID_ARGUMENTS = "expected action and state root"
CONFIGURATION_REFUSED = "Belief Record system journey configuration was refused"
UNEXPECTED_REFUSAL = "Belief Record system journey returned a refusal"
UNKNOWN_ACTION = "unknown Belief Record system journey action"


@dataclass(frozen=True, slots=True)
class FixedClock:
    instant: datetime = datetime(2026, 8, 21, 22, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.instant


@dataclass(frozen=True, slots=True)
class InterruptAfterEvidenceResolution:
    delegate: BeliefEvidenceResolver

    def resolve_belief_evidence(
        self,
        references: tuple[BeliefEvidenceReference, ...],
    ) -> tuple[BeliefEvidenceArtifact, ...] | RecordRefusalCode:
        self.delegate.resolve_belief_evidence(references)
        os._exit(INTERRUPTED_EXIT_CODE)


def _sources(state_root: Path) -> tuple[ConfigurationSource, ...]:
    return (ConfigurationSource("belief-system-journey", runtime_configuration(state_root)),)


def _seed(state_root: Path) -> None:
    capability = configure_advance(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_portfolio=recorded_portfolio(),
        recorded_evidence=production_recorded_evidence(),
        recorded_model=ValidProductionModel(cio_stance="abstain"),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(),
    )
    if not isinstance(capability, Advance):
        raise RuntimeError(CONFIGURATION_REFUSED)
    capability(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="belief-system-evidence",
    )
    sys.stdout.write("seeded")


def _event(state_root: Path) -> BeliefEvent:
    stored = FilesystemEvidenceVault.open_existing(state_root / "evidence-vault").stored_records()[
        0
    ]
    return BeliefEvent.create(
        event_id="belief-system-event",
        belief_id="aapl-demand",
        subject=EquityInstrumentIdentity("alpaca-paper", "equity-aapl", "NASDAQ"),
        claim_kind=BeliefClaimKind.EXPECTATION,
        claim="Demand remains resilient over the stated horizon.",
        valid_at=UtcInstant.from_datetime(datetime(2026, 8, 21, 18, tzinfo=UTC)),
        transaction_at=UtcInstant.from_datetime(datetime(2026, 8, 21, 21, tzinfo=UTC)),
        evidence_cutoff=UtcInstant.from_datetime(datetime(2026, 8, 21, 20, tzinfo=UTC)),
        confidence="0.7",
        evidence=(
            BeliefEvidenceReference(
                stored.artifact.artifact_id,
                stored.artifact.content_hash,
            ),
        ),
        falsifiers=("A reported demand contraction would refute the claim.",),
        status=BeliefStatus.ACTIVE,
        transition_from_event_id=None,
        supersedes_event_id=None,
    )


def _record(state_root: Path) -> Record:
    capability = configure_record(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        clock=FixedClock(),
    )
    if not isinstance(capability, Record):
        raise RuntimeError(CONFIGURATION_REFUSED)
    return capability


def _ambient_authority_absent() -> str:
    return str(AUTHORITY_SENTINEL_NAMES.isdisjoint(os.environ)).lower()


def _emit_record(state_root: Path) -> None:
    receipt = _record(state_root)(_event(state_root).to_payload())
    if (
        receipt.event_id is None
        or receipt.ledger_position is None
        or receipt.projection_identity is None
    ):
        raise RuntimeError(UNEXPECTED_REFUSAL)
    fields = (
        "record",
        receipt.disposition.value,
        receipt.event_id,
        str(receipt.ledger_position),
        receipt.projection_identity,
        _ambient_authority_absent(),
    )
    sys.stdout.write("\t".join(fields))


def _emit_graph(state_root: Path) -> None:
    event = _event(state_root)
    graph = _record(state_root).graph(
        BeliefGraphQuery(
            cutoff=UtcInstant.from_datetime(FixedClock().now()),
            subjects=(event.subject,),
            maximum_belief_events=10,
            maximum_evidence_artifacts=10,
        ).to_payload()
    )
    if not isinstance(graph, BeliefGraph):
        raise RuntimeError(UNEXPECTED_REFUSAL)
    fields = (
        "graph",
        graph.content_hash,
        str(len(graph.belief_nodes)),
        str(len(graph.evidence_nodes)),
        str(graph.omitted_belief_events),
        _ambient_authority_absent(),
    )
    sys.stdout.write("\t".join(fields))


def _interrupt_before_append(state_root: Path) -> None:
    configured = _record(state_root)
    interrupted = Record(
        ledger=configured.ledger,
        evidence_resolver=InterruptAfterEvidenceResolution(configured.evidence_resolver),
        clock=configured.clock,
    )
    interrupted(_event(state_root).to_payload())
    raise RuntimeError(UNEXPECTED_REFUSAL)


def main() -> None:
    if len(sys.argv) != ARGUMENT_COUNT:
        raise RuntimeError(INVALID_ARGUMENTS)
    action = sys.argv[1]
    state_root = Path(sys.argv[2])
    if action == "seed":
        _seed(state_root)
        return
    if action == "interrupt-before-append":
        _interrupt_before_append(state_root)
        return
    if action == "record":
        _emit_record(state_root)
        return
    if action == "graph":
        _emit_graph(state_root)
        return
    raise RuntimeError(UNKNOWN_ACTION)


if __name__ == "__main__":
    main()
