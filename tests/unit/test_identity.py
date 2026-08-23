from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import cast, override

import pytest

from agentic_investment_os.domain.identity import (
    AssetClass,
    CryptoDecisionWindow,
    CryptoSpotInstrumentIdentity,
    EquityInstrumentIdentity,
    ExerciseStyle,
    InstrumentAlias,
    InstrumentIdentity,
    ListedOptionInstrumentIdentity,
    MarketSession,
    OptionRight,
    canonical_cycle_bytes,
    canonical_instrument_bytes,
    parse_decision_cycle_identity,
    parse_instrument_alias,
    parse_instrument_identity,
)
from agentic_investment_os.domain.temporal import UtcInstant
from tests._universe import mutable_mapping


def _equity_payload() -> dict[str, object]:
    return EquityInstrumentIdentity(
        catalog_namespace="alpaca-paper",
        catalog_id="asset-aapl",
        listing_venue="NASDAQ",
    ).to_payload()


def _crypto_payload() -> dict[str, object]:
    return CryptoSpotInstrumentIdentity(
        catalog_namespace="alpaca-paper",
        catalog_id="asset-btc-usd",
        base_currency="BTC",
        quote_currency="USD",
        execution_venue="ALPACA",
    ).to_payload()


def _option_payload() -> dict[str, object]:
    equity = EquityInstrumentIdentity("alpaca-paper", "asset-aapl", "NASDAQ")
    return ListedOptionInstrumentIdentity(
        catalog_namespace="alpaca-paper",
        catalog_id="contract-aapl-call",
        underlying=equity,
        expiration=date(2026, 9, 18),
        right=OptionRight.CALL,
        exercise_style=ExerciseStyle.AMERICAN,
        strike_amount=Decimal(250),
        strike_currency="USD",
        terms_version="standard-100-share-v1",
        multiplier=Decimal(100),
        deliverable="100 AAPL shares",
    ).to_payload()


def test_instrument_identity_round_trips_without_a_display_symbol_key() -> None:
    equity = EquityInstrumentIdentity(
        catalog_namespace="alpaca-paper",
        catalog_id="asset-aapl",
        listing_venue="NASDAQ",
    )
    crypto = CryptoSpotInstrumentIdentity(
        catalog_namespace="alpaca-paper",
        catalog_id="asset-btc-usd",
        base_currency="BTC",
        quote_currency="USD",
        execution_venue="ALPACA",
    )
    option = ListedOptionInstrumentIdentity(
        catalog_namespace="alpaca-paper",
        catalog_id="contract-aapl-call",
        underlying=equity,
        expiration=date(2026, 9, 18),
        right=OptionRight.CALL,
        exercise_style=ExerciseStyle.AMERICAN,
        strike_amount=Decimal(250),
        strike_currency="USD",
        terms_version="standard-100-share-v1",
        multiplier=Decimal(100),
        deliverable="100 AAPL shares",
    )

    for identity in (equity, crypto, option):
        payload = identity.to_payload()
        assert "symbol" not in payload
        assert parse_instrument_identity(payload) == identity
        assert canonical_instrument_bytes(identity) == canonical_instrument_bytes(identity)


def test_decision_cycle_identity_round_trips_closed_variants() -> None:
    market_session = MarketSession(date(2026, 8, 21))
    crypto_window = CryptoDecisionWindow(
        starts_at=UtcInstant.from_datetime(datetime(2026, 8, 22, tzinfo=UTC)),
        ends_at=UtcInstant.from_datetime(datetime(2026, 8, 23, tzinfo=UTC)),
    )

    assert market_session.asset_class is AssetClass.US_EQUITY
    assert crypto_window.asset_class is AssetClass.CRYPTO_SPOT
    assert parse_decision_cycle_identity(market_session.to_payload()) == market_session
    assert parse_decision_cycle_identity(crypto_window.to_payload()) == crypto_window
    assert canonical_cycle_bytes(market_session) != canonical_cycle_bytes(crypto_window)


