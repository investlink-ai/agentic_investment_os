from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from agentic_investment_os.adapters.recorded_portfolio import (
    parse_recorded_portfolio_shadow_account as parse_portfolio_shadow_account,
)
from agentic_investment_os.domain.identity import (
    CryptoSpotInstrumentIdentity,
    EquityInstrumentIdentity,
    MarketSession,
)
from agentic_investment_os.domain.lifecycle import PortfolioShadowKind
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.domain.universe import (
    CryptoSpotPosition,
    EquityPosition,
    PositionSnapshot,
    PositionValuation,
)
from agentic_investment_os.portfolio.construction import (
    AdjustedClose,
    BalancedPortfolioPolicy,
    HouseViewResolution,
    MaterialEventEvidence,
    MaterialEventRisk,
    PortfolioConstructionRequest,
    PortfolioCostInputPolicy,
    PortfolioInputSet,
    PortfolioRefusalReason,
    PortfolioRiskInput,
    PortfolioSizingMethod,
    PortfolioStance,
    PortfolioTarget,
    PortfolioTradeReason,
    ShadowPortfolioPolicy,
    construct_balanced_portfolio,
    parse_portfolio_construction_result,
)
from agentic_investment_os.portfolio.shadows import (
    PortfolioCycleResult,
    PortfolioShadowAccount,
    ShadowCostInput,
    ShadowTargetBand,
    construct_portfolio_cycle,
)
from agentic_investment_os.portfolio.shadows import (
    parse_portfolio_shadow_account as parse_typed_shadow_account,
)
from tests._universe import mutable_mapping, mutable_mapping_list

_CUTOFF = UtcInstant.from_datetime(datetime(2026, 8, 21, 20, tzinfo=UTC))
_AAPL = EquityInstrumentIdentity("alpaca-paper", "equity-aapl", "NASDAQ")
_SPY = EquityInstrumentIdentity("alpaca-paper", "equity-spy", "ARCA")
_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_LENGTH = 64
_MAX_DECIMAL_TEXT_LENGTH = 64


def test_balanced_capped_inverse_volatility_leaves_capped_remainder_in_cash() -> None:
    request = PortfolioConstructionRequest(
        run_id="1" * 64,
        cycle=MarketSession(date.fromisoformat("2026-08-21")),
        evidence_cutoff=_CUTOFF,
        data_regime="alpaca-basic-iex-v1",
        configuration_hash="2" * 64,
        constitution_version=1,
        constitution_hash="3" * 64,
        research_policy_hash="4" * 64,
        research_artifact_ids=(_HASH_A, _HASH_B),
        memory_event_ids=("5" * 64, "6" * 64),
        universe_snapshot_id="7" * 64,
        expected_research_request_ids=tuple(sorted((_HASH_A, _HASH_B))),
        resolutions=(
            _resolution(_AAPL, _HASH_A),
            _resolution(_SPY, _HASH_B),
        ),
        inputs=_inputs(),
        policy=_policy(),
    )

    result = construct_balanced_portfolio(request)

    assert result.refusal is None
    assert tuple((item.identity, item.target_weight) for item in result.targets) == (
        (_AAPL, Decimal("0.08")),
        (_SPY, Decimal("0.08")),
    )
    assert result.cash_weight == Decimal("0.84")
    assert all(item.lower_weight == Decimal("0.07") for item in result.target_bands)
    assert all(item.upper_weight == Decimal("0.08") for item in result.target_bands)


def test_same_input_cycle_hand_calculates_each_approved_shadow_envelope() -> None:
    cycle = construct_portfolio_cycle(
        _request((_resolution(_AAPL, _HASH_A), _resolution(_SPY, _HASH_B)))
    )

    assert tuple(
        (
            shadow.account_kind,
            tuple(target.target_weight for target in shadow.targets),
            shadow.retained_cash_weight,
        )
        for shadow in cycle.shadows
    ) == (
        (
            PortfolioShadowKind.CONSERVATIVE,
            (Decimal("0.05"), Decimal("0.05")),
            Decimal("0.90"),
        ),
        (
            PortfolioShadowKind.GROWTH,
            (Decimal("0.12"), Decimal("0.12")),
            Decimal("0.76"),
        ),
        (
            PortfolioShadowKind.EQUAL_WEIGHT,
            (Decimal("0.08"), Decimal("0.08")),
            Decimal("0.84"),
        ),
    )
    assert cycle.balanced.cash_weight == Decimal("0.84")


def test_shadow_accounts_bind_the_exact_house_view_inputs_and_cost_facts() -> None:
    request = _request((_resolution(_AAPL, _HASH_A), _resolution(_SPY, _HASH_B)))
    cycle = construct_portfolio_cycle(request)
    assert cycle.balanced.house_view is not None

    for shadow in cycle.shadows:
        assert shadow.house_view_id == cycle.balanced.house_view.house_view_id
        assert shadow.policy_id == request.policy.policy_id
        assert shadow.input_id == request.inputs.input_id
        assert shadow.evidence_cutoff == request.evidence_cutoff
        assert shadow.position_snapshot_id == request.inputs.position_snapshot.fingerprint
        assert shadow.starting_cash == request.inputs.cash
        assert shadow.starting_equity == request.inputs.cash
        assert shadow.cost_input_policy_id == request.policy.cost_input_policy.policy_id
        assert shadow.cost_input_source == request.inputs.source_identity
        assert shadow.cost_inputs_available_at == request.inputs.available_at
        assert tuple(item.price for item in shadow.cost_inputs) == (
            Decimal(100),
            Decimal(100),
        )
        assert all(item.median_dollar_volume == Decimal(100000000) for item in shadow.cost_inputs)
        assert dict(shadow.material_fingerprints) == {
            "configuration": request.configuration_hash,
            "constitution": request.constitution_hash,
            "research_policy": request.research_policy_hash,
            "universe_snapshot": request.universe_snapshot_id,
            "portfolio_policy": request.policy.policy_id,
            "portfolio_input": request.inputs.input_id,
            "house_view": cycle.balanced.house_view.house_view_id,
        }


def test_zero_liquidity_preserves_a_complete_cash_only_shadow_cycle() -> None:
    request = _request(
        (_resolution(_AAPL, _HASH_A),),
        inputs=_inputs_for(
            (replace(_risk_input(_AAPL, "technology"), median_dollar_volume=Decimal(0)),)
        ),
    )

    cycle = construct_portfolio_cycle(request)

    assert cycle.balanced.refusal is None
    assert tuple(target.target_weight for target in cycle.balanced.targets) == (Decimal(0),)
    assert all(
        tuple(target.target_weight for target in shadow.targets) == (Decimal(0),)
        for shadow in cycle.shadows
    )
    assert all(shadow.retained_cash_weight == Decimal(1) for shadow in cycle.shadows)
    assert all(shadow.cost_inputs[0].median_dollar_volume == Decimal(0) for shadow in cycle.shadows)


def test_shadow_turnover_notional_uses_starting_equity_and_absolute_adjustment() -> None:
    resolution = replace(_resolution(_AAPL, _HASH_A), is_position=True)
    cycle = construct_portfolio_cycle(
        _request((resolution,), inputs=_inputs_with_position(Decimal("0.08")))
    )
    conservative = cycle.shadows[0]

    assert conservative.cost_inputs[0].current_weight == Decimal("0.08")
    assert conservative.cost_inputs[0].adjustment_weight == Decimal("0.05")
    assert conservative.modeled_turnover_weight == Decimal("0.03")
    assert conservative.modeled_turnover_notional == Decimal(3000)


def test_portfolio_cycle_preserves_balanced_refusal_without_shadow_accounts() -> None:
    request = _request(
        (_resolution(_AAPL, _HASH_A),),
        expected=("f" * 64,),
    )
    balanced = construct_balanced_portfolio(request)

    with pytest.raises(ValueError, match="invalid portfolio construction result"):
        balanced.require_house_view()
    cycle = construct_portfolio_cycle(request)
    assert cycle.balanced == balanced
    assert cycle.shadows == ()


def test_shadow_account_round_trip_refuses_authority_or_outcome_fields() -> None:
    account = construct_portfolio_cycle(
        _request((_resolution(_AAPL, _HASH_A), _resolution(_SPY, _HASH_B)))
    ).shadows[0]
    payload = account.to_payload()

    assert parse_portfolio_shadow_account(payload) == account
    assert (
        parse_typed_shadow_account(
            payload,
            portfolio_inputs=PortfolioInputSet.create(
                position_snapshot=account.portfolio_inputs.position_snapshot,
                cash=account.portfolio_inputs.cash,
                cash_currency=account.portfolio_inputs.cash_currency,
                source_identity="different-source",
                observed_at=account.portfolio_inputs.observed_at,
                available_at=account.portfolio_inputs.available_at,
                data_regime=account.portfolio_inputs.data_regime,
                risk_inputs=account.portfolio_inputs.risk_inputs,
            ),
        )
        is None
    )
    assert payload["authority_scope"] == "non_executable_shadow_account"
    assert {"champion", "packet_id", "order", "fill", "outcome"}.isdisjoint(payload)
    assert parse_portfolio_shadow_account({**payload, "record_kind": "invalid"}) is None
    assert parse_portfolio_shadow_account({**payload, "authority_scope": "executable"}) is None
    for prohibited in ("champion", "packet_id", "fill", "outcome"):
        assert parse_portfolio_shadow_account({**payload, prohibited: True}) is None


def test_shadow_account_values_fail_closed_when_invariants_or_content_change() -> None:
    account = construct_portfolio_cycle(
        _request((_resolution(_AAPL, _HASH_A), _resolution(_SPY, _HASH_B)))
    ).shadows[0]

    with pytest.raises(ValueError, match="invalid portfolio shadow account"):
        replace(account.target_bands[0], lower_weight=Decimal(1))
    with pytest.raises(ValueError, match="invalid portfolio shadow account"):
        replace(account.cost_inputs[0], price=Decimal(0))
    with pytest.raises(ValueError, match="invalid portfolio shadow account"):
        replace(account, schema_version=2)
    with pytest.raises(ValueError, match="invalid portfolio shadow account"):
        replace(account, content_hash="f" * 64).to_payload()


def test_shadow_account_parser_rejects_hostile_nested_material() -> None:
    account = construct_portfolio_cycle(
        _request((_resolution(_AAPL, _HASH_A), _resolution(_SPY, _HASH_B)))
    ).shadows[0]
    payload = account.to_payload()
    crypto_identity = CryptoSpotInstrumentIdentity(
        "alpaca-paper", "crypto-btc-usd", "BTC", "USD", "ALPACA"
    ).to_payload()

    invalid_payloads: list[dict[str, object]] = []
    for collection in ("targets", "target_bands", "cost_inputs"):
        changed = deepcopy(payload)
        values = changed[collection]
        assert isinstance(values, list)
        values[0] = {}
        invalid_payloads.append(changed)
    for collection in ("targets", "target_bands", "cost_inputs"):
        changed = deepcopy(payload)
        values = changed[collection]
        assert isinstance(values, list)
        item = values[0]
        assert isinstance(item, dict)
        item["identity"] = crypto_identity
        invalid_payloads.append(changed)

    invalid_nested_values = (
        ("targets", "target_weight", "+0.05"),
        ("targets", "target_weight", "1.1"),
        ("target_bands", "accounting_reason", True),
        ("target_bands", "accounting_reason", "invalid"),
        ("target_bands", "lower_weight", "+0.04"),
        ("cost_inputs", "price", "0"),
    )
    for collection, key, value in invalid_nested_values:
        changed = deepcopy(payload)
        values = changed[collection]
        assert isinstance(values, list)
        item = values[0]
        assert isinstance(item, dict)
        item[key] = value
        invalid_payloads.append(changed)

    invalid_payloads.extend(
        (
            {**payload, "run_id": True},
            {**payload, "account_kind": "invalid"},
            {**payload, "starting_cash": True},
            {**payload, "retained_cash_weight": "NaN"},
            {**payload, "cost_input_source": "changed"},
            {
                **payload,
                "material_fingerprints": {
                    "configuration": "1" * 64,
                },
            },
            {
                **payload,
                "material_fingerprints": {
                    **dict(account.material_fingerprints),
                    "configuration": True,
                },
            },
        )
    )

    invalid_payloads.extend(
        {**payload, field: None}
        for field in (
            "cycle",
            "schema_version",
            "run_id",
            "account_kind",
            "sizing_method",
            "algorithm_version",
            "house_view_id",
            "policy_id",
            "input_id",
            "position_snapshot_id",
            "cash_currency",
            "cost_input_policy_id",
            "cost_input_source",
            "content_hash",
        )
    )
    invalid_payloads.extend(
        {**payload, field: ()}
        for field in ("targets", "target_bands", "cost_inputs", "material_fingerprints")
    )

    assert all(parse_portfolio_shadow_account(value) is None for value in invalid_payloads)


