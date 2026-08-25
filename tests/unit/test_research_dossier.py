from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from agentic_investment_os.domain.identity import EquityInstrumentIdentity
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.research.dossier import (
    Dossier,
    DossierRefusalReason,
    parse_dossier,
)


def _instant(hour: int) -> UtcInstant:
    return UtcInstant.from_datetime(datetime(2026, 8, 24, hour, tzinfo=UTC))


def _payload() -> dict[str, object]:
    subject = EquityInstrumentIdentity("alpaca-paper", "equity-aapl", "NASDAQ")
    return {
        "schema_version": 1,
        "record_kind": "evidence_collector_dossier",
        "authority_scope": "research_lab_non_production",
        "non_production": True,
        "subject": subject.to_payload(),
        "facts": [
            {
                "assertion_id": "revenue-growth",
                "statement_kind": "fact",
                "statement": "Reported revenue increased year over year.",
                "citation_artifact_ids": ["a" * 64],
                "relevant_at": _instant(17).isoformat(),
                "uncertainty": "observed",
            }
        ],
        "interpretations": [
            {
                "assertion_id": "demand-interpretation",
                "statement_kind": "interpretation",
                "statement": "The filing may indicate resilient demand.",
                "citation_artifact_ids": ["a" * 64],
                "relevant_at": _instant(17).isoformat(),
                "uncertainty": "inferred",
            }
        ],
        "contradicting_evidence": [
            {
                "artifact_id": "b" * 64,
                "explanation": "The latest news describes weaker unit volumes.",
            }
        ],
        "missing_evidence": ["Current allowed-source consensus is unavailable."],
        "lenses": [
            {"lens": lens, "disposition": "addressed", "rationale": "Covered by cited evidence."}
            for lens in (
                "information_and_sentiment",
                "growth_and_expectations",
                "quality_and_resilience",
                "valuation_and_embedded_expectations",
                "market_behavior_and_liquidity",
                "catalyst_timing_and_downside",
            )
        ],
    }


def test_dossier_is_evidence_bound_and_cannot_carry_sizing_authority() -> None:
    valid = parse_dossier(
        _payload(),
        expected_subject=EquityInstrumentIdentity("alpaca-paper", "equity-aapl", "NASDAQ"),
        available_artifact_ids=("a" * 64, "b" * 64),
        cutoff=_instant(18),
    )
    prohibited = {**_payload(), "weight": "0.25"}

    assert isinstance(valid, Dossier)
    assert valid.non_production is True
    assert valid.authority_scope == "research_lab_non_production"
    assert (
        parse_dossier(
            prohibited,
            expected_subject=valid.subject,
            available_artifact_ids=("a" * 64, "b" * 64),
            cutoff=_instant(18),
        )
        is DossierRefusalReason.PROHIBITED_AUTHORITY
    )

    with pytest.raises(ValueError, match="invalid non-production Dossier"):
        replace(valid, non_production=False)
    with pytest.raises(ValueError, match="invalid non-production Dossier"):
        replace(valid, authority_scope="champion")


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("identity", DossierRefusalReason.IDENTITY_MISMATCH),
        ("inferred_fact", DossierRefusalReason.INVALID_SCHEMA),
        ("duplicate_assertion", DossierRefusalReason.INVALID_SCHEMA),
        ("unsupported_contradiction", DossierRefusalReason.UNSUPPORTED_CITATION),
        ("missing_lens", DossierRefusalReason.MISSING_LENS),
        ("duplicate_missing", DossierRefusalReason.BOUNDS_EXCEEDED),
        ("empty_facts", DossierRefusalReason.BOUNDS_EXCEEDED),
        ("unknown_field", DossierRefusalReason.INVALID_SCHEMA),
    ],
)
def test_dossier_rejects_identity_schema_bounds_and_incomplete_lenses(
    mutation: str,
    expected: DossierRefusalReason,
) -> None:
    payload = deepcopy(_payload())
    facts = payload["facts"]
    interpretations = payload["interpretations"]
    contradictions = payload["contradicting_evidence"]
    lenses = payload["lenses"]
    assert isinstance(facts, list)
    assert isinstance(interpretations, list)
    assert isinstance(contradictions, list)
    assert isinstance(lenses, list)
    fact = facts[0]
    interpretation = interpretations[0]
    contradiction = contradictions[0]
    assert isinstance(fact, dict)
    assert isinstance(interpretation, dict)
    assert isinstance(contradiction, dict)
    if mutation == "identity":
        payload["subject"] = EquityInstrumentIdentity(
            "alpaca-paper", "equity-msft", "NASDAQ"
        ).to_payload()
    elif mutation == "inferred_fact":
        fact["uncertainty"] = "inferred"
    elif mutation == "duplicate_assertion":
        interpretation["assertion_id"] = fact["assertion_id"]
    elif mutation == "unsupported_contradiction":
        contradiction["artifact_id"] = "f" * 64
    elif mutation == "missing_lens":
        lenses.pop()
    elif mutation == "duplicate_missing":
        payload["missing_evidence"] = ["missing", "missing"]
    elif mutation == "empty_facts":
        facts.clear()
    else:
        payload["unexpected"] = True

    result = parse_dossier(
        payload,
        expected_subject=EquityInstrumentIdentity("alpaca-paper", "equity-aapl", "NASDAQ"),
        available_artifact_ids=("a" * 64, "b" * 64),
        cutoff=_instant(18),
    )

    assert result is expected
