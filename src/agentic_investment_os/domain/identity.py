"""Define canonical instrument and decision-cycle identities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import assert_never

from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant

__all__ = (
    "AssetClass",
    "CryptoDecisionWindow",
    "CryptoSpotInstrumentIdentity",
    "DecisionCycleIdentity",
    "EquityInstrumentIdentity",
    "ExerciseStyle",
    "InstrumentAlias",
    "InstrumentIdentity",
    "ListedOptionInstrumentIdentity",
    "MarketSession",
    "OptionRight",
    "canonical_cycle_bytes",
    "canonical_instrument_bytes",
    "parse_decision_cycle_identity",
    "parse_instrument_alias",
    "parse_instrument_identity",
)

_SCHEMA_VERSION = 1
_CATALOG_NAMESPACE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_CATALOG_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_VENUE = re.compile(r"[A-Z][A-Z0-9._:-]{0,31}\Z")
_CURRENCY = re.compile(r"[A-Z][A-Z0-9]{1,11}\Z")
_TERMS_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_ALIAS_NAMESPACE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_PLAIN_DECIMAL = re.compile(r"-?[0-9]+(?:\.[0-9]+)?\Z")
_MAXIMUM_DECIMAL_TEXT_LENGTH = 64
_MAXIMUM_ALIAS_LENGTH = 256
_MAXIMUM_DELIVERABLE_LENGTH = 512
_INVALID_ALIAS = "invalid instrument alias"
_INVALID_EQUITY_IDENTITY = "invalid equity listing venue"
_INVALID_CRYPTO_IDENTITY = "invalid crypto spot identity"
_INVALID_OPTION_IDENTITY = "invalid listed option identity"
_INVALID_MARKET_SESSION = "invalid market session date"
_INVALID_CRYPTO_WINDOW = "invalid crypto decision window"
_INVALID_CATALOG_IDENTITY = "invalid catalog identity"


class AssetClass(StrEnum):
    """Name the closed asset classes represented by durable identities."""

    US_EQUITY = "us_equity"
    CRYPTO_SPOT = "crypto_spot"
    LISTED_OPTION = "listed_option"


class OptionRight(StrEnum):
    """Identify the right carried by a listed option contract."""

    CALL = "call"
    PUT = "put"


class ExerciseStyle(StrEnum):
    """Identify the exercise convention carried by an option contract."""

    AMERICAN = "american"
    EUROPEAN = "european"


@dataclass(frozen=True, slots=True)
class InstrumentAlias:
    """Retain a source alias as provenance without making it an identity key."""

    namespace: str
    value: str

    def __post_init__(self) -> None:
        if (
            _ALIAS_NAMESPACE.fullmatch(self.namespace) is None
            or not self.value
            or len(self.value) > _MAXIMUM_ALIAS_LENGTH
        ):
            raise ValueError(_INVALID_ALIAS)

    def to_payload(self) -> dict[str, object]:
        return {"namespace": self.namespace, "value": self.value}


@dataclass(frozen=True, slots=True)
class EquityInstrumentIdentity:
    """Identify one US-equity listing independently of its display symbol."""

    catalog_namespace: str
    catalog_id: str
    listing_venue: str

    def __post_init__(self) -> None:
        _validate_catalog(self.catalog_namespace, self.catalog_id)
        if _VENUE.fullmatch(self.listing_venue) is None:
            raise ValueError(_INVALID_EQUITY_IDENTITY)

    @property
    def asset_class(self) -> AssetClass:
        return AssetClass.US_EQUITY

    def to_payload(self) -> dict[str, object]:
        return _identity_envelope(
            asset_class=self.asset_class,
            catalog_namespace=self.catalog_namespace,
            catalog_id=self.catalog_id,
            payload={"listing_venue": self.listing_venue},
        )


@dataclass(frozen=True, slots=True)
class CryptoSpotInstrumentIdentity:
    """Identify one spot pair at one execution venue."""

    catalog_namespace: str
    catalog_id: str
    base_currency: str
    quote_currency: str
    execution_venue: str

    def __post_init__(self) -> None:
        _validate_catalog(self.catalog_namespace, self.catalog_id)
        if (
            _CURRENCY.fullmatch(self.base_currency) is None
            or _CURRENCY.fullmatch(self.quote_currency) is None
            or self.base_currency == self.quote_currency
            or _VENUE.fullmatch(self.execution_venue) is None
        ):
            raise ValueError(_INVALID_CRYPTO_IDENTITY)

    @property
    def asset_class(self) -> AssetClass:
        return AssetClass.CRYPTO_SPOT

    def to_payload(self) -> dict[str, object]:
        return _identity_envelope(
            asset_class=self.asset_class,
            catalog_namespace=self.catalog_namespace,
            catalog_id=self.catalog_id,
            payload={
                "base_currency": self.base_currency,
                "execution_venue": self.execution_venue,
                "quote_currency": self.quote_currency,
            },
        )


@dataclass(frozen=True, slots=True)
class ListedOptionInstrumentIdentity:
    """Identify one listed option and its immutable contract terms."""

    catalog_namespace: str
    catalog_id: str
    underlying: EquityInstrumentIdentity
    expiration: date
    right: OptionRight
    exercise_style: ExerciseStyle
    strike_amount: Decimal
    strike_currency: str
    terms_version: str
    multiplier: Decimal
    deliverable: str

    def __post_init__(self) -> None:
        _validate_catalog(self.catalog_namespace, self.catalog_id)
        if (
            type(self.underlying) is not EquityInstrumentIdentity
            or type(self.expiration) is not date
            or not isinstance(self.right, OptionRight)
            or not isinstance(self.exercise_style, ExerciseStyle)
            or not _is_positive_decimal(self.strike_amount)
            or _CURRENCY.fullmatch(self.strike_currency) is None
            or _TERMS_VERSION.fullmatch(self.terms_version) is None
            or not _is_positive_decimal(self.multiplier)
            or not self.deliverable
            or len(self.deliverable) > _MAXIMUM_DELIVERABLE_LENGTH
        ):
            raise ValueError(_INVALID_OPTION_IDENTITY)

    @property
    def asset_class(self) -> AssetClass:
        return AssetClass.LISTED_OPTION

    def to_payload(self) -> dict[str, object]:
        return _identity_envelope(
            asset_class=self.asset_class,
            catalog_namespace=self.catalog_namespace,
            catalog_id=self.catalog_id,
            payload={
                "deliverable": self.deliverable,
                "exercise_style": self.exercise_style.value,
                "expiration": self.expiration.isoformat(),
                "multiplier": _decimal_text(self.multiplier),
                "right": self.right.value,
                "strike_amount": _decimal_text(self.strike_amount),
                "strike_currency": self.strike_currency,
                "terms_version": self.terms_version,
                "underlying": self.underlying.to_payload(),
            },
        )


type InstrumentIdentity = (
    EquityInstrumentIdentity | CryptoSpotInstrumentIdentity | ListedOptionInstrumentIdentity
)


@dataclass(frozen=True, slots=True)
class MarketSession:
    """Identify one US-equity Decision Cycle by NYSE trading date."""

    trading_date: date

    def __post_init__(self) -> None:
        if type(self.trading_date) is not date:
            raise ValueError(_INVALID_MARKET_SESSION)

    @property
    def asset_class(self) -> AssetClass:
        return AssetClass.US_EQUITY

    def isoformat(self) -> str:
        return self.trading_date.isoformat()

    def to_payload(self) -> dict[str, object]:
        return _cycle_envelope(
            asset_class=self.asset_class,
            cycle_type="market_session",
            payload={"trading_date": self.trading_date.isoformat()},
        )


@dataclass(frozen=True, slots=True)
class CryptoDecisionWindow:
    """Identify one reserved UTC crypto decision interval."""

    starts_at: UtcInstant
    ends_at: UtcInstant

    def __post_init__(self) -> None:
        if not _is_valid_crypto_window(self.starts_at, self.ends_at):
            raise ValueError(_INVALID_CRYPTO_WINDOW)

    @property
    def asset_class(self) -> AssetClass:
        return AssetClass.CRYPTO_SPOT

    def to_payload(self) -> dict[str, object]:
        return _cycle_envelope(
            asset_class=self.asset_class,
            cycle_type="crypto_decision_window",
            payload={
                "ends_at": self.ends_at.isoformat(),
                "starts_at": self.starts_at.isoformat(),
            },
        )


type DecisionCycleIdentity = MarketSession | CryptoDecisionWindow


def canonical_instrument_bytes(identity: InstrumentIdentity) -> bytes:
    """Return the canonical durable identity bytes used for joins and ordering."""
    return _canonical_json(identity.to_payload())


def canonical_cycle_bytes(identity: DecisionCycleIdentity) -> bytes:
    """Return the canonical durable Decision Cycle identity bytes."""
    return _canonical_json(identity.to_payload())


def parse_instrument_alias(value: object) -> InstrumentAlias | None:
    """Validate one hostile alias representation."""
    fields = _mapping(value, frozenset({"namespace", "value"}))
    if fields is None or type(fields["namespace"]) is not str or type(fields["value"]) is not str:
        return None
    try:
        return InstrumentAlias(fields["namespace"], fields["value"])
    except ValueError:
        return None


def parse_instrument_identity(  # noqa: PLR0911 - reject each hostile envelope field directly.
    value: object,
) -> InstrumentIdentity | None:
    """Validate a discriminator before constructing one typed identity variant."""
    fields = _mapping(
        value,
        frozenset(
            {
                "asset_class",
                "catalog_id",
                "catalog_namespace",
                "payload",
                "payload_schema_version",
                "schema_version",
            }
        ),
    )
    if fields is None or not _is_schema_version(fields["schema_version"]):
        return None
    if not _is_schema_version(fields["payload_schema_version"]):
        return None
    asset_class = _asset_class(fields["asset_class"])
    catalog_namespace = fields["catalog_namespace"]
    catalog_id = fields["catalog_id"]
    if asset_class is None:
        return None
    if type(catalog_namespace) is not str:
        return None
    if type(catalog_id) is not str:
        return None
    try:
        return _parse_instrument_identity_variant(
            asset_class,
            catalog_namespace,
            catalog_id,
            fields["payload"],
        )
    except ValueError:
        return None


def _parse_instrument_identity_variant(
    asset_class: AssetClass,
    catalog_namespace: str,
    catalog_id: str,
    payload: object,
) -> InstrumentIdentity | None:
    if asset_class is AssetClass.US_EQUITY:
        return _parse_equity_identity(catalog_namespace, catalog_id, payload)
    if asset_class is AssetClass.CRYPTO_SPOT:
        return _parse_crypto_identity(catalog_namespace, catalog_id, payload)
    if asset_class is AssetClass.LISTED_OPTION:
        return _parse_option_identity(catalog_namespace, catalog_id, payload)
    # Static exhaustion protects future enum additions; runtime parsing cannot reach this arm.
    assert_never(asset_class)  # pragma: no cover


def parse_decision_cycle_identity(  # noqa: PLR0911 - each closed variant fails independently.
    value: object,
) -> DecisionCycleIdentity | None:
    """Validate a discriminator before constructing one Decision Cycle variant."""
    fields = _mapping(
        value,
        frozenset(
            {
                "asset_class",
                "cycle_type",
                "payload",
                "payload_schema_version",
                "schema_version",
            }
        ),
    )
    if (
        fields is None
        or not _is_schema_version(fields["schema_version"])
        or not _is_schema_version(fields["payload_schema_version"])
    ):
        return None
    asset_class = _asset_class(fields["asset_class"])
    cycle_type = fields["cycle_type"]
    if asset_class is None or type(cycle_type) is not str:
        return None
    if asset_class is AssetClass.US_EQUITY and cycle_type == "market_session":
        payload = _mapping(fields["payload"], frozenset({"trading_date"}))
        if payload is None:
            return None
        trading_date = _parse_date(payload["trading_date"])
        return None if trading_date is None else MarketSession(trading_date)
    if asset_class is AssetClass.CRYPTO_SPOT and cycle_type == "crypto_decision_window":
        payload = _mapping(fields["payload"], frozenset({"ends_at", "starts_at"}))
        if payload is None:
            return None
        starts_at = _parse_instant(payload["starts_at"])
        ends_at = _parse_instant(payload["ends_at"])
        if starts_at is None:
            return None
        if ends_at is None:
            return None
        if not _is_valid_crypto_window(starts_at, ends_at):
            return None
        return CryptoDecisionWindow(starts_at, ends_at)
    return None


def _parse_equity_identity(
    catalog_namespace: str, catalog_id: str, value: object
) -> EquityInstrumentIdentity | None:
    payload = _mapping(value, frozenset({"listing_venue"}))
    if payload is None or type(payload["listing_venue"]) is not str:
        return None
    return EquityInstrumentIdentity(catalog_namespace, catalog_id, payload["listing_venue"])


def _parse_crypto_identity(
    catalog_namespace: str, catalog_id: str, value: object
) -> CryptoSpotInstrumentIdentity | None:
    payload = _mapping(
        value,
        frozenset({"base_currency", "execution_venue", "quote_currency"}),
    )
    if payload is None:
        return None
    base_currency = payload["base_currency"]
    quote_currency = payload["quote_currency"]
    execution_venue = payload["execution_venue"]
    if (
        type(base_currency) is not str
        or type(quote_currency) is not str
        or type(execution_venue) is not str
    ):
        return None
    return CryptoSpotInstrumentIdentity(
        catalog_namespace,
        catalog_id,
        base_currency,
        quote_currency,
        execution_venue,
    )


def _parse_option_identity(  # noqa: PLR0911 - narrow each hostile option component independently.
    catalog_namespace: str, catalog_id: str, value: object
) -> ListedOptionInstrumentIdentity | None:
    payload = _mapping(
        value,
        frozenset(
            {
                "deliverable",
                "exercise_style",
                "expiration",
                "multiplier",
                "right",
                "strike_amount",
                "strike_currency",
                "terms_version",
                "underlying",
            }
        ),
    )
    if payload is None:
        return None
    underlying_value = payload["underlying"]
    underlying_fields = _mapping(
        underlying_value,
        frozenset(
            {
                "asset_class",
                "catalog_id",
                "catalog_namespace",
                "payload",
                "payload_schema_version",
                "schema_version",
            }
        ),
    )
    if (
        underlying_fields is None
        or type(underlying_fields["asset_class"]) is not str
        or underlying_fields["asset_class"] != AssetClass.US_EQUITY.value
    ):
        return None
    underlying = parse_instrument_identity(underlying_value)
    expiration = _parse_date(payload["expiration"])
    right = _enum_value(OptionRight, payload["right"])
    exercise_style = _enum_value(ExerciseStyle, payload["exercise_style"])
    strike_amount = _parse_decimal(payload["strike_amount"])
    multiplier = _parse_decimal(payload["multiplier"])
    strike_currency = payload["strike_currency"]
    terms_version = payload["terms_version"]
    deliverable = payload["deliverable"]
    if type(underlying) is not EquityInstrumentIdentity:
        return None
    if expiration is None:
        return None
    if right is None:
        return None
    if exercise_style is None:
        return None
    if strike_amount is None:
        return None
    if multiplier is None:
        return None
    if type(strike_currency) is not str:
        return None
    if type(terms_version) is not str:
        return None
    if type(deliverable) is not str:
        return None
    return ListedOptionInstrumentIdentity(
        catalog_namespace,
        catalog_id,
        underlying,
        expiration,
        right,
        exercise_style,
        strike_amount,
        strike_currency,
        terms_version,
        multiplier,
        deliverable,
    )


def _identity_envelope(
    *,
    asset_class: AssetClass,
    catalog_namespace: str,
    catalog_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return {
        "asset_class": asset_class.value,
        "catalog_id": catalog_id,
        "catalog_namespace": catalog_namespace,
        "payload": payload,
        "payload_schema_version": _SCHEMA_VERSION,
        "schema_version": _SCHEMA_VERSION,
    }


def _cycle_envelope(
    *, asset_class: AssetClass, cycle_type: str, payload: dict[str, object]
) -> dict[str, object]:
    return {
        "asset_class": asset_class.value,
        "cycle_type": cycle_type,
        "payload": payload,
        "payload_schema_version": _SCHEMA_VERSION,
        "schema_version": _SCHEMA_VERSION,
    }


def _validate_catalog(namespace: str, catalog_id: str) -> None:
    if (
        type(namespace) is not str
        or _CATALOG_NAMESPACE.fullmatch(namespace) is None
        or type(catalog_id) is not str
        or _CATALOG_ID.fullmatch(catalog_id) is None
    ):
        raise ValueError(_INVALID_CATALOG_IDENTITY)


def _asset_class(value: object) -> AssetClass | None:
    if type(value) is not str:
        return None
    try:
        return AssetClass(value)
    except ValueError:
        return None


def _is_schema_version(value: object) -> bool:
    return type(value) is int and value == _SCHEMA_VERSION


def _enum_value[T: StrEnum](enum_type: type[T], value: object) -> T | None:
    if type(value) is not str:
        return None
    try:
        return enum_type(value)
    except ValueError:
        return None


def _mapping(value: object, fields: frozenset[str]) -> dict[str, object] | None:
    if type(value) is not dict:
        return None
    result: dict[str, object] = {}
    for key, item in value.items():
        if type(key) is not str:
            return None
        result[key] = item
    return result if set(result) == fields else None


def _parse_date(value: object) -> date | None:
    if type(value) is not str:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _parse_instant(value: object) -> UtcInstant | None:
    try:
        return UtcInstant.parse(value)
    except InvalidUtcInstantError:
        return None


def _parse_decimal(  # noqa: PLR0911 - reject each hostile decimal property independently.
    value: object,
) -> Decimal | None:
    if type(value) is not str or len(value) > _MAXIMUM_DECIMAL_TEXT_LENGTH:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    if not parsed.is_finite():
        return None
    if _PLAIN_DECIMAL.fullmatch(value) is None:
        return None
    if _decimal_text(parsed) != value:
        return None
    if not _is_positive_decimal(parsed):
        return None
    return parsed


def _is_positive_decimal(value: Decimal) -> bool:
    return type(value) is Decimal and value.is_finite() and value > 0


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized.is_signed():
        raise ValueError
    digits = "".join(str(digit) for digit in normalized.as_tuple().digits)
    decimal_position = normalized.adjusted() + 1
    if decimal_position <= 0:
        unsigned = "0." + "0" * -decimal_position + digits
    elif decimal_position >= len(digits):
        unsigned = digits + "0" * (decimal_position - len(digits))
    else:
        unsigned = digits[:decimal_position] + "." + digits[decimal_position:]
    return unsigned


def _is_valid_crypto_window(starts_at: object, ends_at: object) -> bool:
    if type(starts_at) is not UtcInstant or type(ends_at) is not UtcInstant:
        return False
    try:
        starts_at.isoformat()
        ends_at.isoformat()
    except InvalidUtcInstantError:
        return False
    return starts_at.value < ends_at.value


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