def test_shadow_cycle_rejects_resealed_policy_and_same_input_substitutions() -> None:
    cycle = construct_portfolio_cycle(
        _request((_resolution(_AAPL, _HASH_A), _resolution(_SPY, _HASH_B)))
    )
    account = cycle.shadows[0]

    invalid_profile_payloads: list[dict[str, object]] = []
    changed_method = deepcopy(account.to_payload())
    changed_method["sizing_method"] = "equal_weight"
    invalid_profile_payloads.append(changed_method)

    changed_target = deepcopy(account.to_payload())
    targets = mutable_mapping_list(changed_target["targets"])
    bands = mutable_mapping_list(changed_target["target_bands"])
    mutable_mapping(targets[0])["target_weight"] = "0.9"
    mutable_mapping(bands[0]).update(
        {"target_weight": "0.9", "lower_weight": "0.9", "upper_weight": "0.9"}
    )
    changed_target["retained_cash_weight"] = "0.05"
    invalid_profile_payloads.append(changed_target)
    changed_liquidity = deepcopy(account.to_payload())
    mutable_mapping(mutable_mapping_list(changed_liquidity["cost_inputs"])[0])[
        "median_dollar_volume"
    ] = "1000"
    invalid_profile_payloads.append(changed_liquidity)

    for payload in invalid_profile_payloads:
        _reseal_shadow_payload(payload)
        assert parse_portfolio_shadow_account(payload) is None

    cycle_substitutions: list[dict[str, object]] = []
    changed_cycle = deepcopy(account.to_payload())
    changed_cycle["cycle"] = MarketSession(date.fromisoformat("2026-08-20")).to_payload()
    cycle_substitutions.append(changed_cycle)
    changed_snapshot = deepcopy(account.to_payload())
    changed_snapshot["position_snapshot_id"] = "f" * 64
    changed_fingerprint = deepcopy(account.to_payload())
    mutable_mapping(changed_fingerprint["material_fingerprints"])["configuration"] = "f" * 64
    cycle_substitutions.append(changed_fingerprint)
    changed_price = deepcopy(account.to_payload())
    mutable_mapping(mutable_mapping_list(changed_price["cost_inputs"])[0])["price"] = "101"
    cycle_substitutions.append(changed_price)
    changed_source = deepcopy(account.to_payload())
    changed_source["cost_input_source"] = "substituted-source"

    for payload in (changed_snapshot, changed_source):
        _reseal_shadow_payload(payload)
        assert parse_portfolio_shadow_account(payload) is None

    for payload in cycle_substitutions:
        _reseal_shadow_payload(payload)
        substituted = parse_portfolio_shadow_account(payload)
        assert substituted is not None
        with pytest.raises(ValueError, match="invalid portfolio cycle result"):
            PortfolioCycleResult(
                cycle.balanced,
                (substituted, cycle.shadows[1], cycle.shadows[2]),
            )


def test_shadow_cycle_rejects_collectively_substituted_pinned_inputs() -> None:
    cycle = construct_portfolio_cycle(
        _request((_resolution(_AAPL, _HASH_A), _resolution(_SPY, _HASH_B)))
    )
    original_inputs = cycle.shadows[0].portfolio_inputs
    substituted_inputs = PortfolioInputSet.create(
        position_snapshot=original_inputs.position_snapshot,
        cash=original_inputs.cash,
        cash_currency=original_inputs.cash_currency,
        source_identity="substituted-source",
        observed_at=original_inputs.observed_at,
        available_at=original_inputs.available_at,
        data_regime=original_inputs.data_regime,
        risk_inputs=original_inputs.risk_inputs,
    )
    substituted: list[PortfolioShadowAccount] = []
    for account in cycle.shadows:
        payload = deepcopy(account.to_payload())
        payload.update(
            {
                "input_id": substituted_inputs.input_id,
                "portfolio_inputs": substituted_inputs.to_payload(),
                "cost_input_source": substituted_inputs.source_identity,
            }
        )
        mutable_mapping(payload["material_fingerprints"])["portfolio_input"] = (
            substituted_inputs.input_id
        )
        _reseal_shadow_payload(payload)
        parsed = parse_portfolio_shadow_account(payload)
        assert parsed is not None
        substituted.append(parsed)

    with pytest.raises(ValueError, match="invalid portfolio cycle result"):
        PortfolioCycleResult(cycle.balanced, tuple(substituted))


def test_shadow_parser_rejects_resealed_gross_and_group_limit_breaches() -> None:
    gross_identities = tuple(_identity(index) for index in range(11))
    gross_cycle = construct_portfolio_cycle(
        _request(
            tuple(
                _resolution(identity, _hash(f"gross-{index}"))
                for index, identity in enumerate(gross_identities)
            ),
            inputs=_inputs_for(
                tuple(
                    _risk_input(
                        identity,
                        f"sector-{index}",
                        common=f"cause-{index}",
                        cluster=f"cluster-{index}",
                    )
                    for index, identity in enumerate(gross_identities)
                )
            ),
        )
    )
    gross_payload = deepcopy(gross_cycle.shadows[2].to_payload())
    _replace_all_shadow_targets(gross_payload, Decimal("0.08"), Decimal("0.12"))
    _reseal_shadow_payload(gross_payload)
    assert parse_portfolio_shadow_account(gross_payload) is None

    group_identities = tuple(_identity(index) for index in range(6))
    group_cycle = construct_portfolio_cycle(
        _request(
            tuple(
                _resolution(identity, _hash(f"group-{index}"))
                for index, identity in enumerate(group_identities)
            ),
            inputs=_inputs_for(
                tuple(
                    _risk_input(
                        identity,
                        "shared-sector",
                        common=f"cause-{index}",
                        cluster=f"cluster-{index}",
                    )
                    for index, identity in enumerate(group_identities)
                )
            ),
        )
    )
    group_payload = deepcopy(group_cycle.shadows[0].to_payload())
    _replace_all_shadow_targets(group_payload, Decimal("0.05"), Decimal("0.70"))
    _reseal_shadow_payload(group_payload)
    assert parse_portfolio_shadow_account(group_payload) is None


def test_shadow_cycle_rejects_targets_incompatible_with_house_view_authority() -> None:
    event = MaterialEventRisk(
        "scheduled-event",
        "company_release",
        UtcInstant.from_datetime(_CUTOFF.value + timedelta(days=1)),
        "issuer-calendar-v1",
        UtcInstant.from_datetime(_CUTOFF.value - timedelta(hours=1)),
    )
    event_cycle = construct_portfolio_cycle(
        _request(
            (_resolution(_AAPL, _HASH_A),),
            inputs=_inputs_for(
                (
                    replace(
                        _risk_input(_AAPL, "technology"),
                        material_events=(event,),
                    ),
                )
            ),
        )
    )
    ineligible_cycle = construct_portfolio_cycle(
        _request(
            (replace(_resolution(_AAPL, _HASH_A), eligible_for_new_entry=False),),
            inputs=_inputs_for((_risk_input(_AAPL, "technology"),)),
        )
    )
    held_ineligible_cycle = construct_portfolio_cycle(
        _request(
            (
                replace(
                    _resolution(_AAPL, _HASH_A),
                    eligible_for_new_entry=False,
                    is_position=True,
                ),
            ),
            inputs=_inputs_with_position(Decimal("0.03")),
        )
    )

    for cycle, substituted_weight in (
        (event_cycle, Decimal("0.01")),
        (ineligible_cycle, Decimal("0.01")),
        (held_ineligible_cycle, Decimal("0.04")),
    ):
        substituted = _shadow_with_substituted_target(
            cycle.shadows[0],
            substituted_weight,
        )
        with pytest.raises(ValueError, match="invalid portfolio cycle result"):
            PortfolioCycleResult(
                cycle.balanced,
                (substituted, cycle.shadows[1], cycle.shadows[2]),
            )


def test_shadow_value_types_reject_invalid_direct_construction() -> None:
    account = construct_portfolio_cycle(
        _request((_resolution(_AAPL, _HASH_A), _resolution(_SPY, _HASH_B)))
    ).shadows[0]
    band = account.target_bands[0]
    cost = account.cost_inputs[0]

    with pytest.raises(ValueError, match="invalid portfolio shadow account"):
        ShadowTargetBand(
            band.identity,
            band.target_weight,
            Decimal(1),
            band.upper_weight,
            band.current_weight,
            band.adjustment_weight,
            band.accounting_reason,
        )
    with pytest.raises(ValueError, match="invalid portfolio shadow account"):
        ShadowCostInput(
            cost.identity,
            Decimal(0),
            cost.median_dollar_volume,
            cost.sector,
            cost.common_cause_group,
            cost.correlation_cluster,
            cost.current_weight,
            cost.adjustment_weight,
        )


def test_equal_weight_changes_only_sizing_over_the_same_admitted_subjects() -> None:
    identities = tuple(_identity(index) for index in range(20))
    request = _request(
        tuple(
            _resolution(identity, _hash(str(index))) for index, identity in enumerate(identities)
        ),
        inputs=_inputs_for(
            tuple(
                _risk_input(
                    identity,
                    f"sector-{index}",
                    common=f"cause-{index}",
                    cluster=f"cluster-{index}",
                    amplitude=index + 1,
                )
                for index, identity in enumerate(identities)
            )
        ),
    )

    cycle = construct_portfolio_cycle(request)
    equal_weight = cycle.shadows[2]

    assert all(item.target_weight == Decimal("0.04") for item in equal_weight.targets)
    assert tuple(item.identity for item in equal_weight.targets) == tuple(
        item.identity for item in cycle.balanced.targets
    )
    assert tuple(item.target_weight for item in equal_weight.targets) != tuple(
        item.target_weight for item in cycle.balanced.targets
    )
    for inverse_volatility in cycle.shadows[:2]:
        assert tuple(item.target_weight for item in inverse_volatility.targets) != tuple(
            item.target_weight for item in equal_weight.targets
        )
        assert (
            inverse_volatility.targets[0].target_weight
            != inverse_volatility.targets[-1].target_weight
        )


def test_shadow_calculation_is_independent_of_the_ambient_decimal_context() -> None:
    identities = tuple(_identity(index) for index in range(20))
    request = _request(
        tuple(
            _resolution(identity, _hash(str(index))) for index, identity in enumerate(identities)
        ),
        inputs=_inputs_for(
            tuple(
                _risk_input(
                    identity,
                    f"sector-{index}",
                    common=f"cause-{index}",
                    cluster=f"cluster-{index}",
                    amplitude=index + 1,
                )
                for index, identity in enumerate(identities)
            )
        ),
    )
    expected = construct_portfolio_cycle(request)

    with localcontext() as context:
        context.prec = 2
        actual = construct_portfolio_cycle(request)

    assert actual == expected


@given(st.integers(min_value=2, max_value=30))
@example(14)
def test_shadow_profiles_preserve_caps_cash_and_canonical_order(count: int) -> None:
    identities = tuple(_identity(index) for index in range(count))
    resolutions = tuple(
        replace(
            _resolution(identity, _hash(str(index))),
            is_position=(index == 0),
        )
        for index, identity in enumerate(identities)
    )
    event = MaterialEventRisk(
        "scheduled-event",
        "company_release",
        UtcInstant.from_datetime(_CUTOFF.value + timedelta(days=1)),
        "issuer-calendar-v1",
        UtcInstant.from_datetime(_CUTOFF.value - timedelta(hours=1)),
    )
    risks = tuple(
        replace(
            _risk_input(
                identity,
                f"sector-{index % 3}",
                common=f"cause-{index % 2}",
                cluster=f"cluster-{index % 4}",
                liquidity=Decimal(100000 + index * 100000),
                amplitude=index + 1,
            ),
            material_events=((event,) if index == count - 1 else ()),
        )
        for index, identity in enumerate(identities)
    )
    inputs = _inputs_for(
        risks,
        positions=(_position(identities[0], Decimal("0.03")),),
        cash=Decimal(97000),
    )
    request = _request(resolutions, inputs=inputs)

    cycle = construct_portfolio_cycle(request)
    reordered = construct_portfolio_cycle(_request(tuple(reversed(resolutions)), inputs=inputs))

    for account, policy in zip(cycle.shadows, request.policy.shadow_policies, strict=True):
        assert sum(item.target_weight for item in account.targets) <= policy.maximum_gross_weight
        assert all(item.target_weight <= policy.maximum_name_weight for item in account.targets)
        assert all(
            target.target_weight
            <= cost.median_dollar_volume
            * request.policy.maximum_fraction_of_median_dollar_volume
            / account.starting_equity
            for target, cost in zip(account.targets, account.cost_inputs, strict=True)
        )
        for attribute, cap in (
            ("sector", policy.maximum_sector_weight),
            ("common_cause_group", request.policy.maximum_common_cause_weight),
            ("correlation_cluster", request.policy.maximum_correlation_cluster_weight),
        ):
            totals: dict[str, Decimal] = {}
            for target, cost in zip(account.targets, account.cost_inputs, strict=True):
                group = str(getattr(cost, attribute))
                totals[group] = totals.get(group, Decimal(0)) + target.target_weight
            assert all(total <= cap for total in totals.values())
        assert (
            sum(item.target_weight for item in account.targets) + account.retained_cash_weight == 1
        )
        assert account.cost_inputs[0].current_weight == Decimal("0.03")
        assert account.target_bands[0].current_weight == Decimal("0.03")
        assert account.targets[-1].target_weight == 0
        assert account.target_bands[-1].accounting_reason is PortfolioTradeReason.EVENT_BLOCKED
    assert tuple(item.to_payload() for item in cycle.shadows) == tuple(
        item.to_payload() for item in reordered.shadows
    )


def test_construction_is_invariant_to_the_ambient_decimal_context() -> None:
    identities = tuple(_identity(index) for index in range(11))
    request = _request(
        tuple(
            _resolution(identity, _hash(str(index))) for index, identity in enumerate(identities)
        ),
        inputs=_inputs_for(
            tuple(
                _risk_input(
                    identity,
                    f"sector-{index}",
                    common=f"cause-{index}",
                    cluster=f"cluster-{index}",
                )
                for index, identity in enumerate(identities)
            )
        ),
    )

    with localcontext() as context:
        context.prec = 8
        low_precision = construct_balanced_portfolio(request).to_payload()
    with localcontext() as context:
        context.prec = 50
        high_precision = construct_balanced_portfolio(request).to_payload()

    assert low_precision == high_precision


