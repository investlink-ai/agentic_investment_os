from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from agentic_investment_os.domain.governance import ACTIVE_CONSTITUTION
from agentic_investment_os.domain.identity import EquityInstrumentIdentity
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.memory.beliefs import BeliefGraph, BeliefGraphQuery
from agentic_investment_os.research.dossier import Dossier, parse_dossier
from agentic_investment_os.research.resolution import (
    ScenarioForecast,
    SkepticResult,
    Thesis,
    parse_scenario_forecast,
    parse_skeptic_result,
    parse_thesis,
)

SUBJECT = EquityInstrumentIdentity("alpaca-paper", "equity-aapl", "NASDAQ")
CUTOFF = UtcInstant.from_datetime(datetime(2026, 8, 24, 18, tzinfo=UTC))
AVAILABLE_AT = UtcInstant.from_datetime(datetime(2026, 8, 24, 17, tzinfo=UTC))
ARTIFACT_ID = "a" * 64
PORTFOLIO_FINGERPRINT = "b" * 64
TOOL_SCHEMA_JSON = '{"additionalProperties":false,"type":"object"}'
TOOL_SCHEMA_HASH = hashlib.sha256(TOOL_SCHEMA_JSON.encode()).hexdigest()
SOURCE_HISTORY_HASH = "d" * 64
EVIDENCE_CONTENT = "Reported revenue increased year over year while unit volumes weakened."
EVIDENCE_CONTENT_HASH = hashlib.sha256(EVIDENCE_CONTENT.encode()).hexdigest()
PROMPT_CONTENT = "Build the exact Evidence Collector Dossier schema from only supplied evidence."
PROMPT_HASH = hashlib.sha256(PROMPT_CONTENT.encode()).hexdigest()


def content_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def model_configuration() -> dict[str, object]:
    material: dict[str, object] = {
        "schema_version": 1,
        "model_identity": "codex-subscription/test-model",
        "reasoning": {
            "effort": "medium",
            "maximum_output_tokens": 4_000,
            "maximum_turns": 1,
        },
    }
    return {**material, "content_hash": content_hash(material)}


def belief_graph() -> BeliefGraph:
    return BeliefGraph.create(
        query=BeliefGraphQuery(CUTOFF, (SUBJECT,), 10, 10),
        source_history_hash=SOURCE_HISTORY_HASH,
        belief_nodes=(),
        evidence_nodes=(),
        edges=(),
        omitted_belief_events=0,
        omitted_evidence_artifacts=0,
    )


def replay_request(
    *,
    request_id: str = "replay-aapl-2026-08-24",
    namespace: str = "lab.synthetic.aapl",
    available_at: UtcInstant = AVAILABLE_AT,
    prompt_content: str = PROMPT_CONTENT,
) -> dict[str, object]:
    prompt_hash = hashlib.sha256(prompt_content.encode()).hexdigest()
    model = model_configuration()
    model_hash = model["content_hash"]
    assert isinstance(model_hash, str)
    graph = belief_graph()
    material_hashes = sorted(
        {
            EVIDENCE_CONTENT_HASH,
            ACTIVE_CONSTITUTION.content_hash,
            graph.content_hash,
            PORTFOLIO_FINGERPRINT,
            prompt_hash,
            model_hash,
            TOOL_SCHEMA_HASH,
        }
    )
    return {
        "schema_version": 1,
        "record_kind": "lab_replay_request",
        "request_id": request_id,
        "namespace": namespace,
        "input_kind": "synthetic",
        "subject": SUBJECT.to_payload(),
        "evidence": [
            {
                "artifact_id": ARTIFACT_ID,
                "content_hash": EVIDENCE_CONTENT_HASH,
                "available_at": available_at.isoformat(),
                "subject": SUBJECT.to_payload(),
                "content": EVIDENCE_CONTENT,
            }
        ],
        "evidence_cutoff": CUTOFF.isoformat(),
        "data_regime": "test-regime-v1",
        "constitution": ACTIVE_CONSTITUTION,
        "belief_graph": graph,
        "portfolio_context_fingerprint": PORTFOLIO_FINGERPRINT,
        "prompt": {
            "schema_version": 1,
            "prompt_id": "evidence-collector-v1",
            "content": prompt_content,
            "content_hash": prompt_hash,
        },
        "model_configuration": model,
        "tools": [
            {
                "name": "structured_output",
                "schema_json": TOOL_SCHEMA_JSON,
                "schema_hash": TOOL_SCHEMA_HASH,
            }
        ],
        "material_input_hashes": material_hashes,
    }


