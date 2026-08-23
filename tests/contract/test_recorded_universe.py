from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

import pytest

from agentic_investment_os.adapters.recorded_universe import (
    RecordedUniverseSource,
    is_alpaca_paper_identity,
    parse_persisted_universe_snapshot,
)
from agentic_investment_os.domain.identity import (
    AssetClass,
    CryptoSpotInstrumentIdentity,
    EquityInstrumentIdentity,
    ExerciseStyle,
    InstrumentIdentity,
    ListedOptionInstrumentIdentity,
    MarketSession,
    OptionRight,
)
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.domain.universe import (
    CryptoSpotInstrument,
    CryptoSpotPosition,
    EquityUniversePolicy,
    ListedOptionInstrument,
    ListedOptionPosition,
    UniverseInputs,
    UniverseRefusal,
    UniverseRefusalCode,
    UniverseSnapshot,
)
from agentic_investment_os.domain.universe import (
    build_universe_snapshot as _build_universe_snapshot,
)
from tests._universe import (
    mutable_list,
    mutable_mapping,
    mutable_mapping_list,
    recorded_universe,
    reseal_recorded_snapshot,
    typed_universe_inputs,
    typed_universe_policy,
)

SHA256_HEX_LENGTH = 64
RECORDED_AT = UtcInstant.from_datetime(datetime(2026, 8, 21, 22, 0, tzinfo=UTC))


class _MappingSubclass(dict[str, object]):
    pass


class _ListSubclass(list[object]):
    pass


class _StringSubclass(str):
    __slots__ = ()


def _snapshot(payload: dict[str, object], name: str) -> dict[str, object]:
    return mutable_mapping(payload[name])


def _raw_items(payload: dict[str, object], name: str) -> list[object]:
    snapshot_payload = mutable_mapping(_snapshot(payload, name)["payload"])
    return mutable_list(snapshot_payload["items"])


def _items(payload: dict[str, object], name: str) -> list[dict[str, object]]:
    return mutable_mapping_list(_raw_items(payload, name))


def _snapshot_payload(payload: dict[str, object], name: str) -> dict[str, object]:
    return mutable_mapping(_snapshot(payload, name)["payload"])


def _item(payload: dict[str, object], name: str, index: int = 0) -> dict[str, object]:
    return _items(payload, name)[index]


def _item_payload(payload: dict[str, object], name: str, index: int = 0) -> dict[str, object]:
    return mutable_mapping(_item(payload, name, index)["payload"])


def _append_disabled_observations(payload: dict[str, object]) -> None:
    underlying = EquityInstrumentIdentity("alpaca-paper", "equity-aapl", "NASDAQ")
    crypto = CryptoSpotInstrumentIdentity("alpaca-paper", "crypto-btc-usd", "BTC", "USD", "ALPACA")
    option = ListedOptionInstrumentIdentity(
        "alpaca-paper",
        "option-aapl-20260918-c250",
        underlying,
        date(2026, 9, 18),
        OptionRight.CALL,
        ExerciseStyle.AMERICAN,
        Decimal(250),
        "USD",
        "standard-v1",
        Decimal(100),
        "100 shares",
    )
    for identity, alias in ((crypto, "BTC/USD"), (option, "AAPL260918C00250000")):
        _items(payload, "instruments").append(
            {
                "schema_version": 1,
                "payload_schema_version": 1,
                "record_kind": "instrument_observation",
                "asset_class": identity.asset_class.value,
                "identity": identity.to_payload(),
                "aliases": [{"namespace": "alpaca-symbol", "value": alias}],
                "payload": {"status": "active", "tradable": True},
            }
        )
    for identity, quantity, unit in (
        (crypto, "0.5", "BTC"),
        (option, "2", "contract"),
    ):
        _items(payload, "positions").append(
            {
                "schema_version": 1,
                "payload_schema_version": 1,
                "record_kind": "position_observation",
                "asset_class": identity.asset_class.value,
                "identity": identity.to_payload(),
                "payload": {
                    "quantity": quantity,
                    "unit": unit,
                    "valuation": {
                        "amount": "100",
                        "currency": "USD",
                        "source": "alpaca-paper-market-value",
                    },
                },
            }
        )
    reseal_recorded_snapshot(payload, "instruments")
    reseal_recorded_snapshot(payload, "positions")