def test_target_band_suppresses_an_exact_in_band_holding_adjustment() -> None:
    request = PortfolioConstructionRequest(
        run_id="1" * 64,
        cycle=MarketSession(date.fromisoformat("2026-08-21")),
        evidence_cutoff=_CUTOFF,
        data_regime="alpaca-basic-iex-v1",
        configuration_hash="2" * 64,
        constitution_version=1,
        constitution_hash="3" * 64,
        research_policy_hash="4" * 64,
        research_artifact_ids=(_HASH_A,),
        memory_event_ids=("5" * 64,),
        universe_snapshot_id="7" * 64,
        expected_research_request_ids=(_HASH_A,),
        resolutions=(
            HouseViewResolution(
                identity=_AAPL,
                request_id=_HASH_A,
                resolution_id=_HASH_A,
                stance=PortfolioStance.HOLD,
                uncertainty="low",
                production_authority=True,
                is_position=True,
            ),
        ),
        inputs=_inputs_with_position(Decimal("0.08")),
        policy=_policy(),
    )

    result = construct_balanced_portfolio(request)

    assert result.refusal is None
    assert len(result.target_bands) == 1
    band = result.target_bands[0]
    assert band.current_weight == Decimal("0.08")
    assert band.adjustment_weight == Decimal("0.08")
    assert band.trade_eligible is False
    assert band.trade_reason.value == "in_band"


def test_balanced_policy_parser_requires_the_complete_approved_risk_envelope() -> None:
    payload = _policy().to_payload()

    assert BalancedPortfolioPolicy.parse(payload) == _policy()
    assert BalancedPortfolioPolicy.parse({**payload, "maximum_gross_weight": "0.81"}) is None
    assert BalancedPortfolioPolicy.parse({**payload, "schema_version": True}) is None
    assert BalancedPortfolioPolicy.parse({**payload, "shadow_accounts": []}) is None
    assert BalancedPortfolioPolicy.parse({**payload, "modeled_cost_inputs": {}}) is None
    changed_shadows = deepcopy(payload)
    shadow_accounts = mutable_mapping_list(changed_shadows["shadow_accounts"])
    mutable_mapping(shadow_accounts[0])["maximum_gross_weight"] = "0.61"
    assert BalancedPortfolioPolicy.parse(changed_shadows) is None
    with pytest.raises(ValueError, match="Balanced portfolio policy"):
        replace(_policy(), schema_version=True)


def test_portfolio_policy_parser_rejects_hostile_shadow_and_cost_shapes() -> None:
    payload = _policy().to_payload()
    shadows = mutable_mapping_list(payload["shadow_accounts"])
    cost_inputs = mutable_mapping(payload["modeled_cost_inputs"])

    invalid_shadow_shapes: tuple[dict[str, object], ...] = (
        {**shadows[0], "unexpected": True},
        {**shadows[0], "maximum_gross_weight": "invalid"},
        {**shadows[0], "maximum_name_weight": "invalid"},
        {**shadows[0], "maximum_sector_weight": "invalid"},
        {**shadows[0], "algorithm_version": "1"},
        {**shadows[0], "account_kind": "invalid"},
    )
    for changed_shadow in invalid_shadow_shapes:
        changed = deepcopy(payload)
        mutable_mapping_list(changed["shadow_accounts"])[0] = changed_shadow
        assert BalancedPortfolioPolicy.parse(changed) is None

    invalid_cost_shapes: tuple[object, ...] = (
        {**cost_inputs, "unexpected": True},
        {**cost_inputs, "schema_version": True},
        {**cost_inputs, "model_type": True},
        {**cost_inputs, "turnover_basis": True},
        {**cost_inputs, "price_basis": True},
        {**cost_inputs, "model_type": "invalid"},
    )
    for changed_cost in invalid_cost_shapes:
        changed = deepcopy(payload)
        changed["modeled_cost_inputs"] = changed_cost
        assert BalancedPortfolioPolicy.parse(changed) is None

    with pytest.raises(ValueError, match="Balanced portfolio policy"):
        replace(_policy().cost_input_policy, model_type="invalid")


def test_v0_policy_cannot_exceed_its_approved_clamp_envelope() -> None:
    with pytest.raises(ValueError, match="Balanced portfolio policy"):
        replace(_policy(), maximum_common_cause_weight=Decimal("0.26"))
    with pytest.raises(ValueError, match="Balanced portfolio policy"):
        replace(_policy(), maximum_correlation_cluster_weight=Decimal("0.26"))
    with pytest.raises(ValueError, match="Balanced portfolio policy"):
        replace(_policy(), maximum_fraction_of_median_dollar_volume=Decimal("0.02"))


@pytest.mark.parametrize("stance", [PortfolioStance.LONG, PortfolioStance.HOLD])
def test_pending_material_event_blocks_only_a_new_position(
    stance: PortfolioStance,
) -> None:
    inputs = _inputs()
    pending = MaterialEventRisk(
        event_id="aapl-earnings-2026q3",
        event_type="company_release",
        releases_at=UtcInstant.from_datetime(_CUTOFF.value + timedelta(days=1)),
        source_identity="issuer-calendar-v1",
        calendar_available_at=inputs.available_at,
    )
    risk_inputs = tuple(
        PortfolioRiskInput(
            item.identity,
            item.price,
            item.price_unit,
            item.adjusted_closes,
            item.sector,
            item.median_dollar_volume,
            item.liquidity_unit,
            item.common_cause_group,
            item.correlation_cluster,
            (pending,) if item.identity == _AAPL else (),
        )
        for item in inputs.risk_inputs
    )
    blocked_inputs = PortfolioInputSet.create(
        position_snapshot=inputs.position_snapshot,
        cash=inputs.cash,
        cash_currency=inputs.cash_currency,
        source_identity=inputs.source_identity,
        observed_at=inputs.observed_at,
        available_at=inputs.available_at,
        data_regime=inputs.data_regime,
        risk_inputs=risk_inputs,
    )
    request = PortfolioConstructionRequest(
        run_id="1" * 64,
        cycle=MarketSession(date.fromisoformat("2026-08-21")),
        evidence_cutoff=_CUTOFF,
        data_regime="alpaca-basic-iex-v1",
        configuration_hash="2" * 64,
        constitution_version=1,
        constitution_hash="3" * 64,
        research_policy_hash="4" * 64,
        research_artifact_ids=(_HASH_A,),
        memory_event_ids=("5" * 64,),
        universe_snapshot_id="7" * 64,
        expected_research_request_ids=(_HASH_A,),
        resolutions=(replace(_resolution(_AAPL, _HASH_A), stance=stance),),
        inputs=blocked_inputs,
        policy=_policy(),
    )

    result = construct_balanced_portfolio(request)

    assert result.refusal is None
    assert result.house_view is not None
    assert result.house_view.items[0].eligible is True
    assert result.targets[0].target_weight == Decimal(0)
    assert result.target_bands[0].trade_eligible is False
    assert result.target_bands[0].trade_reason.value == "event_blocked"


def test_a_bare_cited_artifact_id_cannot_masquerade_as_official_release_evidence() -> None:
    event = MaterialEventRisk(
        event_id="aapl-earnings-2026q3",
        event_type="company_release",
        releases_at=UtcInstant.from_datetime(_CUTOFF.value - timedelta(hours=1)),
        source_identity="issuer-release-1",
        calendar_available_at=UtcInstant.from_datetime(_CUTOFF.value - timedelta(days=1)),
    )
    risk = replace(_risk_input(_AAPL, "technology"), material_events=(event,))
    resolution = replace(_resolution(_AAPL, _HASH_A), evidence_artifact_ids=(_HASH_A,))

    result = construct_balanced_portfolio(_request((resolution,), inputs=_inputs_for((risk,))))

    assert result.house_view is not None
    assert result.house_view.items[0].event_blocked is True


def test_portfolio_result_round_trips_only_through_its_canonical_hashed_envelope() -> None:
    request = PortfolioConstructionRequest(
        run_id="1" * 64,
        cycle=MarketSession(date.fromisoformat("2026-08-21")),
        evidence_cutoff=_CUTOFF,
        data_regime="alpaca-basic-iex-v1",
        configuration_hash="2" * 64,
        constitution_version=1,
        constitution_hash="3" * 64,
        research_policy_hash="4" * 64,
        research_artifact_ids=(_HASH_A,),
        memory_event_ids=("5" * 64,),
        universe_snapshot_id="7" * 64,
        expected_research_request_ids=(_HASH_A,),
        resolutions=(_resolution(_AAPL, _HASH_A),),
        inputs=_inputs(),
        policy=_policy(),
    )
    result = construct_balanced_portfolio(request)
    payload = result.to_payload()

    assert parse_portfolio_construction_result(payload) == result
    assert parse_portfolio_construction_result({**payload, "cash_weight": "0.99"}) is None


@pytest.mark.parametrize("location", ["root", "house_view", "constitution"])
def test_portfolio_result_rejects_boolean_integer_schema_fields(location: str) -> None:
    payload = construct_balanced_portfolio(_request((_resolution(_AAPL, _HASH_A),))).to_payload()
    if location == "root":
        payload["schema_version"] = True
    else:
        house = _house_view_payload(payload)
        house["schema_version" if location == "house_view" else "constitution_version"] = True
        house_material = {key: value for key, value in house.items() if key != "house_view_id"}
        house["house_view_id"] = _test_content_hash(house_material)
    material = {key: value for key, value in payload.items() if key != "content_hash"}
    payload["content_hash"] = _test_content_hash(material)

    assert parse_portfolio_construction_result(payload) is None


@pytest.mark.parametrize("corruption", ["policy_binding", "target_weight", "target_identity"])
def test_hostile_resealed_result_must_bind_every_component(corruption: str) -> None:
    payload = construct_balanced_portfolio(_request((_resolution(_AAPL, _HASH_A),))).to_payload()
    if corruption == "policy_binding":
        house = _house_view_payload(payload)
        house["policy_id"] = "f" * 64
        house_material = {key: value for key, value in house.items() if key != "house_view_id"}
        house["house_view_id"] = _test_content_hash(house_material)
    elif corruption == "target_weight":
        _bands(payload)[0]["target_weight"] = "0.07"
    else:
        _targets(payload)[0]["identity"] = _SPY.to_payload()
        _bands(payload)[0]["identity"] = _SPY.to_payload()
    material = {key: value for key, value in payload.items() if key != "content_hash"}
    payload["content_hash"] = _test_content_hash(material)

    assert parse_portfolio_construction_result(payload) is None


def test_canonical_portfolio_material_has_stable_content_identities() -> None:
    inputs = _inputs()
    policy = _policy()
    result = construct_balanced_portfolio(_request((_resolution(_AAPL, _HASH_A),)))

    assert inputs.input_id == "98e69eac483ad140d159dc53d0df78b0d4384a2ec346feee9117250b373a3662"
    assert policy.policy_id == "131be6aa9c10dd88729db052e0086706775e2adc8a8d6984ec3876048a17752a"
    assert result.house_view is not None
    assert (
        result.house_view.house_view_id
        == "058a08fc7056c34440cf5aa40a4ddc64dcd8939b61fa425caf0147371c8ca508"
    )
    assert result.content_hash == "31895c94eb557618de0bea20c030840956dd1fdff0dafdb1e5407b40d0ecd7b4"
    assert (
        result.target_bands[0].target_band_id
        == "19bb8f2b0c9c37e13cc7909e77abe8bcc936d9c85d926319c46d035d577d2d3f"
    )


def test_material_event_fields_are_bound_into_the_canonical_input_identity() -> None:
    inputs = _inputs()
    event = MaterialEventRisk(
        event_id="aapl-earnings-2026q3",
        event_type="company_release",
        releases_at=UtcInstant.from_datetime(_CUTOFF.value + timedelta(days=1)),
        source_identity="issuer-calendar-v1",
        calendar_available_at=inputs.available_at,
    )
    risks = tuple(
        replace(item, material_events=(event,) if item.identity == _AAPL else ())
        for item in inputs.risk_inputs
    )

    event_inputs = PortfolioInputSet.create(
        position_snapshot=inputs.position_snapshot,
        cash=inputs.cash,
        cash_currency=inputs.cash_currency,
        source_identity=inputs.source_identity,
        observed_at=inputs.observed_at,
        available_at=inputs.available_at,
        data_regime=inputs.data_regime,
        risk_inputs=risks,
    )

    assert (
        event_inputs.input_id == "61d818e08507c5c357ca0bed888cd18fd0cb1f34a48766e456a8f442bf6e9e04"
    )


def test_refusal_round_trips_with_a_stable_canonical_identity() -> None:
    request = replace(_request((_resolution(_AAPL, _HASH_A),)), data_regime="other")

    result = construct_balanced_portfolio(request)

    assert result.refusal is PortfolioRefusalReason.INVALID_REQUEST
    assert result.content_hash == "886432dbd0aaf562b60ccf7cb479d6e0bbc107939f08a73044c8e6be5c74e1e9"
    assert parse_portfolio_construction_result(result.to_payload()) == result


def test_missing_terminal_resolution_and_lab_authority_fail_closed() -> None:
    missing = _request((_resolution(_AAPL, _HASH_A),), expected=(_HASH_A, _HASH_B))
    lab = _request(
        (replace(_resolution(_AAPL, _HASH_A), production_authority=False),),
    )

    assert construct_balanced_portfolio(missing).refusal is PortfolioRefusalReason.INCOMPLETE_INPUT
    assert construct_balanced_portfolio(lab).refusal is PortfolioRefusalReason.AUTHORITY_VIOLATION


def test_every_held_position_requires_one_position_marked_terminal_resolution() -> None:
    held_without_resolution = _request(
        (),
        expected=(),
        inputs=_inputs_with_position(Decimal("0.50")),
    )
    mixed = _request(
        (_resolution(_SPY, _HASH_A),),
        expected=(_HASH_A,),
        inputs=_inputs_with_position(Decimal("0.50")),
    )

    assert (
        construct_balanced_portfolio(held_without_resolution).refusal
        is PortfolioRefusalReason.INCOMPLETE_INPUT
    )
    assert construct_balanced_portfolio(mixed).refusal is PortfolioRefusalReason.INCOMPLETE_INPUT


