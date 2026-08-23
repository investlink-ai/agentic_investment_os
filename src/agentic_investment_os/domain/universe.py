"""Define deterministic eligible-universe policy and immutable snapshot values."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, Self, TypeGuard

from agentic_investment_os.domain.identity import (
    AssetClass,
    CryptoSpotInstrumentIdentity,
    DecisionCycleIdentity,
    EquityInstrumentIdentity,
    InstrumentAlias,
    InstrumentIdentity,
    ListedOptionInstrumentIdentity,
    MarketSession,
    canonical_instrument_bytes,
    parse_instrument_identity,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant

__all__ = (
    "CryptoSpotInstrument",
    "CryptoSpotPosition",
    "EquityInstrument",
    "EquityInstrumentType",
    "EquityPosition",
    "EquityUniversePolicy",
    "InstrumentObservation",
    "InstrumentSnapshot",
    "InstrumentStatus",
    "ListedOptionInstrument",
    "ListedOptionPosition",
    "PositionDisposition",
    "PositionObservation",
    "PositionSnapshot",
    "PositionValuation",
    "UniverseInputIdentity",
    "UniverseInputSource",
    "UniverseInputs",
    "UniverseRefusal",
    "UniverseRefusalCode",
    "UniverseSnapshot",
    "UniverseSubject",
    "build_universe_snapshot",
    "is_data_regime",
    "is_universe_schema_version",
    "parse_history_days",
    "parse_nonnegative_decimal",
)

_SCHEMA_VERSION = 1
_ENVELOPE_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DATA_REGIME = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_SOURCE_ID = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_CURRENCY = re.compile(r"[A-Z][A-Z0-9]{1,11}\Z")
_EXCHANGE = re.compile(r"[A-Z][A-Z0-9.]{0,15}\Z")
_PLAIN_DECIMAL = re.compile(r"-?[0-9]+(?:\.[0-9]+)?\Z")
_MAJOR_US_EXCHANGES = frozenset({"ARCA", "NASDAQ", "NYSE"})
_MAXIMUM_DECIMAL_TEXT_LENGTH = 64
_MAXIMUM_HISTORY_DAYS = 1_000_000
_MAXIMUM_TIMEDELTA_SECONDS = 86_399_999_999_999
_INVALID_POSITION_VALUATION = "invalid position valuation"
_INVALID_ABSOLUTE_INSTANT = "universe absolute instant must be canonical"
_POLICY_FIELDS = frozenset(
    {
        "asset_class",
        "schema_version",
        "data_regime",
        "approved_exchanges",
        "etf_allowlist",
        "minimum_price",
        "minimum_median_dollar_volume",
        "minimum_history_days",
        "maximum_snapshot_age_seconds",
        "policy_type",
    }
)


class UniverseRefusalCode(StrEnum):
    """Classify bounded failures at the recorded-universe boundary."""

    MISSING_INPUT = "missing_universe_input"
    INVALID_INPUT = "invalid_universe_input"
    STALE_INPUT = "stale_universe_input"
    CONTRADICTORY_INPUT = "contradictory_universe_input"


class EquityInstrumentType(StrEnum):
    """Name the equity instrument forms enabled by the V0 policy."""

    COMMON_STOCK = "common_stock"
    ETF = "etf"


class InstrumentStatus(StrEnum):
    """Normalize availability without importing an adapter vocabulary."""

    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class UniverseRefusal:
    """Return a bounded failure without retaining hostile input values."""

    code: UniverseRefusalCode


@dataclass(frozen=True, slots=True)
class EquityUniversePolicy:
    """Carry the complete discriminated equity eligibility policy."""

    schema_version: int
    data_regime: str
    approved_exchanges: tuple[str, ...]
    etf_allowlist: tuple[EquityInstrumentIdentity, ...]
    minimum_price: Decimal
    minimum_median_dollar_volume: Decimal
    minimum_history_days: int
    maximum_snapshot_age_seconds: int
    fingerprint: str

    @classmethod
    def parse(cls, value: object) -> Self | UniverseRefusal:
        """Validate a complete untrusted equity policy without applying defaults."""
        if (
            type(value) is not dict
            or any(type(key) is not str for key in value)
            or set(value) != _POLICY_FIELDS
        ):
            return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
        asset_class = value.get("asset_class")
        policy_type = value.get("policy_type")
        schema_version = value.get("schema_version")
        data_regime = value.get("data_regime")
        approved_exchanges = _string_tuple(value.get("approved_exchanges"), _EXCHANGE)
        etf_allowlist = _equity_identity_tuple(value.get("etf_allowlist"))
        minimum_price = parse_nonnegative_decimal(value.get("minimum_price"))
        minimum_volume = parse_nonnegative_decimal(value.get("minimum_median_dollar_volume"))
        minimum_history_days = parse_history_days(value.get("minimum_history_days"))
        maximum_age = _positive_integer(value.get("maximum_snapshot_age_seconds"))
        if (
            type(asset_class) is not str
            or asset_class != AssetClass.US_EQUITY.value
            or type(policy_type) is not str
            or policy_type != "equity_universe"
            or not is_universe_schema_version(schema_version)
            or type(data_regime) is not str
            or _DATA_REGIME.fullmatch(data_regime) is None
            or approved_exchanges is None
            or not set(approved_exchanges).issubset(_MAJOR_US_EXCHANGES)
            or etf_allowlist is None
            or minimum_price is None
            or minimum_volume is None
            or minimum_history_days is None
            or maximum_age is None
            or maximum_age > _MAXIMUM_TIMEDELTA_SECONDS
        ):
            return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
        material = _policy_payload(
            data_regime=data_regime,
            approved_exchanges=approved_exchanges,
            etf_allowlist=etf_allowlist,
            minimum_price=minimum_price,
            minimum_volume=minimum_volume,
            minimum_history_days=minimum_history_days,
            maximum_age=maximum_age,
        )
        return cls(
            schema_version=_SCHEMA_VERSION,
            data_regime=data_regime,
            approved_exchanges=approved_exchanges,
            etf_allowlist=etf_allowlist,
            minimum_price=minimum_price,
            minimum_median_dollar_volume=minimum_volume,
            minimum_history_days=minimum_history_days,
            maximum_snapshot_age_seconds=maximum_age,
            fingerprint=_fingerprint(material),
        )

    def to_payload(self) -> dict[str, object]:
        return _policy_payload(
            data_regime=self.data_regime,
            approved_exchanges=self.approved_exchanges,
            etf_allowlist=self.etf_allowlist,
            minimum_price=self.minimum_price,
            minimum_volume=self.minimum_median_dollar_volume,
            minimum_history_days=self.minimum_history_days,
            maximum_age=self.maximum_snapshot_age_seconds,
        )


@dataclass(frozen=True, slots=True)
class EquityInstrument:
    """Carry one validated equity observation and its alias provenance."""

    identity: EquityInstrumentIdentity
    aliases: tuple[InstrumentAlias, ...]
    instrument_type: EquityInstrumentType
    status: InstrumentStatus
    tradable: bool
    leveraged: bool
    inverse: bool
    price: Decimal
    median_dollar_volume: Decimal
    history_days: int

    def to_payload(self) -> dict[str, object]:
        return _instrument_envelope(
            identity=self.identity,
            aliases=self.aliases,
            payload={
                "history_days": self.history_days,
                "instrument_type": self.instrument_type.value,
                "inverse": self.inverse,
                "leveraged": self.leveraged,
                "median_dollar_volume": _decimal_text(self.median_dollar_volume),
                "price": _decimal_text(self.price),
                "status": self.status.value,
                "tradable": self.tradable,
            },
        )


@dataclass(frozen=True, slots=True)
class CryptoSpotInstrument:
    """Carry one disabled crypto observation without authorizing eligibility."""

    identity: CryptoSpotInstrumentIdentity
    aliases: tuple[InstrumentAlias, ...]
    status: InstrumentStatus
    tradable: bool

    def to_payload(self) -> dict[str, object]:
        return _instrument_envelope(
            identity=self.identity,
            aliases=self.aliases,
            payload={"status": self.status.value, "tradable": self.tradable},
        )


@dataclass(frozen=True, slots=True)
class ListedOptionInstrument:
    """Carry one disabled listed-option observation without authorizing eligibility."""

    identity: ListedOptionInstrumentIdentity
    aliases: tuple[InstrumentAlias, ...]
    status: InstrumentStatus
    tradable: bool

    def to_payload(self) -> dict[str, object]:
        return _instrument_envelope(
            identity=self.identity,
            aliases=self.aliases,
            payload={"status": self.status.value, "tradable": self.tradable},
        )


type InstrumentObservation = EquityInstrument | CryptoSpotInstrument | ListedOptionInstrument


@dataclass(frozen=True, slots=True)
class PositionValuation:
    """Preserve an exact observed valuation and its source provenance."""

    amount: Decimal
    currency: str
    source: str

    def __post_init__(self) -> None:
        if (
            type(self.amount) is not Decimal
            or not self.amount.is_finite()
            or type(self.currency) is not str
            or _CURRENCY.fullmatch(self.currency) is None
            or type(self.source) is not str
            or _SOURCE_ID.fullmatch(self.source) is None
        ):
            raise ValueError(_INVALID_POSITION_VALUATION)

    def to_payload(self) -> dict[str, object]:
        return {
            "amount": _decimal_text(self.amount),
            "currency": self.currency,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class EquityPosition:
    """Carry a signed equity position in explicit share units."""

    identity: EquityInstrumentIdentity
    quantity: Decimal
    valuation: PositionValuation

    def to_payload(self) -> dict[str, object]:
        return _position_envelope(
            identity=self.identity,
            payload={
                "quantity": _decimal_text(self.quantity),
                "unit": "share",
                "valuation": self.valuation.to_payload(),
            },
        )


@dataclass(frozen=True, slots=True)
class CryptoSpotPosition:
    """Carry a signed crypto position in its explicit base-currency unit."""

    identity: CryptoSpotInstrumentIdentity
    quantity: Decimal
    valuation: PositionValuation

    def to_payload(self) -> dict[str, object]:
        return _position_envelope(
            identity=self.identity,
            payload={
                "quantity": _decimal_text(self.quantity),
                "unit": self.identity.base_currency,
                "valuation": self.valuation.to_payload(),
            },
        )


@dataclass(frozen=True, slots=True)
class ListedOptionPosition:
    """Carry a signed whole-contract option position."""

    identity: ListedOptionInstrumentIdentity
    quantity: Decimal
    valuation: PositionValuation

    def to_payload(self) -> dict[str, object]:
        return _position_envelope(
            identity=self.identity,
            payload={
                "quantity": _decimal_text(self.quantity),
                "unit": "contract",
                "valuation": self.valuation.to_payload(),
            },
        )


type PositionObservation = EquityPosition | CryptoSpotPosition | ListedOptionPosition


@dataclass(frozen=True, slots=True)
class InstrumentSnapshot:
    """Pin one complete availability-aware instrument observation set."""

    observed_at: UtcInstant
    available_at: UtcInstant
    data_regime: str
    source_fingerprint: str
    instruments: tuple[InstrumentObservation, ...]
    material_fingerprint: str
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        observed_at: datetime,
        available_at: datetime,
        data_regime: str,
        source_fingerprint: str,
        instruments: tuple[InstrumentObservation, ...],
    ) -> Self | UniverseRefusal:
        normalized_times = _normalize_snapshot_metadata(
            observed_at,
            available_at,
            data_regime,
            source_fingerprint,
        )
        if normalized_times is None:
            return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
        normalized_observed, normalized_available = normalized_times
        ordered = tuple(
            sorted(instruments, key=lambda item: canonical_instrument_bytes(item.identity))
        )
        identities = tuple(canonical_instrument_bytes(item.identity) for item in ordered)
        if len(set(identities)) != len(identities) or not _has_one_to_one_mappings(ordered):
            return UniverseRefusal(UniverseRefusalCode.CONTRADICTORY_INPUT)
        payload = _snapshot_payload(
            record_kind="instrument_snapshot",
            payload_discriminator="recorded_instrument_snapshot",
            authority_scope="market_data_observation",
            observed_at=normalized_observed,
            available_at=normalized_available,
            data_regime=data_regime,
            source_fingerprint=source_fingerprint,
            items=ordered,
        )
        return cls(
            normalized_observed,
            normalized_available,
            data_regime,
            source_fingerprint,
            ordered,
            _content_hash(
                _instrument_snapshot_material(
                    observed_at=normalized_observed,
                    available_at=normalized_available,
                    data_regime=data_regime,
                    source_fingerprint=source_fingerprint,
                    instruments=ordered,
                )
            ),
            _content_hash(payload),
        )

    def to_payload(self) -> dict[str, object]:
        material = _snapshot_payload(
            record_kind="instrument_snapshot",
            payload_discriminator="recorded_instrument_snapshot",
            authority_scope="market_data_observation",
            observed_at=self.observed_at,
            available_at=self.available_at,
            data_regime=self.data_regime,
            source_fingerprint=self.source_fingerprint,
            items=self.instruments,
        )
        return {**material, "content_hash": self.fingerprint}


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """Pin one complete availability-aware position observation set."""

    observed_at: UtcInstant
    available_at: UtcInstant
    data_regime: str
    source_fingerprint: str
    positions: tuple[PositionObservation, ...]
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        observed_at: datetime,
        available_at: datetime,
        data_regime: str,
        source_fingerprint: str,
        positions: tuple[PositionObservation, ...],
    ) -> Self | UniverseRefusal:
        normalized_times = _normalize_snapshot_metadata(
            observed_at,
            available_at,
            data_regime,
            source_fingerprint,
        )
        if normalized_times is None:
            return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
        normalized_observed, normalized_available = normalized_times
        ordered = tuple(
            sorted(positions, key=lambda item: canonical_instrument_bytes(item.identity))
        )
        identities = tuple(canonical_instrument_bytes(item.identity) for item in ordered)
        if len(set(identities)) != len(identities):
            return UniverseRefusal(UniverseRefusalCode.CONTRADICTORY_INPUT)
        payload = _snapshot_payload(
            record_kind="position_snapshot",
            payload_discriminator="recorded_position_snapshot",
            authority_scope="portfolio_observation",
            observed_at=normalized_observed,
            available_at=normalized_available,
            data_regime=data_regime,
            source_fingerprint=source_fingerprint,
            items=ordered,
        )
        return cls(
            normalized_observed,
            normalized_available,
            data_regime,
            source_fingerprint,
            ordered,
            _content_hash(payload),
        )

    def to_payload(self) -> dict[str, object]:
        material = _snapshot_payload(
            record_kind="position_snapshot",
            payload_discriminator="recorded_position_snapshot",
            authority_scope="portfolio_observation",
            observed_at=self.observed_at,
            available_at=self.available_at,
            data_regime=self.data_regime,
            source_fingerprint=self.source_fingerprint,
            items=self.positions,
        )
        return {**material, "content_hash": self.fingerprint}


@dataclass(frozen=True, slots=True)
class UniverseInputs:
    """Pin complete normalized instrument and position snapshots at one cutoff."""

    data_regime: str
    evidence_cutoff: UtcInstant
    instrument_snapshot: InstrumentSnapshot
    position_snapshot: PositionSnapshot

    @classmethod
    def create(
        cls,
        *,
        data_regime: str,
        evidence_cutoff: datetime,
        instrument_snapshot: InstrumentSnapshot,
        position_snapshot: PositionSnapshot,
    ) -> Self | UniverseRefusal:
        snapshot_instants = (
            instrument_snapshot.observed_at,
            instrument_snapshot.available_at,
            position_snapshot.observed_at,
            position_snapshot.available_at,
        )
        if any(type(instant) is not UtcInstant for instant in snapshot_instants):
            return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
        try:
            cutoff = UtcInstant.from_datetime(evidence_cutoff)
            for instant in snapshot_instants:
                instant.isoformat()
        except InvalidUtcInstantError:
            return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
        return cls(data_regime, cutoff, instrument_snapshot, position_snapshot)

    def to_payload(self) -> dict[str, object]:
        return {
            "record_kind": "universe_inputs",
            "schema_version": _SCHEMA_VERSION,
            "data_regime": self.data_regime,
            "evidence_cutoff": _instant_text(self.evidence_cutoff),
            "instruments": self.instrument_snapshot.to_payload(),
            "positions": self.position_snapshot.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class UniverseInputIdentity:
    """Pin the material input and policy facts carried into a lifecycle run."""

    data_regime: str
    evidence_cutoff: UtcInstant
    instrument_snapshot_hash: str
    position_snapshot_hash: str
    eligibility_policy_hash: str

    @classmethod
    def from_inputs(
        cls,
        inputs: UniverseInputs,
        policy: EquityUniversePolicy,
    ) -> Self | UniverseRefusal:
        if not _has_canonical_input_instants(inputs):
            return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
        return cls(
            data_regime=inputs.data_regime,
            evidence_cutoff=inputs.evidence_cutoff,
            instrument_snapshot_hash=inputs.instrument_snapshot.material_fingerprint,
            position_snapshot_hash=inputs.position_snapshot.fingerprint,
            eligibility_policy_hash=policy.fingerprint,
        )


class UniverseInputSource(Protocol):
    """Load one complete recorded universe boundary representation."""

    def load(self) -> UniverseInputs | UniverseRefusal: ...


class UniverseExclusionReason(StrEnum):
    """Explain why an observed asset cannot accept a new entry."""

    INACTIVE = "inactive"
    NOT_TRADABLE = "not_tradable"
    UNSUPPORTED_ASSET_CLASS = "unsupported_asset_class"
    UNAPPROVED_EXCHANGE = "unapproved_exchange"
    ETF_NOT_ALLOWLISTED = "etf_not_allowlisted"
    LEVERAGED = "leveraged"
    INVERSE = "inverse"
    PRICE_BELOW_MINIMUM = "price_below_minimum"
    LIQUIDITY_BELOW_MINIMUM = "liquidity_below_minimum"
    INSUFFICIENT_HISTORY = "insufficient_history"


class PositionDisposition(StrEnum):
    """State how an observed position enters downstream attention."""

    REFRESH_REQUIRED = "refresh_required"
    NOT_APPLICABLE = "not_applicable"
    PORTFOLIO_MISMATCH = "portfolio_mismatch"


@dataclass(frozen=True, slots=True)
class UniverseSubject:
    """Record one eligible instrument or retained position by canonical identity."""

    identity: InstrumentIdentity
    aliases: tuple[InstrumentAlias, ...]
    is_position: bool
    eligible_for_new_entry: bool
    position_disposition: PositionDisposition
    exclusion_reasons: tuple[UniverseExclusionReason, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "identity": self.identity.to_payload(),
            "aliases": [alias.to_payload() for alias in self.aliases],
            "is_position": self.is_position,
            "eligible_for_new_entry": self.eligible_for_new_entry,
            "position_disposition": self.position_disposition.value,
            "exclusion_reasons": [reason.value for reason in self.exclusion_reasons],
        }


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    """Preserve reconstructable inputs, policy, subjects, and immutable identity."""

    snapshot_id: str
    content_hash: str
    run_id: str
    cycle: DecisionCycleIdentity
    policy: EquityUniversePolicy
    inputs: UniverseInputs
    subjects: tuple[UniverseSubject, ...]

    def to_payload(self) -> dict[str, object]:
        material = _universe_snapshot_envelope(
            snapshot_id=self.snapshot_id,
            run_id=self.run_id,
            cycle=self.cycle,
            policy=self.policy,
            inputs=self.inputs,
            subjects=self.subjects,
        )
        return {**material, "content_hash": self.content_hash}

    def to_json(self) -> str:
        """Serialize the complete snapshot in one canonical durable representation."""
        return _canonical_json(self.to_payload())


def build_universe_snapshot(  # noqa: PLR0913 - durable authority inputs remain explicit.
    run_id: str,
    cycle: DecisionCycleIdentity,
    inputs: UniverseInputs,
    policy: EquityUniversePolicy,
    *,
    enabled_asset_classes: tuple[AssetClass, ...],
    recorded_at: UtcInstant,
) -> UniverseSnapshot | UniverseRefusal:
    """Evaluate equity membership while retaining every mapped position explicitly."""
    if (
        _SHA256.fullmatch(run_id) is None
        or inputs.data_regime != policy.data_regime
        or inputs.instrument_snapshot.data_regime != inputs.data_regime
        or inputs.position_snapshot.data_regime != inputs.data_regime
        or type(cycle) is not MarketSession
        or enabled_asset_classes != (AssetClass.US_EQUITY,)
    ):
        return UniverseRefusal(UniverseRefusalCode.CONTRADICTORY_INPUT)
    recording_time_refusal = _recording_time_refusal(inputs, recorded_at)
    if recording_time_refusal is not None:
        return recording_time_refusal
    cutoff = inputs.evidence_cutoff.value
    maximum_age = timedelta(seconds=policy.maximum_snapshot_age_seconds)
    for observed_at, available_at in (
        (
            inputs.instrument_snapshot.observed_at.value,
            inputs.instrument_snapshot.available_at.value,
        ),
        (
            inputs.position_snapshot.observed_at.value,
            inputs.position_snapshot.available_at.value,
        ),
    ):
        if available_at > cutoff:
            return UniverseRefusal(UniverseRefusalCode.CONTRADICTORY_INPUT)
        age = cutoff - observed_at
        if age > maximum_age:
            return UniverseRefusal(UniverseRefusalCode.STALE_INPUT)

    instruments_by_identity = {
        canonical_instrument_bytes(item.identity): item
        for item in inputs.instrument_snapshot.instruments
    }
    positions_by_identity = {
        canonical_instrument_bytes(item.identity): item
        for item in inputs.position_snapshot.positions
    }
    if not positions_by_identity.keys() <= instruments_by_identity.keys():
        return UniverseRefusal(UniverseRefusalCode.CONTRADICTORY_INPUT)

    subjects: list[UniverseSubject] = []
    for instrument in inputs.instrument_snapshot.instruments:
        position = positions_by_identity.get(canonical_instrument_bytes(instrument.identity))
        if isinstance(instrument, EquityInstrument):
            reasons = _exclusion_reasons(instrument, policy)
            if not reasons or position is not None:
                subjects.append(
                    UniverseSubject(
                        identity=instrument.identity,
                        aliases=instrument.aliases,
                        is_position=position is not None,
                        eligible_for_new_entry=not reasons,
                        position_disposition=(
                            PositionDisposition.REFRESH_REQUIRED
                            if position is not None
                            else PositionDisposition.NOT_APPLICABLE
                        ),
                        exclusion_reasons=reasons,
                    )
                )
        elif position is not None:
            subjects.append(
                UniverseSubject(
                    identity=instrument.identity,
                    aliases=instrument.aliases,
                    is_position=True,
                    eligible_for_new_entry=False,
                    position_disposition=PositionDisposition.PORTFOLIO_MISMATCH,
                    exclusion_reasons=(UniverseExclusionReason.UNSUPPORTED_ASSET_CLASS,),
                )
            )
    ordered_subjects = tuple(
        sorted(subjects, key=lambda subject: canonical_instrument_bytes(subject.identity))
    )
    identity_material = _universe_snapshot_identity(
        run_id=run_id,
        cycle=cycle,
        policy=policy,
        inputs=inputs,
        subjects=ordered_subjects,
    )
    snapshot_id = _fingerprint(identity_material)
    material = _universe_snapshot_envelope(
        snapshot_id=snapshot_id,
        run_id=run_id,
        cycle=cycle,
        policy=policy,
        inputs=inputs,
        subjects=ordered_subjects,
    )
    return UniverseSnapshot(
        snapshot_id=snapshot_id,
        content_hash=_fingerprint(material),
        run_id=run_id,
        cycle=cycle,
        policy=policy,
        inputs=inputs,
        subjects=ordered_subjects,
    )


def _universe_snapshot_envelope(  # noqa: PLR0913 - envelope binds all authority inputs.
    *,
    snapshot_id: str,
    run_id: str,
    cycle: DecisionCycleIdentity,
    policy: EquityUniversePolicy,
    inputs: UniverseInputs,
    subjects: tuple[UniverseSubject, ...],
) -> dict[str, object]:
    return {
        "envelope_schema_version": _ENVELOPE_SCHEMA_VERSION,
        "record_kind": "eligible_universe_snapshot",
        "payload_discriminator": "equity_eligible_universe",
        "payload_schema_version": _SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "run_id": run_id,
        "cycle": cycle.to_payload(),
        "data_regime": inputs.data_regime,
        "evidence_cutoff": _instant_text(inputs.evidence_cutoff),
        "authority_scope": "research_attention",
        "material_fingerprints": {
            "eligibility_policy": policy.fingerprint,
            "instrument_snapshot": inputs.instrument_snapshot.material_fingerprint,
            "position_snapshot": inputs.position_snapshot.fingerprint,
        },
        "payload": {
            "inputs": inputs.to_payload(),
            "policy": policy.to_payload(),
            "subjects": [subject.to_payload() for subject in subjects],
        },
    }


def _universe_snapshot_identity(
    *,
    run_id: str,
    cycle: DecisionCycleIdentity,
    policy: EquityUniversePolicy,
    inputs: UniverseInputs,
    subjects: tuple[UniverseSubject, ...],
) -> dict[str, object]:
    return {
        "identity_kind": "eligible_universe_snapshot",
        "identity_schema_version": _SCHEMA_VERSION,
        "run_id": run_id,
        "cycle": cycle.to_payload(),
        "data_regime": inputs.data_regime,
        "evidence_cutoff": _instant_text(inputs.evidence_cutoff),
        "material_fingerprints": {
            "eligibility_policy": policy.fingerprint,
            "instrument_snapshot": inputs.instrument_snapshot.material_fingerprint,
            "position_snapshot": inputs.position_snapshot.fingerprint,
        },
        "subjects": [_subject_identity_payload(subject) for subject in subjects],
    }


def _subject_identity_payload(subject: UniverseSubject) -> dict[str, object]:
    return {
        "identity": subject.identity.to_payload(),
        "is_position": subject.is_position,
        "eligible_for_new_entry": subject.eligible_for_new_entry,
        "position_disposition": subject.position_disposition.value,
        "exclusion_reasons": [reason.value for reason in subject.exclusion_reasons],
    }


def _instrument_snapshot_material(
    *,
    observed_at: UtcInstant,
    available_at: UtcInstant,
    data_regime: str,
    source_fingerprint: str,
    instruments: tuple[InstrumentObservation, ...],
) -> dict[str, object]:
    return {
        "fingerprint_kind": "instrument_snapshot_material",
        "fingerprint_schema_version": _SCHEMA_VERSION,
        "observed_at": _instant_text(observed_at),
        "available_at": _instant_text(available_at),
        "data_regime": data_regime,
        "source_fingerprint": source_fingerprint,
        "items": [
            {key: value for key, value in instrument.to_payload().items() if key != "aliases"}
            for instrument in instruments
        ],
    }


def _recording_time_refusal(
    inputs: UniverseInputs,
    recorded_at: UtcInstant,
) -> UniverseRefusal | None:
    try:
        if type(recorded_at) is not UtcInstant or not _has_canonical_input_instants(inputs):
            return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
        recorded_at.isoformat()
    except InvalidUtcInstantError:
        return UniverseRefusal(UniverseRefusalCode.INVALID_INPUT)
    if inputs.evidence_cutoff.value > recorded_at.value:
        return UniverseRefusal(UniverseRefusalCode.CONTRADICTORY_INPUT)
    return None


def _has_canonical_input_instants(inputs: UniverseInputs) -> bool:
    input_instants = (
        inputs.evidence_cutoff,
        inputs.instrument_snapshot.observed_at,
        inputs.instrument_snapshot.available_at,
        inputs.position_snapshot.observed_at,
        inputs.position_snapshot.available_at,
    )
    if any(type(instant) is not UtcInstant for instant in input_instants):
        return False
    try:
        for instant in input_instants:
            instant.isoformat()
    except InvalidUtcInstantError:
        return False
    return True


def is_data_regime(value: object) -> TypeGuard[str]:
    """Recognize the bounded identifier used to pin comparable data inputs."""
    return type(value) is str and _DATA_REGIME.fullmatch(value) is not None


def is_universe_schema_version(value: object) -> TypeGuard[int]:
    """Recognize the exact integer version shared by universe artifacts."""
    return type(value) is int and value == _SCHEMA_VERSION


def _has_one_to_one_mappings(instruments: tuple[InstrumentObservation, ...]) -> bool:
    catalog_bindings: dict[tuple[str, str], bytes] = {}
    alias_bindings: dict[InstrumentAlias, bytes] = {}
    for instrument in instruments:
        if not instrument.aliases:
            return False
        identity_bytes = canonical_instrument_bytes(instrument.identity)
        for identity in _identity_lineage(instrument.identity):
            key = (identity.catalog_namespace, identity.catalog_id)
            canonical = canonical_instrument_bytes(identity)
            existing = catalog_bindings.get(key)
            if existing is not None and existing != canonical:
                return False
            catalog_bindings[key] = canonical
        for alias in instrument.aliases:
            existing = alias_bindings.get(alias)
            if existing is not None and existing != identity_bytes:
                return False
            alias_bindings[alias] = identity_bytes
    return True


def _identity_lineage(identity: InstrumentIdentity) -> tuple[InstrumentIdentity, ...]:
    if type(identity) is ListedOptionInstrumentIdentity:
        return (identity, identity.underlying)
    return (identity,)


def _exclusion_reasons(
    instrument: EquityInstrument,
    policy: EquityUniversePolicy,
) -> tuple[UniverseExclusionReason, ...]:
    reasons: list[UniverseExclusionReason] = []
    if instrument.status is not InstrumentStatus.ACTIVE:
        reasons.append(UniverseExclusionReason.INACTIVE)
    if not instrument.tradable:
        reasons.append(UniverseExclusionReason.NOT_TRADABLE)
    if (
        instrument.instrument_type is EquityInstrumentType.ETF
        and instrument.identity not in policy.etf_allowlist
    ):
        reasons.append(UniverseExclusionReason.ETF_NOT_ALLOWLISTED)
    if instrument.identity.listing_venue not in policy.approved_exchanges:
        reasons.append(UniverseExclusionReason.UNAPPROVED_EXCHANGE)
    if instrument.leveraged:
        reasons.append(UniverseExclusionReason.LEVERAGED)
    if instrument.inverse:
        reasons.append(UniverseExclusionReason.INVERSE)
    if instrument.price < policy.minimum_price:
        reasons.append(UniverseExclusionReason.PRICE_BELOW_MINIMUM)
    if instrument.median_dollar_volume < policy.minimum_median_dollar_volume:
        reasons.append(UniverseExclusionReason.LIQUIDITY_BELOW_MINIMUM)
    if instrument.history_days < policy.minimum_history_days:
        reasons.append(UniverseExclusionReason.INSUFFICIENT_HISTORY)
    return tuple(reasons)


def _string_tuple(
    value: object,
    pattern: re.Pattern[str],
    *,
    allow_empty: bool = False,
) -> tuple[str, ...] | None:
    if type(value) is not list:
        return None
    if not value and not allow_empty:
        return None
    if any(type(item) is not str or pattern.fullmatch(item) is None for item in value):
        return None
    strings = tuple(item for item in value if type(item) is str)
    if len(set(strings)) != len(strings):
        return None
    return tuple(sorted(strings))


def _equity_identity_tuple(value: object) -> tuple[EquityInstrumentIdentity, ...] | None:
    if type(value) is not list:
        return None
    parsed: list[EquityInstrumentIdentity] = []
    for item in value:
        identity = parse_instrument_identity(item)
        if type(identity) is not EquityInstrumentIdentity:
            return None
        parsed.append(identity)
    ordered = tuple(sorted(parsed, key=canonical_instrument_bytes))
    if len(set(ordered)) != len(ordered):
        return None
    return ordered


def _policy_payload(  # noqa: PLR0913 - policy serialization must name every material field.
    *,
    data_regime: str,
    approved_exchanges: tuple[str, ...],
    etf_allowlist: tuple[EquityInstrumentIdentity, ...],
    minimum_price: Decimal,
    minimum_volume: Decimal,
    minimum_history_days: int,
    maximum_age: int,
) -> dict[str, object]:
    return {
        "asset_class": AssetClass.US_EQUITY.value,
        "schema_version": _SCHEMA_VERSION,
        "policy_type": "equity_universe",
        "data_regime": data_regime,
        "approved_exchanges": list(approved_exchanges),
        "etf_allowlist": [identity.to_payload() for identity in etf_allowlist],
        "minimum_price": _decimal_text(minimum_price),
        "minimum_median_dollar_volume": _decimal_text(minimum_volume),
        "minimum_history_days": minimum_history_days,
        "maximum_snapshot_age_seconds": maximum_age,
    }


def _instrument_envelope(
    *,
    identity: InstrumentIdentity,
    aliases: tuple[InstrumentAlias, ...],
    payload: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "payload_schema_version": _SCHEMA_VERSION,
        "record_kind": "instrument_observation",
        "asset_class": identity.asset_class.value,
        "identity": identity.to_payload(),
        "aliases": [alias.to_payload() for alias in aliases],
        "payload": payload,
    }


def _position_envelope(
    *, identity: InstrumentIdentity, payload: dict[str, object]
) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "payload_schema_version": _SCHEMA_VERSION,
        "record_kind": "position_observation",
        "asset_class": identity.asset_class.value,
        "identity": identity.to_payload(),
        "payload": payload,
    }


def _snapshot_payload(  # noqa: PLR0913 - the common envelope names every provenance field.
    *,
    record_kind: str,
    payload_discriminator: str,
    authority_scope: str,
    observed_at: UtcInstant,
    available_at: UtcInstant,
    data_regime: str,
    source_fingerprint: str,
    items: tuple[InstrumentObservation, ...] | tuple[PositionObservation, ...],
) -> dict[str, object]:
    return {
        "envelope_schema_version": _ENVELOPE_SCHEMA_VERSION,
        "payload_schema_version": _SCHEMA_VERSION,
        "record_kind": record_kind,
        "payload_discriminator": payload_discriminator,
        "observed_at": _instant_text(observed_at),
        "available_at": _instant_text(available_at),
        "data_regime": data_regime,
        "authority_scope": authority_scope,
        "source_fingerprint": source_fingerprint,
        "payload": {
            "complete": True,
            "items": [item.to_payload() for item in items],
        },
    }


def _normalize_snapshot_metadata(
    observed_at: datetime,
    available_at: datetime,
    data_regime: str,
    source_fingerprint: str,
) -> tuple[UtcInstant, UtcInstant] | None:
    try:
        normalized_observed = UtcInstant.from_datetime(observed_at)
        normalized_available = UtcInstant.from_datetime(available_at)
    except InvalidUtcInstantError:
        return None
    if (
        normalized_observed.value <= normalized_available.value
        and is_data_regime(data_regime)
        and type(source_fingerprint) is str
        and _SHA256.fullmatch(source_fingerprint) is not None
    ):
        return normalized_observed, normalized_available
    return None


def _instant_text(value: object) -> str:
    if type(value) is not UtcInstant:
        raise InvalidUtcInstantError(_INVALID_ABSOLUTE_INSTANT)
    return value.isoformat()


def _content_hash(value: object) -> str:
    return _fingerprint(value)


def parse_nonnegative_decimal(value: object) -> Decimal | None:
    """Parse one bounded plain-decimal representation without expanding exponents."""
    if (
        type(value) is not str
        or len(value) > _MAXIMUM_DECIMAL_TEXT_LENGTH
        or _PLAIN_DECIMAL.fullmatch(value) is None
    ):
        return None
    parsed = Decimal(value)
    if parsed < 0:
        return None
    return parsed


def parse_history_days(value: object) -> int | None:
    """Parse a non-negative, operationally bounded history-day count."""
    if type(value) is not int or value < 0 or value > _MAXIMUM_HISTORY_DAYS:
        return None
    return value


def _positive_integer(value: object) -> int | None:
    if type(value) is not int or value <= 0:
        return None
    return value


def _decimal_text(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    # Decimal's fixed-point format treats "f" and "F" identically.
    text = format(value, "f")
    while "." in text and text.endswith("0"):
        text = text[:-1]
    return text.removesuffix(".")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()
