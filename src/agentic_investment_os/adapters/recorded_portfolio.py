"""Validate keyless recorded portfolio-construction inputs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, TypeGuard

from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
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

if TYPE_CHECKING:
    from agentic_investment_os.domain.universe import PositionSnapshot

__all__ = ("RecordedPortfolioSource",)

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
        "risk_inputs",
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
_CLOSE_FIELDS = frozenset({"observed_at", "price"})
_EVENT_FIELDS = frozenset(
    {
        "event_id",
        "event_type",
        "releases_at",
        "source_identity",
        "calendar_available_at",
        "release_artifact_id",
        "release_available_at",
        "fresh_research_request_id",
        "fresh_research_resolution_id",
    }
)
_BOUNDED_ID = re.compile(r"[a-z0-9][a-z0-9._/-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
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
            root["schema_version"] != 1
            or root["record_kind"] != "portfolio_input_set"
            or type(root["data_regime"]) is not str
            or root["data_regime"] != position_snapshot.data_regime
            or not _is_bounded_id(root["source_identity"])
            or root["cash_currency"] != "USD"
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
    instant = _instant(fields["observed_at"])
    price = _decimal(fields["price"], allow_zero=False)
    if instant is None or price is None:
        return None
    return AdjustedClose(instant, price)


def _material_event(value: object) -> MaterialEventRisk | None:
    fields = _mapping(value, _EVENT_FIELDS)
    if fields is None:
        return None
    releases_at = _instant(fields["releases_at"])
    calendar_available_at = _instant(fields["calendar_available_at"])
    release_available_at = (
        None if fields["release_available_at"] is None else _instant(fields["release_available_at"])
    )
    release_valid, release_artifact_id = _optional_sha256(fields["release_artifact_id"])
    request_valid, fresh_research_request_id = _optional_sha256(fields["fresh_research_request_id"])
    resolution_valid, fresh_research_resolution_id = _optional_sha256(
        fields["fresh_research_resolution_id"]
    )
    if (
        not _is_bounded_id(fields["event_id"])
        or not _is_bounded_id(fields["source_identity"])
        or type(fields["event_type"]) is not str
        or releases_at is None
        or calendar_available_at is None
        or not release_valid
        or not request_valid
        or not resolution_valid
        or (fields["release_available_at"] is not None and release_available_at is None)
    ):
        return None
    try:
        return MaterialEventRisk(
            fields["event_id"],
            fields["event_type"],
            releases_at,
            fields["source_identity"],
            calendar_available_at,
            release_artifact_id,
            release_available_at,
            fresh_research_request_id,
            fresh_research_resolution_id,
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


def _optional_sha256(value: object) -> tuple[bool, str | None]:
    if value is None:
        return True, None
    if type(value) is str and _SHA256.fullmatch(value) is not None:
        return True, value
    return False, None