def _valid_persisted_snapshot() -> UniverseSnapshot:
    snapshot = _build(
        "d" * SHA256_HEX_LENGTH,
        typed_universe_inputs(),
        typed_universe_policy(),
        recorded_at=RECORDED_AT,
    )
    assert isinstance(snapshot, UniverseSnapshot)
    return snapshot


def _build(
    run_id: str,
    inputs: UniverseInputs,
    policy: EquityUniversePolicy,
    *,
    recorded_at: UtcInstant,
) -> UniverseSnapshot | UniverseRefusal:
    return _build_universe_snapshot(
        run_id,
        MarketSession(date(2026, 8, 21)),
        inputs,
        policy,
        enabled_asset_classes=(AssetClass.US_EQUITY,),
        recorded_at=recorded_at,
    )


def test_recorded_universe_validates_and_canonicalizes_accepted_inputs() -> None:
    loaded = RecordedUniverseSource(recorded_universe()).load()

    assert isinstance(loaded, UniverseInputs)
    assert loaded.data_regime == "alpaca-basic-iex-v1"
    assert tuple(asset.aliases[0].value for asset in loaded.instrument_snapshot.instruments) == (
        "AAPL",
        "HOLD",
        "OTCX",
        "SPY",
        "TQQQ",
    )
    assert tuple(
        next(
            instrument.aliases[0].value
            for instrument in loaded.instrument_snapshot.instruments
            if instrument.identity == position.identity
        )
        for position in loaded.position_snapshot.positions
    ) == ("HOLD",)
    assert len(loaded.instrument_snapshot.fingerprint) == SHA256_HEX_LENGTH
    assert len(loaded.position_snapshot.fingerprint) == SHA256_HEX_LENGTH


def test_equivalent_provider_offsets_produce_identical_canonical_universe_inputs() -> None:
    utc_payload = recorded_universe()
    offset_payload = recorded_universe()
    offset_payload["evidence_cutoff"] = "2026-08-21T16:00:00.000000-04:00"
    instruments = _snapshot(offset_payload, "instruments")
    instruments["observed_at"] = "2026-08-21T15:30:00.000000-04:00"
    instruments["available_at"] = "2026-08-21T15:35:00.000000-04:00"
    positions = _snapshot(offset_payload, "positions")
    positions["observed_at"] = "2026-08-21T15:45:00.000000-04:00"
    positions["available_at"] = "2026-08-21T15:46:00.000000-04:00"
    reseal_recorded_snapshot(offset_payload, "instruments")
    reseal_recorded_snapshot(offset_payload, "positions")

    utc = RecordedUniverseSource(utc_payload).load()
    offset = RecordedUniverseSource(offset_payload).load()

    assert isinstance(utc, UniverseInputs)
    assert offset == utc
    assert offset.evidence_cutoff.value.tzinfo is UTC
    assert offset.instrument_snapshot.observed_at.value.tzinfo is UTC
    assert offset.instrument_snapshot.available_at.value.tzinfo is UTC
    assert offset.position_snapshot.observed_at.value.tzinfo is UTC
    assert offset.position_snapshot.available_at.value.tzinfo is UTC
    assert offset.to_payload()["evidence_cutoff"] == "2026-08-21T20:00:00.000000+00:00"
    assert offset.instrument_snapshot.to_payload()["observed_at"] == (
        "2026-08-21T19:30:00.000000+00:00"
    )