def test_empty_terminal_resolution_set_returns_full_cash_without_targets() -> None:
    result = construct_balanced_portfolio(_request((), expected=()))

    assert result.refusal is None
    assert result.house_view is not None
    assert result.house_view.items == ()
    assert result.targets == ()
    assert result.target_bands == ()
    assert result.cash_weight == Decimal(1)


@pytest.mark.parametrize(
    ("stance", "current_weight", "target_weight", "adjustment", "reason"),
    [
        (
            PortfolioStance.LONG,
            Decimal(0),
            Decimal("0.08"),
            Decimal("0.04"),
            PortfolioTradeReason.THESIS_ENTRY,
        ),
        (
            PortfolioStance.HOLD,
            Decimal("0.04"),
            Decimal("0.08"),
            Decimal("0.06"),
            PortfolioTradeReason.BAND_BREACH,
        ),
        (
            PortfolioStance.REDUCE,
            Decimal("0.05"),
            Decimal("0.025"),
            Decimal("0.0375"),
            PortfolioTradeReason.THESIS_REDUCTION,
        ),
        (
            PortfolioStance.EXIT,
            Decimal("0.05"),
            Decimal(0),
            Decimal(0),
            PortfolioTradeReason.THESIS_EXIT,
        ),
    ],
)
def test_target_band_authorizes_only_the_frozen_entry_exit_and_partial_rules(
    stance: PortfolioStance,
    current_weight: Decimal,
    target_weight: Decimal,
    adjustment: Decimal,
    reason: PortfolioTradeReason,
) -> None:
    resolution = replace(
        _resolution(_AAPL, _HASH_A),
        stance=stance,
        is_position=current_weight > 0,
    )
    inputs = _inputs_with_position(current_weight) if current_weight > 0 else _inputs()

    band = construct_balanced_portfolio(_request((resolution,), inputs=inputs)).target_bands[0]

    assert band.target_weight == target_weight
    assert band.adjustment_weight == adjustment
    assert band.trade_eligible is True
    assert band.trade_reason is reason


def test_exact_lower_band_boundary_suppresses_adjustment() -> None:
    resolution = replace(
        _resolution(_AAPL, _HASH_A),
        stance=PortfolioStance.HOLD,
        is_position=True,
    )

    band = construct_balanced_portfolio(
        _request((resolution,), inputs=_inputs_with_position(Decimal("0.07")))
    ).target_bands[0]

    assert band.current_weight == band.lower_weight == Decimal("0.07")
    assert band.trade_eligible is False
    assert band.trade_reason is PortfolioTradeReason.IN_BAND


def test_exact_minimum_notional_is_executable() -> None:
    resolution = replace(
        _resolution(_AAPL, _HASH_A),
        stance=PortfolioStance.HOLD,
        is_position=True,
    )
    policy = replace(_policy(), minimum_executable_notional=Decimal(2000))

    band = construct_balanced_portfolio(
        _request(
            (resolution,),
            inputs=_inputs_with_position(Decimal("0.04")),
            policy=policy,
        )
    ).target_bands[0]

    assert abs(band.adjustment_weight - band.current_weight) * Decimal(100000) == Decimal(2000)
    assert band.trade_eligible is True


def test_minimum_notional_suppresses_an_out_of_band_partial_adjustment() -> None:
    policy = replace(_policy(), minimum_executable_notional=Decimal(10000))
    resolution = replace(
        _resolution(_AAPL, _HASH_A),
        stance=PortfolioStance.HOLD,
        is_position=True,
    )

    band = construct_balanced_portfolio(
        _request((resolution,), inputs=_inputs_with_position(Decimal("0.04")), policy=policy)
    ).target_bands[0]

    assert band.trade_eligible is False
    assert band.adjustment_weight == band.current_weight
    assert band.trade_reason is PortfolioTradeReason.BELOW_MINIMUM_NOTIONAL


def test_hard_name_risk_breach_forces_a_compliant_existing_position_reduction() -> None:
    resolution = replace(
        _resolution(_AAPL, _HASH_A),
        stance=PortfolioStance.REDUCE,
        is_position=True,
    )

    result = construct_balanced_portfolio(
        _request((resolution,), inputs=_inputs_with_position(Decimal("0.20")))
    )
    band = result.target_bands[0]

    assert result.targets[0].target_weight == Decimal("0.08")
    assert band.adjustment_weight == Decimal("0.08")
    assert band.trade_eligible is True
    assert band.trade_reason is PortfolioTradeReason.HARD_RISK_BREACH


def test_pending_event_does_not_block_an_existing_position_risk_action() -> None:
    inputs = _inputs_with_position(Decimal("0.20"))
    pending = MaterialEventRisk(
        event_id="aapl-earnings-2026q3",
        event_type="company_release",
        releases_at=UtcInstant.from_datetime(_CUTOFF.value + timedelta(days=1)),
        source_identity="issuer-calendar-v1",
        calendar_available_at=inputs.available_at,
    )
    risk = replace(inputs.risk_inputs[0], material_events=(pending,))
    event_inputs = _inputs_for(
        (risk,),
        positions=tuple(
            item for item in inputs.position_snapshot.positions if type(item) is EquityPosition
        ),
        cash=inputs.cash,
    )
    resolution = replace(
        _resolution(_AAPL, _HASH_A),
        stance=PortfolioStance.REDUCE,
        is_position=True,
    )

    result = construct_balanced_portfolio(_request((resolution,), inputs=event_inputs))

    assert result.house_view is not None
    assert result.house_view.items[0].event_blocked is False
    assert result.target_bands[0].trade_reason is PortfolioTradeReason.HARD_RISK_BREACH


def test_uncertainty_can_only_reduce_an_otherwise_identical_allocation() -> None:
    identities = tuple(_identity(index) for index in range(12))
    inputs = _inputs_for(
        tuple(
            _risk_input(
                identity, f"sector-{index}", common=f"cause-{index}", cluster=f"cluster-{index}"
            )
            for index, identity in enumerate(identities)
        )
    )
    resolutions = tuple(
        replace(
            _resolution(identity, _hash(f"request-{index}")),
            uncertainty=("low", "medium", "high")[index % 3],
        )
        for index, identity in enumerate(identities)
    )

    result = construct_balanced_portfolio(_request(resolutions, inputs=inputs))
    weights = tuple(target.target_weight for target in result.targets[:3])

    assert weights[0] > weights[1] > weights[2]
    assert result.cash_weight > Decimal("0.20")


def test_inverse_volatility_estimator_has_an_exact_hand_oracle_and_lookback() -> None:
    identities = tuple(_identity(index) for index in range(12))
    risks = tuple(
        _risk_input(
            identity,
            f"sector-{index}",
            common=f"cause-{index}",
            cluster=f"cluster-{index}",
            amplitude=index + 2,
        )
        for index, identity in enumerate(identities)
    )
    resolutions = tuple(
        _resolution(identity, _hash(f"request-{index}"))
        for index, identity in enumerate(identities)
    )
    extra_old_close = _close(_days_before_cutoff(4), Decimal(1000))
    changed_old_close = replace(extra_old_close, price=Decimal(1))
    with_first_history = (
        replace(risks[0], adjusted_closes=(extra_old_close, *risks[0].adjusted_closes)),
        *risks[1:],
    )
    with_changed_old_history = (
        replace(risks[0], adjusted_closes=(changed_old_close, *risks[0].adjusted_closes)),
        *risks[1:],
    )

    result = construct_balanced_portfolio(
        _request(resolutions, inputs=_inputs_for(with_first_history))
    )
    changed = construct_balanced_portfolio(
        _request(resolutions, inputs=_inputs_for(with_changed_old_history))
    )

    assert tuple(target.target_weight for target in result.targets) == (
        Decimal("0.08"),
        Decimal("0.08"),
        Decimal("0.08"),
        Decimal("0.07323673977491713785684256264"),
        Decimal("0.06131277374437314708079784578"),
        Decimal("0.05279331844499679315422266952"),
        Decimal("0.04640171184228094757756680219"),
        Decimal("0.04142869674870315803172443891"),
        Decimal("0.03744871841098370654584580015"),
        Decimal("0.03419096886765227369016746703"),
        Decimal("0.03147490912758493346048789377"),
        Decimal("0.02917555034284261979490202688"),
    )
    assert changed.targets == result.targets


def test_sample_variance_divisor_changes_a_three_return_allocation() -> None:
    identities = tuple(_identity(index) for index in range(12))
    base_risks = tuple(
        _risk_input(
            identity,
            f"sector-{index}",
            common=f"cause-{index}",
            cluster=f"cluster-{index}",
            amplitude=index + 2,
        )
        for index, identity in enumerate(identities)
    )
    risks = tuple(
        replace(
            risk,
            adjusted_closes=(
                _close(_days_before_cutoff(4), Decimal(95 + index)),
                *risk.adjusted_closes,
            ),
        )
        for index, risk in enumerate(base_risks)
    )
    resolutions = tuple(
        _resolution(identity, _hash(f"request-{index}"))
        for index, identity in enumerate(identities)
    )

    result = construct_balanced_portfolio(
        _request(
            resolutions,
            inputs=_inputs_for(risks),
            policy=replace(_policy(), lookback_days=3),
        )
    )

    assert result.targets[4].target_weight == Decimal("0.07221326986106502550682927781")
    assert result.cash_weight == Decimal("0.2909222939874216463067067219")


def test_sector_common_cause_correlation_and_liquidity_clamps_leave_cash() -> None:
    identities = tuple(_identity(index) for index in range(6))
    risks = tuple(
        _risk_input(
            identity,
            "shared-sector",
            common="shared-cause",
            cluster="shared-cluster",
            liquidity=Decimal(100000) if index == 0 else Decimal(100000000),
        )
        for index, identity in enumerate(identities)
    )
    resolutions = tuple(
        _resolution(identity, _hash(f"request-{index}"))
        for index, identity in enumerate(identities)
    )

    result = construct_balanced_portfolio(_request(resolutions, inputs=_inputs_for(risks)))

    assert result.refusal is None
    assert sum((item.target_weight for item in result.targets), Decimal(0)) <= Decimal("0.25")
    assert result.targets[0].target_weight <= Decimal("0.01")
    assert result.cash_weight >= Decimal("0.75")


@pytest.mark.parametrize("shared_group", ["sector", "common", "cluster"])
def test_each_group_clamp_has_an_exact_hand_calculated_oracle(shared_group: str) -> None:
    identities = tuple(_identity(index) for index in range(4))
    risks = tuple(
        _risk_input(
            identity,
            "shared" if shared_group == "sector" else f"sector-{index}",
            common="shared" if shared_group == "common" else f"cause-{index}",
            cluster="shared" if shared_group == "cluster" else f"cluster-{index}",
        )
        for index, identity in enumerate(identities)
    )
    resolutions = tuple(
        _resolution(identity, _hash(f"request-{index}"))
        for index, identity in enumerate(identities)
    )

    result = construct_balanced_portfolio(_request(resolutions, inputs=_inputs_for(risks)))

    assert tuple(target.target_weight for target in result.targets) == (Decimal("0.0625"),) * 4
    assert result.cash_weight == Decimal("0.7500")


@given(
    st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=20),
            st.integers(min_value=0, max_value=4),
            st.integers(min_value=0, max_value=3),
            st.integers(min_value=0, max_value=2),
            st.sampled_from(("low", "medium", "high")),
        ),
        min_size=1,
        max_size=24,
    )
)
def test_randomized_portfolios_preserve_every_cap_cash_and_input_order(
    facts: list[tuple[int, int, int, int, str]],
) -> None:
    identities = tuple(_identity(index) for index in range(len(facts)))
    risks = tuple(
        _risk_input(
            identity,
            f"sector-{sector}",
            common=f"cause-{common}",
            cluster=f"cluster-{cluster}",
            amplitude=amplitude,
        )
        for identity, (amplitude, sector, common, cluster, _) in zip(identities, facts, strict=True)
    )
    resolutions = tuple(
        replace(
            _resolution(identity, _hash(f"request-{index}")),
            uncertainty=fact[4],
        )
        for index, (identity, fact) in enumerate(zip(identities, facts, strict=True))
    )
    inputs = _inputs_for(risks)

    result = construct_balanced_portfolio(_request(resolutions, inputs=inputs))
    reordered = construct_balanced_portfolio(
        _request(tuple(reversed(resolutions)), inputs=_inputs_for(tuple(reversed(risks))))
    )

    assert result == reordered
    assert result.refusal is None
    assert Decimal(0) <= result.cash_weight <= Decimal(1)
    assert sum((item.target_weight for item in result.targets), Decimal(0)) <= Decimal("0.80")
    assert all(Decimal(0) <= item.target_weight <= Decimal("0.08") for item in result.targets)
    by_identity = {item.identity: item for item in risks}
    for group_name, cap in (
        ("sector", Decimal("0.25")),
        ("common_cause_group", Decimal("0.25")),
        ("correlation_cluster", Decimal("0.25")),
    ):
        groups: dict[str, Decimal] = {}
        for target in result.targets:
            group = str(getattr(by_identity[target.identity], group_name))
            groups[group] = groups.get(group, Decimal(0)) + target.target_weight
        assert all(weight <= cap for weight in groups.values())


@pytest.mark.parametrize(
    "mutation",
    [
        {"maximum_name_weight": "0.09"},
        {"maximum_sector_weight": "0.26"},
        {"maximum_gross_weight": "0.79"},
        {"minimum_executable_notional": "0"},
        {"partial_adjustment_fraction": "1.1"},
        {"uncertainty_multipliers": {"low": "0.5", "medium": "0.75", "high": "1"}},
        {"model_confidence_multiplier": "2"},
    ],
)
def test_policy_rejects_changed_envelopes_and_model_upside_fields(
    mutation: dict[str, object],
) -> None:
    assert BalancedPortfolioPolicy.parse({**_policy().to_payload(), **mutation}) is None


