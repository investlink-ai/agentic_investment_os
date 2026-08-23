from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, cast, override
from zoneinfo import ZoneInfo

import pytest

from agentic_investment_os.adapters.recorded_universe import RecordedUniverseSource
from agentic_investment_os.domain.identity import (
    AssetClass,
    CryptoDecisionWindow,
    CryptoSpotInstrumentIdentity,
    EquityInstrumentIdentity,
    ExerciseStyle,
    InstrumentAlias,
    ListedOptionInstrumentIdentity,
    MarketSession,
    OptionRight,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.domain.universe import (
    CryptoSpotInstrument,
    CryptoSpotPosition,
    EquityUniversePolicy,
    InstrumentSnapshot,
    InstrumentStatus,
    ListedOptionInstrument,
    ListedOptionPosition,
    PositionDisposition,
    PositionSnapshot,
    PositionValuation,
    UniverseExclusionReason,
    UniverseInputIdentity,
    UniverseInputs,
    UniverseRefusal,
    UniverseRefusalCode,
    UniverseSnapshot,
    UniverseSubject,
)
from agentic_investment_os.domain.universe import (
    build_universe_snapshot as _build_universe_snapshot,
)
from tests._universe import (
    mutable_mapping,
    mutable_mapping_list,
    recorded_universe,
    reseal_recorded_snapshot,
    universe_policy,
)

if TYPE_CHECKING:
    from collections.abc import Callable

RECORDED_AT = UtcInstant.from_datetime(datetime(2026, 8, 21, 22, 0, tzinfo=UTC))
MAXIMUM_HISTORY_DAYS = 1_000_000


class _IntegerSubclass(int):
    pass


class _PolicyMap(dict[str, object]):
    pass


class _StringList(list[str]):
    pass


def _forged_utc_instant() -> UtcInstant:
    forged = object.__new__(UtcInstant)
    object.__setattr__(
        forged,
        "value",
        datetime(2026, 8, 21, 22, 0),  # noqa: DTZ001
    )
    return forged


class _StringSubclass(str):
    __slots__ = ()


class _EqualityBomb:
    @override
    def __eq__(self, other: object) -> bool:
        raise RuntimeError

    @override
    def __hash__(self) -> int:
        return id(self)


def _asset(payload: dict[str, object], symbol: str) -> dict[str, object]:
    item = next(
        item
        for item in _asset_items(payload)
        if mutable_mapping_list(item["aliases"])[0]["value"] == symbol
    )
    return mutable_mapping(item["payload"])


def _snapshot(payload: dict[str, object], name: str) -> dict[str, object]:
    return mutable_mapping(payload[name])


def _asset_items(payload: dict[str, object]) -> list[dict[str, object]]:
    snapshot_payload = mutable_mapping(_snapshot(payload, "instruments")["payload"])
    return mutable_mapping_list(snapshot_payload["items"])


def _hold(payload: dict[str, object], symbol: str) -> None:
    instrument = next(
        item
        for item in _asset_items(payload)
        if mutable_mapping_list(item["aliases"])[0]["value"] == symbol
    )
    snapshot_payload = mutable_mapping(_snapshot(payload, "positions")["payload"])
    items = mutable_mapping_list(snapshot_payload["items"])
    items.append(
        {
            "schema_version": 1,
            "payload_schema_version": 1,
            "record_kind": "position_observation",
            "asset_class": "us_equity",
            "identity": instrument["identity"],
            "payload": {
                "quantity": "1",
                "unit": "share",
                "valuation": {
                    "amount": "10",
                    "currency": "USD",
                    "source": "alpaca-paper-market-value",
                },
            },
        }
    )
    reseal_recorded_snapshot(payload, "instruments")
    reseal_recorded_snapshot(payload, "positions")


def _symbol(subject: UniverseSubject) -> str:
    assert subject.aliases
    return subject.aliases[0].value


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


def test_universe_snapshot_applies_structural_policy_and_retains_ineligible_holdings() -> None:
    policy = EquityUniversePolicy.parse(universe_policy())
    inputs = RecordedUniverseSource(recorded_universe()).load()
    assert isinstance(policy, EquityUniversePolicy)
    assert isinstance(inputs, UniverseInputs)

    snapshot = _build("a" * 64, inputs, policy, recorded_at=RECORDED_AT)

    assert isinstance(snapshot, UniverseSnapshot)
    assert tuple(_symbol(subject) for subject in snapshot.subjects) == ("AAPL", "HOLD", "SPY")
    assert snapshot.subjects[0].eligible_for_new_entry
    assert snapshot.subjects[0].position_disposition is PositionDisposition.NOT_APPLICABLE
    assert snapshot.subjects[1].is_position
    assert not snapshot.subjects[1].eligible_for_new_entry
    assert snapshot.subjects[1].position_disposition is PositionDisposition.REFRESH_REQUIRED
    assert snapshot.subjects[1].exclusion_reasons == (
        UniverseExclusionReason.INACTIVE,
        UniverseExclusionReason.NOT_TRADABLE,
    )
    assert snapshot.subjects[2].eligible_for_new_entry


def test_universe_snapshot_identity_and_order_are_deterministic() -> None:
    policy = EquityUniversePolicy.parse(universe_policy())
    first_payload = recorded_universe()
    second_payload = recorded_universe()
    _asset_items(second_payload).reverse()
    assert isinstance(policy, EquityUniversePolicy)
    first = RecordedUniverseSource(first_payload).load()
    second = RecordedUniverseSource(second_payload).load()
    assert isinstance(first, UniverseInputs)
    assert isinstance(second, UniverseInputs)

    first_snapshot = _build("b" * 64, first, policy, recorded_at=RECORDED_AT)
    second_snapshot = _build("b" * 64, second, policy, recorded_at=RECORDED_AT)

    assert first == second
    assert first_snapshot == second_snapshot


def test_alias_provenance_does_not_change_authority_fingerprints() -> None:
    policy = EquityUniversePolicy.parse(universe_policy())
    first_payload = recorded_universe()
    second_payload = recorded_universe()
    aapl = next(
        item
        for item in _asset_items(second_payload)
        if mutable_mapping_list(item["aliases"])[0]["value"] == "AAPL"
    )
    aliases = mutable_mapping_list(aapl["aliases"])
    aliases[0]["value"] = "AAPL.NEW"
    reseal_recorded_snapshot(second_payload, "instruments")
    first = RecordedUniverseSource(first_payload).load()
    second = RecordedUniverseSource(second_payload).load()
    assert isinstance(policy, EquityUniversePolicy)
    assert isinstance(first, UniverseInputs)
    assert isinstance(second, UniverseInputs)

    first_snapshot = _build("b" * 64, first, policy, recorded_at=RECORDED_AT)
    second_snapshot = _build("b" * 64, second, policy, recorded_at=RECORDED_AT)

    assert first.instrument_snapshot.fingerprint != second.instrument_snapshot.fingerprint
    assert (
        first.instrument_snapshot.material_fingerprint
        == second.instrument_snapshot.material_fingerprint
    )
    assert isinstance(first_snapshot, UniverseSnapshot)
    assert isinstance(second_snapshot, UniverseSnapshot)
    assert first_snapshot.snapshot_id == second_snapshot.snapshot_id
    assert first_snapshot.content_hash != second_snapshot.content_hash


def test_disabled_asset_positions_are_preserved_as_explicit_portfolio_mismatches() -> None:
    inputs = RecordedUniverseSource(recorded_universe()).load()
    policy = EquityUniversePolicy.parse(universe_policy())
    assert isinstance(inputs, UniverseInputs)
    assert isinstance(policy, EquityUniversePolicy)
    underlying = inputs.instrument_snapshot.instruments[0].identity
    assert isinstance(underlying, EquityInstrumentIdentity)
    crypto_identity = CryptoSpotInstrumentIdentity(
        "alpaca-paper", "crypto-btc-usd", "BTC", "USD", "ALPACA"
    )
    unheld_crypto_identity = CryptoSpotInstrumentIdentity(
        "alpaca-paper", "crypto-eth-usd", "ETH", "USD", "ALPACA"
    )
    option_identity = ListedOptionInstrumentIdentity(
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
    instruments = InstrumentSnapshot.create(
        observed_at=inputs.instrument_snapshot.observed_at.value,
        available_at=inputs.instrument_snapshot.available_at.value,
        data_regime=inputs.instrument_snapshot.data_regime,
        source_fingerprint=inputs.instrument_snapshot.source_fingerprint,
        instruments=(
            *inputs.instrument_snapshot.instruments,
            CryptoSpotInstrument(
                identity=crypto_identity,
                aliases=(InstrumentAlias("alpaca-symbol", "BTC/USD"),),
                status=InstrumentStatus.ACTIVE,
                tradable=True,
            ),
            CryptoSpotInstrument(
                identity=unheld_crypto_identity,
                aliases=(InstrumentAlias("alpaca-symbol", "ETH/USD"),),
                status=InstrumentStatus.ACTIVE,
                tradable=True,
            ),
            ListedOptionInstrument(
                identity=option_identity,
                aliases=(InstrumentAlias("alpaca-symbol", "AAPL260918C00250000"),),
                status=InstrumentStatus.ACTIVE,
                tradable=True,
            ),
        ),
    )
    positions = PositionSnapshot.create(
        observed_at=inputs.position_snapshot.observed_at.value,
        available_at=inputs.position_snapshot.available_at.value,
        data_regime=inputs.position_snapshot.data_regime,
        source_fingerprint=inputs.position_snapshot.source_fingerprint,
        positions=(
            *inputs.position_snapshot.positions,
            CryptoSpotPosition(
                crypto_identity,
                Decimal("0.5"),
                PositionValuation(Decimal(35_000), "USD", "alpaca-paper-market-value"),
            ),
            ListedOptionPosition(
                option_identity,
                Decimal(2),
                PositionValuation(Decimal(2_500), "USD", "alpaca-paper-market-value"),
            ),
        ),
    )
    assert isinstance(instruments, InstrumentSnapshot)
    assert isinstance(positions, PositionSnapshot)

    snapshot = _build(
        "1" * 64,
        replace(inputs, instrument_snapshot=instruments, position_snapshot=positions),
        policy,
        recorded_at=RECORDED_AT,
    )

    assert isinstance(snapshot, UniverseSnapshot)
    mismatches = tuple(
        subject
        for subject in snapshot.subjects
        if subject.position_disposition is PositionDisposition.PORTFOLIO_MISMATCH
    )
    assert {subject.identity.asset_class for subject in mismatches} == {
        AssetClass.CRYPTO_SPOT,
        AssetClass.LISTED_OPTION,
    }
    assert all(
        subject.is_position and subject.eligible_for_new_entry is False for subject in mismatches
    )
    assert all(subject.identity != unheld_crypto_identity for subject in snapshot.subjects)


def test_snapshot_factories_refuse_invalid_common_metadata() -> None:
    inputs = RecordedUniverseSource(recorded_universe()).load()
    assert isinstance(inputs, UniverseInputs)

    instruments = InstrumentSnapshot.create(
        observed_at=inputs.instrument_snapshot.observed_at.value,
        available_at=inputs.instrument_snapshot.available_at.value,
        data_regime=inputs.instrument_snapshot.data_regime,
        source_fingerprint="invalid",
        instruments=inputs.instrument_snapshot.instruments,
    )
    positions = PositionSnapshot.create(
        observed_at=inputs.position_snapshot.observed_at.value,
        available_at=inputs.position_snapshot.available_at.value,
        data_regime=inputs.position_snapshot.data_regime,
        source_fingerprint="invalid",
        positions=inputs.position_snapshot.positions,
    )
    invalid_instrument_time = InstrumentSnapshot.create(
        observed_at=datetime(2026, 8, 21, 19, 30),  # noqa: DTZ001
        available_at=inputs.instrument_snapshot.available_at.value,
        data_regime=inputs.instrument_snapshot.data_regime,
        source_fingerprint=inputs.instrument_snapshot.source_fingerprint,
        instruments=inputs.instrument_snapshot.instruments,
    )
    invalid_position_time = PositionSnapshot.create(
        observed_at=inputs.position_snapshot.observed_at.value,
        available_at=datetime(2026, 8, 21, 19, 30),  # noqa: DTZ001
        data_regime=inputs.position_snapshot.data_regime,
        source_fingerprint=inputs.position_snapshot.source_fingerprint,
        positions=inputs.position_snapshot.positions,
    )
    invalid_inputs = UniverseInputs.create(
        data_regime=inputs.data_regime,
        evidence_cutoff=datetime(2026, 8, 21, 20, 0),  # noqa: DTZ001
        instrument_snapshot=inputs.instrument_snapshot,
        position_snapshot=inputs.position_snapshot,
    )
    untyped_instrument_snapshot = replace(
        inputs.instrument_snapshot,
        observed_at=cast("UtcInstant", datetime(2026, 8, 21, 19, 30, tzinfo=UTC)),
    )
    invalid_snapshot_inputs = UniverseInputs.create(
        data_regime=inputs.data_regime,
        evidence_cutoff=inputs.evidence_cutoff.value,
        instrument_snapshot=untyped_instrument_snapshot,
        position_snapshot=inputs.position_snapshot,
    )
    policy = EquityUniversePolicy.parse(universe_policy())
    assert isinstance(policy, EquityUniversePolicy)
    invalid_identity = UniverseInputIdentity.from_inputs(
        replace(
            inputs,
            evidence_cutoff=cast(
                "UtcInstant",
                datetime(2026, 8, 21, 20, 0, tzinfo=UTC),
            ),
        ),
        policy,
    )

    assert instruments == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    assert positions == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    assert invalid_instrument_time == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    assert invalid_position_time == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    assert invalid_inputs == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    assert invalid_snapshot_inputs == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    assert invalid_identity == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    with pytest.raises(InvalidUtcInstantError, match="universe absolute instant"):
        untyped_instrument_snapshot.to_payload()


def test_universe_snapshot_refuses_a_forged_recording_instant() -> None:
    inputs = RecordedUniverseSource(recorded_universe()).load()
    policy = EquityUniversePolicy.parse(universe_policy())
    assert isinstance(inputs, UniverseInputs)
    assert isinstance(policy, EquityUniversePolicy)

    assert _build(
        "1" * 64,
        inputs,
        policy,
        recorded_at=_forged_utc_instant(),
    ) == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)


def test_universe_snapshot_compares_fall_back_fold_times_as_absolute_instants() -> None:
    inputs = RecordedUniverseSource(recorded_universe()).load()
    policy = EquityUniversePolicy.parse(universe_policy())
    assert isinstance(inputs, UniverseInputs)
    assert isinstance(policy, EquityUniversePolicy)
    new_york = ZoneInfo("America/New_York")
    instruments = InstrumentSnapshot.create(
        observed_at=datetime(2026, 11, 1, 1, 15, tzinfo=new_york, fold=0),
        available_at=datetime(2026, 11, 1, 1, 30, tzinfo=new_york, fold=1),
        data_regime=inputs.data_regime,
        source_fingerprint=inputs.instrument_snapshot.source_fingerprint,
        instruments=inputs.instrument_snapshot.instruments,
    )
    positions = PositionSnapshot.create(
        observed_at=datetime(2026, 11, 1, 1, 20, tzinfo=new_york, fold=0),
        available_at=datetime(2026, 11, 1, 1, 25, tzinfo=new_york, fold=0),
        data_regime=inputs.data_regime,
        source_fingerprint=inputs.position_snapshot.source_fingerprint,
        positions=inputs.position_snapshot.positions,
    )
    assert isinstance(instruments, InstrumentSnapshot)
    assert isinstance(positions, PositionSnapshot)
    folded_inputs = UniverseInputs.create(
        data_regime=inputs.data_regime,
        evidence_cutoff=datetime(2026, 11, 1, 1, 45, tzinfo=new_york, fold=0),
        instrument_snapshot=instruments,
        position_snapshot=positions,
    )
    assert isinstance(folded_inputs, UniverseInputs)
    assert instruments.available_at.value > folded_inputs.evidence_cutoff.value

    assert _build(
        "2" * 64,
        folded_inputs,
        policy,
        recorded_at=UtcInstant.from_datetime(datetime(2026, 11, 1, 7, tzinfo=UTC)),
    ) == UniverseRefusal(UniverseRefusalCode.CONTRADICTORY_INPUT)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda asset: asset.update({"status": "inactive"}), UniverseExclusionReason.INACTIVE),
        (
            lambda asset: asset.update({"tradable": False}),
            UniverseExclusionReason.NOT_TRADABLE,
        ),
        (
            lambda asset: asset.update({"instrument_type": "etf"}),
            UniverseExclusionReason.ETF_NOT_ALLOWLISTED,
        ),
        (
            lambda asset: asset.update({"leveraged": True}),
            UniverseExclusionReason.LEVERAGED,
        ),
        (lambda asset: asset.update({"inverse": True}), UniverseExclusionReason.INVERSE),
        (
            lambda asset: asset.update({"price": "4.99"}),
            UniverseExclusionReason.PRICE_BELOW_MINIMUM,
        ),
        (
            lambda asset: asset.update({"median_dollar_volume": "999999"}),
            UniverseExclusionReason.LIQUIDITY_BELOW_MINIMUM,
        ),
        (
            lambda asset: asset.update({"history_days": 19}),
            UniverseExclusionReason.INSUFFICIENT_HISTORY,
        ),
    ],
)
def test_every_structural_and_threshold_exclusion_preserves_a_held_attention_subject(
    mutate: Callable[[dict[str, object]], None],
    reason: UniverseExclusionReason,
) -> None:
    payload = deepcopy(recorded_universe())
    mutate(_asset(payload, "AAPL"))
    _hold(payload, "AAPL")
    inputs = RecordedUniverseSource(payload).load()
    policy = EquityUniversePolicy.parse(universe_policy())
    assert isinstance(inputs, UniverseInputs)
    assert isinstance(policy, EquityUniversePolicy)

    snapshot = _build("c" * 64, inputs, policy, recorded_at=RECORDED_AT)

    assert isinstance(snapshot, UniverseSnapshot)
    subject = next(item for item in snapshot.subjects if _symbol(item) == "AAPL")
    assert subject.is_position
    assert not subject.eligible_for_new_entry
    assert subject.position_disposition is PositionDisposition.REFRESH_REQUIRED
    assert reason in subject.exclusion_reasons


