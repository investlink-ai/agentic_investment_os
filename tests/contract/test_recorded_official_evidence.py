from __future__ import annotations

from copy import deepcopy

from agentic_investment_os.adapters.recorded_evidence import RecordedOfficialEvidenceSource
from agentic_investment_os.evidence.capture import (
    EvidenceCandidate,
    EvidenceCaptureStatus,
    EvidenceFeed,
    EvidenceKind,
    EvidencePolicy,
    EvidenceRefusalReason,
    EvidenceRetrieval,
    EvidenceSourceDisposition,
)
from tests._evidence import (
    evidence_policy,
    official_evidence_item,
    recorded_official_evidence,
)


def _official_requests() -> tuple[EvidenceRetrieval, ...]:
    policy = EvidencePolicy.parse(evidence_policy())
    assert isinstance(policy, EvidencePolicy)
    return tuple(
        request
        for request in policy.requests
        if request.kind not in (EvidenceKind.MARKET, EvidenceKind.NEWS)
    )


def test_recorded_official_sources_preserve_identity_and_as_of_provenance() -> None:
    source = RecordedOfficialEvidenceSource(recorded_official_evidence())

    results = tuple(source.retrieve(request) for request in _official_requests())

    assert all(isinstance(result, EvidenceCandidate) for result in results)
    candidates = tuple(result for result in results if isinstance(result, EvidenceCandidate))
    assert {candidate.feed for candidate in candidates} == {
        EvidenceFeed.SEC_EDGAR,
        EvidenceFeed.ISSUER_INVESTOR_RELATIONS,
        EvidenceFeed.FEDERAL_RESERVE,
        EvidenceFeed.BLS,
        EvidenceFeed.BEA,
    }
    sec = next(candidate for candidate in candidates if candidate.kind is EvidenceKind.SEC_FILING)
    assert sec.source_identity == "0000320193-26-000081"
    assert sec.published_at is not None
    assert sec.available_at == sec.first_observed_at
    assert sec.entity_mappings[0].mapping_version == "issuer-map-v1"


def test_entity_mapping_availability_delays_the_derived_evidence_availability() -> None:
    payload = recorded_official_evidence()
    sec = official_evidence_item(payload, 0)
    mappings = sec["entity_mappings"]
    assert isinstance(mappings, list)
    mapping = mappings[0]
    assert isinstance(mapping, dict)
    mapping["available_at"] = "2026-08-21T19:00:00.000000+00:00"

    result = RecordedOfficialEvidenceSource(payload).retrieve(_official_requests()[0])

    assert isinstance(result, EvidenceCandidate)
    assert result.available_at.isoformat() == "2026-08-21T19:00:00.000000+00:00"


def test_current_sec_aggregate_is_invalid_instead_of_becoming_point_in_time_evidence() -> None:
    payload = recorded_official_evidence()
    sec = official_evidence_item(payload, 0)
    sec["content"] = {
        "accession_number": "0000320193-26-000081",
        "current_submission_summary": [],
    }

    result = RecordedOfficialEvidenceSource(payload).retrieve(_official_requests()[0])

    assert result == EvidenceSourceDisposition(
        EvidenceCaptureStatus.INVALID,
        EvidenceRefusalReason.INVALID_RECORDED_INPUT,
        "alpaca-basic-iex-v1",
    )


def test_ambiguous_mapping_is_explicit_and_never_attached_to_a_subject() -> None:
    payload = recorded_official_evidence()
    sec = official_evidence_item(payload, 0)
    mappings = sec["entity_mappings"]
    assert isinstance(mappings, list)
    mapping = mappings[0]
    assert isinstance(mapping, dict)
    mapping["confidence"] = "ambiguous"
    mapping["identity"] = None

    result = RecordedOfficialEvidenceSource(payload).retrieve(_official_requests()[0])

    assert result == EvidenceSourceDisposition(
        EvidenceCaptureStatus.AMBIGUOUS,
        EvidenceRefusalReason.AMBIGUOUS_ENTITY_MAPPING,
        "alpaca-basic-iex-v1",
    )


def test_multiple_exact_issuer_mappings_are_an_explicit_ambiguity() -> None:
    payload = recorded_official_evidence()
    sec = official_evidence_item(payload, 0)
    mappings = sec["entity_mappings"]
    assert isinstance(mappings, list)
    mappings.append(deepcopy(mappings[0]))

    result = RecordedOfficialEvidenceSource(payload).retrieve(_official_requests()[0])

    assert result == EvidenceSourceDisposition(
        EvidenceCaptureStatus.AMBIGUOUS,
        EvidenceRefusalReason.AMBIGUOUS_ENTITY_MAPPING,
        "alpaca-basic-iex-v1",
    )


def test_official_source_cannot_be_substituted_for_the_configured_authority() -> None:
    payload = recorded_official_evidence()
    sec = official_evidence_item(payload, 0)
    sec["source"] = "issuer_investor_relations"

    result = RecordedOfficialEvidenceSource(payload).retrieve(_official_requests()[0])

    assert result == EvidenceSourceDisposition(
        EvidenceCaptureStatus.INVALID,
        EvidenceRefusalReason.INVALID_RECORDED_INPUT,
        "alpaca-basic-iex-v1",
    )


def test_amendment_representation_requires_its_own_accession_and_relationship() -> None:
    payload = recorded_official_evidence()
    sec = official_evidence_item(payload, 0)
    amendment = deepcopy(sec["content"])
    assert isinstance(amendment, dict)
    amendment["form"] = "10-Q/A"
    amendment["amends_accession"] = None
    sec["content"] = amendment

    result = RecordedOfficialEvidenceSource(payload).retrieve(_official_requests()[0])

    assert isinstance(result, EvidenceSourceDisposition)
    assert result.status is EvidenceCaptureStatus.INVALID