@pytest.mark.parametrize("value", [True, 1.0])
@pytest.mark.parametrize("field", ["schema_version", "payload_schema_version"])
def test_identity_parsers_reject_non_integer_version_lookalikes(
    field: str,
    value: object,
) -> None:
    instrument = _equity_payload()
    instrument[field] = value
    cycle = MarketSession(date(2026, 8, 21)).to_payload()
    cycle[field] = value

    assert parse_instrument_identity(instrument) is None
    assert parse_decision_cycle_identity(cycle) is None


def test_identity_parsers_reject_unknown_or_mixed_variants() -> None:
    equity = EquityInstrumentIdentity(
        catalog_namespace="alpaca-paper",
        catalog_id="asset-aapl",
        listing_venue="NASDAQ",
    ).to_payload()
    equity["asset_class"] = "unknown"
    cycle = MarketSession(date(2026, 8, 21)).to_payload()
    cycle["payload"] = {"trading_date": "2026-08-21", "starts_at": "later"}

    assert parse_instrument_identity(equity) is None
    assert parse_decision_cycle_identity(cycle) is None


def test_crypto_decision_window_requires_canonical_utc_boundaries() -> None:
    non_utc = timezone(timedelta(hours=10))
    with pytest.raises(ValueError, match="invalid crypto decision window"):
        CryptoDecisionWindow(
            starts_at=cast("UtcInstant", datetime(2026, 8, 22, tzinfo=non_utc)),
            ends_at=UtcInstant.from_datetime(datetime(2026, 8, 23, tzinfo=non_utc)),
        )
    forged = object.__new__(UtcInstant)
    object.__setattr__(
        forged,
        "value",
        datetime(2026, 8, 22),  # noqa: DTZ001
    )
    with pytest.raises(ValueError, match="invalid crypto decision window"):
        CryptoDecisionWindow(
            starts_at=forged,
            ends_at=UtcInstant.from_datetime(datetime(2026, 8, 23, tzinfo=UTC)),
        )

    payload = {
        "schema_version": 1,
        "payload_schema_version": 1,
        "asset_class": "crypto_spot",
        "cycle_type": "crypto_decision_window",
        "payload": {
            "starts_at": "2026-08-22T10:00:00+10:00",
            "ends_at": "2026-08-23T10:00:00+10:00",
        },
    }
    assert parse_decision_cycle_identity(payload) is None


@pytest.mark.parametrize(
    "value",
    [
        None,
        {"namespace": "alpaca-symbol"},
        {"namespace": 1, "value": "AAPL"},
        {"namespace": "INVALID", "value": "AAPL"},
        {"namespace": "alpaca-symbol", "value": ""},
        {1: "alpaca-symbol", "value": "AAPL"},
    ],
)
def test_instrument_alias_refuses_hostile_shapes(value: object) -> None:
    assert parse_instrument_alias(value) is None


def test_identity_value_objects_reject_invalid_direct_construction() -> None:
    equity = EquityInstrumentIdentity("alpaca-paper", "asset-aapl", "NASDAQ")
    invalid_calls = (
        lambda: InstrumentAlias("INVALID", "AAPL"),
        lambda: EquityInstrumentIdentity("alpaca-paper", "asset-aapl", "not-a-venue"),
        lambda: EquityInstrumentIdentity("INVALID", "asset-aapl", "NASDAQ"),
        lambda: CryptoSpotInstrumentIdentity(
            "alpaca-paper", "asset-btc-usd", "USD", "USD", "ALPACA"
        ),
        lambda: ListedOptionInstrumentIdentity(
            "alpaca-paper",
            "contract-aapl-call",
            equity,
            date(2026, 9, 18),
            OptionRight.CALL,
            ExerciseStyle.AMERICAN,
            Decimal(0),
            "USD",
            "standard-v1",
            Decimal(100),
            "100 shares",
        ),
        # The cast deliberately probes runtime validation beyond the static constructor contract.
        lambda: MarketSession(cast("date", "2026-08-21")),
    )

    for call in invalid_calls:
        with pytest.raises(ValueError, match="invalid"):
            call()