def test_unapproved_listing_venue_is_an_explicit_exclusion_reason() -> None:
    payload = recorded_universe()
    aapl = next(
        item
        for item in _asset_items(payload)
        if mutable_mapping_list(item["aliases"])[0]["value"] == "AAPL"
    )
    identity = mutable_mapping(aapl["identity"])
    mutable_mapping(identity["payload"])["listing_venue"] = "BATS"
    _hold(payload, "AAPL")
    inputs = RecordedUniverseSource(payload).load()
    policy = EquityUniversePolicy.parse(universe_policy())
    assert isinstance(inputs, UniverseInputs)
    assert isinstance(policy, EquityUniversePolicy)

    snapshot = _build("2" * 64, inputs, policy, recorded_at=RECORDED_AT)

    assert isinstance(snapshot, UniverseSnapshot)
    subject = next(item for item in snapshot.subjects if _symbol(item) == "AAPL")
    assert UniverseExclusionReason.UNAPPROVED_EXCHANGE in subject.exclusion_reasons


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("asset_class", 1),
        ("asset_class", _StringSubclass("us_equity")),
        ("policy_type", 1),
        ("policy_type", _StringSubclass("equity_universe")),
        ("schema_version", 2),
        ("schema_version", True),
        ("schema_version", 1.0),
        ("schema_version", _IntegerSubclass(1)),
        ("data_regime", _StringSubclass("alpaca-basic-iex-v1")),
        ("approved_exchanges", ["NASDAQ", "OTC"]),
        ("approved_exchanges", ["NASDAQ", "NASDAQ"]),
        ("approved_exchanges", "NASDAQ"),
        ("approved_exchanges", ("NASDAQ",)),
        ("approved_exchanges", []),
        ("approved_exchanges", ["nasdaq"]),
        ("approved_exchanges", [3]),
        ("approved_exchanges", _StringList(["NASDAQ"])),
        ("approved_exchanges", [_StringSubclass("NASDAQ")]),
        ("etf_allowlist", ["SPY", "SPY"]),
        (
            "etf_allowlist",
            [
                EquityInstrumentIdentity("alpaca-paper", "equity-spy", "ARCA").to_payload(),
                EquityInstrumentIdentity("alpaca-paper", "equity-spy", "ARCA").to_payload(),
            ],
        ),
        ("etf_allowlist", ("SPY",)),
        ("minimum_price", 5),
        ("minimum_price", _StringSubclass("5")),
        ("minimum_price", "not-a-decimal"),
        ("minimum_price", "1e1000000"),
        ("minimum_price", "1" * 65),
        ("minimum_price", "-1"),
        ("minimum_median_dollar_volume", "NaN"),
        ("minimum_history_days", -1),
        ("minimum_history_days", "0"),
        ("minimum_history_days", False),
        ("minimum_history_days", 1_000_001),
        ("minimum_history_days", _IntegerSubclass(20)),
        ("maximum_snapshot_age_seconds", 0),
        ("maximum_snapshot_age_seconds", True),
        ("maximum_snapshot_age_seconds", _IntegerSubclass(7200)),
    ],
)
def test_universe_policy_refuses_unknown_or_noncanonical_policy_values(
    field: str,
    value: object,
) -> None:
    policy = universe_policy()
    policy[field] = value

    refused = EquityUniversePolicy.parse(policy)

    assert refused == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)


