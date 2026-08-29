from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from agentic_investment_os.domain.identity import EquityInstrumentIdentity, MarketSession
from agentic_investment_os.domain.lifecycle import PortfolioShadowKind
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.domain.universe import (
    EquityPosition,
    PositionSnapshot,
    PositionValuation,
)
from agentic_investment_os.portfolio.construction import (
    AdjustedClose,
    BalancedPortfolioPolicy,
    HouseViewResolution,
    PortfolioConstructionRequest,
    PortfolioCostInputPolicy,
    PortfolioInputSet,
    PortfolioRiskInput,
    PortfolioSizingMethod,
    PortfolioStance,
    ShadowPortfolioPolicy,
)
from agentic_investment_os.portfolio.shadows import (
    PortfolioCycleResult,
    construct_portfolio_cycle,
)

_ADJUSTED_CLOSE_HISTORY_LENGTH = 21
_WEEKEND_START_DAY = 5
_SYNTHETIC_CUTOFF = UtcInstant.from_datetime(datetime(2026, 8, 21, 20, tzinfo=UTC))
SYNTHETIC_AAPL = EquityInstrumentIdentity("alpaca-paper", "equity-aapl", "NASDAQ")
SYNTHETIC_SPY = EquityInstrumentIdentity("alpaca-paper", "equity-spy", "ARCA")
SYNTHETIC_FORECAST_IDS = ("a" * 64, "b" * 64)


def synthetic_portfolio_cycle(
    *,
    run_id: str = "1" * 64,
    with_authorized_adjustments: bool = True,
    with_authorized_decrease: bool = False,
) -> PortfolioCycleResult:
    """Construct one complete deterministic Balanced cycle and all required shadows."""
    resolutions = (
        (
            _synthetic_resolution(
                SYNTHETIC_AAPL,
                SYNTHETIC_FORECAST_IDS[0],
                stance=(
                    PortfolioStance.REDUCE if with_authorized_decrease else PortfolioStance.LONG
                ),
                is_position=with_authorized_decrease,
            ),
            _synthetic_resolution(SYNTHETIC_SPY, SYNTHETIC_FORECAST_IDS[1]),
        )
        if with_authorized_adjustments
        else ()
    )
    request_ids = tuple(sorted(item.request_id for item in resolutions))
    return construct_portfolio_cycle(
        PortfolioConstructionRequest(
            run_id=run_id,
            cycle=MarketSession(date(2026, 8, 21)),
            evidence_cutoff=_SYNTHETIC_CUTOFF,
            data_regime="alpaca-basic-iex-v1",
            configuration_hash="2" * 64,
            constitution_version=1,
            constitution_hash="3" * 64,
            research_policy_hash="4" * 64,
            research_artifact_ids=tuple(sorted(item.resolution_id for item in resolutions)),
            memory_event_ids=("5" * 64,),
            universe_snapshot_id="7" * 64,
            expected_research_request_ids=request_ids,
            resolutions=resolutions,
            inputs=_synthetic_inputs(with_position=with_authorized_decrease),
            policy=_synthetic_policy(),
            material_event_evidence=(),
        )
    )


def recorded_portfolio_inputs(position_snapshot: PositionSnapshot) -> dict[str, object]:
    """Return one complete synthetic as-of Balanced construction input set."""
    cutoff = datetime(2026, 8, 21, 20, tzinfo=UTC)

    def adjusted_closes(start_price: int) -> list[dict[str, object]]:
        sessions: list[datetime] = []
        cursor = cutoff - timedelta(days=1)
        while len(sessions) < _ADJUSTED_CLOSE_HISTORY_LENGTH:
            if cursor.weekday() < _WEEKEND_START_DAY:
                sessions.append(cursor)
            cursor -= timedelta(days=1)
        return [
            {
                "session": instant.date().isoformat(),
                "observed_at": instant.isoformat(timespec="microseconds"),
                "available_at": (instant + timedelta(minutes=1)).isoformat(timespec="microseconds"),
                "source_identity": "alpaca-iex-adjusted-daily-v1",
                "price": str(start_price + index),
            }
            for index, instant in enumerate(reversed(sessions))
        ]

    def risk_item(
        identity: EquityInstrumentIdentity,
        *,
        start_price: int,
        sector: str,
        common_cause_group: str,
        correlation_cluster: str,
    ) -> dict[str, object]:
        return {
            "identity": identity.to_payload(),
            "price": str(start_price + 20),
            "price_unit": "usd_per_share",
            "adjusted_closes": adjusted_closes(start_price),
            "sector": sector,
            "median_dollar_volume": "100000000",
            "liquidity_unit": "usd_per_day",
            "common_cause_group": common_cause_group,
            "correlation_cluster": correlation_cluster,
            "material_events": [],
        }

    return {
        "schema_version": 1,
        "record_kind": "portfolio_input_set",
        "data_regime": "alpaca-basic-iex-v1",
        "observed_at": "2026-08-21T19:30:00.000000+00:00",
        "available_at": "2026-08-21T19:40:00.000000+00:00",
        "source_identity": "recorded-portfolio-source-v1",
        "position_snapshot_hash": position_snapshot.fingerprint,
        "cash": "100000",
        "cash_currency": "USD",
        "session_calendar_id": "xnys-regular-2026a",
        "risk_inputs": [
            risk_item(
                EquityInstrumentIdentity("alpaca-paper", "equity-aapl", "NASDAQ"),
                start_price=200,
                sector="technology",
                common_cause_group="mega-cap",
                correlation_cluster="market-beta",
            ),
            risk_item(
                EquityInstrumentIdentity("alpaca-paper", "equity-hold", "NYSE"),
                start_price=16,
                sector="industrials",
                common_cause_group="domestic-cycle",
                correlation_cluster="market-beta",
            ),
            risk_item(
                EquityInstrumentIdentity("alpaca-paper", "equity-spy", "ARCA"),
                start_price=500,
                sector="broad-market",
                common_cause_group="market-index",
                correlation_cluster="market-beta",
            ),
        ],
    }


