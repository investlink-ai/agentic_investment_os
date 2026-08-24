from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest

from agentic_investment_os.domain.identity import EquityInstrumentIdentity
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.evidence.capture import (
    CaptureEvidence,
    CaptureIntent,
    CaptureOutcome,
    EvidenceArtifact,
    EvidenceCandidate,
    EvidenceCaptureStatus,
    EvidenceCoverage,
    EvidenceEntityMapping,
    EvidenceFeed,
    EvidenceKind,
    EvidenceMappingDisposition,
    EvidencePolicy,
    EvidenceQuery,
    EvidenceRefusalReason,
    EvidenceRetrieval,
    EvidenceSourceDisposition,
    EvidenceSourceResult,
    EvidenceStoredRecord,
    InvalidEvidenceError,
    parse_capture_intent,
    parse_capture_outcome,
    select_evidence_as_of,
)
from tests._evidence import alpaca_evidence_policy, evidence_policy

RUN_ID = "a" * 64
SNAPSHOT_ID = "b" * 64
INVALID_EVIDENCE = "invalid evidence"
HOSTILE_BOOLEAN = True
MARKET_CONTENT = (
    b'{"bars":[{"asset_id":"equity-aapl","close":"225.50",'
    b'"timestamp":"2026-08-21T19:00:00.000000+00:00"}]}'
)
SECOND_MARKET_CONTENT = (
    b'{"bars":[{"asset_id":"equity-aapl","close":"226.00",'
    b'"timestamp":"2026-08-21T19:00:00.000000+00:00"}]}'
)


def _instant(hour: int, minute: int = 0) -> UtcInstant:
    return UtcInstant.from_datetime(datetime(2026, 8, 21, hour, minute, tzinfo=UTC))


def _market_candidate(*, first_observed_at: UtcInstant) -> EvidenceCandidate:
    return EvidenceCandidate.create(
        retrieval_identity="market-session-bars",
        source_identity="market-session-bars",
        kind=EvidenceKind.MARKET,
        source_event_at=_instant(19),
        published_at=None,
        first_observed_at=first_observed_at,
        data_regime="alpaca-basic-iex-v1",
        feed=EvidenceFeed.IEX,
        entity_mappings=(
            EvidenceEntityMapping.exact(
                EquityInstrumentIdentity(
                    "alpaca-paper",
                    "equity-aapl",
                    "NASDAQ",
                ),
                mapping_version="alpaca-paper-catalog-v1",
                available_at=_instant(18),
            ),
        ),
        content=MARKET_CONTENT,
    )


def _policy() -> EvidencePolicy:
    parsed = EvidencePolicy.parse(alpaca_evidence_policy())
    assert isinstance(parsed, EvidencePolicy)
    return parsed


def _intent() -> CaptureIntent:
    return CaptureIntent.create(
        run_id=RUN_ID,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=_instant(20),
        data_regime="alpaca-basic-iex-v1",
        request=_policy().requests[0],
    )


def _captured_outcome() -> CaptureOutcome:
    intent = _intent()
    return CaptureOutcome(
        intent.intent_id,
        EvidenceCaptureStatus.CAPTURED,
        None,
        EvidenceArtifact.from_candidate(
            _market_candidate(first_observed_at=_instant(19, 5)),
            observation_id=intent.intent_id,
        ),
    )


@dataclass
class _MemoryVault:
    policies: dict[str, EvidencePolicy] = field(default_factory=dict)
    intents: list[CaptureIntent] = field(default_factory=list)
    outcomes: dict[str, CaptureOutcome] = field(default_factory=dict)

    def append_policy(
        self,
        policy: EvidencePolicy,
        capture_intents: tuple[CaptureIntent, ...],
    ) -> None:
        _ = capture_intents
        self.policies[policy.policy_id] = policy

    def load_policy(self, policy_id: str) -> EvidencePolicy:
        return self.policies[policy_id]

    def append_intent(self, intent: CaptureIntent) -> None:
        self.intents.append(intent)

    def load_outcome(self, intent: CaptureIntent) -> CaptureOutcome | None:
        return self.outcomes.get(intent.intent_id)

    def append_outcome(
        self,
        intent: CaptureIntent,
        outcome: CaptureOutcome,
        content: bytes | None,
    ) -> None:
        _ = (intent, content)
        self.outcomes[outcome.intent_id] = outcome

    def stored_records(self) -> tuple[EvidenceStoredRecord, ...]:
        return ()