@pytest.mark.parametrize("field", ["asset_class", "policy_type"])
def test_universe_policy_refuses_discriminators_without_invoking_equality(field: str) -> None:
    policy = universe_policy()
    policy[field] = _EqualityBomb()

    assert EquityUniversePolicy.parse(policy) == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)


def test_universe_policy_refuses_missing_and_unknown_fields_without_defaults() -> None:
    missing = universe_policy()
    del missing["minimum_price"]
    unknown = {**universe_policy(), "return_optimized_threshold": True}

    assert EquityUniversePolicy.parse(missing) == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    assert EquityUniversePolicy.parse(unknown) == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    assert EquityUniversePolicy.parse(_PolicyMap(universe_policy())) == UniverseRefusal(
        UniverseRefusalCode.INVALID_INPUT
    )


def test_universe_snapshot_refuses_invalid_run_or_mismatched_data_regime() -> None:
    inputs = RecordedUniverseSource(recorded_universe()).load()
    policy = EquityUniversePolicy.parse(universe_policy())
    mismatched_policy_payload = universe_policy()
    mismatched_policy_payload["data_regime"] = "different-regime-v1"
    mismatched_policy = EquityUniversePolicy.parse(mismatched_policy_payload)
    assert isinstance(inputs, UniverseInputs)
    assert isinstance(policy, EquityUniversePolicy)
    assert isinstance(mismatched_policy, EquityUniversePolicy)

    assert _build("invalid", inputs, policy, recorded_at=RECORDED_AT) == UniverseRefusal(
        UniverseRefusalCode.CONTRADICTORY_INPUT
    )
    assert _build("d" * 64, inputs, mismatched_policy, recorded_at=RECORDED_AT) == UniverseRefusal(
        UniverseRefusalCode.CONTRADICTORY_INPUT
    )