def test_stale_future_or_insufficient_as_of_material_refuses_construction() -> None:
    inputs = _inputs()
    stale = _inputs_for(
        inputs.risk_inputs,
        observed_at=UtcInstant.from_datetime(_CUTOFF.value - timedelta(hours=3)),
    )
    contradictory_cutoff = UtcInstant.from_datetime(
        inputs.available_at.value - timedelta(seconds=1)
    )
    insufficient = replace(
        inputs.risk_inputs[0], adjusted_closes=inputs.risk_inputs[0].adjusted_closes[:2]
    )

    assert (
        construct_balanced_portfolio(_request((_resolution(_AAPL, _HASH_A),), inputs=stale)).refusal
        is PortfolioRefusalReason.STALE_INPUT
    )
    assert (
        construct_balanced_portfolio(
            replace(
                _request((_resolution(_SPY, _HASH_A),), inputs=inputs),
                evidence_cutoff=contradictory_cutoff,
            )
        ).refusal
        is PortfolioRefusalReason.CONTRADICTORY_INPUT
    )
    assert (
        construct_balanced_portfolio(
            _request(
                (_resolution(_AAPL, _HASH_A),),
                inputs=_inputs_for((insufficient, inputs.risk_inputs[1])),
            )
        ).refusal
        is PortfolioRefusalReason.INCOMPLETE_INPUT
    )


def test_adjusted_closes_require_one_exchange_session_per_daily_observation() -> None:
    risk = _risk_input(_AAPL, "technology")
    same_day = (
        _close(UtcInstant.from_datetime(datetime(2026, 8, 18, 19, tzinfo=UTC)), Decimal(100)),
        _close(UtcInstant.from_datetime(datetime(2026, 8, 18, 20, tzinfo=UTC)), Decimal(101)),
        _close(UtcInstant.from_datetime(datetime(2026, 8, 19, 20, tzinfo=UTC)), Decimal(100)),
    )

    with pytest.raises(ValueError, match="portfolio risk input"):
        replace(risk, adjusted_closes=same_day)
    with pytest.raises(ValueError, match="adjusted close"):
        _close(UtcInstant.from_datetime(datetime(2026, 8, 15, 20, tzinfo=UTC)), Decimal(100))
    late_close = replace(risk.adjusted_closes[-1], available_at=_CUTOFF)
    with pytest.raises(ValueError, match="portfolio input set"):
        _inputs_for((replace(risk, adjusted_closes=(*risk.adjusted_closes[:-1], late_close)),))


def test_exact_cutoff_and_maximum_age_boundaries_are_admissible() -> None:
    risk = _risk_input(_AAPL, "technology")
    event = MaterialEventRisk(
        event_id="aapl-earnings-2026q3",
        event_type="company_release",
        releases_at=UtcInstant.from_datetime(_CUTOFF.value - timedelta(hours=1)),
        source_identity="issuer-release-1",
        calendar_available_at=UtcInstant.from_datetime(_CUTOFF.value - timedelta(days=1)),
    )
    boundary_risk = replace(
        risk,
        adjusted_closes=(*risk.adjusted_closes[1:], _close(_CUTOFF, Decimal(100))),
        material_events=(event,),
    )
    inputs = _inputs_for(
        (boundary_risk,),
        observed_at=UtcInstant.from_datetime(_CUTOFF.value - timedelta(seconds=7200)),
        available_at=_CUTOFF,
    )

    resolution = replace(_resolution(_AAPL, _HASH_A), evidence_artifact_ids=(_HASH_A,))
    result = construct_balanced_portfolio(
        _request(
            (resolution,),
            inputs=inputs,
            event_evidence=(_release_evidence(),),
        )
    )

    assert result.refusal is None
    assert result.house_view is not None
    assert result.house_view.items[0].event_blocked is False
    assert result.target_bands[0].trade_reason is not PortfolioTradeReason.EVENT_BLOCKED

    same_instant_inputs = _inputs_for(
        (_risk_input(_AAPL, "technology"),),
        observed_at=_CUTOFF,
        available_at=_CUTOFF,
    )
    same_instant_result = construct_balanced_portfolio(
        _request((_resolution(_AAPL, _HASH_A),), inputs=same_instant_inputs)
    )

    assert same_instant_result.refusal is None


@pytest.mark.parametrize("corruption", ["missing_citation", "kind", "source", "time", "mapping"])
def test_event_clearance_binds_typed_release_provenance_and_current_citation(
    corruption: str,
) -> None:
    event = MaterialEventRisk(
        event_id="aapl-earnings-2026q3",
        event_type="company_release",
        releases_at=UtcInstant.from_datetime(_CUTOFF.value - timedelta(hours=1)),
        source_identity="issuer-release-1",
        calendar_available_at=UtcInstant.from_datetime(_CUTOFF.value - timedelta(days=1)),
    )
    risk = replace(_risk_input(_AAPL, "technology"), material_events=(event,))
    resolution = replace(
        _resolution(_AAPL, _HASH_A),
        evidence_artifact_ids=() if corruption == "missing_citation" else (_HASH_A,),
    )
    evidence = _release_evidence()
    if corruption == "kind":
        evidence = replace(evidence, evidence_kind="official_macro")
    elif corruption == "source":
        evidence = replace(evidence, source_identity="another-release")
    elif corruption == "time":
        evidence = replace(evidence, released_at=UtcInstant.from_datetime(_CUTOFF.value))
    elif corruption == "mapping":
        evidence = replace(evidence, mapped_identities=(_SPY,))

    result = construct_balanced_portfolio(
        _request(
            (resolution,),
            inputs=_inputs_for((risk,)),
            event_evidence=(evidence,),
        )
    )

    assert result.house_view is not None
    assert result.house_view.items[0].event_blocked is True
    assert result.target_bands[0].trade_reason is PortfolioTradeReason.EVENT_BLOCKED


def test_macro_event_clearance_accepts_matching_current_official_release() -> None:
    released_at = UtcInstant.from_datetime(_CUTOFF.value - timedelta(hours=1))
    event = MaterialEventRisk(
        event_id="fed-rate-decision-2026-08",
        event_type="macro_release",
        releases_at=released_at,
        source_identity="federal-reserve-release",
        calendar_available_at=UtcInstant.from_datetime(_CUTOFF.value - timedelta(days=1)),
    )
    risk = replace(_risk_input(_AAPL, "technology"), material_events=(event,))
    resolution = replace(_resolution(_AAPL, _HASH_A), evidence_artifact_ids=(_HASH_A,))
    evidence = MaterialEventEvidence(
        artifact_id=_HASH_A,
        evidence_kind="official_macro",
        source_identity="federal-reserve-release",
        released_at=released_at,
        available_at=_CUTOFF,
        mapped_identities=(),
    )

    result = construct_balanced_portfolio(
        _request(
            (resolution,),
            inputs=_inputs_for((risk,)),
            event_evidence=(evidence,),
        )
    )

    assert result.refusal is None
    assert result.house_view is not None
    assert result.house_view.items[0].event_blocked is False
    assert result.target_bands[0].trade_reason is not PortfolioTradeReason.EVENT_BLOCKED


def test_every_simultaneous_material_event_must_be_cleared_for_a_new_position() -> None:
    cleared = MaterialEventRisk(
        event_id="a-cleared-release",
        event_type="company_release",
        releases_at=UtcInstant.from_datetime(_CUTOFF.value - timedelta(hours=1)),
        source_identity="issuer-release-1",
        calendar_available_at=UtcInstant.from_datetime(_CUTOFF.value - timedelta(days=1)),
    )
    pending = MaterialEventRisk(
        event_id="z-pending-release",
        event_type="macro_release",
        releases_at=UtcInstant.from_datetime(_CUTOFF.value + timedelta(days=1)),
        source_identity="official-calendar-v1",
        calendar_available_at=_CUTOFF,
    )
    risk = replace(_risk_input(_AAPL, "technology"), material_events=(cleared, pending))
    inputs = _inputs_for((risk,))
    resolution = replace(_resolution(_AAPL, _HASH_A), evidence_artifact_ids=(_HASH_A,))

    result = construct_balanced_portfolio(
        _request(
            (resolution,),
            inputs=inputs,
            event_evidence=(_release_evidence(),),
        )
    )

    assert result.house_view is not None
    assert result.house_view.items[0].event_blocked is True
    assert result.target_bands[0].trade_reason is PortfolioTradeReason.EVENT_BLOCKED


def test_one_dollar_of_equity_remains_a_valid_cash_preserving_portfolio() -> None:
    result = construct_balanced_portfolio(
        _request(
            (_resolution(_AAPL, _HASH_A),),
            inputs=_inputs_for((_risk_input(_AAPL, "technology"),), cash=Decimal(1)),
        )
    )

    assert result.refusal is None
    assert result.cash_weight == Decimal("0.92")


def test_typed_portfolio_values_reject_invalid_or_duplicate_material() -> None:
    risk = _risk_input(_AAPL, "technology")

    with pytest.raises(ValueError, match="invalid adjusted close"):
        AdjustedClose(
            MarketSession(_CUTOFF.value.date()),
            _CUTOFF,
            _CUTOFF,
            "alpaca-iex-adjusted-daily-v1",
            Decimal(0),
        )
    holiday = UtcInstant.from_datetime(datetime(2026, 9, 7, 20, tzinfo=UTC))
    with pytest.raises(ValueError, match="invalid adjusted close"):
        AdjustedClose(
            MarketSession(holiday.value.date()),
            holiday,
            holiday,
            "alpaca-iex-adjusted-daily-v1",
            Decimal(100),
        )
    with pytest.raises(ValueError, match="invalid material event risk"):
        MaterialEventRisk(
            "",
            "company_release",
            _CUTOFF,
            "issuer-calendar-v1",
            _CUTOFF,
        )
    with pytest.raises(ValueError, match="invalid material event evidence"):
        MaterialEventEvidence(
            _HASH_A,
            "market",
            "alpaca-iex-v1",
            _CUTOFF,
            _CUTOFF,
            (_AAPL,),
        )
    with pytest.raises(ValueError, match="invalid portfolio risk input"):
        replace(risk, adjusted_closes=(risk.adjusted_closes[0], risk.adjusted_closes[0]))
    with pytest.raises(ValueError, match="invalid portfolio risk input"):
        replace(risk, adjusted_closes=risk.adjusted_closes[::2])
    with pytest.raises(ValueError, match="invalid portfolio input set"):
        PortfolioInputSet.create(
            position_snapshot=_inputs().position_snapshot,
            cash=Decimal(100000),
            cash_currency="USD",
            source_identity="recorded-portfolio-source-v1",
            observed_at=_inputs().observed_at,
            available_at=_inputs().available_at,
            data_regime="alpaca-basic-iex-v1",
            risk_inputs=[],  # type: ignore[arg-type]  # Prove the runtime boundary rejects lists.
        )
    with pytest.raises(ValueError, match="invalid portfolio input set"):
        _inputs_for((risk, risk))
    with pytest.raises(ValueError, match="invalid portfolio input set"):
        replace(_inputs(), input_id="f" * 64)


def test_pinned_session_calendar_crosses_weekend_and_exchange_holiday() -> None:
    closes = tuple(
        _close(
            UtcInstant.from_datetime(datetime(2026, 9, day, 20, tzinfo=UTC)),
            Decimal(100 + index),
        )
        for index, day in enumerate((3, 4, 8))
    )

    risk = replace(_risk_input(_AAPL, "technology"), adjusted_closes=closes)

    assert tuple(close.session.trading_date for close in risk.adjusted_closes) == (
        date(2026, 9, 3),
        date(2026, 9, 4),
        date(2026, 9, 8),
    )


def test_immutable_result_values_reject_internally_inconsistent_states() -> None:
    result = construct_balanced_portfolio(_request((_resolution(_AAPL, _HASH_A),)))
    assert result.house_view is not None
    item = result.house_view.items[0]
    band = result.target_bands[0]

    with pytest.raises(ValueError, match="invalid HouseView item"):
        replace(item, stance=PortfolioStance.ABSTAIN, eligible=True)
    with pytest.raises(ValueError, match="invalid HouseView"):
        replace(
            result.house_view,
            items=[],  # type: ignore[arg-type]  # Prove tuples are enforced at construction.
        )
    with pytest.raises(ValueError, match="invalid HouseView"):
        replace(result.house_view, house_view_id="f" * 64)
    with pytest.raises(ValueError, match="invalid portfolio target"):
        PortfolioTarget(_AAPL, Decimal("-0.01"))
    with pytest.raises(ValueError, match="invalid target band"):
        replace(band, trade_eligible=True, adjustment_weight=band.current_weight)
    with pytest.raises(ValueError, match="invalid portfolio construction result"):
        replace(
            result,
            targets=[],  # type: ignore[arg-type]  # Prove tuples are enforced at construction.
        )
    with pytest.raises(ValueError, match="invalid portfolio construction result"):
        replace(result, content_hash="f" * 64).to_payload()
    with localcontext() as context:
        context.prec = 2
        with pytest.raises(ValueError, match="invalid portfolio construction result"):
            replace(result, cash_weight=Decimal("0.9199999999999999999999999999"))

    assert len(band.target_band_id) == _HASH_LENGTH


