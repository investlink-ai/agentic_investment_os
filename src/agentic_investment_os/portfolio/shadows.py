"""Account for required same-input portfolio shadows without executable authority."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from typing import Protocol

from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    MarketSession,
    canonical_instrument_bytes,
    parse_decision_cycle_identity,
    parse_instrument_identity,
)
from agentic_investment_os.domain.lifecycle import (
    PortfolioCheckpoint,
    PortfolioCheckpointReference,
    PortfolioShadowKind,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.domain.universe import EquityPosition
from agentic_investment_os.portfolio.construction import (
    _PORTFOLIO_DECIMAL_CONTEXT,
    HouseView,
    PortfolioConstructionRequest,
    PortfolioConstructionResult,
    PortfolioSizingMethod,
    PortfolioStance,
    PortfolioTarget,
    PortfolioTradeReason,
    ShadowPortfolioPolicy,
    TargetBand,
    _construct_shadow_variant,
    _content_hash,
    _decimal_text,
    _exact_mapping,
    _is_hash,
    _plain_decimal,
    _sum_decimals,
    construct_balanced_portfolio,
)

__all__ = (
    "PortfolioCycleHistoryValidator",
    "PortfolioCycleResult",
    "PortfolioCycleResultLedger",
    "PortfolioShadowAccount",
    "ShadowCostInput",
    "ShadowTargetBand",
    "construct_portfolio_cycle",
    "parse_portfolio_shadow_account",
)

_INVALID_SHADOW = "invalid portfolio shadow account"
_INVALID_CYCLE = "invalid portfolio cycle result"
_SHADOW_ENVELOPES = {
    PortfolioShadowKind.CONSERVATIVE: (
        PortfolioSizingMethod.INVERSE_VOLATILITY,
        Decimal("0.60"),
        Decimal("0.05"),
        Decimal("0.20"),
    ),
    PortfolioShadowKind.GROWTH: (
        PortfolioSizingMethod.INVERSE_VOLATILITY,
        Decimal("1.00"),
        Decimal("0.12"),
        Decimal("0.30"),
    ),
    PortfolioShadowKind.EQUAL_WEIGHT: (
        PortfolioSizingMethod.EQUAL_WEIGHT,
        Decimal("0.80"),
        Decimal("0.08"),
        Decimal("0.25"),
    ),
}
_MAXIMUM_COMMON_CAUSE_WEIGHT = Decimal("0.25")
_MAXIMUM_CORRELATION_CLUSTER_WEIGHT = Decimal("0.25")
_MAXIMUM_FRACTION_OF_MEDIAN_DOLLAR_VOLUME = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class ShadowTargetBand:
    """Record a hypothetical adjustment without granting trade eligibility."""

    identity: EquityInstrumentIdentity
    target_weight: Decimal
    lower_weight: Decimal
    upper_weight: Decimal
    current_weight: Decimal
    adjustment_weight: Decimal
    accounting_reason: PortfolioTradeReason

    def __post_init__(self) -> None:
        weights = (
            self.target_weight,
            self.lower_weight,
            self.upper_weight,
            self.current_weight,
            self.adjustment_weight,
        )
        if (
            type(self.identity) is not EquityInstrumentIdentity
            or any(
                type(item) is not Decimal or not item.is_finite() or item < 0 for item in weights
            )
            or not self.lower_weight <= self.target_weight <= self.upper_weight <= Decimal(1)
            or type(self.accounting_reason) is not PortfolioTradeReason
        ):
            raise ValueError(_INVALID_SHADOW)


@dataclass(frozen=True, slots=True)
class ShadowCostInput:
    """Freeze one available-at-time price and hypothetical adjustment for later costing."""

    identity: EquityInstrumentIdentity
    price: Decimal
    median_dollar_volume: Decimal
    sector: str
    common_cause_group: str
    correlation_cluster: str
    current_weight: Decimal
    adjustment_weight: Decimal

    def __post_init__(self) -> None:
        if (
            type(self.identity) is not EquityInstrumentIdentity
            or type(self.price) is not Decimal
            or not self.price.is_finite()
            or self.price <= 0
            or type(self.median_dollar_volume) is not Decimal
            or not self.median_dollar_volume.is_finite()
            or self.median_dollar_volume <= 0
            or any(
                type(item) is not str or not item
                for item in (self.sector, self.common_cause_group, self.correlation_cluster)
            )
            or any(
                type(item) is not Decimal or not item.is_finite() or item < 0
                for item in (self.current_weight, self.adjustment_weight)
            )
        ):
            raise ValueError(_INVALID_SHADOW)


@dataclass(frozen=True, slots=True)
class PortfolioShadowAccount:
    """Preserve one immutable ex-ante accounting variant with no packet authority."""

    schema_version: int
    run_id: str
    cycle: MarketSession
    account_kind: PortfolioShadowKind
    sizing_method: PortfolioSizingMethod
    algorithm_version: int
    house_view_id: str
    policy_id: str
    input_id: str
    evidence_cutoff: UtcInstant
    position_snapshot_id: str
    starting_cash: Decimal
    starting_equity: Decimal
    cash_currency: str
    targets: tuple[PortfolioTarget, ...]
    target_bands: tuple[ShadowTargetBand, ...]
    retained_cash_weight: Decimal
    modeled_turnover_weight: Decimal
    modeled_turnover_notional: Decimal
    cost_input_policy_id: str
    cost_input_source: str
    cost_inputs_available_at: UtcInstant
    cost_inputs: tuple[ShadowCostInput, ...]
    material_fingerprints: tuple[tuple[str, str], ...]
    content_hash: str

    def __post_init__(self) -> None:
        target_keys = tuple(canonical_instrument_bytes(item.identity) for item in self.targets)
        band_keys = tuple(canonical_instrument_bytes(item.identity) for item in self.target_bands)
        cost_keys = tuple(canonical_instrument_bytes(item.identity) for item in self.cost_inputs)
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or not _is_hash(self.run_id)
            or type(self.cycle) is not MarketSession
            or type(self.account_kind) is not PortfolioShadowKind
            or type(self.sizing_method) is not PortfolioSizingMethod
            or type(self.algorithm_version) is not int
            or self.algorithm_version != 1
            or not all(
                _is_hash(item)
                for item in (
                    self.house_view_id,
                    self.policy_id,
                    self.input_id,
                    self.position_snapshot_id,
                    self.cost_input_policy_id,
                )
            )
            or type(self.evidence_cutoff) is not UtcInstant
            or type(self.starting_cash) is not Decimal
            or not self.starting_cash.is_finite()
            or self.starting_cash < 0
            or type(self.starting_equity) is not Decimal
            or not self.starting_equity.is_finite()
            or self.starting_equity <= 0
            or self.starting_cash > self.starting_equity
            or self.cash_currency != "USD"
            or type(self.targets) is not tuple
            or any(type(item) is not PortfolioTarget for item in self.targets)
            or type(self.target_bands) is not tuple
            or any(type(item) is not ShadowTargetBand for item in self.target_bands)
            or type(self.cost_inputs) is not tuple
            or any(type(item) is not ShadowCostInput for item in self.cost_inputs)
            or target_keys != tuple(sorted(set(target_keys)))
            or band_keys != target_keys
            or cost_keys != target_keys
            or any(
                target.target_weight != band.target_weight
                for target, band in zip(self.targets, self.target_bands, strict=True)
            )
            or any(
                (band.current_weight, band.adjustment_weight)
                != (cost.current_weight, cost.adjustment_weight)
                for band, cost in zip(self.target_bands, self.cost_inputs, strict=True)
            )
            or type(self.retained_cash_weight) is not Decimal
            or not self.retained_cash_weight.is_finite()
            or not Decimal(0) <= self.retained_cash_weight <= Decimal(1)
            or sum(item.target_weight for item in self.targets) + self.retained_cash_weight != 1
            or type(self.modeled_turnover_weight) is not Decimal
            or type(self.modeled_turnover_notional) is not Decimal
            or not self.modeled_turnover_weight.is_finite()
            or not self.modeled_turnover_notional.is_finite()
            or self.modeled_turnover_weight < 0
            or self.modeled_turnover_notional < 0
            or self.modeled_turnover_weight
            != sum(abs(item.adjustment_weight - item.current_weight) for item in self.cost_inputs)
            or self.modeled_turnover_notional != self.modeled_turnover_weight * self.starting_equity
            or type(self.cost_input_source) is not str
            or not self.cost_input_source
            or type(self.cost_inputs_available_at) is not UtcInstant
            or self.cost_inputs_available_at.value > self.evidence_cutoff.value
            or type(self.material_fingerprints) is not tuple
            or tuple(key for key, _ in self.material_fingerprints)
            != (
                "configuration",
                "constitution",
                "research_policy",
                "universe_snapshot",
                "portfolio_policy",
                "portfolio_input",
                "house_view",
            )
            or any(not _is_hash(value) for _, value in self.material_fingerprints)
            or dict(self.material_fingerprints)["portfolio_policy"] != self.policy_id
            or dict(self.material_fingerprints)["portfolio_input"] != self.input_id
            or dict(self.material_fingerprints)["house_view"] != self.house_view_id
            or not _shadow_constraints_are_valid(self)
            or (self.content_hash != "" and not _is_hash(self.content_hash))
        ):
            raise ValueError(_INVALID_SHADOW)

    @property
    def account_id(self) -> str:
        """Return the exact immutable shadow-account identity."""
        return self.content_hash

    def to_payload(self) -> dict[str, object]:
        material = _shadow_material(self)
        if self.content_hash != _content_hash(material):
            raise ValueError(_INVALID_SHADOW)
        return {**material, "content_hash": self.content_hash}


def _shadow_constraints_are_valid(account: PortfolioShadowAccount) -> bool:
    sizing_method, maximum_gross, maximum_name, maximum_sector = _SHADOW_ENVELOPES[
        account.account_kind
    ]
    if (
        account.sizing_method is not sizing_method
        or sum(item.target_weight for item in account.targets) > maximum_gross
        or any(
            max(target.target_weight, account.target_bands[index].upper_weight) > maximum_name
            for index, target in enumerate(account.targets)
        )
    ):
        return False
    for index, target in enumerate(account.targets):
        cost = account.cost_inputs[index]
        liquidity_cap = (
            cost.median_dollar_volume
            * _MAXIMUM_FRACTION_OF_MEDIAN_DOLLAR_VOLUME
            / account.starting_equity
        )
        if target.target_weight > liquidity_cap:
            return False
    return all(
        _group_targets_within(account, attribute, cap)
        for attribute, cap in (
            ("sector", maximum_sector),
            ("common_cause_group", _MAXIMUM_COMMON_CAUSE_WEIGHT),
            ("correlation_cluster", _MAXIMUM_CORRELATION_CLUSTER_WEIGHT),
        )
    )


def _group_targets_within(
    account: PortfolioShadowAccount,
    attribute: str,
    cap: Decimal,
) -> bool:
    totals: dict[str, Decimal] = {}
    for index, target in enumerate(account.targets):
        cost = account.cost_inputs[index]
        group = str(getattr(cost, attribute))
        totals[group] = totals.get(group, Decimal(0)) + target.target_weight
    return all(total <= cap for total in totals.values())


def _house_view_fingerprints(house_view: HouseView) -> tuple[tuple[str, str], ...]:
    return (
        ("configuration", house_view.configuration_hash),
        ("constitution", house_view.constitution_hash),
        ("research_policy", house_view.research_policy_hash),
        ("universe_snapshot", house_view.universe_snapshot_id),
        ("portfolio_policy", house_view.policy_id),
        ("portfolio_input", house_view.input_id),
        ("house_view", house_view.house_view_id),
    )


def _targets_match_house_view(account: PortfolioShadowAccount, house_view: HouseView) -> bool:
    for index, view_item in enumerate(house_view.items):
        target = account.targets[index]
        cost = account.cost_inputs[index]
        if (
            view_item.event_blocked
            or view_item.stance in (PortfolioStance.EXIT, PortfolioStance.ABSTAIN)
        ) and target.target_weight != 0:
            return False
        if not view_item.eligible and target.target_weight > cost.current_weight:
            return False
    return True


def _shadows_share_input_material(shadows: tuple[PortfolioShadowAccount, ...]) -> bool:
    if not shadows:
        return True
    expected = shadows[0]
    expected_costs = tuple(_shared_cost_input(item) for item in expected.cost_inputs)
    return all(
        (
            item.position_snapshot_id,
            item.starting_cash,
            item.starting_equity,
            item.cash_currency,
            item.cost_input_policy_id,
            item.cost_input_source,
            item.cost_inputs_available_at,
            item.material_fingerprints,
            tuple(_shared_cost_input(cost) for cost in item.cost_inputs),
        )
        == (
            expected.position_snapshot_id,
            expected.starting_cash,
            expected.starting_equity,
            expected.cash_currency,
            expected.cost_input_policy_id,
            expected.cost_input_source,
            expected.cost_inputs_available_at,
            expected.material_fingerprints,
            expected_costs,
        )
        for item in shadows[1:]
    )


def _shared_cost_input(item: ShadowCostInput) -> tuple[object, ...]:
    return (
        item.identity,
        item.price,
        item.median_dollar_volume,
        item.sector,
        item.common_cause_group,
        item.correlation_cluster,
        item.current_weight,
    )


@dataclass(frozen=True, slots=True)
class PortfolioCycleResult:
    """Carry Balanced and every required non-executable shadow as one lifecycle result."""

    balanced: PortfolioConstructionResult
    shadows: tuple[PortfolioShadowAccount, ...]

    def __post_init__(self) -> None:
        house_view = self.balanced.house_view
        if (
            type(self.balanced) is not PortfolioConstructionResult
            or type(self.shadows) is not tuple
            or any(type(item) is not PortfolioShadowAccount for item in self.shadows)
            or (
                self.balanced.refusal is None
                and tuple(item.account_kind for item in self.shadows)
                != (
                    PortfolioShadowKind.CONSERVATIVE,
                    PortfolioShadowKind.GROWTH,
                    PortfolioShadowKind.EQUAL_WEIGHT,
                )
            )
            or (self.balanced.refusal is not None and self.shadows)
            or any(
                house_view is None
                or item.run_id != house_view.run_id
                or item.cycle != house_view.cycle
                or item.evidence_cutoff != house_view.evidence_cutoff
                or item.house_view_id != house_view.house_view_id
                or item.policy_id != self.balanced.policy_id
                or item.input_id != self.balanced.input_id
                or item.material_fingerprints != _house_view_fingerprints(house_view)
                or tuple(target.identity for target in item.targets)
                != tuple(view_item.identity for view_item in house_view.items)
                or not _targets_match_house_view(item, house_view)
                for item in self.shadows
            )
            or not _shadows_share_input_material(self.shadows)
        ):
            raise ValueError(_INVALID_CYCLE)


class PortfolioCycleResultLedger(Protocol):
    """Append or replay one exact Balanced-plus-shadow accounting cycle."""

    def record_cycle(
        self,
        run_id: str,
        result: PortfolioCycleResult,
        recorded_at: UtcInstant,
    ) -> PortfolioCheckpoint: ...

    def validate_history(self, references: tuple[PortfolioCheckpointReference, ...]) -> None: ...


class PortfolioCycleHistoryValidator(Protocol):
    """Validate durable Balanced and shadow artifacts named by lifecycle history."""

    def validate_history(self, references: tuple[PortfolioCheckpointReference, ...]) -> None: ...


def construct_portfolio_cycle(request: PortfolioConstructionRequest) -> PortfolioCycleResult:
    """Construct Balanced and all required same-input accounting variants."""
    with localcontext(_PORTFOLIO_DECIMAL_CONTEXT):
        return _construct_portfolio_cycle(request)


def _construct_portfolio_cycle(request: PortfolioConstructionRequest) -> PortfolioCycleResult:
    balanced = construct_balanced_portfolio(request)
    if balanced.refusal is not None:
        return PortfolioCycleResult(balanced, ())
    house_view = balanced.require_house_view()
    shadows = tuple(
        _shadow_account(request, house_view, policy) for policy in request.policy.shadow_policies
    )
    return PortfolioCycleResult(balanced, shadows)


def _shadow_account(
    request: PortfolioConstructionRequest,
    house_view: HouseView,
    policy: ShadowPortfolioPolicy,
) -> PortfolioShadowAccount:
    variant = _construct_shadow_variant(request, house_view, policy)
    bands = tuple(_shadow_band(item) for item in variant.target_bands)
    risks = {canonical_instrument_bytes(item.identity): item for item in request.inputs.risk_inputs}
    cost_inputs = tuple(
        ShadowCostInput(
            band.identity,
            risks[canonical_instrument_bytes(band.identity)].price,
            risks[canonical_instrument_bytes(band.identity)].median_dollar_volume,
            risks[canonical_instrument_bytes(band.identity)].sector,
            risks[canonical_instrument_bytes(band.identity)].common_cause_group,
            risks[canonical_instrument_bytes(band.identity)].correlation_cluster,
            band.current_weight,
            band.adjustment_weight,
        )
        for band in bands
    )
    equity = request.inputs.cash + sum(
        position.valuation.amount
        for position in request.inputs.position_snapshot.positions
        if type(position) is EquityPosition
    )
    turnover_weight = _sum_decimals(
        abs(item.adjustment_weight - item.current_weight) for item in cost_inputs
    )
    provisional = PortfolioShadowAccount(
        1,
        request.run_id,
        request.cycle,
        policy.account_kind,
        policy.sizing_method,
        policy.algorithm_version,
        house_view.house_view_id,
        request.policy.policy_id,
        request.inputs.input_id,
        request.evidence_cutoff,
        request.inputs.position_snapshot.fingerprint,
        request.inputs.cash,
        equity,
        request.inputs.cash_currency,
        variant.targets,
        bands,
        variant.cash_weight,
        turnover_weight,
        turnover_weight * equity,
        request.policy.cost_input_policy.policy_id,
        request.inputs.source_identity,
        request.inputs.available_at,
        cost_inputs,
        (
            ("configuration", request.configuration_hash),
            ("constitution", request.constitution_hash),
            ("research_policy", request.research_policy_hash),
            ("universe_snapshot", request.universe_snapshot_id),
            ("portfolio_policy", request.policy.policy_id),
            ("portfolio_input", request.inputs.input_id),
            ("house_view", house_view.house_view_id),
        ),
        "",
    )
    return replace(provisional, content_hash=_content_hash(_shadow_material(provisional)))


def parse_portfolio_shadow_account(  # noqa: PLR0911 - refuse each hostile schema layer.
    value: object,
) -> PortfolioShadowAccount | None:
    """Validate one hostile durable shadow-account representation."""
    root = _exact_mapping(
        value,
        {
            "schema_version",
            "record_kind",
            "authority_scope",
            "run_id",
            "cycle",
            "account_kind",
            "sizing_method",
            "algorithm_version",
            "house_view_id",
            "policy_id",
            "input_id",
            "evidence_cutoff",
            "position_snapshot_id",
            "starting_cash",
            "starting_equity",
            "cash_currency",
            "targets",
            "target_bands",
            "retained_cash_weight",
            "modeled_turnover_weight",
            "modeled_turnover_notional",
            "cost_input_policy_id",
            "cost_input_source",
            "cost_inputs_available_at",
            "cost_inputs",
            "material_fingerprints",
            "content_hash",
        },
    )
    if root is None:
        return None
    if root["schema_version"] != 1:
        return None
    if root["record_kind"] != "portfolio_shadow_account":
        return None
    if root["authority_scope"] != "non_executable_shadow_account":
        return None
    match (
        root["targets"],
        root["target_bands"],
        root["cost_inputs"],
        root["material_fingerprints"],
    ):
        case (
            list() as target_values,
            list() as band_values,
            list() as cost_input_values,
            dict() as fingerprint_values,
        ):
            pass
        case _:
            return None
    cycle = parse_decision_cycle_identity(root["cycle"])
    targets = tuple(_parse_target(item) for item in target_values)
    bands = tuple(_parse_shadow_band(item) for item in band_values)
    cost_inputs = tuple(_parse_cost_input(item) for item in cost_input_values)
    match (
        cycle,
        root["schema_version"],
        root["run_id"],
        root["account_kind"],
        root["sizing_method"],
        root["algorithm_version"],
        root["house_view_id"],
        root["policy_id"],
        root["input_id"],
        root["position_snapshot_id"],
        root["cash_currency"],
        root["cost_input_policy_id"],
        root["cost_input_source"],
        root["content_hash"],
        _parse_material_fingerprints(fingerprint_values),
        _plain_decimal(root["starting_cash"]),
        _plain_decimal(root["starting_equity"]),
        _plain_decimal(root["retained_cash_weight"]),
        _plain_decimal(root["modeled_turnover_weight"]),
        _plain_decimal(root["modeled_turnover_notional"]),
    ):
        case (
            MarketSession() as parsed_cycle,
            int() as schema_version,
            str() as run_id,
            str() as account_kind,
            str() as sizing_method,
            int() as algorithm_version,
            str() as house_view_id,
            str() as policy_id,
            str() as input_id,
            str() as position_snapshot_id,
            str() as cash_currency,
            str() as cost_input_policy_id,
            str() as cost_input_source,
            str() as content_hash,
            tuple() as fingerprints,
            Decimal() as starting_cash,
            Decimal() as starting_equity,
            Decimal() as retained_cash_weight,
            Decimal() as modeled_turnover_weight,
            Decimal() as modeled_turnover_notional,
        ):
            pass
        case _:
            return None
    try:
        account = PortfolioShadowAccount(
            schema_version=schema_version,
            run_id=run_id,
            cycle=parsed_cycle,
            account_kind=PortfolioShadowKind(account_kind),
            sizing_method=PortfolioSizingMethod(sizing_method),
            algorithm_version=algorithm_version,
            house_view_id=house_view_id,
            policy_id=policy_id,
            input_id=input_id,
            evidence_cutoff=UtcInstant.parse(root["evidence_cutoff"]),
            position_snapshot_id=position_snapshot_id,
            starting_cash=starting_cash,
            starting_equity=starting_equity,
            cash_currency=cash_currency,
            targets=tuple(item for item in targets if item is not None),
            target_bands=tuple(item for item in bands if item is not None),
            retained_cash_weight=retained_cash_weight,
            modeled_turnover_weight=modeled_turnover_weight,
            modeled_turnover_notional=modeled_turnover_notional,
            cost_input_policy_id=cost_input_policy_id,
            cost_input_source=cost_input_source,
            cost_inputs_available_at=UtcInstant.parse(root["cost_inputs_available_at"]),
            cost_inputs=tuple(item for item in cost_inputs if item is not None),
            material_fingerprints=fingerprints,
            content_hash=content_hash,
        )
        return account if account.to_payload() == root else None
    except (InvalidUtcInstantError, TypeError, ValueError):
        return None


def _shadow_band(band: TargetBand) -> ShadowTargetBand:
    return ShadowTargetBand(
        band.identity,
        band.target_weight,
        band.lower_weight,
        band.upper_weight,
        band.current_weight,
        band.adjustment_weight,
        band.trade_reason,
    )


def _shadow_material(account: PortfolioShadowAccount) -> dict[str, object]:
    return {
        "schema_version": account.schema_version,
        "record_kind": "portfolio_shadow_account",
        "authority_scope": "non_executable_shadow_account",
        "run_id": account.run_id,
        "cycle": account.cycle.to_payload(),
        "account_kind": account.account_kind.value,
        "sizing_method": account.sizing_method.value,
        "algorithm_version": account.algorithm_version,
        "house_view_id": account.house_view_id,
        "policy_id": account.policy_id,
        "input_id": account.input_id,
        "evidence_cutoff": account.evidence_cutoff.isoformat(),
        "position_snapshot_id": account.position_snapshot_id,
        "starting_cash": _decimal_text(account.starting_cash),
        "starting_equity": _decimal_text(account.starting_equity),
        "cash_currency": account.cash_currency,
        "targets": [_target_payload(item) for item in account.targets],
        "target_bands": [_shadow_band_payload(item) for item in account.target_bands],
        "retained_cash_weight": _decimal_text(account.retained_cash_weight),
        "modeled_turnover_weight": _decimal_text(account.modeled_turnover_weight),
        "modeled_turnover_notional": _decimal_text(account.modeled_turnover_notional),
        "cost_input_policy_id": account.cost_input_policy_id,
        "cost_input_source": account.cost_input_source,
        "cost_inputs_available_at": account.cost_inputs_available_at.isoformat(),
        "cost_inputs": [_cost_input_payload(item) for item in account.cost_inputs],
        "material_fingerprints": dict(account.material_fingerprints),
    }


def _target_payload(item: PortfolioTarget) -> dict[str, object]:
    return {
        "identity": item.identity.to_payload(),
        "target_weight": _decimal_text(item.target_weight),
    }


def _shadow_band_payload(item: ShadowTargetBand) -> dict[str, object]:
    return {
        "identity": item.identity.to_payload(),
        "target_weight": _decimal_text(item.target_weight),
        "lower_weight": _decimal_text(item.lower_weight),
        "upper_weight": _decimal_text(item.upper_weight),
        "current_weight": _decimal_text(item.current_weight),
        "adjustment_weight": _decimal_text(item.adjustment_weight),
        "accounting_reason": item.accounting_reason.value,
    }


def _cost_input_payload(item: ShadowCostInput) -> dict[str, object]:
    return {
        "identity": item.identity.to_payload(),
        "price": _decimal_text(item.price),
        "median_dollar_volume": _decimal_text(item.median_dollar_volume),
        "sector": item.sector,
        "common_cause_group": item.common_cause_group,
        "correlation_cluster": item.correlation_cluster,
        "current_weight": _decimal_text(item.current_weight),
        "adjustment_weight": _decimal_text(item.adjustment_weight),
    }


def _parse_target(value: object) -> PortfolioTarget | None:
    fields = _exact_mapping(value, {"identity", "target_weight"})
    if fields is None:
        return None
    match (
        parse_instrument_identity(fields["identity"]),
        _plain_decimal(fields["target_weight"]),
    ):
        case EquityInstrumentIdentity() as identity, Decimal() as target_weight:
            pass
        case _:
            return None
    try:
        return PortfolioTarget(identity, target_weight)
    except ValueError:
        return None


def _parse_shadow_band(value: object) -> ShadowTargetBand | None:
    fields = _exact_mapping(
        value,
        {
            "identity",
            "target_weight",
            "lower_weight",
            "upper_weight",
            "current_weight",
            "adjustment_weight",
            "accounting_reason",
        },
    )
    if fields is None:
        return None
    reason = fields["accounting_reason"]
    match (
        parse_instrument_identity(fields["identity"]),
        _plain_decimal(fields["target_weight"]),
        _plain_decimal(fields["lower_weight"]),
        _plain_decimal(fields["upper_weight"]),
        _plain_decimal(fields["current_weight"]),
        _plain_decimal(fields["adjustment_weight"]),
    ):
        case (
            EquityInstrumentIdentity() as identity,
            Decimal() as target_weight,
            Decimal() as lower_weight,
            Decimal() as upper_weight,
            Decimal() as current_weight,
            Decimal() as adjustment_weight,
        ):
            pass
        case _:
            return None
    match reason:
        case str() as reason_text:
            pass
        case _:
            return None
    try:
        return ShadowTargetBand(
            identity,
            target_weight,
            lower_weight,
            upper_weight,
            current_weight,
            adjustment_weight,
            PortfolioTradeReason(reason_text),
        )
    except (TypeError, ValueError):
        return None


def _parse_cost_input(value: object) -> ShadowCostInput | None:
    fields = _exact_mapping(
        value,
        {
            "identity",
            "price",
            "median_dollar_volume",
            "sector",
            "common_cause_group",
            "correlation_cluster",
            "current_weight",
            "adjustment_weight",
        },
    )
    if fields is None:
        return None
    match (
        parse_instrument_identity(fields["identity"]),
        _plain_decimal(fields["price"]),
        _plain_decimal(fields["median_dollar_volume"]),
        fields["sector"],
        fields["common_cause_group"],
        fields["correlation_cluster"],
        _plain_decimal(fields["current_weight"]),
        _plain_decimal(fields["adjustment_weight"]),
    ):
        case (
            EquityInstrumentIdentity() as identity,
            Decimal() as price,
            Decimal() as median_dollar_volume,
            str() as sector,
            str() as common_cause_group,
            str() as correlation_cluster,
            Decimal() as current_weight,
            Decimal() as adjustment_weight,
        ):
            pass
        case _:
            return None
    try:
        return ShadowCostInput(
            identity,
            price,
            median_dollar_volume,
            sector,
            common_cause_group,
            correlation_cluster,
            current_weight,
            adjustment_weight,
        )
    except ValueError:
        return None


def _parse_material_fingerprints(value: object) -> tuple[tuple[str, str], ...] | None:
    fields = _exact_mapping(
        value,
        {
            "configuration",
            "constitution",
            "research_policy",
            "universe_snapshot",
            "portfolio_policy",
            "portfolio_input",
            "house_view",
        },
    )
    if fields is None:
        return None
    ordered_keys = (
        "configuration",
        "constitution",
        "research_policy",
        "universe_snapshot",
        "portfolio_policy",
        "portfolio_input",
        "house_view",
    )
    parsed: list[tuple[str, str]] = []
    for key in ordered_keys:
        item = fields[key]
        if type(item) is not str:
            return None
        parsed.append((key, item))
    return tuple(parsed)
