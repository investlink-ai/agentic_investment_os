from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentic_investment_os.domain.identity import EquityInstrumentIdentity
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.memory.admission import (
    BeliefClaimKind,
    BeliefEvent,
    BeliefEvidenceArtifact,
    BeliefEvidenceReference,
    BeliefStatus,
    RecordRefusalCode,
    canonical_belief_event_payload,
    validate_belief_event,
    validate_belief_evidence,
)
from agentic_investment_os.memory.beliefs import (
    AppendBeliefRecord,
    BeliefGraph,
    BeliefGraphEdgeKind,
    BeliefGraphQuery,
    BeliefHistory,
    BeliefLedgerEntry,
    RecordDisposition,
    RecordReceipt,
    project_belief_graph,
)
from agentic_investment_os.memory.reducer import (
    belief_commitment_identity,
    belief_projection_identity,
    decide_belief_record,
    validate_belief_history_integrity,
    validate_belief_transition_history,
)

SECOND_LEDGER_POSITION = 2
FIRST_PROJECTION_IDENTITY = "dd4df4a0d063a69f5a0a6ee79d12856a7f5449becfc9a78ad7d6bdd809c5b8a6"


def _instant(hour: int) -> UtcInstant:
    return UtcInstant.from_datetime(datetime(2026, 8, 21, hour, tzinfo=UTC))


def _belief_event(  # noqa: PLR0913 - fixture exposes each material transition difference.
    *,
    event_id: str = "belief-event-1",
    claim: str = "Demand remains resilient over the stated horizon.",
    confidence: str = "0.7000",
    evidence_hash: str = "a",
    falsifier: str = "A reported demand contraction would refute the claim.",
    status: BeliefStatus = BeliefStatus.ACTIVE,
    transition_from_event_id: str | None = None,
    supersedes_event_id: str | None = None,
    belief_id: str = "aapl-demand",
    subject: EquityInstrumentIdentity | None = None,
    claim_kind: BeliefClaimKind = BeliefClaimKind.EXPECTATION,
    transaction_hour: int = 20,
) -> BeliefEvent:
    return BeliefEvent.create(
        event_id=event_id,
        belief_id=belief_id,
        subject=subject
        if subject is not None
        else EquityInstrumentIdentity("alpaca-paper", "equity-aapl", "NASDAQ"),
        claim_kind=claim_kind,
        claim=claim,
        valid_at=_instant(18),
        transaction_at=_instant(transaction_hour),
        evidence_cutoff=_instant(19),
        confidence=confidence,
        evidence=(BeliefEvidenceReference(evidence_hash * 64, "b" * 64),),
        falsifiers=(falsifier,),
        status=status,
        transition_from_event_id=transition_from_event_id,
        supersedes_event_id=supersedes_event_id,
    )


def _forged_event(event: BeliefEvent, field: str, value: object) -> BeliefEvent:
    forged = copy.copy(event)
    # Authoritative admission must defend against an in-memory typed object forged past
    # dataclass initialization.
    object.__setattr__(forged, field, value)
    if field != "content_hash":
        try:
            payload = forged.to_payload()
            material = {key: item for key, item in payload.items() if key != "content_hash"}
            encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        except (AttributeError, TypeError):
            return forged
        object.__setattr__(forged, "content_hash", hashlib.sha256(encoded).hexdigest())
    return forged


