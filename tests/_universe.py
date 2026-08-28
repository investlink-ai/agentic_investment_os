from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeGuard

from agentic_investment_os.adapters.recorded_portfolio import RecordedPortfolioSource
from agentic_investment_os.adapters.recorded_universe import RecordedUniverseSource
from agentic_investment_os.domain.governance import ACTIVE_CONSTITUTION
from agentic_investment_os.domain.identity import AssetClass, EquityInstrumentIdentity
from agentic_investment_os.domain.lifecycle import (
    AdvanceCommand,
    AdvanceRequest,
    PinnedRunIdentity,
)
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.domain.universe import (
    EquityUniversePolicy,
    UniverseInputIdentity,
    UniverseInputs,
    UniverseSnapshot,
    build_universe_snapshot,
)
from agentic_investment_os.portfolio.construction import (
    BalancedPortfolioPolicy,
    PortfolioInputSet,
)
from agentic_investment_os.research.policy import ProductionResearchPolicy
from tests._evidence import evidence_policy
from tests._portfolio import recorded_portfolio_inputs

if TYPE_CHECKING:
    from pathlib import Path

RECORDED_AT = UtcInstant.from_datetime(datetime(2026, 8, 21, 22, 0, tzinfo=UTC))
INSTRUMENT_SOURCE_FINGERPRINT = "1" * 64
POSITION_SOURCE_FINGERPRINT = "2" * 64


def _is_mutable_mapping(value: object) -> TypeGuard[dict[str, object]]:
    return (
        isinstance(value, dict) and type(value) is dict and all(type(key) is str for key in value)
    )


def _is_mutable_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list) and type(value) is list


def _is_mutable_mapping_list(value: object) -> TypeGuard[list[dict[str, object]]]:
    return _is_mutable_list(value) and all(_is_mutable_mapping(item) for item in value)


def mutable_mapping(value: object) -> dict[str, object]:
    """Narrow one exact mutable JSON fixture mapping after validating its runtime shape."""
    assert _is_mutable_mapping(value)
    return value


def mutable_list(value: object) -> list[object]:
    """Narrow one exact mutable JSON fixture list after validating its runtime shape."""
    assert _is_mutable_list(value)
    return value


def mutable_mapping_list(value: object) -> list[dict[str, object]]:
    """Narrow one exact list of mutable JSON fixture mappings after validation."""
    assert _is_mutable_mapping_list(value)
    return value


def exact_text(value: object) -> str:
    """Narrow one exact JSON fixture string after validating its runtime type."""
    assert type(value) is str
    return value


def _equity_identity(symbol: str, venue: str) -> dict[str, object]:
    return EquityInstrumentIdentity("alpaca-paper", f"equity-{symbol.lower()}", venue).to_payload()


def _equity_instrument(  # noqa: PLR0913 - fixture exposes every material equity fact.
    symbol: str,
    venue: str,
    *,
    instrument_type: str = "common_stock",
    status: str = "active",
    tradable: bool = True,
    leveraged: bool = False,
    inverse: bool = False,
    price: str = "10",
    volume: str = "2000000",
    history_days: int = 250,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "payload_schema_version": 1,
        "record_kind": "instrument_observation",
        "asset_class": "us_equity",
        "identity": _equity_identity(symbol, venue),
        "aliases": [{"namespace": "alpaca-symbol", "value": symbol}],
        "payload": {
            "instrument_type": instrument_type,
            "status": status,
            "tradable": tradable,
            "leveraged": leveraged,
            "inverse": inverse,
            "price": price,
            "median_dollar_volume": volume,
            "history_days": history_days,
        },
    }


def _equity_position(symbol: str, venue: str, quantity: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "payload_schema_version": 1,
        "record_kind": "position_observation",
        "asset_class": "us_equity",
        "identity": _equity_identity(symbol, venue),
        "payload": {
            "quantity": quantity,
            "unit": "share",
            "valuation": {
                "amount": "108",
                "currency": "USD",
                "source": "alpaca-paper-market-value",
            },
        },
    }


def universe_policy() -> dict[str, object]:
    return {
        "asset_class": "us_equity",
        "schema_version": 1,
        "policy_type": "equity_universe",
        "data_regime": "alpaca-basic-iex-v1",
        "approved_exchanges": ["ARCA", "NASDAQ", "NYSE"],
        "etf_allowlist": [_equity_identity("SPY", "ARCA")],
        "minimum_price": "5",
        "minimum_median_dollar_volume": "1000000",
        "minimum_history_days": 20,
        "maximum_snapshot_age_seconds": 7200,
    }


def attention_policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_type": "v0_attention",
        "candidate_card_limit": 20,
        "new_dossier_limit": 5,
        "weekly_dossier_budget": 10,
        "weekly_exploration_budget": 1,
        "exploration_seed": "baseline-attention-v1",
    }