@dataclass(frozen=True)
class _FixedSource:
    result: EvidenceSourceResult

    def retrieve(self, request: EvidenceRetrieval) -> EvidenceSourceResult:
        _ = request
        return self.result


def test_content_identity_is_stable_while_distinct_observations_use_availability() -> None:
    first = EvidenceArtifact.from_candidate(
        _market_candidate(first_observed_at=_instant(19, 5)),
        observation_id="1" * 64,
    )
    repeated = EvidenceArtifact.from_candidate(
        _market_candidate(first_observed_at=_instant(19, 20)),
        observation_id="2" * 64,
    )

    assert first.content_hash == repeated.content_hash
    assert first.artifact_id != repeated.artifact_id
    assert first.observation_id != repeated.observation_id
    assert first.available_at == _instant(19, 5)
    assert repeated.available_at == _instant(19, 20)

    admitted = select_evidence_as_of(
        (
            EvidenceStoredRecord(first, MARKET_CONTENT),
            EvidenceStoredRecord(repeated, MARKET_CONTENT),
        ),
        EvidenceQuery(
            cutoff=_instant(19, 10),
            data_regime="alpaca-basic-iex-v1",
            limit=10,
        ),
    )

    assert admitted == (EvidenceStoredRecord(first, MARKET_CONTENT),)
    exact_cutoff_record = EvidenceStoredRecord(first, MARKET_CONTENT)
    assert select_evidence_as_of(
        (exact_cutoff_record,),
        EvidenceQuery(first.available_at, "alpaca-basic-iex-v1", 10),
    ) == (exact_cutoff_record,)


def test_evidence_identifiers_pin_canonical_json_serialization() -> None:
    intent = _intent()
    outcome = _captured_outcome()
    artifact = outcome.artifact
    assert artifact is not None

    assert intent.intent_id == "322b00bdc25d3e7406301f8daf25e7950aa92a4334079628a07597a7e6182ed9"
    assert intent.to_payload()["content_hash"] == (
        "0d7610360d7ffbcd9e13f275ad6e3e107709e33affa8c3f7176b4f02c86dd5cf"
    )
    artifact_payload = artifact.to_payload()
    assert artifact.observation_id == intent.intent_id
    assert artifact.artifact_id != artifact.observation_id
    assert artifact_payload["record_kind"] == "evidence_snapshot"
    assert artifact_payload["payload_discriminator"] == "alpaca_market_evidence"
    assert artifact_payload["authority_scope"] == "research_evidence"
    assert artifact_payload["content_hash"] == artifact.artifact_id
    fingerprints = artifact_payload["material_fingerprints"]
    assert isinstance(fingerprints, dict)
    assert fingerprints["source_content"] == artifact.content_hash
    assert outcome.to_payload()["content_hash"] == (
        "97e45be63d9096cdeec23d33cd23ccb33446646789af92f0af405a37b45285ee"
    )


def test_evidence_value_objects_reject_invalid_states() -> None:
    identity = EquityInstrumentIdentity("alpaca-paper", "equity-aapl", "NASDAQ")
    request = _policy().requests[0]
    artifact = _captured_outcome().artifact
    assert artifact is not None

    invalid_constructors = (
        lambda: EvidenceEntityMapping(identity, "ambiguous", "map-v1", _instant(18)),
        lambda: EvidenceRetrieval(
            kind=EvidenceKind.MARKET,
            source=EvidenceFeed.IEX,
            retrieval_identity="NOT VALID",
            maximum_age_seconds=1,
            required=True,
        ),
        lambda: EvidencePolicy("invalid regime", _policy().requests),
        lambda: replace(_market_candidate(first_observed_at=_instant(19, 5)), content=b""),
        lambda: EvidenceSourceDisposition(
            EvidenceCaptureStatus.CAPTURED,
            EvidenceRefusalReason.SOURCE_REFUSED,
            "alpaca-basic-iex-v1",
        ),
        lambda: CaptureOutcome(
            "not-a-hash",
            EvidenceCaptureStatus.REFUSED,
            EvidenceRefusalReason.SOURCE_REFUSED,
            None,
        ),
        lambda: CaptureOutcome(
            request.retrieval_identity, EvidenceCaptureStatus.CAPTURED, None, None
        ),
        lambda: EvidenceStoredRecord(artifact, b"changed"),
        lambda: EvidenceQuery(_instant(20), "invalid regime", 0),
    )

    for construct in invalid_constructors:
        with pytest.raises(InvalidEvidenceError):
            construct()