def test_critical_admission_revalidates_every_material_typed_event_dimension() -> None:
    event = _belief_event()
    invalid_reference = copy.copy(event.evidence[0])
    object.__setattr__(invalid_reference, "artifact_id", "invalid")
    invalid_events = (
        _forged_event(event, "event_id", "INVALID"),
        _forged_event(event, "belief_id", "INVALID"),
        _forged_event(event, "subject", object()),
        _forged_event(event, "claim_kind", "expectation"),
        _forged_event(event, "claim", " leading"),
        _forged_event(event, "claim", ""),
        _forged_event(event, "claim", "contains\x00null"),
        _forged_event(event, "claim", "x" * 4_001),
        _forged_event(event, "valid_at", object()),
        _forged_event(event, "transaction_at", object()),
        _forged_event(event, "evidence_cutoff", object()),
        _forged_event(event, "valid_at", _instant(21)),
        _forged_event(event, "evidence_cutoff", _instant(21)),
        _forged_event(event, "confidence", 1),
        _forged_event(event, "confidence", None),
        _forged_event(event, "confidence", "not-a-number"),
        _forged_event(event, "confidence", "NaN"),
        _forged_event(event, "confidence", "-0.1"),
        _forged_event(event, "confidence", "1.1"),
        _forged_event(event, "confidence", "0.7000"),
        _forged_event(event, "status", "active"),
        _forged_event(event, "transition_from_event_id", "INVALID"),
        _forged_event(event, "supersedes_event_id", "INVALID"),
        _forged_event(event, "content_hash", "invalid"),
        _forged_event(event, "evidence", []),
        _forged_event(event, "evidence", ()),
        _forged_event(event, "evidence", (object(),)),
        _forged_event(event, "evidence", (invalid_reference,)),
        _forged_event(event, "evidence", (event.evidence[0], event.evidence[0])),
        _forged_event(event, "falsifiers", []),
        _forged_event(event, "falsifiers", ()),
        _forged_event(event, "falsifiers", (object(),)),
        _forged_event(event, "falsifiers", (event.falsifiers[0], event.falsifiers[0])),
        _forged_event(event, "content_hash", "0" * 64),
    )

    assert validate_belief_event(event)
    assert canonical_belief_event_payload(event) == event.to_payload()
    assert not validate_belief_event(object())
    for index, candidate in enumerate(invalid_events):
        assert not validate_belief_event(candidate), index
    with pytest.raises(ValueError, match="invalid belief event"):
        canonical_belief_event_payload(object())  # type: ignore[arg-type]


def test_critical_admission_accepts_exact_contract_boundaries() -> None:
    event = _belief_event()
    evidence = tuple(BeliefEvidenceReference(f"{index:064x}", "b" * 64) for index in range(32))
    boundary_events = (
        _forged_event(event, "valid_at", event.transaction_at),
        _forged_event(event, "transition_from_event_id", "prior-event"),
        _forged_event(event, "supersedes_event_id", "prior-event"),
        _forged_event(event, "confidence", "0"),
        _forged_event(event, "confidence", "1"),
        _forged_event(event, "confidence", "0.12345678901234"),
        _forged_event(event, "claim", "x"),
        _forged_event(event, "claim", "x" * 4_000),
        _forged_event(event, "evidence", evidence),
        _forged_event(event, "falsifiers", ("x",)),
        _forged_event(
            event,
            "falsifiers",
            tuple(f"falsifier-{index:02d}" for index in range(16)),
        ),
        _forged_event(event, "falsifiers", ("x" * 1_000,)),
    )

    for candidate in boundary_events:
        assert validate_belief_event(candidate)


def test_critical_evidence_admission_rejects_hostile_container_and_artifact_types() -> None:
    event = _belief_event()

    assert validate_belief_evidence((event,), ()) is RecordRefusalCode.EVIDENCE_MISSING
    assert (
        validate_belief_evidence((event,), [])  # type: ignore[arg-type]
        is RecordRefusalCode.INVALID_EVIDENCE
    )
    assert (
        validate_belief_evidence((event,), (object(),))  # type: ignore[arg-type]
        is RecordRefusalCode.INVALID_EVIDENCE
    )
    assert (
        validate_belief_evidence([event], ())  # type: ignore[arg-type]
        is RecordRefusalCode.INVALID_EVIDENCE
    )
    available_at_cutoff = BeliefEvidenceArtifact(
        event.evidence[0].artifact_id,
        event.evidence[0].content_hash,
        event.evidence_cutoff,
    )
    assert validate_belief_evidence((event,), (available_at_cutoff,)) is None


def test_commitment_identity_binds_position_projection_and_previous_commitment() -> None:
    first = belief_commitment_identity(None, 1, "a" * 64)

    assert first == "1a7e2da58fbd18ba16e3436dc57c45985c0001c87a38f09ffe6edad6ddddb942"
    assert belief_commitment_identity(first, 2, "b" * 64) != first