def dossier_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_kind": "evidence_collector_dossier",
        "authority_scope": "research_lab_non_production",
        "non_production": True,
        "subject": SUBJECT.to_payload(),
        "facts": [
            {
                "assertion_id": "reported-revenue",
                "statement_kind": "fact",
                "statement": "Reported revenue increased year over year.",
                "citation_artifact_ids": [ARTIFACT_ID],
                "relevant_at": AVAILABLE_AT.isoformat(),
                "uncertainty": "observed",
            }
        ],
        "interpretations": [
            {
                "assertion_id": "resilient-demand",
                "statement_kind": "interpretation",
                "statement": "The filing may indicate resilient demand.",
                "citation_artifact_ids": [ARTIFACT_ID],
                "relevant_at": AVAILABLE_AT.isoformat(),
                "uncertainty": "inferred",
            }
        ],
        "contradicting_evidence": [
            {
                "artifact_id": ARTIFACT_ID,
                "explanation": "The same artifact reports weaker unit volumes.",
            }
        ],
        "missing_evidence": [],
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


def dossier_bytes(payload: object | None = None) -> bytes:
    value = dossier_payload() if payload is None else payload
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def dossier() -> Dossier:
    parsed = parse_dossier(
        dossier_payload(),
        expected_subject=SUBJECT,
        available_artifact_ids=(ARTIFACT_ID,),
        cutoff=CUTOFF,
    )
    assert isinstance(parsed, Dossier)
    return parsed


def _thesis_claim(text: str, *, contradicting: bool = False) -> dict[str, object]:
    return {
        "text": text,
        "supporting_assertion_ids": ["reported-revenue"],
        "contradicting_artifact_ids": [ARTIFACT_ID] if contradicting else [],
    }


def thesis_payload(*, active_uninvestable: bool = False) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_kind": "thesis",
        "authority_scope": "research_lab_non_production",
        "non_production": True,
        "subject": SUBJECT.to_payload(),
        "dossier_id": dossier().content_hash,
        "apparent_expectation": _thesis_claim(
            "The market expects revenue growth to offset weaker unit volumes."
        ),
        "variant_view": _thesis_claim(
            "Margins may disappoint if the weaker volume signal persists.", contradicting=True
        ),
        "causal_path": [
            _thesis_claim("Weaker units reduce operating leverage."),
            _thesis_claim("Lower operating leverage pressures margins."),
        ],
        "catalyst": _thesis_claim("The next reported operating-margin update."),
        "horizon_trading_days": 10,
        "scenario_summaries": {
            "bull": _thesis_claim("Revenue resilience offsets the unit-volume decline."),
            "base": _thesis_claim("Growth slows while margins remain broadly stable."),
            "bear": _thesis_claim("Weak volume produces material margin compression."),
        },
        "invalidators": [_thesis_claim("Unit volumes recover without margin deterioration.")],
        "supporting_assertion_ids": ["reported-revenue"],
        "contradicting_artifact_ids": [ARTIFACT_ID],
        "uninvestable_conditions": [
            {
                "condition": _thesis_claim("Required margin evidence is unavailable."),
                "active": active_uninvestable,
            }
        ],
    }


