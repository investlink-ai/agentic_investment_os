from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from agentic_investment_os.domain.identity import EquityInstrumentIdentity
from agentic_investment_os.research.model import (
    ModelCallDisposition,
    ModelCallRequest,
    ModelCallResponse,
    ModelTimingDisposition,
    ResearchRole,
)
from agentic_investment_os.research.resolution import (
    ScenarioForecast,
    SkepticResult,
    Thesis,
    parse_scenario_forecast,
    parse_skeptic_result,
    parse_thesis,
)
from tests._evidence import recorded_evidence, recorded_official_evidence
from tests._replay import (
    ARTIFACT_ID,
    SUBJECT,
    dossier,
    dossier_payload,
    forecast_payload,
    resolution_payload,
    skeptic_payload,
    thesis_payload,
)

MODEL_IDENTITY = "codex-subscription/test-model"


def production_recorded_evidence() -> dict[str, object]:
    """Add subject-local HOLD and SPY evidence without changing the canonical fixture."""
    payload = recorded_evidence()
    items = payload["items"]
    assert isinstance(items, list)
    holding_identity = EquityInstrumentIdentity("alpaca-paper", "equity-hold", "NYSE")
    spy_identity = EquityInstrumentIdentity("alpaca-paper", "equity-spy", "ARCA")
    for index, item in enumerate(items):
        assert isinstance(item, dict)
        mappings = item["entity_mappings"]
        catalog_ids = item["entity_catalog_ids"]
        assert isinstance(mappings, list)
        assert isinstance(catalog_ids, list)
        added_mappings = [(holding_identity, "equity-hold")]
        if index == 0:
            added_mappings.append((spy_identity, "equity-spy"))
        for identity, catalog_id in added_mappings:
            mappings.append(
                {
                    "identity": identity.to_payload(),
                    "confidence": "exact",
                }
            )
            catalog_ids.append(catalog_id)
    market = items[0]
    assert isinstance(market, dict)
    content = market["content"]
    assert isinstance(content, dict)
    bars = content["bars"]
    assert isinstance(bars, list)
    bars.append(
        {
            "asset_id": "equity-hold",
            "close": "12.00",
            "timestamp": "2026-08-21T19:00:00.000000+00:00",
        }
    )
    bars.append(
        {
            "asset_id": "equity-spy",
            "close": "550.00",
            "timestamp": "2026-08-21T19:00:00.000000+00:00",
        }
    )
    return payload


def production_recorded_official_evidence() -> dict[str, object]:
    """Assign separate synthetic official records to AAPL and HOLD subjects."""
    payload = recorded_official_evidence()
    items = payload["items"]
    assert isinstance(items, list)
    issuer_release = items[1]
    assert isinstance(issuer_release, dict)
    issuer_release["entity_mappings"] = [
        {
            "identity": EquityInstrumentIdentity(
                "alpaca-paper",
                "equity-hold",
                "NYSE",
            ).to_payload(),
            "confidence": "exact",
            "mapping_version": "issuer-map-v1",
            "available_at": "2026-08-21T17:00:00.000000+00:00",
        }
    ]
    return payload


@dataclass(slots=True)
class ValidProductionModel:
    """Return subject-bound valid fixtures for every production research role."""

    cio_stance: Literal["long", "hold", "abstain"] = "hold"
    skeptic_decision: Literal["accept", "reject"] = "accept"
    unique_effect_count: int = 0

    def call(self, request: ModelCallRequest) -> ModelCallResponse:
        self.unique_effect_count += 1
        model_input = json.loads(request.model_input_json)
        assert isinstance(model_input, dict)
        evidence = model_input["evidence"]
        assert isinstance(evidence, list)
        assert evidence
        first_evidence = evidence[0]
        assert isinstance(first_evidence, dict)
        artifact_id = first_evidence["artifact_id"]
        available_at = first_evidence["available_at"]
        subject = model_input["subject"]
        assert isinstance(artifact_id, str)
        assert isinstance(available_at, str)
        assert isinstance(subject, dict)
        output = _role_output(
            request.role,
            model_input,
            self.cio_stance,
            self.skeptic_decision,
        )
        output = _replace_fixture(output, subject, artifact_id)
        if request.role is ResearchRole.EVIDENCE_COLLECTOR:
            facts = output["facts"]
            interpretations = output["interpretations"]
            assert isinstance(facts, list)
            assert isinstance(interpretations, list)
            for assertion in (*facts, *interpretations):
                assert isinstance(assertion, dict)
                assertion["relevant_at"] = available_at
        return ModelCallResponse(
            ModelCallDisposition.RESPONDED,
            json.dumps(output, sort_keys=True, separators=(",", ":")).encode(),
            MODEL_IDENTITY,
            input_tokens=100,
            output_tokens=200,
            turns=1,
            elapsed_milliseconds=10,
            timing_disposition=ModelTimingDisposition.WITHIN_BUDGET,
        )