@pytest.mark.parametrize("snapshot_name", ["instrument_snapshot", "position_snapshot"])
def test_universe_snapshot_refuses_each_snapshot_data_regime_mismatch(
    snapshot_name: str,
) -> None:
    inputs = RecordedUniverseSource(recorded_universe()).load()
    policy = EquityUniversePolicy.parse(universe_policy())
    assert isinstance(inputs, UniverseInputs)
    assert isinstance(policy, EquityUniversePolicy)
    if snapshot_name == "instrument_snapshot":
        changed_inputs = replace(
            inputs,
            instrument_snapshot=replace(
                inputs.instrument_snapshot,
                data_regime="different-regime-v1",
            ),
        )
    else:
        assert snapshot_name == "position_snapshot"
        changed_inputs = replace(
            inputs,
            position_snapshot=replace(
                inputs.position_snapshot,
                data_regime="different-regime-v1",
            ),
        )

    assert _build("d" * 64, changed_inputs, policy, recorded_at=RECORDED_AT) == UniverseRefusal(
        UniverseRefusalCode.CONTRADICTORY_INPUT
    )


def test_universe_snapshot_refuses_cycle_and_activation_mismatches_independently() -> None:
    inputs = RecordedUniverseSource(recorded_universe()).load()
    policy = EquityUniversePolicy.parse(universe_policy())
    assert isinstance(inputs, UniverseInputs)
    assert isinstance(policy, EquityUniversePolicy)
    crypto_window = CryptoDecisionWindow(
        UtcInstant.from_datetime(datetime(2026, 8, 21, tzinfo=UTC)),
        UtcInstant.from_datetime(datetime(2026, 8, 22, tzinfo=UTC)),
    )

    assert _build_universe_snapshot(
        "3" * 64,
        crypto_window,
        inputs,
        policy,
        enabled_asset_classes=(AssetClass.US_EQUITY,),
        recorded_at=RECORDED_AT,
    ) == UniverseRefusal(UniverseRefusalCode.CONTRADICTORY_INPUT)
    assert _build_universe_snapshot(
        "3" * 64,
        MarketSession(date(2026, 8, 21)),
        inputs,
        policy,
        enabled_asset_classes=(),
        recorded_at=RECORDED_AT,
    ) == UniverseRefusal(UniverseRefusalCode.CONTRADICTORY_INPUT)