@pytest.mark.parametrize(
    "case",
    [
        "not_mapping",
        "non_text_key",
        "missing_field",
        "schema_version",
        "payload_schema_version",
        "asset_class_type",
        "unknown_asset_class",
        "catalog_namespace_type",
        "catalog_id_type",
        "invalid_catalog",
        "invalid_catalog_id",
        "equity_payload_shape",
        "equity_venue_type",
        "equity_venue_value",
        "crypto_payload_shape",
        "crypto_field_type",
        "crypto_same_currency",
        "option_payload_shape",
        "option_underlying",
        "option_expiration_type",
        "option_expiration_value",
        "option_right_type",
        "option_right_value",
        "option_exercise_style",
        "option_strike_shape",
        "option_strike_noncanonical",
        "option_multiplier_zero",
        "option_strike_currency_type",
        "option_terms_type",
        "option_deliverable_type",
    ],
)
def test_instrument_identity_parser_refuses_every_hostile_variant(  # noqa: PLR0912, PLR0915 - table covers the closed hostile-input contract.
    case: str,
) -> None:
    value: object = _equity_payload()
    if case == "not_mapping":
        value = []
    elif case == "non_text_key":
        hostile: dict[object, object] = {}
        hostile.update(_equity_payload())
        hostile[1] = "hostile"
        value = hostile
    elif case == "missing_field":
        payload = _equity_payload()
        del payload["payload"]
        value = payload
    elif case == "schema_version":
        payload = _equity_payload()
        payload["schema_version"] = 2
        value = payload
    elif case == "payload_schema_version":
        payload = _equity_payload()
        payload["payload_schema_version"] = 2
        value = payload
    elif case == "asset_class_type":
        payload = _equity_payload()
        payload["asset_class"] = 1
        value = payload
    elif case == "unknown_asset_class":
        payload = _equity_payload()
        payload["asset_class"] = "unknown"
        value = payload
    elif case == "catalog_namespace_type":
        payload = _equity_payload()
        payload["catalog_namespace"] = 1
        value = payload
    elif case == "catalog_id_type":
        payload = _equity_payload()
        payload["catalog_id"] = 1
        value = payload
    elif case == "invalid_catalog":
        payload = _equity_payload()
        payload["catalog_namespace"] = "INVALID"
        value = payload
    elif case == "invalid_catalog_id":
        payload = _equity_payload()
        payload["catalog_id"] = "invalid id"
        value = payload
    elif case.startswith("equity_"):
        payload = _equity_payload()
        if case == "equity_payload_shape":
            payload["payload"] = []
        elif case == "equity_venue_type":
            mutable_mapping(payload["payload"])["listing_venue"] = 1
        else:
            mutable_mapping(payload["payload"])["listing_venue"] = "invalid"
        value = payload
    elif case.startswith("crypto_"):
        payload = _crypto_payload()
        crypto = mutable_mapping(payload["payload"])
        if case == "crypto_payload_shape":
            payload["payload"] = {"base_currency": "BTC"}
        elif case == "crypto_field_type":
            crypto["base_currency"] = 1
        else:
            crypto["base_currency"] = "USD"
        value = payload
    else:
        payload = _option_payload()
        option = mutable_mapping(payload["payload"])
        if case == "option_payload_shape":
            option["unexpected"] = True
        elif case == "option_underlying":
            option["underlying"] = _crypto_payload()
        elif case == "option_expiration_type":
            option["expiration"] = 1
        elif case == "option_expiration_value":
            option["expiration"] = "not-a-date"
        elif case == "option_right_type":
            option["right"] = 1
        elif case == "option_right_value":
            option["right"] = "unknown"
        elif case == "option_exercise_style":
            option["exercise_style"] = "unknown"
        elif case == "option_strike_shape":
            option["strike_amount"] = 1
        elif case == "option_strike_noncanonical":
            option["strike_amount"] = "0250"
        elif case == "option_multiplier_zero":
            option["multiplier"] = "0"
        elif case == "option_strike_currency_type":
            option["strike_currency"] = 1
        elif case == "option_terms_type":
            option["terms_version"] = 1
        else:
            assert case == "option_deliverable_type"
            option["deliverable"] = 1
        value = payload

    assert parse_instrument_identity(value) is None


