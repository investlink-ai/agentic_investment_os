from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace

import pytest

from agentic_investment_os.research.dossier import Dossier, parse_dossier
from agentic_investment_os.research.resolution import (
    CioRefusalReason,
    CioResolution,
    CioStance,
    ForecastRefusalReason,
    ObservationWindow,
    ScenarioEvidenceSource,
    ScenarioForecast,
    ScenarioResolutionRule,
    SkepticDecision,
    SkepticResult,
    Thesis,
    parse_cio_resolution,
    parse_scenario_forecast,
    parse_skeptic_result,
    parse_thesis,
)
from tests._replay import (
    ARTIFACT_ID,
    CUTOFF,
    SUBJECT,
    dossier,
    dossier_payload,
    forecast_payload,
    resolution_payload,
    skeptic_payload,
    thesis_payload,
)

HORIZON_TRADING_DAYS = 10
TOTAL_PROBABILITY_BPS = 10_000


def _hash_payload(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _revalidate_runtime_field(
    instance: Thesis | SkepticResult | ScenarioForecast, field: str, value: object
) -> None:
    forged = deepcopy(instance)
    object.__setattr__(forged, field, value)
    forged.__post_init__()


def test_validated_resolution_artifacts_preserve_identity_horizon_and_abstention() -> None:
    validated_dossier = dossier()
    thesis = parse_thesis(thesis_payload(), dossier=validated_dossier)
    assert isinstance(thesis, Thesis)
    skeptic = parse_skeptic_result(
        skeptic_payload(thesis), dossier=validated_dossier, thesis=thesis
    )
    assert isinstance(skeptic, SkepticResult)
    forecast = parse_scenario_forecast(
        forecast_payload(thesis, skeptic),
        dossier=validated_dossier,
        thesis=thesis,
        skeptic=skeptic,
    )
    assert isinstance(forecast, ScenarioForecast)
    cio = parse_cio_resolution(
        resolution_payload(thesis, skeptic, forecast, stance="abstain"),
        dossier=validated_dossier,
        thesis=thesis,
        skeptic=skeptic,
        forecast=forecast,
    )

    assert isinstance(cio, CioResolution)
    assert cio.stance is CioStance.ABSTAIN
    assert thesis.horizon_trading_days == forecast.horizon_trading_days == HORIZON_TRADING_DAYS
    assert sum(item.probability_bps or 0 for item in forecast.scenarios) == TOTAL_PROBABILITY_BPS
    assert all(artifact.non_production for artifact in (thesis, skeptic, forecast, cio))


@pytest.mark.parametrize(
    ("mutate", "parser"),
    [
        (
            lambda payload: payload.update({"supporting_assertion_ids": ["unsupported-claim"]}),
            "thesis",
        ),
        (lambda payload: payload.update({"position_weight": "25%"}), "thesis"),
        (lambda payload: payload.update({"decision": "approve"}), "skeptic"),
        (lambda payload: payload.update({"order": "buy"}), "cio"),
    ],
)
def test_resolution_schemas_reject_unsupported_claims_enums_and_authority(
    mutate: object,
    parser: str,
) -> None:
    validated_dossier = dossier()
    thesis_value = parse_thesis(thesis_payload(), dossier=validated_dossier)
    assert isinstance(thesis_value, Thesis)
    skeptic_value = parse_skeptic_result(
        skeptic_payload(thesis_value), dossier=validated_dossier, thesis=thesis_value
    )
    assert isinstance(skeptic_value, SkepticResult)
    forecast_value = parse_scenario_forecast(
        forecast_payload(thesis_value, skeptic_value),
        dossier=validated_dossier,
        thesis=thesis_value,
        skeptic=skeptic_value,
    )
    assert isinstance(forecast_value, ScenarioForecast)
    payload = deepcopy(
        {
            "thesis": thesis_payload(),
            "skeptic": skeptic_payload(thesis_value),
            "cio": resolution_payload(thesis_value, skeptic_value, forecast_value),
        }[parser]
    )
    assert callable(mutate)
    mutate(payload)

    if parser == "thesis":
        assert not isinstance(parse_thesis(payload, dossier=validated_dossier), Thesis)
    elif parser == "skeptic":
        assert not isinstance(
            parse_skeptic_result(payload, dossier=validated_dossier, thesis=thesis_value),
            SkepticResult,
        )
    else:
        assert not isinstance(
            parse_cio_resolution(
                payload,
                dossier=validated_dossier,
                thesis=thesis_value,
                skeptic=skeptic_value,
                forecast=forecast_value,
            ),
            CioResolution,
        )


def test_forecast_requires_exact_outcomes_observable_horizon_and_valid_probability_total() -> None:
    validated_dossier = dossier()
    thesis = parse_thesis(thesis_payload(), dossier=validated_dossier)
    assert isinstance(thesis, Thesis)
    skeptic = parse_skeptic_result(
        skeptic_payload(thesis), dossier=validated_dossier, thesis=thesis
    )
    assert isinstance(skeptic, SkepticResult)
    invalid = forecast_payload(thesis, skeptic)
    scenarios = invalid["scenarios"]
    assert isinstance(scenarios, list)
    first = scenarios[0]
    assert isinstance(first, dict)
    first["probability_bps"] = 5_001

    result = parse_scenario_forecast(
        invalid,
        dossier=validated_dossier,
        thesis=thesis,
        skeptic=skeptic,
    )

    assert result is ForecastRefusalReason.INVALID_PROBABILITIES


def test_forecast_refuses_overlapping_or_unobservable_resolution_partition() -> None:
    validated_dossier = dossier()
    thesis = parse_thesis(thesis_payload(), dossier=validated_dossier)
    assert isinstance(thesis, Thesis)
    skeptic = parse_skeptic_result(
        skeptic_payload(thesis), dossier=validated_dossier, thesis=thesis
    )
    assert isinstance(skeptic, SkepticResult)
    invalid = forecast_payload(thesis, skeptic)
    scenarios = invalid["scenarios"]
    assert isinstance(scenarios, list)
    base = scenarios[1]
    assert isinstance(base, dict)
    rule = base["resolution_rule"]
    assert isinstance(rule, dict)
    rule["upper_bound_bps"] = 50

    result = parse_scenario_forecast(
        invalid,
        dossier=validated_dossier,
        thesis=thesis,
        skeptic=skeptic,
    )

    assert result is ForecastRefusalReason.UNOBSERVABLE_RESOLUTION


@pytest.mark.parametrize(
    ("scenario_index", "field", "value"),
    [
        (0, "lower_bound_inclusive", False),
        (1, "upper_bound_inclusive", True),
        (2, "source", "analyst_judgment"),
    ],
)
def test_forecast_requires_canonical_boundary_ownership_and_allowed_source(
    scenario_index: int, field: str, value: object
) -> None:
    validated_dossier = dossier()
    thesis = parse_thesis(thesis_payload(), dossier=validated_dossier)
    assert isinstance(thesis, Thesis)
    skeptic = parse_skeptic_result(
        skeptic_payload(thesis), dossier=validated_dossier, thesis=thesis
    )
    assert isinstance(skeptic, SkepticResult)
    invalid = forecast_payload(thesis, skeptic)
    scenarios = invalid["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[scenario_index]
    assert isinstance(scenario, dict)
    rule = scenario["resolution_rule"]
    assert isinstance(rule, dict)
    rule[field] = value

    result = parse_scenario_forecast(
        invalid,
        dossier=validated_dossier,
        thesis=thesis,
        skeptic=skeptic,
    )

    assert result is ForecastRefusalReason.UNOBSERVABLE_RESOLUTION


def test_typed_artifact_constructors_reject_hash_consistent_authority_and_invariant_forgery() -> (
    None
):
    validated_dossier = dossier()
    thesis = parse_thesis(thesis_payload(), dossier=validated_dossier)
    assert isinstance(thesis, Thesis)
    skeptic = parse_skeptic_result(
        skeptic_payload(thesis), dossier=validated_dossier, thesis=thesis
    )
    assert isinstance(skeptic, SkepticResult)
    forecast = parse_scenario_forecast(
        forecast_payload(thesis, skeptic),
        dossier=validated_dossier,
        thesis=thesis,
        skeptic=skeptic,
    )
    assert isinstance(forecast, ScenarioForecast)
    cio = parse_cio_resolution(
        resolution_payload(thesis, skeptic, forecast),
        dossier=validated_dossier,
        thesis=thesis,
        skeptic=skeptic,
        forecast=forecast,
    )
    assert isinstance(cio, CioResolution)
    forged_authority = thesis.material_payload()
    forged_authority["authority_scope"] = "champion"
    forged_authority["non_production"] = False
    invalid_horizon = thesis.material_payload()
    invalid_horizon["horizon_trading_days"] = 0

    with pytest.raises(ValueError, match="invalid non-production Thesis"):
        replace(
            thesis,
            authority_scope="champion",
            non_production=False,
            content_hash=_hash_payload(forged_authority),
        )
    with pytest.raises(ValueError, match="invalid non-production Thesis"):
        replace(thesis, horizon_trading_days=0, content_hash=_hash_payload(invalid_horizon))
    for artifact in (skeptic, forecast, cio):
        forged = artifact.material_payload()
        forged["authority_scope"] = "champion"
        forged["non_production"] = False
        with pytest.raises(ValueError, match="invalid non-production"):
            replace(
                artifact,
                authority_scope="champion",
                non_production=False,
                content_hash=_hash_payload(forged),
            )
    with pytest.raises(ValueError, match="invalid non-production Thesis"):
        _revalidate_runtime_field(thesis, "causal_path", list(thesis.causal_path))
    with pytest.raises(ValueError, match="invalid non-production Skeptic"):
        _revalidate_runtime_field(skeptic, "contradictions", list(skeptic.contradictions))
    with pytest.raises(ValueError, match="invalid non-production scenario forecast"):
        _revalidate_runtime_field(forecast, "scenarios", list(forecast.scenarios))
    with pytest.raises(ValueError, match="invalid scenario resolution rule"):
        ScenarioResolutionRule(
            metric="operating_margin_change_bps",
            source=ScenarioEvidenceSource.ALLOWED_OFFICIAL_FILING,
            observation_window=ObservationWindow.NEXT_ALLOWED_RELEASE,
            lower_bound_bps=100,
            lower_bound_inclusive=True,
            upper_bound_bps=0,
            upper_bound_inclusive=False,
        )


def test_missing_evidence_and_active_uninvestable_conditions_allow_only_abstention() -> None:
    missing_payload = dossier_payload()
    missing_payload["missing_evidence"] = [
        "Required allowed-source margin evidence is unavailable."
    ]
    missing = parse_dossier(
        missing_payload,
        expected_subject=SUBJECT,
        available_artifact_ids=(ARTIFACT_ID,),
        cutoff=CUTOFF,
    )
    assert isinstance(missing, Dossier)
    thesis_input = thesis_payload()
    thesis_input["dossier_id"] = missing.content_hash
    thesis = parse_thesis(thesis_input, dossier=missing)
    assert isinstance(thesis, Thesis)
    skeptic_input = skeptic_payload(thesis)
    skeptic_input["dossier_id"] = missing.content_hash
    skeptic = parse_skeptic_result(skeptic_input, dossier=missing, thesis=thesis)
    assert isinstance(skeptic, SkepticResult)
    forecast_input = forecast_payload(thesis, skeptic)
    forecast_input["dossier_id"] = missing.content_hash
    forecast = parse_scenario_forecast(
        forecast_input, dossier=missing, thesis=thesis, skeptic=skeptic
    )
    assert isinstance(forecast, ScenarioForecast)
    active = resolution_payload(thesis, skeptic, forecast, stance="hold")
    active["dossier_id"] = missing.content_hash
    abstain = resolution_payload(thesis, skeptic, forecast, stance="abstain")
    abstain["dossier_id"] = missing.content_hash
    rationale = abstain["rationale"]
    assert isinstance(rationale, dict)
    rationale["basis"] = "missing_evidence"

    active_result = parse_cio_resolution(
        active, dossier=missing, thesis=thesis, skeptic=skeptic, forecast=forecast
    )
    abstain_result = parse_cio_resolution(
        abstain, dossier=missing, thesis=thesis, skeptic=skeptic, forecast=forecast
    )

    assert active_result is CioRefusalReason.MISSING_EVIDENCE
    assert isinstance(abstain_result, CioResolution)

    clear = dossier()
    active_thesis = parse_thesis(thesis_payload(active_uninvestable=True), dossier=clear)
    assert isinstance(active_thesis, Thesis)
    active_skeptic = parse_skeptic_result(
        skeptic_payload(active_thesis), dossier=clear, thesis=active_thesis
    )
    assert isinstance(active_skeptic, SkepticResult)
    active_forecast = parse_scenario_forecast(
        forecast_payload(active_thesis, active_skeptic),
        dossier=clear,
        thesis=active_thesis,
        skeptic=active_skeptic,
    )
    assert isinstance(active_forecast, ScenarioForecast)
    condition_hold = resolution_payload(
        active_thesis, active_skeptic, active_forecast, stance="hold"
    )
    condition_abstain = resolution_payload(
        active_thesis, active_skeptic, active_forecast, stance="abstain"
    )
    condition_rationale = condition_abstain["rationale"]
    assert isinstance(condition_rationale, dict)
    condition_rationale["basis"] = "uninvestable"

    condition_hold_result = parse_cio_resolution(
        condition_hold,
        dossier=clear,
        thesis=active_thesis,
        skeptic=active_skeptic,
        forecast=active_forecast,
    )
    condition_abstain_result = parse_cio_resolution(
        condition_abstain,
        dossier=clear,
        thesis=active_thesis,
        skeptic=active_skeptic,
        forecast=active_forecast,
    )

    assert condition_hold_result is CioRefusalReason.ACTIVE_UNINVESTABLE_CONDITION
    assert isinstance(condition_abstain_result, CioResolution)


def test_evidence_request_cannot_be_normalized_into_an_active_cio_stance() -> None:
    validated_dossier = dossier()
    thesis = parse_thesis(thesis_payload(), dossier=validated_dossier)
    assert isinstance(thesis, Thesis)
    skeptic = parse_skeptic_result(
        skeptic_payload(thesis, decision="request_evidence"),
        dossier=validated_dossier,
        thesis=thesis,
    )
    assert isinstance(skeptic, SkepticResult)
    assert skeptic.decision is SkepticDecision.REQUEST_EVIDENCE
    forecast = parse_scenario_forecast(
        forecast_payload(thesis, skeptic),
        dossier=validated_dossier,
        thesis=thesis,
        skeptic=skeptic,
    )
    assert isinstance(forecast, ScenarioForecast)

    active = parse_cio_resolution(
        resolution_payload(thesis, skeptic, forecast, stance="long"),
        dossier=validated_dossier,
        thesis=thesis,
        skeptic=skeptic,
        forecast=forecast,
    )
    abstain = parse_cio_resolution(
        resolution_payload(thesis, skeptic, forecast, stance="abstain"),
        dossier=validated_dossier,
        thesis=thesis,
        skeptic=skeptic,
        forecast=forecast,
    )

    assert not isinstance(active, CioResolution)
    assert isinstance(abstain, CioResolution)
