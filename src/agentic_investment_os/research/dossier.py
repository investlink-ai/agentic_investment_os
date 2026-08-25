"""Validate bounded Evidence Collector output without granting production authority."""

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
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant

__all__ = (
    "ContradictingEvidence",
    "Dossier",
    "DossierRefusalReason",
    "EvidenceAssertion",
    "ResearchLens",
    "ResearchLensDisposition",
    "ResearchLensRecord",
    "StatementKind",
    "StatementUncertainty",
    "parse_dossier",
)

_AUTHORITY_SCOPE = "research_lab_non_production"
_DOSSIER_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "authority_scope",
        "non_production",
        "subject",
        "facts",
        "interpretations",
        "contradicting_evidence",
        "missing_evidence",
        "lenses",
    }
)
_ASSERTION_FIELDS = frozenset(
    {
        "assertion_id",
        "statement_kind",
        "statement",
        "citation_artifact_ids",
        "relevant_at",
        "uncertainty",
    }
)
_CONTRADICTION_FIELDS = frozenset({"artifact_id", "explanation"})
_LENS_FIELDS = frozenset({"lens", "disposition", "rationale"})
_PROHIBITED_FIELDS = frozenset(
    {
        "weight",
        "weights",
        "target_weight",
        "order",
        "orders",
        "tool_instruction",
        "tool_call",
        "lifecycle_control",
        "memory_write",
        "govern",
        "champion",
        "decision_packet",
        "broker",
        "credential",
    }
)
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAXIMUM_ASSERTIONS = 50
_MAXIMUM_CONTRADICTIONS = 50
_MAXIMUM_MISSING_EVIDENCE = 50
_MAXIMUM_STATEMENT_CHARACTERS = 4_000
_MAXIMUM_EXPLANATION_CHARACTERS = 2_000
_MAXIMUM_CITATIONS_PER_ASSERTION = 10
_INVALID_DOSSIER = "invalid non-production Dossier"


class DossierRefusalReason(StrEnum):
    """Bound one class of hostile or unreconstructable model output."""

    INVALID_SCHEMA = "invalid_schema"
    IDENTITY_MISMATCH = "identity_mismatch"
    UNSUPPORTED_CITATION = "unsupported_citation"
    FUTURE_EVIDENCE = "future_evidence"
    PROHIBITED_AUTHORITY = "prohibited_authority"
    BOUNDS_EXCEEDED = "bounds_exceeded"
    MISSING_LENS = "missing_lens"


class StatementKind(StrEnum):
    """Separate source-supported facts from model interpretation."""

    FACT = "fact"
    INTERPRETATION = "interpretation"


class StatementUncertainty(StrEnum):
    """Describe whether an assertion is observed or inferred."""

    OBSERVED = "observed"
    INFERRED = "inferred"


class ResearchLens(StrEnum):
    """Name every required Evidence Collector research lens."""

    INFORMATION_AND_SENTIMENT = "information_and_sentiment"
    GROWTH_AND_EXPECTATIONS = "growth_and_expectations"
    QUALITY_AND_RESILIENCE = "quality_and_resilience"
    VALUATION_AND_EMBEDDED_EXPECTATIONS = "valuation_and_embedded_expectations"
    MARKET_BEHAVIOR_AND_LIQUIDITY = "market_behavior_and_liquidity"
    CATALYST_TIMING_AND_DOWNSIDE = "catalyst_timing_and_downside"


class ResearchLensDisposition(StrEnum):
    """Require each lens to be addressed or explicitly irrelevant."""

    ADDRESSED = "addressed"
    IRRELEVANT = "irrelevant"


@dataclass(frozen=True, slots=True)
class EvidenceAssertion:
    """Bind one bounded statement to evidence available at the replay cutoff."""

    assertion_id: str
    statement_kind: StatementKind
    statement: str
    citation_artifact_ids: tuple[str, ...]
    relevant_at: UtcInstant
    uncertainty: StatementUncertainty

    def to_payload(self) -> dict[str, object]:
        return {
            "assertion_id": self.assertion_id,
            "statement_kind": self.statement_kind.value,
            "statement": self.statement,
            "citation_artifact_ids": list(self.citation_artifact_ids),
            "relevant_at": self.relevant_at.isoformat(),
            "uncertainty": self.uncertainty.value,
        }


@dataclass(frozen=True, slots=True)
class ContradictingEvidence:
    """Name a captured artifact that contradicts the collected account."""

    artifact_id: str
    explanation: str

    def to_payload(self) -> dict[str, object]:
        return {"artifact_id": self.artifact_id, "explanation": self.explanation}