def test_universe_snapshot_accepts_positions_covering_the_complete_instrument_set() -> None:
    payload = recorded_universe()
    for symbol in ("AAPL", "OTCX", "SPY", "TQQQ"):
        _hold(payload, symbol)
    inputs = RecordedUniverseSource(payload).load()
    policy = EquityUniversePolicy.parse(universe_policy())
    assert isinstance(inputs, UniverseInputs)
    assert isinstance(policy, EquityUniversePolicy)

    snapshot = _build("4" * 64, inputs, policy, recorded_at=RECORDED_AT)

    assert isinstance(snapshot, UniverseSnapshot)
    assert all(subject.is_position for subject in snapshot.subjects)


def test_universe_snapshot_accepts_exact_freshness_and_eligibility_boundaries() -> None:
    payload = recorded_universe()
    assets = _snapshot(payload, "instruments")
    holdings = _snapshot(payload, "positions")
    assets["observed_at"] = "2026-08-21T18:00:00+00:00"
    assets["available_at"] = "2026-08-21T18:00:00+00:00"
    holdings["observed_at"] = payload["evidence_cutoff"]
    holdings["available_at"] = payload["evidence_cutoff"]
    aapl = _asset(payload, "AAPL")
    aapl["price"] = "5"
    aapl["median_dollar_volume"] = "1000000"
    aapl["history_days"] = 20
    reseal_recorded_snapshot(payload, "instruments")
    reseal_recorded_snapshot(payload, "positions")
    inputs = RecordedUniverseSource(payload).load()
    policy = EquityUniversePolicy.parse(universe_policy())
    assert isinstance(inputs, UniverseInputs)
    assert isinstance(policy, EquityUniversePolicy)

    snapshot = _build("e" * 64, inputs, policy, recorded_at=RECORDED_AT)

    assert isinstance(snapshot, UniverseSnapshot)
    subject = next(item for item in snapshot.subjects if _symbol(item) == "AAPL")
    assert subject.eligible_for_new_entry
    assert isinstance(
        _build(
            "e" * 64,
            inputs,
            policy,
            recorded_at=inputs.evidence_cutoff,
        ),
        UniverseSnapshot,
    )


