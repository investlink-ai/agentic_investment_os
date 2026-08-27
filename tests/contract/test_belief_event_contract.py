from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentic_investment_os.domain.identity import EquityInstrumentIdentity
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.memory.admission import (
    BeliefClaimKind,
    BeliefEvent,
    BeliefEvidenceArtifact,
    BeliefEvidenceReference,
    BeliefStatus,
    parse_belief_event,
    validate_belief_evidence,
)
from agentic_investment_os.memory.beliefs import BeliefGraphQuery, parse_belief_graph_query


def _instant(hour: int) -> UtcInstant:
    return UtcInstant.from_datetime(datetime(2026, 8, 21, hour, tzinfo=UTC))


def _event(
    *,
    transition_from_event_id: str | None = None,
    supersedes_event_id: str | None = None,
) -> BeliefEvent:
    return BeliefEvent.create(
        event_id="belief-event-1",
        belief_id="aapl-demand",
        subject=EquityInstrumentIdentity("alpaca-paper", "equity-aapl", "NASDAQ"),
        claim_kind=BeliefClaimKind.EXPECTATION,
        claim="Demand remains resilient over the stated horizon.",
        valid_at=_instant(18),
        transaction_at=_instant(20),
        evidence_cutoff=_instant(19),
        confidence="0.7",
        evidence=(BeliefEvidenceReference("a" * 64, "b" * 64),),
        falsifiers=("A reported demand contraction would refute the claim.",),
        status=BeliefStatus.ACTIVE,
        transition_from_event_id=transition_from_event_id,
        supersedes_event_id=supersedes_event_id,
    )


def test_canonical_belief_event_round_trips_through_the_hostile_input_parser() -> None:
    event = _event()

    parsed = parse_belief_event(event.to_payload())

    assert parsed == event
    assert parse_belief_event(event) == event


def test_linked_belief_event_round_trip_preserves_both_history_references() -> None:
    event = _event(
        transition_from_event_id="prior-event",
        supersedes_event_id="superseded-event",
    )

    assert parse_belief_event(event.to_payload()) == event


def test_belief_event_rejects_boolean_schema_version_lookalikes() -> None:
    payload = _event().to_payload()
    payload["schema_version"] = True

    assert parse_belief_event(payload) is None


@pytest.mark.parametrize(
    "case",
    [
        "missing_field",
        "unknown_version",
        "unknown_claim_kind",
        "claim_kind_not_text",
        "unknown_status",
        "status_not_text",
        "extra_authority_field",
        "noncanonical_timestamp",
        "hash_inconsistent",
        "alias_subject",
        "event_id_not_text",
        "belief_id_not_text",
        "claim_not_text",
        "valid_at_invalid",
        "evidence_cutoff_invalid",
        "confidence_not_text",
        "confidence_exponent",
        "content_hash_not_text",
        "transition_not_text",
        "supersedes_not_text",
        "evidence_not_list",
        "evidence_fields_wrong",
        "evidence_identifier_not_text",
        "evidence_content_hash_not_text",
        "evidence_hash_invalid",
        "evidence_relationship_not_text",
        "evidence_relationship_invalid",
        "falsifier_not_text",
        "mixed_falsifier_types",
    ],
)
def test_belief_event_rejects_hostile_or_ambiguous_representations(  # noqa: PLR0912, PLR0915
    case: str,
) -> None:
    payload = _event().to_payload()
    if case == "missing_field":
        del payload["confidence"]
    elif case == "unknown_version":
        payload["schema_version"] = 99
    elif case == "unknown_claim_kind":
        claim = payload["claim"]
        assert isinstance(claim, dict)
        claim["kind"] = "weight"
    elif case == "claim_kind_not_text":
        claim = payload["claim"]
        assert isinstance(claim, dict)
        claim["kind"] = 1
    elif case == "unknown_status":
        payload["status"] = "unknown"
    elif case == "status_not_text":
        payload["status"] = 1
    elif case == "extra_authority_field":
        payload["target_weight"] = "1.0"
    elif case == "noncanonical_timestamp":
        payload["transaction_at"] = "2026-08-21T20:00:00Z"
    elif case == "hash_inconsistent":
        payload["content_hash"] = "0" * 64
    elif case == "alias_subject":
        payload["subject"] = {"symbol": "AAPL"}
    elif case == "event_id_not_text":
        payload["event_id"] = 1
    elif case == "belief_id_not_text":
        payload["belief_id"] = 1
    elif case == "claim_not_text":
        claim = payload["claim"]
        assert isinstance(claim, dict)
        claim["statement"] = 1
    elif case == "valid_at_invalid":
        payload["valid_at"] = 1
    elif case == "evidence_cutoff_invalid":
        payload["evidence_cutoff"] = 1
    elif case == "confidence_not_text":
        payload["confidence"] = 1
    elif case == "confidence_exponent":
        payload["confidence"] = "1e-1000000"
    elif case == "content_hash_not_text":
        payload["content_hash"] = 1
    elif case == "transition_not_text":
        payload["transition_from_event_id"] = 1
    elif case == "supersedes_not_text":
        payload["supersedes_event_id"] = 1
    elif case == "evidence_not_list":
        payload["evidence"] = {}
    elif case == "evidence_fields_wrong":
        payload["evidence"] = [{"artifact_id": "a" * 64}]
    elif case == "evidence_identifier_not_text":
        payload["evidence"] = [
            {"artifact_id": 1, "content_hash": "b" * 64, "relationship": "supporting"}
        ]
    elif case == "evidence_content_hash_not_text":
        payload["evidence"] = [
            {"artifact_id": "a" * 64, "content_hash": 1, "relationship": "supporting"}
        ]
    elif case == "evidence_hash_invalid":
        payload["evidence"] = [
            {
                "artifact_id": "invalid",
                "content_hash": "b" * 64,
                "relationship": "supporting",
            }
        ]
    elif case == "evidence_relationship_not_text":
        payload["evidence"] = [
            {
                "artifact_id": "a" * 64,
                "content_hash": "b" * 64,
                "relationship": 1,
            }
        ]
    elif case == "evidence_relationship_invalid":
        payload["evidence"] = [
            {
                "artifact_id": "a" * 64,
                "content_hash": "b" * 64,
                "relationship": "unknown",
            }
        ]
    elif case == "falsifier_not_text":
        payload["falsifiers"] = [1]
    else:
        assert case == "mixed_falsifier_types"
        falsifiers = payload["falsifiers"]
        assert isinstance(falsifiers, list)
        payload["falsifiers"] = [falsifiers[0], 1]

    assert parse_belief_event(payload) is None