@dataclass(frozen=True, slots=True)
class ResearchLensRecord:
    """Record how one required research lens was handled."""

    lens: ResearchLens
    disposition: ResearchLensDisposition
    rationale: str

    def to_payload(self) -> dict[str, object]:
        return {
            "lens": self.lens.value,
            "disposition": self.disposition.value,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class Dossier:
    """Expose one validated, content-addressed, non-production evidence bundle."""

    subject: EquityInstrumentIdentity
    facts: tuple[EvidenceAssertion, ...]
    interpretations: tuple[EvidenceAssertion, ...]
    contradicting_evidence: tuple[ContradictingEvidence, ...]
    missing_evidence: tuple[str, ...]
    lenses: tuple[ResearchLensRecord, ...]
    content_hash: str
    authority_scope: str = _AUTHORITY_SCOPE
    non_production: bool = True

    def __post_init__(self) -> None:
        if (
            type(self.subject) is not EquityInstrumentIdentity
            or type(self.facts) is not tuple
            or any(
                type(item) is not EvidenceAssertion or item.statement_kind is not StatementKind.FACT
                for item in self.facts
            )
            or type(self.interpretations) is not tuple
            or any(
                type(item) is not EvidenceAssertion
                or item.statement_kind is not StatementKind.INTERPRETATION
                for item in self.interpretations
            )
            or type(self.contradicting_evidence) is not tuple
            or any(type(item) is not ContradictingEvidence for item in self.contradicting_evidence)
            or type(self.missing_evidence) is not tuple
            or any(type(item) is not str for item in self.missing_evidence)
            or type(self.lenses) is not tuple
            or any(type(item) is not ResearchLensRecord for item in self.lenses)
            or {item.lens for item in self.lenses} != set(ResearchLens)
            or self.authority_scope != _AUTHORITY_SCOPE
            or self.non_production is not True
            or self.content_hash != _content_hash(self.material_payload())
        ):
            raise ValueError(_INVALID_DOSSIER)

    @classmethod
    def create(  # noqa: PLR0913 - Dossier identity binds every validated output section.
        cls,
        *,
        subject: EquityInstrumentIdentity,
        facts: tuple[EvidenceAssertion, ...],
        interpretations: tuple[EvidenceAssertion, ...],
        contradicting_evidence: tuple[ContradictingEvidence, ...],
        missing_evidence: tuple[str, ...],
        lenses: tuple[ResearchLensRecord, ...],
    ) -> Dossier:
        material = _dossier_material_payload(
            subject=subject,
            facts=facts,
            interpretations=interpretations,
            contradicting_evidence=contradicting_evidence,
            missing_evidence=missing_evidence,
            lenses=lenses,
        )
        return cls(
            subject,
            facts,
            interpretations,
            contradicting_evidence,
            missing_evidence,
            lenses,
            _content_hash(material),
        )

    def material_payload(self) -> dict[str, object]:
        return _dossier_material_payload(
            subject=self.subject,
            facts=self.facts,
            interpretations=self.interpretations,
            contradicting_evidence=self.contradicting_evidence,
            missing_evidence=self.missing_evidence,
            lenses=self.lenses,
        )

    def to_payload(self) -> dict[str, object]:
        return {**self.material_payload(), "content_hash": self.content_hash}


def parse_dossier(  # noqa: PLR0911 - retain distinct hostile-output refusal reasons.
    value: object,
    *,
    expected_subject: EquityInstrumentIdentity,
    available_artifact_ids: tuple[str, ...],
    cutoff: UtcInstant,
) -> Dossier | DossierRefusalReason:
    """Validate hostile Evidence Collector output against pinned replay inputs."""
    if _contains_prohibited_field(value):
        return DossierRefusalReason.PROHIBITED_AUTHORITY
    root = _exact_mapping(value, _DOSSIER_FIELDS)
    if root is None:
        return DossierRefusalReason.INVALID_SCHEMA
    if (
        root["schema_version"] != 1
        or root["record_kind"] != "evidence_collector_dossier"
        or root["authority_scope"] != _AUTHORITY_SCOPE
        or root["non_production"] is not True
        or type(expected_subject) is not EquityInstrumentIdentity
        or type(cutoff) is not UtcInstant
        or type(available_artifact_ids) is not tuple
        or tuple(sorted(set(available_artifact_ids))) != available_artifact_ids
        or any(_SHA256.fullmatch(item) is None for item in available_artifact_ids)
    ):
        return DossierRefusalReason.INVALID_SCHEMA
    subject = parse_instrument_identity(root["subject"])
    if type(subject) is not EquityInstrumentIdentity or subject != expected_subject:
        return DossierRefusalReason.IDENTITY_MISMATCH
    facts = _parse_assertions(
        root["facts"],
        expected_kind=StatementKind.FACT,
        available_artifact_ids=available_artifact_ids,
        cutoff=cutoff,
    )
    interpretations = _parse_assertions(
        root["interpretations"],
        expected_kind=StatementKind.INTERPRETATION,
        available_artifact_ids=available_artifact_ids,
        cutoff=cutoff,
    )
    for result in (facts, interpretations):
        if isinstance(result, DossierRefusalReason):
            return result
    if not isinstance(facts, tuple) or not isinstance(interpretations, tuple):
        return DossierRefusalReason.INVALID_SCHEMA
    contradictions = _parse_contradictions(root["contradicting_evidence"], available_artifact_ids)
    if isinstance(contradictions, DossierRefusalReason):
        return contradictions
    missing = _parse_bounded_text_list(
        root["missing_evidence"],
        maximum_items=_MAXIMUM_MISSING_EVIDENCE,
        maximum_characters=_MAXIMUM_EXPLANATION_CHARACTERS,
    )
    if missing is None:
        return DossierRefusalReason.BOUNDS_EXCEEDED
    lenses = _parse_lenses(root["lenses"])
    if isinstance(lenses, DossierRefusalReason):
        return lenses
    assertion_ids = tuple(item.assertion_id for item in (*facts, *interpretations))
    if len(set(assertion_ids)) != len(assertion_ids):
        return DossierRefusalReason.INVALID_SCHEMA
    return Dossier.create(
        subject=subject,
        facts=facts,
        interpretations=interpretations,
        contradicting_evidence=contradictions,
        missing_evidence=missing,
        lenses=lenses,
    )


def _parse_assertions(  # noqa: PLR0911 - preserve exact citation and time refusals.
    value: object,
    *,
    expected_kind: StatementKind,
    available_artifact_ids: tuple[str, ...],
    cutoff: UtcInstant,
) -> tuple[EvidenceAssertion, ...] | DossierRefusalReason:
    if type(value) is not list or not value or len(value) > _MAXIMUM_ASSERTIONS:
        return DossierRefusalReason.BOUNDS_EXCEEDED
    assertions: list[EvidenceAssertion] = []
    for item in value:
        fields = _exact_mapping(item, _ASSERTION_FIELDS)
        if fields is None:
            return DossierRefusalReason.INVALID_SCHEMA
        assertion_id = fields["assertion_id"]
        statement = fields["statement"]
        citations = fields["citation_artifact_ids"]
        if (
            type(assertion_id) is not str
            or _IDENTIFIER.fullmatch(assertion_id) is None
            or type(statement) is not str
            or not statement.strip()
            or len(statement) > _MAXIMUM_STATEMENT_CHARACTERS
            or fields["statement_kind"] != expected_kind.value
            or type(citations) is not list
            or not 1 <= len(citations) <= _MAXIMUM_CITATIONS_PER_ASSERTION
            or any(type(citation) is not str for citation in citations)
            or citations != sorted(set(citations))
        ):
            return DossierRefusalReason.INVALID_SCHEMA
        if not set(citations) <= set(available_artifact_ids):
            return DossierRefusalReason.UNSUPPORTED_CITATION
        uncertainty_value = fields["uncertainty"]
        if type(uncertainty_value) is not str:
            return DossierRefusalReason.INVALID_SCHEMA
        try:
            relevant_at = UtcInstant.parse(fields["relevant_at"])
            uncertainty = StatementUncertainty(uncertainty_value)
        except (InvalidUtcInstantError, TypeError, ValueError):
            return DossierRefusalReason.INVALID_SCHEMA
        if relevant_at.value > cutoff.value:
            return DossierRefusalReason.FUTURE_EVIDENCE
        if expected_kind is StatementKind.FACT and uncertainty is not StatementUncertainty.OBSERVED:
            return DossierRefusalReason.INVALID_SCHEMA
        assertions.append(
            EvidenceAssertion(
                assertion_id,
                expected_kind,
                statement,
                tuple(citations),
                relevant_at,
                uncertainty,
            )
        )
    return tuple(assertions)


def _parse_contradictions(
    value: object,
    available_artifact_ids: tuple[str, ...],
) -> tuple[ContradictingEvidence, ...] | DossierRefusalReason:
    if type(value) is not list or len(value) > _MAXIMUM_CONTRADICTIONS:
        return DossierRefusalReason.BOUNDS_EXCEEDED
    parsed: list[ContradictingEvidence] = []
    for item in value:
        fields = _exact_mapping(item, _CONTRADICTION_FIELDS)
        if fields is None:
            return DossierRefusalReason.INVALID_SCHEMA
        artifact_id = fields["artifact_id"]
        explanation = fields["explanation"]
        if (
            type(artifact_id) is not str
            or artifact_id not in available_artifact_ids
            or type(explanation) is not str
            or not explanation.strip()
            or len(explanation) > _MAXIMUM_EXPLANATION_CHARACTERS
        ):
            return (
                DossierRefusalReason.UNSUPPORTED_CITATION
                if type(artifact_id) is str and artifact_id not in available_artifact_ids
                else DossierRefusalReason.INVALID_SCHEMA
            )
        parsed.append(ContradictingEvidence(artifact_id, explanation))
    if len({item.artifact_id for item in parsed}) != len(parsed):
        return DossierRefusalReason.INVALID_SCHEMA
    return tuple(parsed)


def _parse_lenses(  # noqa: PLR0911 - retain complete-lens and schema refusals.
    value: object,
) -> tuple[ResearchLensRecord, ...] | DossierRefusalReason:
    if type(value) is not list or len(value) != len(ResearchLens):
        return DossierRefusalReason.MISSING_LENS
    parsed: list[ResearchLensRecord] = []
    for item in value:
        fields = _exact_mapping(item, _LENS_FIELDS)
        if fields is None:
            return DossierRefusalReason.INVALID_SCHEMA
        rationale = fields["rationale"]
        lens_value = fields["lens"]
        disposition_value = fields["disposition"]
        if type(lens_value) is not str or type(disposition_value) is not str:
            return DossierRefusalReason.INVALID_SCHEMA
        try:
            lens = ResearchLens(lens_value)
            disposition = ResearchLensDisposition(disposition_value)
        except (TypeError, ValueError):
            return DossierRefusalReason.INVALID_SCHEMA
        if (
            type(rationale) is not str
            or not rationale.strip()
            or len(rationale) > _MAXIMUM_EXPLANATION_CHARACTERS
        ):
            return DossierRefusalReason.INVALID_SCHEMA
        parsed.append(ResearchLensRecord(lens, disposition, rationale))
    if {item.lens for item in parsed} != set(ResearchLens):
        return DossierRefusalReason.MISSING_LENS
    return tuple(sorted(parsed, key=lambda item: item.lens.value))


def _parse_bounded_text_list(
    value: object,
    *,
    maximum_items: int,
    maximum_characters: int,
) -> tuple[str, ...] | None:
    if (
        type(value) is not list
        or len(value) > maximum_items
        or any(
            type(item) is not str or not item.strip() or len(item) > maximum_characters
            for item in value
        )
        or value != sorted(set(value))
    ):
        return None
    return tuple(value)


def _contains_prohibited_field(value: object) -> bool:
    if type(value) is dict:
        return any(
            type(key) is str and (key in _PROHIBITED_FIELDS or _contains_prohibited_field(item))
            for key, item in value.items()
        )
    if type(value) is list:
        return any(_contains_prohibited_field(item) for item in value)
    return False


def _exact_mapping(value: object, fields: frozenset[str]) -> dict[str, object] | None:
    if (
        type(value) is not dict
        or set(value) != fields
        or any(type(key) is not str for key in value)
    ):
        return None
    return value


def _content_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _dossier_material_payload(  # noqa: PLR0913 - canonical identity binds every section.
    *,
    subject: EquityInstrumentIdentity,
    facts: tuple[EvidenceAssertion, ...],
    interpretations: tuple[EvidenceAssertion, ...],
    contradicting_evidence: tuple[ContradictingEvidence, ...],
    missing_evidence: tuple[str, ...],
    lenses: tuple[ResearchLensRecord, ...],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_kind": "evidence_collector_dossier",
        "authority_scope": _AUTHORITY_SCOPE,
        "non_production": True,
        "subject": subject.to_payload(),
        "facts": [item.to_payload() for item in facts],
        "interpretations": [item.to_payload() for item in interpretations],
        "contradicting_evidence": [item.to_payload() for item in contradicting_evidence],
        "missing_evidence": list(missing_evidence),
        "lenses": [item.to_payload() for item in lenses],
    }