def test_critical_reducer_refuses_invalid_admission_time_and_history_shapes() -> None:
    event = _belief_event()
    appended = decide_belief_record(BeliefHistory(()), event, event.transaction_at)
    assert isinstance(appended, AppendBeliefRecord)
    invalid_event = _forged_event(event, "content_hash", "0" * 64)
    wrong_projection = BeliefLedgerEntry(
        1,
        event,
        event.transaction_at,
        "0" * 64,
    )
    position_only = BeliefLedgerEntry(
        2,
        event,
        event.transaction_at,
        appended.entry.projection_identity,
    )
    duplicate_transition = _belief_event(
        event_id=event.event_id,
        status=BeliefStatus.WEAKENED,
        transition_from_event_id=event.event_id,
    )
    duplicate_event = BeliefLedgerEntry(
        2,
        duplicate_transition,
        duplicate_transition.transaction_at,
        belief_projection_identity(
            appended.entry.projection_identity,
            duplicate_transition,
            duplicate_transition.transaction_at,
            2,
        ),
    )

    assert decide_belief_record(BeliefHistory(()), invalid_event, event.transaction_at) == (
        RecordReceipt.refused(RecordRefusalCode.INVALID_EVENT)
    )
    assert decide_belief_record(
        BeliefHistory(()),
        event,
        object(),  # type: ignore[arg-type]
    ) == RecordReceipt.refused(RecordRefusalCode.INVALID_EVENT)
    assert decide_belief_record(BeliefHistory(()), event, _instant(19)) == (
        RecordReceipt.refused(RecordRefusalCode.INVALID_EVENT)
    )
    assert not validate_belief_history_integrity([])  # type: ignore[arg-type]
    assert not validate_belief_history_integrity((object(),))  # type: ignore[arg-type]
    assert not validate_belief_history_integrity((wrong_projection,))
    assert not validate_belief_history_integrity((position_only,))
    assert not validate_belief_history_integrity((appended.entry, duplicate_event))


def test_record_decision_appends_then_replays_the_same_event() -> None:
    event = _belief_event()

    decision = decide_belief_record(BeliefHistory(()), event, event.transaction_at)

    assert isinstance(decision, AppendBeliefRecord)
    assert decision.entry.ledger_position == 1
    assert decision.receipt == RecordReceipt(
        disposition=RecordDisposition.APPENDED,
        event_id=event.event_id,
        ledger_position=1,
        projection_identity=decision.entry.projection_identity,
        refusal=None,
    )

    replay = decide_belief_record(
        BeliefHistory((decision.entry,)),
        event,
        _instant(19),
    )

    assert replay == RecordReceipt(
        disposition=RecordDisposition.REPLAYED,
        event_id=event.event_id,
        ledger_position=1,
        projection_identity=decision.entry.projection_identity,
        refusal=None,
    )
    assert decision.entry.projection_identity == FIRST_PROJECTION_IDENTITY


def test_record_decision_refuses_changed_content_reusing_an_event_identity() -> None:
    event = _belief_event()
    appended = decide_belief_record(BeliefHistory(()), event, event.transaction_at)
    assert isinstance(appended, AppendBeliefRecord)

    conflict = decide_belief_record(
        BeliefHistory((appended.entry,)),
        _belief_event(claim="Changed content must not overwrite the original event."),
        event.transaction_at,
    )

    assert conflict == RecordReceipt.refused(RecordRefusalCode.EVENT_IDENTITY_CONFLICT)


@pytest.mark.parametrize(
    ("status", "transition_from_event_id", "supersedes_event_id"),
    [
        (BeliefStatus.WEAKENED, None, None),
        (BeliefStatus.ACTIVE, "unknown-event", None),
        (BeliefStatus.ACTIVE, None, "unknown-event"),
    ],
)
def test_first_belief_event_must_be_an_unlinked_active_state(
    status: BeliefStatus,
    transition_from_event_id: str | None,
    supersedes_event_id: str | None,
) -> None:
    decision = decide_belief_record(
        BeliefHistory(()),
        _belief_event(
            status=status,
            transition_from_event_id=transition_from_event_id,
            supersedes_event_id=supersedes_event_id,
        ),
        _instant(20),
    )

    assert decision == RecordReceipt.refused(RecordRefusalCode.INVALID_TRANSITION)


