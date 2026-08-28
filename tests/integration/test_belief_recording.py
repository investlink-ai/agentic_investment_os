from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from agentic_investment_os.adapters.filesystem_evidence import FilesystemEvidenceVault
from agentic_investment_os.adapters.sqlite_memory import SQLiteBeliefLedger
from agentic_investment_os.application.memory import Record
from agentic_investment_os.domain.identity import EquityInstrumentIdentity, MarketSession
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.entrypoints.configuration import ConfigurationSource
from agentic_investment_os.entrypoints.lifecycle import configure_advance
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
    BeliefGraph,
    BeliefGraphQuery,
    BeliefGraphRefusal,
    BeliefGraphRefusalCode,
    RecordDisposition,
)
from tests._governance import RecordedSessionEligibility
from tests._production_research import ValidProductionModel, production_recorded_evidence
from tests._universe import recorded_portfolio, recorded_universe, runtime_configuration

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class _RefusingEvidenceResolver:
    code: RecordRefusalCode

    def resolve_belief_evidence(
        self,
        _references: tuple[BeliefEvidenceReference, ...],
    ) -> tuple[BeliefEvidenceArtifact, ...] | RecordRefusalCode:
        return self.code


@dataclass(frozen=True, slots=True)
class _EmptyEvidenceResolver:
    def resolve_belief_evidence(
        self,
        _references: tuple[BeliefEvidenceReference, ...],
    ) -> tuple[BeliefEvidenceArtifact, ...] | RecordRefusalCode:
        return ()


@dataclass(frozen=True, slots=True)
class _FixedEvidenceResolver:
    artifact: BeliefEvidenceArtifact

    def resolve_belief_evidence(
        self,
        _references: tuple[BeliefEvidenceReference, ...],
    ) -> tuple[BeliefEvidenceArtifact, ...] | RecordRefusalCode:
        return (self.artifact,)


@dataclass(frozen=True, slots=True)
class _FixedClock:
    hour: int

    def now(self) -> datetime:
        return datetime(2026, 8, 21, self.hour, tzinfo=UTC)


def _seed_evidence(state_root: Path) -> tuple[str, str]:
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    advance = configure_advance(
        sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_portfolio=recorded_portfolio(),
        recorded_evidence=production_recorded_evidence(),
        recorded_model=ValidProductionModel(cio_stance="abstain"),
        session_eligibility=RecordedSessionEligibility(),
        clock=_FixedClock(22),
    )
    assert callable(advance)
    advance(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="belief-evidence-seed",
    )
    record = FilesystemEvidenceVault.open_existing(state_root / "evidence-vault").stored_records()[
        0
    ]
    return record.artifact.artifact_id, record.artifact.content_hash


def _belief_event(artifact_id: str, content_hash: str) -> BeliefEvent:
    return BeliefEvent.create(
        event_id="belief-event-1",
        belief_id="aapl-demand",
        subject=EquityInstrumentIdentity("alpaca-paper", "equity-aapl", "NASDAQ"),
        claim_kind=BeliefClaimKind.EXPECTATION,
        claim="Demand remains resilient over the stated horizon.",
        valid_at=UtcInstant.from_datetime(datetime(2026, 8, 21, 18, tzinfo=UTC)),
        transaction_at=UtcInstant.from_datetime(datetime(2026, 8, 21, 21, tzinfo=UTC)),
        evidence_cutoff=UtcInstant.from_datetime(datetime(2026, 8, 21, 20, tzinfo=UTC)),
        confidence="0.7",
        evidence=(BeliefEvidenceReference(artifact_id, content_hash),),
        falsifiers=("A reported demand contraction would refute the claim.",),
        status=BeliefStatus.ACTIVE,
        transition_from_event_id=None,
        supersedes_event_id=None,
    )


def _belief_transition(previous: BeliefEvent, *, event_id: str) -> BeliefEvent:
    return BeliefEvent.create(
        event_id=event_id,
        belief_id=previous.belief_id,
        subject=previous.subject,
        claim_kind=previous.claim_kind,
        claim="New observed evidence contradicts the prior demand expectation.",
        valid_at=previous.valid_at,
        transaction_at=previous.transaction_at,
        evidence_cutoff=previous.evidence_cutoff,
        confidence="0.3",
        evidence=previous.evidence,
        falsifiers=("Verified demand recovery would challenge the contradiction.",),
        status=BeliefStatus.CONTRADICTED,
        transition_from_event_id=previous.event_id,
        supersedes_event_id=None,
    )


