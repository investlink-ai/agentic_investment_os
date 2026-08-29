"""Validate keyless recorded portfolio-construction inputs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, TypeGuard

from agentic_investment_os.adapters.recorded_universe import parse_recorded_position_snapshot
from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    MarketSession,
    parse_instrument_identity,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.portfolio.construction import (
    AdjustedClose,
    MaterialEventRisk,
    PortfolioInputSet,
    PortfolioRefusalReason,
    PortfolioRiskInput,
)
from agentic_investment_os.portfolio.shadows import (
    PortfolioShadowAccount,
    parse_portfolio_shadow_account,
)

if TYPE_CHECKING:
    from agentic_investment_os.domain.universe import PositionSnapshot

__all__ = (
    "RecordedPortfolioSource",
    "parse_recorded_portfolio_input_set",
    "parse_recorded_portfolio_shadow_account",
)

_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "data_regime",
        "observed_at",
        "available_at",
        "source_identity",
        "position_snapshot_hash",
        "cash",
        "cash_currency",
        "session_calendar_id",
        "risk_inputs",
    }
)
_DURABLE_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "position_snapshot",
        "cash",
        "cash_currency",
        "source_identity",
        "observed_at",
        "available_at",
        "data_regime",
        "session_calendar_id",
        "risk_inputs",
        "input_id",
    }
)
_RISK_FIELDS = frozenset(
    {
        "identity",
        "price",
        "price_unit",
        "adjusted_closes",
        "sector",
        "median_dollar_volume",
        "liquidity_unit",
        "common_cause_group",
        "correlation_cluster",
        "material_events",
    }
)
_CLOSE_FIELDS = frozenset({"session", "observed_at", "available_at", "source_identity", "price"})
_EVENT_FIELDS = frozenset(
    {"event_id", "event_type", "releases_at", "source_identity", "calendar_available_at"}
)
_BOUNDED_ID = re.compile(r"[a-z0-9][a-z0-9._/-]{0,127}")
_PLAIN_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")
_MAXIMUM_RISK_INPUTS = 100
_MINIMUM_ADJUSTED_CLOSES = 3
_MAXIMUM_ADJUSTED_CLOSES = 1_000
_MAXIMUM_MATERIAL_EVENTS = 100
_MAXIMUM_DECIMAL_TEXT_LENGTH = 64


@dataclass(frozen=True, slots=True)
class RecordedPortfolioSource:
    """Translate one hostile recorded input set into portfolio-owned values."""

    recorded: object

    def load(
        self, position_snapshot: PositionSnapshot
    ) -> PortfolioInputSet | PortfolioRefusalReason:
        root = _mapping(self.recorded, _ROOT_FIELDS)
        if root is None:
            return PortfolioRefusalReason.INVALID_REQUEST
        if root["position_snapshot_hash"] != position_snapshot.fingerprint:
            return PortfolioRefusalReason.CONTRADICTORY_INPUT
        if (
            type(root["schema_version"]) is not int
            or root["schema_version"] != 1
            or root["record_kind"] != "portfolio_input_set"
            or type(root["data_regime"]) is not str
            or root["data_regime"] != position_snapshot.data_regime
            or not _is_bounded_id(root["source_identity"])
            or root["cash_currency"] != "USD"
            or root["session_calendar_id"] != "xnys-regular-2026a"
            or type(root["risk_inputs"]) is not list
            or not 1 <= len(root["risk_inputs"]) <= _MAXIMUM_RISK_INPUTS
        ):
            return PortfolioRefusalReason.INVALID_REQUEST
        observed_at = _instant(root["observed_at"])
        available_at = _instant(root["available_at"])
        cash = _decimal(root["cash"], allow_zero=True)
        risks = tuple(_risk_input(item) for item in root["risk_inputs"])
        if (
            observed_at is None
            or available_at is None
            or cash is None
            or any(item is None for item in risks)
        ):
            return PortfolioRefusalReason.INVALID_REQUEST
        try:
            return PortfolioInputSet.create(
                position_snapshot=position_snapshot,
                cash=cash,
                cash_currency="USD",
                source_identity=root["source_identity"],
                observed_at=observed_at,
                available_at=available_at,
                data_regime=root["data_regime"],
                risk_inputs=tuple(item for item in risks if item is not None),
            )
        except ValueError:
            return PortfolioRefusalReason.CONTRADICTORY_INPUT


def parse_recorded_portfolio_input_set(value: object) -> PortfolioInputSet | None:
    """Validate the complete input set embedded in durable shadow accounting."""
    fields = _mapping(value, _DURABLE_ROOT_FIELDS)
    if fields is None:
        return None
    snapshot = parse_recorded_position_snapshot(fields["position_snapshot"])
    if snapshot is None or type(fields["input_id"]) is not str:
        return None
    recorded = {
        "schema_version": fields["schema_version"],
        "record_kind": fields["record_kind"],
        "data_regime": fields["data_regime"],
        "observed_at": fields["observed_at"],
        "available_at": fields["available_at"],
        "source_identity": fields["source_identity"],
        "position_snapshot_hash": snapshot.fingerprint,
        "cash": fields["cash"],
        "cash_currency": fields["cash_currency"],
        "session_calendar_id": fields["session_calendar_id"],
        "risk_inputs": fields["risk_inputs"],
    }
    parsed = RecordedPortfolioSource(recorded).load(snapshot)
    return (
        parsed
        if type(parsed) is PortfolioInputSet
        and parsed.input_id == fields["input_id"]
        and parsed.to_payload() == fields
        else None
    )


def parse_recorded_portfolio_shadow_account(value: object) -> PortfolioShadowAccount | None:
    """Validate a durable shadow and its embedded complete portfolio input set."""
    root = value if type(value) is dict else None
    inputs = (
        None if root is None else parse_recorded_portfolio_input_set(root.get("portfolio_inputs"))
    )
    return (
        None if inputs is None else parse_portfolio_shadow_account(value, portfolio_inputs=inputs)
    )


def _risk_input(value: object) -> PortfolioRiskInput | None:
    fields = _mapping(value, _RISK_FIELDS)
    if fields is None:
        return None
    identity = parse_instrument_identity(fields["identity"])
    price = _decimal(fields["price"], allow_zero=False)
    liquidity = _decimal(fields["median_dollar_volume"], allow_zero=True)
    closes_value = fields["adjusted_closes"]
    if (
        type(identity) is not EquityInstrumentIdentity
        or price is None
        or liquidity is None
        or fields["price_unit"] != "usd_per_share"
        or fields["liquidity_unit"] != "usd_per_day"
        or not _is_bounded_id(fields["sector"])
        or not _is_bounded_id(fields["common_cause_group"])
        or not _is_bounded_id(fields["correlation_cluster"])
        or type(closes_value) is not list
        or not _MINIMUM_ADJUSTED_CLOSES <= len(closes_value) <= _MAXIMUM_ADJUSTED_CLOSES
    ):
        return None
    closes = tuple(_adjusted_close(item) for item in closes_value)
    events_value = fields["material_events"]
    if type(events_value) is not list or len(events_value) > _MAXIMUM_MATERIAL_EVENTS:
        return None
    events = tuple(_material_event(item) for item in events_value)
    if any(item is None for item in closes) or any(item is None for item in events):
        return None
    try:
        return PortfolioRiskInput(
            identity,
            price,
            "usd_per_share",
            tuple(item for item in closes if item is not None),
            fields["sector"],
            liquidity,
            "usd_per_day",
            fields["common_cause_group"],
            fields["correlation_cluster"],
            tuple(item for item in events if item is not None),
        )
    except ValueError:
        return None


def _adjusted_close(value: object) -> AdjustedClose | None:
    fields = _mapping(value, _CLOSE_FIELDS)
    if fields is None:
        return None
    session = _market_session(fields["session"])
    instant = _instant(fields["observed_at"])
    available_at = _instant(fields["available_at"])
    price = _decimal(fields["price"], allow_zero=False)
    if (
        session is None
        or instant is None
        or available_at is None
        or not _is_bounded_id(fields["source_identity"])
        or price is None
    ):
        return None
    try:
        return AdjustedClose(session, instant, available_at, fields["source_identity"], price)
    except ValueError:
        return None


def _material_event(value: object) -> MaterialEventRisk | None:
    fields = _mapping(value, _EVENT_FIELDS)
    if fields is None:
        return None
    releases_at = _instant(fields["releases_at"])
    calendar_available_at = _instant(fields["calendar_available_at"])
    if (
        not _is_bounded_id(fields["event_id"])
        or not _is_bounded_id(fields["source_identity"])
        or type(fields["event_type"]) is not str
        or releases_at is None
        or calendar_available_at is None
    ):
        return None
    try:
        return MaterialEventRisk(
            fields["event_id"],
            fields["event_type"],
            releases_at,
            fields["source_identity"],
            calendar_available_at,
        )
    except (TypeError, ValueError):
        return None


def _mapping(value: object, fields: frozenset[str]) -> dict[str, object] | None:
    if (
        type(value) is not dict
        or set(value) != fields
        or any(type(key) is not str for key in value)
    ):
        return None
    return value


def _instant(value: object) -> UtcInstant | None:
    try:
        return UtcInstant.parse(value)
    except InvalidUtcInstantError:
        return None


def _market_session(value: object) -> MarketSession | None:
    if type(value) is not str:
        return None
    try:
        return MarketSession(date.fromisoformat(value))
    except ValueError:
        return None


def _decimal(value: object, *, allow_zero: bool) -> Decimal | None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= _MAXIMUM_DECIMAL_TEXT_LENGTH
        or _PLAIN_DECIMAL.fullmatch(value) is None
    ):
        return None
    parsed = Decimal(value)
    if not parsed.is_finite() or parsed < 0 or (parsed == 0 and not allow_zero):
        return None
    normalized = format(parsed, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return parsed if normalized == value else None


def _is_bounded_id(value: object) -> TypeGuard[str]:
    return type(value) is str and _BOUNDED_ID.fullmatch(value) is not None