def changed_recorded_portfolio_inputs(position_snapshot: PositionSnapshot) -> dict[str, object]:
    payload = deepcopy(recorded_portfolio_inputs(position_snapshot))
    payload["cash"] = "99999"
    return payload


def _synthetic_resolution(
    identity: EquityInstrumentIdentity,
    resolution_id: str,
    *,
    stance: PortfolioStance = PortfolioStance.LONG,
    is_position: bool = False,
) -> HouseViewResolution:
    return HouseViewResolution(
        identity=identity,
        request_id=resolution_id,
        resolution_id=resolution_id,
        stance=stance,
        uncertainty="low",
        production_authority=True,
        is_position=is_position,
    )


def _synthetic_inputs(*, with_position: bool) -> PortfolioInputSet:
    observed_at = UtcInstant.from_datetime(_SYNTHETIC_CUTOFF.value - timedelta(minutes=30))
    available_at = UtcInstant.from_datetime(_SYNTHETIC_CUTOFF.value - timedelta(minutes=20))
    position_snapshot = PositionSnapshot.create(
        observed_at=observed_at.value,
        available_at=available_at.value,
        data_regime="alpaca-basic-iex-v1",
        source_fingerprint="8" * 64,
        positions=(
            (
                EquityPosition(
                    SYNTHETIC_AAPL,
                    Decimal(100),
                    PositionValuation(
                        Decimal(10000),
                        "USD",
                        "alpaca-paper-market-value",
                    ),
                ),
            )
            if with_position
            else ()
        ),
    )
    assert isinstance(position_snapshot, PositionSnapshot)
    return PortfolioInputSet.create(
        position_snapshot=position_snapshot,
        cash=Decimal(90000) if with_position else Decimal(100000),
        cash_currency="USD",
        source_identity="recorded-portfolio-source-v1",
        observed_at=observed_at,
        available_at=available_at,
        data_regime="alpaca-basic-iex-v1",
        risk_inputs=(
            _synthetic_risk_input(SYNTHETIC_SPY),
            _synthetic_risk_input(SYNTHETIC_AAPL),
        ),
    )


def _synthetic_risk_input(identity: EquityInstrumentIdentity) -> PortfolioRiskInput:
    def close(days: int, price: Decimal) -> AdjustedClose:
        observed_at = UtcInstant.from_datetime(_SYNTHETIC_CUTOFF.value - timedelta(days=days))
        return AdjustedClose(
            MarketSession(observed_at.value.date()),
            observed_at,
            observed_at,
            "alpaca-iex-adjusted-daily-v1",
            price,
        )

    return PortfolioRiskInput(
        identity=identity,
        price=Decimal(100),
        price_unit="usd_per_share",
        adjusted_closes=(
            close(3, Decimal(100)),
            close(2, Decimal(101)),
            close(1, Decimal(100)),
        ),
        sector=("broad-market" if identity == SYNTHETIC_SPY else "technology"),
        median_dollar_volume=Decimal(100000000),
        liquidity_unit="usd_per_day",
        common_cause_group=("market-index" if identity == SYNTHETIC_SPY else "mega-cap"),
        correlation_cluster="market-beta",
        material_events=(),
    )


def _synthetic_policy() -> BalancedPortfolioPolicy:
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
