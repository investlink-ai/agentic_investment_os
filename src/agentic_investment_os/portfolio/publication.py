"""Construct immutable Champion decisions and signed Balanced packets."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    MarketSession,
    canonical_instrument_bytes,
    parse_decision_cycle_identity,
    parse_instrument_identity,
)
from agentic_investment_os.domain.lifecycle import (
    DecisionCheckpoint,
    DecisionCheckpointReference,
    PortfolioShadowKind,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.portfolio.construction import (
    HouseView,
    PortfolioConstructionResult,
    PortfolioTradeReason,
    TargetBand,
    _content_hash,
    _decimal_text,
    _exact_mapping,
    _is_hash,
    _plain_decimal,
    _risk_input_payload,
)
from agentic_investment_os.portfolio.shadows import PortfolioCycleResult

__all__ = (
    "ChampionDecisionRecord",
    "DecisionPacket",
    "DecisionPacketAccountScope",
    "DecisionPacketSigner",
    "DecisionPacketValidityWindow",
    "DecisionPacketVerifier",
    "DecisionPacketWindowSource",
    "DecisionPublicationHistoryValidator",
    "DecisionPublicationLedger",
    "DecisionPublicationRefusalReason",
    "DecisionPublicationResult",
    "PacketDirection",
    "PacketInstruction",
    "PacketNoActionReason",
    "PacketSignature",
    "construct_decision_publication",
    "parse_champion_decision_record",
    "parse_decision_packet",
    "validate_decision_publication",
)

_INVALID_SCOPE = "invalid DecisionPacket account scope"
_INVALID_WINDOW = "invalid DecisionPacket validity window"
_INVALID_SIGNATURE = "invalid DecisionPacket signature"
_INVALID_INSTRUCTION = "invalid DecisionPacket instruction"
_INVALID_DECISION = "invalid Champion Decision Record"
_INVALID_PACKET = "invalid DecisionPacket"
_INVALID_PUBLICATION = "invalid decision publication result"
_SIGNATURE_SCHEME = "hmac-sha256-v1"
_DECISION_MATERIAL_KEYS = (
    "configuration",
    "constitution",
    "research_policy",
    "model",
    "universe_snapshot",
    "portfolio_policy",
    "portfolio_input",
    "cost_policy",
    "house_view",
    "balanced_result",
    "position_snapshot",
    "benchmark_state",
    "conservative_shadow",
    "growth_shadow",
    "equal_weight_shadow",
)


class DecisionPublicationRefusalReason(StrEnum):
    """Bound pre-publication failures that cannot authorize a packet."""

    INVALID_PORTFOLIO = "invalid_portfolio"
    INVALID_FORECASTS = "invalid_forecasts"
    MISSING_BENCHMARK_STATE = "missing_benchmark_state"
    INVALID_VALIDITY_WINDOW = "invalid_validity_window"
    SIGNING_FAILED = "signing_failed"


class PacketNoActionReason(StrEnum):
    """Explain why one valid Champion decision publishes no executable packet."""

    NO_AUTHORIZED_ADJUSTMENTS = "no_authorized_adjustments"


class PacketDirection(StrEnum):
    """Restrict the executor to one weight-change direction."""

    INCREASE = "increase"
    DECREASE = "decrease"


@dataclass(frozen=True, slots=True)
class DecisionPacketAccountScope:
    """Carry a non-secret paper-account scope without a broker account identifier."""

    broker: str
    environment: str
    scope_id: str

    def __post_init__(self) -> None:
        if self.broker != "alpaca" or self.environment != "paper" or not _is_hash(self.scope_id):
            raise ValueError(_INVALID_SCOPE)

    def to_payload(self) -> dict[str, object]:
        return {
            "broker": self.broker,
            "environment": self.environment,
            "scope_id": self.scope_id,
        }


@dataclass(frozen=True, slots=True)
class DecisionPacketValidityWindow:
    """Bind one packet to a cycle and canonical issue and expiry instants."""

    cycle: MarketSession
    issued_at: UtcInstant
    expires_at: UtcInstant

    def __post_init__(self) -> None:
        if (
            type(self.cycle) is not MarketSession
            or type(self.issued_at) is not UtcInstant
            or type(self.expires_at) is not UtcInstant
            or self.issued_at.value >= self.expires_at.value
        ):
            raise ValueError(_INVALID_WINDOW)


@dataclass(frozen=True, slots=True)
class PacketSignature:
    """Carry one detached signature and its non-secret verification identity."""

    scheme: str
    key_id: str
    value: str

    def __post_init__(self) -> None:
        if (
            self.scheme != _SIGNATURE_SCHEME
            or not _is_hash(self.key_id)
            or not _is_hash(self.value)
        ):
            raise ValueError(_INVALID_SIGNATURE)

    def to_payload(self) -> dict[str, object]:
        return {"scheme": self.scheme, "key_id": self.key_id, "value": self.value}


class DecisionPacketSigner(Protocol):
    """Sign canonical packet material without exposing key material to portfolio code."""

    def sign(self, material: bytes) -> PacketSignature: ...


class DecisionPacketVerifier(Protocol):
    """Verify canonical packet material at a receiving or persistence boundary."""

    def verify(self, material: bytes, signature: PacketSignature) -> bool: ...


class DecisionPacketWindowSource(Protocol):
    """Resolve and revalidate packet authority times for one market session."""

    def window_for(
        self,
        cycle: MarketSession,
        recorded_at: UtcInstant,
    ) -> DecisionPacketValidityWindow | DecisionPublicationRefusalReason: ...

    def allows_publication(
        self,
        window: DecisionPacketValidityWindow,
        recorded_at: UtcInstant,
    ) -> bool: ...


class DecisionPublicationLedger(Protocol):
    """Publish one atomic decision record and optional complete signed packet."""

    def record_publication(
        self,
        run_id: str,
        result: DecisionPublicationResult,
        recorded_at: UtcInstant,
    ) -> DecisionCheckpoint | DecisionPublicationRefusalReason: ...

    def replay_publication(
        self,
        run_id: str,
        cycle: MarketSession,
    ) -> DecisionCheckpoint | None: ...

    def validate_reference(self, reference: DecisionCheckpointReference) -> None: ...

    def validate_history(self, references: tuple[DecisionCheckpointReference, ...]) -> None: ...


class DecisionPublicationHistoryValidator(Protocol):
    """Validate durable decisions and packets named by lifecycle history."""

    def validate_reference(self, reference: DecisionCheckpointReference) -> None: ...

    def validate_history(self, references: tuple[DecisionCheckpointReference, ...]) -> None: ...


@dataclass(frozen=True, slots=True)
class PacketInstruction:
    """Freeze one Balanced weight adjustment without quote or order-price discretion."""

    identity: EquityInstrumentIdentity
    direction: PacketDirection
    target_band_id: str
    current_weight: Decimal
    target_weight: Decimal
    authorized_weight: Decimal
    lower_weight: Decimal
    upper_weight: Decimal
    reason: PortfolioTradeReason

    def __post_init__(self) -> None:
        weights = (
            self.current_weight,
            self.target_weight,
            self.authorized_weight,
            self.lower_weight,
            self.upper_weight,
        )
        if (
            type(self.identity) is not EquityInstrumentIdentity
            or type(self.direction) is not PacketDirection
            or not _is_hash(self.target_band_id)
            or any(
                type(item) is not Decimal or not item.is_finite() or not Decimal(0) <= item <= 1
                for item in weights
            )
            or self.authorized_weight == self.current_weight
            or (
                self.direction is PacketDirection.INCREASE
                and self.authorized_weight <= self.current_weight
            )
            or (
                self.direction is PacketDirection.DECREASE
                and self.authorized_weight >= self.current_weight
            )
            or not self.lower_weight <= self.upper_weight
            or type(self.reason) is not PortfolioTradeReason
            or self.reason
            in (
                PortfolioTradeReason.IN_BAND,
                PortfolioTradeReason.BELOW_MINIMUM_NOTIONAL,
                PortfolioTradeReason.EVENT_BLOCKED,
            )
        ):
            raise ValueError(_INVALID_INSTRUCTION)

    def to_payload(self) -> dict[str, object]:
        return {
            "identity": self.identity.to_payload(),
            "direction": self.direction.value,
            "target_band_id": self.target_band_id,
            "current_weight": _decimal_text(self.current_weight),
            "target_weight": _decimal_text(self.target_weight),
            "authorized_weight": _decimal_text(self.authorized_weight),
            "lower_weight": _decimal_text(self.lower_weight),
            "upper_weight": _decimal_text(self.upper_weight),
            "reason": self.reason.value,
        }


@dataclass(frozen=True, slots=True)
class ChampionDecisionRecord:
    """Bind one immutable ex-ante Champion decision to all material portfolio inputs."""

    schema_version: int
    run_id: str
    cycle: MarketSession
    evidence_cutoff: UtcInstant
    house_view_id: str
    balanced_result_id: str
    forecast_ids: tuple[str, ...]
    target_band_ids: tuple[str, ...]
    benchmark_identity: EquityInstrumentIdentity
    benchmark_state_id: str
    position_snapshot_id: str
    cash: Decimal
    cash_currency: str
    shadow_account_ids: tuple[tuple[PortfolioShadowKind, str], ...]
    constitution_version: int
    constitution_hash: str
    portfolio_policy_id: str
    cost_policy_id: str
    data_regime: str
    source_fingerprint: str
    model_fingerprint: str
    material_fingerprints: tuple[tuple[str, str], ...]
    decision_record_id: str

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or not all(
                _is_hash(item)
                for item in (
                    self.run_id,
                    self.house_view_id,
                    self.balanced_result_id,
                    self.benchmark_state_id,
                    self.position_snapshot_id,
                    self.constitution_hash,
                    self.portfolio_policy_id,
                    self.cost_policy_id,
                    self.source_fingerprint,
                    self.model_fingerprint,
                )
            )
            or (self.decision_record_id != "" and not _is_hash(self.decision_record_id))
            or type(self.cycle) is not MarketSession
            or type(self.evidence_cutoff) is not UtcInstant
            or type(self.forecast_ids) is not tuple
            or not _valid_hash_tuple(self.forecast_ids)
            or type(self.target_band_ids) is not tuple
            or not _valid_hash_tuple(self.target_band_ids)
            or type(self.benchmark_identity) is not EquityInstrumentIdentity
            or type(self.cash) is not Decimal
            or not self.cash.is_finite()
            or self.cash < 0
            or self.cash_currency != "USD"
            or tuple(kind for kind, _ in self.shadow_account_ids)
            != (
                PortfolioShadowKind.CONSERVATIVE,
                PortfolioShadowKind.GROWTH,
                PortfolioShadowKind.EQUAL_WEIGHT,
            )
            or any(not _is_hash(account_id) for _, account_id in self.shadow_account_ids)
            or type(self.constitution_version) is not int
            or self.constitution_version < 1
            or type(self.data_regime) is not str
            or not self.data_regime
            or tuple(key for key, _ in self.material_fingerprints) != _DECISION_MATERIAL_KEYS
            or any(not _is_hash(value) for _, value in self.material_fingerprints)
            or (
                self.decision_record_id != ""
                and self.decision_record_id != _content_hash(_decision_material(self))
            )
        ):
            raise ValueError(_INVALID_DECISION)

    def to_payload(self) -> dict[str, object]:
        material = _decision_material(self)
        if self.decision_record_id != _content_hash(material):
            raise ValueError(_INVALID_DECISION)
        return {**material, "decision_record_id": self.decision_record_id}


@dataclass(frozen=True, slots=True)
class DecisionPacket:
    """Carry one complete signed Balanced-only authorization for independent validation."""

    schema_version: int
    packet_id: str
    run_id: str
    cycle: MarketSession
    issued_at: UtcInstant
    expires_at: UtcInstant
    account_scope: DecisionPacketAccountScope
    decision_record_id: str
    portfolio_policy_id: str
    cost_policy_id: str
    maximum_gross_weight: Decimal
    maximum_name_weight: Decimal
    maximum_sector_weight: Decimal
    maximum_common_cause_weight: Decimal
    maximum_correlation_cluster_weight: Decimal
    maximum_fraction_of_median_dollar_volume: Decimal
    instructions: tuple[PacketInstruction, ...]
    authority_scope: str
    risk_profile: str
    asset_class: str
    quantity_unit: str
    order_policy: str
    leverage_allowed: bool
    signature: PacketSignature
    content_hash: str

    def __post_init__(self) -> None:
        limits = (
            self.maximum_gross_weight,
            self.maximum_name_weight,
            self.maximum_sector_weight,
            self.maximum_common_cause_weight,
            self.maximum_correlation_cluster_weight,
            self.maximum_fraction_of_median_dollar_volume,
        )
        instruction_keys = tuple(
            canonical_instrument_bytes(item.identity) for item in self.instructions
        )
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or not all(
                _is_hash(item)
                for item in (
                    self.run_id,
                    self.decision_record_id,
                    self.portfolio_policy_id,
                    self.cost_policy_id,
                )
            )
            or (self.packet_id != "" and not _is_hash(self.packet_id))
            or (self.content_hash != "" and not _is_hash(self.content_hash))
            or type(self.cycle) is not MarketSession
            or type(self.issued_at) is not UtcInstant
            or type(self.expires_at) is not UtcInstant
            or self.issued_at.value >= self.expires_at.value
            or type(self.account_scope) is not DecisionPacketAccountScope
            or any(
                type(item) is not Decimal or not item.is_finite() or not Decimal(0) < item <= 1
                for item in limits
            )
            or type(self.instructions) is not tuple
            or not self.instructions
            or any(type(item) is not PacketInstruction for item in self.instructions)
            or instruction_keys != tuple(sorted(set(instruction_keys)))
            or type(self.signature) is not PacketSignature
            or self.authority_scope != "balanced_paper_execution"
            or self.risk_profile != "balanced"
            or self.asset_class != "us_equity"
            or self.quantity_unit != "whole_share"
            or self.order_policy != "regular_session_day_limit_v1"
            or self.leverage_allowed is not False
            or (
                self.packet_id != ""
                and self.packet_id != _content_hash(_packet_identity_material(self))
            )
            or (
                self.content_hash != ""
                and self.content_hash != _content_hash(_packet_signed_material(self))
            )
        ):
            raise ValueError(_INVALID_PACKET)

    def signing_bytes(self) -> bytes:
        """Return the exact canonical bytes covered by the detached signature."""
        if not _is_hash(self.packet_id):
            raise ValueError(_INVALID_PACKET)
        return _canonical_json(_packet_signing_material(self)).encode()

    def to_payload(self) -> dict[str, object]:
        self.__post_init__()
        if not _is_hash(self.packet_id) or not _is_hash(self.content_hash):
            raise ValueError(_INVALID_PACKET)
        return {**_packet_signed_material(self), "content_hash": self.content_hash}


@dataclass(frozen=True, slots=True)
class DecisionPublicationResult:
    """Carry one immutable decision and its optional executable packet."""

    decision_record: ChampionDecisionRecord
    packet: DecisionPacket | None
    no_action_reason: PacketNoActionReason | None

    def __post_init__(self) -> None:
        if (
            type(self.decision_record) is not ChampionDecisionRecord
            or (self.packet is not None and type(self.packet) is not DecisionPacket)
            or ((self.packet is None) != (self.no_action_reason is not None))
            or (
                self.packet is not None
                and (
                    self.packet.decision_record_id != self.decision_record.decision_record_id
                    or self.packet.run_id != self.decision_record.run_id
                    or self.packet.cycle != self.decision_record.cycle
                )
            )
            or (
                self.no_action_reason is not None
                and type(self.no_action_reason) is not PacketNoActionReason
            )
        ):
            raise ValueError(_INVALID_PUBLICATION)


def _validated_decision_source(
    cycle_result: PortfolioCycleResult,
) -> tuple[PortfolioConstructionResult, HouseView] | DecisionPublicationRefusalReason:
    if type(cycle_result) is not PortfolioCycleResult or cycle_result.balanced.refusal is not None:
        return DecisionPublicationRefusalReason.INVALID_PORTFOLIO
    balanced = cycle_result.balanced
    house_view = balanced.require_house_view()
    return balanced, house_view


def _construct_champion_decision_record(
    cycle_result: PortfolioCycleResult,
    balanced: PortfolioConstructionResult,
    house_view: HouseView,
    *,
    benchmark_identity: EquityInstrumentIdentity,
) -> ChampionDecisionRecord | DecisionPublicationRefusalReason:
    if type(benchmark_identity) is not EquityInstrumentIdentity:
        return DecisionPublicationRefusalReason.MISSING_BENCHMARK_STATE
    conservative_shadow, _, _ = cycle_result.shadows
    inputs = conservative_shadow.portfolio_inputs
    benchmark = next(
        (item for item in inputs.risk_inputs if item.identity == benchmark_identity),
        None,
    )
    if benchmark is None:
        return DecisionPublicationRefusalReason.MISSING_BENCHMARK_STATE
    benchmark_state_id = _content_hash(_risk_input_payload(benchmark))
    policy = conservative_shadow.portfolio_policy
    shadow_ids = tuple((item.account_kind, item.account_id) for item in cycle_result.shadows)
    material_fingerprints = (
        ("configuration", house_view.configuration_hash),
        ("constitution", house_view.constitution_hash),
        ("research_policy", house_view.research_policy_hash),
        ("model", house_view.model_fingerprint),
        ("universe_snapshot", house_view.universe_snapshot_id),
        ("portfolio_policy", balanced.policy_id),
        ("portfolio_input", balanced.input_id),
        ("cost_policy", policy.cost_input_policy.policy_id),
        ("house_view", house_view.house_view_id),
        ("balanced_result", balanced.content_hash),
        ("position_snapshot", inputs.position_snapshot.fingerprint),
        ("benchmark_state", benchmark_state_id),
        *tuple((f"{kind.value}_shadow", account_id) for kind, account_id in shadow_ids),
    )
    provisional_record = ChampionDecisionRecord(
        schema_version=1,
        run_id=house_view.run_id,
        cycle=house_view.cycle,
        evidence_cutoff=house_view.evidence_cutoff,
        house_view_id=house_view.house_view_id,
        balanced_result_id=balanced.content_hash,
        forecast_ids=house_view.forecast_ids,
        target_band_ids=tuple(sorted(item.target_band_id for item in balanced.target_bands)),
        benchmark_identity=benchmark_identity,
        benchmark_state_id=benchmark_state_id,
        position_snapshot_id=inputs.position_snapshot.fingerprint,
        cash=inputs.cash,
        cash_currency=inputs.cash_currency,
        shadow_account_ids=shadow_ids,
        constitution_version=house_view.constitution_version,
        constitution_hash=house_view.constitution_hash,
        portfolio_policy_id=balanced.policy_id,
        cost_policy_id=policy.cost_input_policy.policy_id,
        data_regime=house_view.data_regime,
        source_fingerprint=_content_hash(inputs.source_identity),
        model_fingerprint=house_view.model_fingerprint,
        material_fingerprints=material_fingerprints,
        decision_record_id="",
    )
    return replace(
        provisional_record,
        decision_record_id=_content_hash(_decision_material(provisional_record)),
    )


def construct_decision_publication(  # noqa: PLR0911 - every authority input is explicit.
    cycle_result: PortfolioCycleResult,
    *,
    benchmark_identity: EquityInstrumentIdentity,
    account_scope: DecisionPacketAccountScope,
    validity_window: DecisionPacketValidityWindow | None,
    signer: DecisionPacketSigner,
) -> DecisionPublicationResult | DecisionPublicationRefusalReason:
    """Construct one ex-ante decision and at most one signed Balanced packet."""
    source = _validated_decision_source(cycle_result)
    if isinstance(source, DecisionPublicationRefusalReason):
        return source
    balanced, house_view = source
    decision_record = _construct_champion_decision_record(
        cycle_result,
        balanced,
        house_view,
        benchmark_identity=benchmark_identity,
    )
    if isinstance(decision_record, DecisionPublicationRefusalReason):
        return decision_record
    conservative_shadow, _, _ = cycle_result.shadows
    policy = conservative_shadow.portfolio_policy
    instructions = tuple(
        _packet_instruction(item) for item in balanced.target_bands if item.trade_eligible
    )
    if not instructions:
        return DecisionPublicationResult(
            decision_record,
            None,
            PacketNoActionReason.NO_AUTHORIZED_ADJUSTMENTS,
        )
    if (
        type(validity_window) is not DecisionPacketValidityWindow
        or validity_window.cycle != house_view.cycle
        or validity_window.issued_at.value < house_view.evidence_cutoff.value
    ):
        return DecisionPublicationRefusalReason.INVALID_VALIDITY_WINDOW
    provisional_packet = DecisionPacket(
        schema_version=1,
        packet_id="",
        run_id=house_view.run_id,
        cycle=house_view.cycle,
        issued_at=validity_window.issued_at,
        expires_at=validity_window.expires_at,
        account_scope=account_scope,
        decision_record_id=decision_record.decision_record_id,
        portfolio_policy_id=balanced.policy_id,
        cost_policy_id=policy.cost_input_policy.policy_id,
        maximum_gross_weight=policy.maximum_gross_weight,
        maximum_name_weight=policy.maximum_name_weight,
        maximum_sector_weight=policy.maximum_sector_weight,
        maximum_common_cause_weight=policy.maximum_common_cause_weight,
        maximum_correlation_cluster_weight=policy.maximum_correlation_cluster_weight,
        maximum_fraction_of_median_dollar_volume=(policy.maximum_fraction_of_median_dollar_volume),
        instructions=instructions,
        authority_scope="balanced_paper_execution",
        risk_profile="balanced",
        asset_class="us_equity",
        quantity_unit="whole_share",
        order_policy="regular_session_day_limit_v1",
        leverage_allowed=False,
        signature=PacketSignature(_SIGNATURE_SCHEME, "0" * 64, "0" * 64),
        content_hash="",
    )
    packet_id = _content_hash(_packet_identity_material(provisional_packet))
    unsigned_packet = replace(provisional_packet, packet_id=packet_id)
    try:
        signature = signer.sign(unsigned_packet.signing_bytes())
    except Exception:  # noqa: BLE001 - signer failures become a bounded refusal.
        return DecisionPublicationRefusalReason.SIGNING_FAILED
    if type(signature) is not PacketSignature:
        return DecisionPublicationRefusalReason.SIGNING_FAILED
    signed_packet = replace(unsigned_packet, signature=signature)
    packet = replace(
        signed_packet,
        content_hash=_content_hash(_packet_signed_material(signed_packet)),
    )
    return DecisionPublicationResult(decision_record, packet, None)


def validate_decision_publication(
    publication: DecisionPublicationResult,
    cycle_result: PortfolioCycleResult,
    *,
    benchmark_identity: EquityInstrumentIdentity,
) -> bool:
    """Reconstruct publication semantics from the cycle and trusted official benchmark."""
    if (
        type(publication) is not DecisionPublicationResult
        or publication.decision_record.benchmark_identity != benchmark_identity
    ):
        return False
    record = publication.decision_record
    packet = publication.packet
    if packet is None:
        source = _validated_decision_source(cycle_result)
        if isinstance(source, DecisionPublicationRefusalReason):
            return False
        balanced, house_view = source
        rebuilt_record = _construct_champion_decision_record(
            cycle_result,
            balanced,
            house_view,
            benchmark_identity=benchmark_identity,
        )
        return (
            rebuilt_record,
            publication.no_action_reason,
            any(item.trade_eligible for item in balanced.target_bands),
        ) == (
            record,
            PacketNoActionReason.NO_AUTHORIZED_ADJUSTMENTS,
            False,
        )
    account_scope = packet.account_scope
    validity_window = DecisionPacketValidityWindow(
        packet.cycle,
        packet.issued_at,
        packet.expires_at,
    )
    rebuilt = construct_decision_publication(
        cycle_result,
        benchmark_identity=benchmark_identity,
        account_scope=account_scope,
        validity_window=validity_window,
        signer=_StoredSignatureSigner(packet.signature),
    )
    return rebuilt == publication


@dataclass(frozen=True, slots=True)
class _StoredSignatureSigner:
    signature: PacketSignature

    def sign(self, material: bytes) -> PacketSignature:
        _ = material
        return self.signature


def parse_champion_decision_record(  # noqa: PLR0911 - fail closed at each hostile field.
    value: object,
) -> ChampionDecisionRecord | None:
    """Validate one hostile Champion Decision Record representation."""
    fields = _exact_mapping(
        value,
        {
            "schema_version",
            "record_kind",
            "authority_scope",
            "run_id",
            "cycle",
            "evidence_cutoff",
            "house_view_id",
            "balanced_result_id",
            "forecast_ids",
            "target_band_ids",
            "benchmark_identity",
            "benchmark_state_id",
            "position_snapshot_id",
            "cash",
            "cash_currency",
            "shadow_account_ids",
            "constitution_version",
            "constitution_hash",
            "portfolio_policy_id",
            "cost_policy_id",
            "data_regime",
            "source_fingerprint",
            "model_fingerprint",
            "material_fingerprints",
            "decision_record_id",
        },
    )
    if fields is None:
        return None
    if fields["record_kind"] != "champion_decision_record":
        return None
    if fields["authority_scope"] != "champion":
        return None
    schema_version = fields["schema_version"]
    if type(schema_version) is not int:
        return None
    constitution_version = fields["constitution_version"]
    if type(constitution_version) is not int:
        return None
    cycle = parse_decision_cycle_identity(fields["cycle"])
    benchmark = parse_instrument_identity(fields["benchmark_identity"])
    forecast_ids = _parse_hash_list(fields["forecast_ids"])
    target_band_ids = _parse_hash_list(fields["target_band_ids"])
    shadow_ids = _parse_shadow_ids(fields["shadow_account_ids"])
    fingerprints = _parse_fingerprints(fields["material_fingerprints"])
    try:
        cutoff = UtcInstant.parse(fields["evidence_cutoff"])
    except InvalidUtcInstantError:
        return None
    cash = _plain_decimal(fields["cash"])
    strings = _string_tuple(
        fields,
        (
            "run_id",
            "house_view_id",
            "balanced_result_id",
            "benchmark_state_id",
            "position_snapshot_id",
            "cash_currency",
            "constitution_hash",
            "portfolio_policy_id",
            "cost_policy_id",
            "data_regime",
            "source_fingerprint",
            "model_fingerprint",
            "decision_record_id",
        ),
    )
    match (
        cycle,
        benchmark,
        forecast_ids,
        target_band_ids,
        shadow_ids,
        fingerprints,
        cash,
        strings,
    ):
        case (
            MarketSession() as parsed_cycle,
            EquityInstrumentIdentity() as parsed_benchmark,
            tuple() as parsed_forecast_ids,
            tuple() as parsed_target_band_ids,
            tuple() as parsed_shadow_ids,
            tuple() as parsed_fingerprints,
            Decimal() as parsed_cash,
            tuple() as parsed_strings,
        ):
            pass
        case _:
            return None
    (
        run_id,
        house_view_id,
        balanced_result_id,
        benchmark_state_id,
        position_snapshot_id,
        cash_currency,
        constitution_hash,
        portfolio_policy_id,
        cost_policy_id,
        data_regime,
        source_fingerprint,
        model_fingerprint,
        decision_record_id,
    ) = parsed_strings
    try:
        record = ChampionDecisionRecord(
            schema_version,
            run_id,
            parsed_cycle,
            cutoff,
            house_view_id,
            balanced_result_id,
            parsed_forecast_ids,
            parsed_target_band_ids,
            parsed_benchmark,
            benchmark_state_id,
            position_snapshot_id,
            parsed_cash,
            cash_currency,
            parsed_shadow_ids,
            constitution_version,
            constitution_hash,
            portfolio_policy_id,
            cost_policy_id,
            data_regime,
            source_fingerprint,
            model_fingerprint,
            parsed_fingerprints,
            decision_record_id,
        )
    except (TypeError, ValueError):
        return None
    return record if record.to_payload() == fields else None


def parse_decision_packet(  # noqa: PLR0911 - fail closed at each hostile field.
    value: object,
    *,
    verifier: DecisionPacketVerifier,
) -> DecisionPacket | None:
    """Validate and verify one hostile serialized DecisionPacket."""
    fields = _exact_mapping(
        value,
        {
            "schema_version",
            "record_kind",
            "authority_scope",
            "risk_profile",
            "asset_class",
            "quantity_unit",
            "order_policy",
            "leverage_allowed",
            "packet_id",
            "run_id",
            "cycle",
            "issued_at",
            "expires_at",
            "account_scope",
            "decision_record_id",
            "portfolio_policy_id",
            "cost_policy_id",
            "risk_limits",
            "instructions",
            "signature",
            "content_hash",
        },
    )
    if fields is None or fields["record_kind"] != "decision_packet":
        return None
    schema_version = fields["schema_version"]
    if type(schema_version) is not int:
        return None
    leverage_allowed = fields["leverage_allowed"]
    if type(leverage_allowed) is not bool:
        return None
    cycle = parse_decision_cycle_identity(fields["cycle"])
    scope = _parse_scope(fields["account_scope"])
    signature = _parse_signature(fields["signature"])
    limits = _parse_risk_limits(fields["risk_limits"])
    instructions = _parse_instructions(fields["instructions"])
    try:
        issued_at = UtcInstant.parse(fields["issued_at"])
        expires_at = UtcInstant.parse(fields["expires_at"])
    except InvalidUtcInstantError:
        return None
    strings = _string_tuple(
        fields,
        (
            "packet_id",
            "run_id",
            "decision_record_id",
            "portfolio_policy_id",
            "cost_policy_id",
            "content_hash",
            "authority_scope",
            "risk_profile",
            "asset_class",
            "quantity_unit",
            "order_policy",
        ),
    )
    match (
        cycle,
        scope,
        signature,
        limits,
        instructions,
        strings,
    ):
        case (
            MarketSession() as parsed_cycle,
            DecisionPacketAccountScope() as parsed_scope,
            PacketSignature() as parsed_signature,
            tuple() as parsed_limits,
            tuple() as parsed_instructions,
            tuple() as parsed_strings,
        ):
            pass
        case _:
            return None
    (
        packet_id,
        run_id,
        decision_record_id,
        portfolio_policy_id,
        cost_policy_id,
        content_hash,
        authority_scope,
        risk_profile,
        asset_class,
        quantity_unit,
        order_policy,
    ) = parsed_strings
    (
        maximum_gross_weight,
        maximum_name_weight,
        maximum_sector_weight,
        maximum_common_cause_weight,
        maximum_correlation_cluster_weight,
        maximum_fraction_of_median_dollar_volume,
    ) = parsed_limits
    try:
        packet = DecisionPacket(
            schema_version=schema_version,
            packet_id=packet_id,
            run_id=run_id,
            cycle=parsed_cycle,
            issued_at=issued_at,
            expires_at=expires_at,
            account_scope=parsed_scope,
            decision_record_id=decision_record_id,
            portfolio_policy_id=portfolio_policy_id,
            cost_policy_id=cost_policy_id,
            maximum_gross_weight=maximum_gross_weight,
            maximum_name_weight=maximum_name_weight,
            maximum_sector_weight=maximum_sector_weight,
            maximum_common_cause_weight=maximum_common_cause_weight,
            maximum_correlation_cluster_weight=maximum_correlation_cluster_weight,
            maximum_fraction_of_median_dollar_volume=(maximum_fraction_of_median_dollar_volume),
            instructions=parsed_instructions,
            authority_scope=authority_scope,
            risk_profile=risk_profile,
            asset_class=asset_class,
            quantity_unit=quantity_unit,
            order_policy=order_policy,
            leverage_allowed=leverage_allowed,
            signature=parsed_signature,
            content_hash=content_hash,
        )
    except (TypeError, ValueError):
        return None
    try:
        verified = verifier.verify(packet.signing_bytes(), packet.signature)
    except Exception:  # noqa: BLE001 - verifier failures reject hostile packets.
        return None
    return packet if verified is True and packet.to_payload() == fields else None


def _packet_instruction(band: TargetBand) -> PacketInstruction:
    direction = {
        Decimal(-1): PacketDirection.DECREASE,
        Decimal(1): PacketDirection.INCREASE,
    }[band.adjustment_weight.compare(band.current_weight)]
    return PacketInstruction(
        band.identity,
        direction,
        band.target_band_id,
        band.current_weight,
        band.target_weight,
        band.adjustment_weight,
        band.lower_weight,
        band.upper_weight,
        band.trade_reason,
    )


def _decision_material(record: ChampionDecisionRecord) -> dict[str, object]:
    return {
        "schema_version": record.schema_version,
        "record_kind": "champion_decision_record",
        "authority_scope": "champion",
        "run_id": record.run_id,
        "cycle": record.cycle.to_payload(),
        "evidence_cutoff": record.evidence_cutoff.isoformat(),
        "house_view_id": record.house_view_id,
        "balanced_result_id": record.balanced_result_id,
        "forecast_ids": list(record.forecast_ids),
        "target_band_ids": list(record.target_band_ids),
        "benchmark_identity": record.benchmark_identity.to_payload(),
        "benchmark_state_id": record.benchmark_state_id,
        "position_snapshot_id": record.position_snapshot_id,
        "cash": _decimal_text(record.cash),
        "cash_currency": record.cash_currency,
        "shadow_account_ids": [
            {"account_kind": kind.value, "account_id": account_id}
            for kind, account_id in record.shadow_account_ids
        ],
        "constitution_version": record.constitution_version,
        "constitution_hash": record.constitution_hash,
        "portfolio_policy_id": record.portfolio_policy_id,
        "cost_policy_id": record.cost_policy_id,
        "data_regime": record.data_regime,
        "source_fingerprint": record.source_fingerprint,
        "model_fingerprint": record.model_fingerprint,
        "material_fingerprints": dict(record.material_fingerprints),
    }


def _packet_identity_material(packet: DecisionPacket) -> dict[str, object]:
    return {
        "schema_version": packet.schema_version,
        "record_kind": "decision_packet",
        "authority_scope": packet.authority_scope,
        "risk_profile": packet.risk_profile,
        "asset_class": packet.asset_class,
        "quantity_unit": packet.quantity_unit,
        "order_policy": packet.order_policy,
        "leverage_allowed": packet.leverage_allowed,
        "run_id": packet.run_id,
        "cycle": packet.cycle.to_payload(),
        "issued_at": packet.issued_at.isoformat(),
        "expires_at": packet.expires_at.isoformat(),
        "account_scope": packet.account_scope.to_payload(),
        "decision_record_id": packet.decision_record_id,
        "portfolio_policy_id": packet.portfolio_policy_id,
        "cost_policy_id": packet.cost_policy_id,
        "risk_limits": {
            "maximum_gross_weight": _decimal_text(packet.maximum_gross_weight),
            "maximum_name_weight": _decimal_text(packet.maximum_name_weight),
            "maximum_sector_weight": _decimal_text(packet.maximum_sector_weight),
            "maximum_common_cause_weight": _decimal_text(packet.maximum_common_cause_weight),
            "maximum_correlation_cluster_weight": _decimal_text(
                packet.maximum_correlation_cluster_weight
            ),
            "maximum_fraction_of_median_dollar_volume": _decimal_text(
                packet.maximum_fraction_of_median_dollar_volume
            ),
        },
        "instructions": [item.to_payload() for item in packet.instructions],
    }


def _packet_signing_material(packet: DecisionPacket) -> dict[str, object]:
    return {**_packet_identity_material(packet), "packet_id": packet.packet_id}


def _packet_signed_material(packet: DecisionPacket) -> dict[str, object]:
    return {**_packet_signing_material(packet), "signature": packet.signature.to_payload()}


def _parse_scope(value: object) -> DecisionPacketAccountScope | None:
    fields = _exact_mapping(value, {"broker", "environment", "scope_id"})
    strings = (
        None if fields is None else _string_tuple(fields, ("broker", "environment", "scope_id"))
    )
    if strings is None:
        return None
    broker, environment, scope_id = strings
    try:
        return DecisionPacketAccountScope(broker, environment, scope_id)
    except ValueError:
        return None


def _parse_signature(value: object) -> PacketSignature | None:
    fields = _exact_mapping(value, {"scheme", "key_id", "value"})
    strings = None if fields is None else _string_tuple(fields, ("scheme", "key_id", "value"))
    if strings is None:
        return None
    scheme, key_id, signature_value = strings
    try:
        return PacketSignature(scheme, key_id, signature_value)
    except ValueError:
        return None


def _parse_instructions(  # noqa: PLR0911 - reject each malformed instruction boundary.
    value: object,
) -> tuple[PacketInstruction, ...] | None:
    if type(value) is not list:
        return None
    parsed: list[PacketInstruction] = []
    for item in value:
        fields = _exact_mapping(
            item,
            {
                "identity",
                "direction",
                "target_band_id",
                "current_weight",
                "target_weight",
                "authorized_weight",
                "lower_weight",
                "upper_weight",
                "reason",
            },
        )
        if fields is None:
            return None
        identity = parse_instrument_identity(fields["identity"])
        direction = fields["direction"]
        target_band_id = fields["target_band_id"]
        reason = fields["reason"]
        if type(direction) is not str:
            return None
        if type(target_band_id) is not str:
            return None
        if type(reason) is not str:
            return None
        current_weight = _plain_decimal(fields["current_weight"])
        target_weight = _plain_decimal(fields["target_weight"])
        authorized_weight = _plain_decimal(fields["authorized_weight"])
        lower_weight = _plain_decimal(fields["lower_weight"])
        upper_weight = _plain_decimal(fields["upper_weight"])
        match (
            identity,
            current_weight,
            target_weight,
            authorized_weight,
            lower_weight,
            upper_weight,
        ):
            case (
                EquityInstrumentIdentity() as parsed_identity,
                Decimal() as parsed_current_weight,
                Decimal() as parsed_target_weight,
                Decimal() as parsed_authorized_weight,
                Decimal() as parsed_lower_weight,
                Decimal() as parsed_upper_weight,
            ):
                pass
            case _:
                return None
        try:
            instruction = PacketInstruction(
                parsed_identity,
                PacketDirection(direction),
                target_band_id,
                parsed_current_weight,
                parsed_target_weight,
                parsed_authorized_weight,
                parsed_lower_weight,
                parsed_upper_weight,
                PortfolioTradeReason(reason),
            )
        except (TypeError, ValueError):
            return None
        parsed.append(instruction)
    return tuple(parsed)


def _parse_risk_limits(
    value: object,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal] | None:
    fields = _exact_mapping(
        value,
        {
            "maximum_gross_weight",
            "maximum_name_weight",
            "maximum_sector_weight",
            "maximum_common_cause_weight",
            "maximum_correlation_cluster_weight",
            "maximum_fraction_of_median_dollar_volume",
        },
    )
    if fields is None:
        return None
    parsed = tuple(
        item
        for item in (
            _plain_decimal(fields["maximum_gross_weight"]),
            _plain_decimal(fields["maximum_name_weight"]),
            _plain_decimal(fields["maximum_sector_weight"]),
            _plain_decimal(fields["maximum_common_cause_weight"]),
            _plain_decimal(fields["maximum_correlation_cluster_weight"]),
            _plain_decimal(fields["maximum_fraction_of_median_dollar_volume"]),
        )
        if item is not None
    )
    try:
        gross, name, sector, common_cause, correlation, liquidity = parsed
    except ValueError:
        return None
    return gross, name, sector, common_cause, correlation, liquidity


def _parse_hash_list(value: object) -> tuple[str, ...] | None:
    if type(value) is not list:
        return None
    if any(type(item) is not str for item in value):
        return None
    parsed = tuple(value)
    return parsed if _valid_hash_tuple(parsed) else None


def _parse_shadow_ids(value: object) -> tuple[tuple[PortfolioShadowKind, str], ...] | None:
    if type(value) is not list:
        return None
    parsed: list[tuple[PortfolioShadowKind, str]] = []
    for item in value:
        fields = _exact_mapping(item, {"account_kind", "account_id"})
        if fields is None:
            return None
        account_kind = fields["account_kind"]
        if type(account_kind) is not str:
            return None
        account_id = fields["account_id"]
        if type(account_id) is not str:
            return None
        try:
            parsed.append((PortfolioShadowKind(account_kind), account_id))
        except ValueError:
            return None
    return tuple(parsed)


def _parse_fingerprints(value: object) -> tuple[tuple[str, str], ...] | None:
    fields = _exact_mapping(value, set(_DECISION_MATERIAL_KEYS))
    if fields is None:
        return None
    parsed: list[tuple[str, str]] = []
    for key in _DECISION_MATERIAL_KEYS:
        fingerprint = fields[key]
        if type(fingerprint) is not str:
            return None
        parsed.append((key, fingerprint))
    return tuple(parsed)


def _string_tuple(
    fields: dict[str, object],
    keys: tuple[str, ...],
) -> tuple[str, ...] | None:
    values: list[str] = []
    for key in keys:
        value = fields[key]
        if type(value) is not str:
            return None
        values.append(value)
    return tuple(values)


def _valid_hash_tuple(value: tuple[str, ...]) -> bool:
    if not all(_is_hash(item) for item in value):
        return False
    return tuple(sorted(set(value))) == value


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