def test_position_valuation_uses_canonical_decimal_text() -> None:
    valuation = PositionValuation(
        Decimal("12.340"),
        "USD",
        "alpaca-paper-market-value",
    )

    assert valuation.to_payload()["amount"] == "12.34"


def test_universe_snapshot_ages_observations_instead_of_delivery_time() -> None:
    payload = recorded_universe()
    assets = _snapshot(payload, "instruments")
    assets["observed_at"] = "2026-08-21T17:59:59+00:00"
    assets["available_at"] = "2026-08-21T19:59:59+00:00"
    reseal_recorded_snapshot(payload, "instruments")
    inputs = RecordedUniverseSource(payload).load()
    policy = EquityUniversePolicy.parse(universe_policy())
    assert isinstance(inputs, UniverseInputs)
    assert isinstance(policy, EquityUniversePolicy)

    assert _build("e" * 64, inputs, policy, recorded_at=RECORDED_AT) == UniverseRefusal(
        UniverseRefusalCode.STALE_INPUT
    )


def test_universe_snapshot_refuses_a_future_cutoff_and_naive_record_time() -> None:
    payload = recorded_universe()
    payload["evidence_cutoff"] = "2026-08-21T22:01:00+00:00"
    assets = _snapshot(payload, "instruments")
    assets["observed_at"] = payload["evidence_cutoff"]
    assets["available_at"] = payload["evidence_cutoff"]
    holdings = _snapshot(payload, "positions")
    holdings["observed_at"] = payload["evidence_cutoff"]
    holdings["available_at"] = payload["evidence_cutoff"]
    reseal_recorded_snapshot(payload, "instruments")
    reseal_recorded_snapshot(payload, "positions")
    inputs = RecordedUniverseSource(payload).load()
    policy = EquityUniversePolicy.parse(universe_policy())
    assert isinstance(inputs, UniverseInputs)
    assert isinstance(policy, EquityUniversePolicy)

    assert _build(
        "f" * 64,
        inputs,
        policy,
        recorded_at=RECORDED_AT,
    ) == UniverseRefusal(UniverseRefusalCode.CONTRADICTORY_INPUT)
    assert _build(
        "f" * 64,
        inputs,
        policy,
        recorded_at=cast(
            "UtcInstant",
            datetime(2026, 8, 21, 22, 2),  # noqa: DTZ001
        ),
    ) == UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)