def test_source_disposition_accepts_only_owner_defined_status_reason_shapes() -> None:
    allowed = {
        (EvidenceCaptureStatus.UNAVAILABLE, EvidenceRefusalReason.SOURCE_UNAVAILABLE),
        (EvidenceCaptureStatus.STALE, EvidenceRefusalReason.SOURCE_STALE),
        (EvidenceCaptureStatus.REFUSED, EvidenceRefusalReason.SOURCE_REFUSED),
        (EvidenceCaptureStatus.REFUSED, EvidenceRefusalReason.INVALID_RECORDED_INPUT),
        (EvidenceCaptureStatus.INVALID, EvidenceRefusalReason.INVALID_RECORDED_INPUT),
        (EvidenceCaptureStatus.AMBIGUOUS, EvidenceRefusalReason.AMBIGUOUS_ENTITY_MAPPING),
    }
    for status in EvidenceCaptureStatus:
        for reason in EvidenceRefusalReason:
            if (status, reason) in allowed:
                mapping = (
                    EvidenceMappingDisposition("issuer-map-v1", _instant(19))
                    if status is EvidenceCaptureStatus.AMBIGUOUS
                    else None
                )
                disposition = EvidenceSourceDisposition(
                    status,
                    reason,
                    "alpaca-basic-iex-v1",
                    mapping,
                )
                assert disposition.data_regime == "alpaca-basic-iex-v1"
            else:
                with pytest.raises(InvalidEvidenceError):
                    EvidenceSourceDisposition(
                        status,
                        reason,
                        "alpaca-basic-iex-v1",
                    )
    assert (
        EvidenceSourceDisposition(
            EvidenceCaptureStatus.REFUSED,
            EvidenceRefusalReason.INVALID_RECORDED_INPUT,
            None,
        ).data_regime
        is None
    )
    with pytest.raises(InvalidEvidenceError):
        EvidenceSourceDisposition(
            EvidenceCaptureStatus.UNAVAILABLE,
            EvidenceRefusalReason.SOURCE_UNAVAILABLE,
            None,
        )


def test_capture_outcome_accepts_only_owner_defined_status_reason_artifact_shapes() -> None:
    artifact = _captured_outcome().artifact
    assert artifact is not None
    allowed = {
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
    }
    for status in EvidenceCaptureStatus:
        for reason in (None, *EvidenceRefusalReason):
            for has_artifact in (False, True):
                for has_mapping in (False, True):
                    mapping = (
                        EvidenceMappingDisposition("issuer-map-v1", _instant(19))
                        if has_mapping
                        else None
                    )
                    if (status, reason, has_artifact, has_mapping) in allowed:
                        outcome = CaptureOutcome(
                            _intent().intent_id,
                            status,
                            reason,
                            artifact if has_artifact else None,
                            mapping,
                        )
                        assert outcome.status is status
                    else:
                        with pytest.raises(InvalidEvidenceError):
                            CaptureOutcome(
                                _intent().intent_id,
                                status,
                                reason,
                                artifact if has_artifact else None,
                                mapping,
                            )


@pytest.mark.parametrize(
    "value",
    [
        None,
        {**evidence_policy(), "schema_version": 3},
        {**evidence_policy(), "data_regime": "INVALID"},
        {**evidence_policy(), "requests": "not-a-list"},
        {**evidence_policy(), "requests": [{"kind": "market"}]},
        {
            **evidence_policy(),
            "requests": [
                {
                    "kind": True,
                    "retrieval_identity": "market-session-bars",
                    "maximum_age_seconds": 1,
                }
            ],
        },
        {
            **evidence_policy(),
            "requests": [
                {
                    "kind": "unknown",
                    "retrieval_identity": "market-session-bars",
                    "maximum_age_seconds": 1,
                }
            ],
        },
    ],
)
def test_evidence_policy_parser_rejects_hostile_shapes(value: object) -> None:
    assert EvidencePolicy.parse(value) is None