def research_policy() -> dict[str, object]:
    """Return the complete scripted production-research policy used by tests."""

    def role_contract(role: str) -> dict[str, object]:
        prompt_content = f"Return only the exact production {role} schema from declared inputs."
        prompt = {
            "schema_version": 1,
            "prompt_id": f"production-{role}-v1",
            "content": prompt_content,
            "content_hash": hashlib.sha256(prompt_content.encode()).hexdigest(),
        }
        model_material: dict[str, object] = {
            "schema_version": 1,
            "model_identity": "codex-subscription/test-model",
            "reasoning": {
                "effort": "medium",
                "maximum_output_tokens": 4_000,
                "maximum_turns": 1,
            },
        }
        return {
            "role": role,
            "prompt": prompt,
            "model_configuration": {
                **model_material,
                "content_hash": hashlib.sha256(
                    json.dumps(
                        model_material,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
            },
            "tools": [],
        }

    return {
        "schema_version": 1,
        "policy_type": "v0_production_research",
        "maximum_belief_events": 20,
        "maximum_evidence_artifacts": 50,
        "role_contracts": [
            role_contract(role)
            for role in (
                "evidence_collector",
                "thesis_builder",
                "independent_skeptic",
                "scenario_forecaster",
                "cio",
            )
        ],
    }


def portfolio_policy() -> dict[str, object]:
    """Return the complete mechanics-calibrated Balanced policy used by tests."""
    return {
        "schema_version": 1,
        "policy_type": "balanced_inverse_volatility",
        "asset_class": "us_equity",
        "risk_profile": "balanced",
        "realized_volatility": {
            "estimator": "sample_standard_deviation",
            "lookback_days": 20,
            "annualization_periods": 252,
            "floor": "0.1",
            "price_adjustment": "split_adjusted_close",
        },
        "maximum_input_age_seconds": 7200,
        "maximum_gross_weight": "0.8",
        "maximum_name_weight": "0.08",
        "maximum_sector_weight": "0.25",
        "maximum_common_cause_weight": "0.25",
        "maximum_correlation_cluster_weight": "0.25",
        "maximum_fraction_of_median_dollar_volume": "0.01",
        "target_band_width": "0.01",
        "minimum_executable_notional": "100",
        "partial_adjustment_fraction": "0.5",
        "reduce_multiplier": "0.5",
        "uncertainty_multipliers": {"low": "1", "medium": "0.75", "high": "0.5"},
    }


def runtime_configuration(state_root: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "state_root": str(state_root),
        "enabled_asset_classes": ["us_equity"],
        "universe_policy": universe_policy(),
        "evidence_policy": evidence_policy(),
        "attention_policy": attention_policy(),
        "research_policy": research_policy(),
        "portfolio_policy": portfolio_policy(),
    }


def recorded_universe() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "record_kind": "universe_inputs",
        "data_regime": "alpaca-basic-iex-v1",
        "evidence_cutoff": "2026-08-21T20:00:00.000000+00:00",
        "instruments": {
            "envelope_schema_version": 1,
            "payload_schema_version": 1,
            "record_kind": "instrument_snapshot",
            "payload_discriminator": "recorded_instrument_snapshot",
            "observed_at": "2026-08-21T19:30:00.000000+00:00",
            "available_at": "2026-08-21T19:35:00.000000+00:00",
            "data_regime": "alpaca-basic-iex-v1",
            "authority_scope": "market_data_observation",
            "source_fingerprint": INSTRUMENT_SOURCE_FINGERPRINT,
            "payload": {
                "complete": True,
                "items": [
                    _equity_instrument("AAPL", "NASDAQ", price="225.5", volume="125000000"),
                    _equity_instrument(
                        "HOLD",
                        "NYSE",
                        status="inactive",
                        tradable=False,
                        price="12",
                        volume="2000000",
                        history_days=100,
                    ),
                    _equity_instrument(
                        "OTCX", "OTC", price="20", volume="3000000", history_days=100
                    ),
                    _equity_instrument(
                        "SPY",
                        "ARCA",
                        instrument_type="etf",
                        price="650",
                        volume="5000000000",
                        history_days=5000,
                    ),
                    _equity_instrument(
                        "TQQQ",
                        "NASDAQ",
                        instrument_type="etf",
                        leveraged=True,
                        price="90",
                        volume="500000000",
                        history_days=1000,
                    ),
                ],
            },
        },
        "positions": {
            "envelope_schema_version": 1,
            "payload_schema_version": 1,
            "record_kind": "position_snapshot",
            "payload_discriminator": "recorded_position_snapshot",
            "observed_at": "2026-08-21T19:45:00.000000+00:00",
            "available_at": "2026-08-21T19:46:00.000000+00:00",
            "data_regime": "alpaca-basic-iex-v1",
            "authority_scope": "portfolio_observation",
            "source_fingerprint": POSITION_SOURCE_FINGERPRINT,
            "payload": {
                "complete": True,
                "items": [_equity_position("HOLD", "NYSE", "3")],
            },
        },
    }
    reseal_recorded_snapshot(payload, "instruments")
    reseal_recorded_snapshot(payload, "positions")
    return payload


def reseal_recorded_snapshot(payload: dict[str, object], name: str) -> None:
    snapshot = payload[name]
    assert isinstance(snapshot, dict)
    envelope = {key: value for key, value in snapshot.items() if key != "content_hash"}
    for field in ("observed_at", "available_at"):
        text = exact_text(envelope[field])
        envelope[field] = UtcInstant.from_datetime(datetime.fromisoformat(text)).isoformat()
    item_payload = envelope["payload"]
    assert isinstance(item_payload, dict)
    items = item_payload["items"]
    assert isinstance(items, list)
    items.sort(key=lambda item: json.dumps(item["identity"], sort_keys=True, separators=(",", ":")))
    snapshot["content_hash"] = hashlib.sha256(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def typed_universe_policy() -> EquityUniversePolicy:
    parsed = EquityUniversePolicy.parse(universe_policy())
    assert isinstance(parsed, EquityUniversePolicy)
    return parsed


def typed_research_policy() -> ProductionResearchPolicy:
    parsed = ProductionResearchPolicy.parse(research_policy())
    assert isinstance(parsed, ProductionResearchPolicy)
    return parsed


def typed_portfolio_policy() -> BalancedPortfolioPolicy:
    parsed = BalancedPortfolioPolicy.parse(portfolio_policy())
    assert isinstance(parsed, BalancedPortfolioPolicy)
    return parsed


def recorded_portfolio(payload: object | None = None) -> dict[str, object]:
    """Return portfolio inputs bound to the fixture's exact position snapshot."""
    parsed = RecordedUniverseSource(recorded_universe() if payload is None else payload).load()
    inputs = parsed if isinstance(parsed, UniverseInputs) else typed_universe_inputs()
    return recorded_portfolio_inputs(inputs.position_snapshot)


def typed_portfolio_inputs(payload: object | None = None) -> PortfolioInputSet:
    inputs = typed_universe_inputs(payload)
    parsed = RecordedPortfolioSource(recorded_portfolio_inputs(inputs.position_snapshot)).load(
        inputs.position_snapshot
    )
    assert isinstance(parsed, PortfolioInputSet)
    return parsed


def typed_universe_inputs(payload: object | None = None) -> UniverseInputs:
    parsed = RecordedUniverseSource(recorded_universe() if payload is None else payload).load()
    assert isinstance(parsed, UniverseInputs)
    return parsed


def pinned_run_identity(
    request: AdvanceRequest,
    *,
    configuration_version: int = 1,
    configuration_hash: str = "a" * 64,
    payload: object | None = None,
) -> PinnedRunIdentity:
    inputs = typed_universe_inputs(payload)
    policy = typed_universe_policy()
    input_identity = UniverseInputIdentity.from_inputs(inputs, policy)
    assert isinstance(input_identity, UniverseInputIdentity)
    typed_policy = typed_research_policy()
    portfolio_inputs = typed_portfolio_inputs(payload)
    return PinnedRunIdentity.create(
        request,
        configuration_version=configuration_version,
        configuration_hash=configuration_hash,
        research_policy_hash=typed_policy.fingerprint,
        portfolio_policy_hash=typed_portfolio_policy().policy_id,
        portfolio_input_hash=portfolio_inputs.input_id,
        universe_inputs=input_identity,
        constitution=ACTIVE_CONSTITUTION.reference,
    )


def universe_snapshot(
    identity: PinnedRunIdentity,
    *,
    payload: object | None = None,
) -> UniverseSnapshot:
    snapshot = build_universe_snapshot(
        identity.run_id,
        identity.cycle,
        typed_universe_inputs(payload),
        typed_universe_policy(),
        enabled_asset_classes=(AssetClass.US_EQUITY,),
        recorded_at=RECORDED_AT,
    )
    assert isinstance(snapshot, UniverseSnapshot)
    return snapshot


def advance_command(
    request: AdvanceRequest,
    *,
    configuration_version: int = 1,
    configuration_hash: str = "a" * 64,
    payload: object | None = None,
) -> AdvanceCommand:
    identity = pinned_run_identity(
        request,
        configuration_version=configuration_version,
        configuration_hash=configuration_hash,
        payload=payload,
    )
    return AdvanceCommand(
        request,
        identity,
        universe_snapshot(identity, payload=payload),
        typed_portfolio_inputs(payload),
    )