@pytest.mark.parametrize(
    "case",
    [
        "not_mapping",
        "non_text_key",
        "missing_field",
        "schema_version",
        "payload_schema_version",
        "asset_class_type",
        "unknown_asset_class",
        "cycle_type_type",
        "market_payload_shape",
        "market_date_type",
        "market_date_value",
        "market_date_noncanonical",
        "crypto_payload_shape",
        "crypto_start_type",
        "crypto_end_type",
        "crypto_start_value",
        "crypto_start_naive",
        "crypto_start_noncanonical",
        "crypto_reversed",
        "unsupported_combination",
        "crossed_market_asset",
    ],
)
def test_decision_cycle_parser_refuses_every_hostile_variant(  # noqa: PLR0912, PLR0915 - table covers the closed hostile-input contract.
    case: str,
) -> None:
    value: object = MarketSession(date(2026, 8, 21)).to_payload()
    if case == "not_mapping":
        value = []
    elif case == "non_text_key":
        hostile_cycle: dict[object, object] = {}
        hostile_cycle.update(mutable_mapping(value))
        hostile_cycle[1] = "hostile"
        value = hostile_cycle
    elif case == "missing_field":
        payload = mutable_mapping(value)
        del payload["payload"]
    elif case in {"schema_version", "payload_schema_version"}:
        mutable_mapping(value)[case] = 2
    elif case == "asset_class_type":
        mutable_mapping(value)["asset_class"] = 1
    elif case == "unknown_asset_class":
        mutable_mapping(value)["asset_class"] = "unknown"
    elif case == "cycle_type_type":
        mutable_mapping(value)["cycle_type"] = 1
    elif case.startswith("market_"):
        payload = mutable_mapping(value)
        market = mutable_mapping(payload["payload"])
        if case == "market_payload_shape":
            market["unexpected"] = True
        elif case == "market_date_type":
            market["trading_date"] = 1
        elif case == "market_date_value":
            market["trading_date"] = "not-a-date"
        else:
            market["trading_date"] = "20260821"
    else:
        value = CryptoDecisionWindow(
            UtcInstant.from_datetime(datetime(2026, 8, 22, tzinfo=UTC)),
            UtcInstant.from_datetime(datetime(2026, 8, 23, tzinfo=UTC)),
        ).to_payload()
        payload = value
        window = mutable_mapping(payload["payload"])
        if case == "crypto_payload_shape":
            window["unexpected"] = True
        elif case == "crypto_start_type":
            window["starts_at"] = 1
        elif case == "crypto_end_type":
            window["ends_at"] = 1
        elif case == "crypto_start_value":
            window["starts_at"] = "not-a-time"
        elif case == "crypto_start_naive":
            window["starts_at"] = "2026-08-22T00:00:00"
        elif case == "crypto_start_noncanonical":
            window["starts_at"] = "2026-08-22T00:00:00Z"
        elif case == "crypto_reversed":
            window["starts_at"] = window["ends_at"]
        elif case == "crossed_market_asset":
            value = MarketSession(date(2026, 8, 21)).to_payload()
            value["asset_class"] = "crypto_spot"
        else:
            assert case == "unsupported_combination"
            payload["asset_class"] = "us_equity"

    assert parse_decision_cycle_identity(deepcopy(value)) is None


def test_identity_parser_rejects_an_unknown_closed_asset_class() -> None:
    payload = _equity_payload()
    payload["asset_class"] = "unexpected-asset-class"

    assert parse_instrument_identity(payload) is None


def test_instrument_parser_rejects_a_non_text_catalog() -> None:
    payload = _equity_payload()
    payload["catalog_namespace"] = 1

    assert parse_instrument_identity(payload) is None


def test_decision_cycle_parser_rejects_a_non_text_discriminator_before_comparison() -> None:
    class SpoofedCycleType:
        @override
        def __eq__(self, other: object) -> bool:
            return other == "market_session"

        @override
        def __hash__(self) -> int:
            return hash("market_session")

    payload = MarketSession(date(2026, 8, 21)).to_payload()
    payload["cycle_type"] = SpoofedCycleType()

    assert parse_decision_cycle_identity(payload) is None


@pytest.mark.parametrize("changed_field", ["base_currency", "quote_currency", "execution_venue"])
def test_crypto_parser_rechecks_values_after_mapping_validation(
    changed_field: str,
) -> None:
    payload = _crypto_payload()
    crypto = mutable_mapping(payload["payload"])
    crypto[changed_field] = 1

    assert parse_instrument_identity(payload) is None