def _role_output(
    role: ResearchRole,
    model_input: dict[str, object],
    cio_stance: Literal["long", "hold", "abstain"],
    skeptic_decision: Literal["accept", "reject"],
) -> dict[str, object]:
    if role is ResearchRole.EVIDENCE_COLLECTOR:
        return dossier_payload()
    fixed_dossier = dossier()
    fixed_thesis = parse_thesis(thesis_payload(), dossier=fixed_dossier)
    assert isinstance(fixed_thesis, Thesis)
    dossier_input = model_input["dossier"]
    assert isinstance(dossier_input, dict)
    dossier_id = dossier_input["content_hash"]
    assert isinstance(dossier_id, str)
    if role is ResearchRole.THESIS_BUILDER:
        payload = thesis_payload()
        payload["dossier_id"] = dossier_id
        return payload
    thesis_input = model_input["thesis"]
    assert isinstance(thesis_input, dict)
    thesis_id = thesis_input["content_hash"]
    assert isinstance(thesis_id, str)
    if role is ResearchRole.INDEPENDENT_SKEPTIC:
        payload = skeptic_payload(fixed_thesis, decision=skeptic_decision)
        payload["dossier_id"] = dossier_id
        payload["thesis_id"] = thesis_id
        return payload
    fixed_skeptic = parse_skeptic_result(
        skeptic_payload(fixed_thesis),
        dossier=fixed_dossier,
        thesis=fixed_thesis,
    )
    assert isinstance(fixed_skeptic, SkepticResult)
    skeptic_input = model_input["skeptic"]
    assert isinstance(skeptic_input, dict)
    skeptic_id = skeptic_input["content_hash"]
    assert isinstance(skeptic_id, str)
    if role is ResearchRole.SCENARIO_FORECASTER:
        payload = forecast_payload(fixed_thesis, fixed_skeptic)
        payload["dossier_id"] = dossier_id
        payload["thesis_id"] = thesis_id
        payload["skeptic_id"] = skeptic_id
        return payload
    forecast_input = model_input["forecast"]
    assert isinstance(forecast_input, dict)
    forecast_id = forecast_input["content_hash"]
    assert isinstance(forecast_id, str)
    fixed_forecast = parse_scenario_forecast(
        forecast_payload(fixed_thesis, fixed_skeptic),
        dossier=fixed_dossier,
        thesis=fixed_thesis,
        skeptic=fixed_skeptic,
    )
    assert isinstance(fixed_forecast, ScenarioForecast)
    payload = resolution_payload(
        fixed_thesis,
        fixed_skeptic,
        fixed_forecast,
        stance=cio_stance,
    )
    payload["dossier_id"] = dossier_id
    payload["thesis_id"] = thesis_id
    payload["skeptic_id"] = skeptic_id
    payload["forecast_id"] = forecast_id
    rationale = payload["rationale"]
    assert isinstance(rationale, dict)
    if cio_stance == "abstain":
        rationale["basis"] = "insufficient_confidence"
    rationale["resolution_artifact_ids"] = sorted((thesis_id, skeptic_id, forecast_id))
    return payload


def _replace_fixture(
    value: dict[str, object],
    subject: dict[str, object],
    artifact_id: str,
) -> dict[str, object]:
    def replace(item: object) -> object:
        if item == SUBJECT.to_payload():
            return deepcopy(subject)
        if item == ARTIFACT_ID:
            return artifact_id
        if isinstance(item, dict):
            result = {key: replace(child) for key, child in item.items()}
            if "authority_scope" in result:
                result["authority_scope"] = "production_research"
                result["non_production"] = False
            return result
        if isinstance(item, list):
            return [replace(child) for child in item]
        return item

    replaced = replace(value)
    assert isinstance(replaced, dict)
    return replaced