@pytest.mark.parametrize(
    "case",
    ["alias_collision", "catalog_collision", "underlying_catalog_collision", "missing_alias"],
)
def test_recorded_universe_requires_global_one_to_one_instrument_mappings(case: str) -> None:
    payload = recorded_universe()
    first = _item(payload, "instruments", 0)
    second = _item(payload, "instruments", 1)
    if case == "alias_collision":
        second["aliases"] = deepcopy(first["aliases"])
    elif case == "catalog_collision":
        first_identity = mutable_mapping(first["identity"])
        second_identity = mutable_mapping(second["identity"])
        second_identity["catalog_namespace"] = first_identity["catalog_namespace"]
        second_identity["catalog_id"] = first_identity["catalog_id"]
        first_payload = mutable_mapping(first_identity["payload"])
        second_payload = mutable_mapping(second_identity["payload"])
        second_payload["listing_venue"] = (
            "NYSE" if first_payload["listing_venue"] != "NYSE" else "NASDAQ"
        )
    elif case == "underlying_catalog_collision":
        _append_disabled_observations(payload)
        option = next(
            item
            for item in _items(payload, "instruments")
            if item["asset_class"] == "listed_option"
        )
        option_identity = mutable_mapping(option["identity"])
        option_payload = mutable_mapping(option_identity["payload"])
        underlying = mutable_mapping(option_payload["underlying"])
        underlying_payload = mutable_mapping(underlying["payload"])
        underlying_payload["listing_venue"] = "NYSE"
    else:
        assert case == "missing_alias"
        second["aliases"] = []
    reseal_recorded_snapshot(payload, "instruments")

    assert RecordedUniverseSource(payload).load() == UniverseRefusal(
        UniverseRefusalCode.CONTRADICTORY_INPUT
    )


def test_recorded_universe_preserves_disabled_instrument_and_position_variants() -> None:
    payload = recorded_universe()
    _append_disabled_observations(payload)

    loaded = RecordedUniverseSource(payload).load()

    assert isinstance(loaded, UniverseInputs)
    assert any(
        isinstance(item, CryptoSpotInstrument) for item in loaded.instrument_snapshot.instruments
    )
    assert any(
        isinstance(item, ListedOptionInstrument) for item in loaded.instrument_snapshot.instruments
    )
    assert any(isinstance(item, CryptoSpotPosition) for item in loaded.position_snapshot.positions)
    assert any(
        isinstance(item, ListedOptionPosition) for item in loaded.position_snapshot.positions
    )


