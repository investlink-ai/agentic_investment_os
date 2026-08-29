"""Construct one cash-preserving Balanced portfolio from a validated HouseView."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from enum import StrEnum
from itertools import pairwise
from typing import TYPE_CHECKING, Protocol, Self, TypeGuard

from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    MarketSession,
    canonical_instrument_bytes,
    parse_decision_cycle_identity,
    parse_instrument_identity,
)
from agentic_investment_os.domain.lifecycle import PortfolioShadowKind
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.domain.universe import (
    EquityPosition,
    PositionSnapshot,
    PositionValuation,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = (
    "AdjustedClose",
    "BalancedPortfolioPolicy",
    "HouseView",
    "HouseViewResolution",
    "MaterialEventEvidence",
    "MaterialEventRisk",
    "PortfolioConstructionRequest",
    "PortfolioConstructionResult",
    "PortfolioCostInputPolicy",
    "PortfolioInputSet",
    "PortfolioInputSource",
    "PortfolioRefusalReason",
    "PortfolioRiskInput",
    "PortfolioSizingMethod",
    "PortfolioStance",
    "PortfolioTarget",
    "PortfolioTradeReason",
    "ShadowPortfolioPolicy",
    "TargetBand",
    "construct_balanced_portfolio",
    "parse_portfolio_construction_result",
)

_HASH_LENGTH = 64
_POLICY_SCHEMA_VERSION = 2
_MINIMUM_LOOKBACK_DAYS = 2
_MAXIMUM_DECIMAL_TEXT_LENGTH = 64
_MAXIMUM_SOURCE_IDENTITY_LENGTH = 256
_WEEKEND_START_DAY = 5
_SESSION_CALENDAR_ID = "xnys-regular-2026a"
_SESSION_CALENDAR_YEAR = 2026
_SESSION_CALENDAR_HOLIDAYS = frozenset(
    {
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    }
)
_PORTFOLIO_DECIMAL_CONTEXT = Context(
    prec=28,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)
_UNAVAILABLE_POLICY_ID = "e8330921e97c6b1482ec9aa35b078a0faf00b2ae5af23e5d1d727bc428586238"
_UNAVAILABLE_INPUT_ID = "882c53691ad360cf565f4a11bf79418e747dad07abcba8da67c0c536fd6b9099"
_INVALID_ADJUSTED_CLOSE = "invalid adjusted close"
_INVALID_EVENT_RISK = "invalid material event risk"
_INVALID_EVENT_EVIDENCE = "invalid material event evidence"
_INVALID_RISK_INPUT = "invalid portfolio risk input"
_INVALID_INPUT_SET = "invalid portfolio input set"
_INVALID_POLICY = "invalid Balanced portfolio policy"
_INVALID_HOUSE_VIEW_ITEM = "invalid HouseView item"
_INVALID_HOUSE_VIEW = "invalid HouseView"
_INVALID_TARGET = "invalid portfolio target"
_INVALID_TARGET_BAND = "invalid target band"
_INVALID_RESULT = "invalid portfolio construction result"


class PortfolioStance(StrEnum):
    """Preserve the closed validated CIO stance vocabulary."""

    LONG = "long"
    HOLD = "hold"
    REDUCE = "reduce"
    EXIT = "exit"
    ABSTAIN = "abstain"


class PortfolioRefusalReason(StrEnum):
    """Bound deterministic construction refusals before accepted exposure changes."""

    INVALID_REQUEST = "invalid_request"
    INCOMPLETE_INPUT = "incomplete_input"
    STALE_INPUT = "stale_input"
    CONTRADICTORY_INPUT = "contradictory_input"
    AUTHORITY_VIOLATION = "authority_violation"


class PortfolioTradeReason(StrEnum):
    """Explain why one Target Band does or does not permit an adjustment."""

    IN_BAND = "in_band"
    THESIS_ENTRY = "thesis_entry"
    THESIS_EXIT = "thesis_exit"
    THESIS_REDUCTION = "thesis_reduction"
    BAND_BREACH = "band_breach"
    BELOW_MINIMUM_NOTIONAL = "below_minimum_notional"
    EVENT_BLOCKED = "event_blocked"
    HARD_RISK_BREACH = "hard_risk_breach"


class PortfolioSizingMethod(StrEnum):
    """Name the only deterministic sizing methods admitted to portfolio accounting."""

    INVERSE_VOLATILITY = "inverse_volatility"
    EQUAL_WEIGHT = "equal_weight"


@dataclass(frozen=True, slots=True)
class ShadowPortfolioPolicy:
    """Freeze one required non-executable shadow envelope and sizing method."""

    account_kind: PortfolioShadowKind
    sizing_method: PortfolioSizingMethod
    algorithm_version: int
    maximum_gross_weight: Decimal
    maximum_name_weight: Decimal
    maximum_sector_weight: Decimal

    def __post_init__(self) -> None:
        expected = {
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
        actual = (
            self.sizing_method,
            self.maximum_gross_weight,
            self.maximum_name_weight,
            self.maximum_sector_weight,
        )
        if (
            type(self.account_kind) is not PortfolioShadowKind
            or type(self.sizing_method) is not PortfolioSizingMethod
            or type(self.algorithm_version) is not int
            or self.algorithm_version != 1
            or any(type(item) is not Decimal or not item.is_finite() for item in actual[1:])
            or actual != expected[self.account_kind]
        ):
            raise ValueError(_INVALID_POLICY)

    def to_payload(self) -> dict[str, object]:
        return {
            "account_kind": self.account_kind.value,
            "sizing_method": self.sizing_method.value,
            "algorithm_version": self.algorithm_version,
            "maximum_gross_weight": _decimal_text(self.maximum_gross_weight),
            "maximum_name_weight": _decimal_text(self.maximum_name_weight),
            "maximum_sector_weight": _decimal_text(self.maximum_sector_weight),
        }


@dataclass(frozen=True, slots=True)
class PortfolioCostInputPolicy:
    """Freeze ex-ante turnover and price inputs without evaluating later outcomes."""

    schema_version: int
    model_type: str
    turnover_basis: str
    price_basis: str

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or self.model_type != "frozen_ex_ante_turnover_inputs"
            or self.turnover_basis != "absolute_adjustment_weight"
            or self.price_basis != "available_at_time"
        ):
            raise ValueError(_INVALID_POLICY)

    @property
    def policy_id(self) -> str:
        return _content_hash(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "model_type": self.model_type,
            "turnover_basis": self.turnover_basis,
            "price_basis": self.price_basis,
        }


def _parse_shadow_policy(value: object) -> ShadowPortfolioPolicy | None:
    fields = _exact_mapping(
        value,
        {
            "account_kind",
            "sizing_method",
            "algorithm_version",
            "maximum_gross_weight",
            "maximum_name_weight",
            "maximum_sector_weight",
        },
    )
    if fields is None:
        return None
    match (
        fields["account_kind"],
        fields["sizing_method"],
        fields["algorithm_version"],
        _plain_decimal(fields["maximum_gross_weight"]),
        _plain_decimal(fields["maximum_name_weight"]),
        _plain_decimal(fields["maximum_sector_weight"]),
    ):
        case (
            str() as account_kind,
            str() as sizing_method,
            int() as algorithm_version,
            Decimal() as maximum_gross,
            Decimal() as maximum_name,
            Decimal() as maximum_sector,
        ) if type(algorithm_version) is int:
            pass
        case _:
            return None
    try:
        return ShadowPortfolioPolicy(
            account_kind=PortfolioShadowKind(account_kind),
            sizing_method=PortfolioSizingMethod(sizing_method),
            algorithm_version=algorithm_version,
            maximum_gross_weight=maximum_gross,
            maximum_name_weight=maximum_name,
            maximum_sector_weight=maximum_sector,
        )
    except (TypeError, ValueError):
        return None


def _parse_cost_input_policy(value: object) -> PortfolioCostInputPolicy | None:
    fields = _exact_mapping(
        value,
        {"schema_version", "model_type", "turnover_basis", "price_basis"},
    )
    if fields is None:
        return None
    match (
        fields["schema_version"],
        fields["model_type"],
        fields["turnover_basis"],
        fields["price_basis"],
    ):
        case (
            int() as schema_version,
            str() as model_type,
            str() as turnover_basis,
            str() as price_basis,
        ) if type(schema_version) is int:
            pass
        case _:
            return None
    try:
        return PortfolioCostInputPolicy(
            schema_version=schema_version,
            model_type=model_type,
            turnover_basis=turnover_basis,
            price_basis=price_basis,
        )
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class AdjustedClose:
    """Carry one canonical daily close with its session, source, and availability."""

    session: MarketSession
    observed_at: UtcInstant
    available_at: UtcInstant
    source_identity: str
    price: Decimal

    def __post_init__(self) -> None:
        if (
            type(self.session) is not MarketSession
            or not _is_pinned_regular_session(self.session.trading_date)
            or type(self.observed_at) is not UtcInstant
            or self.observed_at.value.date() != self.session.trading_date
            or type(self.available_at) is not UtcInstant
            or self.available_at.value < self.observed_at.value
            or type(self.source_identity) is not str
            or not 1 <= len(self.source_identity) <= _MAXIMUM_SOURCE_IDENTITY_LENGTH
            or type(self.price) is not Decimal
            or not self.price.is_finite()
            or self.price <= 0
        ):
            raise ValueError(_INVALID_ADJUSTED_CLOSE)


@dataclass(frozen=True, slots=True)
class MaterialEventRisk:
    """Describe one independently recorded company or macro release calendar entry."""

    event_id: str
    event_type: str
    releases_at: UtcInstant
    source_identity: str
    calendar_available_at: UtcInstant

    def __post_init__(self) -> None:
        if (
            type(self.event_id) is not str
            or not self.event_id
            or self.event_type not in ("company_release", "macro_release")
            or type(self.releases_at) is not UtcInstant
            or type(self.source_identity) is not str
            or not self.source_identity
            or type(self.calendar_available_at) is not UtcInstant
        ):
            raise ValueError(_INVALID_EVENT_RISK)


@dataclass(frozen=True, slots=True)
class MaterialEventEvidence:
    """Carry typed official release provenance resolved from the current Evidence Vault."""

    artifact_id: str
    evidence_kind: str
    source_identity: str
    released_at: UtcInstant
    available_at: UtcInstant
    mapped_identities: tuple[EquityInstrumentIdentity, ...]

    def __post_init__(self) -> None:
        canonical_mappings = tuple(sorted(self.mapped_identities, key=canonical_instrument_bytes))
        if (
            not _is_hash(self.artifact_id)
            or self.evidence_kind not in ("issuer_release", "official_macro")
            or type(self.source_identity) is not str
            or not self.source_identity
            or type(self.released_at) is not UtcInstant
            or type(self.available_at) is not UtcInstant
            or self.available_at.value < self.released_at.value
            or type(self.mapped_identities) is not tuple
            or any(type(item) is not EquityInstrumentIdentity for item in self.mapped_identities)
            or canonical_mappings != self.mapped_identities
            or len({canonical_instrument_bytes(item) for item in self.mapped_identities})
            != len(self.mapped_identities)
        ):
            raise ValueError(_INVALID_EVENT_EVIDENCE)


@dataclass(frozen=True, slots=True)
class PortfolioRiskInput:
    """Carry complete per-instrument as-of inputs required by Balanced policy."""

    identity: EquityInstrumentIdentity
    price: Decimal
    price_unit: str
    adjusted_closes: tuple[AdjustedClose, ...]
    sector: str
    median_dollar_volume: Decimal
    liquidity_unit: str
    common_cause_group: str
    correlation_cluster: str
    material_events: tuple[MaterialEventRisk, ...]

    def __post_init__(self) -> None:
        if (
            type(self.identity) is not EquityInstrumentIdentity
            or type(self.price) is not Decimal
            or not self.price.is_finite()
            or self.price <= 0
            or self.price_unit != "usd_per_share"
            or type(self.adjusted_closes) is not tuple
            or not self.adjusted_closes
            or any(type(item) is not AdjustedClose for item in self.adjusted_closes)
            or tuple(item.session.trading_date for item in self.adjusted_closes)
            != tuple(sorted(item.session.trading_date for item in self.adjusted_closes))
            or len({item.session.trading_date for item in self.adjusted_closes})
            != len(self.adjusted_closes)
            or not _close_sessions_are_consecutive(self.adjusted_closes)
            or len({item.source_identity for item in self.adjusted_closes}) != 1
            or type(self.sector) is not str
            or not self.sector
            or type(self.median_dollar_volume) is not Decimal
            or not self.median_dollar_volume.is_finite()
            or self.median_dollar_volume < 0
            or self.liquidity_unit != "usd_per_day"
            or type(self.common_cause_group) is not str
            or not self.common_cause_group
            or type(self.correlation_cluster) is not str
            or not self.correlation_cluster
            or type(self.material_events) is not tuple
            or any(type(item) is not MaterialEventRisk for item in self.material_events)
            or tuple(item.event_id for item in self.material_events)
            != tuple(sorted({item.event_id for item in self.material_events}))
        ):
            raise ValueError(_INVALID_RISK_INPUT)


@dataclass(frozen=True, slots=True)
class PortfolioInputSet:
    """Bind complete positions, cash, and market-risk facts to one as-of identity."""

    position_snapshot: PositionSnapshot
    cash: Decimal
    cash_currency: str
    source_identity: str
    observed_at: UtcInstant
    available_at: UtcInstant
    data_regime: str
    risk_inputs: tuple[PortfolioRiskInput, ...]
    input_id: str

    @property
    def session_calendar_id(self) -> str:
        """Identify the code-pinned exchange calendar used to admit daily closes."""
        return _SESSION_CALENDAR_ID

    def __post_init__(self) -> None:
        if (
            not _input_set_fields_are_valid(
                self.position_snapshot,
                self.cash,
                self.cash_currency,
                self.source_identity,
                self.observed_at,
                self.available_at,
                self.data_regime,
                self.risk_inputs,
            )
            or not _is_hash(self.input_id)
            or self.input_id
            != _content_hash(
                _input_set_material(
                    self.position_snapshot,
                    self.cash,
                    self.cash_currency,
                    self.source_identity,
                    self.observed_at,
                    self.available_at,
                    self.data_regime,
                    self.risk_inputs,
                )
            )
        ):
            raise ValueError(_INVALID_INPUT_SET)

    @classmethod
    def create(  # noqa: PLR0913 - every as-of source field is an explicit authority pin.
        cls,
        *,
        position_snapshot: PositionSnapshot,
        cash: Decimal,
        cash_currency: str,
        source_identity: str,
        observed_at: UtcInstant,
        available_at: UtcInstant,
        data_regime: str,
        risk_inputs: tuple[PortfolioRiskInput, ...],
    ) -> Self:
        if type(risk_inputs) is not tuple or any(
            type(item) is not PortfolioRiskInput for item in risk_inputs
        ):
            raise ValueError(_INVALID_INPUT_SET)
        ordered = tuple(
            sorted(risk_inputs, key=lambda item: canonical_instrument_bytes(item.identity))
        )
        if not _input_set_fields_are_valid(
            position_snapshot,
            cash,
            cash_currency,
            source_identity,
            observed_at,
            available_at,
            data_regime,
            ordered,
        ):
            raise ValueError(_INVALID_INPUT_SET)
        input_id = _content_hash(
            _input_set_material(
                position_snapshot,
                cash,
                cash_currency,
                source_identity,
                observed_at,
                available_at,
                data_regime,
                ordered,
            )
        )
        return cls(
            position_snapshot,
            cash,
            cash_currency,
            source_identity,
            observed_at,
            available_at,
            data_regime,
            ordered,
            input_id,
        )


class PortfolioInputSource(Protocol):
    """Load one hostile as-of input set against the pinned position snapshot."""

    def load(
        self,
        position_snapshot: PositionSnapshot,
    ) -> PortfolioInputSet | PortfolioRefusalReason: ...


@dataclass(frozen=True, slots=True)
class BalancedPortfolioPolicy:
    """Freeze Balanced mechanics and every required same-input shadow declaration."""

    schema_version: int
    estimator: str
    lookback_days: int
    annualization_periods: int
    volatility_floor: Decimal
    price_adjustment: str
    maximum_input_age_seconds: int
    maximum_gross_weight: Decimal
    maximum_name_weight: Decimal
    maximum_sector_weight: Decimal
    maximum_common_cause_weight: Decimal
    maximum_correlation_cluster_weight: Decimal
    maximum_fraction_of_median_dollar_volume: Decimal
    target_band_width: Decimal
    minimum_executable_notional: Decimal
    partial_adjustment_fraction: Decimal
    reduce_multiplier: Decimal
    uncertainty_multipliers: tuple[tuple[str, Decimal], ...]
    shadow_policies: tuple[ShadowPortfolioPolicy, ...]
    cost_input_policy: PortfolioCostInputPolicy

    def __post_init__(self) -> None:
        uncertainty = dict(self.uncertainty_multipliers)
        decimals = (
            self.volatility_floor,
            self.maximum_gross_weight,
            self.maximum_name_weight,
            self.maximum_sector_weight,
            self.maximum_common_cause_weight,
            self.maximum_correlation_cluster_weight,
            self.maximum_fraction_of_median_dollar_volume,
            self.target_band_width,
            self.minimum_executable_notional,
            self.partial_adjustment_fraction,
            self.reduce_multiplier,
            *uncertainty.values(),
        )
        if (
            type(self.schema_version) is not int
            or self.schema_version != _POLICY_SCHEMA_VERSION
            or self.estimator != "sample_standard_deviation"
            or type(self.lookback_days) is not int
            or self.lookback_days < _MINIMUM_LOOKBACK_DAYS
            or type(self.annualization_periods) is not int
            or self.annualization_periods < 1
            or self.price_adjustment != "split_adjusted_close"
            or type(self.maximum_input_age_seconds) is not int
            or self.maximum_input_age_seconds < 1
            or any(type(item) is not Decimal or not item.is_finite() for item in decimals)
            or self.volatility_floor <= 0
            or self.maximum_gross_weight != Decimal("0.80")
            or self.maximum_name_weight != Decimal("0.08")
            or self.maximum_sector_weight != Decimal("0.25")
            or self.maximum_common_cause_weight != Decimal("0.25")
            or self.maximum_correlation_cluster_weight != Decimal("0.25")
            or self.maximum_fraction_of_median_dollar_volume != Decimal("0.01")
            or self.target_band_width < 0
            or self.target_band_width > self.maximum_name_weight
            or self.minimum_executable_notional <= 0
            or not Decimal(0) < self.partial_adjustment_fraction <= Decimal(1)
            or not Decimal(0) <= self.reduce_multiplier <= Decimal(1)
            or tuple(key for key, _ in self.uncertainty_multipliers) != ("low", "medium", "high")
            or not (
                Decimal(0)
                < uncertainty["high"]
                <= uncertainty["medium"]
                <= uncertainty["low"]
                <= Decimal(1)
            )
            or type(self.shadow_policies) is not tuple
            or tuple(item.account_kind for item in self.shadow_policies)
            != (
                PortfolioShadowKind.CONSERVATIVE,
                PortfolioShadowKind.GROWTH,
                PortfolioShadowKind.EQUAL_WEIGHT,
            )
            or any(type(item) is not ShadowPortfolioPolicy for item in self.shadow_policies)
            or type(self.cost_input_policy) is not PortfolioCostInputPolicy
        ):
            raise ValueError(_INVALID_POLICY)

    @classmethod
    def parse(cls, value: object) -> Self | None:
        """Validate one complete hostile policy representation without defaults."""
        fields = _exact_mapping(
            value,
            {
                "schema_version",
                "policy_type",
                "asset_class",
                "risk_profile",
                "realized_volatility",
                "maximum_input_age_seconds",
                "maximum_gross_weight",
                "maximum_name_weight",
                "maximum_sector_weight",
                "maximum_common_cause_weight",
                "maximum_correlation_cluster_weight",
                "maximum_fraction_of_median_dollar_volume",
                "target_band_width",
                "minimum_executable_notional",
                "partial_adjustment_fraction",
                "reduce_multiplier",
                "uncertainty_multipliers",
                "shadow_accounts",
                "modeled_cost_inputs",
            },
        )
        if fields is None:
            return None
        volatility = _exact_mapping(
            fields["realized_volatility"],
            {
                "estimator",
                "lookback_days",
                "annualization_periods",
                "floor",
                "price_adjustment",
            },
        )
        multipliers = _exact_mapping(fields["uncertainty_multipliers"], {"low", "medium", "high"})
        if (
            volatility is None
            or multipliers is None
            or fields["policy_type"] != "balanced_with_same_input_shadows"
            or fields["asset_class"] != "us_equity"
            or fields["risk_profile"] != "balanced"
            or type(fields["shadow_accounts"]) is not list
        ):
            return None
        shadow_policies = tuple(
            item
            for item in (_parse_shadow_policy(value) for value in fields["shadow_accounts"])
            if item is not None
        )
        cost_input_policy = _parse_cost_input_policy(fields["modeled_cost_inputs"])
        if len(shadow_policies) != len(fields["shadow_accounts"]) or cost_input_policy is None:
            return None
        decimal_fields = (
            "maximum_gross_weight",
            "maximum_name_weight",
            "maximum_sector_weight",
            "maximum_common_cause_weight",
            "maximum_correlation_cluster_weight",
            "maximum_fraction_of_median_dollar_volume",
            "target_band_width",
            "minimum_executable_notional",
            "partial_adjustment_fraction",
            "reduce_multiplier",
        )
        parsed_decimal_values = tuple(_plain_decimal(fields[field]) for field in decimal_fields)
        floor = _plain_decimal(volatility["floor"])
        parsed_multiplier_values = tuple(
            _plain_decimal(multipliers[key]) for key in ("low", "medium", "high")
        )
        schema_version = fields["schema_version"]
        estimator = volatility["estimator"]
        lookback_days = volatility["lookback_days"]
        annualization_periods = volatility["annualization_periods"]
        price_adjustment = volatility["price_adjustment"]
        maximum_input_age_seconds = fields["maximum_input_age_seconds"]
        if (
            floor is None
            or any(item is None for item in parsed_decimal_values)
            or any(item is None for item in parsed_multiplier_values)
            or type(schema_version) is not int
            or type(estimator) is not str
            or type(lookback_days) is not int
            or type(annualization_periods) is not int
            or type(price_adjustment) is not str
            or type(maximum_input_age_seconds) is not int
        ):
            return None
        parsed_decimals = tuple(item for item in parsed_decimal_values if item is not None)
        (
            maximum_gross_weight,
            maximum_name_weight,
            maximum_sector_weight,
            maximum_common_cause_weight,
            maximum_correlation_cluster_weight,
            maximum_fraction_of_median_dollar_volume,
            target_band_width,
            minimum_executable_notional,
            partial_adjustment_fraction,
            reduce_multiplier,
        ) = parsed_decimals
        parsed_multipliers = tuple(item for item in parsed_multiplier_values if item is not None)
        try:
            return cls(
                schema_version=schema_version,
                estimator=estimator,
                lookback_days=lookback_days,
                annualization_periods=annualization_periods,
                volatility_floor=floor,
                price_adjustment=price_adjustment,
                maximum_input_age_seconds=maximum_input_age_seconds,
                maximum_gross_weight=maximum_gross_weight,
                maximum_name_weight=maximum_name_weight,
                maximum_sector_weight=maximum_sector_weight,
                maximum_common_cause_weight=maximum_common_cause_weight,
                maximum_correlation_cluster_weight=maximum_correlation_cluster_weight,
                maximum_fraction_of_median_dollar_volume=(maximum_fraction_of_median_dollar_volume),
                target_band_width=target_band_width,
                minimum_executable_notional=minimum_executable_notional,
                partial_adjustment_fraction=partial_adjustment_fraction,
                reduce_multiplier=reduce_multiplier,
                uncertainty_multipliers=tuple(
                    zip(("low", "medium", "high"), parsed_multipliers, strict=True)
                ),
                shadow_policies=shadow_policies,
                cost_input_policy=cost_input_policy,
            )
        except (TypeError, ValueError):
            return None

    @property
    def policy_id(self) -> str:
        return _content_hash(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_type": "balanced_with_same_input_shadows",
            "asset_class": "us_equity",
            "risk_profile": "balanced",
            "realized_volatility": {
                "estimator": self.estimator,
                "lookback_days": self.lookback_days,
                "annualization_periods": self.annualization_periods,
                "floor": _decimal_text(self.volatility_floor),
                "price_adjustment": self.price_adjustment,
            },
            "maximum_input_age_seconds": self.maximum_input_age_seconds,
            "maximum_gross_weight": _decimal_text(self.maximum_gross_weight),
            "maximum_name_weight": _decimal_text(self.maximum_name_weight),
            "maximum_sector_weight": _decimal_text(self.maximum_sector_weight),
            "maximum_common_cause_weight": _decimal_text(self.maximum_common_cause_weight),
            "maximum_correlation_cluster_weight": _decimal_text(
                self.maximum_correlation_cluster_weight
            ),
            "maximum_fraction_of_median_dollar_volume": _decimal_text(
                self.maximum_fraction_of_median_dollar_volume
            ),
            "target_band_width": _decimal_text(self.target_band_width),
            "minimum_executable_notional": _decimal_text(self.minimum_executable_notional),
            "partial_adjustment_fraction": _decimal_text(self.partial_adjustment_fraction),
            "reduce_multiplier": _decimal_text(self.reduce_multiplier),
            "uncertainty_multipliers": {
                key: _decimal_text(value) for key, value in self.uncertainty_multipliers
            },
            "shadow_accounts": [item.to_payload() for item in self.shadow_policies],
            "modeled_cost_inputs": self.cost_input_policy.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class HouseViewResolution:
    """Carry one exact production CIO stance without sizing authority."""

    identity: EquityInstrumentIdentity
    request_id: str
    resolution_id: str
    stance: PortfolioStance
    uncertainty: str
    production_authority: bool
    eligible_for_new_entry: bool = True
    is_position: bool = False
    evidence_artifact_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HouseViewItem:
    identity: EquityInstrumentIdentity
    request_id: str
    resolution_id: str
    stance: PortfolioStance
    uncertainty: str
    eligible: bool
    event_blocked: bool

    def __post_init__(self) -> None:
        if (
            type(self.identity) is not EquityInstrumentIdentity
            or not _is_hash(self.request_id)
            or not _is_hash(self.resolution_id)
            or type(self.stance) is not PortfolioStance
            or self.uncertainty not in ("low", "medium", "high")
            or type(self.eligible) is not bool
            or type(self.event_blocked) is not bool
            or (self.stance is PortfolioStance.ABSTAIN and self.eligible)
        ):
            raise ValueError(_INVALID_HOUSE_VIEW_ITEM)


@dataclass(frozen=True, slots=True)
class HouseView:
    schema_version: int
    run_id: str
    cycle: MarketSession
    evidence_cutoff: UtcInstant
    data_regime: str
    configuration_hash: str
    constitution_version: int
    constitution_hash: str
    research_policy_hash: str
    research_artifact_ids: tuple[str, ...]
    memory_event_ids: tuple[str, ...]
    universe_snapshot_id: str
    policy_id: str
    input_id: str
    house_view_id: str
    items: tuple[HouseViewItem, ...]

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or any(
            type(item) is not HouseViewItem for item in self.items
        ):
            raise ValueError(_INVALID_HOUSE_VIEW)
        ordered = tuple(
            sorted(self.items, key=lambda item: canonical_instrument_bytes(item.identity))
        )
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or not _is_hash(self.run_id)
            or type(self.cycle) is not MarketSession
            or type(self.evidence_cutoff) is not UtcInstant
            or type(self.data_regime) is not str
            or not self.data_regime
            or not _is_hash(self.configuration_hash)
            or type(self.constitution_version) is not int
            or self.constitution_version < 1
            or not _is_hash(self.constitution_hash)
            or not _is_hash(self.research_policy_hash)
            or type(self.research_artifact_ids) is not tuple
            or any(not _is_hash(item) for item in self.research_artifact_ids)
            or tuple(sorted(set(self.research_artifact_ids))) != self.research_artifact_ids
            or type(self.memory_event_ids) is not tuple
            or any(not _is_hash(item) for item in self.memory_event_ids)
            or tuple(sorted(set(self.memory_event_ids))) != self.memory_event_ids
            or not _is_hash(self.universe_snapshot_id)
            or not _is_hash(self.policy_id)
            or not _is_hash(self.input_id)
            or ordered != self.items
            or len({canonical_instrument_bytes(item.identity) for item in self.items})
            != len(self.items)
            or len({item.request_id for item in self.items}) != len(self.items)
            or not _is_hash(self.house_view_id)
            or self.house_view_id != _content_hash(_house_view_material(self))
        ):
            raise ValueError(_INVALID_HOUSE_VIEW)


@dataclass(frozen=True, slots=True)
class PortfolioConstructionRequest:
    """Carry the exact validated production and as-of material for one construction."""

    run_id: str
    cycle: MarketSession
    evidence_cutoff: UtcInstant
    data_regime: str
    configuration_hash: str
    constitution_version: int
    constitution_hash: str
    research_policy_hash: str
    research_artifact_ids: tuple[str, ...]
    memory_event_ids: tuple[str, ...]
    universe_snapshot_id: str
    expected_research_request_ids: tuple[str, ...]
    resolutions: tuple[HouseViewResolution, ...]
    inputs: PortfolioInputSet
    policy: BalancedPortfolioPolicy
    material_event_evidence: tuple[MaterialEventEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class PortfolioTarget:
    identity: EquityInstrumentIdentity
    target_weight: Decimal

    def __post_init__(self) -> None:
        if (
            type(self.identity) is not EquityInstrumentIdentity
            or type(self.target_weight) is not Decimal
            or not self.target_weight.is_finite()
            or not Decimal(0) <= self.target_weight <= Decimal(1)
        ):
            raise ValueError(_INVALID_TARGET)


@dataclass(frozen=True, slots=True)
class TargetBand:
    identity: EquityInstrumentIdentity
    target_weight: Decimal
    lower_weight: Decimal
    upper_weight: Decimal
    current_weight: Decimal
    adjustment_weight: Decimal
    trade_eligible: bool
    trade_reason: PortfolioTradeReason

    def __post_init__(self) -> None:
        weights = (
            self.target_weight,
            self.lower_weight,
            self.upper_weight,
            self.current_weight,
            self.adjustment_weight,
        )
        no_trade_reasons = (
            PortfolioTradeReason.IN_BAND,
            PortfolioTradeReason.BELOW_MINIMUM_NOTIONAL,
            PortfolioTradeReason.EVENT_BLOCKED,
        )
        if (
            type(self.identity) is not EquityInstrumentIdentity
            or any(
                type(item) is not Decimal or not item.is_finite() or item < 0 for item in weights
            )
            or not self.lower_weight <= self.target_weight <= self.upper_weight <= Decimal(1)
            or type(self.trade_eligible) is not bool
            or type(self.trade_reason) is not PortfolioTradeReason
            or self.trade_eligible != (self.adjustment_weight != self.current_weight)
            or (self.trade_eligible and self.trade_reason in no_trade_reasons)
            or (not self.trade_eligible and self.trade_reason not in no_trade_reasons)
        ):
            raise ValueError(_INVALID_TARGET_BAND)

    @property
    def target_band_id(self) -> str:
        """Identify this exact immutable target-band instruction."""
        return _content_hash(_band_payload(self))


@dataclass(frozen=True, slots=True)
class PortfolioConstructionResult:
    house_view: HouseView | None
    targets: tuple[PortfolioTarget, ...]
    target_bands: tuple[TargetBand, ...]
    cash_weight: Decimal
    policy_id: str
    input_id: str
    content_hash: str
    refusal: PortfolioRefusalReason | None = None

    def __post_init__(self) -> None:
        if (
            (self.house_view is not None and type(self.house_view) is not HouseView)
            or type(self.targets) is not tuple
            or any(type(item) is not PortfolioTarget for item in self.targets)
            or type(self.target_bands) is not tuple
            or any(type(item) is not TargetBand for item in self.target_bands)
        ):
            raise ValueError(_INVALID_RESULT)
        target_identities = tuple(
            canonical_instrument_bytes(item.identity) for item in self.targets
        )
        band_identities = tuple(
            canonical_instrument_bytes(item.identity) for item in self.target_bands
        )
        house_view_identities = (
            ()
            if self.house_view is None
            else tuple(canonical_instrument_bytes(item.identity) for item in self.house_view.items)
        )
        success = self.refusal is None
        if (
            target_identities != tuple(sorted(set(target_identities)))
            or band_identities != target_identities
            or (success and house_view_identities != target_identities)
            or any(
                target.target_weight != band.target_weight
                for target, band in zip(self.targets, self.target_bands, strict=True)
            )
            or type(self.cash_weight) is not Decimal
            or not self.cash_weight.is_finite()
            or not Decimal(0) <= self.cash_weight <= Decimal(1)
            or not _is_hash(self.policy_id)
            or not _is_hash(self.input_id)
            or (self.content_hash != "" and not _is_hash(self.content_hash))
            or (self.refusal is not None and type(self.refusal) is not PortfolioRefusalReason)
            or (success and self.house_view is None)
            or (
                success
                and self.house_view is not None
                and (
                    self.house_view.policy_id != self.policy_id
                    or self.house_view.input_id != self.input_id
                )
            )
            or (
                success
                and _sum_decimals(
                    (*tuple(item.target_weight for item in self.targets), self.cash_weight)
                )
                != 1
            )
            or (
                not success
                and (
                    self.house_view is not None
                    or self.targets
                    or self.target_bands
                    or self.cash_weight != 1
                )
            )
        ):
            raise ValueError(_INVALID_RESULT)

    def to_payload(self) -> dict[str, object]:
        material = _result_material(self)
        if self.content_hash != _content_hash(material):
            raise ValueError(_INVALID_RESULT)
        return {**material, "content_hash": self.content_hash}

    def require_house_view(self) -> HouseView:
        """Return the accepted HouseView or refuse use of a non-accepted result."""
        if self.refusal is not None or self.house_view is None:
            raise ValueError(_INVALID_RESULT)
        return self.house_view


@dataclass(frozen=True, slots=True)
class _PortfolioVariantResult:
    targets: tuple[PortfolioTarget, ...]
    target_bands: tuple[TargetBand, ...]
    cash_weight: Decimal


def construct_balanced_portfolio(
    request: PortfolioConstructionRequest,
) -> PortfolioConstructionResult:
    """Construct the complete deterministic Balanced result or a typed refusal."""
    with localcontext(_PORTFOLIO_DECIMAL_CONTEXT):
        return _construct_balanced_portfolio(request)


def _construct_balanced_portfolio(  # noqa: PLR0911,PLR0912 - each refusal is distinct.
    request: PortfolioConstructionRequest,
) -> PortfolioConstructionResult:
    if not _request_is_valid(request):
        return _invalid_request_refusal(request)
    if any(not resolution.production_authority for resolution in request.resolutions):
        return _refused(request, PortfolioRefusalReason.AUTHORITY_VIOLATION)
    if {item.request_id for item in request.resolutions} != set(
        request.expected_research_request_ids
    ):
        return _refused(request, PortfolioRefusalReason.INCOMPLETE_INPUT)
    if request.inputs.available_at.value > request.evidence_cutoff.value:
        return _refused(request, PortfolioRefusalReason.CONTRADICTORY_INPUT)
    age = request.evidence_cutoff.value - request.inputs.observed_at.value
    if age.total_seconds() > request.policy.maximum_input_age_seconds:
        return _refused(request, PortfolioRefusalReason.STALE_INPUT)
    risk_by_identity = {
        canonical_instrument_bytes(item.identity): item for item in request.inputs.risk_inputs
    }
    if any(
        close.observed_at.value > request.evidence_cutoff.value
        for item in request.inputs.risk_inputs
        for close in item.adjusted_closes
    ):
        return _refused(request, PortfolioRefusalReason.CONTRADICTORY_INPUT)
    if any(
        close.available_at.value > request.evidence_cutoff.value
        for item in request.inputs.risk_inputs
        for close in item.adjusted_closes
    ):
        return _refused(request, PortfolioRefusalReason.CONTRADICTORY_INPUT)
    if any(
        event.calendar_available_at.value > request.evidence_cutoff.value
        for item in request.inputs.risk_inputs
        for event in item.material_events
    ):
        return _refused(request, PortfolioRefusalReason.CONTRADICTORY_INPUT)
    if any(
        canonical_instrument_bytes(item.identity) not in risk_by_identity
        or len(risk_by_identity[canonical_instrument_bytes(item.identity)].adjusted_closes)
        < request.policy.lookback_days + 1
        for item in request.resolutions
        if item.stance in (PortfolioStance.LONG, PortfolioStance.HOLD, PortfolioStance.REDUCE)
    ):
        return _refused(request, PortfolioRefusalReason.INCOMPLETE_INPUT)

    positions: dict[bytes, EquityPosition] = {}
    for position in request.inputs.position_snapshot.positions:
        if type(position) is not EquityPosition:
            return _refused(request, PortfolioRefusalReason.CONTRADICTORY_INPUT)
        key = canonical_instrument_bytes(position.identity)
        if (
            type(position.quantity) is not Decimal
            or not position.quantity.is_finite()
            or type(position.valuation) is not PositionValuation
        ):
            return _refused(request, PortfolioRefusalReason.CONTRADICTORY_INPUT)
        if not position.valuation.amount.is_finite():
            return _refused(request, PortfolioRefusalReason.CONTRADICTORY_INPUT)
        if position.valuation.amount <= 0 or position.valuation.currency != "USD":
            return _refused(request, PortfolioRefusalReason.CONTRADICTORY_INPUT)
        risk = risk_by_identity.get(key)
        if risk is None:
            return _refused(request, PortfolioRefusalReason.INCOMPLETE_INPUT)
        if _exact_product(position.quantity, risk.price) != position.valuation.amount:
            return _refused(request, PortfolioRefusalReason.CONTRADICTORY_INPUT)
        positions[key] = position
    resolution_positions = {
        canonical_instrument_bytes(item.identity): item
        for item in request.resolutions
        if item.is_position
    }
    if set(positions) != set(resolution_positions):
        return _refused(request, PortfolioRefusalReason.INCOMPLETE_INPUT)
    equity = request.inputs.cash + sum(position.valuation.amount for position in positions.values())
    if equity <= 0:
        return _refused(request, PortfolioRefusalReason.CONTRADICTORY_INPUT)
    ordered_resolutions = tuple(
        sorted(request.resolutions, key=lambda item: canonical_instrument_bytes(item.identity))
    )
    items = tuple(
        _house_view_item(
            resolution,
            positions,
            risk_by_identity,
            request.material_event_evidence,
            request.evidence_cutoff,
        )
        for resolution in ordered_resolutions
    )
    house_view = HouseView(
        1,
        request.run_id,
        request.cycle,
        request.evidence_cutoff,
        request.data_regime,
        request.configuration_hash,
        request.constitution_version,
        request.constitution_hash,
        request.research_policy_hash,
        request.research_artifact_ids,
        request.memory_event_ids,
        request.universe_snapshot_id,
        request.policy.policy_id,
        request.inputs.input_id,
        _house_view_id(request, items),
        items,
    )
    variant = _allocate_variant(
        request,
        house_view,
        maximum_gross_weight=request.policy.maximum_gross_weight,
        maximum_name_weight=request.policy.maximum_name_weight,
        maximum_sector_weight=request.policy.maximum_sector_weight,
        sizing_method=PortfolioSizingMethod.INVERSE_VOLATILITY,
    )
    targets = variant.targets
    bands = variant.target_bands
    cash_weight = variant.cash_weight
    provisional = PortfolioConstructionResult(
        house_view,
        targets,
        bands,
        cash_weight,
        request.policy.policy_id,
        request.inputs.input_id,
        "",
    )
    return PortfolioConstructionResult(
        house_view,
        targets,
        bands,
        cash_weight,
        request.policy.policy_id,
        request.inputs.input_id,
        _content_hash(_result_material(provisional)),
    )


def _construct_shadow_variant(
    request: PortfolioConstructionRequest,
    house_view: HouseView,
    shadow_policy: ShadowPortfolioPolicy,
) -> _PortfolioVariantResult:
    """Construct accounting targets only from the exact accepted Balanced HouseView."""
    return _allocate_variant(
        request,
        house_view,
        maximum_gross_weight=shadow_policy.maximum_gross_weight,
        maximum_name_weight=shadow_policy.maximum_name_weight,
        maximum_sector_weight=shadow_policy.maximum_sector_weight,
        sizing_method=shadow_policy.sizing_method,
    )


def _allocate_variant(  # noqa: PLR0913 - the variant envelope is explicit and immutable.
    request: PortfolioConstructionRequest,
    house_view: HouseView,
    *,
    maximum_gross_weight: Decimal,
    maximum_name_weight: Decimal,
    maximum_sector_weight: Decimal,
    sizing_method: PortfolioSizingMethod,
) -> _PortfolioVariantResult:
    items = house_view.items
    risk_by_identity = {
        canonical_instrument_bytes(item.identity): item for item in request.inputs.risk_inputs
    }
    positions = {
        canonical_instrument_bytes(position.identity): position
        for position in request.inputs.position_snapshot.positions
        if type(position) is EquityPosition
    }
    active = tuple(
        item
        for item in items
        if not item.event_blocked
        and item.stance in (PortfolioStance.LONG, PortfolioStance.HOLD)
        and (item.eligible or canonical_instrument_bytes(item.identity) in positions)
    )
    sizing_scores = (
        {
            canonical_instrument_bytes(item.identity): Decimal(1)
            / _realized_volatility(
                risk_by_identity[canonical_instrument_bytes(item.identity)], request.policy
            )
            for item in active
        }
        if sizing_method is PortfolioSizingMethod.INVERSE_VOLATILITY
        else {}
    )
    denominator = sum(sizing_scores.values())
    equity = request.inputs.cash + sum(position.valuation.amount for position in positions.values())
    weights: dict[bytes, Decimal] = {}
    uncertainty = dict(request.policy.uncertainty_multipliers)
    for item in items:
        key = canonical_instrument_bytes(item.identity)
        if item.stance in (PortfolioStance.EXIT, PortfolioStance.ABSTAIN) or item.event_blocked:
            weight = Decimal(0)
        elif item.stance is PortfolioStance.REDUCE:
            current = _current_weight(positions.get(key), equity)
            risk = risk_by_identity[key]
            liquidity = (
                risk.median_dollar_volume
                * request.policy.maximum_fraction_of_median_dollar_volume
                / equity
            )
            weight = min(
                current * request.policy.reduce_multiplier,
                maximum_name_weight,
                liquidity,
            )
        elif not item.eligible and key not in positions:
            weight = Decimal(0)
        else:
            risk = risk_by_identity[key]
            raw = (
                maximum_gross_weight * sizing_scores[key] / denominator
                if sizing_method is PortfolioSizingMethod.INVERSE_VOLATILITY
                else maximum_name_weight
            ) * uncertainty[item.uncertainty]
            liquidity = (
                risk.median_dollar_volume
                * request.policy.maximum_fraction_of_median_dollar_volume
                / equity
            )
            weight = min(raw, maximum_name_weight, liquidity)
            if not item.eligible:
                weight = min(weight, _current_weight(positions[key], equity))
        weights[key] = weight

    weights = _apply_group_caps(
        weights,
        items,
        risk_by_identity,
        request.policy,
        maximum_sector_weight,
    )
    weights = _apply_gross_cap(weights, maximum_gross_weight)
    hard_risk_breaches = _hard_risk_breaches(
        weights,
        items,
        positions,
        risk_by_identity,
        equity,
        request.policy,
        maximum_gross_weight,
        maximum_name_weight,
        maximum_sector_weight,
    )
    targets = tuple(
        PortfolioTarget(item.identity, weights[canonical_instrument_bytes(item.identity)])
        for item in items
    )
    bands = tuple(
        _target_band(
            target,
            next(item for item in items if item.identity == target.identity),
            _current_weight(positions.get(canonical_instrument_bytes(target.identity)), equity),
            equity,
            request.policy,
            maximum_name_weight=maximum_name_weight,
            hard_risk_breach=(canonical_instrument_bytes(target.identity) in hard_risk_breaches),
        )
        for target in targets
    )
    cash_weight = Decimal(1) - sum(target.target_weight for target in targets)
    return _PortfolioVariantResult(targets, bands, cash_weight)


def parse_portfolio_construction_result(  # noqa: PLR0911 - reject each hostile envelope layer.
    value: object,
) -> PortfolioConstructionResult | None:
    """Validate one hostile durable portfolio-result representation."""
    root = _exact_mapping(
        value,
        {
            "schema_version",
            "record_kind",
            "house_view",
            "targets",
            "target_bands",
            "cash_weight",
            "policy_id",
            "input_id",
            "refusal",
            "content_hash",
        },
    )
    if root is None:
        return None
    if (
        type(root["schema_version"]),
        root["schema_version"],
        type(root["record_kind"]),
        root["record_kind"],
    ) != (int, 1, str, "balanced_portfolio_construction"):
        return None
    policy_id = root["policy_id"]
    input_id = root["input_id"]
    content_hash = root["content_hash"]
    if type(root["targets"]) is not list or type(root["target_bands"]) is not list:
        return None
    match policy_id, input_id, content_hash:
        case str() as parsed_policy_id, str() as parsed_input_id, str() as parsed_content_hash:
            pass
        case _:
            return None
    house_view = _parse_house_view(root["house_view"])
    targets = tuple(_parse_target(item) for item in root["targets"])
    bands = tuple(_parse_band(item) for item in root["target_bands"])
    cash_weight = _plain_decimal(root["cash_weight"])
    match cash_weight:
        case Decimal() as parsed_cash_weight:
            pass
        case _:
            return None
    refusal_value = root["refusal"]
    match refusal_value:
        case None:
            refusal = None
        case str() as refusal_text:
            try:
                refusal = PortfolioRefusalReason(refusal_text)
            except ValueError:
                return None
        case _:
            return None
    try:
        result = PortfolioConstructionResult(
            house_view,
            tuple(item for item in targets if item is not None),
            tuple(item for item in bands if item is not None),
            parsed_cash_weight,
            parsed_policy_id,
            parsed_input_id,
            parsed_content_hash,
            refusal,
        )
        return result if result.to_payload() == root else None
    except ValueError:
        return None


def _request_is_valid(request: object) -> bool:
    if (
        type(request) is not PortfolioConstructionRequest
        or type(request.inputs) is not PortfolioInputSet
        or type(request.policy) is not BalancedPortfolioPolicy
    ):
        return False
    return (
        _is_hash(request.run_id)
        and type(request.cycle) is MarketSession
        and type(request.evidence_cutoff) is UtcInstant
        and type(request.data_regime) is str
        and request.data_regime == request.inputs.data_regime
        and all(
            _is_hash(value)
            for value in (
                request.configuration_hash,
                request.constitution_hash,
                request.research_policy_hash,
                request.universe_snapshot_id,
                *request.research_artifact_ids,
                *request.memory_event_ids,
            )
        )
        and tuple(sorted(set(request.research_artifact_ids))) == request.research_artifact_ids
        and tuple(sorted(set(request.memory_event_ids))) == request.memory_event_ids
        and type(request.constitution_version) is int
        and request.constitution_version >= 1
        and type(request.resolutions) is tuple
        and type(request.material_event_evidence) is tuple
        and all(type(item) is MaterialEventEvidence for item in request.material_event_evidence)
        and tuple(item.artifact_id for item in request.material_event_evidence)
        == tuple(sorted({item.artifact_id for item in request.material_event_evidence}))
        and type(request.expected_research_request_ids) is tuple
        and tuple(sorted(set(request.expected_research_request_ids)))
        == request.expected_research_request_ids
        and all(_is_hash(item) for item in request.expected_research_request_ids)
        and all(type(item) is HouseViewResolution for item in request.resolutions)
        and all(type(item.identity) is EquityInstrumentIdentity for item in request.resolutions)
        and len({canonical_instrument_bytes(item.identity) for item in request.resolutions})
        == len(request.resolutions)
        and len({item.request_id for item in request.resolutions}) == len(request.resolutions)
        and all(
            _is_hash(item.request_id)
            and _is_hash(item.resolution_id)
            and type(item.stance) is PortfolioStance
            and item.uncertainty in ("low", "medium", "high")
            and type(item.production_authority) is bool
            and type(item.eligible_for_new_entry) is bool
            and type(item.is_position) is bool
            and type(item.evidence_artifact_ids) is tuple
            and tuple(sorted(set(item.evidence_artifact_ids))) == item.evidence_artifact_ids
            and all(_is_hash(value) for value in item.evidence_artifact_ids)
            for item in request.resolutions
        )
    )


def _realized_volatility(risk: PortfolioRiskInput, policy: BalancedPortfolioPolicy) -> Decimal:
    closes = risk.adjusted_closes[-(policy.lookback_days + 1) :]
    returns = tuple(
        closes[index].price / closes[index - 1].price - Decimal(1)
        for index in range(1, len(closes))
    )
    mean = sum(returns) / Decimal(len(returns))
    variance = sum((item - mean) ** 2 for item in returns) / Decimal(len(returns) - 1)
    annualized = (variance * Decimal(policy.annualization_periods)).sqrt()
    return max(annualized, policy.volatility_floor)


def _apply_group_caps(
    weights: dict[bytes, Decimal],
    items: tuple[HouseViewItem, ...],
    risks: dict[bytes, PortfolioRiskInput],
    policy: BalancedPortfolioPolicy,
    maximum_sector_weight: Decimal,
) -> dict[bytes, Decimal]:
    bounded = dict(weights)
    for attribute, cap in (
        ("sector", maximum_sector_weight),
        ("common_cause_group", policy.maximum_common_cause_weight),
        ("correlation_cluster", policy.maximum_correlation_cluster_weight),
    ):
        groups: dict[str, list[bytes]] = {}
        for item in items:
            key = canonical_instrument_bytes(item.identity)
            risk = risks.get(key)
            if risk is not None:
                groups.setdefault(str(getattr(risk, attribute)), []).append(key)
        for keys in groups.values():
            total = Decimal(sum(bounded[key] for key in keys))
            _scale_to_cap(bounded, tuple(sorted(keys)), min(cap, total), total)
    return bounded


def _apply_gross_cap(
    weights: dict[bytes, Decimal], maximum_gross_weight: Decimal
) -> dict[bytes, Decimal]:
    bounded = dict(weights)
    gross = Decimal(sum(weights.values()))
    _scale_to_cap(
        bounded,
        tuple(sorted(weights)),
        min(maximum_gross_weight, gross),
        gross,
    )
    return bounded


def _scale_to_cap(
    weights: dict[bytes, Decimal],
    keys: tuple[bytes, ...],
    cap: Decimal,
    total: Decimal,
) -> None:
    if total <= cap:
        return
    scale = cap / total
    for key in keys[:-1]:
        weights[key] *= scale
    weights[keys[-1]] = cap - sum(weights[key] for key in keys[:-1])


def _hard_risk_breaches(  # noqa: PLR0913,PLR0917 - evaluate each frozen risk envelope.
    target_weights: dict[bytes, Decimal],
    items: tuple[HouseViewItem, ...],
    positions: dict[bytes, EquityPosition],
    risks: dict[bytes, PortfolioRiskInput],
    equity: Decimal,
    policy: BalancedPortfolioPolicy,
    maximum_gross_weight: Decimal,
    maximum_name_weight: Decimal,
    maximum_sector_weight: Decimal,
) -> frozenset[bytes]:
    current = {
        canonical_instrument_bytes(item.identity): _current_weight(
            positions.get(canonical_instrument_bytes(item.identity)), equity
        )
        for item in items
    }
    breached: set[bytes] = set()
    for key, weight in current.items():
        risk = risks.get(key)
        liquidity_cap = (
            None
            if risk is None
            else risk.median_dollar_volume
            * policy.maximum_fraction_of_median_dollar_volume
            / equity
        )
        if weight > maximum_name_weight or (liquidity_cap is not None and weight > liquidity_cap):
            breached.add(key)
    for attribute, cap in (
        ("sector", maximum_sector_weight),
        ("common_cause_group", policy.maximum_common_cause_weight),
        ("correlation_cluster", policy.maximum_correlation_cluster_weight),
    ):
        groups: dict[str, list[bytes]] = {}
        for item in items:
            key = canonical_instrument_bytes(item.identity)
            risk = risks.get(key)
            if risk is not None:
                groups.setdefault(str(getattr(risk, attribute)), []).append(key)
        for keys in groups.values():
            if sum(current[key] for key in keys) > cap:
                breached.update(key for key in keys if current[key] > target_weights[key])
    gross = sum(position.valuation.amount / equity for position in positions.values())
    if gross > maximum_gross_weight:
        breached.update(key for key, weight in current.items() if weight > target_weights[key])
    return frozenset(breached)


def _house_view_item(
    resolution: HouseViewResolution,
    positions: dict[bytes, EquityPosition],
    risks: dict[bytes, PortfolioRiskInput],
    release_evidence: tuple[MaterialEventEvidence, ...],
    evidence_cutoff: UtcInstant,
) -> HouseViewItem:
    key = canonical_instrument_bytes(resolution.identity)
    risk = risks.get(key)
    event_blocked = (
        resolution.stance in (PortfolioStance.LONG, PortfolioStance.HOLD)
        and key not in positions
        and risk is not None
        and any(
            not _event_is_cleared(event, resolution, release_evidence, evidence_cutoff)
            for event in risk.material_events
        )
    )
    direction_eligible = (
        resolution.eligible_for_new_entry
        if resolution.stance in (PortfolioStance.LONG, PortfolioStance.HOLD)
        else resolution.is_position
        if resolution.stance in (PortfolioStance.REDUCE, PortfolioStance.EXIT)
        else False
    )
    return HouseViewItem(
        resolution.identity,
        resolution.request_id,
        resolution.resolution_id,
        resolution.stance,
        resolution.uncertainty,
        direction_eligible,
        event_blocked,
    )


def _event_is_cleared(
    event: MaterialEventRisk,
    resolution: HouseViewResolution,
    release_evidence: tuple[MaterialEventEvidence, ...],
    evidence_cutoff: UtcInstant,
) -> bool:
    """Require typed release evidence cited by this current terminal resolution."""
    expected_kind = "issuer_release" if event.event_type == "company_release" else "official_macro"
    identity = canonical_instrument_bytes(resolution.identity)
    return any(
        evidence.artifact_id in resolution.evidence_artifact_ids
        and evidence.evidence_kind == expected_kind
        and evidence.source_identity == event.source_identity
        and evidence.released_at == event.releases_at
        and evidence.available_at.value <= evidence_cutoff.value
        and (
            expected_kind == "official_macro"
            or identity in {canonical_instrument_bytes(item) for item in evidence.mapped_identities}
        )
        for evidence in release_evidence
    )


def _target_band(  # noqa: PLR0913 - one pure decision over explicit facts.
    target: PortfolioTarget,
    house_view_item: HouseViewItem,
    current_weight: Decimal,
    equity: Decimal,
    policy: BalancedPortfolioPolicy,
    *,
    maximum_name_weight: Decimal,
    hard_risk_breach: bool,
) -> TargetBand:
    lower = max(Decimal(0), target.target_weight - policy.target_band_width)
    upper = min(maximum_name_weight, target.target_weight + policy.target_band_width)
    if house_view_item.event_blocked:
        return TargetBand(
            identity=target.identity,
            target_weight=target.target_weight,
            lower_weight=lower,
            upper_weight=upper,
            current_weight=current_weight,
            adjustment_weight=current_weight,
            trade_eligible=False,
            trade_reason=PortfolioTradeReason.EVENT_BLOCKED,
        )
    if hard_risk_breach:
        return TargetBand(
            identity=target.identity,
            target_weight=target.target_weight,
            lower_weight=lower,
            upper_weight=upper,
            current_weight=current_weight,
            adjustment_weight=target.target_weight,
            trade_eligible=True,
            trade_reason=PortfolioTradeReason.HARD_RISK_BREACH,
        )
    if lower <= current_weight <= upper:
        return TargetBand(
            identity=target.identity,
            target_weight=target.target_weight,
            lower_weight=lower,
            upper_weight=upper,
            current_weight=current_weight,
            adjustment_weight=current_weight,
            trade_eligible=False,
            trade_reason=PortfolioTradeReason.IN_BAND,
        )
    if house_view_item.stance is PortfolioStance.EXIT:
        adjustment = Decimal(0)
        reason = PortfolioTradeReason.THESIS_EXIT
    else:
        adjustment = current_weight + policy.partial_adjustment_fraction * (
            target.target_weight - current_weight
        )
        if current_weight == 0:
            reason = PortfolioTradeReason.THESIS_ENTRY
        elif house_view_item.stance is PortfolioStance.REDUCE:
            reason = PortfolioTradeReason.THESIS_REDUCTION
        else:
            reason = PortfolioTradeReason.BAND_BREACH
    eligible = abs(adjustment - current_weight) * equity >= policy.minimum_executable_notional
    if not eligible:
        reason = PortfolioTradeReason.BELOW_MINIMUM_NOTIONAL
        adjustment = current_weight
    return TargetBand(
        identity=target.identity,
        target_weight=target.target_weight,
        lower_weight=lower,
        upper_weight=upper,
        current_weight=current_weight,
        adjustment_weight=adjustment,
        trade_eligible=eligible,
        trade_reason=reason,
    )


def _current_weight(position: EquityPosition | None, equity: Decimal) -> Decimal:
    return Decimal(0) if position is None else position.valuation.amount / equity


def _house_view_id(request: PortfolioConstructionRequest, items: tuple[HouseViewItem, ...]) -> str:
    return _content_hash(
        {
            "schema_version": 1,
            "run_id": request.run_id,
            "cycle": request.cycle.to_payload(),
            "evidence_cutoff": request.evidence_cutoff.isoformat(),
            "data_regime": request.data_regime,
            "configuration_hash": request.configuration_hash,
            "constitution_version": request.constitution_version,
            "constitution_hash": request.constitution_hash,
            "research_policy_hash": request.research_policy_hash,
            "research_artifact_ids": list(request.research_artifact_ids),
            "memory_event_ids": list(request.memory_event_ids),
            "universe_snapshot_id": request.universe_snapshot_id,
            "policy_id": request.policy.policy_id,
            "input_id": request.inputs.input_id,
            "items": [
                {
                    "identity": item.identity.to_payload(),
                    "request_id": item.request_id,
                    "resolution_id": item.resolution_id,
                    "stance": item.stance.value,
                    "uncertainty": item.uncertainty,
                    "eligible": item.eligible,
                    "event_blocked": item.event_blocked,
                }
                for item in items
            ],
        }
    )


def _house_view_material(house_view: HouseView) -> dict[str, object]:
    return {
        "schema_version": house_view.schema_version,
        "run_id": house_view.run_id,
        "cycle": house_view.cycle.to_payload(),
        "evidence_cutoff": house_view.evidence_cutoff.isoformat(),
        "data_regime": house_view.data_regime,
        "configuration_hash": house_view.configuration_hash,
        "constitution_version": house_view.constitution_version,
        "constitution_hash": house_view.constitution_hash,
        "research_policy_hash": house_view.research_policy_hash,
        "research_artifact_ids": list(house_view.research_artifact_ids),
        "memory_event_ids": list(house_view.memory_event_ids),
        "universe_snapshot_id": house_view.universe_snapshot_id,
        "policy_id": house_view.policy_id,
        "input_id": house_view.input_id,
        "items": [
            {
                "identity": item.identity.to_payload(),
                "request_id": item.request_id,
                "resolution_id": item.resolution_id,
                "stance": item.stance.value,
                "uncertainty": item.uncertainty,
                "eligible": item.eligible,
                "event_blocked": item.event_blocked,
            }
            for item in house_view.items
        ],
    }


def _refused(
    request: PortfolioConstructionRequest,
    reason: PortfolioRefusalReason,
) -> PortfolioConstructionResult:
    return _refusal_result(request.policy.policy_id, request.inputs.input_id, reason)


def _invalid_request_refusal(request: object) -> PortfolioConstructionResult:
    policy = getattr(request, "policy", None)
    inputs = getattr(request, "inputs", None)
    policy_id = (
        policy.policy_id if type(policy) is BalancedPortfolioPolicy else _UNAVAILABLE_POLICY_ID
    )
    input_id = inputs.input_id if type(inputs) is PortfolioInputSet else _UNAVAILABLE_INPUT_ID
    return _refusal_result(policy_id, input_id, PortfolioRefusalReason.INVALID_REQUEST)


def _refusal_result(
    policy_id: str,
    input_id: str,
    reason: PortfolioRefusalReason,
) -> PortfolioConstructionResult:
    provisional = PortfolioConstructionResult(
        None,
        (),
        (),
        Decimal(1),
        policy_id,
        input_id,
        "",
        reason,
    )
    return PortfolioConstructionResult(
        None,
        (),
        (),
        Decimal(1),
        policy_id,
        input_id,
        _content_hash(_result_material(provisional)),
        reason,
    )


def _result_material(result: PortfolioConstructionResult) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_kind": "balanced_portfolio_construction",
        "house_view": (
            None
            if result.house_view is None
            else {
                **_house_view_material(result.house_view),
                "house_view_id": result.house_view.house_view_id,
            }
        ),
        "targets": [
            {
                "identity": item.identity.to_payload(),
                "target_weight": _decimal_text(item.target_weight),
            }
            for item in result.targets
        ],
        "target_bands": [_band_payload(item) for item in result.target_bands],
        "cash_weight": _decimal_text(result.cash_weight),
        "policy_id": result.policy_id,
        "input_id": result.input_id,
        "refusal": None if result.refusal is None else result.refusal.value,
    }


def _parse_house_view(value: object) -> HouseView | None:  # noqa: PLR0911,PLR0912
    fields = _exact_mapping(
        value,
        {
            "schema_version",
            "run_id",
            "cycle",
            "evidence_cutoff",
            "data_regime",
            "configuration_hash",
            "constitution_version",
            "constitution_hash",
            "research_policy_hash",
            "research_artifact_ids",
            "memory_event_ids",
            "universe_snapshot_id",
            "policy_id",
            "input_id",
            "house_view_id",
            "items",
        },
    )
    if fields is None:
        return None
    if type(fields["schema_version"]) is not int:
        return None
    if type(fields["constitution_version"]) is not int:
        return None
    match fields:
        case {
            "schema_version": int() as schema_version,
            "run_id": str() as run_id,
            "cycle": cycle_value,
            "evidence_cutoff": str() as cutoff_text,
            "data_regime": str() as data_regime,
            "configuration_hash": str() as configuration_hash,
            "constitution_version": int() as constitution_version,
            "constitution_hash": str() as constitution_hash,
            "research_policy_hash": str() as research_policy_hash,
            "research_artifact_ids": list() as raw_research_artifact_ids,
            "memory_event_ids": list() as raw_memory_event_ids,
            "universe_snapshot_id": str() as universe_snapshot_id,
            "policy_id": str() as policy_id,
            "input_id": str() as input_id,
            "house_view_id": str() as house_view_id,
            "items": list() as raw_items,
        }:
            pass
        case _:
            return None
    cycle = parse_decision_cycle_identity(cycle_value)
    try:
        evidence_cutoff = UtcInstant.parse(cutoff_text)
    except InvalidUtcInstantError:
        return None
    match cycle:
        case MarketSession() as market_session:
            pass
        case _:
            return None
    research_artifact_ids = tuple(item for item in raw_research_artifact_ids if type(item) is str)
    memory_event_ids = tuple(item for item in raw_memory_event_ids if type(item) is str)
    items: list[HouseViewItem] = []
    for value_item in raw_items:
        item = _exact_mapping(
            value_item,
            {
                "identity",
                "request_id",
                "resolution_id",
                "stance",
                "uncertainty",
                "eligible",
                "event_blocked",
            },
        )
        if item is None:
            return None
        identity = parse_instrument_identity(item["identity"])
        match (
            identity,
            item["request_id"],
            item["resolution_id"],
            item["stance"],
            item["uncertainty"],
            item["eligible"],
            item["event_blocked"],
        ):
            case (
                EquityInstrumentIdentity() as parsed_identity,
                str() as request_id,
                str() as resolution_id,
                str() as stance_value,
                str() as uncertainty,
                bool() as eligible,
                bool() as event_blocked,
            ):
                pass
            case _:
                return None
        try:
            stance = PortfolioStance(stance_value)
        except ValueError:
            return None
        try:
            items.append(
                HouseViewItem(
                    parsed_identity,
                    request_id,
                    resolution_id,
                    stance,
                    uncertainty,
                    eligible,
                    event_blocked,
                )
            )
        except ValueError:
            return None
    try:
        return HouseView(
            schema_version,
            run_id,
            market_session,
            evidence_cutoff,
            data_regime,
            configuration_hash,
            constitution_version,
            constitution_hash,
            research_policy_hash,
            research_artifact_ids,
            memory_event_ids,
            universe_snapshot_id,
            policy_id,
            input_id,
            house_view_id,
            tuple(items),
        )
    except ValueError:
        return None


def _parse_target(value: object) -> PortfolioTarget | None:
    fields = _exact_mapping(value, {"identity", "target_weight"})
    if fields is None:
        return None
    identity = parse_instrument_identity(fields["identity"])
    weight = _plain_decimal(fields["target_weight"])
    match identity, weight:
        case EquityInstrumentIdentity() as parsed_identity, Decimal() as parsed_weight:
            pass
        case _:
            return None
    try:
        return PortfolioTarget(parsed_identity, parsed_weight)
    except (TypeError, ValueError):
        return None


def _parse_band(value: object) -> TargetBand | None:
    fields = _exact_mapping(
        value,
        {
            "identity",
            "target_weight",
            "lower_weight",
            "upper_weight",
            "current_weight",
            "adjustment_weight",
            "trade_eligible",
            "trade_reason",
        },
    )
    if fields is None:
        return None
    identity = parse_instrument_identity(fields["identity"])
    decimals = tuple(
        _plain_decimal(fields[name])
        for name in (
            "target_weight",
            "lower_weight",
            "upper_weight",
            "current_weight",
            "adjustment_weight",
        )
    )
    reason_value = fields["trade_reason"]
    if type(reason_value) is not str:
        return None
    try:
        reason = PortfolioTradeReason(reason_value)
    except (TypeError, ValueError):
        return None
    match (*decimals, identity, fields["trade_eligible"]):
        case (
            Decimal() as target,
            Decimal() as lower,
            Decimal() as upper,
            Decimal() as current,
            Decimal() as adjustment,
            EquityInstrumentIdentity() as parsed_identity,
            bool() as trade_eligible,
        ):
            pass
        case _:
            return None
    try:
        return TargetBand(
            identity=parsed_identity,
            target_weight=target,
            lower_weight=lower,
            upper_weight=upper,
            current_weight=current,
            adjustment_weight=adjustment,
            trade_eligible=trade_eligible,
            trade_reason=reason,
        )
    except ValueError:
        return None


def _band_payload(item: TargetBand) -> dict[str, object]:
    return {
        "identity": item.identity.to_payload(),
        "target_weight": _decimal_text(item.target_weight),
        "lower_weight": _decimal_text(item.lower_weight),
        "upper_weight": _decimal_text(item.upper_weight),
        "current_weight": _decimal_text(item.current_weight),
        "adjustment_weight": _decimal_text(item.adjustment_weight),
        "trade_eligible": item.trade_eligible,
        "trade_reason": item.trade_reason.value,
    }


def _risk_input_payload(item: PortfolioRiskInput) -> dict[str, object]:
    return {
        "identity": item.identity.to_payload(),
        "price": _decimal_text(item.price),
        "price_unit": item.price_unit,
        "adjusted_closes": [
            {
                "session": close.session.isoformat(),
                "observed_at": close.observed_at.isoformat(),
                "available_at": close.available_at.isoformat(),
                "source_identity": close.source_identity,
                "price": _decimal_text(close.price),
            }
            for close in item.adjusted_closes
        ],
        "sector": item.sector,
        "median_dollar_volume": _decimal_text(item.median_dollar_volume),
        "liquidity_unit": item.liquidity_unit,
        "common_cause_group": item.common_cause_group,
        "correlation_cluster": item.correlation_cluster,
        "material_events": [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "releases_at": event.releases_at.isoformat(),
                "source_identity": event.source_identity,
                "calendar_available_at": event.calendar_available_at.isoformat(),
            }
            for event in item.material_events
        ],
    }


def _input_set_material(  # noqa: PLR0913,PLR0917 - every input authority pin is explicit.
    position_snapshot: PositionSnapshot,
    cash: Decimal,
    cash_currency: str,
    source_identity: str,
    observed_at: UtcInstant,
    available_at: UtcInstant,
    data_regime: str,
    risk_inputs: tuple[PortfolioRiskInput, ...],
) -> dict[str, object]:
    return {
        "position_snapshot_hash": position_snapshot.fingerprint,
        "cash": _decimal_text(cash),
        "cash_currency": cash_currency,
        "source_identity": source_identity,
        "observed_at": observed_at.isoformat(),
        "available_at": available_at.isoformat(),
        "data_regime": data_regime,
        "session_calendar_id": _SESSION_CALENDAR_ID,
        "risk_inputs": [_risk_input_payload(item) for item in risk_inputs],
    }


def _is_pinned_regular_session(trading_date: date) -> bool:
    return (
        trading_date.year == _SESSION_CALENDAR_YEAR
        and trading_date.weekday() < _WEEKEND_START_DAY
        and trading_date not in _SESSION_CALENDAR_HOLIDAYS
    )


def _close_sessions_are_consecutive(closes: tuple[AdjustedClose, ...]) -> bool:
    sessions = tuple(close.session.trading_date for close in closes)
    for earlier, later in pairwise(sessions):
        expected = earlier + timedelta(days=1)
        while expected.year == _SESSION_CALENDAR_YEAR and not _is_pinned_regular_session(expected):
            expected += timedelta(days=1)
        if expected != later:
            return False
    return True


def _input_set_fields_are_valid(  # noqa: PLR0913,PLR0917 - validate every explicit authority pin.
    position_snapshot: object,
    cash: object,
    cash_currency: object,
    source_identity: object,
    observed_at: object,
    available_at: object,
    data_regime: object,
    risk_inputs: object,
) -> bool:
    return (
        type(position_snapshot) is PositionSnapshot
        and type(cash) is Decimal
        and cash.is_finite()
        and cash >= 0
        and cash_currency == "USD"
        and type(source_identity) is str
        and bool(source_identity)
        and type(observed_at) is UtcInstant
        and type(available_at) is UtcInstant
        and available_at.value >= observed_at.value
        and type(data_regime) is str
        and bool(data_regime)
        and type(risk_inputs) is tuple
        and all(type(item) is PortfolioRiskInput for item in risk_inputs)
        and all(
            close.available_at.value <= available_at.value
            for risk in risk_inputs
            for close in risk.adjusted_closes
        )
        and tuple(canonical_instrument_bytes(item.identity) for item in risk_inputs)
        == tuple(sorted({canonical_instrument_bytes(item.identity) for item in risk_inputs}))
    )


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    _, untrimmed_digits, raw_exponent = value.as_tuple()
    exponent = int(raw_exponent)
    last_nonzero = max(index for index, digit in enumerate(untrimmed_digits) if digit != 0)
    digits = untrimmed_digits[: last_nonzero + 1]
    exponent += len(untrimmed_digits) - len(digits)
    text = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        return text + "0" * exponent
    point = len(text) + exponent
    return f"0.{('0' * -point)}{text}" if point < 1 else f"{text[:point]}.{text[point:]}"


def _sum_decimals(values: Iterable[Decimal]) -> Decimal:
    with localcontext(_PORTFOLIO_DECIMAL_CONTEXT):
        total = Decimal(0)
        for value in values:
            total += value
        return total


def _exact_product(left: Decimal, right: Decimal) -> Decimal:
    precision = len(left.as_tuple().digits) + len(right.as_tuple().digits)
    with localcontext(Context(prec=precision)):
        return left * right


def _content_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _is_hash(value: object) -> TypeGuard[str]:
    return (
        type(value) is str
        and len(value) == _HASH_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _exact_mapping(value: object, fields: set[str]) -> dict[str, object] | None:
    if (
        type(value) is not dict
        or set(value) != fields
        or any(type(key) is not str for key in value)
    ):
        return None
    return value


def _plain_decimal(value: object) -> Decimal | None:
    if type(value) is not str:
        return None
    if not 1 <= len(value) <= _MAXIMUM_DECIMAL_TEXT_LENGTH:
        return None
    try:
        parsed = Decimal(value)
    except ArithmeticError:
        return None
    if not parsed.is_finite():
        return None
    if _decimal_text(parsed) != value:
        return None
    return parsed