def skeptic_payload(thesis: Thesis, *, decision: str = "accept") -> dict[str, object]:
    requested_evidence = (
        ["Obtain an allowed-source operating-margin observation."]
        if decision == "request_evidence"
        else []
    )
    return {
        "schema_version": 1,
        "record_kind": "independent_skeptic_result",
        "authority_scope": "research_lab_non_production",
        "non_production": True,
        "subject": SUBJECT.to_payload(),
        "dossier_id": dossier().content_hash,
        "thesis_id": thesis.content_hash,
        "decision": decision,
        "strongest_countercase": (
            "Revenue growth may already reflect temporary price rather than demand."
        ),
        "contradictions": [
            {
                "claim": "Unit-volume weakness contradicts a resilient-demand interpretation.",
                "citation_artifact_ids": [ARTIFACT_ID],
            }
        ],
        "base_rates": [
            {
                "description": "No uncited base-rate claim is treated as observed.",
                "citation_artifact_ids": [ARTIFACT_ID],
            }
        ],
        "requested_evidence": requested_evidence,
    }


def forecast_payload(thesis: Thesis, skeptic: SkepticResult) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_kind": "scenario_forecast",
        "authority_scope": "research_lab_non_production",
        "non_production": True,
        "subject": SUBJECT.to_payload(),
        "dossier_id": dossier().content_hash,
        "thesis_id": thesis.content_hash,
        "skeptic_id": skeptic.content_hash,
        "horizon_trading_days": thesis.horizon_trading_days,
        "scenarios": [
            {
                "kind": "bull",
                "outcome": "Operating margin expands from the pinned baseline.",
                "resolution_rule": {
                    "metric": "operating_margin_change_bps",
                    "source": "allowed_official_filing",
                    "observation_window": "next_allowed_release",
                    "lower_bound_bps": 100,
                    "lower_bound_inclusive": True,
                    "upper_bound_bps": None,
                    "upper_bound_inclusive": None,
                },
                "downside_path": "Demand resilience fails to translate into margin.",
                "probability_bps": 2_500,
            },
            {
                "kind": "base",
                "outcome": "Operating margin remains within one point of the pinned baseline.",
                "resolution_rule": {
                    "metric": "operating_margin_change_bps",
                    "source": "allowed_official_filing",
                    "observation_window": "next_allowed_release",
                    "lower_bound_bps": -100,
                    "lower_bound_inclusive": True,
                    "upper_bound_bps": 100,
                    "upper_bound_inclusive": False,
                },
                "downside_path": "A mild unit decline offsets price resilience.",
                "probability_bps": 5_000,
            },
            {
                "kind": "bear",
                "outcome": "Operating margin contracts by more than one point.",
                "resolution_rule": {
                    "metric": "operating_margin_change_bps",
                    "source": "allowed_official_filing",
                    "observation_window": "next_allowed_release",
                    "lower_bound_bps": None,
                    "lower_bound_inclusive": None,
                    "upper_bound_bps": -100,
                    "upper_bound_inclusive": False,
                },
                "downside_path": "Weaker volume causes material operating deleverage.",
                "probability_bps": 2_500,
            },
        ],
    }


def resolution_payload(
    thesis: Thesis,
    skeptic: SkepticResult,
    forecast: ScenarioForecast,
    *,
    stance: str = "hold",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_kind": "cio_resolution",
        "authority_scope": "research_lab_non_production",
        "non_production": True,
        "subject": SUBJECT.to_payload(),
        "dossier_id": dossier().content_hash,
        "thesis_id": thesis.content_hash,
        "skeptic_id": skeptic.content_hash,
        "forecast_id": forecast.content_hash,
        "stance": stance,
        "uncertainty": "high" if stance == "abstain" else "medium",
        "rationale": {
            "basis": (
                "insufficient_confidence"
                if skeptic.decision.value == "request_evidence"
                else "contested_thesis"
                if skeptic.decision.value == "reject"
                else "supported_thesis"
            ),
            "assertion_ids": ["reported-revenue"],
            "resolution_artifact_ids": sorted(
                (thesis.content_hash, skeptic.content_hash, forecast.content_hash)
            ),
        },
    }