def test_record_decision_preserves_the_prior_state_when_a_belief_is_contradicted() -> None:
    original = _belief_event()
    first = decide_belief_record(BeliefHistory(()), original, original.transaction_at)
    assert isinstance(first, AppendBeliefRecord)
    contradicted = _belief_event(
        event_id="belief-event-2",
        claim="New observed evidence contradicts the prior demand expectation.",
        confidence="0.3",
        evidence_hash="c",
        falsifier="A subsequent verified demand recovery would challenge the contradiction.",
        status=BeliefStatus.CONTRADICTED,
        transition_from_event_id=original.event_id,
    )

    second = decide_belief_record(
        BeliefHistory((first.entry,)),
        contradicted,
        contradicted.transaction_at,
    )

    assert isinstance(second, AppendBeliefRecord)
    assert second.entry.ledger_position == SECOND_LEDGER_POSITION
    history = BeliefHistory((first.entry, second.entry))
    assert history.entries[0].event == original
    assert history.entries[1].event == contradicted


def test_belief_evidence_observed_after_the_pinned_cutoff_is_refused() -> None:
    event = _belief_event()
    artifact = BeliefEvidenceArtifact(
        artifact_id=event.evidence[0].artifact_id,
        content_hash=event.evidence[0].content_hash,
        available_at=_instant(20),
    )

    refusal = validate_belief_evidence((event,), (artifact,))

    assert refusal is RecordRefusalCode.EVIDENCE_AFTER_CUTOFF


def test_belief_evidence_requires_unique_matching_artifact_facts() -> None:
    event = _belief_event()
    mismatched = BeliefEvidenceArtifact("a" * 64, "c" * 64, _instant(18))
    duplicate = BeliefEvidenceArtifact("a" * 64, "b" * 64, _instant(18))

    assert (
        validate_belief_evidence((event,), (mismatched,))
        is RecordRefusalCode.EVIDENCE_HASH_MISMATCH
    )
    assert (
        validate_belief_evidence((event,), (duplicate, duplicate))
        is RecordRefusalCode.INVALID_EVIDENCE
    )
    with pytest.raises(ValueError, match="invalid belief history"):
        project_belief_graph(
            BeliefHistory(
                (
                    BeliefLedgerEntry(
                        1,
                        event,
                        event.transaction_at,
                        belief_projection_identity(None, event, event.transaction_at, 1),
                    ),
                )
            ),
            BeliefGraphQuery(_instant(20), (event.subject,), 1, 1),
            (mismatched,),
        )


def test_belief_graph_applies_relevance_and_size_bounds_deterministically() -> None:
    original = _belief_event()
    first = decide_belief_record(BeliefHistory(()), original, original.transaction_at)
    assert isinstance(first, AppendBeliefRecord)
    contradicted = _belief_event(
        event_id="belief-event-2",
        claim="New observed evidence contradicts the prior demand expectation.",
        confidence="0.3",
        evidence_hash="c",
        falsifier="A subsequent verified demand recovery would challenge the contradiction.",
        status=BeliefStatus.CONTRADICTED,
        transition_from_event_id=original.event_id,
    )
    second = decide_belief_record(
        BeliefHistory((first.entry,)),
        contradicted,
        contradicted.transaction_at,
    )
    assert isinstance(second, AppendBeliefRecord)
    history = BeliefHistory((first.entry, second.entry))
    artifacts = (
        BeliefEvidenceArtifact("a" * 64, "b" * 64, _instant(18)),
        BeliefEvidenceArtifact("c" * 64, "b" * 64, _instant(18)),
    )
    query = BeliefGraphQuery(
        cutoff=_instant(20),
        subjects=(original.subject,),
        maximum_belief_events=1,
        maximum_evidence_artifacts=1,
    )

    graph = project_belief_graph(history, query, artifacts)

    assert isinstance(graph, BeliefGraph)
    assert tuple(node.event.event_id for node in graph.belief_nodes) == ("belief-event-2",)
    assert tuple(node.artifact_id for node in graph.evidence_nodes) == ("c" * 64,)
    assert graph.omitted_belief_events == 1
    assert graph.omitted_evidence_artifacts == 0
    assert graph == project_belief_graph(history, query, artifacts)