@pytest.mark.parametrize(
    "case",
    [
        "instrument_header",
        "instrument_schema_boolean",
        "instrument_payload_version_float",
        "instrument_identity",
        "instrument_catalog_namespace",
        "instrument_asset_class_type",
        "instrument_asset_class_mismatch",
        "aliases_not_list",
        "aliases_empty",
        "alias_invalid",
        "alias_duplicate",
        "equity_payload_shape",
        "instrument_type_type",
        "instrument_type_value",
        "status_type",
        "status_value",
        "tradable_type",
        "leveraged_type",
        "inverse_type",
        "disabled_payload_shape",
        "disabled_status",
        "disabled_tradable",
        "option_underlying_catalog_namespace",
        "snapshot_header",
        "snapshot_envelope_version_boolean",
        "snapshot_payload_version_float",
    ],
)
def test_recorded_universe_refuses_hostile_instrument_variants(  # noqa: PLR0912, PLR0915 - exercise every closed variant through one public boundary.
    case: str,
) -> None:
    payload = recorded_universe()
    item = _item(payload, "instruments")
    item_payload = _item_payload(payload, "instruments")
    if case == "instrument_header":
        item["schema_version"] = 2
    elif case == "instrument_schema_boolean":
        item["schema_version"] = True
    elif case == "instrument_payload_version_float":
        item["payload_schema_version"] = 1.0
    elif case == "instrument_identity":
        item["identity"] = {}
    elif case == "instrument_catalog_namespace":
        mutable_mapping(item["identity"])["catalog_namespace"] = "alpaca"
    elif case == "instrument_asset_class_type":
        item["asset_class"] = 1
    elif case == "instrument_asset_class_mismatch":
        item["asset_class"] = "crypto_spot"
    elif case == "aliases_not_list":
        item["aliases"] = ()
    elif case == "aliases_empty":
        item["aliases"] = []
    elif case == "alias_invalid":
        item["aliases"] = [{"namespace": "INVALID", "value": "AAPL"}]
    elif case == "alias_duplicate":
        alias = mutable_list(item["aliases"])[0]
        item["aliases"] = [alias, deepcopy(alias)]
    elif case == "equity_payload_shape":
        item_payload["unexpected"] = True
    elif case == "instrument_type_type":
        item_payload["instrument_type"] = 1
    elif case == "instrument_type_value":
        item_payload["instrument_type"] = "unknown"
    elif case == "status_type":
        item_payload["status"] = 1
    elif case == "status_value":
        item_payload["status"] = "unknown"
    elif case == "tradable_type":
        item_payload["tradable"] = 1
    elif case == "leveraged_type":
        item_payload["leveraged"] = 0
    elif case == "inverse_type":
        item_payload["inverse"] = 0
    elif case.startswith("disabled_"):
        _append_disabled_observations(payload)
        disabled = next(
            candidate
            for candidate in _items(payload, "instruments")
            if candidate["asset_class"] == "crypto_spot"
        )
        disabled_payload = mutable_mapping(disabled["payload"])
        if case == "disabled_payload_shape":
            disabled_payload["unexpected"] = True
        elif case == "disabled_status":
            disabled_payload["status"] = "unknown"
        else:
            disabled_payload["tradable"] = 1
        assert disabled["asset_class"] == "crypto_spot"
    elif case == "option_underlying_catalog_namespace":
        _append_disabled_observations(payload)
        option = next(
            candidate
            for candidate in _items(payload, "instruments")
            if candidate["asset_class"] == "listed_option"
        )
        identity = mutable_mapping(option["identity"])
        identity_payload = mutable_mapping(identity["payload"])
        underlying = mutable_mapping(identity_payload["underlying"])
        underlying["catalog_namespace"] = "alpaca"
    elif case == "snapshot_header":
        _snapshot(payload, "instruments")["payload_schema_version"] = 2
    elif case == "snapshot_envelope_version_boolean":
        _snapshot(payload, "instruments")["envelope_schema_version"] = True
    else:
        assert case == "snapshot_payload_version_float"
        _snapshot(payload, "instruments")["payload_schema_version"] = 1.0

    expected = (
        UniverseRefusalCode.CONTRADICTORY_INPUT
        if case == "aliases_empty"
        else UniverseRefusalCode.INVALID_INPUT
    )
    assert RecordedUniverseSource(payload).load() == UniverseRefusal(expected)