def _role_contract(role: str, *, prompt_suffix: str = "") -> dict[str, object]:
    prompt_content = f"Return only the exact {role} schema from declared inputs.{prompt_suffix}"
    prompt_hash = hashlib.sha256(prompt_content.encode()).hexdigest()
    model = model_configuration()
    return {
        "role": role,
        "prompt": {
            "schema_version": 1,
            "prompt_id": f"{role}-v1",
            "content": prompt_content,
            "content_hash": prompt_hash,
        },
        "model_configuration": model,
        "tools": [
            {
                "name": "structured_output",
                "schema_json": TOOL_SCHEMA_JSON,
                "schema_hash": TOOL_SCHEMA_HASH,
            }
        ],
    }


def resolution_replay_request(
    *,
    request_id: str = "resolve-aapl-2026-08-24",
    namespace: str = "lab.synthetic.aapl",
    changed_role: str | None = None,
) -> dict[str, object]:
    roles = ("thesis_builder", "independent_skeptic", "scenario_forecaster", "cio")
    contracts = [
        _role_contract(role, prompt_suffix=" Changed." if role == changed_role else "")
        for role in roles
    ]
    graph = belief_graph()
    validated_dossier = dossier()
    material_hashes = {
        EVIDENCE_CONTENT_HASH,
        ACTIVE_CONSTITUTION.content_hash,
        graph.content_hash,
        PORTFOLIO_FINGERPRINT,
        validated_dossier.content_hash,
    }
    for contract in contracts:
        prompt = contract["prompt"]
        model = contract["model_configuration"]
        tools = contract["tools"]
        assert isinstance(prompt, dict)
        assert isinstance(model, dict)
        assert isinstance(tools, list)
        prompt_hash = prompt["content_hash"]
        model_hash = model["content_hash"]
        assert isinstance(prompt_hash, str)
        assert isinstance(model_hash, str)
        material_hashes.add(prompt_hash)
        material_hashes.add(model_hash)
        for tool in tools:
            assert isinstance(tool, dict)
            schema_hash = tool["schema_hash"]
            assert isinstance(schema_hash, str)
            material_hashes.add(schema_hash)
    return {
        "schema_version": 2,
        "record_kind": "lab_replay_request",
        "request_id": request_id,
        "namespace": namespace,
        "input_kind": "synthetic",
        "subject": SUBJECT.to_payload(),
        "evidence": [
            {
                "artifact_id": ARTIFACT_ID,
                "content_hash": EVIDENCE_CONTENT_HASH,
                "available_at": AVAILABLE_AT.isoformat(),
                "subject": SUBJECT.to_payload(),
                "content": EVIDENCE_CONTENT,
            }
        ],
        "evidence_cutoff": CUTOFF.isoformat(),
        "data_regime": "test-regime-v1",
        "constitution": ACTIVE_CONSTITUTION,
        "belief_graph": graph,
        "portfolio_context_fingerprint": PORTFOLIO_FINGERPRINT,
        "dossier": validated_dossier,
        "role_contracts": contracts,
        "material_input_hashes": sorted(material_hashes),
    }


def resolution_bytes(
    *,
    skeptic_decision: str = "accept",
    stance: str = "hold",
) -> tuple[bytes, bytes, bytes, bytes]:
    validated_dossier = dossier()
    thesis_value = parse_thesis(thesis_payload(), dossier=validated_dossier)
    assert isinstance(thesis_value, Thesis)
    skeptic_value = parse_skeptic_result(
        skeptic_payload(thesis_value, decision=skeptic_decision),
        dossier=validated_dossier,
        thesis=thesis_value,
    )
    assert isinstance(skeptic_value, SkepticResult)
    forecast_value = parse_scenario_forecast(
        forecast_payload(thesis_value, skeptic_value),
        dossier=validated_dossier,
        thesis=thesis_value,
        skeptic=skeptic_value,
    )
    assert isinstance(forecast_value, ScenarioForecast)
    payloads = (
        thesis_payload(),
        skeptic_payload(thesis_value, decision=skeptic_decision),
        forecast_payload(thesis_value, skeptic_value),
        resolution_payload(thesis_value, skeptic_value, forecast_value, stance=stance),
    )

    def encode(payload: object) -> bytes:
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    return (encode(payloads[0]), encode(payloads[1]), encode(payloads[2]), encode(payloads[3]))