def test_option_decimal_parser_rejects_an_invalid_decimal() -> None:
    payload = _option_payload()
    mutable_mapping(payload["payload"])["strike_amount"] = "not-a-number"

    assert parse_instrument_identity(payload) is None


@pytest.mark.parametrize("value", ["1e2", "NaN"])
def test_option_decimal_parser_rejects_non_plain_or_nonfinite_decimals(value: str) -> None:
    payload = _option_payload()
    mutable_mapping(payload["payload"])["strike_amount"] = value

    assert parse_instrument_identity(payload) is None


def test_crypto_parser_rejects_an_empty_window() -> None:
    payload = CryptoDecisionWindow(
        UtcInstant.from_datetime(datetime(2026, 8, 22, tzinfo=UTC)),
        UtcInstant.from_datetime(datetime(2026, 8, 23, tzinfo=UTC)),
    ).to_payload()
    window = mutable_mapping(payload["payload"])
    window["starts_at"] = window["ends_at"]

    assert parse_decision_cycle_identity(payload) is None


@pytest.mark.parametrize(
    "field",
    ["underlying", "expiration", "right", "exercise_style", "strike_amount", "multiplier"],
)
def test_option_parser_rejects_invalid_components(
    field: str,
) -> None:
    payload = _option_payload()
    option = mutable_mapping(payload["payload"])
    option[field] = {} if field == "underlying" else "invalid"

    assert parse_instrument_identity(payload) is None


def test_option_parser_rejects_an_invalid_equity_underlying_payload() -> None:
    payload = _option_payload()
    option = mutable_mapping(payload["payload"])
    underlying = mutable_mapping(option["underlying"])
    mutable_mapping(underlying["payload"])["listing_venue"] = "invalid"

    assert parse_instrument_identity(payload) is None


def test_option_identity_accepts_positive_fractional_and_maximum_length_decimals() -> None:
    payload = _option_payload()
    option = mutable_mapping(payload["payload"])
    option["strike_amount"] = "0." + "0" * 61 + "1"
    option["multiplier"] = "0.5"

    parsed = parse_instrument_identity(payload)

    assert isinstance(parsed, ListedOptionInstrumentIdentity)
    assert parsed.strike_amount == Decimal("1e-62")
    assert parsed.multiplier == Decimal("0.5")


def test_option_identity_accepts_a_single_digit_decimal_without_a_decimal_point() -> None:
    payload = _option_payload()
    mutable_mapping(payload["payload"])["strike_amount"] = "1"

    parsed = parse_instrument_identity(payload)

    assert isinstance(parsed, ListedOptionInstrumentIdentity)
    assert parsed.strike_amount == Decimal(1)


def test_option_identity_rejects_a_canonical_negative_decimal() -> None:
    payload = _option_payload()
    mutable_mapping(payload["payload"])["strike_amount"] = "-1"

    assert parse_instrument_identity(payload) is None


def test_option_identity_refuses_a_decimal_over_the_length_limit() -> None:
    payload = _option_payload()
    mutable_mapping(payload["payload"])["strike_amount"] = "0." + "0" * 62 + "1"

    assert parse_instrument_identity(payload) is None


def test_option_identity_refuses_recursive_underlying_envelopes() -> None:
    payload = _option_payload()
    option = mutable_mapping(payload["payload"])
    option["underlying"] = payload

    assert parse_instrument_identity(payload) is None


def test_canonical_identity_bytes_sort_mapping_keys() -> None:
    class UnorderedIdentity:
        def to_payload(self) -> dict[str, object]:
            return {"z": 1, "a": 2}

    # Deliberately violate the static union to verify canonical bytes remain order-independent.
    identity = cast("InstrumentIdentity", UnorderedIdentity())

    assert canonical_instrument_bytes(identity) == b'{"a":2,"z":1}'


def test_option_identity_serializes_canonical_fixed_point_decimals() -> None:
    parsed = parse_instrument_identity(_option_payload())
    assert isinstance(parsed, ListedOptionInstrumentIdentity)
    identity = replace(
        parsed,
        strike_amount=Decimal("1.2500"),
        multiplier=Decimal("100.00"),
    )
    serialized = mutable_mapping(identity.to_payload()["payload"])
    assert serialized["strike_amount"] == "1.25"
    assert serialized["multiplier"] == "100"