def test_belief_event_rejects_mapping_and_list_subclasses() -> None:
    class DictSubclass(dict[str, object]):
        pass

    class ListSubclass(list[object]):
        pass

    payload = _event().to_payload()
    assert parse_belief_event(DictSubclass(payload)) is None

    payload = _event().to_payload()
    evidence = payload["evidence"]
    assert isinstance(evidence, list)
    payload["evidence"] = ListSubclass(evidence)
    assert parse_belief_event(payload) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("subjects", []),
        ("maximum_belief_events", 0),
        ("maximum_evidence_artifacts", 101),
    ],
)
def test_belief_graph_query_rejects_missing_or_oversized_bounds(
    field: str,
    value: object,
) -> None:
    query = BeliefGraphQuery(
        cutoff=_instant(20),
        subjects=(_event().subject,),
        maximum_belief_events=10,
        maximum_evidence_artifacts=10,
    )
    payload = query.to_payload()
    payload[field] = value

    assert parse_belief_graph_query(payload) is None


def test_graph_query_round_trips_the_typed_contract() -> None:
    query = BeliefGraphQuery(_instant(20), (_event().subject,), 10, 10)

    assert parse_belief_graph_query(query) == query


@pytest.mark.parametrize(
    ("artifact", "expected"),
    [
        (
            BeliefEvidenceArtifact("a" * 64, "c" * 64, _instant(18)),
            "evidence_hash_mismatch",
        ),
        (
            BeliefEvidenceArtifact("a" * 64, "b" * 64, _instant(20)),
            "evidence_after_cutoff",
        ),
    ],
)
def test_belief_evidence_contract_refuses_mismatch_or_late_availability(
    artifact: BeliefEvidenceArtifact,
    expected: str,
) -> None:
    refusal = validate_belief_evidence((_event(),), (artifact,))

    assert refusal is not None
    assert refusal.value == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cutoff", "invalid"),
        ("subjects", "not-a-list"),
        ("subjects", [{"symbol": "AAPL"}]),
    ],
)
def test_graph_query_rejects_malformed_cutoff_or_subjects(
    field: str,
    value: object,
) -> None:
    payload = BeliefGraphQuery(_instant(20), (_event().subject,), 10, 10).to_payload()
    payload[field] = value

    assert parse_belief_graph_query(payload) is None