@pytest.mark.parametrize(
    "changed_field",
    ["retrieval_identity", "kind", "data_regime"],
)
def test_capture_evidence_rejects_invalid_call_facts_and_source_contracts(
    changed_field: str,
) -> None:
    policy = _policy()
    vault = _MemoryVault()
    candidate = _market_candidate(first_observed_at=_instant(19, 5))
    capture = CaptureEvidence(policy, _FixedSource(candidate), vault)

    with pytest.raises(InvalidEvidenceError):
        capture(
            run_id="invalid",
            universe_snapshot_id=SNAPSHOT_ID,
            cutoff=_instant(20),
            data_regime=policy.data_regime,
        )

    if changed_field == "kind":
        mismatched = EvidenceCandidate.create(
            retrieval_identity=candidate.retrieval_identity,
            source_identity="news-1",
            kind=EvidenceKind.NEWS,
            source_event_at=None,
            published_at=_instant(19),
            first_observed_at=_instant(19, 5),
            data_regime=candidate.data_regime,
            feed=EvidenceFeed.ALPACA_NEWS,
            entity_mappings=candidate.entity_mappings,
            content=candidate.content,
        )
    elif changed_field == "retrieval_identity":
        mismatched = replace(candidate, retrieval_identity="different-retrieval")
    else:
        assert changed_field == "data_regime"
        mismatched = replace(candidate, data_regime="another-regime")
    refused = CaptureEvidence(policy, _FixedSource(mismatched), _MemoryVault())(
        run_id=RUN_ID,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=_instant(20),
        data_regime=policy.data_regime,
    )
    assert all(outcome.status is EvidenceCaptureStatus.REFUSED for outcome in refused.outcomes)
    assert all(
        outcome.refusal_reason is EvidenceRefusalReason.UNSUPPORTED_CONTRACT
        for outcome in refused.outcomes
    )

    unavailable = CaptureEvidence(
        policy,
        _FixedSource(
            EvidenceSourceDisposition(
                EvidenceCaptureStatus.UNAVAILABLE,
                EvidenceRefusalReason.SOURCE_UNAVAILABLE,
                policy.data_regime,
            )
        ),
        _MemoryVault(),
    )(
        run_id=RUN_ID,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=_instant(20),
        data_regime=policy.data_regime,
    )
    assert unavailable.artifact_ids == ()
    assert len(unavailable.refusal_ids) == len(policy.requests)


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (EvidenceCaptureStatus.UNAVAILABLE, EvidenceRefusalReason.SOURCE_UNAVAILABLE),
        (EvidenceCaptureStatus.STALE, EvidenceRefusalReason.SOURCE_STALE),
        (EvidenceCaptureStatus.REFUSED, EvidenceRefusalReason.SOURCE_REFUSED),
    ],
)
def test_capture_refuses_each_non_capture_disposition_from_another_regime(
    status: EvidenceCaptureStatus,
    reason: EvidenceRefusalReason,
) -> None:
    policy = _policy()
    summary = CaptureEvidence(
        policy,
        _FixedSource(EvidenceSourceDisposition(status, reason, "another-regime")),
        _MemoryVault(),
    )(
        run_id=RUN_ID,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=_instant(20),
        data_regime=policy.data_regime,
    )

    assert all(
        outcome.status is EvidenceCaptureStatus.REFUSED
        and outcome.refusal_reason is EvidenceRefusalReason.UNSUPPORTED_CONTRACT
        for outcome in summary.outcomes
    )


@pytest.mark.parametrize("cutoff", [_instant(19, 5), _instant(21, 5)])
def test_capture_includes_exact_cutoff_and_maximum_age_boundaries(cutoff: UtcInstant) -> None:
    policy = _policy()
    summary = CaptureEvidence(
        policy,
        _FixedSource(_market_candidate(first_observed_at=_instant(19, 5))),
        _MemoryVault(),
    )(
        run_id=RUN_ID,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=cutoff,
        data_regime=policy.data_regime,
    )

    assert summary.outcomes[0].status is EvidenceCaptureStatus.CAPTURED