def test_invalid_request_future_availability_and_zero_equity_fail_closed() -> None:
    invalid = replace(_request((_resolution(_AAPL, _HASH_A),)), data_regime="other")
    future = _inputs_for(
        (_risk_input(_AAPL, "technology"),),
        available_at=UtcInstant.from_datetime(_CUTOFF.value + timedelta(seconds=1)),
    )
    zero_equity = _inputs_for((_risk_input(_AAPL, "technology"),), cash=Decimal(0))
    future_calendar_event = MaterialEventRisk(
        event_id="future-known-event",
        event_type="company_release",
        releases_at=UtcInstant.from_datetime(_CUTOFF.value + timedelta(days=2)),
        source_identity="issuer-calendar-v1",
        calendar_available_at=UtcInstant.from_datetime(_CUTOFF.value + timedelta(seconds=1)),
    )
    future_calendar = _inputs_for(
        (
            replace(
                _risk_input(_AAPL, "technology"),
                material_events=(future_calendar_event,),
            ),
        )
    )

    assert construct_balanced_portfolio(invalid).refusal is PortfolioRefusalReason.INVALID_REQUEST
    assert (
        construct_balanced_portfolio(
            _request((_resolution(_AAPL, _HASH_A),), inputs=future)
        ).refusal
        is PortfolioRefusalReason.CONTRADICTORY_INPUT
    )
    assert (
        construct_balanced_portfolio(
            _request((_resolution(_AAPL, _HASH_A),), inputs=zero_equity)
        ).refusal
        is PortfolioRefusalReason.CONTRADICTORY_INPUT
    )
    assert (
        construct_balanced_portfolio(
            _request((_resolution(_AAPL, _HASH_A),), inputs=future_calendar)
        ).refusal
        is PortfolioRefusalReason.CONTRADICTORY_INPUT
    )


@pytest.mark.parametrize("field", ["observed_at", "available_at"])
def test_forged_future_adjusted_close_timestamp_fails_closed(field: str) -> None:
    inputs = _inputs_for((_risk_input(_AAPL, "technology"),))
    close = inputs.risk_inputs[0].adjusted_closes[-1]
    object.__setattr__(
        close,
        field,
        UtcInstant.from_datetime(_CUTOFF.value + timedelta(seconds=1)),
    )

    result = construct_balanced_portfolio(_request((_resolution(_AAPL, _HASH_A),), inputs=inputs))

    assert result.refusal is PortfolioRefusalReason.CONTRADICTORY_INPUT


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("identity", "not-an-equity-identity"),
        ("request_id", "a" * 63),
        ("resolution_id", "A" * 64),
        ("stance", "long"),
        ("uncertainty", "extreme"),
        ("production_authority", 1),
        ("eligible_for_new_entry", 1),
        ("is_position", 1),
    ],
)
def test_invalid_terminal_resolution_fields_return_a_typed_request_refusal(
    field: str,
    invalid_value: object,
) -> None:
    resolution = replace(
        _resolution(_AAPL, _HASH_A),
        **{field: invalid_value},  # type: ignore[arg-type]  # Exercise hostile runtime values.
    )

    result = construct_balanced_portfolio(_request((resolution,)))

    assert result.refusal is PortfolioRefusalReason.INVALID_REQUEST


@pytest.mark.parametrize("invalid_hash", ["a" * 63, "A" * 64, "X" * 64, 1])
def test_invalid_request_hashes_return_a_typed_refusal(invalid_hash: object) -> None:
    request = replace(
        _request((_resolution(_AAPL, _HASH_A),)),
        configuration_hash=invalid_hash,  # type: ignore[arg-type]  # Exercise hostile runtime values.
    )

    result = construct_balanced_portfolio(request)

    assert result.refusal is PortfolioRefusalReason.INVALID_REQUEST


def test_unavailable_invalid_request_pins_have_explicit_deterministic_identities() -> None:
    request = _request((_resolution(_AAPL, _HASH_A),))
    invalid_policy = replace(
        request,
        policy="invalid",  # type: ignore[arg-type]  # Exercise the typed refusal fallback.
    )

    result = construct_balanced_portfolio(invalid_policy)

    assert result.refusal is PortfolioRefusalReason.INVALID_REQUEST
    assert result.policy_id == "e8330921e97c6b1482ec9aa35b078a0faf00b2ae5af23e5d1d727bc428586238"
    assert result.input_id == request.inputs.input_id

    invalid_inputs = replace(
        request,
        inputs="invalid",  # type: ignore[arg-type]  # Exercise the typed refusal fallback.
    )

    input_result = construct_balanced_portfolio(invalid_inputs)

    assert input_result.refusal is PortfolioRefusalReason.INVALID_REQUEST
    assert input_result.policy_id == request.policy.policy_id
    assert (
        input_result.input_id == "882c53691ad360cf565f4a11bf79418e747dad07abcba8da67c0c536fd6b9099"
    )

    unavailable = construct_balanced_portfolio(
        "invalid",  # type: ignore[arg-type]  # Exercise the untyped hostile boundary.
    )

    assert unavailable.refusal is PortfolioRefusalReason.INVALID_REQUEST
    assert (
        unavailable.policy_id == "e8330921e97c6b1482ec9aa35b078a0faf00b2ae5af23e5d1d727bc428586238"
    )
    assert (
        unavailable.input_id == "882c53691ad360cf565f4a11bf79418e747dad07abcba8da67c0c536fd6b9099"
    )


def test_ineligible_long_is_zero_weighted_when_another_name_is_active() -> None:
    ineligible = replace(
        _resolution(_AAPL, _HASH_A),
        eligible_for_new_entry=False,
    )
    active = _resolution(_SPY, _HASH_B)

    result = construct_balanced_portfolio(_request((ineligible, active)))

    assert result.house_view is not None
    assert result.house_view.items[0].eligible is False
    assert result.targets[0].target_weight == 0


def test_ineligible_existing_long_cannot_receive_an_upward_adjustment() -> None:
    resolution = replace(
        _resolution(_AAPL, _HASH_A),
        eligible_for_new_entry=False,
        is_position=True,
    )
    result = construct_balanced_portfolio(
        _request((resolution,), inputs=_inputs_with_position(Decimal("0.01")))
    )

    assert result.refusal is None
    assert result.house_view is not None
    assert result.house_view.items[0].eligible is False
    assert result.targets[0].target_weight == Decimal("0.01")
    assert result.target_bands[0].adjustment_weight == Decimal("0.01")


def test_mixed_asset_position_snapshot_fails_closed() -> None:
    crypto = CryptoSpotPosition(
        CryptoSpotInstrumentIdentity("alpaca-paper", "crypto-btc-usd", "BTC", "USD", "ALPACA"),
        Decimal("0.5"),
        PositionValuation(Decimal(35000), "USD", "alpaca-paper-market-value"),
    )
    inputs = _inputs_for((_risk_input(_AAPL, "technology"),), positions=(crypto,))

    result = construct_balanced_portfolio(_request((_resolution(_AAPL, _HASH_A),), inputs=inputs))

    assert result.refusal is PortfolioRefusalReason.CONTRADICTORY_INPUT


@pytest.mark.parametrize(
    "position",
    [
        EquityPosition(
            _AAPL,
            Decimal(-1),
            PositionValuation(Decimal(-100), "USD", "alpaca-paper-market-value"),
        ),
        EquityPosition(
            _AAPL,
            Decimal(0),
            PositionValuation(Decimal(0), "USD", "alpaca-paper-market-value"),
        ),
        EquityPosition(
            _AAPL,
            Decimal("NaN"),
            PositionValuation(Decimal(100), "USD", "alpaca-paper-market-value"),
        ),
        EquityPosition(
            _AAPL,
            Decimal(1),
            PositionValuation(Decimal(100), "AUD", "alpaca-paper-market-value"),
        ),
        EquityPosition(
            _AAPL,
            Decimal(1),
            PositionValuation(Decimal(200), "USD", "alpaca-paper-market-value"),
        ),
    ],
)
def test_signed_non_usd_or_price_inconsistent_position_fails_closed(
    position: EquityPosition,
) -> None:
    inputs = _inputs_for(
        (_risk_input(_AAPL, "technology"),),
        positions=(position,),
    )

    result = construct_balanced_portfolio(_request((_resolution(_AAPL, _HASH_A),), inputs=inputs))

    assert result.refusal is PortfolioRefusalReason.CONTRADICTORY_INPUT


def test_position_quantity_and_valuation_runtime_types_fail_closed() -> None:
    invalid_quantity = EquityPosition(
        _AAPL,
        1,  # type: ignore[arg-type]  # Exercise a hostile runtime value.
        PositionValuation(Decimal(100), "USD", "alpaca-paper-market-value"),
    )
    invalid_valuation = EquityPosition(
        _AAPL,
        Decimal(1),
        "invalid",  # type: ignore[arg-type]  # Exercise a hostile runtime value.
    )
    nonfinite_valuation = PositionValuation(Decimal(100), "USD", "alpaca-paper-market-value")
    object.__setattr__(nonfinite_valuation, "amount", Decimal("NaN"))
    invalid_amount = EquityPosition(_AAPL, Decimal(1), nonfinite_valuation)
    inputs = _inputs_for((_risk_input(_AAPL, "technology"),))

    for position in (invalid_quantity, invalid_valuation, invalid_amount):
        forged_snapshot = replace(inputs.position_snapshot, positions=(position,))
        forged_inputs = replace(inputs, position_snapshot=forged_snapshot)
        result = construct_balanced_portfolio(
            _request(
                (_resolution(_AAPL, _HASH_A),),
                inputs=forged_inputs,
            )
        )

        assert result.refusal is PortfolioRefusalReason.CONTRADICTORY_INPUT


def test_one_share_with_consistent_positive_valuation_is_admissible() -> None:
    position = EquityPosition(
        _AAPL,
        Decimal(1),
        PositionValuation(Decimal(100), "USD", "alpaca-paper-market-value"),
    )

    result = construct_balanced_portfolio(
        _request(
            (replace(_resolution(_AAPL, _HASH_A), is_position=True),),
            inputs=_inputs_for((_risk_input(_AAPL, "technology"),), positions=(position,)),
        )
    )

    assert result.refusal is None


def test_sub_dollar_consistent_positive_valuation_is_admissible() -> None:
    price = Decimal("0.5")
    position = EquityPosition(
        _AAPL,
        Decimal(1),
        PositionValuation(price, "USD", "alpaca-paper-market-value"),
    )
    risk = replace(_risk_input(_AAPL, "technology"), price=price)

    result = construct_balanced_portfolio(
        _request(
            (replace(_resolution(_AAPL, _HASH_A), is_position=True),),
            inputs=_inputs_for((risk,), positions=(position,)),
        )
    )

    assert result.refusal is None


def test_high_precision_position_consistency_is_exact_and_context_independent() -> None:
    quantity = Decimal(12345678901234567890123456789)
    price = Decimal("9.8765432109876543210987654321")
    with localcontext() as context:
        context.prec = 80
        valuation = quantity * price
    position = EquityPosition(
        _AAPL,
        quantity,
        PositionValuation(valuation, "USD", "alpaca-paper-market-value"),
    )
    risk = replace(_risk_input(_AAPL, "technology"), price=price)

    with localcontext() as context:
        context.prec = 8
        result = construct_balanced_portfolio(
            _request(
                (replace(_resolution(_AAPL, _HASH_A), is_position=True),),
                inputs=_inputs_for((risk,), positions=(position,)),
            )
        )

    assert result.refusal is None


def test_abstention_is_never_direction_eligible() -> None:
    resolution = replace(
        _resolution(_AAPL, _HASH_A),
        stance=PortfolioStance.ABSTAIN,
        eligible_for_new_entry=True,
        is_position=True,
    )

    result = construct_balanced_portfolio(
        _request((resolution,), inputs=_inputs_with_position(Decimal("0.05")))
    )

    assert result.house_view is not None
    assert result.house_view.items[0].eligible is False
    assert result.targets[0].target_weight == 0


def test_liquidity_breach_forces_reduction_to_the_liquidity_cap() -> None:
    resolution = replace(
        _resolution(_AAPL, _HASH_A),
        stance=PortfolioStance.REDUCE,
        is_position=True,
    )
    risk = _risk_input(_AAPL, "technology", liquidity=Decimal(100000))
    inputs = _inputs_for(
        (risk,),
        positions=(_position(_AAPL, Decimal("0.06")),),
        cash=Decimal(94000),
    )

    result = construct_balanced_portfolio(_request((resolution,), inputs=inputs))

    assert result.targets[0].target_weight == Decimal("0.01")
    assert result.target_bands[0].trade_reason is PortfolioTradeReason.HARD_RISK_BREACH


def test_exact_name_and_liquidity_caps_are_not_breaches() -> None:
    resolution = replace(
        _resolution(_AAPL, _HASH_A),
        stance=PortfolioStance.REDUCE,
        is_position=True,
    )
    risk = _risk_input(_AAPL, "technology", liquidity=Decimal(800000))
    inputs = _inputs_for(
        (risk,),
        positions=(_position(_AAPL, Decimal("0.08")),),
        cash=Decimal(92000),
    )

    band = construct_balanced_portfolio(_request((resolution,), inputs=inputs)).target_bands[0]

    assert band.target_weight == Decimal("0.04")
    assert band.trade_reason is PortfolioTradeReason.THESIS_REDUCTION


@pytest.mark.parametrize("shared_group", ["sector", "common", "cluster"])
def test_each_group_cap_independently_forces_compliant_reductions(shared_group: str) -> None:
    identities = tuple(_identity(index) for index in range(4))
    risks = tuple(
        _risk_input(
            identity,
            "shared" if shared_group == "sector" else f"sector-{index}",
            common="shared" if shared_group == "common" else f"cause-{index}",
            cluster="shared" if shared_group == "cluster" else f"cluster-{index}",
        )
        for index, identity in enumerate(identities)
    )
    positions = tuple(_position(identity, Decimal("0.07")) for identity in identities)
    resolutions = tuple(
        replace(
            _resolution(identity, _hash(f"request-{index}")),
            stance=PortfolioStance.REDUCE,
            is_position=True,
        )
        for index, identity in enumerate(identities)
    )

    result = construct_balanced_portfolio(
        _request(
            resolutions,
            inputs=_inputs_for(risks, positions=positions, cash=Decimal(72000)),
        )
    )

    assert all(
        band.trade_reason is PortfolioTradeReason.HARD_RISK_BREACH for band in result.target_bands
    )