def test_correction_appends_and_names_the_preserved_event_it_supersedes() -> None:
    original = _belief_event()
    first = decide_belief_record(BeliefHistory(()), original, original.transaction_at)
    assert isinstance(first, AppendBeliefRecord)
    weakened = _belief_event(
        event_id="belief-event-2",
        confidence="0.5",
        evidence_hash="c",
        status=BeliefStatus.WEAKENED,
        transition_from_event_id=original.event_id,
    )
    second = decide_belief_record(
        BeliefHistory((first.entry,)),
        weakened,
        weakened.transaction_at,
    )
    assert isinstance(second, AppendBeliefRecord)
    correction = _belief_event(
        event_id="belief-event-3",
        claim="The corrected interpretation restores the original demand expectation.",
        confidence="0.65",
        evidence_hash="d",
        status=BeliefStatus.ACTIVE,
        transition_from_event_id=weakened.event_id,
        supersedes_event_id=original.event_id,
    )

    third = decide_belief_record(
        BeliefHistory((first.entry, second.entry)),
        correction,
        correction.transaction_at,
    )

    assert isinstance(third, AppendBeliefRecord)
    history = BeliefHistory((first.entry, second.entry, third.entry))
    assert tuple(entry.event.event_id for entry in history.entries) == (
        original.event_id,
        weakened.event_id,
        correction.event_id,
    )
    assert history.entries[-1].event.supersedes_event_id == original.event_id
    graph = project_belief_graph(
        history,
        BeliefGraphQuery(_instant(20), (original.subject,), 10, 10),
        (
            BeliefEvidenceArtifact("a" * 64, "b" * 64, _instant(18)),
            BeliefEvidenceArtifact("c" * 64, "b" * 64, _instant(18)),
            BeliefEvidenceArtifact("d" * 64, "b" * 64, _instant(18)),
        ),
    )
    assert {edge.kind for edge in graph.edges} == {
        BeliefGraphEdgeKind.SUPPORTS,
        BeliefGraphEdgeKind.SUPERSEDES,
        BeliefGraphEdgeKind.TRANSITION_FROM,
    }


def test_bitemporal_graph_excludes_a_transition_not_yet_known_at_the_cutoff() -> None:
    event = _belief_event()
    appended = decide_belief_record(BeliefHistory(()), event, event.transaction_at)
    assert isinstance(appended, AppendBeliefRecord)
    graph = project_belief_graph(
        BeliefHistory((appended.entry,)),
        BeliefGraphQuery(
            cutoff=_instant(19),
            subjects=(event.subject,),
            maximum_belief_events=10,
            maximum_evidence_artifacts=10,
        ),
        (BeliefEvidenceArtifact("a" * 64, "b" * 64, _instant(18)),),
    )

    assert graph.belief_nodes == ()
    assert graph.evidence_nodes == ()


def test_graph_excludes_a_backdated_event_until_its_durable_record_time() -> None:
    event = _belief_event()
    recorded_at = _instant(22)
    entry = BeliefLedgerEntry(
        1,
        event,
        recorded_at,
        belief_projection_identity(None, event, recorded_at, 1),
    )

    graph = project_belief_graph(
        BeliefHistory((entry,)),
        BeliefGraphQuery(_instant(21), (event.subject,), 10, 10),
        (BeliefEvidenceArtifact("a" * 64, "b" * 64, _instant(18)),),
    )

    assert graph.belief_nodes == ()
    assert graph.evidence_nodes == ()


def test_as_of_graph_does_not_require_evidence_from_a_later_recorded_transition() -> None:
    original = _belief_event()
    first = decide_belief_record(BeliefHistory(()), original, original.transaction_at)
    assert isinstance(first, AppendBeliefRecord)
    later = _belief_event(
        event_id="belief-event-later",
        evidence_hash="c",
        transaction_hour=21,
        transition_from_event_id=original.event_id,
    )
    later_recorded_at = _instant(22)
    second = decide_belief_record(
        BeliefHistory((first.entry,)),
        later,
        later_recorded_at,
    )
    assert isinstance(second, AppendBeliefRecord)

    graph = project_belief_graph(
        BeliefHistory((first.entry, second.entry)),
        BeliefGraphQuery(_instant(20), (original.subject,), 10, 10),
        (BeliefEvidenceArtifact("a" * 64, "b" * 64, _instant(18)),),
    )

    assert tuple(node.event.event_id for node in graph.belief_nodes) == (original.event_id,)