@pytest.mark.parametrize(
    "case",
    [
        "position_header",
        "position_schema_boolean",
        "position_payload_version_float",
        "position_identity",
        "position_payload_shape",
        "position_asset_class_type",
        "position_asset_class_mismatch",
        "quantity_type",
        "quantity_shape",
        "quantity_zero",
        "unit_type",
        "wrong_equity_unit",
        "valuation_missing_field",
        "valuation_amount",
        "valuation_currency",
        "valuation_source",
        "fractional_option",
    ],
)
def test_recorded_universe_refuses_hostile_position_variants(  # noqa: PLR0912 - table covers the closed position boundary.
    case: str,
) -> None:
    payload = recorded_universe()
    if case == "fractional_option":
        _append_disabled_observations(payload)
        position = _item(payload, "positions", -1)
        _item_payload(payload, "positions", -1)["quantity"] = "0.5"
    else:
        position = _item(payload, "positions")
        position_payload = _item_payload(payload, "positions")
        if case == "position_header":
            position["record_kind"] = "instrument_observation"
        elif case == "position_schema_boolean":
            position["schema_version"] = True
        elif case == "position_payload_version_float":
            position["payload_schema_version"] = 1.0
        elif case == "position_identity":
            position["identity"] = {}
        elif case == "position_payload_shape":
            position_payload["unexpected"] = True
        elif case == "position_asset_class_type":
            position["asset_class"] = 1
        elif case == "position_asset_class_mismatch":
            position["asset_class"] = "crypto_spot"
        elif case == "quantity_type":
            position_payload["quantity"] = 1
        elif case == "quantity_shape":
            position_payload["quantity"] = "not-a-quantity"
        elif case == "quantity_zero":
            position_payload["quantity"] = "0"
        elif case == "unit_type":
            position_payload["unit"] = 1
        elif case.startswith("valuation_"):
            valuation = mutable_mapping(position_payload["valuation"])
            if case == "valuation_missing_field":
                del valuation["source"]
            elif case == "valuation_amount":
                valuation["amount"] = 100
            elif case == "valuation_currency":
                valuation["currency"] = "usd"
            else:
                assert case == "valuation_source"
                valuation["source"] = "INVALID SOURCE"
        else:
            assert case == "wrong_equity_unit"
            position_payload["unit"] = "contract"

    assert position
    assert RecordedUniverseSource(payload).load() == UniverseRefusal(
        UniverseRefusalCode.INVALID_INPUT
    )


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda payload: payload.update({"unexpected": True}), UniverseRefusalCode.INVALID_INPUT),
        (
            lambda payload: payload.update({"schema_version": True}),
            UniverseRefusalCode.INVALID_INPUT,
        ),
        (
            lambda payload: payload.update({"schema_version": 1.0}),
            UniverseRefusalCode.INVALID_INPUT,
        ),
        (
            lambda payload: payload.update({"evidence_cutoff": "not-a-timestamp"}),
            UniverseRefusalCode.INVALID_INPUT,
        ),
        (
            lambda payload: payload.update({"evidence_cutoff": "2026-08-21T20:00:00.1+00:00"}),
            UniverseRefusalCode.INVALID_INPUT,
        ),
        (
            lambda payload: payload.update({"data_regime": _StringSubclass("alpaca-basic-iex-v1")}),
            UniverseRefusalCode.INVALID_INPUT,
        ),
        (
            lambda payload: payload.update(
                {"evidence_cutoff": _StringSubclass("2026-08-21T20:00:00+00:00")}
            ),
            UniverseRefusalCode.INVALID_INPUT,
        ),
        (
            lambda payload: _snapshot_payload(payload, "instruments").update({"complete": False}),
            UniverseRefusalCode.MISSING_INPUT,
        ),
        (
            lambda payload: _items(payload, "instruments").append(
                deepcopy(_items(payload, "instruments")[0])
            ),
            UniverseRefusalCode.CONTRADICTORY_INPUT,
        ),
    ],
)
def test_recorded_universe_refuses_hostile_representations_without_retaining_them(
    mutate: object,
    reason: UniverseRefusalCode,
) -> None:
    payload = recorded_universe()
    assert callable(mutate)
    mutate(payload)
    if reason is UniverseRefusalCode.CONTRADICTORY_INPUT:
        reseal_recorded_snapshot(payload, "instruments")

    loaded = RecordedUniverseSource(payload).load()

    assert isinstance(loaded, UniverseRefusal)
    assert loaded.code is reason
    cutoff = payload["evidence_cutoff"]
    assert isinstance(cutoff, str)
    assert cutoff not in repr(loaded)


def test_recorded_universe_reports_missing_payload_without_manufacturing_defaults() -> None:
    loaded = RecordedUniverseSource(None).load()

    assert isinstance(loaded, UniverseRefusal)
    assert loaded.code is UniverseRefusalCode.MISSING_INPUT