def test_capture_intent_parser_rejects_hostile_shapes() -> None:
    payload = _intent().to_payload()
    invalid_payloads: list[object] = [
        None,
        {**payload, "schema_version": 3},
        {**payload, "record_kind": "unknown"},
        {**payload, "request": None},
        {
            **payload,
            "request": {"kind": True, "retrieval_identity": "valid", "maximum_age_seconds": 1},
        },
        {
            **payload,
            "request": {"kind": "unknown", "retrieval_identity": "valid", "maximum_age_seconds": 1},
        },
        {**payload, "cutoff": "invalid"},
        {**payload, "run_id": "invalid"},
        {**payload, "universe_snapshot_id": "invalid"},
        {**payload, "data_regime": "INVALID"},
        {
            **payload,
            "request": {
                "kind": "market",
                "retrieval_identity": True,
                "maximum_age_seconds": 1,
            },
        },
        {
            **payload,
            "request": {
                "kind": "market",
                "retrieval_identity": "valid",
                "maximum_age_seconds": True,
            },
        },
        {**payload, "intent_id": "c" * 64},
    ]

    assert parse_capture_intent(payload) == _intent()
    assert all(parse_capture_intent(value) is None for value in invalid_payloads)


def _captured_payload() -> dict[str, object]:
    return deepcopy(_captured_outcome().to_payload())


def _artifact_payload(payload: dict[str, object]) -> dict[str, object]:
    artifact = payload["artifact"]
    assert isinstance(artifact, dict)
    return artifact


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _reseal_artifact_payload(payload: dict[str, object]) -> None:
    artifact = _artifact_payload(payload)
    artifact["content_hash"] = _canonical_hash(
        {key: value for key, value in artifact.items() if key != "content_hash"}
    )
    payload["content_hash"] = _canonical_hash(
        {key: value for key, value in payload.items() if key != "content_hash"}
    )


def _reseal_outcome(payload: dict[str, object]) -> None:
    payload["content_hash"] = _canonical_hash(
        {key: value for key, value in payload.items() if key != "content_hash"}
    )


def test_capture_outcome_parser_rejects_hostile_outcome_shapes() -> None:
    payload = _captured_payload()
    invalid_payloads: list[object] = [
        None,
        {**payload, "schema_version": 3},
        {**payload, "record_kind": "unknown"},
        {**payload, "intent_id": True},
        {**payload, "status": True},
        {**payload, "status": "unknown"},
        {**payload, "refusal_reason": True},
        {**payload, "refusal_reason": "unknown"},
        {**payload, "artifact": "not-an-artifact"},
        {**payload, "artifact": None},
        {**payload, "content_hash": "c" * 64},
    ]

    assert parse_capture_outcome(payload) == _captured_outcome()
    assert all(parse_capture_outcome(value) is None for value in invalid_payloads)


@pytest.mark.parametrize(
    ("status", "reason", "artifact_shape"),
    [
        ("captured", "source_refused", "present"),
        ("unavailable", "after_cutoff", "present"),
        ("unavailable", "source_unavailable", "present"),
        ("stale", "source_stale", "present"),
        ("refused", "after_cutoff", "absent"),
    ],
)
def test_capture_outcome_parser_rejects_resealed_contradictory_dispositions(
    status: str,
    reason: str,
    artifact_shape: str,
) -> None:
    payload = _captured_payload()
    payload["status"] = status
    payload["refusal_reason"] = reason
    if artifact_shape == "absent":
        payload["artifact"] = None
    else:
        assert artifact_shape == "present"
    _reseal_outcome(payload)

    assert parse_capture_outcome(payload) is None