def test_delayed_transition_survives_an_unrelated_newer_stream_without_backdating() -> None:
    original = _belief_event()
    first = decide_belief_record(BeliefHistory(()), original, original.transaction_at)
    assert isinstance(first, AppendBeliefRecord)
    unrelated = _belief_event(
        event_id="msft-event",
        belief_id="msft-demand",
        subject=EquityInstrumentIdentity("alpaca-paper", "equity-msft", "NASDAQ"),
        transaction_hour=22,
    )
    second = decide_belief_record(BeliefHistory((first.entry,)), unrelated, _instant(22))
    assert isinstance(second, AppendBeliefRecord)
    delayed = _belief_event(
        event_id="belief-event-delayed",
        transaction_hour=21,
        transition_from_event_id=original.event_id,
    )

    third = decide_belief_record(
        BeliefHistory((first.entry, second.entry)),
        delayed,
        _instant(23),
    )

    assert isinstance(third, AppendBeliefRecord)
    graph = project_belief_graph(
        BeliefHistory((first.entry, second.entry, third.entry)),
        BeliefGraphQuery(_instant(22), (original.subject,), 10, 10),
        (
            BeliefEvidenceArtifact("a" * 64, "b" * 64, _instant(18)),
            BeliefEvidenceArtifact("c" * 64, "b" * 64, _instant(18)),
        ),
    )
    assert tuple(node.event.event_id for node in graph.belief_nodes) == (original.event_id,)


@given(st.integers(min_value=1, max_value=999))
def test_generated_event_hash_and_idempotent_receipt_are_stable(case: int) -> None:
    event = _belief_event(
        event_id=f"belief-event-{case}",
        claim=f"Generated demand case {case} remains evidence bound.",
        confidence=f"0.{case:03d}",
    )
    reconstructed = BeliefEvent.create(
        event_id=event.event_id,
        belief_id=event.belief_id,
        subject=event.subject,
        claim_kind=event.claim_kind,
        claim=event.claim,
        valid_at=event.valid_at,
        transaction_at=event.transaction_at,
        evidence_cutoff=event.evidence_cutoff,
        confidence=event.confidence,
        evidence=event.evidence,
        falsifiers=event.falsifiers,
        status=event.status,
        transition_from_event_id=None,
        supersedes_event_id=None,
    )
    appended = decide_belief_record(BeliefHistory(()), event, event.transaction_at)
    assert isinstance(appended, AppendBeliefRecord)

    replayed = decide_belief_record(
        BeliefHistory((appended.entry,)),
        reconstructed,
        reconstructed.transaction_at,
    )

    assert reconstructed.content_hash == event.content_hash
    assert isinstance(replayed, RecordReceipt)
    assert replayed.disposition is RecordDisposition.REPLAYED
    assert replayed.projection_identity == appended.entry.projection_identity


@given(st.sampled_from(tuple(BeliefStatus)))
def test_every_allowed_transition_from_active_appends_without_overwrite(
    status: BeliefStatus,
) -> None:
    original = _belief_event()
    first = decide_belief_record(BeliefHistory(()), original, original.transaction_at)
    assert isinstance(first, AppendBeliefRecord)
    transition = _belief_event(
        event_id=f"belief-event-{status.value}",
        status=status,
        transition_from_event_id=original.event_id,
    )

    second = decide_belief_record(
        BeliefHistory((first.entry,)),
        transition,
        transition.transaction_at,
    )

    assert isinstance(second, AppendBeliefRecord)
    assert BeliefHistory((first.entry, second.entry)).entries[0].event == original