def test_alpaca_paper_identity_rejects_an_object_outside_the_closed_union() -> None:
    class UnexpectedIdentity:
        pass

    # Deliberately violate the static union to exercise runtime boundary validation.
    assert not is_alpaca_paper_identity(cast("InstrumentIdentity", UnexpectedIdentity()))


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("root_not_mapping", UniverseRefusalCode.INVALID_INPUT),
        ("root_non_text_key", UniverseRefusalCode.INVALID_INPUT),
        ("asset_snapshot_not_mapping", UniverseRefusalCode.INVALID_INPUT),
        ("asset_observed_type", UniverseRefusalCode.INVALID_INPUT),
        ("asset_observed_naive", UniverseRefusalCode.INVALID_INPUT),
        ("asset_available_type", UniverseRefusalCode.INVALID_INPUT),
        ("asset_authority", UniverseRefusalCode.INVALID_INPUT),
        ("asset_source_fingerprint", UniverseRefusalCode.INVALID_INPUT),
        ("asset_content_hash", UniverseRefusalCode.INVALID_INPUT),
        ("asset_payload_shape", UniverseRefusalCode.INVALID_INPUT),
        ("asset_items_not_list", UniverseRefusalCode.INVALID_INPUT),
        ("asset_items_empty", UniverseRefusalCode.MISSING_INPUT),
        ("holding_items_not_list", UniverseRefusalCode.INVALID_INPUT),
        ("root_mapping_subclass", UniverseRefusalCode.INVALID_INPUT),
        ("asset_snapshot_mapping_subclass", UniverseRefusalCode.INVALID_INPUT),
        ("asset_items_list_subclass", UniverseRefusalCode.INVALID_INPUT),
    ],
)
def test_recorded_universe_refuses_hostile_container_and_mapping_shapes(  # noqa: PLR0912 - table covers the closed snapshot envelope.
    case: str,
    reason: UniverseRefusalCode,
) -> None:
    payload: object = deepcopy(recorded_universe())
    assert isinstance(payload, dict)
    if case == "root_mapping_subclass":
        payload = _MappingSubclass(payload)
    elif case == "root_not_mapping":
        payload = []
    elif case == "root_non_text_key":
        payload[1] = "hostile"
    elif case == "asset_snapshot_mapping_subclass":
        payload["instruments"] = _MappingSubclass(_snapshot(payload, "instruments"))
    elif case == "asset_snapshot_not_mapping":
        payload["instruments"] = []
    elif case == "asset_observed_type":
        _snapshot(payload, "instruments")["observed_at"] = 3
    elif case == "asset_observed_naive":
        _snapshot(payload, "instruments")["observed_at"] = "2026-08-21T19:30:00"
    elif case == "asset_available_type":
        _snapshot(payload, "instruments")["available_at"] = 3
    elif case == "asset_authority":
        _snapshot(payload, "instruments")["authority_scope"] = "execution"
    elif case == "asset_source_fingerprint":
        _snapshot(payload, "instruments")["source_fingerprint"] = "invalid"
    elif case == "asset_content_hash":
        _snapshot(payload, "instruments")["content_hash"] = "f" * SHA256_HEX_LENGTH
    elif case == "asset_payload_shape":
        _snapshot_payload(payload, "instruments")["unexpected"] = True
    elif case == "asset_items_list_subclass":
        _snapshot_payload(payload, "instruments")["items"] = _ListSubclass(
            _items(payload, "instruments")
        )
    elif case == "asset_items_not_list":
        _snapshot_payload(payload, "instruments")["items"] = ()
    elif case == "asset_items_empty":
        _snapshot_payload(payload, "instruments")["items"] = []
    else:
        assert case == "holding_items_not_list"
        _snapshot_payload(payload, "positions")["items"] = ()

    loaded = RecordedUniverseSource(payload).load()

    assert loaded == UniverseRefusal(reason)