def test_distinct_groups_are_not_accidentally_combined_for_risk_breaches() -> None:
    identities = tuple(_identity(index) for index in range(4))
    risks = tuple(
        _risk_input(
            identity,
            f"sector-{index}",
            common=f"cause-{index}",
            cluster=f"cluster-{index}",
        )
        for index, identity in enumerate(identities)
    )
    positions = tuple(_position(identity, Decimal("0.07")) for identity in identities)
    resolutions = tuple(
        replace(
            _resolution(identity, _hash(f"request-{index}")),
            stance=PortfolioStance.REDUCE,
            is_position=True,
        )
        for index, identity in enumerate(identities)
    )

    result = construct_balanced_portfolio(
        _request(
            resolutions,
            inputs=_inputs_for(risks, positions=positions, cash=Decimal(72000)),
        )
    )

    assert all(
        band.trade_reason is PortfolioTradeReason.THESIS_REDUCTION for band in result.target_bands
    )


def test_exact_group_cap_is_not_a_hard_risk_breach() -> None:
    identities = tuple(_identity(index) for index in range(5))
    risks = tuple(
        _risk_input(
            identity,
            "shared-sector",
            common=f"cause-{index}",
            cluster=f"cluster-{index}",
        )
        for index, identity in enumerate(identities)
    )
    positions = tuple(_position(identity, Decimal("0.05")) for identity in identities)
    resolutions = tuple(
        replace(
            _resolution(identity, _hash(f"request-{index}")),
            stance=PortfolioStance.REDUCE,
            is_position=True,
        )
        for index, identity in enumerate(identities)
    )

    result = construct_balanced_portfolio(
        _request(
            resolutions,
            inputs=_inputs_for(risks, positions=positions, cash=Decimal(75000)),
        )
    )

    assert all(
        band.trade_reason is PortfolioTradeReason.THESIS_REDUCTION for band in result.target_bands
    )


def test_group_breach_does_not_flag_a_holding_already_equal_to_its_target() -> None:
    identities = tuple(_identity(index) for index in range(4))
    risks = tuple(_risk_input(identity, "shared-sector") for identity in identities)
    current_weights = (Decimal("0.0625"), Decimal("0.07"), Decimal("0.07"), Decimal("0.07"))
    positions = tuple(
        _position(identity, weight)
        for identity, weight in zip(identities, current_weights, strict=True)
    )
    resolutions = tuple(
        replace(
            _resolution(identity, _hash(f"request-{index}")),
            stance=PortfolioStance.HOLD,
            is_position=True,
        )
        for index, identity in enumerate(identities)
    )

    result = construct_balanced_portfolio(
        _request(
            resolutions,
            inputs=_inputs_for(risks, positions=positions, cash=Decimal(72750)),
        )
    )

    assert result.targets[0].target_weight == current_weights[0]
    assert result.target_bands[0].trade_reason is PortfolioTradeReason.IN_BAND
    assert all(
        band.trade_reason is PortfolioTradeReason.HARD_RISK_BREACH
        for band in result.target_bands[1:]
    )


@pytest.mark.parametrize(
    ("position_weight", "cash", "reason"),
    [
        (Decimal("0.08"), Decimal(20000), PortfolioTradeReason.THESIS_REDUCTION),
        (Decimal("0.075"), Decimal(17500), PortfolioTradeReason.HARD_RISK_BREACH),
    ],
)
def test_gross_cap_boundary_and_breach_have_distinct_trade_reasons(
    position_weight: Decimal,
    cash: Decimal,
    reason: PortfolioTradeReason,
) -> None:
    count = 10 if position_weight == Decimal("0.08") else 11
    identities = tuple(_identity(index) for index in range(count))
    risks = tuple(
        _risk_input(
            identity,
            f"sector-{index}",
            common=f"cause-{index}",
            cluster=f"cluster-{index}",
        )
        for index, identity in enumerate(identities)
    )
    positions = tuple(_position(identity, position_weight) for identity in identities)
    resolutions = tuple(
        replace(
            _resolution(identity, _hash(f"request-{index}")),
            stance=PortfolioStance.REDUCE,
            is_position=True,
        )
        for index, identity in enumerate(identities)
    )

    result = construct_balanced_portfolio(
        _request(resolutions, inputs=_inputs_for(risks, positions=positions, cash=cash))
    )

    assert all(band.trade_reason is reason for band in result.target_bands)


def test_gross_breach_does_not_flag_a_holding_already_equal_to_its_target() -> None:
    identities = tuple(_identity(index) for index in range(11))
    risks = tuple(
        _risk_input(
            identity,
            f"sector-{index}",
            common=f"cause-{index}",
            cluster=f"cluster-{index}",
        )
        for index, identity in enumerate(identities)
    )
    target_weight = Decimal("0.07272727272727272727272727270")
    current_weights = (target_weight, *(Decimal("0.075") for _ in range(10)))
    positions = tuple(
        _position(identity, weight)
        for identity, weight in zip(identities, current_weights, strict=True)
    )
    cash = Decimal(100000) - sum(
        (position.valuation.amount for position in positions),
        start=Decimal(0),
    )
    resolutions = tuple(
        replace(
            _resolution(identity, _hash(f"request-{index}")),
            stance=PortfolioStance.HOLD,
            is_position=True,
        )
        for index, identity in enumerate(identities)
    )

    result = construct_balanced_portfolio(
        _request(resolutions, inputs=_inputs_for(risks, positions=positions, cash=cash))
    )

    assert result.targets[0].target_weight == target_weight
    assert result.target_bands[0].trade_reason is PortfolioTradeReason.IN_BAND
    assert all(
        band.trade_reason is PortfolioTradeReason.HARD_RISK_BREACH
        for band in result.target_bands[1:]
    )


def test_gross_cap_applies_after_existing_position_reduction_targets() -> None:
    identities = tuple(_identity(index) for index in range(12))
    risks = tuple(
        _risk_input(
            identity,
            f"sector-{index}",
            common=f"cause-{index}",
            cluster=f"cluster-{index}",
        )
        for index, identity in enumerate(identities)
    )
    position = _position(identities[0], Decimal("0.10"))
    resolutions = tuple(
        replace(
            _resolution(identity, _hash(f"request-{index}")),
            stance=PortfolioStance.REDUCE if index == 0 else PortfolioStance.LONG,
            is_position=index == 0,
        )
        for index, identity in enumerate(identities)
    )

    result = construct_balanced_portfolio(
        _request(
            resolutions,
            inputs=_inputs_for(risks, positions=(position,), cash=Decimal(90000)),
        )
    )

    assert sum((target.target_weight for target in result.targets), Decimal(0)) == Decimal("0.80")
    assert result.cash_weight == Decimal("0.20")


def test_current_group_and_gross_breaches_force_compliant_reductions() -> None:
    identities = tuple(_identity(index) for index in range(9))
    risks = tuple(_risk_input(identity, "shared-sector") for identity in identities)
    positions = tuple(_position(identity, Decimal("0.10")) for identity in identities)
    resolutions = tuple(
        replace(
            _resolution(identity, _hash(f"request-{index}")),
            stance=PortfolioStance.REDUCE,
            is_position=True,
        )
        for index, identity in enumerate(identities)
    )

    result = construct_balanced_portfolio(
        _request(
            resolutions,
            inputs=_inputs_for(risks, positions=positions, cash=Decimal(10000)),
        )
    )

    assert sum((target.target_weight for target in result.targets), Decimal(0)) <= Decimal("0.25")
    assert all(
        band.trade_reason is PortfolioTradeReason.HARD_RISK_BREACH for band in result.target_bands
    )


def test_exit_without_complete_risk_input_fails_closed() -> None:
    resolution = replace(
        _resolution(_AAPL, _HASH_A),
        stance=PortfolioStance.EXIT,
        is_position=True,
    )
    inputs = _inputs_for(
        (),
        positions=(_position(_AAPL, Decimal("0.05")),),
        cash=Decimal(95000),
    )

    result = construct_balanced_portfolio(_request((resolution,), inputs=inputs))

    assert result.refusal is PortfolioRefusalReason.INCOMPLETE_INPUT


def test_non_position_exit_does_not_require_unused_risk_input() -> None:
    resolution = replace(_resolution(_AAPL, _HASH_A), stance=PortfolioStance.EXIT)

    result = construct_balanced_portfolio(
        _request((resolution,), inputs=_inputs_for((_risk_input(_SPY, "broad-market"),)))
    )

    assert result.refusal is None
    assert result.targets[0].target_weight == 0
    assert result.target_bands[0].trade_reason is PortfolioTradeReason.IN_BAND


def test_hostile_result_parser_rejects_every_nested_shape_and_semantic_corruption(  # noqa: PLR0915
) -> None:
    result = construct_balanced_portfolio(
        _request(
            (_resolution(_AAPL, _HASH_A), _resolution(_SPY, _HASH_B)),
        )
    )
    payload = result.to_payload()

    corruptions: list[dict[str, object]] = []
    root_missing = deepcopy(payload)
    del root_missing["record_kind"]
    corruptions.append(root_missing)
    bad_schema = deepcopy(payload)
    bad_schema["schema_version"] = 2
    corruptions.append(bad_schema)
    for refusal in (1, "unknown_refusal"):
        corrupted = deepcopy(payload)
        corrupted["refusal"] = refusal
        corruptions.append(corrupted)
    no_house = deepcopy(payload)
    no_house["house_view"] = None
    corruptions.append(no_house)
    for cash in (1, "x", "NaN"):
        corrupted = deepcopy(payload)
        corrupted["cash_weight"] = cash
        corruptions.append(corrupted)

    house_missing = deepcopy(payload)
    del _house_view_payload(house_missing)["input_id"]
    corruptions.append(house_missing)
    bad_house_schema = deepcopy(payload)
    _house_view_payload(bad_house_schema)["schema_version"] = 2
    corruptions.append(bad_house_schema)
    bad_cutoff = deepcopy(payload)
    _house_view_payload(bad_cutoff)["evidence_cutoff"] = "2026-08-21"
    corruptions.append(bad_cutoff)
    bad_artifacts = deepcopy(payload)
    _house_view_payload(bad_artifacts)["research_artifact_ids"] = [1]
    corruptions.append(bad_artifacts)
    bad_house_hash = deepcopy(payload)
    _house_view_payload(bad_house_hash)["house_view_id"] = "f" * 64
    corruptions.append(bad_house_hash)

    item_missing = deepcopy(payload)
    del _house_items(item_missing)[0]["stance"]
    corruptions.append(item_missing)
    for field, value in (
        ("stance", 1),
        ("stance", "unknown"),
        ("uncertainty", "extreme"),
    ):
        corrupted = deepcopy(payload)
        _house_items(corrupted)[0][field] = value
        corruptions.append(corrupted)
    inconsistent_item = deepcopy(payload)
    _house_items(inconsistent_item)[0]["eligible"] = True
    _house_items(inconsistent_item)[0]["event_blocked"] = True
    corruptions.append(inconsistent_item)

    target_missing = deepcopy(payload)
    _targets(target_missing)[0] = {}
    corruptions.append(target_missing)
    invalid_target = deepcopy(payload)
    _targets(invalid_target)[0]["target_weight"] = "-0.01"
    corruptions.append(invalid_target)
    out_of_range_target = deepcopy(payload)
    _targets(out_of_range_target)[0]["target_weight"] = "2"
    corruptions.append(out_of_range_target)
    invalid_target_identity = deepcopy(payload)
    _targets(invalid_target_identity)[0]["identity"] = {}
    corruptions.append(invalid_target_identity)
    band_missing = deepcopy(payload)
    _bands(band_missing)[0] = {}
    corruptions.append(band_missing)
    for field, value in (
        ("trade_reason", 1),
        ("trade_reason", "unknown"),
        ("trade_eligible", "true"),
        ("lower_weight", "-0.01"),
    ):
        corrupted = deepcopy(payload)
        _bands(corrupted)[0][field] = value
        corruptions.append(corrupted)
    inverted_band = deepcopy(payload)
    _bands(inverted_band)[0]["lower_weight"] = "1"
    _bands(inverted_band)[0]["upper_weight"] = "0"
    corruptions.append(inverted_band)
    reordered = deepcopy(payload)
    _targets(reordered).reverse()
    corruptions.append(reordered)

    assert all(parse_portfolio_construction_result(item) is None for item in corruptions)


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("root", "targets"),
        ("root", "target_bands"),
        ("house", "research_artifact_ids"),
        ("house", "memory_event_ids"),
        ("house", "items"),
    ],
)
def test_hostile_result_parser_rejects_each_non_list_container_without_raising(
    location: str,
    field: str,
) -> None:
    payload = construct_balanced_portfolio(_request((_resolution(_AAPL, _HASH_A),))).to_payload()
    target = payload if location == "root" else _house_view_payload(payload)
    target[field] = 1

    assert parse_portfolio_construction_result(payload) is None


@pytest.mark.parametrize("field", ["policy_id", "input_id", "content_hash"])
def test_hostile_result_parser_rejects_non_string_content_identities(field: str) -> None:
    payload = construct_balanced_portfolio(_request((_resolution(_AAPL, _HASH_A),))).to_payload()
    payload[field] = 1

    assert parse_portfolio_construction_result(payload) is None


def test_hostile_result_parser_rejects_a_non_equity_decision_cycle_without_raising() -> None:
    payload = construct_balanced_portfolio(_request((_resolution(_AAPL, _HASH_A),))).to_payload()
    _house_view_payload(payload)["cycle"] = {
        "asset_class": "crypto_spot",
        "cycle_type": "crypto_decision_window",
        "payload": {
            "starts_at": "2026-08-21T19:00:00.000000+00:00",
            "ends_at": "2026-08-21T20:00:00.000000+00:00",
        },
        "payload_schema_version": 1,
        "schema_version": 1,
    }

    assert parse_portfolio_construction_result(payload) is None