def test_transition_refuses_a_transaction_time_regression() -> None:
    original = _belief_event()
    first = decide_belief_record(BeliefHistory(()), original, original.transaction_at)
    assert isinstance(first, AppendBeliefRecord)

    decision = decide_belief_record(
        BeliefHistory((first.entry,)),
        _belief_event(
            event_id="belief-event-2",
            transaction_hour=19,
            transition_from_event_id=original.event_id,
        ),
        original.transaction_at,
    )

    assert decision == RecordReceipt.refused(RecordRefusalCode.INVALID_TRANSITION)


@pytest.mark.parametrize(
    "changed_field",
    ["transition", "subject", "claim_kind", "unknown_superseded_event"],
)
def test_transition_must_continue_the_same_belief_head_and_semantics(
    changed_field: str,
) -> None:
    original = _belief_event()
    first = decide_belief_record(BeliefHistory(()), original, original.transaction_at)
    assert isinstance(first, AppendBeliefRecord)
    transition_from_event_id = original.event_id
    subject: EquityInstrumentIdentity | None = None
    claim_kind = BeliefClaimKind.EXPECTATION
    supersedes_event_id: str | None = None
    if changed_field == "transition":
        transition_from_event_id = "unknown-event"
    elif changed_field == "subject":
        subject = EquityInstrumentIdentity("alpaca-paper", "equity-msft", "NASDAQ")
    elif changed_field == "claim_kind":
        claim_kind = BeliefClaimKind.RISK
    else:
        supersedes_event_id = "unknown-event"

    candidate = _belief_event(
        event_id="belief-event-2",
        transition_from_event_id=transition_from_event_id,
        subject=subject,
        claim_kind=claim_kind,
        supersedes_event_id=supersedes_event_id,
    )
    decision = decide_belief_record(
        BeliefHistory((first.entry,)),
        candidate,
        candidate.transaction_at,
    )

    assert decision == RecordReceipt.refused(RecordRefusalCode.INVALID_TRANSITION)


_EXPECTED_TRANSITIONS = {
    BeliefStatus.ACTIVE: frozenset(BeliefStatus),
    BeliefStatus.WEAKENED: frozenset(BeliefStatus),
    BeliefStatus.CONTRADICTED: frozenset(BeliefStatus),
    BeliefStatus.DORMANT: frozenset(BeliefStatus),
    BeliefStatus.EXPIRED: frozenset(
        {BeliefStatus.ACTIVE, BeliefStatus.SUPERSEDED, BeliefStatus.ARCHIVED}
    ),
    BeliefStatus.REFUTED: frozenset({BeliefStatus.SUPERSEDED, BeliefStatus.ARCHIVED}),
    BeliefStatus.SUPERSEDED: frozenset({BeliefStatus.ARCHIVED}),
    BeliefStatus.ARCHIVED: frozenset(),
}


@pytest.mark.parametrize(
    ("current_status", "next_status"),
    [
        (current_status, next_status)
        for current_status in _EXPECTED_TRANSITIONS
        for next_status in BeliefStatus
    ],
)
def test_status_transition_matrix_is_enforced(
    current_status: BeliefStatus,
    next_status: BeliefStatus,
) -> None:
    original = _belief_event()
    first = decide_belief_record(BeliefHistory(()), original, original.transaction_at)
    assert isinstance(first, AppendBeliefRecord)
    current = _belief_event(
        event_id="belief-event-current",
        status=current_status,
        transition_from_event_id=original.event_id,
    )
    second = decide_belief_record(
        BeliefHistory((first.entry,)),
        current,
        current.transaction_at,
    )
    assert isinstance(second, AppendBeliefRecord)
    candidate = _belief_event(
        event_id="belief-event-candidate",
        status=next_status,
        transition_from_event_id=current.event_id,
    )

    decision = decide_belief_record(
        BeliefHistory((first.entry, second.entry)),
        candidate,
        candidate.transaction_at,
    )

    if next_status in _EXPECTED_TRANSITIONS[current_status]:
        assert isinstance(decision, AppendBeliefRecord)
    else:
        assert decision == RecordReceipt.refused(RecordRefusalCode.INVALID_TRANSITION)