def test_universe_snapshot_refuses_an_observation_after_the_pinned_cutoff() -> None:
    inputs = RecordedUniverseSource(recorded_universe()).load()
    policy = EquityUniversePolicy.parse(universe_policy())
    assert isinstance(inputs, UniverseInputs)
    assert isinstance(policy, EquityUniversePolicy)
    future_instruments = replace(
        inputs.instrument_snapshot,
        observed_at=UtcInstant.from_datetime(inputs.evidence_cutoff.value + timedelta(seconds=1)),
        available_at=UtcInstant.from_datetime(inputs.evidence_cutoff.value + timedelta(seconds=1)),
    )

    assert _build(
        "f" * 64,
        replace(inputs, instrument_snapshot=future_instruments),
        policy,
        recorded_at=RECORDED_AT,
    ) == UniverseRefusal(UniverseRefusalCode.CONTRADICTORY_INPUT)


def test_universe_policy_accepts_zero_thresholds_one_second_age_and_canonicalizes_decimals() -> (
    None
):
    payload = universe_policy()
    payload["minimum_price"] = "0005.0000"
    payload["minimum_median_dollar_volume"] = "-0.000"
    payload["minimum_history_days"] = 0
    payload["maximum_snapshot_age_seconds"] = 1

    policy = EquityUniversePolicy.parse(payload)

    assert isinstance(policy, EquityUniversePolicy)
    assert policy.to_payload()["minimum_price"] == "5"
    assert policy.to_payload()["minimum_median_dollar_volume"] == "0"
    assert policy.minimum_history_days == 0
    assert policy.maximum_snapshot_age_seconds == 1