@pytest.mark.parametrize("case", ["available_after_cutoff", "data_regime_conflict"])
def test_recorded_universe_refuses_semantically_contradictory_snapshot_provenance(
    case: str,
) -> None:
    payload = recorded_universe()
    instruments = _snapshot(payload, "instruments")
    if case == "available_after_cutoff":
        instruments["available_at"] = "2026-08-21T20:01:00+00:00"
    else:
        assert case == "data_regime_conflict"
        instruments["data_regime"] = "different-regime-v1"
    reseal_recorded_snapshot(payload, "instruments")

    assert RecordedUniverseSource(payload).load() == UniverseRefusal(
        UniverseRefusalCode.CONTRADICTORY_INPUT
    )


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("asset_item_not_mapping", UniverseRefusalCode.INVALID_INPUT),
        ("asset_item_non_text_key", UniverseRefusalCode.INVALID_INPUT),
        ("asset_item_missing_field", UniverseRefusalCode.INVALID_INPUT),
        ("asset_item_mapping_subclass", UniverseRefusalCode.INVALID_INPUT),
        ("holding_item_not_mapping", UniverseRefusalCode.INVALID_INPUT),
        ("holding_item_missing_field", UniverseRefusalCode.INVALID_INPUT),
        ("holding_quantity_zero", UniverseRefusalCode.INVALID_INPUT),
        ("holding_quantity_exponent", UniverseRefusalCode.INVALID_INPUT),
        ("duplicate_holding", UniverseRefusalCode.CONTRADICTORY_INPUT),
    ],
)
def test_recorded_universe_refuses_hostile_item_shapes(
    case: str,
    reason: UniverseRefusalCode,
) -> None:
    payload = deepcopy(recorded_universe())
    if case == "asset_item_mapping_subclass":
        _items(payload, "instruments")[0] = _MappingSubclass(_item(payload, "instruments"))
    elif case == "asset_item_not_mapping":
        _raw_items(payload, "instruments")[0] = "hostile"
    elif case == "asset_item_non_text_key":
        hostile_item: dict[object, object] = {}
        hostile_item.update(_item(payload, "instruments"))
        hostile_item[1] = "hostile"
        _raw_items(payload, "instruments")[0] = hostile_item
    elif case == "asset_item_missing_field":
        del _item(payload, "instruments")["payload"]
    elif case == "holding_item_not_mapping":
        _raw_items(payload, "positions")[0] = "hostile"
    elif case == "holding_item_missing_field":
        del _item(payload, "positions")["payload"]
    elif case == "holding_quantity_zero":
        _item(payload, "positions")["quantity"] = "0"
    elif case == "holding_quantity_exponent":
        _item(payload, "positions")["quantity"] = "1e1000000"
    else:
        assert case == "duplicate_holding"
        _items(payload, "positions").append(deepcopy(_items(payload, "positions")[0]))
        reseal_recorded_snapshot(payload, "positions")

    loaded = RecordedUniverseSource(payload).load()

    assert loaded == UniverseRefusal(reason)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "not a symbol"),
        ("symbol", _StringSubclass("AAPL")),
        ("asset_class", "unknown"),
        ("asset_class", _StringSubclass("us_equity")),
        ("instrument_type", "unknown"),
        ("instrument_type", _StringSubclass("common_stock")),
        ("exchange", "not an exchange"),
        ("exchange", _StringSubclass("NASDAQ")),
        ("status", "unknown"),
        ("status", _StringSubclass("active")),
        ("tradable", 1),
        ("leveraged", 0),
        ("inverse", 0),
        ("price", 1),
        ("price", _StringSubclass("10")),
        ("price", "not-a-decimal"),
        ("price", "1e1000000"),
        ("price", "1" * 65),
        ("price", "-1"),
        ("median_dollar_volume", None),
        ("history_days", -1),
        ("history_days", 1_000_001),
    ],
)
def test_recorded_universe_refuses_invalid_asset_fields(field: str, value: object) -> None:
    payload = deepcopy(recorded_universe())
    _item(payload, "instruments")[field] = value

    loaded = RecordedUniverseSource(payload).load()

    assert loaded == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)


def test_persisted_universe_snapshot_round_trips_after_full_revalidation() -> None:
    snapshot = _valid_persisted_snapshot()

    parsed = parse_persisted_universe_snapshot(
        snapshot.to_json(),
        expected_run_id=snapshot.run_id,
        expected_snapshot_id=snapshot.snapshot_id,
        recorded_at=RECORDED_AT,
    )

    assert parsed == snapshot


@pytest.mark.parametrize("value", [3, "{", "{}"])
def test_persisted_universe_snapshot_refuses_invalid_root_representations(value: object) -> None:
    refused = parse_persisted_universe_snapshot(
        value,
        expected_run_id="d" * SHA256_HEX_LENGTH,
        expected_snapshot_id="e" * SHA256_HEX_LENGTH,
        recorded_at=RECORDED_AT,
    )

    assert refused == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)