def test_transition_history_validator_rejects_an_invalid_initial_state() -> None:
    invalid_initial = _belief_event(status=BeliefStatus.WEAKENED)
    entry = BeliefLedgerEntry(
        1,
        invalid_initial,
        invalid_initial.transaction_at,
        belief_projection_identity(
            None,
            invalid_initial,
            invalid_initial.transaction_at,
            1,
        ),
    )

    assert validate_belief_transition_history((entry,)) is False


def test_new_belief_stream_can_follow_an_existing_stream_in_global_history() -> None:
    original = _belief_event()
    first = decide_belief_record(BeliefHistory(()), original, original.transaction_at)
    assert isinstance(first, AppendBeliefRecord)
    independent = _belief_event(
        event_id="independent-belief-event",
        belief_id="msft-demand",
        subject=EquityInstrumentIdentity("alpaca-paper", "equity-msft", "NASDAQ"),
    )

    decision = decide_belief_record(
        BeliefHistory((first.entry,)),
        independent,
        independent.transaction_at,
    )

    assert isinstance(decision, AppendBeliefRecord)
    replay = decide_belief_record(
        BeliefHistory((first.entry, decision.entry)),
        independent,
        independent.transaction_at,
    )
    assert replay == RecordReceipt(
        RecordDisposition.REPLAYED,
        independent.event_id,
        SECOND_LEDGER_POSITION,
        decision.entry.projection_identity,
        None,
    )


@pytest.mark.parametrize(
    "confidence",
    ["not-a-number", "NaN", "1.1", "0" * 17, "1e-1000000"],
)
def test_belief_event_refuses_noncanonical_or_out_of_range_confidence(
    confidence: str,
) -> None:
    with pytest.raises(ValueError, match="invalid belief event"):
        _belief_event(confidence=confidence)


def test_belief_event_normalizes_negative_zero_confidence() -> None:
    assert _belief_event(confidence="-0").confidence == "0"


def test_belief_value_objects_reject_invalid_runtime_state() -> None:
    event = _belief_event()
    with pytest.raises(ValueError, match="invalid belief event"):
        BeliefEvidenceReference("not-a-hash", "b" * 64)
    with pytest.raises(ValueError, match="invalid belief event"):
        BeliefEvidenceArtifact("not-a-hash", "b" * 64, _instant(18))
    with pytest.raises(ValueError, match="invalid belief event"):
        BeliefEvent.create(
            event_id=event.event_id,
            belief_id=event.belief_id,
            subject=event.subject,
            claim_kind=event.claim_kind,
            claim=event.claim,
            valid_at=event.valid_at,
            transaction_at=event.transaction_at,
            evidence_cutoff=event.evidence_cutoff,
            confidence=event.confidence,
            evidence=(),
            falsifiers=event.falsifiers,
            status=event.status,
            transition_from_event_id=None,
            supersedes_event_id=None,
        )
    with pytest.raises(ValueError, match="invalid belief history"):
        BeliefLedgerEntry(0, event, event.transaction_at, "a" * 64)
    with pytest.raises(ValueError, match="invalid belief graph"):
        BeliefGraph.create(
            query=BeliefGraphQuery(_instant(20), (event.subject,), 1, 1),
            source_history_hash="not-a-hash",
            belief_nodes=(),
            evidence_nodes=(),
            edges=(),
            omitted_belief_events=0,
            omitted_evidence_artifacts=0,
        )
    with pytest.raises(ValueError, match="invalid belief record receipt"):
        RecordReceipt(
            RecordDisposition.APPENDED,
            event.event_id,
            1,
            "a" * 64,
            RecordRefusalCode.INVALID_EVENT,
        )


def test_history_rejects_non_tuple_and_invalid_transition_state() -> None:
    with pytest.raises(ValueError, match="invalid belief history"):
        # Hostile runtime input deliberately violates the typed public constructor.
        BeliefHistory([])  # type: ignore[arg-type]
    event = _belief_event(status=BeliefStatus.WEAKENED)
    entry = BeliefLedgerEntry(
        1,
        event,
        event.transaction_at,
        belief_projection_identity(None, event, event.transaction_at, 1),
    )
    with pytest.raises(ValueError, match="invalid belief history"):
        BeliefHistory((entry,))
