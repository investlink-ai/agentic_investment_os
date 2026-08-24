"""Own canonical belief-event contracts and authoritative admission rules."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TypeGuard

from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    parse_instrument_identity,
)
from agentic_investment_os.domain.lifecycle import is_sha256
from agentic_investment_os.domain.temporal import UtcInstant

__all__ = (
    "BeliefClaimKind",
    "BeliefEvent",
    "BeliefEvidenceArtifact",
    "BeliefEvidenceReference",
    "BeliefStatus",
    "RecordRefusalCode",
    "canonical_belief_event_payload",
    "parse_belief_event",
    "validate_belief_event",
    "validate_belief_evidence",
)

_BELIEF_SCHEMA_VERSION = 1
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_CONFIDENCE_INPUT = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_MAXIMUM_CLAIM_CHARACTERS = 4_000
_MAXIMUM_FALSIFIER_CHARACTERS = 1_000
_MAXIMUM_FALSIFIERS = 16
_MAXIMUM_EVIDENCE_REFERENCES = 32
_MAXIMUM_CONFIDENCE_CHARACTERS = 16
_INVALID_EVENT = "invalid belief event"
_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "event_id",
        "belief_id",
        "subject",
        "claim",
        "valid_at",
        "transaction_at",
        "evidence_cutoff",
        "confidence",
        "evidence",
        "falsifiers",
        "status",
        "transition_from_event_id",
        "supersedes_event_id",
        "content_hash",
    }
)
_CLAIM_FIELDS = frozenset({"kind", "statement"})
_EVIDENCE_REFERENCE_FIELDS = frozenset({"artifact_id", "content_hash"})


class BeliefClaimKind(StrEnum):
    """Classify the investment reasoning asserted by one belief."""

    EXPECTATION = "expectation"
    CAUSAL = "causal"
    CATALYST = "catalyst"
    RISK = "risk"
    VALUATION = "valuation"
    LIQUIDITY = "liquidity"


class BeliefStatus(StrEnum):
    """Name the preserved state of one belief transition."""

    ACTIVE = "active"
    WEAKENED = "weakened"
    CONTRADICTED = "contradicted"
    SUPERSEDED = "superseded"
    REFUTED = "refuted"
    EXPIRED = "expired"
    DORMANT = "dormant"
    ARCHIVED = "archived"


class RecordRefusalCode(StrEnum):
    """Bound invalid belief input without exposing its hostile content."""

    INVALID_EVENT = "invalid_event"
    EVENT_IDENTITY_CONFLICT = "event_identity_conflict"
    INVALID_TRANSITION = "invalid_transition"
    INVALID_AUTHORITATIVE_HISTORY = "invalid_authoritative_history"
    INVALID_EVIDENCE = "invalid_evidence"
    EVIDENCE_MISSING = "evidence_missing"
    EVIDENCE_HASH_MISMATCH = "evidence_hash_mismatch"
    EVIDENCE_AFTER_CUTOFF = "evidence_after_cutoff"


@dataclass(frozen=True, slots=True)
class BeliefEvidenceReference:
    """Pin one immutable Evidence Vault artifact and its source-content hash."""

    artifact_id: str
    content_hash: str

    def __post_init__(self) -> None:
        if not is_sha256(self.artifact_id) or not is_sha256(self.content_hash):
            raise ValueError(_INVALID_EVENT)

    def to_payload(self) -> dict[str, object]:
        return {"artifact_id": self.artifact_id, "content_hash": self.content_hash}


@dataclass(frozen=True, slots=True)
class BeliefEvidenceArtifact:
    """Expose only immutable Vault facts needed to validate a belief reference."""

    artifact_id: str
    content_hash: str
    available_at: UtcInstant

    def __post_init__(self) -> None:
        if not _valid_artifact(self):
            raise ValueError(_INVALID_EVENT)


@dataclass(frozen=True, slots=True)
class BeliefEvent:
    """Represent one immutable bitemporal transition to an evidence-bound claim."""

    event_id: str
    belief_id: str
    subject: EquityInstrumentIdentity
    claim_kind: BeliefClaimKind
    claim: str
    valid_at: UtcInstant
    transaction_at: UtcInstant
    evidence_cutoff: UtcInstant
    confidence: str
    evidence: tuple[BeliefEvidenceReference, ...]
    falsifiers: tuple[str, ...]
    status: BeliefStatus
    transition_from_event_id: str | None
    supersedes_event_id: str | None
    content_hash: str

    def __post_init__(self) -> None:
        if not _event_is_valid(self):
            raise ValueError(_INVALID_EVENT)

    @classmethod
    def create(  # noqa: PLR0913 - the event names every authoritative belief field.
        cls,
        *,
        event_id: str,
        belief_id: str,
        subject: EquityInstrumentIdentity,
        claim_kind: BeliefClaimKind,
        claim: str,
        valid_at: UtcInstant,
        transaction_at: UtcInstant,
        evidence_cutoff: UtcInstant,
        confidence: str,
        evidence: tuple[BeliefEvidenceReference, ...],
        falsifiers: tuple[str, ...],
        status: BeliefStatus,
        transition_from_event_id: str | None,
        supersedes_event_id: str | None,
    ) -> BeliefEvent:
        """Construct one canonical event and derive its material content hash."""
        normalized_confidence = _normalize_confidence(confidence)
        provisional = cls.__new__(cls)
        object.__setattr__(provisional, "event_id", event_id)
        object.__setattr__(provisional, "belief_id", belief_id)
        object.__setattr__(provisional, "subject", subject)
        object.__setattr__(provisional, "claim_kind", claim_kind)
        object.__setattr__(provisional, "claim", claim)
        object.__setattr__(provisional, "valid_at", valid_at)
        object.__setattr__(provisional, "transaction_at", transaction_at)
        object.__setattr__(provisional, "evidence_cutoff", evidence_cutoff)
        object.__setattr__(provisional, "confidence", normalized_confidence)
        object.__setattr__(provisional, "evidence", evidence)
        object.__setattr__(provisional, "falsifiers", falsifiers)
        object.__setattr__(provisional, "status", status)
        object.__setattr__(provisional, "transition_from_event_id", transition_from_event_id)
        object.__setattr__(provisional, "supersedes_event_id", supersedes_event_id)
        object.__setattr__(provisional, "content_hash", "0" * 64)
        return cls(
            event_id=event_id,
            belief_id=belief_id,
            subject=subject,
            claim_kind=claim_kind,
            claim=claim,
            valid_at=valid_at,
            transaction_at=transaction_at,
            evidence_cutoff=evidence_cutoff,
            confidence=normalized_confidence,
            evidence=evidence,
            falsifiers=falsifiers,
            status=status,
            transition_from_event_id=transition_from_event_id,
            supersedes_event_id=supersedes_event_id,
            content_hash=_event_content_hash(provisional),
        )

    def to_payload(self) -> dict[str, object]:
        return {**_event_material(self), "content_hash": self.content_hash}


def parse_belief_event(  # noqa: PLR0911, PLR0912 - narrow each hostile field before construction.
    value: object,
) -> BeliefEvent | None:
    """Validate one hostile serialized Belief Event without blessing parsed fields."""
    if type(value) is BeliefEvent:
        value = value.to_payload()
    fields = _exact_mapping(value, _EVENT_FIELDS)
    if (
        fields is None
        or not _schema_version(fields["schema_version"], _BELIEF_SCHEMA_VERSION)
        or fields["record_kind"] != "belief_event"
    ):
        return None
    claim_fields = _exact_mapping(fields["claim"], _CLAIM_FIELDS)
    subject = parse_instrument_identity(fields["subject"])
    claim_kind = _claim_kind(None if claim_fields is None else claim_fields["kind"])
    status = _belief_status(fields["status"])
    evidence = _evidence_references(fields["evidence"])
    falsifiers = _string_tuple(fields["falsifiers"])
    valid_at = _instant(fields["valid_at"])
    transaction_at = _instant(fields["transaction_at"])
    evidence_cutoff = _instant(fields["evidence_cutoff"])
    event_id = _text(fields["event_id"])
    belief_id = _text(fields["belief_id"])
    claim = _text(None if claim_fields is None else claim_fields["statement"])
    confidence = _text(fields["confidence"])
    content_hash = _text(fields["content_hash"])
    transition_valid, transition_from = _optional_text(fields["transition_from_event_id"])
    supersedes_valid, supersedes = _optional_text(fields["supersedes_event_id"])
    if type(subject) is not EquityInstrumentIdentity:
        return None
    if claim_kind is None:
        return None
    if status is None:
        return None
    if evidence is None:
        return None
    if falsifiers is None:
        return None
    if valid_at is None:
        return None
    if transaction_at is None:
        return None
    if evidence_cutoff is None:
        return None
    if event_id is None:
        return None
    if belief_id is None:
        return None
    if claim is None:
        return None
    if confidence is None:
        return None
    if content_hash is None:
        return None
    if not transition_valid:
        return None
    if not supersedes_valid:
        return None
    try:
        return BeliefEvent(
            event_id=event_id,
            belief_id=belief_id,
            subject=subject,
            claim_kind=claim_kind,
            claim=claim,
            valid_at=valid_at,
            transaction_at=transaction_at,
            evidence_cutoff=evidence_cutoff,
            confidence=confidence,
            evidence=evidence,
            falsifiers=falsifiers,
            status=status,
            transition_from_event_id=transition_from,
            supersedes_event_id=supersedes,
            content_hash=content_hash,
        )
    except ValueError:
        return None


def validate_belief_event(event: object) -> bool:
    """Return whether a typed event satisfies the canonical admission contract."""
    return type(event) is BeliefEvent and _event_is_valid(event)


def canonical_belief_event_payload(event: BeliefEvent) -> dict[str, object]:
    """Serialize one canonically admitted event for authoritative persistence."""
    if not validate_belief_event(event):
        raise ValueError(_INVALID_EVENT)
    return {**_event_material(event), "content_hash": event.content_hash}


def validate_belief_evidence(
    events: tuple[BeliefEvent, ...],
    artifacts: tuple[BeliefEvidenceArtifact, ...],
) -> RecordRefusalCode | None:
    """Validate immutable evidence facts before authoritative use."""
    if (
        type(events) is not tuple
        or any(not validate_belief_event(event) for event in events)
        or type(artifacts) is not tuple
        or any(not _valid_artifact(artifact) for artifact in artifacts)
    ):
        return RecordRefusalCode.INVALID_EVIDENCE
    artifact_ids = tuple(artifact.artifact_id for artifact in artifacts)
    if artifact_ids != tuple(sorted(set(artifact_ids))):
        return RecordRefusalCode.INVALID_EVIDENCE
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    for event in events:
        for reference in event.evidence:
            artifact = by_id.get(reference.artifact_id)
            if artifact is None:
                return RecordRefusalCode.EVIDENCE_MISSING
            if artifact.content_hash != reference.content_hash:
                return RecordRefusalCode.EVIDENCE_HASH_MISMATCH
            if artifact.available_at.value > event.evidence_cutoff.value:
                return RecordRefusalCode.EVIDENCE_AFTER_CUTOFF
    return None


def _event_is_valid(event: BeliefEvent) -> bool:
    try:
        normalized_confidence = _normalize_confidence(event.confidence)
    except ValueError:
        return False
    return (
        _IDENTIFIER.fullmatch(event.event_id) is not None
        and _IDENTIFIER.fullmatch(event.belief_id) is not None
        and type(event.subject) is EquityInstrumentIdentity
        and type(event.claim_kind) is BeliefClaimKind
        and _bounded_text(event.claim, _MAXIMUM_CLAIM_CHARACTERS)
        and type(event.valid_at) is UtcInstant
        and type(event.transaction_at) is UtcInstant
        and type(event.evidence_cutoff) is UtcInstant
        and event.valid_at.value <= event.transaction_at.value
        and event.evidence_cutoff.value <= event.transaction_at.value
        and normalized_confidence == event.confidence
        and _valid_evidence_tuple(event.evidence)
        and _valid_falsifier_tuple(event.falsifiers)
        and type(event.status) is BeliefStatus
        and _optional_identifier(event.transition_from_event_id)
        and _optional_identifier(event.supersedes_event_id)
        and is_sha256(event.content_hash)
        and _event_content_hash(event) == event.content_hash
    )


def _event_material(event: BeliefEvent) -> dict[str, object]:
    return {
        "schema_version": _BELIEF_SCHEMA_VERSION,
        "record_kind": "belief_event",
        "event_id": event.event_id,
        "belief_id": event.belief_id,
        "subject": event.subject.to_payload(),
        "claim": {"kind": event.claim_kind.value, "statement": event.claim},
        "valid_at": event.valid_at.isoformat(),
        "transaction_at": event.transaction_at.isoformat(),
        "evidence_cutoff": event.evidence_cutoff.isoformat(),
        "confidence": event.confidence,
        "evidence": [reference.to_payload() for reference in event.evidence],
        "falsifiers": list(event.falsifiers),
        "status": event.status.value,
        "transition_from_event_id": event.transition_from_event_id,
        "supersedes_event_id": event.supersedes_event_id,
    }


def _event_content_hash(event: BeliefEvent) -> str:
    encoded = json.dumps(_event_material(event), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _normalize_confidence(value: object) -> str:
    if (
        type(value) is not str
        or len(value) > _MAXIMUM_CONFIDENCE_CHARACTERS
        or _CONFIDENCE_INPUT.fullmatch(value) is None
    ):
        raise ValueError(_INVALID_EVENT)
    confidence = Decimal(value)
    if not confidence.is_finite() or confidence < 0 or confidence > 1:
        raise ValueError(_INVALID_EVENT)
    normalized = f"{confidence.normalize():f}"
    return "0" if normalized == "-0" else normalized


def _valid_evidence_tuple(value: object) -> bool:
    if type(value) is not tuple or not 1 <= len(value) <= _MAXIMUM_EVIDENCE_REFERENCES:
        return False
    if any(not _valid_reference(reference) for reference in value):
        return False
    keys = tuple(reference.artifact_id for reference in value)
    return keys == tuple(sorted(set(keys)))


def _valid_falsifier_tuple(value: object) -> bool:
    return (
        type(value) is tuple
        and 1 <= len(value) <= _MAXIMUM_FALSIFIERS
        and all(_bounded_text(item, _MAXIMUM_FALSIFIER_CHARACTERS) for item in value)
        and value == tuple(sorted(set(value)))
    )


def _bounded_text(value: object, maximum: int) -> bool:
    return (
        type(value) is str
        and value == value.strip()
        and 0 < len(value) <= maximum
        and "\x00" not in value
    )


def _valid_artifact(value: object) -> bool:
    return (
        type(value) is BeliefEvidenceArtifact
        and is_sha256(value.artifact_id)
        and is_sha256(value.content_hash)
        and type(value.available_at) is UtcInstant
    )


def _valid_reference(value: object) -> bool:
    return (
        type(value) is BeliefEvidenceReference
        and is_sha256(value.artifact_id)
        and is_sha256(value.content_hash)
    )


def _optional_identifier(value: object) -> bool:
    return value is None or (type(value) is str and _IDENTIFIER.fullmatch(value) is not None)


def _is_json_object(value: object) -> TypeGuard[dict[str, object]]:
    return type(value) is dict and all(type(key) is str for key in value)


def _is_json_list(value: object) -> TypeGuard[list[object]]:
    return type(value) is list


def _exact_mapping(
    value: object,
    fields: frozenset[str],
) -> dict[str, object] | None:
    if not _is_json_object(value) or frozenset(value) != fields:
        return None
    return value


def _text(value: object) -> str | None:
    return value if type(value) is str else None


def _optional_text(value: object) -> tuple[bool, str | None]:
    if value is None:
        return True, None
    if type(value) is str:
        return True, value
    return False, None


def _claim_kind(value: object) -> BeliefClaimKind | None:
    if type(value) is not str:
        return None
    try:
        return BeliefClaimKind(value)
    except ValueError:
        return None


def _belief_status(value: object) -> BeliefStatus | None:
    if type(value) is not str:
        return None
    try:
        return BeliefStatus(value)
    except ValueError:
        return None


def _instant(value: object) -> UtcInstant | None:
    try:
        return UtcInstant.parse(value)
    except ValueError:
        return None


def _evidence_references(value: object) -> tuple[BeliefEvidenceReference, ...] | None:
    if not _is_json_list(value):
        return None
    references: list[BeliefEvidenceReference] = []
    for item in value:
        fields = _exact_mapping(item, _EVIDENCE_REFERENCE_FIELDS)
        if fields is None:
            return None
        artifact_id = _text(fields["artifact_id"])
        content_hash = _text(fields["content_hash"])
        if artifact_id is None:
            return None
        if content_hash is None:
            return None
        try:
            references.append(BeliefEvidenceReference(artifact_id, content_hash))
        except ValueError:
            return None
    return tuple(references)


def _string_tuple(value: object) -> tuple[str, ...] | None:
    if not _is_json_list(value) or any(type(item) is not str for item in value):
        return None
    return tuple(item for item in value if type(item) is str)


def _schema_version(value: object, expected: int) -> bool:
    return type(value) is int and value == expected
