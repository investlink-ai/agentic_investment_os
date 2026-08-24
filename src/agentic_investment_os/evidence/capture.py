"""Capture immutable evidence and admit observations by availability at a pinned cutoff."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol, Self, assert_never

from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    parse_instrument_identity,
)
from agentic_investment_os.domain.lifecycle import (
    EvidenceCaptureCheckpoint,
    EvidenceCaptureReference,
    is_sha256,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.domain.universe import is_data_regime

__all__ = (
    "CaptureEvidence",
    "CaptureIntent",
    "CaptureOutcome",
    "EvidenceArtifact",
    "EvidenceCandidate",
    "EvidenceCaptureCapability",
    "EvidenceCaptureStatus",
    "EvidenceCaptureSummary",
    "EvidenceEntityMapping",
    "EvidenceFeed",
    "EvidenceKind",
    "EvidenceLookup",
    "EvidenceMappingDisposition",
    "EvidencePersistenceError",
    "EvidencePolicy",
    "EvidenceQuery",
    "EvidenceReferenceValidator",
    "EvidenceRefusalReason",
    "EvidenceRetrieval",
    "EvidenceSource",
    "EvidenceSourceDisposition",
    "EvidenceSourceIdentityConflictError",
    "EvidenceSourceResult",
    "EvidenceStoredRecord",
    "EvidenceVault",
    "InvalidEvidenceError",
    "is_normalized_evidence_content",
    "parse_capture_intent",
    "parse_capture_outcome",
    "select_evidence_as_of",
    "source_identity_is_consistent",
    "validate_capture_outcome_association",
)

_RETRIEVAL_ID = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_NORMALIZED_INSTANT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})\Z"
)
_NORMALIZED_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_NORMALIZED_PRICE = re.compile(r"[0-9]+(?:\.[0-9]{1,9})?\Z")
_NORMALIZATION_VERSION = "canonical-json-v1"
_EVIDENCE_AUTHORITY_SCOPE = "research_evidence"
_EVIDENCE_ENVELOPE_SCHEMA_VERSION = 2
_EVIDENCE_PAYLOAD_SCHEMA_VERSION = 2
_EVIDENCE_POLICY_SCHEMA_VERSION = 2
_CAPTURE_INTENT_SCHEMA_VERSION = 2
_CAPTURE_OUTCOME_SCHEMA_VERSION = 2
_MAXIMUM_CONTENT_BYTES = 1_000_000
_MAXIMUM_QUERY_RESULTS = 100
_MAXIMUM_AGE_SECONDS = 86_399_999_999_999
_MAXIMUM_RETRIEVAL_COUNT = 100
_POLICY_FIELDS = frozenset({"schema_version", "policy_type", "data_regime", "requests"})
_REQUEST_FIELDS = frozenset(
    {"kind", "source", "retrieval_identity", "maximum_age_seconds", "required"}
)
_INTENT_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "run_id",
        "universe_snapshot_id",
        "cutoff",
        "data_regime",
        "request",
        "intent_id",
        "content_hash",
    }
)
_ARTIFACT_FIELDS = frozenset(
    {
        "envelope_schema_version",
        "record_kind",
        "payload_discriminator",
        "payload_schema_version",
        "subject",
        "relevant_at",
        "available_at",
        "data_regime",
        "authority_scope",
        "observation_id",
        "material_fingerprints",
        "payload",
        "content_hash",
    }
)
_ARTIFACT_SUBJECT_FIELDS = frozenset({"entity_mappings"})
_ARTIFACT_FINGERPRINT_FIELDS = frozenset(
    {
        "source_content",
        "retrieval_contract",
        "transformation_contract",
    }
)
_ARTIFACT_PAYLOAD_FIELDS = frozenset(
    {
        "retrieval_identity",
        "source_identity",
        "source_event_at",
        "published_at",
        "first_observed_at",
        "entitlement",
        "feed",
        "coverage",
        "parser_version",
        "normalization_version",
        "source_content_hash",
    }
)
_OUTCOME_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "intent_id",
        "status",
        "refusal_reason",
        "artifact",
        "mapping_disposition",
        "content_hash",
    }
)
_MAPPING_FIELDS = frozenset({"identity", "confidence", "mapping_version", "available_at"})
_MAPPING_DISPOSITION_FIELDS = frozenset({"mapping_version", "available_at"})
_MARKET_CONTENT_FIELDS = frozenset({"bars"})
_MARKET_BAR_FIELDS = frozenset({"asset_id", "close", "timestamp"})
_NEWS_CONTENT_FIELDS = frozenset({"headline", "id", "summary"})
_SEC_CONTENT_FIELDS = frozenset(
    {"accession_number", "amends_accession", "filing_period", "form", "restatement", "text"}
)
_ISSUER_CONTENT_FIELDS = frozenset({"release_id", "text", "title"})
_MACRO_CONTENT_FIELDS = frozenset({"artifact_type", "document_id", "text", "title"})
_INVALID_CANDIDATE = "invalid evidence candidate"
_INVALID_ARTIFACT = "invalid evidence artifact"
_INVALID_POLICY = "invalid evidence policy"
_INVALID_QUERY = "invalid evidence query"
_INVALID_CAPTURE = "invalid evidence capture state"
_MAXIMUM_HEADLINE_CHARACTERS = 10_000
_MAXIMUM_SUMMARY_CHARACTERS = 100_000
_MAXIMUM_SOURCE_TEXT_CHARACTERS = 900_000
_ACCESSION_NUMBER = re.compile(r"[0-9]{10}-[0-9]{2}-[0-9]{6}\Z")
_SOURCE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_MAPPING_VERSION = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_FILING_PERIOD = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_FORM = re.compile(r"[A-Z0-9-]+(?:/A)?\Z")


class InvalidEvidenceError(ValueError):
    """Report that typed evidence cannot preserve the required provenance contract."""


class EvidencePersistenceError(RuntimeError):
    """Report that the append-only Evidence Vault cannot be trusted or published."""


class EvidenceSourceIdentityConflictError(EvidencePersistenceError):
    """Report a conflicting immutable binding for one official source identity."""


class EvidenceKind(StrEnum):
    """Name the closed recorded evidence variants implemented in V0."""

    MARKET = "market"
    NEWS = "news"
    SEC_FILING = "sec_filing"
    ISSUER_RELEASE = "issuer_release"
    OFFICIAL_MACRO = "official_macro"


class EvidenceFeed(StrEnum):
    """Pin the authorized provider or official source carried by one observation."""

    IEX = "iex"
    ALPACA_NEWS = "alpaca_news"
    SEC_EDGAR = "sec_edgar"
    ISSUER_INVESTOR_RELATIONS = "issuer_investor_relations"
    FEDERAL_RESERVE = "federal_reserve"
    BLS = "bls"
    BEA = "bea"


class EvidenceCoverage(StrEnum):
    """Make source coverage and entitlement boundaries explicit."""

    IEX_BASIC_MARKET_ONLY = "iex_basic_market_only"
    ALPACA_NEWS_ENTITLEMENT = "alpaca_news_entitlement"
    SEC_EDGAR_PUBLIC = "sec_edgar_public"
    ISSUER_OFFICIAL_PUBLICATION = "issuer_official_publication"
    FEDERAL_RESERVE_PUBLIC = "federal_reserve_public"
    BLS_PUBLIC = "bls_public"
    BEA_PUBLIC = "bea_public"


class EvidenceCaptureStatus(StrEnum):
    """Classify the durable result of one configured retrieval."""

    CAPTURED = "captured"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"
    REFUSED = "refused"


class EvidenceRefusalReason(StrEnum):
    """Bound source and cutoff failures without retaining hostile input."""

    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_STALE = "source_stale"
    SOURCE_REFUSED = "source_refused"
    INVALID_RECORDED_INPUT = "invalid_recorded_input"
    AMBIGUOUS_ENTITY_MAPPING = "ambiguous_entity_mapping"
    UNSUPPORTED_CONTRACT = "unsupported_contract"
    AFTER_CUTOFF = "after_cutoff"
    STALE_AT_CUTOFF = "stale_at_cutoff"


@dataclass(frozen=True, slots=True)
class EvidenceEntityMapping:
    """Bind evidence to one unambiguous canonical equity identity."""

    identity: EquityInstrumentIdentity
    confidence: str
    mapping_version: str
    available_at: UtcInstant

    def __post_init__(self) -> None:
        if (
            type(self.identity) is not EquityInstrumentIdentity
            or self.confidence != "exact"
            or type(self.mapping_version) is not str
            or _MAPPING_VERSION.fullmatch(self.mapping_version) is None
            or type(self.available_at) is not UtcInstant
        ):
            raise InvalidEvidenceError(_INVALID_CANDIDATE)

    @classmethod
    def exact(
        cls,
        identity: EquityInstrumentIdentity,
        *,
        mapping_version: str,
        available_at: UtcInstant,
    ) -> Self:
        return cls(identity, "exact", mapping_version, available_at)

    def to_payload(self) -> dict[str, object]:
        return {
            "identity": self.identity.to_payload(),
            "confidence": self.confidence,
            "mapping_version": self.mapping_version,
            "available_at": self.available_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class EvidenceRetrieval:
    """Describe one required, effect-local recorded retrieval."""

    kind: EvidenceKind
    source: EvidenceFeed
    retrieval_identity: str
    maximum_age_seconds: int
    required: bool

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not EvidenceKind
            or type(self.source) is not EvidenceFeed
            or not _source_supports_kind(self.source, self.kind)
            or type(self.retrieval_identity) is not str
            or not self.is_valid_identity(self.retrieval_identity)
            or type(self.maximum_age_seconds) is not int
            or not 1 <= self.maximum_age_seconds <= _MAXIMUM_AGE_SECONDS
            or type(self.required) is not bool
        ):
            raise InvalidEvidenceError(_INVALID_POLICY)

    @staticmethod
    def is_valid_identity(value: str) -> bool:
        """Return whether text satisfies the bounded retrieval-identity grammar."""
        return _RETRIEVAL_ID.fullmatch(value) is not None

    def to_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "source": self.source.value,
            "retrieval_identity": self.retrieval_identity,
            "maximum_age_seconds": self.maximum_age_seconds,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    """Carry one canonical set of authorized recorded retrievals."""

    data_regime: str
    requests: tuple[EvidenceRetrieval, ...]

    def __post_init__(self) -> None:
        if (
            not is_data_regime(self.data_regime)
            or type(self.requests) is not tuple
            or not 1 <= len(self.requests) <= _MAXIMUM_RETRIEVAL_COUNT
            or any(type(request) is not EvidenceRetrieval for request in self.requests)
            or len({request.retrieval_identity for request in self.requests}) != len(self.requests)
            or tuple(sorted(self.requests, key=_request_sort_key)) != self.requests
        ):
            raise InvalidEvidenceError(_INVALID_POLICY)

    @classmethod
    def parse(cls, value: object) -> EvidencePolicy | None:
        root = _exact_mapping(value, _POLICY_FIELDS)
        if (
            root is None
            or root["schema_version"] != _EVIDENCE_POLICY_SCHEMA_VERSION
            or root["policy_type"] != "v0_evidence"
        ):
            return None
        data_regime = root["data_regime"]
        raw_requests = root["requests"]
        if not is_data_regime(data_regime) or type(raw_requests) is not list:
            return None
        requests: list[EvidenceRetrieval] = []
        try:
            for item in raw_requests:
                fields = _exact_mapping(item, _REQUEST_FIELDS)
                if fields is None:
                    return None
                kind = _text(fields["kind"])
                source = _text(fields["source"])
                retrieval_identity = _text(fields["retrieval_identity"])
                maximum_age_seconds = _integer(fields["maximum_age_seconds"])
                required = fields["required"]
                if (
                    kind is None
                    or source is None
                    or retrieval_identity is None
                    or maximum_age_seconds is None
                    or type(required) is not bool
                ):
                    return None
                requests.append(
                    EvidenceRetrieval(
                        EvidenceKind(kind),
                        EvidenceFeed(source),
                        retrieval_identity,
                        maximum_age_seconds,
                        required,
                    )
                )
            return cls(data_regime, tuple(sorted(requests, key=_request_sort_key)))
        except (InvalidEvidenceError, ValueError):
            return None

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": _EVIDENCE_POLICY_SCHEMA_VERSION,
            "policy_type": "v0_evidence",
            "data_regime": self.data_regime,
            "requests": [request.to_payload() for request in self.requests],
        }

    @property
    def policy_id(self) -> str:
        """Return the content address of the complete canonical retrieval policy."""
        return _content_hash(self.to_payload())

    @property
    def has_complete_v0_source_set(self) -> bool:
        """Return whether every V0 evidence authority has a scheduled retrieval."""
        return {request.source for request in self.requests} == set(EvidenceFeed)

    @property
    def has_required_retrieval(self) -> bool:
        """Return whether runtime capture has at least one fail-closed requirement."""
        return any(request.required for request in self.requests)


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    """Carry one validated recorded observation before durable publication."""

    retrieval_identity: str
    source_identity: str
    kind: EvidenceKind
    source_event_at: UtcInstant | None
    published_at: UtcInstant | None
    first_observed_at: UtcInstant
    available_at: UtcInstant
    data_regime: str
    feed: EvidenceFeed
    coverage: EvidenceCoverage
    entity_mappings: tuple[EvidenceEntityMapping, ...]
    content: bytes

    def __post_init__(self) -> None:
        relevant = tuple(
            value for value in (self.source_event_at, self.published_at) if value is not None
        )
        mapping_times = (
            tuple(
                mapping.available_at
                for mapping in self.entity_mappings
                if type(mapping) is EvidenceEntityMapping
            )
            if type(self.entity_mappings) is tuple
            else ()
        )
        if (
            _RETRIEVAL_ID.fullmatch(self.retrieval_identity) is None
            or type(self.source_identity) is not str
            or _SOURCE_IDENTITY.fullmatch(self.source_identity) is None
            or type(self.kind) is not EvidenceKind
            or type(self.first_observed_at) is not UtcInstant
            or type(self.available_at) is not UtcInstant
            or not is_data_regime(self.data_regime)
            or type(self.feed) is not EvidenceFeed
            or type(self.coverage) is not EvidenceCoverage
            or type(self.entity_mappings) is not tuple
            or not _entity_mapping_shape_is_valid(self.kind, self.entity_mappings)
            or len({mapping.identity for mapping in self.entity_mappings})
            != len(self.entity_mappings)
            or type(self.content) is not bytes
            or not self.content
            or len(self.content) > _MAXIMUM_CONTENT_BYTES
            or any(type(value) is not UtcInstant for value in relevant)
            or any(type(value) is not UtcInstant for value in mapping_times)
            or any(self.first_observed_at.value < value.value for value in relevant)
            or self.available_at.value
            != max(
                (self.first_observed_at, *relevant, *mapping_times),
                key=lambda item: item.value,
            ).value
            or not _kind_contract_is_valid(
                self.kind,
                self.feed,
                self.coverage,
                self.source_event_at,
                self.published_at,
            )
        ):
            raise InvalidEvidenceError(_INVALID_CANDIDATE)

    @classmethod
    def create(  # noqa: PLR0913 - artifact provenance names every material input.
        cls,
        *,
        retrieval_identity: str,
        source_identity: str,
        kind: EvidenceKind,
        source_event_at: UtcInstant | None,
        published_at: UtcInstant | None,
        first_observed_at: UtcInstant,
        data_regime: str,
        feed: EvidenceFeed,
        entity_mappings: tuple[EvidenceEntityMapping, ...],
        content: bytes,
    ) -> EvidenceCandidate:
        relevant = tuple(value for value in (source_event_at, published_at) if value is not None)
        mapping_times = tuple(mapping.available_at for mapping in entity_mappings)
        available_at = max(
            (first_observed_at, *relevant, *mapping_times), key=lambda item: item.value
        )
        return cls(
            retrieval_identity=retrieval_identity,
            source_identity=source_identity,
            kind=kind,
            source_event_at=source_event_at,
            published_at=published_at,
            first_observed_at=first_observed_at,
            available_at=available_at,
            data_regime=data_regime,
            feed=feed,
            coverage=_coverage_for(feed),
            entity_mappings=entity_mappings,
            content=content,
        )


@dataclass(frozen=True, slots=True)
class EvidenceArtifact:
    """Describe immutable content and one append-only observation of it."""

    artifact_id: str
    observation_id: str
    retrieval_identity: str
    source_identity: str
    kind: EvidenceKind
    source_event_at: UtcInstant | None
    published_at: UtcInstant | None
    first_observed_at: UtcInstant
    available_at: UtcInstant
    data_regime: str
    entitlement: str
    feed: EvidenceFeed
    coverage: EvidenceCoverage
    parser_version: str
    normalization_version: str
    entity_mappings: tuple[EvidenceEntityMapping, ...]
    content_hash: str

    @classmethod
    def from_candidate(
        cls,
        candidate: EvidenceCandidate,
        *,
        observation_id: str,
    ) -> EvidenceArtifact:
        candidate.__post_init__()
        if not is_sha256(observation_id):
            raise InvalidEvidenceError(_INVALID_ARTIFACT)
        content_hash = hashlib.sha256(candidate.content).hexdigest()
        material = _artifact_material(candidate, content_hash, observation_id)
        artifact_id = _content_hash(material)
        return cls(
            artifact_id=artifact_id,
            observation_id=observation_id,
            retrieval_identity=candidate.retrieval_identity,
            source_identity=candidate.source_identity,
            kind=candidate.kind,
            source_event_at=candidate.source_event_at,
            published_at=candidate.published_at,
            first_observed_at=candidate.first_observed_at,
            available_at=candidate.available_at,
            data_regime=candidate.data_regime,
            entitlement=_entitlement_for(candidate.feed),
            feed=candidate.feed,
            coverage=candidate.coverage,
            parser_version=_parser_version_for(candidate.feed),
            normalization_version=_NORMALIZATION_VERSION,
            entity_mappings=candidate.entity_mappings,
            content_hash=content_hash,
        )

    def to_payload(self) -> dict[str, object]:
        material = _artifact_envelope_material(self)
        return {**material, "content_hash": self.artifact_id}


@dataclass(frozen=True, slots=True)
class EvidenceMappingDisposition:
    """Preserve the version and availability of one explicit mapping ambiguity."""

    mapping_version: str
    available_at: UtcInstant

    def __post_init__(self) -> None:
        if (
            type(self.mapping_version) is not str
            or _MAPPING_VERSION.fullmatch(self.mapping_version) is None
            or type(self.available_at) is not UtcInstant
        ):
            raise InvalidEvidenceError(_INVALID_CAPTURE)

    def to_payload(self) -> dict[str, object]:
        return {
            "mapping_version": self.mapping_version,
            "available_at": self.available_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class EvidenceSourceDisposition:
    """Return one bounded recorded-source failure before publication."""

    status: EvidenceCaptureStatus
    reason: EvidenceRefusalReason
    data_regime: str | None
    mapping: EvidenceMappingDisposition | None = None

    def __post_init__(self) -> None:
        allowed = {
            EvidenceCaptureStatus.UNAVAILABLE: (EvidenceRefusalReason.SOURCE_UNAVAILABLE,),
            EvidenceCaptureStatus.STALE: (EvidenceRefusalReason.SOURCE_STALE,),
            EvidenceCaptureStatus.INVALID: (EvidenceRefusalReason.INVALID_RECORDED_INPUT,),
            EvidenceCaptureStatus.AMBIGUOUS: (EvidenceRefusalReason.AMBIGUOUS_ENTITY_MAPPING,),
            EvidenceCaptureStatus.REFUSED: (
                EvidenceRefusalReason.SOURCE_REFUSED,
                EvidenceRefusalReason.INVALID_RECORDED_INPUT,
            ),
        }
        if (
            type(self.status) is not EvidenceCaptureStatus
            or type(self.reason) is not EvidenceRefusalReason
            or self.reason not in allowed.get(self.status, ())
            or (
                self.data_regime is None
                and self.reason is not EvidenceRefusalReason.INVALID_RECORDED_INPUT
            )
            or (self.data_regime is not None and not is_data_regime(self.data_regime))
            or (
                (self.status is EvidenceCaptureStatus.AMBIGUOUS)
                != (type(self.mapping) is EvidenceMappingDisposition)
            )
        ):
            raise InvalidEvidenceError(_INVALID_CAPTURE)


EvidenceSourceResult = EvidenceCandidate | EvidenceSourceDisposition


@dataclass(frozen=True, slots=True)
class CaptureIntent:
    """Identify one retrieval effect durably before the source is consulted."""

    run_id: str
    universe_snapshot_id: str
    cutoff: UtcInstant
    data_regime: str
    request: EvidenceRetrieval
    intent_id: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        universe_snapshot_id: str,
        cutoff: UtcInstant,
        data_regime: str,
        request: EvidenceRetrieval,
    ) -> CaptureIntent:
        material = {
            "run_id": run_id,
            "universe_snapshot_id": universe_snapshot_id,
            "cutoff": cutoff.isoformat(),
            "data_regime": data_regime,
            "request": request.to_payload(),
        }
        return cls(
            run_id,
            universe_snapshot_id,
            cutoff,
            data_regime,
            request,
            _content_hash(material),
        )

    def to_payload(self) -> dict[str, object]:
        material = {
            "schema_version": _CAPTURE_INTENT_SCHEMA_VERSION,
            "record_kind": "evidence_capture_intent",
            "run_id": self.run_id,
            "universe_snapshot_id": self.universe_snapshot_id,
            "cutoff": self.cutoff.isoformat(),
            "data_regime": self.data_regime,
            "request": self.request.to_payload(),
            "intent_id": self.intent_id,
        }
        return {**material, "content_hash": _content_hash(material)}


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    """Preserve one idempotent capture disposition and optional artifact observation."""

    intent_id: str
    status: EvidenceCaptureStatus
    refusal_reason: EvidenceRefusalReason | None
    artifact: EvidenceArtifact | None
    mapping_disposition: EvidenceMappingDisposition | None = None

    def __post_init__(self) -> None:
        shape = (
            self.status,
            self.refusal_reason,
            self.artifact is not None,
            self.mapping_disposition is not None,
        )
        allowed_shapes = (
            (EvidenceCaptureStatus.CAPTURED, None, True, False),
            (
                EvidenceCaptureStatus.UNAVAILABLE,
                EvidenceRefusalReason.SOURCE_UNAVAILABLE,
                False,
                False,
            ),
            (
                EvidenceCaptureStatus.UNAVAILABLE,
                EvidenceRefusalReason.AFTER_CUTOFF,
                False,
                True,
            ),
            (EvidenceCaptureStatus.STALE, EvidenceRefusalReason.SOURCE_STALE, False, False),
            (EvidenceCaptureStatus.STALE, EvidenceRefusalReason.STALE_AT_CUTOFF, True, False),
            (
                EvidenceCaptureStatus.INVALID,
                EvidenceRefusalReason.INVALID_RECORDED_INPUT,
                False,
                False,
            ),
            (
                EvidenceCaptureStatus.AMBIGUOUS,
                EvidenceRefusalReason.AMBIGUOUS_ENTITY_MAPPING,
                False,
                True,
            ),
            (EvidenceCaptureStatus.REFUSED, EvidenceRefusalReason.SOURCE_REFUSED, False, False),
            (
                EvidenceCaptureStatus.REFUSED,
                EvidenceRefusalReason.INVALID_RECORDED_INPUT,
                False,
                False,
            ),
            (
                EvidenceCaptureStatus.REFUSED,
                EvidenceRefusalReason.UNSUPPORTED_CONTRACT,
                False,
                False,
            ),
            (EvidenceCaptureStatus.REFUSED, EvidenceRefusalReason.AFTER_CUTOFF, True, False),
        )
        if (
            not is_sha256(self.intent_id)
            or shape not in allowed_shapes
            or (
                self.mapping_disposition is not None
                and type(self.mapping_disposition) is not EvidenceMappingDisposition
            )
        ):
            raise InvalidEvidenceError(_INVALID_CAPTURE)

    def to_payload(self) -> dict[str, object]:
        material = {
            "schema_version": _CAPTURE_OUTCOME_SCHEMA_VERSION,
            "record_kind": "evidence_capture_outcome",
            "intent_id": self.intent_id,
            "status": self.status.value,
            "refusal_reason": (None if self.refusal_reason is None else self.refusal_reason.value),
            "artifact": None if self.artifact is None else self.artifact.to_payload(),
            "mapping_disposition": (
                None if self.mapping_disposition is None else self.mapping_disposition.to_payload()
            ),
        }
        return {**material, "content_hash": _content_hash(material)}


def validate_capture_outcome_association(
    intent: CaptureIntent,
    outcome: CaptureOutcome,
) -> None:
    """Reject an outcome whose provenance or cutoff disposition contradicts its intent."""
    outcome.__post_init__()
    if outcome.intent_id != intent.intent_id:
        raise InvalidEvidenceError(_INVALID_CAPTURE)
    artifact = outcome.artifact
    if artifact is None:
        mapping_disposition = outcome.mapping_disposition
        if mapping_disposition is None:
            return
        expected_mapping_outcome = (
            (
                EvidenceCaptureStatus.UNAVAILABLE,
                EvidenceRefusalReason.AFTER_CUTOFF,
            )
            if mapping_disposition.available_at.value > intent.cutoff.value
            else (
                EvidenceCaptureStatus.AMBIGUOUS,
                EvidenceRefusalReason.AMBIGUOUS_ENTITY_MAPPING,
            )
        )
        if (outcome.status, outcome.refusal_reason) != expected_mapping_outcome:
            raise InvalidEvidenceError(_INVALID_CAPTURE)
        return
    if (
        artifact.observation_id != intent.intent_id
        or artifact.retrieval_identity != intent.request.retrieval_identity
        or artifact.kind is not intent.request.kind
        or artifact.feed is not intent.request.source
        or artifact.data_regime != intent.data_regime
    ):
        raise InvalidEvidenceError(_INVALID_CAPTURE)
    expected: tuple[EvidenceCaptureStatus, EvidenceRefusalReason | None]
    if artifact.available_at.value > intent.cutoff.value:
        expected = (
            EvidenceCaptureStatus.REFUSED,
            EvidenceRefusalReason.AFTER_CUTOFF,
        )
    elif intent.cutoff.value - artifact.available_at.value > timedelta(
        seconds=intent.request.maximum_age_seconds
    ):
        expected = (
            EvidenceCaptureStatus.STALE,
            EvidenceRefusalReason.STALE_AT_CUTOFF,
        )
    else:
        expected = (EvidenceCaptureStatus.CAPTURED, None)
    if (outcome.status, outcome.refusal_reason) != expected:
        raise InvalidEvidenceError(_INVALID_CAPTURE)


@dataclass(frozen=True, slots=True)
class EvidenceCaptureSummary:
    """Return the complete ordered result of all configured retrievals."""

    policy_id: str
    outcomes: tuple[CaptureOutcome, ...]
    required_refusal_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not is_sha256(self.policy_id)
            or type(self.outcomes) is not tuple
            or type(self.required_refusal_ids) is not tuple
            or tuple(sorted(set(self.required_refusal_ids))) != self.required_refusal_ids
            or any(not is_sha256(value) for value in self.required_refusal_ids)
            or not set(self.required_refusal_ids).issubset(set(self.disposition_ids))
        ):
            raise InvalidEvidenceError(_INVALID_CAPTURE)

    @classmethod
    def from_policy(
        cls,
        policy: EvidencePolicy,
        outcomes: tuple[CaptureOutcome, ...],
    ) -> EvidenceCaptureSummary:
        """Classify required failures while retaining every durable optional disposition."""
        if len(outcomes) != len(policy.requests):
            raise InvalidEvidenceError(_INVALID_CAPTURE)
        required_refusals = tuple(
            sorted(
                outcome.intent_id
                for request, outcome in zip(policy.requests, outcomes, strict=True)
                if request.required and outcome.status is not EvidenceCaptureStatus.CAPTURED
            )
        )
        return cls(policy.policy_id, outcomes, required_refusals)

    @property
    def artifact_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    outcome.artifact.artifact_id
                    for outcome in self.outcomes
                    if outcome.status is EvidenceCaptureStatus.CAPTURED
                    and outcome.artifact is not None
                }
            )
        )

    @property
    def refusal_ids(self) -> tuple[str, ...]:
        """Return only dispositions that make the configured capture fail closed."""
        return self.required_refusal_ids

    @property
    def disposition_ids(self) -> tuple[str, ...]:
        """Return every non-captured outcome, including explicit optional absence."""
        return tuple(
            sorted(
                {
                    outcome.intent_id
                    for outcome in self.outcomes
                    if outcome.status is not EvidenceCaptureStatus.CAPTURED
                }
            )
        )


@dataclass(frozen=True, slots=True)
class EvidenceStoredRecord:
    """Join validated observation metadata to its immutable content bytes."""

    artifact: EvidenceArtifact
    content: bytes

    def __post_init__(self) -> None:
        if (
            type(self.content) is not bytes
            or hashlib.sha256(self.content).hexdigest() != self.artifact.content_hash
            or not is_normalized_evidence_content(
                self.artifact.kind,
                self.content,
                self.artifact.source_identity,
                self.artifact.source_event_at,
                self.artifact.entity_mappings,
            )
        ):
            raise InvalidEvidenceError(_INVALID_ARTIFACT)


@dataclass(frozen=True, slots=True)
class EvidenceQuery:
    """Bound one reconstructable as-of lookup to a regime and result count."""

    cutoff: UtcInstant
    data_regime: str
    limit: int

    def __post_init__(self) -> None:
        if (
            type(self.cutoff) is not UtcInstant
            or not is_data_regime(self.data_regime)
            or type(self.limit) is not int
            or not 1 <= self.limit <= _MAXIMUM_QUERY_RESULTS
        ):
            raise InvalidEvidenceError(_INVALID_QUERY)


class EvidenceSource(Protocol):
    """Retrieve one typed result from an external or recorded provider boundary."""

    def retrieve(self, request: EvidenceRetrieval) -> EvidenceSourceResult: ...


class EvidenceVault(Protocol):
    """Persist capture intent, outcomes, and immutable content under one private root."""

    def append_policy(
        self,
        policy: EvidencePolicy,
        capture_intents: tuple[CaptureIntent, ...],
    ) -> None: ...

    def load_policy(self, policy_id: str) -> EvidencePolicy: ...

    def append_intent(self, intent: CaptureIntent) -> None: ...

    def load_outcome(self, intent: CaptureIntent) -> CaptureOutcome | None: ...

    def append_outcome(
        self,
        intent: CaptureIntent,
        outcome: CaptureOutcome,
        content: bytes | None,
    ) -> None: ...

    def stored_records(self) -> tuple[EvidenceStoredRecord, ...]: ...


class EvidenceReferenceValidator(Protocol):
    """Validate lifecycle evidence references against authoritative Vault records."""

    def validate_references(
        self,
        checkpoints: tuple[EvidenceCaptureReference, ...],
    ) -> None: ...


class EvidenceCaptureCapability(Protocol):
    """Capture one run's configured evidence and return durable references."""

    @property
    def policy_id(self) -> str: ...

    def __call__(
        self,
        *,
        run_id: str,
        universe_snapshot_id: str,
        cutoff: UtcInstant,
        data_regime: str,
    ) -> EvidenceCaptureSummary: ...

    def validate_checkpoint(
        self,
        *,
        run_id: str,
        universe_snapshot_id: str,
        cutoff: UtcInstant,
        data_regime: str,
        checkpoint: EvidenceCaptureCheckpoint,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class CaptureEvidence:
    """Capture every configured retrieval behind intent-first idempotent checkpoints."""

    policy: EvidencePolicy
    source: EvidenceSource
    vault: EvidenceVault

    @property
    def policy_id(self) -> str:
        return self.policy.policy_id

    def __call__(
        self,
        *,
        run_id: str,
        universe_snapshot_id: str,
        cutoff: UtcInstant,
        data_regime: str,
    ) -> EvidenceCaptureSummary:
        if (
            not is_sha256(run_id)
            or not is_sha256(universe_snapshot_id)
            or type(cutoff) is not UtcInstant
            or data_regime != self.policy.data_regime
        ):
            raise InvalidEvidenceError(_INVALID_CAPTURE)
        intents = tuple(
            CaptureIntent.create(
                run_id=run_id,
                universe_snapshot_id=universe_snapshot_id,
                cutoff=cutoff,
                data_regime=data_regime,
                request=request,
            )
            for request in self.policy.requests
        )
        self.vault.append_policy(self.policy, intents)
        outcomes: list[CaptureOutcome] = []
        for request, intent in zip(self.policy.requests, intents, strict=True):
            existing = self.vault.load_outcome(intent)
            if existing is not None:
                outcomes.append(existing)
                continue
            self.vault.append_intent(intent)
            source_result = self.source.retrieve(request)
            outcome, content = _capture_outcome(intent, request, source_result, cutoff, data_regime)
            if outcome.artifact is not None and not source_identity_is_consistent(
                outcome.artifact,
                self.vault.stored_records(),
            ):
                outcome = CaptureOutcome(
                    intent.intent_id,
                    EvidenceCaptureStatus.INVALID,
                    EvidenceRefusalReason.INVALID_RECORDED_INPUT,
                    None,
                )
                content = None
            try:
                self.vault.append_outcome(intent, outcome, content)
            except EvidenceSourceIdentityConflictError:
                outcome = CaptureOutcome(
                    intent.intent_id,
                    EvidenceCaptureStatus.INVALID,
                    EvidenceRefusalReason.INVALID_RECORDED_INPUT,
                    None,
                )
                self.vault.append_outcome(intent, outcome, None)
            outcomes.append(outcome)
        return EvidenceCaptureSummary.from_policy(self.policy, tuple(outcomes))

    def validate_checkpoint(
        self,
        *,
        run_id: str,
        universe_snapshot_id: str,
        cutoff: UtcInstant,
        data_regime: str,
        checkpoint: EvidenceCaptureCheckpoint,
    ) -> None:
        """Reopen every expected outcome without repeating a recorded source effect."""
        if (
            not is_sha256(run_id)
            or not is_sha256(universe_snapshot_id)
            or type(cutoff) is not UtcInstant
            or data_regime != self.policy.data_regime
            or type(checkpoint) is not EvidenceCaptureCheckpoint
            or checkpoint.policy_id != self.policy.policy_id
        ):
            raise InvalidEvidenceError(_INVALID_CAPTURE)
        if self.vault.load_policy(checkpoint.policy_id) != self.policy:
            raise EvidencePersistenceError(_INVALID_CAPTURE)
        outcomes: list[CaptureOutcome] = []
        for request in self.policy.requests:
            intent = CaptureIntent.create(
                run_id=run_id,
                universe_snapshot_id=universe_snapshot_id,
                cutoff=cutoff,
                data_regime=data_regime,
                request=request,
            )
            outcome = self.vault.load_outcome(intent)
            if outcome is None:
                raise EvidencePersistenceError(_INVALID_CAPTURE)
            outcomes.append(outcome)
        summary = EvidenceCaptureSummary.from_policy(self.policy, tuple(outcomes))
        if (summary.artifact_ids, summary.refusal_ids) != (
            checkpoint.artifact_ids,
            checkpoint.refusal_ids,
        ):
            raise EvidencePersistenceError(_INVALID_CAPTURE)


@dataclass(frozen=True, slots=True)
class EvidenceLookup:
    """Return bounded typed artifacts admitted only by availability and Data Regime."""

    vault: EvidenceVault

    def __call__(self, query: EvidenceQuery) -> tuple[EvidenceStoredRecord, ...]:
        return select_evidence_as_of(self.vault.stored_records(), query)


def select_evidence_as_of(
    records: tuple[EvidenceStoredRecord, ...],
    query: EvidenceQuery,
) -> tuple[EvidenceStoredRecord, ...]:
    """Admit observations by availability, never by earlier source-event ordering."""
    query.__post_init__()
    admitted = tuple(
        record
        for record in records
        if record.artifact.data_regime == query.data_regime
        and record.artifact.available_at.value <= query.cutoff.value
    )
    ordered = sorted(
        admitted,
        key=lambda record: (
            record.artifact.available_at.value,
            record.artifact.observation_id,
        ),
    )
    return tuple(ordered[: query.limit])


def source_identity_is_consistent(
    artifact: EvidenceArtifact,
    records: tuple[EvidenceStoredRecord, ...],
) -> bool:
    """Return whether an official source identity retains its immutable source facts."""
    official_sources = (
        EvidenceFeed.SEC_EDGAR,
        EvidenceFeed.ISSUER_INVESTOR_RELATIONS,
        EvidenceFeed.FEDERAL_RESERVE,
        EvidenceFeed.BLS,
        EvidenceFeed.BEA,
    )
    if artifact.feed not in official_sources:
        return True
    return all(
        existing.artifact.feed is not artifact.feed
        or existing.artifact.source_identity != artifact.source_identity
        or (
            existing.artifact.content_hash == artifact.content_hash
            and existing.artifact.published_at == artifact.published_at
        )
        for existing in records
    )


def parse_capture_intent(  # noqa: PLR0911,PLR0912 - reject hostile fields independently.
    value: object,
) -> CaptureIntent | None:
    root = _exact_mapping(value, _INTENT_FIELDS)
    if root is None:
        return None
    if root["schema_version"] != _CAPTURE_INTENT_SCHEMA_VERSION:
        return None
    if root["record_kind"] != "evidence_capture_intent":
        return None
    request_fields = _exact_mapping(root["request"], _REQUEST_FIELDS)
    if request_fields is None:
        return None
    kind = _text(request_fields["kind"])
    source = _text(request_fields["source"])
    retrieval_identity = _text(request_fields["retrieval_identity"])
    maximum_age_seconds = _integer(request_fields["maximum_age_seconds"])
    required = request_fields["required"]
    if kind is None:
        return None
    if retrieval_identity is None:
        return None
    if source is None:
        return None
    if maximum_age_seconds is None:
        return None
    if type(required) is not bool:
        return None
    try:
        request = EvidenceRetrieval(
            EvidenceKind(kind),
            EvidenceFeed(source),
            retrieval_identity,
            maximum_age_seconds,
            required,
        )
        cutoff = UtcInstant.parse(root["cutoff"])
    except (InvalidEvidenceError, InvalidUtcInstantError, ValueError):
        return None
    run_id = root["run_id"]
    snapshot_id = root["universe_snapshot_id"]
    data_regime = root["data_regime"]
    if not is_sha256(run_id):
        return None
    if not is_sha256(snapshot_id):
        return None
    if not is_data_regime(data_regime):
        return None
    intent = CaptureIntent.create(
        run_id=run_id,
        universe_snapshot_id=snapshot_id,
        cutoff=cutoff,
        data_regime=data_regime,
        request=request,
    )
    return intent if intent.to_payload() == root else None


def parse_capture_outcome(  # noqa: PLR0911 - reject hostile fields independently.
    value: object,
) -> CaptureOutcome | None:
    root = _exact_mapping(value, _OUTCOME_FIELDS)
    if root is None:
        return None
    if root["schema_version"] != _CAPTURE_OUTCOME_SCHEMA_VERSION:
        return None
    if root["record_kind"] != "evidence_capture_outcome":
        return None
    status_value = _text(root["status"])
    intent_id = _text(root["intent_id"])
    if status_value is None:
        return None
    if intent_id is None:
        return None
    try:
        status = EvidenceCaptureStatus(status_value)
        reason_value = root["refusal_reason"]
        reason_text = None if reason_value is None else _text(reason_value)
        if reason_value is not None and reason_text is None:
            return None
        reason = None if reason_text is None else EvidenceRefusalReason(reason_text)
    except (TypeError, ValueError):
        return None
    artifact_value = root["artifact"]
    artifact = None if artifact_value is None else _parse_artifact(artifact_value)
    if artifact_value is not None and artifact is None:
        return None
    mapping_value = root["mapping_disposition"]
    mapping_disposition = _parse_mapping_disposition(mapping_value)
    if mapping_value is not None and mapping_disposition is None:
        return None
    try:
        outcome = CaptureOutcome(intent_id, status, reason, artifact, mapping_disposition)
    except InvalidEvidenceError:
        return None
    return outcome if outcome.to_payload() == root else None


def _parse_mapping_disposition(value: object) -> EvidenceMappingDisposition | None:
    if value is None:
        return None
    fields = _exact_mapping(value, _MAPPING_DISPOSITION_FIELDS)
    if fields is None:
        return None
    mapping_version = _text(fields["mapping_version"])
    if mapping_version is None:
        return None
    try:
        return EvidenceMappingDisposition(
            mapping_version,
            UtcInstant.parse(fields["available_at"]),
        )
    except (InvalidEvidenceError, InvalidUtcInstantError, TypeError):
        return None


def _parse_artifact(value: object) -> EvidenceArtifact | None:  # noqa: PLR0911, PLR0912
    root = _exact_mapping(value, _ARTIFACT_FIELDS)
    if root is None:
        return None
    subject = _exact_mapping(root["subject"], _ARTIFACT_SUBJECT_FIELDS)
    fingerprints = _exact_mapping(
        root["material_fingerprints"],
        _ARTIFACT_FINGERPRINT_FIELDS,
    )
    payload = _exact_mapping(root["payload"], _ARTIFACT_PAYLOAD_FIELDS)
    if subject is None or fingerprints is None or payload is None:
        return None
    required_text = (
        "payload_discriminator",
        "relevant_at",
        "available_at",
        "data_regime",
        "authority_scope",
        "observation_id",
        "content_hash",
    )
    text_values = _required_text_values(root, required_text)
    payload_text = _required_text_values(
        payload,
        (
            "retrieval_identity",
            "source_identity",
            "entitlement",
            "feed",
            "coverage",
            "parser_version",
            "normalization_version",
            "source_content_hash",
        ),
    )
    fingerprint_text = _required_text_values(
        fingerprints,
        tuple(sorted(_ARTIFACT_FINGERPRINT_FIELDS)),
    )
    if root["envelope_schema_version"] != _EVIDENCE_ENVELOPE_SCHEMA_VERSION:
        return None
    if root["payload_schema_version"] != _EVIDENCE_PAYLOAD_SCHEMA_VERSION:
        return None
    if root["record_kind"] != "evidence_snapshot":
        return None
    if text_values is None or payload_text is None or fingerprint_text is None:
        return None
    (
        discriminator,
        relevant_at_text,
        available_at_text,
        data_regime,
        authority_scope,
        observation_id,
        artifact_id,
    ) = text_values
    (
        retrieval_identity,
        source_identity,
        entitlement,
        feed_value,
        coverage_value,
        parser_version,
        normalization_version,
        source_content_hash,
    ) = payload_text
    if authority_scope != _EVIDENCE_AUTHORITY_SCOPE:
        return None
    kind = _kind_from_discriminator(discriminator)
    if kind is None:
        return None
    mappings_value = subject["entity_mappings"]
    if type(mappings_value) is not list:
        return None
    mappings: list[EvidenceEntityMapping] = []
    try:
        for mapping_value in mappings_value:
            fields = _exact_mapping(mapping_value, _MAPPING_FIELDS)
            if fields is None:
                return None
            if fields["confidence"] != "exact":
                return None
            identity = parse_instrument_identity(fields["identity"])
            if type(identity) is not EquityInstrumentIdentity:
                return None
            mapping_version = _text(fields["mapping_version"])
            if mapping_version is None:
                return None
            mappings.append(
                EvidenceEntityMapping.exact(
                    identity,
                    mapping_version=mapping_version,
                    available_at=UtcInstant.parse(fields["available_at"]),
                )
            )
        artifact = EvidenceArtifact(
            artifact_id=artifact_id,
            observation_id=observation_id,
            retrieval_identity=retrieval_identity,
            source_identity=source_identity,
            kind=kind,
            source_event_at=_parse_optional_instant(payload["source_event_at"]),
            published_at=_parse_optional_instant(payload["published_at"]),
            first_observed_at=UtcInstant.parse(payload["first_observed_at"]),
            available_at=UtcInstant.parse(available_at_text),
            data_regime=data_regime,
            entitlement=entitlement,
            feed=EvidenceFeed(feed_value),
            coverage=EvidenceCoverage(coverage_value),
            parser_version=parser_version,
            normalization_version=normalization_version,
            entity_mappings=tuple(mappings),
            content_hash=source_content_hash,
        )
    except (InvalidEvidenceError, InvalidUtcInstantError, TypeError, ValueError):
        return None
    relevant_at = _relevant_at(artifact.kind, artifact.source_event_at, artifact.published_at)
    if relevant_at is None or relevant_at.isoformat() != relevant_at_text:
        return None
    return artifact if _artifact_is_valid(artifact) and artifact.to_payload() == root else None


def _artifact_is_valid(  # noqa: PLR0911 - validate provenance contracts independently.
    artifact: EvidenceArtifact,
) -> bool:
    if not is_sha256(artifact.artifact_id):
        return False
    if not is_sha256(artifact.observation_id):
        return False
    if not is_sha256(artifact.content_hash):
        return False
    if artifact.entitlement != _entitlement_for(artifact.feed):
        return False
    if artifact.parser_version != _parser_version_for(artifact.feed):
        return False
    if artifact.normalization_version != _NORMALIZATION_VERSION:
        return False
    try:
        candidate = EvidenceCandidate(
            retrieval_identity=artifact.retrieval_identity,
            source_identity=artifact.source_identity,
            kind=artifact.kind,
            source_event_at=artifact.source_event_at,
            published_at=artifact.published_at,
            first_observed_at=artifact.first_observed_at,
            available_at=artifact.available_at,
            data_regime=artifact.data_regime,
            feed=artifact.feed,
            coverage=artifact.coverage,
            entity_mappings=artifact.entity_mappings,
            # Content bytes are unavailable while parsing metadata. Digest bytes exercise the
            # candidate's size invariants without pretending to reconstruct original content.
            content=bytes.fromhex(artifact.content_hash),
        )
    except InvalidEvidenceError:
        return False
    material = _artifact_material(candidate, artifact.content_hash, artifact.observation_id)
    return artifact.artifact_id == _content_hash(material)


def _capture_outcome(  # noqa: PLR0911 - preserve distinct capture dispositions.
    intent: CaptureIntent,
    request: EvidenceRetrieval,
    result: EvidenceSourceResult,
    cutoff: UtcInstant,
    data_regime: str,
) -> tuple[CaptureOutcome, bytes | None]:
    if isinstance(result, EvidenceSourceDisposition):
        if result.data_regime is not None and result.data_regime != data_regime:
            return (
                CaptureOutcome(
                    intent.intent_id,
                    EvidenceCaptureStatus.REFUSED,
                    EvidenceRefusalReason.UNSUPPORTED_CONTRACT,
                    None,
                ),
                None,
            )
        mapping = result.mapping
        if mapping is not None and mapping.available_at.value > cutoff.value:
            return (
                CaptureOutcome(
                    intent.intent_id,
                    EvidenceCaptureStatus.UNAVAILABLE,
                    EvidenceRefusalReason.AFTER_CUTOFF,
                    None,
                    mapping,
                ),
                None,
            )
        return (
            CaptureOutcome(
                intent.intent_id,
                result.status,
                result.reason,
                None,
                mapping,
            ),
            None,
        )
    if isinstance(result, EvidenceCandidate):
        result.__post_init__()
        if result.retrieval_identity != request.retrieval_identity:
            return (
                CaptureOutcome(
                    intent.intent_id,
                    EvidenceCaptureStatus.REFUSED,
                    EvidenceRefusalReason.UNSUPPORTED_CONTRACT,
                    None,
                ),
                None,
            )
        if result.kind is not request.kind or result.feed is not request.source:
            return (
                CaptureOutcome(
                    intent.intent_id,
                    EvidenceCaptureStatus.REFUSED,
                    EvidenceRefusalReason.UNSUPPORTED_CONTRACT,
                    None,
                ),
                None,
            )
        if result.data_regime != data_regime:
            return (
                CaptureOutcome(
                    intent.intent_id,
                    EvidenceCaptureStatus.REFUSED,
                    EvidenceRefusalReason.UNSUPPORTED_CONTRACT,
                    None,
                ),
                None,
            )
        artifact = EvidenceArtifact.from_candidate(
            result,
            observation_id=intent.intent_id,
        )
        if artifact.available_at.value > cutoff.value:
            return (
                CaptureOutcome(
                    intent.intent_id,
                    EvidenceCaptureStatus.REFUSED,
                    EvidenceRefusalReason.AFTER_CUTOFF,
                    artifact,
                ),
                result.content,
            )
        if cutoff.value - artifact.available_at.value > timedelta(
            seconds=request.maximum_age_seconds
        ):
            return (
                CaptureOutcome(
                    intent.intent_id,
                    EvidenceCaptureStatus.STALE,
                    EvidenceRefusalReason.STALE_AT_CUTOFF,
                    artifact,
                ),
                result.content,
            )
        return CaptureOutcome(intent.intent_id, EvidenceCaptureStatus.CAPTURED, None, artifact), (
            result.content
        )
    assert_never(result)  # pragma: no cover - union is closed.


def _kind_contract_is_valid(
    kind: EvidenceKind,
    feed: EvidenceFeed,
    coverage: EvidenceCoverage,
    source_event_at: UtcInstant | None,
    published_at: UtcInstant | None,
) -> bool:
    if not _source_supports_kind(feed, kind) or coverage is not _coverage_for(feed):
        return False
    if kind is EvidenceKind.MARKET:
        return source_event_at is not None and published_at is None
    if kind in (
        EvidenceKind.NEWS,
        EvidenceKind.SEC_FILING,
        EvidenceKind.ISSUER_RELEASE,
        EvidenceKind.OFFICIAL_MACRO,
    ):
        return source_event_at is None and published_at is not None
    assert_never(kind)  # pragma: no cover - enum is closed.


def _coverage_for(  # noqa: PLR0911 - keep the closed source contract explicit.
    source: EvidenceFeed,
) -> EvidenceCoverage:
    if source is EvidenceFeed.IEX:
        return EvidenceCoverage.IEX_BASIC_MARKET_ONLY
    if source is EvidenceFeed.ALPACA_NEWS:
        return EvidenceCoverage.ALPACA_NEWS_ENTITLEMENT
    if source is EvidenceFeed.SEC_EDGAR:
        return EvidenceCoverage.SEC_EDGAR_PUBLIC
    if source is EvidenceFeed.ISSUER_INVESTOR_RELATIONS:
        return EvidenceCoverage.ISSUER_OFFICIAL_PUBLICATION
    if source is EvidenceFeed.FEDERAL_RESERVE:
        return EvidenceCoverage.FEDERAL_RESERVE_PUBLIC
    if source is EvidenceFeed.BLS:
        return EvidenceCoverage.BLS_PUBLIC
    if source is EvidenceFeed.BEA:
        return EvidenceCoverage.BEA_PUBLIC
    assert_never(source)  # pragma: no cover - enum is closed.


def _artifact_material(
    candidate: EvidenceCandidate,
    content_hash: str,
    observation_id: str,
) -> dict[str, object]:
    relevant_at = _relevant_at(candidate.kind, candidate.source_event_at, candidate.published_at)
    if relevant_at is None:  # pragma: no cover - candidate validation requires the kind's time.
        raise InvalidEvidenceError(_INVALID_ARTIFACT)
    retrieval_contract = {
        "retrieval_identity": candidate.retrieval_identity,
        "source_identity": candidate.source_identity,
        "entitlement": _entitlement_for(candidate.feed),
        "feed": candidate.feed.value,
        "coverage": candidate.coverage.value,
    }
    transformation_contract = {
        "parser_version": _parser_version_for(candidate.feed),
        "normalization_version": _NORMALIZATION_VERSION,
    }
    return {
        "envelope_schema_version": _EVIDENCE_ENVELOPE_SCHEMA_VERSION,
        "record_kind": "evidence_snapshot",
        "payload_discriminator": _payload_discriminator(candidate.kind),
        "payload_schema_version": _EVIDENCE_PAYLOAD_SCHEMA_VERSION,
        "subject": {
            "entity_mappings": [mapping.to_payload() for mapping in candidate.entity_mappings]
        },
        "relevant_at": relevant_at.isoformat(),
        "available_at": candidate.available_at.isoformat(),
        "data_regime": candidate.data_regime,
        "authority_scope": _EVIDENCE_AUTHORITY_SCOPE,
        "observation_id": observation_id,
        "material_fingerprints": {
            "source_content": content_hash,
            "retrieval_contract": _content_hash(retrieval_contract),
            "transformation_contract": _content_hash(transformation_contract),
        },
        "payload": {
            **retrieval_contract,
            "source_event_at": _optional_instant_text(candidate.source_event_at),
            "published_at": _optional_instant_text(candidate.published_at),
            "first_observed_at": candidate.first_observed_at.isoformat(),
            **transformation_contract,
            "source_content_hash": content_hash,
        },
    }


def _artifact_envelope_material(artifact: EvidenceArtifact) -> dict[str, object]:
    candidate = EvidenceCandidate(
        retrieval_identity=artifact.retrieval_identity,
        source_identity=artifact.source_identity,
        kind=artifact.kind,
        source_event_at=artifact.source_event_at,
        published_at=artifact.published_at,
        first_observed_at=artifact.first_observed_at,
        available_at=artifact.available_at,
        data_regime=artifact.data_regime,
        feed=artifact.feed,
        coverage=artifact.coverage,
        entity_mappings=artifact.entity_mappings,
        content=bytes.fromhex(artifact.content_hash),
    )
    return _artifact_material(candidate, artifact.content_hash, artifact.observation_id)


def is_normalized_evidence_content(  # noqa: PLR0911 - validate closed variants locally.
    kind: EvidenceKind,
    content: bytes,
    source_identity: str,
    source_event_at: UtcInstant | None,
    entity_mappings: tuple[EvidenceEntityMapping, ...],
) -> bool:
    """Return whether canonical bytes satisfy the closed normalized evidence variant."""
    try:
        value: object = json.loads(content)
        if not _is_json_value(value) or _canonical_json(value) != content:
            return False
        if kind is EvidenceKind.MARKET:
            return _normalized_market_content_is_valid(
                value,
                source_event_at,
                entity_mappings,
            )
        if kind is EvidenceKind.NEWS:
            return _normalized_news_content_is_valid(value, source_identity)
        if kind is EvidenceKind.SEC_FILING:
            return _normalized_sec_content_is_valid(value, source_identity)
        if kind is EvidenceKind.ISSUER_RELEASE:
            return _normalized_issuer_content_is_valid(value, source_identity)
        if kind is EvidenceKind.OFFICIAL_MACRO:
            return _normalized_macro_content_is_valid(value, source_identity)
        assert_never(kind)  # pragma: no cover - enum is closed.
    except (UnicodeDecodeError, ValueError, RecursionError):
        return False


def _normalized_market_content_is_valid(  # noqa: PLR0911 - validate hostile fields locally.
    value: object,
    source_event_at: UtcInstant | None,
    mappings: tuple[EvidenceEntityMapping, ...],
) -> bool:
    root = _exact_mapping(value, _MARKET_CONTENT_FIELDS)
    if root is None or source_event_at is None or type(root["bars"]) is not list:
        return False
    raw_bars = root["bars"]
    if not raw_bars:
        return False
    expected_asset_ids = {mapping.identity.catalog_id for mapping in mappings}
    observed_asset_ids: set[str] = set()
    observations: set[tuple[str, str]] = set()
    timestamps: list[UtcInstant] = []
    for raw_bar in raw_bars:
        bar = _exact_mapping(raw_bar, _MARKET_BAR_FIELDS)
        if bar is None:
            return False
        asset_id = bar["asset_id"]
        close = bar["close"]
        timestamp = _normalized_content_instant(bar["timestamp"])
        if (
            type(asset_id) is not str
            or asset_id not in expected_asset_ids
            or type(close) is not str
            or _NORMALIZED_PRICE.fullmatch(close) is None
            or timestamp is None
        ):
            return False
        try:
            if Decimal(close) <= 0:
                return False
        except InvalidOperation:
            return False
        observation = (asset_id, timestamp.isoformat())
        if observation in observations:
            return False
        observations.add(observation)
        observed_asset_ids.add(asset_id)
        timestamps.append(timestamp)
    return (
        observed_asset_ids == expected_asset_ids
        and max(timestamps, key=lambda instant: instant.value) == source_event_at
    )


def _normalized_news_content_is_valid(value: object, source_identity: str) -> bool:
    root = _exact_mapping(value, _NEWS_CONTENT_FIELDS)
    if root is None:
        return False
    headline = root["headline"]
    identifier = root["id"]
    summary = root["summary"]
    return (
        type(headline) is str
        and 1 <= len(headline) <= _MAXIMUM_HEADLINE_CHARACTERS
        and type(identifier) is str
        and _NORMALIZED_IDENTIFIER.fullmatch(identifier) is not None
        and identifier == source_identity
        and type(summary) is str
        and 1 <= len(summary) <= _MAXIMUM_SUMMARY_CHARACTERS
    )


def _normalized_sec_content_is_valid(value: object, source_identity: str) -> bool:
    root = _exact_mapping(value, _SEC_CONTENT_FIELDS)
    if root is None:
        return False
    accession = root["accession_number"]
    amended = root["amends_accession"]
    filing_period = root["filing_period"]
    form = root["form"]
    restatement = root["restatement"]
    text = root["text"]
    if (
        type(accession) is not str
        or _ACCESSION_NUMBER.fullmatch(accession) is None
        or accession != source_identity
        or type(filing_period) is not str
        or _FILING_PERIOD.fullmatch(filing_period) is None
        or type(form) is not str
        or _FORM.fullmatch(form) is None
        or type(restatement) is not bool
        or type(text) is not str
        or not 1 <= len(text) <= _MAXIMUM_SOURCE_TEXT_CHARACTERS
    ):
        return False
    try:
        date.fromisoformat(filing_period)
    except ValueError:
        return False
    if form.endswith("/A"):
        return (
            type(amended) is str
            and _ACCESSION_NUMBER.fullmatch(amended) is not None
            and amended != accession
        )
    return amended is None and not restatement


def _normalized_issuer_content_is_valid(value: object, source_identity: str) -> bool:
    root = _exact_mapping(value, _ISSUER_CONTENT_FIELDS)
    return root is not None and _normalized_publication_text_is_valid(
        root,
        identity_field="release_id",
        source_identity=source_identity,
    )


def _normalized_macro_content_is_valid(value: object, source_identity: str) -> bool:
    root = _exact_mapping(value, _MACRO_CONTENT_FIELDS)
    return (
        root is not None
        and root["artifact_type"] in ("release", "schedule")
        and _normalized_publication_text_is_valid(
            root,
            identity_field="document_id",
            source_identity=source_identity,
        )
    )


def _normalized_publication_text_is_valid(
    root: dict[str, object],
    *,
    identity_field: str,
    source_identity: str,
) -> bool:
    identity = root[identity_field]
    title = root["title"]
    text = root["text"]
    return (
        type(identity) is str
        and identity == source_identity
        and _SOURCE_IDENTITY.fullmatch(identity) is not None
        and type(title) is str
        and 1 <= len(title) <= _MAXIMUM_HEADLINE_CHARACTERS
        and type(text) is str
        and 1 <= len(text) <= _MAXIMUM_SOURCE_TEXT_CHARACTERS
    )


def _normalized_content_instant(value: object) -> UtcInstant | None:
    if type(value) is not str or _NORMALIZED_INSTANT.fullmatch(value) is None:
        return None
    try:
        return UtcInstant.from_datetime(datetime.fromisoformat(value))
    except (InvalidUtcInstantError, ValueError):
        return None


def _is_json_value(value: object) -> bool:
    if value is None or type(value) in (bool, int, str):
        return True
    if type(value) is list:
        return all(_is_json_value(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _is_json_value(item) for key, item in value.items())
    return False


def _source_supports_kind(source: EvidenceFeed, kind: EvidenceKind) -> bool:
    allowed = {
        EvidenceKind.MARKET: (EvidenceFeed.IEX,),
        EvidenceKind.NEWS: (EvidenceFeed.ALPACA_NEWS,),
        EvidenceKind.SEC_FILING: (EvidenceFeed.SEC_EDGAR,),
        EvidenceKind.ISSUER_RELEASE: (EvidenceFeed.ISSUER_INVESTOR_RELATIONS,),
        EvidenceKind.OFFICIAL_MACRO: (
            EvidenceFeed.FEDERAL_RESERVE,
            EvidenceFeed.BLS,
            EvidenceFeed.BEA,
        ),
    }
    return source in allowed[kind]


def _entity_mapping_shape_is_valid(
    kind: EvidenceKind,
    mappings: object,
) -> bool:
    if type(mappings) is not tuple or any(
        type(mapping) is not EvidenceEntityMapping for mapping in mappings
    ):
        return False
    if kind is EvidenceKind.OFFICIAL_MACRO:
        return not mappings
    return bool(mappings)


def _entitlement_for(source: EvidenceFeed) -> str:
    if source in (EvidenceFeed.IEX, EvidenceFeed.ALPACA_NEWS):
        return "alpaca-basic"
    if source in (
        EvidenceFeed.SEC_EDGAR,
        EvidenceFeed.ISSUER_INVESTOR_RELATIONS,
        EvidenceFeed.FEDERAL_RESERVE,
        EvidenceFeed.BLS,
        EvidenceFeed.BEA,
    ):
        return "keyless-public"
    assert_never(source)  # pragma: no cover - enum is closed.


def _parser_version_for(source: EvidenceFeed) -> str:
    versions = {
        EvidenceFeed.IEX: "recorded-alpaca-evidence-v1",
        EvidenceFeed.ALPACA_NEWS: "recorded-alpaca-evidence-v1",
        EvidenceFeed.SEC_EDGAR: "recorded-sec-edgar-v1",
        EvidenceFeed.ISSUER_INVESTOR_RELATIONS: "recorded-issuer-ir-v1",
        EvidenceFeed.FEDERAL_RESERVE: "recorded-federal-reserve-v1",
        EvidenceFeed.BLS: "recorded-bls-v1",
        EvidenceFeed.BEA: "recorded-bea-v1",
    }
    return versions[source]


def _payload_discriminator(kind: EvidenceKind) -> str:
    discriminators = {
        EvidenceKind.MARKET: "alpaca_market_evidence",
        EvidenceKind.NEWS: "alpaca_news_evidence",
        EvidenceKind.SEC_FILING: "sec_filing_evidence",
        EvidenceKind.ISSUER_RELEASE: "issuer_release_evidence",
        EvidenceKind.OFFICIAL_MACRO: "official_macro_evidence",
    }
    return discriminators[kind]


def _kind_from_discriminator(value: str) -> EvidenceKind | None:
    for kind in EvidenceKind:
        if _payload_discriminator(kind) == value:
            return kind
    return None


def _relevant_at(
    kind: EvidenceKind,
    source_event_at: UtcInstant | None,
    published_at: UtcInstant | None,
) -> UtcInstant | None:
    return source_event_at if kind is EvidenceKind.MARKET else published_at


def _request_sort_key(request: EvidenceRetrieval) -> tuple[int, str, str]:
    order = {
        EvidenceKind.MARKET: 0,
        EvidenceKind.NEWS: 1,
        EvidenceKind.SEC_FILING: 2,
        EvidenceKind.ISSUER_RELEASE: 3,
        EvidenceKind.OFFICIAL_MACRO: 4,
    }
    return order[request.kind], request.source.value, request.retrieval_identity


def _parse_optional_instant(value: object) -> UtcInstant | None:
    return None if value is None else UtcInstant.parse(value)


def _optional_instant_text(value: UtcInstant | None) -> str | None:
    return None if value is None else value.isoformat()


def _exact_mapping(value: object, fields: frozenset[str]) -> dict[str, object] | None:
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != fields
    ):
        return None
    return value


def _required_text_values(
    values: dict[str, object],
    fields: tuple[str, ...],
) -> tuple[str, ...] | None:
    result: list[str] = []
    for field in fields:
        value = _text(values[field])
        if value is None:
            return None
        result.append(value)
    return tuple(result)


def _text(value: object) -> str | None:
    return value if type(value) is str else None


def _integer(value: object) -> int | None:
    return value if type(value) is int else None


def _content_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
