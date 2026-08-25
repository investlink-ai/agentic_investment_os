"""Validate non-production Thesis, Skeptic, forecast, and CIO artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum

from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    parse_instrument_identity,
)
from agentic_investment_os.research.dossier import Dossier, contains_prohibited_research_directive

__all__ = (
    "CioRationale",
    "CioRationaleBasis",
    "CioRefusalReason",
    "CioResolution",
    "CioStance",
    "EvidenceBoundClaim",
    "ForecastRefusalReason",
    "ObservationWindow",
    "ResearchArtifact",
    "ResearchArtifactRefusal",
    "ScenarioCase",
    "ScenarioEvidenceSource",
    "ScenarioForecast",
    "ScenarioKind",
    "ScenarioMetric",
    "ScenarioResolutionRule",
    "SkepticDecision",
    "SkepticFinding",
    "SkepticRefusalReason",
    "SkepticResult",
    "Thesis",
    "ThesisRefusalReason",
    "UninvestableCondition",
    "parse_cio_resolution",
    "parse_scenario_forecast",
    "parse_skeptic_result",
    "parse_thesis",
)

_AUTHORITY_SCOPE = "research_lab_non_production"
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAXIMUM_TEXT = 4_000
_MAXIMUM_ITEMS = 20
_MAXIMUM_HORIZON_TRADING_DAYS = 20
_SCENARIO_COUNT = 3
_SCENARIO_SUMMARY_PAIR_SIZE = 2
_TOTAL_PROBABILITY_BPS = 10_000
_INVALID_CLAIM = "invalid evidence-bound research claim"
_INVALID_CONDITION = "invalid uninvestable condition"
_INVALID_THESIS = "invalid non-production Thesis"
_INVALID_FINDING = "invalid Skeptic finding"
_INVALID_SKEPTIC = "invalid non-production Skeptic result"
_INVALID_RULE = "invalid scenario resolution rule"
_INVALID_SCENARIO = "invalid scenario case"
_INVALID_FORECAST = "invalid non-production scenario forecast"
_INVALID_RATIONALE = "invalid CIO rationale"
_INVALID_CIO = "invalid non-production CIO resolution"
_THESIS_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "authority_scope",
        "non_production",
        "subject",
        "dossier_id",
        "apparent_expectation",
        "variant_view",
        "causal_path",
        "catalyst",
        "horizon_trading_days",
        "scenario_summaries",
        "invalidators",
        "supporting_assertion_ids",
        "contradicting_artifact_ids",
        "uninvestable_conditions",
    }
)
_CLAIM_FIELDS = frozenset({"text", "supporting_assertion_ids", "contradicting_artifact_ids"})
_UNINVESTABLE_FIELDS = frozenset({"condition", "active"})
_SKEPTIC_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "authority_scope",
        "non_production",
        "subject",
        "dossier_id",
        "thesis_id",
        "decision",
        "strongest_countercase",
        "contradictions",
        "base_rates",
        "requested_evidence",
    }
)
_FINDING_FIELDS = frozenset({"claim", "citation_artifact_ids"})
_BASE_RATE_FIELDS = frozenset({"description", "citation_artifact_ids"})
_FORECAST_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "authority_scope",
        "non_production",
        "subject",
        "dossier_id",
        "thesis_id",
        "skeptic_id",
        "horizon_trading_days",
        "scenarios",
    }
)
_SCENARIO_FIELDS = frozenset(
    {"kind", "outcome", "resolution_rule", "downside_path", "probability_bps"}
)
_RESOLUTION_RULE_FIELDS = frozenset(
    {
        "metric",
        "source",
        "observation_window",
        "lower_bound_bps",
        "lower_bound_inclusive",
        "upper_bound_bps",
        "upper_bound_inclusive",
    }
)
_CIO_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "authority_scope",
        "non_production",
        "subject",
        "dossier_id",
        "thesis_id",
        "skeptic_id",
        "forecast_id",
        "stance",
        "uncertainty",
        "rationale",
    }
)
_RATIONALE_FIELDS = frozenset({"basis", "assertion_ids", "resolution_artifact_ids"})
_SCENARIO_SUMMARIES = frozenset({"bull", "base", "bear"})
_PROHIBITED_FIELDS = frozenset(
    {
        "account",
        "allocation",
        "broker",
        "client_order_id",
        "credentials",
        "decision_packet",
        "execution",
        "governance",
        "lifecycle",
        "memory_write",
        "order",
        "packet",
        "position_size",
        "position_weight",
        "quantity",
        "risk_limit",
        "target",
        "target_band",
        "target_weight",
        "tool_instruction",
        "weight",
    }
)


class ThesisRefusalReason(StrEnum):
    INVALID_SCHEMA = "invalid_schema"
    IDENTITY_MISMATCH = "identity_mismatch"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    INCONSISTENT_HORIZON = "inconsistent_horizon"
    PROHIBITED_AUTHORITY = "prohibited_authority"
    BOUNDS_EXCEEDED = "bounds_exceeded"


class SkepticDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    REQUEST_EVIDENCE = "request_evidence"


class SkepticRefusalReason(StrEnum):
    INVALID_SCHEMA = "invalid_schema"
    IDENTITY_MISMATCH = "identity_mismatch"
    UNSUPPORTED_CITATION = "unsupported_citation"
    PROHIBITED_AUTHORITY = "prohibited_authority"
    BOUNDS_EXCEEDED = "bounds_exceeded"


class ScenarioKind(StrEnum):
    BULL = "bull"
    BASE = "base"
    BEAR = "bear"


class ObservationWindow(StrEnum):
    LATEST_ALLOWED_RELEASE_BY_THESIS_HORIZON = "latest_allowed_release_by_thesis_horizon"


class ScenarioEvidenceSource(StrEnum):
    """Restrict scenario resolution to an allowed captured source class."""

    ALLOWED_OFFICIAL_FILING = "allowed_official_filing"


class ScenarioMetric(StrEnum):
    """Name an observable metric with fixed basis-point units and source semantics."""

    OPERATING_MARGIN_CHANGE_BPS = "operating_margin_change_bps"


class ForecastRefusalReason(StrEnum):
    INVALID_SCHEMA = "invalid_schema"
    IDENTITY_MISMATCH = "identity_mismatch"
    INCONSISTENT_HORIZON = "inconsistent_horizon"
    INVALID_PROBABILITIES = "invalid_probabilities"
    UNOBSERVABLE_RESOLUTION = "unobservable_resolution"
    PROHIBITED_AUTHORITY = "prohibited_authority"
    BOUNDS_EXCEEDED = "bounds_exceeded"


class CioStance(StrEnum):
    LONG = "long"
    HOLD = "hold"
    REDUCE = "reduce"
    EXIT = "exit"
    ABSTAIN = "abstain"


class CioRationaleBasis(StrEnum):
    SUPPORTED_THESIS = "supported_thesis"
    CONTESTED_THESIS = "contested_thesis"
    MISSING_EVIDENCE = "missing_evidence"
    UNINVESTABLE = "uninvestable"
    INSUFFICIENT_CONFIDENCE = "insufficient_confidence"


class CioRefusalReason(StrEnum):
    INVALID_SCHEMA = "invalid_schema"
    IDENTITY_MISMATCH = "identity_mismatch"
    UNRESOLVED_SKEPTIC = "unresolved_skeptic"
    MISSING_EVIDENCE = "missing_evidence"
    ACTIVE_UNINVESTABLE_CONDITION = "active_uninvestable_condition"
    PROHIBITED_AUTHORITY = "prohibited_authority"
    BOUNDS_EXCEEDED = "bounds_exceeded"


@dataclass(frozen=True, slots=True)
class EvidenceBoundClaim:
    text: str
    supporting_assertion_ids: tuple[str, ...]
    contradicting_artifact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            _bounded_text(self.text) != self.text
            or not _valid_identifier_tuple(self.supporting_assertion_ids, require_nonempty=False)
            or not _valid_hash_tuple(self.contradicting_artifact_ids, require_nonempty=False)
            or not (self.supporting_assertion_ids or self.contradicting_artifact_ids)
        ):
            raise ValueError(_INVALID_CLAIM)

    def to_payload(self) -> dict[str, object]:
        return {
            "text": self.text,
            "supporting_assertion_ids": list(self.supporting_assertion_ids),
            "contradicting_artifact_ids": list(self.contradicting_artifact_ids),
        }


@dataclass(frozen=True, slots=True)
class UninvestableCondition:
    condition: EvidenceBoundClaim
    active: bool

    def __post_init__(self) -> None:
        try:
            self.condition.__post_init__()
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(_INVALID_CONDITION) from error
        if type(self.active) is not bool:
            raise ValueError(_INVALID_CONDITION)

    def to_payload(self) -> dict[str, object]:
        return {"condition": self.condition.to_payload(), "active": self.active}


@dataclass(frozen=True, slots=True)
class Thesis:
    subject: EquityInstrumentIdentity
    dossier_id: str
    apparent_expectation: EvidenceBoundClaim
    variant_view: EvidenceBoundClaim
    causal_path: tuple[EvidenceBoundClaim, ...]
    catalyst: EvidenceBoundClaim
    horizon_trading_days: int
    scenario_summaries: tuple[tuple[ScenarioKind, EvidenceBoundClaim], ...]
    invalidators: tuple[EvidenceBoundClaim, ...]
    supporting_assertion_ids: tuple[str, ...]
    contradicting_artifact_ids: tuple[str, ...]
    uninvestable_conditions: tuple[UninvestableCondition, ...]
    content_hash: str
    authority_scope: str = _AUTHORITY_SCOPE
    non_production: bool = True

    def __post_init__(self) -> None:
        claims = self.material_claims()
        expected_supporting = tuple(
            sorted({item for claim in claims for item in claim.supporting_assertion_ids})
        )
        expected_contradicting = tuple(
            sorted({item for claim in claims for item in claim.contradicting_artifact_ids})
        )
        try:
            for claim in claims:
                claim.__post_init__()
            for condition in self.uninvestable_conditions:
                condition.__post_init__()
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(_INVALID_THESIS) from error
        if (
            type(self.subject) is not EquityInstrumentIdentity
            or _SHA256.fullmatch(self.dossier_id) is None
            or type(self.causal_path) is not tuple
            or type(self.scenario_summaries) is not tuple
            or type(self.invalidators) is not tuple
            or type(self.supporting_assertion_ids) is not tuple
            or type(self.contradicting_artifact_ids) is not tuple
            or type(self.uninvestable_conditions) is not tuple
            or any(
                type(item) is not tuple or len(item) != _SCENARIO_SUMMARY_PAIR_SIZE
                for item in self.scenario_summaries
            )
            or self.apparent_expectation.text == self.variant_view.text
            or type(self.horizon_trading_days) is not int
            or not 1 <= self.horizon_trading_days <= _MAXIMUM_HORIZON_TRADING_DAYS
            or not self.causal_path
            or len(self.causal_path) > _MAXIMUM_ITEMS
            or tuple(item[0] for item in self.scenario_summaries)
            != (ScenarioKind.BULL, ScenarioKind.BASE, ScenarioKind.BEAR)
            or not self.invalidators
            or len(self.invalidators) > _MAXIMUM_ITEMS
            or not self.uninvestable_conditions
            or len(self.uninvestable_conditions) > _MAXIMUM_ITEMS
            or self.supporting_assertion_ids != expected_supporting
            or self.contradicting_artifact_ids != expected_contradicting
            or not self.supporting_assertion_ids
            or self.authority_scope != _AUTHORITY_SCOPE
            or self.non_production is not True
            or self.content_hash != _content_hash(self.material_payload())
        ):
            raise ValueError(_INVALID_THESIS)

    def material_claims(self) -> tuple[EvidenceBoundClaim, ...]:
        return (
            self.apparent_expectation,
            self.variant_view,
            *self.causal_path,
            self.catalyst,
            *(claim for _, claim in self.scenario_summaries),
            *self.invalidators,
            *(item.condition for item in self.uninvestable_conditions),
        )

    def material_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record_kind": "thesis",
            "authority_scope": self.authority_scope,
            "non_production": self.non_production,
            "subject": self.subject.to_payload(),
            "dossier_id": self.dossier_id,
            "apparent_expectation": self.apparent_expectation.to_payload(),
            "variant_view": self.variant_view.to_payload(),
            "causal_path": [item.to_payload() for item in self.causal_path],
            "catalyst": self.catalyst.to_payload(),
            "horizon_trading_days": self.horizon_trading_days,
            "scenario_summaries": {
                kind.value: claim.to_payload() for kind, claim in self.scenario_summaries
            },
            "invalidators": [item.to_payload() for item in self.invalidators],
            "supporting_assertion_ids": list(self.supporting_assertion_ids),
            "contradicting_artifact_ids": list(self.contradicting_artifact_ids),
            "uninvestable_conditions": [item.to_payload() for item in self.uninvestable_conditions],
        }

    def to_payload(self) -> dict[str, object]:
        return {**self.material_payload(), "content_hash": self.content_hash}


@dataclass(frozen=True, slots=True)
class SkepticFinding:
    claim: str
    citation_artifact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if _bounded_text(self.claim) != self.claim or not _valid_hash_tuple(
            self.citation_artifact_ids, require_nonempty=True
        ):
            raise ValueError(_INVALID_FINDING)

    def to_payload(self) -> dict[str, object]:
        return {"claim": self.claim, "citation_artifact_ids": list(self.citation_artifact_ids)}


@dataclass(frozen=True, slots=True)
class SkepticResult:
    subject: EquityInstrumentIdentity
    dossier_id: str
    thesis_id: str
    decision: SkepticDecision
    strongest_countercase: SkepticFinding
    contradictions: tuple[SkepticFinding, ...]
    base_rates: tuple[SkepticFinding, ...]
    requested_evidence: tuple[str, ...]
    content_hash: str
    authority_scope: str = _AUTHORITY_SCOPE
    non_production: bool = True

    def __post_init__(self) -> None:
        try:
            self.strongest_countercase.__post_init__()
            for finding in (*self.contradictions, *self.base_rates):
                finding.__post_init__()
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(_INVALID_SKEPTIC) from error
        if (
            type(self.subject) is not EquityInstrumentIdentity
            or _SHA256.fullmatch(self.dossier_id) is None
            or _SHA256.fullmatch(self.thesis_id) is None
            or type(self.decision) is not SkepticDecision
            or type(self.contradictions) is not tuple
            or type(self.base_rates) is not tuple
            or type(self.requested_evidence) is not tuple
            or type(self.strongest_countercase) is not SkepticFinding
            or (
                self.decision is not SkepticDecision.REQUEST_EVIDENCE
                and (not self.contradictions or not self.base_rates)
            )
            or len(self.contradictions) > _MAXIMUM_ITEMS
            or len(self.base_rates) > _MAXIMUM_ITEMS
            or not _valid_text_tuple(self.requested_evidence, require_nonempty=False)
            or (self.decision is SkepticDecision.REQUEST_EVIDENCE) != bool(self.requested_evidence)
            or self.authority_scope != _AUTHORITY_SCOPE
            or self.non_production is not True
            or self.content_hash != _content_hash(self.material_payload())
        ):
            raise ValueError(_INVALID_SKEPTIC)

    def material_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record_kind": "independent_skeptic_result",
            "authority_scope": self.authority_scope,
            "non_production": self.non_production,
            "subject": self.subject.to_payload(),
            "dossier_id": self.dossier_id,
            "thesis_id": self.thesis_id,
            "decision": self.decision.value,
            "strongest_countercase": self.strongest_countercase.to_payload(),
            "contradictions": [item.to_payload() for item in self.contradictions],
            "base_rates": [
                {
                    "description": item.claim,
                    "citation_artifact_ids": list(item.citation_artifact_ids),
                }
                for item in self.base_rates
            ],
            "requested_evidence": list(self.requested_evidence),
        }

    def to_payload(self) -> dict[str, object]:
        return {**self.material_payload(), "content_hash": self.content_hash}


@dataclass(frozen=True, slots=True)
class ScenarioResolutionRule:
    metric: ScenarioMetric
    source: ScenarioEvidenceSource
    observation_window: ObservationWindow
    lower_bound_bps: int | None
    lower_bound_inclusive: bool | None
    upper_bound_bps: int | None
    upper_bound_inclusive: bool | None

    def __post_init__(self) -> None:
        if (
            type(self.metric) is not ScenarioMetric
            or type(self.source) is not ScenarioEvidenceSource
            or type(self.observation_window) is not ObservationWindow
            or (self.lower_bound_bps is not None and type(self.lower_bound_bps) is not int)
            or (
                self.lower_bound_inclusive is not None
                and type(self.lower_bound_inclusive) is not bool
            )
            or (self.upper_bound_bps is not None and type(self.upper_bound_bps) is not int)
            or (
                self.upper_bound_inclusive is not None
                and type(self.upper_bound_inclusive) is not bool
            )
            or (self.lower_bound_bps is None) != (self.lower_bound_inclusive is None)
            or (self.upper_bound_bps is None) != (self.upper_bound_inclusive is None)
            or (
                self.lower_bound_bps is not None
                and self.upper_bound_bps is not None
                and self.lower_bound_bps >= self.upper_bound_bps
            )
        ):
            raise ValueError(_INVALID_RULE)

    def to_payload(self) -> dict[str, object]:
        return {
            "metric": self.metric.value,
            "source": self.source.value,
            "observation_window": self.observation_window.value,
            "lower_bound_bps": self.lower_bound_bps,
            "lower_bound_inclusive": self.lower_bound_inclusive,
            "upper_bound_bps": self.upper_bound_bps,
            "upper_bound_inclusive": self.upper_bound_inclusive,
        }


@dataclass(frozen=True, slots=True)
class ScenarioCase:
    kind: ScenarioKind
    outcome: EvidenceBoundClaim
    resolution_rule: ScenarioResolutionRule
    downside_path: EvidenceBoundClaim
    probability_bps: int | None

    def __post_init__(self) -> None:
        try:
            self.outcome.__post_init__()
            self.resolution_rule.__post_init__()
            self.downside_path.__post_init__()
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(_INVALID_SCENARIO) from error
        if (
            type(self.kind) is not ScenarioKind
            or type(self.outcome) is not EvidenceBoundClaim
            or type(self.downside_path) is not EvidenceBoundClaim
            or (
                self.probability_bps is not None
                and (
                    type(self.probability_bps) is not int
                    or not 0 <= self.probability_bps <= _TOTAL_PROBABILITY_BPS
                )
            )
        ):
            raise ValueError(_INVALID_SCENARIO)

    def to_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "outcome": self.outcome.to_payload(),
            "resolution_rule": self.resolution_rule.to_payload(),
            "downside_path": self.downside_path.to_payload(),
            "probability_bps": self.probability_bps,
        }


@dataclass(frozen=True, slots=True)
class ScenarioForecast:
    subject: EquityInstrumentIdentity
    dossier_id: str
    thesis_id: str
    skeptic_id: str
    horizon_trading_days: int
    scenarios: tuple[ScenarioCase, ...]
    content_hash: str
    authority_scope: str = _AUTHORITY_SCOPE
    non_production: bool = True

    def __post_init__(self) -> None:
        try:
            for scenario in self.scenarios:
                scenario.__post_init__()
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(_INVALID_FORECAST) from error
        if (
            type(self.subject) is not EquityInstrumentIdentity
            or type(self.scenarios) is not tuple
            or any(
                _SHA256.fullmatch(value) is None
                for value in (self.dossier_id, self.thesis_id, self.skeptic_id)
            )
            or type(self.horizon_trading_days) is not int
            or not 1 <= self.horizon_trading_days <= _MAXIMUM_HORIZON_TRADING_DAYS
            or not _scenario_rules_form_partition(self.scenarios)
            or not _valid_scenario_probabilities(self.scenarios)
            or self.authority_scope != _AUTHORITY_SCOPE
            or self.non_production is not True
            or self.content_hash != _content_hash(self.material_payload())
        ):
            raise ValueError(_INVALID_FORECAST)

    def material_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record_kind": "scenario_forecast",
            "authority_scope": self.authority_scope,
            "non_production": self.non_production,
            "subject": self.subject.to_payload(),
            "dossier_id": self.dossier_id,
            "thesis_id": self.thesis_id,
            "skeptic_id": self.skeptic_id,
            "horizon_trading_days": self.horizon_trading_days,
            "scenarios": [item.to_payload() for item in self.scenarios],
        }

    def to_payload(self) -> dict[str, object]:
        return {**self.material_payload(), "content_hash": self.content_hash}


@dataclass(frozen=True, slots=True)
class CioRationale:
    basis: CioRationaleBasis
    assertion_ids: tuple[str, ...]
    resolution_artifact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.basis) is not CioRationaleBasis
            or not _valid_identifier_tuple(self.assertion_ids, require_nonempty=True)
            or not _valid_hash_tuple(self.resolution_artifact_ids, require_nonempty=True)
        ):
            raise ValueError(_INVALID_RATIONALE)

    def to_payload(self) -> dict[str, object]:
        return {
            "basis": self.basis.value,
            "assertion_ids": list(self.assertion_ids),
            "resolution_artifact_ids": list(self.resolution_artifact_ids),
        }


@dataclass(frozen=True, slots=True)
class CioResolution:
    subject: EquityInstrumentIdentity
    dossier_id: str
    thesis_id: str
    skeptic_id: str
    forecast_id: str
    stance: CioStance
    uncertainty: str
    rationale: CioRationale
    content_hash: str
    authority_scope: str = _AUTHORITY_SCOPE
    non_production: bool = True

    def __post_init__(self) -> None:
        try:
            self.rationale.__post_init__()
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(_INVALID_CIO) from error
        if (
            type(self.subject) is not EquityInstrumentIdentity
            or any(
                _SHA256.fullmatch(value) is None
                for value in (self.dossier_id, self.thesis_id, self.skeptic_id, self.forecast_id)
            )
            or type(self.stance) is not CioStance
            or self.uncertainty not in ("low", "medium", "high")
            or self.authority_scope != _AUTHORITY_SCOPE
            or self.non_production is not True
            or self.content_hash != _content_hash(self.material_payload())
        ):
            raise ValueError(_INVALID_CIO)

    def material_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record_kind": "cio_resolution",
            "authority_scope": self.authority_scope,
            "non_production": self.non_production,
            "subject": self.subject.to_payload(),
            "dossier_id": self.dossier_id,
            "thesis_id": self.thesis_id,
            "skeptic_id": self.skeptic_id,
            "forecast_id": self.forecast_id,
            "stance": self.stance.value,
            "uncertainty": self.uncertainty,
            "rationale": self.rationale.to_payload(),
        }

    def to_payload(self) -> dict[str, object]:
        return {**self.material_payload(), "content_hash": self.content_hash}


ResearchArtifact = Dossier | Thesis | SkepticResult | ScenarioForecast | CioResolution
ResearchArtifactRefusal = (
    ThesisRefusalReason | SkepticRefusalReason | ForecastRefusalReason | CioRefusalReason
)


def parse_thesis(  # noqa: PLR0911
    value: object, *, dossier: Dossier
) -> Thesis | ThesisRefusalReason:
    if _contains_prohibited_authority(value):
        return ThesisRefusalReason.PROHIBITED_AUTHORITY
    fields = _exact_mapping(value, _THESIS_FIELDS)
    if fields is None or not _valid_envelope(fields, "thesis"):
        return ThesisRefusalReason.INVALID_SCHEMA
    subject = parse_instrument_identity(fields["subject"])
    if type(subject) is not EquityInstrumentIdentity or subject != dossier.subject:
        return ThesisRefusalReason.IDENTITY_MISMATCH
    if fields["dossier_id"] != dossier.content_hash:
        return ThesisRefusalReason.IDENTITY_MISMATCH
    expectation = _parse_claim(fields["apparent_expectation"], dossier)
    variant = _parse_claim(fields["variant_view"], dossier)
    catalyst = _parse_claim(fields["catalyst"], dossier)
    causal_path = _parse_claims(fields["causal_path"], dossier, require_nonempty=True, ordered=True)
    invalidators = _parse_claims(fields["invalidators"], dossier, require_nonempty=True)
    uninvestable = _parse_uninvestable_conditions(fields["uninvestable_conditions"], dossier)
    summaries = _parse_summaries(fields["scenario_summaries"], dossier)
    if (
        expectation is None
        or variant is None
        or catalyst is None
        or causal_path is None
        or invalidators is None
        or uninvestable is None
        or summaries is None
    ):
        return ThesisRefusalReason.UNSUPPORTED_CLAIM
    if expectation.text == variant.text:
        return ThesisRefusalReason.INVALID_SCHEMA
    horizon = fields["horizon_trading_days"]
    if type(horizon) is not int or not 1 <= horizon <= _MAXIMUM_HORIZON_TRADING_DAYS:
        return ThesisRefusalReason.INCONSISTENT_HORIZON
    claims = (
        expectation,
        variant,
        *causal_path,
        catalyst,
        *(claim for _, claim in summaries),
        *invalidators,
        *(item.condition for item in uninvestable),
    )
    supporting = _identifier_tuple(fields["supporting_assertion_ids"], require_nonempty=True)
    contradicting = _hash_tuple(fields["contradicting_artifact_ids"], require_nonempty=False)
    expected_supporting = tuple(
        sorted({item for claim in claims for item in claim.supporting_assertion_ids})
    )
    expected_contradicting = tuple(
        sorted({item for claim in claims for item in claim.contradicting_artifact_ids})
    )
    if supporting != expected_supporting or contradicting != expected_contradicting:
        return ThesisRefusalReason.UNSUPPORTED_CLAIM
    try:
        return Thesis(
            subject,
            dossier.content_hash,
            expectation,
            variant,
            causal_path,
            catalyst,
            horizon,
            summaries,
            invalidators,
            supporting,
            contradicting,
            uninvestable,
            _content_hash(dict(fields)),
        )
    except (TypeError, ValueError):
        return ThesisRefusalReason.INVALID_SCHEMA


def parse_skeptic_result(  # noqa: PLR0911
    value: object, *, dossier: Dossier, thesis: Thesis
) -> SkepticResult | SkepticRefusalReason:
    if _contains_prohibited_authority(value):
        return SkepticRefusalReason.PROHIBITED_AUTHORITY
    fields = _exact_mapping(value, _SKEPTIC_FIELDS)
    if fields is None or not _valid_envelope(fields, "independent_skeptic_result"):
        return SkepticRefusalReason.INVALID_SCHEMA
    subject = parse_instrument_identity(fields["subject"])
    if (
        type(subject) is not EquityInstrumentIdentity
        or subject != dossier.subject
        or thesis.subject != dossier.subject
        or fields["dossier_id"] != dossier.content_hash
        or fields["thesis_id"] != thesis.content_hash
    ):
        return SkepticRefusalReason.IDENTITY_MISMATCH
    decision_value = fields["decision"]
    if type(decision_value) is not str:
        return SkepticRefusalReason.INVALID_SCHEMA
    try:
        decision = SkepticDecision(decision_value)
    except ValueError:
        return SkepticRefusalReason.INVALID_SCHEMA
    countercases = _parse_findings(
        [fields["strongest_countercase"]], dossier, base_rate=False, require_nonempty=True
    )
    require_findings = decision is not SkepticDecision.REQUEST_EVIDENCE
    contradictions = _parse_findings(
        fields["contradictions"], dossier, base_rate=False, require_nonempty=require_findings
    )
    base_rates = _parse_findings(
        fields["base_rates"], dossier, base_rate=True, require_nonempty=require_findings
    )
    requested = _text_tuple(fields["requested_evidence"], require_nonempty=False)
    if isinstance(countercases, SkepticRefusalReason):
        return countercases
    if isinstance(contradictions, SkepticRefusalReason):
        return contradictions
    if isinstance(base_rates, SkepticRefusalReason):
        return base_rates
    if requested is None:
        return SkepticRefusalReason.BOUNDS_EXCEEDED
    if (decision is SkepticDecision.REQUEST_EVIDENCE) != bool(requested):
        return SkepticRefusalReason.INVALID_SCHEMA
    try:
        return SkepticResult(
            subject,
            dossier.content_hash,
            thesis.content_hash,
            decision,
            countercases[0],
            contradictions,
            base_rates,
            requested,
            _content_hash(dict(fields)),
        )
    except (TypeError, ValueError):
        return SkepticRefusalReason.INVALID_SCHEMA


def parse_scenario_forecast(  # noqa: PLR0911
    value: object, *, dossier: Dossier, thesis: Thesis, skeptic: SkepticResult
) -> ScenarioForecast | ForecastRefusalReason:
    if _contains_prohibited_authority(value):
        return ForecastRefusalReason.PROHIBITED_AUTHORITY
    fields = _exact_mapping(value, _FORECAST_FIELDS)
    if fields is None or not _valid_envelope(fields, "scenario_forecast"):
        return ForecastRefusalReason.INVALID_SCHEMA
    subject = parse_instrument_identity(fields["subject"])
    if (
        type(subject) is not EquityInstrumentIdentity
        or subject != dossier.subject
        or fields["dossier_id"] != dossier.content_hash
        or fields["thesis_id"] != thesis.content_hash
        or fields["skeptic_id"] != skeptic.content_hash
    ):
        return ForecastRefusalReason.IDENTITY_MISMATCH
    horizon = fields["horizon_trading_days"]
    if type(horizon) is not int or horizon != thesis.horizon_trading_days:
        return ForecastRefusalReason.INCONSISTENT_HORIZON
    scenarios = _parse_scenarios(fields["scenarios"], dossier)
    if isinstance(scenarios, ForecastRefusalReason):
        return scenarios
    try:
        return ScenarioForecast(
            subject,
            dossier.content_hash,
            thesis.content_hash,
            skeptic.content_hash,
            horizon,
            scenarios,
            _content_hash(dict(fields)),
        )
    except (TypeError, ValueError):
        return ForecastRefusalReason.INVALID_SCHEMA


def parse_cio_resolution(  # noqa: PLR0911, PLR0912
    value: object,
    *,
    dossier: Dossier,
    thesis: Thesis,
    skeptic: SkepticResult,
    forecast: ScenarioForecast,
) -> CioResolution | CioRefusalReason:
    if _contains_prohibited_authority(value):
        return CioRefusalReason.PROHIBITED_AUTHORITY
    fields = _exact_mapping(value, _CIO_FIELDS)
    if fields is None or not _valid_envelope(fields, "cio_resolution"):
        return CioRefusalReason.INVALID_SCHEMA
    subject = parse_instrument_identity(fields["subject"])
    if (
        type(subject) is not EquityInstrumentIdentity
        or subject != dossier.subject
        or fields["dossier_id"] != dossier.content_hash
        or fields["thesis_id"] != thesis.content_hash
        or fields["skeptic_id"] != skeptic.content_hash
        or fields["forecast_id"] != forecast.content_hash
    ):
        return CioRefusalReason.IDENTITY_MISMATCH
    stance_value = fields["stance"]
    if type(stance_value) is not str:
        return CioRefusalReason.INVALID_SCHEMA
    try:
        stance = CioStance(stance_value)
    except ValueError:
        return CioRefusalReason.INVALID_SCHEMA
    uncertainty = fields["uncertainty"]
    if uncertainty not in ("low", "medium", "high"):
        return CioRefusalReason.BOUNDS_EXCEEDED
    rationale = _parse_cio_rationale(fields["rationale"], dossier, thesis, skeptic, forecast)
    if rationale is None:
        return CioRefusalReason.INVALID_SCHEMA
    active_uninvestable = any(item.active for item in thesis.uninvestable_conditions)
    blocker_bases: set[CioRationaleBasis] = set()
    if dossier.missing_evidence:
        blocker_bases.add(CioRationaleBasis.MISSING_EVIDENCE)
    if active_uninvestable:
        blocker_bases.add(CioRationaleBasis.UNINVESTABLE)
    if skeptic.decision is SkepticDecision.REQUEST_EVIDENCE:
        blocker_bases.update(
            (CioRationaleBasis.MISSING_EVIDENCE, CioRationaleBasis.INSUFFICIENT_CONFIDENCE)
        )
    if blocker_bases:
        if skeptic.decision is SkepticDecision.REJECT:
            blocker_bases.add(CioRationaleBasis.CONTESTED_THESIS)
        if stance is not CioStance.ABSTAIN:
            if dossier.missing_evidence:
                return CioRefusalReason.MISSING_EVIDENCE
            if active_uninvestable:
                return CioRefusalReason.ACTIVE_UNINVESTABLE_CONDITION
            return CioRefusalReason.UNRESOLVED_SKEPTIC
        if rationale.basis not in blocker_bases:
            return CioRefusalReason.INVALID_SCHEMA
    elif skeptic.decision is SkepticDecision.REJECT:
        if stance in (CioStance.LONG, CioStance.HOLD):
            return CioRefusalReason.UNRESOLVED_SKEPTIC
        if rationale.basis is not CioRationaleBasis.CONTESTED_THESIS:
            return CioRefusalReason.INVALID_SCHEMA
    elif rationale.basis is not CioRationaleBasis.SUPPORTED_THESIS and not (
        stance is CioStance.ABSTAIN and rationale.basis is CioRationaleBasis.INSUFFICIENT_CONFIDENCE
    ):
        return CioRefusalReason.INVALID_SCHEMA
    try:
        return CioResolution(
            subject,
            dossier.content_hash,
            thesis.content_hash,
            skeptic.content_hash,
            forecast.content_hash,
            stance,
            uncertainty,
            rationale,
            _content_hash(dict(fields)),
        )
    except (TypeError, ValueError):
        return CioRefusalReason.INVALID_SCHEMA


def _parse_claim(value: object, dossier: Dossier) -> EvidenceBoundClaim | None:
    fields = _exact_mapping(value, _CLAIM_FIELDS)
    if fields is None:
        return None
    text = _bounded_text(fields["text"])
    supporting = _identifier_tuple(fields["supporting_assertion_ids"], require_nonempty=False)
    contradicting = _hash_tuple(fields["contradicting_artifact_ids"], require_nonempty=False)
    dossier_assertions = {item.assertion_id for item in (*dossier.facts, *dossier.interpretations)}
    dossier_contradictions = {item.artifact_id for item in dossier.contradicting_evidence}
    if (
        text is None
        or supporting is None
        or contradicting is None
        or not (supporting or contradicting)
        or not set(supporting) <= dossier_assertions
        or not set(contradicting) <= dossier_contradictions
    ):
        return None
    try:
        return EvidenceBoundClaim(text, supporting, contradicting)
    except (TypeError, ValueError):
        return None


def _parse_claims(
    value: object,
    dossier: Dossier,
    *,
    require_nonempty: bool,
    ordered: bool = False,
) -> tuple[EvidenceBoundClaim, ...] | None:
    if type(value) is not list or (require_nonempty and not value) or len(value) > _MAXIMUM_ITEMS:
        return None
    parsed = tuple(_parse_claim(item, dossier) for item in value)
    if any(item is None for item in parsed):
        return None
    claims = tuple(item for item in parsed if item is not None)
    payloads = [_canonical_json(item.to_payload()) for item in claims]
    if len(payloads) != len(set(payloads)) or (not ordered and payloads != sorted(payloads)):
        return None
    return claims


def _parse_uninvestable_conditions(
    value: object, dossier: Dossier
) -> tuple[UninvestableCondition, ...] | None:
    if type(value) is not list or not value or len(value) > _MAXIMUM_ITEMS:
        return None
    parsed: list[UninvestableCondition] = []
    for item in value:
        fields = _exact_mapping(item, _UNINVESTABLE_FIELDS)
        if fields is None or type(fields["active"]) is not bool:
            return None
        claim = _parse_claim(fields["condition"], dossier)
        if claim is None:
            return None
        parsed.append(UninvestableCondition(claim, fields["active"]))
    payloads = [_canonical_json(item.to_payload()) for item in parsed]
    if payloads != sorted(set(payloads)):
        return None
    return tuple(parsed)


def _parse_findings(
    value: object,
    dossier: Dossier,
    *,
    base_rate: bool,
    require_nonempty: bool,
) -> tuple[SkepticFinding, ...] | SkepticRefusalReason:
    if type(value) is not list or (require_nonempty and not value) or len(value) > _MAXIMUM_ITEMS:
        return SkepticRefusalReason.BOUNDS_EXCEEDED
    available = {
        artifact_id
        for assertion in (*dossier.facts, *dossier.interpretations)
        for artifact_id in assertion.citation_artifact_ids
    } | {item.artifact_id for item in dossier.contradicting_evidence}
    parsed: list[SkepticFinding] = []
    expected = _BASE_RATE_FIELDS if base_rate else _FINDING_FIELDS
    text_key = "description" if base_rate else "claim"
    for item in value:
        fields = _exact_mapping(item, expected)
        if fields is None:
            return SkepticRefusalReason.INVALID_SCHEMA
        text = _bounded_text(fields[text_key])
        citations = _hash_tuple(fields["citation_artifact_ids"], require_nonempty=True)
        if text is None or citations is None:
            return SkepticRefusalReason.BOUNDS_EXCEEDED
        if not set(citations) <= available:
            return SkepticRefusalReason.UNSUPPORTED_CITATION
        parsed.append(SkepticFinding(text, citations))
    return tuple(parsed)


def _parse_scenarios(  # noqa: PLR0911
    value: object, dossier: Dossier
) -> tuple[ScenarioCase, ...] | ForecastRefusalReason:
    if type(value) is not list or len(value) != _SCENARIO_COUNT:
        return ForecastRefusalReason.INVALID_SCHEMA
    parsed: list[ScenarioCase] = []
    for item in value:
        fields = _exact_mapping(item, _SCENARIO_FIELDS)
        if fields is None or type(fields["kind"]) is not str:
            return ForecastRefusalReason.INVALID_SCHEMA
        try:
            kind = ScenarioKind(fields["kind"])
        except ValueError:
            return ForecastRefusalReason.INVALID_SCHEMA
        outcome = _parse_claim(fields["outcome"], dossier)
        downside = _parse_claim(fields["downside_path"], dossier)
        rule = _parse_resolution_rule(fields["resolution_rule"])
        probability = fields["probability_bps"]
        if outcome is None or downside is None or rule is None:
            return ForecastRefusalReason.UNOBSERVABLE_RESOLUTION
        if probability is not None and (
            type(probability) is not int or not 0 <= probability <= _TOTAL_PROBABILITY_BPS
        ):
            return ForecastRefusalReason.INVALID_PROBABILITIES
        parsed.append(ScenarioCase(kind, outcome, rule, downside, probability))
    scenarios = tuple(parsed)
    if not _valid_scenario_probabilities(scenarios):
        return ForecastRefusalReason.INVALID_PROBABILITIES
    if not _scenario_rules_form_partition(scenarios):
        return ForecastRefusalReason.UNOBSERVABLE_RESOLUTION
    return scenarios


def _parse_resolution_rule(value: object) -> ScenarioResolutionRule | None:
    fields = _exact_mapping(value, _RESOLUTION_RULE_FIELDS)
    if (
        fields is None
        or type(fields["metric"]) is not str
        or type(fields["source"]) is not str
        or type(fields["observation_window"]) is not str
    ):
        return None
    try:
        return ScenarioResolutionRule(
            ScenarioMetric(fields["metric"]),
            ScenarioEvidenceSource(fields["source"]),
            ObservationWindow(fields["observation_window"]),
            _optional_int(fields["lower_bound_bps"]),
            _optional_bool(fields["lower_bound_inclusive"]),
            _optional_int(fields["upper_bound_bps"]),
            _optional_bool(fields["upper_bound_inclusive"]),
        )
    except (TypeError, ValueError):
        return None


def _scenario_rules_form_partition(scenarios: tuple[ScenarioCase, ...]) -> bool:
    if (
        tuple(item.kind for item in scenarios)
        != (
            ScenarioKind.BULL,
            ScenarioKind.BASE,
            ScenarioKind.BEAR,
        )
        or len({item.outcome.text for item in scenarios}) != _SCENARIO_COUNT
    ):
        return False
    bull, base, bear = scenarios
    rules = tuple(item.resolution_rule for item in scenarios)
    if len({(item.metric, item.source, item.observation_window) for item in rules}) != 1:
        return False
    return not (
        bull.resolution_rule.lower_bound_bps is None
        or bull.resolution_rule.upper_bound_bps is not None
        or base.resolution_rule.lower_bound_bps is None
        or base.resolution_rule.upper_bound_bps is None
        or bear.resolution_rule.lower_bound_bps is not None
        or bear.resolution_rule.upper_bound_bps is None
        or bull.resolution_rule.lower_bound_bps != base.resolution_rule.upper_bound_bps
        or bear.resolution_rule.upper_bound_bps != base.resolution_rule.lower_bound_bps
        or bull.resolution_rule.lower_bound_inclusive is not True
        or base.resolution_rule.lower_bound_inclusive is not True
        or base.resolution_rule.upper_bound_inclusive is not False
        or bear.resolution_rule.upper_bound_inclusive is not False
    )


def _valid_scenario_probabilities(scenarios: tuple[ScenarioCase, ...]) -> bool:
    probabilities = tuple(item.probability_bps for item in scenarios)
    return all(item is None for item in probabilities) or (
        all(type(item) is int for item in probabilities)
        and sum(item for item in probabilities if item is not None) == _TOTAL_PROBABILITY_BPS
    )


def _parse_summaries(
    value: object, dossier: Dossier
) -> tuple[tuple[ScenarioKind, EvidenceBoundClaim], ...] | None:
    fields = _exact_mapping(value, _SCENARIO_SUMMARIES)
    if fields is None:
        return None
    parsed: list[tuple[ScenarioKind, EvidenceBoundClaim]] = []
    for kind in (ScenarioKind.BULL, ScenarioKind.BASE, ScenarioKind.BEAR):
        claim = _parse_claim(fields[kind.value], dossier)
        if claim is None:
            return None
        parsed.append((kind, claim))
    return tuple(parsed)


def _parse_cio_rationale(
    value: object,
    dossier: Dossier,
    thesis: Thesis,
    skeptic: SkepticResult,
    forecast: ScenarioForecast,
) -> CioRationale | None:
    fields = _exact_mapping(value, _RATIONALE_FIELDS)
    if fields is None or type(fields["basis"]) is not str:
        return None
    try:
        basis = CioRationaleBasis(fields["basis"])
    except ValueError:
        return None
    assertion_ids = _identifier_tuple(fields["assertion_ids"], require_nonempty=True)
    artifact_ids = _hash_tuple(fields["resolution_artifact_ids"], require_nonempty=True)
    available_assertions = {
        item.assertion_id for item in (*dossier.facts, *dossier.interpretations)
    }
    expected_artifacts = tuple(
        sorted((thesis.content_hash, skeptic.content_hash, forecast.content_hash))
    )
    if (
        assertion_ids is None
        or artifact_ids is None
        or not set(assertion_ids) <= available_assertions
        or artifact_ids != expected_artifacts
    ):
        return None
    try:
        return CioRationale(basis, assertion_ids, artifact_ids)
    except (TypeError, ValueError):
        return None


def _valid_envelope(fields: dict[str, object], record_kind: str) -> bool:
    return (
        fields["schema_version"] == 1
        and fields["record_kind"] == record_kind
        and fields["authority_scope"] == _AUTHORITY_SCOPE
        and fields["non_production"] is True
    )


def _bounded_text(value: object) -> str | None:
    return (
        value
        if (
            type(value) is str
            and value.strip() == value
            and 1 <= len(value) <= _MAXIMUM_TEXT
            and not contains_prohibited_research_directive(value)
        )
        else None
    )


def _text_tuple(value: object, *, require_nonempty: bool) -> tuple[str, ...] | None:
    if type(value) is not list:
        return None
    result = tuple(value) if all(type(item) is str for item in value) else ()
    return result if _valid_text_tuple(result, require_nonempty=require_nonempty) else None


def _valid_text_tuple(value: object, *, require_nonempty: bool) -> bool:
    return (
        type(value) is tuple
        and (not require_nonempty or bool(value))
        and len(value) <= _MAXIMUM_ITEMS
        and all(_bounded_text(item) == item for item in value)
        and value == tuple(sorted(set(value)))
    )


def _identifier_tuple(value: object, *, require_nonempty: bool) -> tuple[str, ...] | None:
    if type(value) is not list:
        return None
    result = tuple(value) if all(type(item) is str for item in value) else ()
    return result if _valid_identifier_tuple(result, require_nonempty=require_nonempty) else None


def _valid_identifier_tuple(value: object, *, require_nonempty: bool) -> bool:
    return (
        type(value) is tuple
        and (not require_nonempty or bool(value))
        and len(value) <= _MAXIMUM_ITEMS
        and all(type(item) is str and _IDENTIFIER.fullmatch(item) is not None for item in value)
        and value == tuple(sorted(set(value)))
    )


def _hash_tuple(value: object, *, require_nonempty: bool) -> tuple[str, ...] | None:
    if type(value) is not list:
        return None
    result = tuple(value) if all(type(item) is str for item in value) else ()
    return result if _valid_hash_tuple(result, require_nonempty=require_nonempty) else None


def _valid_hash_tuple(value: object, *, require_nonempty: bool) -> bool:
    return (
        type(value) is tuple
        and (not require_nonempty or bool(value))
        and len(value) <= _MAXIMUM_ITEMS
        and all(type(item) is str and _SHA256.fullmatch(item) is not None for item in value)
        and value == tuple(sorted(set(value)))
    )


def _contains_prohibited_authority(value: object) -> bool:
    if type(value) is dict:
        return any(
            type(key) is str and (key in _PROHIBITED_FIELDS or _contains_prohibited_authority(item))
            for key, item in value.items()
        )
    if type(value) is list:
        return any(_contains_prohibited_authority(item) for item in value)
    if type(value) is str:
        return contains_prohibited_research_directive(value)
    return False


def _exact_mapping(value: object, fields: frozenset[str]) -> dict[str, object] | None:
    if (
        type(value) is not dict
        or set(value) != fields
        or any(type(key) is not str for key in value)
    ):
        return None
    return value


def _require_identifier(value: object) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(_INVALID_RULE)
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise TypeError(_INVALID_RULE)
    return value


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise TypeError(_INVALID_RULE)
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _content_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()
