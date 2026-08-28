from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from agentic_investment_os.domain.identity import EquityInstrumentIdentity

if TYPE_CHECKING:
    from agentic_investment_os.domain.universe import PositionSnapshot


def recorded_portfolio_inputs(position_snapshot: PositionSnapshot) -> dict[str, object]:
    """Return one complete synthetic as-of Balanced construction input set."""
    cutoff = datetime(2026, 8, 21, 20, tzinfo=UTC)

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
            "adjusted_closes": [
                {
                    "observed_at": (cutoff - timedelta(days=21 - index)).isoformat(
                        timespec="microseconds"
                    ),
                    "price": str(start_price + index),
                }
                for index in range(21)
            ],
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