def test_persisted_universe_snapshot_refuses_a_string_subclass() -> None:
    snapshot = _valid_persisted_snapshot()

    refused = parse_persisted_universe_snapshot(
        _StringSubclass(snapshot.to_json()),
        expected_run_id=snapshot.run_id,
        expected_snapshot_id=snapshot.snapshot_id,
        recorded_at=RECORDED_AT,
    )

    assert refused == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)


@pytest.mark.parametrize(
    "value",
    ["9" * 5_000, "[" * 2_000 + "]" * 2_000],
    ids=["oversized_integer", "excessive_nesting"],
)
def test_persisted_universe_snapshot_translates_extreme_json_failures(value: str) -> None:
    refused = parse_persisted_universe_snapshot(
        value,
        expected_run_id="d" * SHA256_HEX_LENGTH,
        expected_snapshot_id="e" * SHA256_HEX_LENGTH,
        recorded_at=RECORDED_AT,
    )

    assert refused == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)


@pytest.mark.parametrize("case", ["duplicate_key", "noncanonical_whitespace"])
def test_persisted_universe_snapshot_refuses_noncanonical_authoritative_bytes(case: str) -> None:
    snapshot = _valid_persisted_snapshot()
    encoded = snapshot.to_json()
    hostile = '{"schema_version":999,' + encoded[1:] if case == "duplicate_key" else " " + encoded

    refused = parse_persisted_universe_snapshot(
        hostile,
        expected_run_id=snapshot.run_id,
        expected_snapshot_id=snapshot.snapshot_id,
        recorded_at=RECORDED_AT,
    )

    assert refused == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)


@pytest.mark.parametrize(
    "case",
    [
        "invalid_root_version_boolean",
        "invalid_root_version_float",
        "invalid_cycle",
        "invalid_material_fingerprints",
        "invalid_policy",
        "policy_catalog_namespace",
        "invalid_inputs",
        "invalid_subject_boolean",
        "regime_conflict",
        "mismatch",
    ],
)
def test_persisted_universe_snapshot_refuses_invalid_or_changed_material(case: str) -> None:
    snapshot = _valid_persisted_snapshot()
    payload = snapshot.to_payload()
    material = mutable_mapping(payload["payload"])
    expected_snapshot_id = snapshot.snapshot_id
    if case == "invalid_root_version_boolean":
        payload["envelope_schema_version"] = True
    elif case == "invalid_root_version_float":
        payload["envelope_schema_version"] = 1.0
    elif case == "invalid_cycle":
        payload["cycle"] = {}
    elif case == "invalid_material_fingerprints":
        payload["material_fingerprints"] = {}
    elif case == "invalid_policy":
        material["policy"] = {}
    elif case == "policy_catalog_namespace":
        policy = mutable_mapping(material["policy"])
        allowlist = mutable_list(policy["etf_allowlist"])
        identity = mutable_mapping(allowlist[0])
        identity["catalog_namespace"] = "alpaca"
    elif case == "invalid_inputs":
        material["inputs"] = {}
    elif case == "invalid_subject_boolean":
        subjects = material["subjects"]
        assert isinstance(subjects, list)
        subject = subjects[0]
        assert isinstance(subject, dict)
        subject["is_position"] = int(bool(subject["is_position"]))
    elif case == "regime_conflict":
        policy = typed_universe_policy().to_payload()
        policy["data_regime"] = "different-regime-v1"
        parsed_policy = EquityUniversePolicy.parse(policy)
        assert isinstance(parsed_policy, EquityUniversePolicy)
        material["policy"] = parsed_policy.to_payload()
    else:
        assert case == "mismatch"
        expected_snapshot_id = "e" * SHA256_HEX_LENGTH

    refused = parse_persisted_universe_snapshot(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        expected_run_id=snapshot.run_id,
        expected_snapshot_id=expected_snapshot_id,
        recorded_at=RECORDED_AT,
    )

    assert refused == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