@pytest.mark.parametrize(
    "cash_weight",
    ["", "0.0", "-0", "+1", "1e0", "1" * 65],
)
def test_hostile_result_parser_requires_plain_canonical_decimal_text(cash_weight: str) -> None:
    payload = construct_balanced_portfolio(_request((_resolution(_AAPL, _HASH_A),))).to_payload()
    payload["cash_weight"] = cash_weight

    assert parse_portfolio_construction_result(payload) is None


def test_hostile_result_parser_accepts_the_maximum_canonical_decimal_length() -> None:
    payload = construct_balanced_portfolio(_request((_resolution(_AAPL, _HASH_A),))).to_payload()
    target_text = "0." + "1" * 62
    with localcontext() as context:
        context.prec = 80
        cash_text = f"{Decimal(1) - Decimal(target_text):f}"
        _targets(payload)[0]["target_weight"] = target_text
        band = _bands(payload)[0]
        band["target_weight"] = target_text
        band["lower_weight"] = "0"
        band["upper_weight"] = target_text
        band["current_weight"] = "0"
        band["adjustment_weight"] = target_text
        payload["cash_weight"] = cash_text
        material = {key: value for key, value in payload.items() if key != "content_hash"}
        payload["content_hash"] = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        parsed = parse_portfolio_construction_result(payload)

    assert len(target_text) == _MAX_DECIMAL_TEXT_LENGTH
    assert parsed is not None
    assert parsed.targets[0].target_weight == Decimal(target_text)


def test_policy_parser_rejects_malformed_nested_and_decimal_material() -> None:
    malformed_volatility = {**_policy().to_payload(), "realized_volatility": {}}
    malformed_decimal = {**_policy().to_payload(), "maximum_gross_weight": 0.8}
    noncanonical_decimal = {
        **_policy().to_payload(),
        "minimum_executable_notional": "100.0",
    }
    nonfinite_decimal = {
        **_policy().to_payload(),
        "minimum_executable_notional": "NaN",
    }

    assert BalancedPortfolioPolicy.parse(malformed_volatility) is None
    assert BalancedPortfolioPolicy.parse(malformed_decimal) is None
    assert BalancedPortfolioPolicy.parse(noncanonical_decimal) is None
    assert BalancedPortfolioPolicy.parse(nonfinite_decimal) is None


def test_decimal_payloads_canonicalize_negative_zero() -> None:
    policy = replace(_policy(), target_band_width=Decimal("-0"))

    assert policy.to_payload()["target_band_width"] == "0"


def test_decimal_payloads_expand_exponents_and_trim_fractional_zeroes() -> None:
    exponent_payload = replace(
        _policy(),
        minimum_executable_notional=Decimal("1E+1"),
    ).to_payload()
    integral_payload = replace(
        _policy(),
        minimum_executable_notional=Decimal("1.200"),
        target_band_width=Decimal("0.0100"),
    ).to_payload()
    whole_payload = replace(
        _policy(),
        minimum_executable_notional=Decimal("1.000"),
    ).to_payload()

    assert exponent_payload["minimum_executable_notional"] == "10"
    assert integral_payload["minimum_executable_notional"] == "1.2"
    assert integral_payload["target_band_width"] == "0.01"
    assert whole_payload["minimum_executable_notional"] == "1"


def _request(
    resolutions: tuple[HouseViewResolution, ...],
    *,
    inputs: PortfolioInputSet | None = None,
    policy: BalancedPortfolioPolicy | None = None,
    expected: tuple[str, ...] | None = None,
    event_evidence: tuple[MaterialEventEvidence, ...] = (),
) -> PortfolioConstructionRequest:
    request_ids = tuple(sorted(item.request_id for item in resolutions))
    return PortfolioConstructionRequest(
        run_id="1" * 64,
        cycle=MarketSession(date.fromisoformat("2026-08-21")),
        evidence_cutoff=_CUTOFF,
        data_regime="alpaca-basic-iex-v1",
        configuration_hash="2" * 64,
        constitution_version=1,
        constitution_hash="3" * 64,
        research_policy_hash="4" * 64,
        research_artifact_ids=tuple(sorted(item.resolution_id for item in resolutions)),
        memory_event_ids=("5" * 64,),
        universe_snapshot_id="7" * 64,
        expected_research_request_ids=request_ids if expected is None else expected,
        resolutions=resolutions,
        inputs=_inputs() if inputs is None else inputs,
        policy=_policy() if policy is None else policy,
        material_event_evidence=event_evidence,
    )


def _release_evidence() -> MaterialEventEvidence:
    return MaterialEventEvidence(
        _HASH_A,
        "issuer_release",
        "issuer-release-1",
        UtcInstant.from_datetime(_CUTOFF.value - timedelta(hours=1)),
        _CUTOFF,
        (_AAPL,),
    )


def _resolution(identity: EquityInstrumentIdentity, resolution_id: str) -> HouseViewResolution:
    return HouseViewResolution(
        identity=identity,
        request_id=resolution_id,
        resolution_id=resolution_id,
        stance=PortfolioStance.LONG,
        uncertainty="low",
        production_authority=True,
    )


def _inputs() -> PortfolioInputSet:
    return _inputs_for(
        (
            _risk_input(_SPY, "technology"),
            _risk_input(_AAPL, "technology"),
        )
    )


def _inputs_for(
    risks: tuple[PortfolioRiskInput, ...],
    *,
    positions: tuple[EquityPosition | CryptoSpotPosition, ...] = (),
    cash: Decimal = Decimal(100000),
    observed_at: UtcInstant | None = None,
    available_at: UtcInstant | None = None,
) -> PortfolioInputSet:
    observed = (
        UtcInstant.from_datetime(_CUTOFF.value - timedelta(minutes=30))
        if observed_at is None
        else observed_at
    )
    available = (
        UtcInstant.from_datetime(_CUTOFF.value - timedelta(minutes=20))
        if available_at is None
        else available_at
    )
    position_snapshot = PositionSnapshot.create(
        observed_at=observed.value,
        available_at=available.value,
        data_regime="alpaca-basic-iex-v1",
        source_fingerprint="8" * 64,
        positions=positions,
    )
    assert isinstance(position_snapshot, PositionSnapshot)
    return PortfolioInputSet.create(
        position_snapshot=position_snapshot,
        cash=cash,
        cash_currency="USD",
        source_identity="recorded-portfolio-source-v1",
        observed_at=observed,
        available_at=available,
        data_regime="alpaca-basic-iex-v1",
        risk_inputs=risks,
    )


def _inputs_with_position(weight: Decimal) -> PortfolioInputSet:
    observed_at = UtcInstant.from_datetime(_CUTOFF.value - timedelta(minutes=30))
    available_at = UtcInstant.from_datetime(_CUTOFF.value - timedelta(minutes=20))
    total = Decimal(100000)
    amount = total * weight
    position_snapshot = PositionSnapshot.create(
        observed_at=observed_at.value,
        available_at=available_at.value,
        data_regime="alpaca-basic-iex-v1",
        source_fingerprint="8" * 64,
        positions=(
            EquityPosition(
                _AAPL,
                amount / Decimal(100),
                PositionValuation(amount, "USD", "alpaca-paper-market-value"),
            ),
        ),
    )
    assert isinstance(position_snapshot, PositionSnapshot)
    return PortfolioInputSet.create(
        position_snapshot=position_snapshot,
        cash=total - amount,
        cash_currency="USD",
        source_identity="recorded-portfolio-source-v1",
        observed_at=observed_at,
        available_at=available_at,
        data_regime="alpaca-basic-iex-v1",
        risk_inputs=(_risk_input(_AAPL, "technology"),),
    )


def _position(identity: EquityInstrumentIdentity, weight: Decimal) -> EquityPosition:
    amount = Decimal(100000) * weight
    return EquityPosition(
        identity,
        amount / Decimal(100),
        PositionValuation(amount, "USD", "alpaca-paper-market-value"),
    )


def _house_view_payload(payload: dict[str, object]) -> dict[str, object]:
    return mutable_mapping(payload["house_view"])


def _house_items(payload: dict[str, object]) -> list[dict[str, object]]:
    return mutable_mapping_list(_house_view_payload(payload)["items"])


def _targets(payload: dict[str, object]) -> list[dict[str, object]]:
    return mutable_mapping_list(payload["targets"])


def _bands(payload: dict[str, object]) -> list[dict[str, object]]:
    return mutable_mapping_list(payload["target_bands"])


def _risk_input(  # noqa: PLR0913 - make each group and liquidity fact explicit.
    identity: EquityInstrumentIdentity,
    sector: str,
    *,
    common: str = "mega-cap",
    cluster: str = "market-beta",
    liquidity: Decimal = Decimal(100000000),
    amplitude: int = 1,
) -> PortfolioRiskInput:
    return PortfolioRiskInput(
        identity=identity,
        price=Decimal(100),
        price_unit="usd_per_share",
        adjusted_closes=(
            _close(_days_before_cutoff(3), Decimal(100)),
            _close(_days_before_cutoff(2), Decimal(100 + amplitude)),
            _close(_days_before_cutoff(1), Decimal(100)),
        ),
        sector=sector,
        median_dollar_volume=liquidity,
        liquidity_unit="usd_per_day",
        common_cause_group=common,
        correlation_cluster=cluster,
        material_events=(),
    )


def _days_before_cutoff(days: int) -> UtcInstant:
    return UtcInstant.from_datetime(_CUTOFF.value - timedelta(days=days))


def _close(
    observed_at: UtcInstant,
    price: Decimal,
    *,
    available_at: UtcInstant | None = None,
) -> AdjustedClose:
    return AdjustedClose(
        MarketSession(observed_at.value.date()),
        observed_at,
        observed_at if available_at is None else available_at,
        "alpaca-iex-adjusted-daily-v1",
        price,
    )


def _identity(index: int) -> EquityInstrumentIdentity:
    return EquityInstrumentIdentity("alpaca-paper", f"equity-{index:02d}", "NASDAQ")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _test_content_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _reseal_shadow_payload(payload: dict[str, object]) -> None:
    material = {key: value for key, value in payload.items() if key != "content_hash"}
    payload["content_hash"] = _test_content_hash(material)


def _shadow_with_substituted_target(
    account: PortfolioShadowAccount,
    target_weight: Decimal,
) -> PortfolioShadowAccount:
    payload = deepcopy(account.to_payload())
    target = mutable_mapping(mutable_mapping_list(payload["targets"])[0])
    band = mutable_mapping(mutable_mapping_list(payload["target_bands"])[0])
    previous = Decimal(str(target["target_weight"]))
    target["target_weight"] = str(target_weight)
    band.update(
        {
            "target_weight": str(target_weight),
            "lower_weight": str(target_weight),
            "upper_weight": str(target_weight),
        }
    )
    payload["retained_cash_weight"] = str(
        Decimal(str(payload["retained_cash_weight"])) - (target_weight - previous)
    )
    _reseal_shadow_payload(payload)
    parsed = parse_portfolio_shadow_account(payload)
    assert parsed is not None
    return parsed


def _replace_all_shadow_targets(
    payload: dict[str, object],
    target_weight: Decimal,
    retained_cash_weight: Decimal,
) -> None:
    for target in mutable_mapping_list(payload["targets"]):
        mutable_mapping(target)["target_weight"] = str(target_weight)
    for band in mutable_mapping_list(payload["target_bands"]):
        mutable_mapping(band).update(
            {
                "target_weight": str(target_weight),
                "lower_weight": str(target_weight),
                "upper_weight": str(target_weight),
            }
        )
    payload["retained_cash_weight"] = format(retained_cash_weight.normalize(), "f")


def _policy() -> BalancedPortfolioPolicy:
    return BalancedPortfolioPolicy(
        schema_version=2,
        estimator="sample_standard_deviation",
        lookback_days=2,
        annualization_periods=252,
        volatility_floor=Decimal("0.10"),
        price_adjustment="split_adjusted_close",
        maximum_input_age_seconds=7200,
        maximum_gross_weight=Decimal("0.80"),
        maximum_name_weight=Decimal("0.08"),
        maximum_sector_weight=Decimal("0.25"),
        maximum_common_cause_weight=Decimal("0.25"),
        maximum_correlation_cluster_weight=Decimal("0.25"),
        maximum_fraction_of_median_dollar_volume=Decimal("0.01"),
        target_band_width=Decimal("0.01"),
        minimum_executable_notional=Decimal(100),
        partial_adjustment_fraction=Decimal("0.50"),
        reduce_multiplier=Decimal("0.50"),
        uncertainty_multipliers=(
            ("low", Decimal(1)),
            ("medium", Decimal("0.75")),
            ("high", Decimal("0.50")),
        ),
        shadow_policies=(
            ShadowPortfolioPolicy(
                PortfolioShadowKind.CONSERVATIVE,
                PortfolioSizingMethod.INVERSE_VOLATILITY,
                1,
                Decimal("0.60"),
                Decimal("0.05"),
                Decimal("0.20"),
            ),
            ShadowPortfolioPolicy(
                PortfolioShadowKind.GROWTH,
                PortfolioSizingMethod.INVERSE_VOLATILITY,
                1,
                Decimal("1.00"),
                Decimal("0.12"),
                Decimal("0.30"),
            ),
            ShadowPortfolioPolicy(
                PortfolioShadowKind.EQUAL_WEIGHT,
                PortfolioSizingMethod.EQUAL_WEIGHT,
                1,
                Decimal("0.80"),
                Decimal("0.08"),
                Decimal("0.25"),
            ),
        ),
        cost_input_policy=PortfolioCostInputPolicy(
            1,
            "frozen_ex_ante_turnover_inputs",
            "absolute_adjustment_weight",
            "available_at_time",
        ),
    )