def test_capture_outcome_parser_rejects_hostile_artifact_shapes() -> None:
    base = _captured_payload()
    variants: list[dict[str, object]] = []

    def variant(field: str, value: object) -> None:
        changed = deepcopy(base)
        _artifact_payload(changed)[field] = value
        variants.append(changed)

    variant("envelope_schema_version", 3)
    variant("record_kind", "unknown")
    variant("payload_discriminator", "unknown")
    variant("payload_schema_version", 3)
    variant("subject", "not-a-subject")
    variant("material_fingerprints", {})
    variant("payload", "not-a-payload")
    variant("relevant_at", "invalid")
    variant("available_at", "invalid")
    variant("authority_scope", "execution")
    variant("content_hash", HOSTILE_BOOLEAN)

    for subject in (
        {"entity_mappings": "not-a-list"},
        {"entity_mappings": [None]},
        {"entity_mappings": [{"identity": None, "confidence": "ambiguous"}]},
        {"entity_mappings": [{"identity": None, "confidence": "exact"}]},
    ):
        variant("subject", subject)

    changed_payload = deepcopy(base)
    artifact_payload = _artifact_payload(changed_payload)["payload"]
    assert isinstance(artifact_payload, dict)
    artifact_payload["first_observed_at"] = "invalid"
    variants.append(changed_payload)

    assert all(parse_capture_outcome(value) is None for value in variants)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("entitlement", "alpaca-pro"),
        ("parser_version", "unknown-parser"),
        ("normalization_version", "unknown-normalizer"),
        ("feed", "alpaca_news"),
        ("coverage", EvidenceCoverage.ALPACA_NEWS_ENTITLEMENT.value),
        ("source_event_at", None),
        ("published_at", "2026-08-21T19:00:00.000000+00:00"),
    ],
)
def test_capture_outcome_rejects_resealed_artifacts_that_violate_provenance_contracts(
    field: str,
    value: object,
) -> None:
    payload = _captured_payload()
    artifact_variant = _artifact_payload(payload)["payload"]
    assert isinstance(artifact_variant, dict)
    artifact_variant[field] = value
    _reseal_artifact_payload(payload)

    assert parse_capture_outcome(payload) is None


def test_capture_outcome_rejects_resealed_invalid_artifact_identifiers() -> None:
    invalid_artifact = _captured_payload()
    _artifact_payload(invalid_artifact)["content_hash"] = "invalid"
    _reseal_outcome(invalid_artifact)
    invalid_source_content = _captured_payload()
    source_payload = _artifact_payload(invalid_source_content)["payload"]
    assert isinstance(source_payload, dict)
    source_payload["source_content_hash"] = "invalid"
    _reseal_artifact_payload(invalid_source_content)

    assert parse_capture_outcome(invalid_artifact) is None
    assert parse_capture_outcome(invalid_source_content) is None


@pytest.mark.parametrize(
    ("feed", "coverage"),
    [
        (EvidenceFeed.IEX.value, EvidenceCoverage.ALPACA_NEWS_ENTITLEMENT.value),
        (EvidenceFeed.ALPACA_NEWS.value, EvidenceCoverage.IEX_BASIC_MARKET_ONLY.value),
    ],
)
def test_capture_outcome_rejects_resealed_news_feed_contract_mismatches(
    feed: str,
    coverage: str,
) -> None:
    payload = _captured_payload()
    artifact = _artifact_payload(payload)
    artifact["payload_discriminator"] = "alpaca_news_evidence"
    artifact["relevant_at"] = "2026-08-21T19:00:00.000000+00:00"
    variant_payload = artifact["payload"]
    assert isinstance(variant_payload, dict)
    variant_payload.update(
        {
            "source_event_at": None,
            "published_at": "2026-08-21T19:00:00.000000+00:00",
            "feed": feed,
            "coverage": coverage,
        }
    )
    _reseal_artifact_payload(payload)

    assert parse_capture_outcome(payload) is None


def test_as_of_lookup_orders_equal_availability_and_applies_limit() -> None:
    first = EvidenceArtifact.from_candidate(
        _market_candidate(first_observed_at=_instant(19, 5)),
        observation_id="1" * 64,
    )
    second_candidate = replace(
        _market_candidate(first_observed_at=_instant(19, 5)),
        content=SECOND_MARKET_CONTENT,
    )
    second = EvidenceArtifact.from_candidate(second_candidate, observation_id="2" * 64)
    records = (
        EvidenceStoredRecord(first, MARKET_CONTENT),
        EvidenceStoredRecord(second, second_candidate.content),
    )

    selected = select_evidence_as_of(
        records,
        EvidenceQuery(_instant(20), "alpaca-basic-iex-v1", 1),
    )

    assert len(selected) == 1
    assert selected[0].artifact.observation_id == min(
        first.observation_id,
        second.observation_id,
    )