def test_universe_policy_accepts_only_representable_snapshot_ages() -> None:
    maximum_seconds = 86_399_999_999_999
    accepted_payload = universe_policy()
    accepted_payload["maximum_snapshot_age_seconds"] = maximum_seconds
    refused_payload = universe_policy()
    refused_payload["maximum_snapshot_age_seconds"] = maximum_seconds + 1

    accepted = EquityUniversePolicy.parse(accepted_payload)

    assert isinstance(accepted, EquityUniversePolicy)
    assert accepted.maximum_snapshot_age_seconds == maximum_seconds
    assert EquityUniversePolicy.parse(refused_payload) == UniverseRefusal(
        UniverseRefusalCode.INVALID_INPUT
    )


def test_universe_policy_accepts_only_bounded_decimal_and_history_representations() -> None:
    accepted_payload = universe_policy()
    accepted_payload["minimum_price"] = "0." + "0" * 62
    accepted_payload["minimum_history_days"] = MAXIMUM_HISTORY_DAYS
    refused_payload = universe_policy()
    refused_payload["minimum_price"] = "0." + "0" * 63

    accepted = EquityUniversePolicy.parse(accepted_payload)

    assert isinstance(accepted, EquityUniversePolicy)
    assert accepted.to_payload()["minimum_price"] == "0"
    assert accepted.minimum_history_days == MAXIMUM_HISTORY_DAYS
    assert EquityUniversePolicy.parse(refused_payload) == UniverseRefusal(
        UniverseRefusalCode.INVALID_INPUT
    )