def test_record_replays_the_durable_receipt_after_reopen_and_clock_regression(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    event = _belief_event(*_seed_evidence(state_root))
    record = configure_record(
        sources,
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(22),
    )
    assert isinstance(record, Record)

    appended = record(event.to_payload())

    reopened = configure_record(
        sources,
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(20),
    )
    assert isinstance(reopened, Record)
    replayed = reopened(event.to_payload())
    assert appended.disposition is RecordDisposition.APPENDED
    assert replayed.disposition is RecordDisposition.REPLAYED
    assert appended.event_id == replayed.event_id == event.event_id
    assert appended.ledger_position == replayed.ledger_position == 1
    assert appended.projection_identity == replayed.projection_identity


def test_record_rebuilds_the_same_graph_after_projection_deletion(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    event = _belief_event(*_seed_evidence(state_root))
    record = configure_record(
        sources,
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(22),
    )
    assert isinstance(record, Record)
    record(event.to_payload())
    query = BeliefGraphQuery(
        cutoff=UtcInstant.from_datetime(datetime(2026, 8, 21, 21, tzinfo=UTC)),
        subjects=(event.subject,),
        maximum_belief_events=10,
        maximum_evidence_artifacts=10,
    )

    first = record.graph(query.to_payload())

    assert isinstance(first, BeliefGraph)
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        connection.execute("DROP TABLE belief_graph_projection")
    reopened = configure_record(
        sources,
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(23),
    )
    assert isinstance(reopened, Record)
    rebuilt = reopened.graph(query.to_payload())
    assert rebuilt == first


def test_record_refuses_missing_evidence_without_appending_history(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    _seed_evidence(state_root)
    event = _belief_event("d" * 64, "e" * 64)
    record = configure_record(
        sources,
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(22),
    )
    assert isinstance(record, Record)

    receipt = record(event.to_payload())

    assert receipt.refusal is RecordRefusalCode.EVIDENCE_MISSING
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        count = connection.execute("SELECT COUNT(*) FROM belief_events").fetchone()
    assert count == (0,)


@pytest.mark.parametrize("confidence", ["1e-1000000", "1e+1000000"])
def test_record_refuses_extreme_exponent_confidence_without_allocating_or_appending(
    tmp_path: Path,
    confidence: str,
) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    event = _belief_event(*_seed_evidence(state_root))
    payload = event.to_payload()
    payload["confidence"] = confidence
    record = configure_record(
        sources,
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(22),
    )
    assert isinstance(record, Record)

    receipt = record(payload)

    assert receipt.refusal is RecordRefusalCode.INVALID_EVENT
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        counts = (
            connection.execute("SELECT COUNT(*) FROM belief_events").fetchone(),
            connection.execute("SELECT COUNT(*) FROM belief_ledger_commitments").fetchone(),
            connection.execute("SELECT COUNT(*) FROM belief_ledger_head").fetchone(),
        )
    assert counts == ((0,), (0,), (0,))


@pytest.mark.parametrize(
    ("content_hash", "available_hour", "expected"),
    [
        ("f" * 64, 20, RecordRefusalCode.EVIDENCE_HASH_MISMATCH),
        ("e" * 64, 21, RecordRefusalCode.EVIDENCE_AFTER_CUTOFF),
    ],
)
def test_record_refuses_invalid_evidence_fact_without_appending_history(
    tmp_path: Path,
    content_hash: str,
    available_hour: int,
    expected: RecordRefusalCode,
) -> None:
    state_root = tmp_path / "runtime"
    artifact_id, expected_hash = _seed_evidence(state_root)
    event = _belief_event(artifact_id, expected_hash)
    record = configure_record(
        (ConfigurationSource("test", runtime_configuration(state_root)),),
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(22),
    )
    assert isinstance(record, Record)
    resolver = _FixedEvidenceResolver(
        BeliefEvidenceArtifact(
            artifact_id,
            content_hash if expected is RecordRefusalCode.EVIDENCE_HASH_MISMATCH else expected_hash,
            UtcInstant.from_datetime(datetime(2026, 8, 21, available_hour, tzinfo=UTC)),
        )
    )

    receipt = Record(record.ledger, resolver, _FixedClock(22))(event.to_payload())

    assert receipt.refusal is expected
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        count = connection.execute("SELECT COUNT(*) FROM belief_events").fetchone()
    assert count == (0,)


def test_belief_ledger_translates_resolver_refusals_and_revalidates_graph_evidence(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    event = _belief_event(*_seed_evidence(state_root))
    record = configure_record(sources, repository_root=REPOSITORY_ROOT, clock=_FixedClock(22))
    assert isinstance(record, Record)
    refusing = Record(
        record.ledger,
        _RefusingEvidenceResolver(RecordRefusalCode.INVALID_EVIDENCE),
        _FixedClock(22),
    )

    refused_record = refusing(event.to_payload())
    appended = record(event.to_payload())
    query = BeliefGraphQuery(
        UtcInstant.from_datetime(datetime(2026, 8, 21, 22, tzinfo=UTC)),
        (event.subject,),
        10,
        10,
    )
    refused_graph = refusing.graph(query.to_payload())
    missing_graph = Record(record.ledger, _EmptyEvidenceResolver(), _FixedClock(22)).graph(
        query.to_payload()
    )

    assert refused_record.refusal is RecordRefusalCode.INVALID_EVIDENCE
    assert appended.disposition is RecordDisposition.APPENDED
    assert refused_graph == BeliefGraphRefusal(BeliefGraphRefusalCode.INVALID_EVIDENCE)
    assert missing_graph == BeliefGraphRefusal(BeliefGraphRefusalCode.INVALID_EVIDENCE)


def test_sqlite_belief_ledger_refuses_an_event_not_yet_recordable(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    event = _belief_event(*_seed_evidence(state_root))
    configured = configure_record(
        (ConfigurationSource("test", runtime_configuration(state_root)),),
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(22),
    )
    assert isinstance(configured, Record)
    ledger = SQLiteBeliefLedger(state_root / "lifecycle.sqlite3")

    receipt = ledger.record(event, configured.evidence_resolver, event.evidence_cutoff)

    assert receipt.refusal is RecordRefusalCode.INVALID_EVENT


def test_record_refuses_changed_material_reusing_an_event_identity(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    artifact_id, content_hash = _seed_evidence(state_root)
    original = _belief_event(artifact_id, content_hash)
    changed = BeliefEvent.create(
        event_id=original.event_id,
        belief_id=original.belief_id,
        subject=original.subject,
        claim_kind=original.claim_kind,
        claim="Changed material must not replace the accepted event.",
        valid_at=original.valid_at,
        transaction_at=original.transaction_at,
        evidence_cutoff=original.evidence_cutoff,
        confidence=original.confidence,
        evidence=original.evidence,
        falsifiers=original.falsifiers,
        status=original.status,
        transition_from_event_id=None,
        supersedes_event_id=None,
    )
    record = configure_record(
        sources,
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(22),
    )
    assert isinstance(record, Record)
    record(original.to_payload())

    conflict = record(changed.to_payload())

    assert conflict.refusal is RecordRefusalCode.EVENT_IDENTITY_CONFLICT
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        rows = connection.execute("SELECT event_json FROM belief_events").fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0]) == original.to_payload()


def test_record_concurrent_duplicate_delivery_appends_exactly_once(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    event = _belief_event(*_seed_evidence(state_root))
    records = tuple(
        configure_record(
            sources,
            repository_root=REPOSITORY_ROOT,
            clock=_FixedClock(22),
        )
        for _ in range(2)
    )
    assert all(isinstance(record, Record) for record in records)

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = tuple(pool.map(lambda record: record(event.to_payload()), records))

    assert {receipt.disposition for receipt in receipts} == {
        RecordDisposition.APPENDED,
        RecordDisposition.REPLAYED,
    }
    assert {receipt.ledger_position for receipt in receipts} == {1}
    assert len({receipt.projection_identity for receipt in receipts}) == 1


def test_record_rebuilds_after_projection_content_corruption(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    event = _belief_event(*_seed_evidence(state_root))
    record = configure_record(
        sources,
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(22),
    )
    assert isinstance(record, Record)
    record(event.to_payload())
    query = BeliefGraphQuery(
        cutoff=UtcInstant.from_datetime(datetime(2026, 8, 21, 21, tzinfo=UTC)),
        subjects=(event.subject,),
        maximum_belief_events=10,
        maximum_evidence_artifacts=10,
    )
    expected = record.graph(query.to_payload())
    assert isinstance(expected, BeliefGraph)
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        connection.execute(
            "UPDATE belief_graph_projection SET graph_json = ? WHERE singleton = 1",
            ('{"hostile":"projection"}',),
        )

    rebuilt = record.graph(query.to_payload())

    assert rebuilt == expected


def test_belief_ledger_and_commitments_reject_in_place_update_and_delete(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    event = _belief_event(*_seed_evidence(state_root))
    record = configure_record(
        sources,
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(22),
    )
    assert isinstance(record, Record)
    record(event.to_payload())

    for statement, message in (
        ("UPDATE belief_events SET belief_id = 'changed'", "append-only belief ledger"),
        ("DELETE FROM belief_events", "append-only belief ledger"),
        (
            "UPDATE belief_ledger_commitments SET projection_identity = 'changed'",
            "append-only belief commitment",
        ),
        ("DELETE FROM belief_ledger_commitments", "append-only belief commitment"),
        (
            "UPDATE belief_ledger_head SET ledger_position = ledger_position",
            "invalid belief ledger head",
        ),
        ("DELETE FROM belief_ledger_head", "durable belief ledger head"),
    ):
        with (
            sqlite3.connect(state_root / "lifecycle.sqlite3") as connection,
            pytest.raises(sqlite3.IntegrityError, match=message),
        ):
            connection.execute(statement)


@pytest.mark.parametrize(
    "corruption",
    [
        "tampered",
        "noncontiguous",
        "tail_deleted",
        "missing_commitment",
        "missing_head",
        "recorded_in_future",
    ],
)
def test_belief_graph_fails_closed_on_invalid_authoritative_history(
    tmp_path: Path,
    corruption: str,
) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    event = _belief_event(*_seed_evidence(state_root))
    record = configure_record(
        sources,
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(22),
    )
    assert isinstance(record, Record)
    record(event.to_payload())
    if corruption == "noncontiguous":
        record(_belief_transition(event, event_id="belief-event-2").to_payload())
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        guard = "belief_events_are_append_only_update"
        operation = "UPDATE belief_events SET event_json = '{}' WHERE ledger_position = 1"
        if corruption in {"noncontiguous", "tail_deleted"}:
            guard = "belief_events_are_append_only_delete"
            operation = "DELETE FROM belief_events WHERE ledger_position = 1"
        elif corruption == "missing_commitment":
            guard = "belief_ledger_commitments_are_append_only_delete"
            operation = "DELETE FROM belief_ledger_commitments WHERE ledger_position = 1"
        elif corruption == "missing_head":
            guard = "belief_ledger_head_cannot_be_deleted"
            operation = "DELETE FROM belief_ledger_head WHERE singleton = 1"
        elif corruption == "recorded_in_future":
            operation = (
                "UPDATE belief_events SET recorded_at = "
                "'2026-08-21T20:00:00.000000+00:00' WHERE ledger_position = 1"
            )
        connection.execute(f"DROP TRIGGER {guard}")
        connection.execute(operation)
        action = (
            "DELETE"
            if corruption in {"noncontiguous", "tail_deleted", "missing_commitment", "missing_head"}
            else "UPDATE"
        )
        table = "belief_events"
        message = "append-only belief ledger"
        if corruption == "missing_commitment":
            table = "belief_ledger_commitments"
            message = "append-only belief commitment"
        elif corruption == "missing_head":
            table = "belief_ledger_head"
            message = "durable belief ledger head"
        connection.execute(
            f"""
            CREATE TRIGGER {guard}
            BEFORE {action} ON {table}
            BEGIN SELECT RAISE(ABORT, '{message}'); END
            """
        )
    reopened = configure_record(
        sources,
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(23),
    )
    assert isinstance(reopened, Record)
    record_result = reopened(event.to_payload())
    result = reopened.graph(
        BeliefGraphQuery(
            cutoff=event.transaction_at,
            subjects=(event.subject,),
            maximum_belief_events=10,
            maximum_evidence_artifacts=10,
        ).to_payload()
    )

    assert record_result.refusal is RecordRefusalCode.INVALID_AUTHORITATIVE_HISTORY
    assert result == BeliefGraphRefusal(BeliefGraphRefusalCode.INVALID_AUTHORITATIVE_HISTORY)


def test_belief_history_rejects_coordinated_event_and_commitment_tail_deletion(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    event = _belief_event(*_seed_evidence(state_root))
    record = configure_record(
        sources,
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(22),
    )
    assert isinstance(record, Record)
    record(event.to_payload())
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        connection.execute("DROP TRIGGER belief_events_are_append_only_delete")
        connection.execute("DROP TRIGGER belief_ledger_commitments_are_append_only_delete")
        connection.execute("DELETE FROM belief_events WHERE ledger_position = 1")
        connection.execute("DELETE FROM belief_ledger_commitments WHERE ledger_position = 1")
        connection.execute(
            """
            CREATE TRIGGER belief_events_are_append_only_delete
            BEFORE DELETE ON belief_events
            BEGIN SELECT RAISE(ABORT, 'append-only belief ledger'); END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER belief_ledger_commitments_are_append_only_delete
            BEFORE DELETE ON belief_ledger_commitments
            BEGIN SELECT RAISE(ABORT, 'append-only belief commitment'); END
            """
        )

    reopened = configure_record(
        sources,
        repository_root=REPOSITORY_ROOT,
        clock=_FixedClock(23),
    )
    assert isinstance(reopened, Record)
    record_result = reopened(event.to_payload())
    graph_result = reopened.graph(
        BeliefGraphQuery(
            cutoff=event.transaction_at,
            subjects=(event.subject,),
            maximum_belief_events=10,
            maximum_evidence_artifacts=10,
        ).to_payload()
    )

    assert record_result.refusal is RecordRefusalCode.INVALID_AUTHORITATIVE_HISTORY
    assert graph_result == BeliefGraphRefusal(BeliefGraphRefusalCode.INVALID_AUTHORITATIVE_HISTORY)
