"""Validate recorded instrument and position envelopes for deterministic local use."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TypeGuard

from agentic_investment_os.domain.identity import (
    AssetClass,
    CryptoSpotInstrumentIdentity,
    EquityInstrumentIdentity,
    InstrumentAlias,
    InstrumentIdentity,
    ListedOptionInstrumentIdentity,
    parse_decision_cycle_identity,
    parse_instrument_alias,
    parse_instrument_identity,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.domain.universe import (
    CryptoSpotInstrument,
    CryptoSpotPosition,
    EquityInstrument,
    EquityInstrumentType,
    EquityPosition,
    EquityUniversePolicy,
    InstrumentObservation,
    InstrumentSnapshot,
    InstrumentStatus,
    ListedOptionInstrument,
    ListedOptionPosition,
    PositionObservation,
    PositionSnapshot,
    PositionValuation,
    UniverseInputs,
    UniverseRefusal,
    UniverseRefusalCode,
    UniverseSnapshot,
    build_universe_snapshot,
    is_data_regime,
    is_universe_schema_version,
    parse_history_days,
    parse_nonnegative_decimal,
)

__all__ = (
    "RecordedUniverseSource",
    "is_alpaca_paper_identity",
    "parse_recorded_position_snapshot",
)

_ALPACA_PAPER_CATALOG_NAMESPACE = "alpaca-paper"

_ROOT_FIELDS = frozenset(
    {"schema_version", "record_kind", "data_regime", "evidence_cutoff", "instruments", "positions"}
)
_SNAPSHOT_FIELDS = frozenset(
    {
        "envelope_schema_version",
        "payload_schema_version",
        "record_kind",
        "payload_discriminator",
        "observed_at",
        "available_at",
        "data_regime",
        "authority_scope",
        "source_fingerprint",
        "payload",
        "content_hash",
    }
)
_SNAPSHOT_PAYLOAD_FIELDS = frozenset({"complete", "items"})
_INSTRUMENT_FIELDS = frozenset(
    {
        "schema_version",
        "payload_schema_version",
        "record_kind",
        "asset_class",
        "identity",
        "aliases",
        "payload",
    }
)
_POSITION_FIELDS = frozenset(
    {
        "schema_version",
        "payload_schema_version",
        "record_kind",
        "asset_class",
        "identity",
        "payload",
    }
)
_EQUITY_INSTRUMENT_PAYLOAD_FIELDS = frozenset(
    {
        "instrument_type",
        "status",
        "tradable",
        "leveraged",
        "inverse",
        "price",
        "median_dollar_volume",
        "history_days",
    }
)
_DISABLED_INSTRUMENT_PAYLOAD_FIELDS = frozenset({"status", "tradable"})
_POSITION_PAYLOAD_FIELDS = frozenset({"quantity", "unit", "valuation"})
_VALUATION_FIELDS = frozenset({"amount", "currency", "source"})
_PERSISTED_FIELDS = frozenset(
    {
        "envelope_schema_version",
        "record_kind",
        "payload_discriminator",
        "payload_schema_version",
        "snapshot_id",
        "run_id",
        "cycle",
        "data_regime",
        "evidence_cutoff",
        "authority_scope",
        "material_fingerprints",
        "payload",
        "content_hash",
    }
)
_PERSISTED_PAYLOAD_FIELDS = frozenset({"policy", "inputs", "subjects"})
_FINGERPRINT_FIELDS = frozenset({"eligibility_policy", "instrument_snapshot", "position_snapshot"})
_PLAIN_DECIMAL = re.compile(r"-?[0-9]+(?:\.[0-9]+)?\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAXIMUM_DECIMAL_TEXT_LENGTH = 64


@dataclass(frozen=True, slots=True)
class RecordedUniverseSource:
    """Expose one immutable hostile representation through the universe input port."""

    payload: object

    def load(self) -> UniverseInputs | UniverseRefusal:
        return parse_recorded_universe(self.payload)


@dataclass(frozen=True, slots=True)
class _SnapshotHeader:
    observed_at: datetime
    available_at: datetime
    data_regime: str
    source_fingerprint: str
    content_hash: str
    items: tuple[object, ...]


def parse_recorded_universe(value: object) -> UniverseInputs | UniverseRefusal:
    """Validate complete recorded inputs and return only normalized typed facts."""
    parsed_root = _parse_recorded_root(value)
    if isinstance(parsed_root, UniverseRefusal):
        return parsed_root
    root, data_regime, cutoff = parsed_root
    return _parse_recorded_snapshots(root, data_regime=data_regime, cutoff=cutoff)


def parse_recorded_position_snapshot(value: object) -> PositionSnapshot | None:
    """Validate one standalone durable position snapshot."""
    fields = _mapping(value, _SNAPSHOT_FIELDS)
    if fields is None:
        return None
    parsed = _parse_position_snapshot(fields)
    return None if isinstance(parsed, UniverseRefusal) else parsed


def _parse_recorded_root(
    value: object,
) -> tuple[dict[str, object], str, datetime] | UniverseRefusal:
    if value is None:
        return UniverseRefusal(UniverseRefusalCode.MISSING_INPUT)
    root = _mapping(value, _ROOT_FIELDS)
    if root is None:
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    version = root["schema_version"]
    data_regime = root["data_regime"]
    cutoff = _aware_timestamp(root["evidence_cutoff"])
    if (
        not is_universe_schema_version(version)
        or not _is_literal(root["record_kind"], "universe_inputs")
        or not is_data_regime(data_regime)
        or cutoff is None
    ):
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    return root, data_regime, cutoff


def _parse_recorded_snapshots(
    root: dict[str, object],
    *,
    data_regime: str,
    cutoff: datetime,
) -> UniverseInputs | UniverseRefusal:
    instrument_fields = _mapping(root["instruments"], _SNAPSHOT_FIELDS)
    position_fields = _mapping(root["positions"], _SNAPSHOT_FIELDS)
    if instrument_fields is None or position_fields is None:
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    instruments = _parse_instrument_snapshot(instrument_fields)
    positions = _parse_position_snapshot(position_fields)
    if isinstance(instruments, UniverseRefusal):
        return instruments
    if isinstance(positions, UniverseRefusal):
        return positions
    if instruments.available_at.value > cutoff or positions.available_at.value > cutoff:
        return UniverseRefusal(UniverseRefusalCode.CONTRADICTORY_INPUT)
    if instruments.data_regime != data_regime or positions.data_regime != data_regime:
        return UniverseRefusal(UniverseRefusalCode.CONTRADICTORY_INPUT)
    return UniverseInputs.create(
        data_regime=data_regime,
        evidence_cutoff=cutoff,
        instrument_snapshot=instruments,
        position_snapshot=positions,
    )


def parse_persisted_universe_snapshot(
    value: object,
    *,
    expected_run_id: str,
    expected_snapshot_id: str,
    recorded_at: UtcInstant,
) -> UniverseSnapshot | UniverseRefusal:
    """Revalidate a durable snapshot and every material input at the SQLite boundary."""
    root = _parse_persisted_root(value)
    if isinstance(root, UniverseRefusal):
        return root
    snapshot = _rebuild_persisted_snapshot(
        root,
        expected_run_id=expected_run_id,
        expected_snapshot_id=expected_snapshot_id,
        recorded_at=recorded_at,
    )
    if isinstance(snapshot, UniverseRefusal) or snapshot.to_json() != value:
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    return snapshot


def _parse_persisted_root(value: object) -> dict[str, object] | UniverseRefusal:
    if type(value) is not str:
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    try:
        decoded: object = json.loads(value)
    except (ValueError, RecursionError):
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    root = _mapping(decoded, _PERSISTED_FIELDS)
    if (
        root is None
        or not is_universe_schema_version(root["envelope_schema_version"])
        or not _is_literal(root["record_kind"], "eligible_universe_snapshot")
        or not _is_literal(root["payload_discriminator"], "equity_eligible_universe")
        or not is_universe_schema_version(root["payload_schema_version"])
        or not _is_literal(root["authority_scope"], "research_attention")
    ):
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    return root


def _rebuild_persisted_snapshot(
    root: dict[str, object],
    *,
    expected_run_id: str,
    expected_snapshot_id: str,
    recorded_at: UtcInstant,
) -> UniverseSnapshot | UniverseRefusal:
    payload = _mapping(root["payload"], _PERSISTED_PAYLOAD_FIELDS)
    fingerprints = _mapping(root["material_fingerprints"], _FINGERPRINT_FIELDS)
    cycle = parse_decision_cycle_identity(root["cycle"])
    if payload is None or fingerprints is None or cycle is None:
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    policy = EquityUniversePolicy.parse(payload["policy"])
    inputs = parse_recorded_universe(payload["inputs"])
    if isinstance(policy, UniverseRefusal) or isinstance(inputs, UniverseRefusal):
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    if any(not is_alpaca_paper_identity(identity) for identity in policy.etf_allowlist):
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    snapshot = build_universe_snapshot(
        expected_run_id,
        cycle,
        inputs,
        policy,
        enabled_asset_classes=(AssetClass.US_EQUITY,),
        recorded_at=recorded_at,
    )
    if isinstance(snapshot, UniverseRefusal):
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    if (
        root["snapshot_id"] != expected_snapshot_id
        or root["content_hash"] != snapshot.content_hash
        or root["run_id"] != expected_run_id
        or root["data_regime"] != inputs.data_regime
        or root["evidence_cutoff"] != inputs.evidence_cutoff.isoformat()
        or fingerprints["eligibility_policy"] != policy.fingerprint
        or fingerprints["instrument_snapshot"] != inputs.instrument_snapshot.material_fingerprint
        or fingerprints["position_snapshot"] != inputs.position_snapshot.fingerprint
        or snapshot.snapshot_id != expected_snapshot_id
    ):
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    return snapshot


def _parse_instrument_snapshot(
    fields: dict[str, object],
) -> InstrumentSnapshot | UniverseRefusal:
    header = _snapshot_header(
        fields,
        record_kind="instrument_snapshot",
        payload_discriminator="recorded_instrument_snapshot",
        authority_scope="market_data_observation",
    )
    if isinstance(header, UniverseRefusal):
        return header
    if not header.items:
        return UniverseRefusal(UniverseRefusalCode.MISSING_INPUT)
    parsed: list[InstrumentObservation] = []
    for item in header.items:
        item_fields = _mapping(item, _INSTRUMENT_FIELDS)
        if item_fields is None:
            return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
        instrument = _parse_instrument(item_fields)
        if instrument is None:
            return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
        parsed.append(instrument)
    snapshot = InstrumentSnapshot.create(
        observed_at=header.observed_at,
        available_at=header.available_at,
        data_regime=header.data_regime,
        source_fingerprint=header.source_fingerprint,
        instruments=tuple(parsed),
    )
    if isinstance(snapshot, UniverseRefusal):
        return snapshot
    return (
        snapshot
        if snapshot.fingerprint == header.content_hash
        else UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    )


def _parse_instrument(  # noqa: PLR0911 - each closed observation variant fails independently.
    fields: dict[str, object],
) -> InstrumentObservation | None:
    if (
        not is_universe_schema_version(fields["schema_version"])
        or not is_universe_schema_version(fields["payload_schema_version"])
        or not _is_literal(fields["record_kind"], "instrument_observation")
    ):
        return None
    identity = parse_instrument_identity(fields["identity"])
    aliases = _parse_aliases(fields["aliases"])
    if (
        identity is None
        or not is_alpaca_paper_identity(identity)
        or aliases is None
        or type(fields["asset_class"]) is not str
        or fields["asset_class"] != identity.asset_class.value
    ):
        return None
    payload = fields["payload"]
    if isinstance(identity, EquityInstrumentIdentity):
        return _parse_equity_instrument(identity, aliases, payload)
    disabled_payload = _mapping(payload, _DISABLED_INSTRUMENT_PAYLOAD_FIELDS)
    if disabled_payload is None:
        return None
    status = _instrument_status(disabled_payload["status"])
    tradable = disabled_payload["tradable"]
    if status is None or type(tradable) is not bool:
        return None
    if isinstance(identity, CryptoSpotInstrumentIdentity):
        return CryptoSpotInstrument(identity, aliases, status, tradable)
    return ListedOptionInstrument(identity, aliases, status, tradable)


def _parse_equity_instrument(
    identity: EquityInstrumentIdentity,
    aliases: tuple[InstrumentAlias, ...],
    value: object,
) -> EquityInstrument | None:
    payload = _mapping(value, _EQUITY_INSTRUMENT_PAYLOAD_FIELDS)
    if payload is None:
        return None
    instrument_type = _instrument_type(payload["instrument_type"])
    status = _instrument_status(payload["status"])
    tradable = payload["tradable"]
    leveraged = payload["leveraged"]
    inverse = payload["inverse"]
    price = parse_nonnegative_decimal(payload["price"])
    volume = parse_nonnegative_decimal(payload["median_dollar_volume"])
    history_days = parse_history_days(payload["history_days"])
    if (
        instrument_type is None
        or status is None
        or type(tradable) is not bool
        or type(leveraged) is not bool
        or type(inverse) is not bool
        or price is None
        or volume is None
        or history_days is None
    ):
        return None
    return EquityInstrument(
        identity,
        aliases,
        instrument_type,
        status,
        tradable,
        leveraged,
        inverse,
        price,
        volume,
        history_days,
    )


def _parse_position_snapshot(fields: dict[str, object]) -> PositionSnapshot | UniverseRefusal:
    header = _snapshot_header(
        fields,
        record_kind="position_snapshot",
        payload_discriminator="recorded_position_snapshot",
        authority_scope="portfolio_observation",
    )
    if isinstance(header, UniverseRefusal):
        return header
    parsed: list[PositionObservation] = []
    for item in header.items:
        item_fields = _mapping(item, _POSITION_FIELDS)
        if item_fields is None:
            return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
        position = _parse_position(item_fields)
        if position is None:
            return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
        parsed.append(position)
    snapshot = PositionSnapshot.create(
        observed_at=header.observed_at,
        available_at=header.available_at,
        data_regime=header.data_regime,
        source_fingerprint=header.source_fingerprint,
        positions=tuple(parsed),
    )
    if isinstance(snapshot, UniverseRefusal):
        return snapshot
    if snapshot.fingerprint != header.content_hash:
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    return snapshot


def _parse_position(  # noqa: PLR0911 - each closed position variant fails independently.
    fields: dict[str, object],
) -> PositionObservation | None:
    if (
        not is_universe_schema_version(fields["schema_version"])
        or not is_universe_schema_version(fields["payload_schema_version"])
        or not _is_literal(fields["record_kind"], "position_observation")
    ):
        return None
    identity = parse_instrument_identity(fields["identity"])
    payload = _mapping(fields["payload"], _POSITION_PAYLOAD_FIELDS)
    if (
        identity is None
        or not is_alpaca_paper_identity(identity)
        or payload is None
        or type(fields["asset_class"]) is not str
        or fields["asset_class"] != identity.asset_class.value
    ):
        return None
    quantity = _signed_nonzero_decimal(payload["quantity"])
    unit = payload["unit"]
    valuation = _parse_valuation(payload["valuation"])
    if quantity is None or type(unit) is not str or valuation is None:
        return None
    if type(identity) is EquityInstrumentIdentity and unit == "share":
        return EquityPosition(identity, quantity, valuation)
    if type(identity) is CryptoSpotInstrumentIdentity and unit == identity.base_currency:
        return CryptoSpotPosition(identity, quantity, valuation)
    if (
        type(identity) is ListedOptionInstrumentIdentity
        and unit == "contract"
        and quantity == quantity.to_integral_value()
    ):
        return ListedOptionPosition(identity, quantity, valuation)
    return None


def _parse_valuation(value: object) -> PositionValuation | None:
    fields = _mapping(value, _VALUATION_FIELDS)
    if fields is None:
        return None
    amount = _decimal(fields["amount"])
    currency = fields["currency"]
    source = fields["source"]
    if amount is None or type(currency) is not str or type(source) is not str:
        return None
    try:
        return PositionValuation(amount, currency, source)
    except ValueError:
        return None


def _snapshot_header(
    fields: dict[str, object],
    *,
    record_kind: str,
    payload_discriminator: str,
    authority_scope: str,
) -> _SnapshotHeader | UniverseRefusal:
    if (
        not is_universe_schema_version(fields["envelope_schema_version"])
        or not is_universe_schema_version(fields["payload_schema_version"])
        or not _is_literal(fields["record_kind"], record_kind)
        or not _is_literal(fields["payload_discriminator"], payload_discriminator)
        or not _is_literal(fields["authority_scope"], authority_scope)
    ):
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    observed_at = _aware_timestamp(fields["observed_at"])
    available_at = _aware_timestamp(fields["available_at"])
    data_regime = fields["data_regime"]
    source_fingerprint = fields["source_fingerprint"]
    content_hash = fields["content_hash"]
    payload = _mapping(fields["payload"], _SNAPSHOT_PAYLOAD_FIELDS)
    if payload is None:
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    if payload["complete"] is not True:
        return UniverseRefusal(UniverseRefusalCode.MISSING_INPUT)
    items = _object_list(payload["items"])
    if (
        observed_at is None
        or available_at is None
        or observed_at > available_at
        or not is_data_regime(data_regime)
        or not _is_sha256(source_fingerprint)
        or not _is_sha256(content_hash)
        or items is None
    ):
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    return _SnapshotHeader(
        observed_at,
        available_at,
        data_regime,
        source_fingerprint,
        content_hash,
        items,
    )


def _parse_aliases(value: object) -> tuple[InstrumentAlias, ...] | None:
    items = _object_list(value)
    if items is None:
        return None
    aliases: list[InstrumentAlias] = []
    for item in items:
        alias = parse_instrument_alias(item)
        if alias is None:
            return None
        aliases.append(alias)
    if len(set(aliases)) != len(aliases):
        return None
    return tuple(sorted(aliases, key=lambda alias: (alias.namespace, alias.value)))


def is_alpaca_paper_identity(identity: InstrumentIdentity) -> bool:
    """Require the provider-and-environment namespace composed by the V0 adapter."""
    if type(identity) not in (
        EquityInstrumentIdentity,
        CryptoSpotInstrumentIdentity,
        ListedOptionInstrumentIdentity,
    ):
        return False
    if identity.catalog_namespace != _ALPACA_PAPER_CATALOG_NAMESPACE:
        return False
    if type(identity) is ListedOptionInstrumentIdentity:
        return identity.underlying.catalog_namespace == _ALPACA_PAPER_CATALOG_NAMESPACE
    return True


def _is_literal(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _instrument_type(value: object) -> EquityInstrumentType | None:
    if type(value) is not str:
        return None
    try:
        return EquityInstrumentType(value)
    except (TypeError, ValueError):
        return None


def _instrument_status(value: object) -> InstrumentStatus | None:
    if type(value) is not str:
        return None
    try:
        return InstrumentStatus(value)
    except (TypeError, ValueError):
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


def _object_list(value: object) -> tuple[object, ...] | None:
    if type(value) is not list:
        return None
    return tuple(value)


def _aware_timestamp(value: object) -> datetime | None:
    if type(value) is not str:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if value not in (
        datetime.isoformat(parsed),
        datetime.isoformat(parsed, timespec="microseconds"),
    ):
        return None
    try:
        return UtcInstant.from_datetime(parsed).value
    except InvalidUtcInstantError:
        return None


def _signed_nonzero_decimal(value: object) -> Decimal | None:
    parsed = _decimal(value)
    return parsed if parsed is not None and parsed != 0 else None


def _decimal(value: object) -> Decimal | None:
    if (
        type(value) is not str
        or len(value) > _MAXIMUM_DECIMAL_TEXT_LENGTH
        or _PLAIN_DECIMAL.fullmatch(value) is None
    ):
        return None
    parsed = Decimal(value)
    canonical = format(parsed, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    if parsed == 0:
        canonical = "0"
    return parsed if canonical == value else None


def _is_sha256(value: object) -> TypeGuard[str]:
    return type(value) is str and _SHA256.fullmatch(value) is not None
