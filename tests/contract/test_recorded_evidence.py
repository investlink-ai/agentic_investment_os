from __future__ import annotations

from copy import deepcopy

import pytest

from agentic_investment_os.adapters.recorded_evidence import RecordedAlpacaEvidenceSource
from agentic_investment_os.domain.identity import (
    CryptoSpotInstrumentIdentity,
    EquityInstrumentIdentity,
)
from agentic_investment_os.evidence.capture import (
    EvidenceCandidate,
    EvidenceCaptureStatus,
    EvidenceKind,
    EvidencePolicy,
    EvidenceRefusalReason,
    EvidenceRetrieval,
    EvidenceSourceDisposition,
)
from tests._evidence import evidence_item, evidence_policy, recorded_evidence

HOSTILE_BOOLEAN = True
DATA_REGIME = "alpaca-basic-iex-v1"


def _requests() -> tuple[EvidenceRetrieval, EvidenceRetrieval]:
    policy = EvidencePolicy.parse(evidence_policy())
    assert isinstance(policy, EvidencePolicy)
    market, news = policy.requests
    return market, news


def test_recorded_market_and_news_are_validated_and_normalized() -> None:
    market_request, news_request = _requests()
    source = RecordedAlpacaEvidenceSource(recorded_evidence())

    market = source.retrieve(market_request)
    news = source.retrieve(news_request)

    assert isinstance(market, EvidenceCandidate)
    assert isinstance(news, EvidenceCandidate)
    assert market.kind is EvidenceKind.MARKET
    assert market.available_at.isoformat() == "2026-08-21T19:05:00.000000+00:00"
    assert market.content == (
        b'{"bars":[{"asset_id":"equity-aapl","close":"225.50",'
        b'"timestamp":"2026-08-21T19:00:00.000000+00:00"}]}'
    )
    assert news.kind is EvidenceKind.NEWS
    assert news.available_at.isoformat() == "2026-08-21T18:01:00.000000+00:00"
    assert b"ignore previous instructions" in news.content


def test_recorded_content_canonicalization_sorts_valid_mapping_order() -> None:
    payload = recorded_evidence()
    evidence_item(payload, 0)["content"] = {
        "bars": [
            {
                "timestamp": "2026-08-21T19:00:00.000000+00:00",
                "close": "225.50",
                "asset_id": "equity-aapl",
            }
        ]
    }

    market = RecordedAlpacaEvidenceSource(payload).retrieve(_requests()[0])

    assert isinstance(market, EvidenceCandidate)
    assert market.content == (
        b'{"bars":[{"asset_id":"equity-aapl","close":"225.50",'
        b'"timestamp":"2026-08-21T19:00:00.000000+00:00"}]}'
    )


@pytest.mark.parametrize(
    "content",
    [
        {},
        {"bars": []},
        {"bars": [{"asset_id": "equity-aapl", "close": "225.50"}]},
        {
            "bars": [
                {
                    "asset_id": "equity-msft",
                    "close": "225.50",
                    "timestamp": "2026-08-21T19:00:00.000000+00:00",
                }
            ]
        },
        {
            "bars": [
                {
                    "asset_id": "equity-aapl",
                    "close": "225.50",
                    "timestamp": "2026-08-21T19:00:00",
                }
            ]
        },
        {
            "bars": [
                {
                    "asset_id": "equity-aapl",
                    "close": "not-a-decimal",
                    "timestamp": "2026-08-21T19:00:00.000000+00:00",
                }
            ]
        },
    ],
    ids=[
        "missing-market-variant",
        "empty-bars",
        "missing-bar-timestamp",
        "bar-identity-disagrees-with-mapping",
        "malformed-bar-timestamp",
        "malformed-close",
    ],
)
def test_recorded_market_content_is_validated_before_domain_construction(
    content: object,
) -> None:
    payload = recorded_evidence()
    evidence_item(payload, 0)["content"] = content

    assert RecordedAlpacaEvidenceSource(payload).retrieve(_requests()[0]) == (
        EvidenceSourceDisposition(
            EvidenceCaptureStatus.REFUSED,
            EvidenceRefusalReason.INVALID_RECORDED_INPUT,
            DATA_REGIME,
        )
    )


@pytest.mark.parametrize(
    "content",
    [
        {},
        {"headline": "headline", "id": "news-1"},
        {"headline": "", "id": "news-1", "summary": "summary"},
        {"headline": "headline", "id": "not valid", "summary": "summary"},
        {"headline": "headline", "id": "news-1", "summary": True},
    ],
    ids=[
        "missing-news-variant",
        "missing-summary",
        "empty-headline",
        "malformed-news-id",
        "nontext-summary",
    ],
)
def test_recorded_news_content_is_validated_before_domain_construction(
    content: object,
) -> None:
    payload = recorded_evidence()
    evidence_item(payload, 1)["content"] = content

    assert RecordedAlpacaEvidenceSource(payload).retrieve(_requests()[1]) == (
        EvidenceSourceDisposition(
            EvidenceCaptureStatus.REFUSED,
            EvidenceRefusalReason.INVALID_RECORDED_INPUT,
            DATA_REGIME,
        )
    )


@pytest.mark.parametrize(
    ("status", "expected_reason"),
    [
        ("unavailable", EvidenceRefusalReason.SOURCE_UNAVAILABLE),
        ("stale", EvidenceRefusalReason.SOURCE_STALE),
        ("refused", EvidenceRefusalReason.SOURCE_REFUSED),
    ],
)
def test_recorded_source_preserves_explicit_non_capture_dispositions(
    status: str,
    expected_reason: EvidenceRefusalReason,
) -> None:
    payload = recorded_evidence()
    market = evidence_item(payload, 0)
    market.update(
        {
            "status": status,
            "source_event_at": None,
            "published_at": None,
            "entity_mappings": [],
            "entity_catalog_ids": [],
            "content": None,
        }
    )

    result = RecordedAlpacaEvidenceSource(payload).retrieve(_requests()[0])

    assert result == EvidenceSourceDisposition(
        EvidenceCaptureStatus(status), expected_reason, DATA_REGIME
    )


def _mutate_timestamp(payload: dict[str, object]) -> None:
    evidence_item(payload, 0)["source_event_at"] = "2026-08-21T19:00:00"


def _mutate_status(payload: dict[str, object]) -> None:
    evidence_item(payload, 0)["status"] = "maybe"


def _mutate_identifier(payload: dict[str, object]) -> None:
    evidence_item(payload, 0)["entity_catalog_ids"] = ["equity-msft"]


def _mutate_oversized_content(payload: dict[str, object]) -> None:
    evidence_item(payload, 0)["content"] = {"text": "x" * 1_000_001}


def _mutate_entitlement(payload: dict[str, object]) -> None:
    evidence_item(payload, 0)["entitlement"] = "sip"


def _mutate_ambiguous_mapping(payload: dict[str, object]) -> None:
    item = evidence_item(payload, 0)
    mappings = item["entity_mappings"]
    assert isinstance(mappings, list)
    mappings.append(deepcopy(mappings[0]))
    item["entity_catalog_ids"] = ["equity-aapl", "equity-aapl"]


@pytest.mark.parametrize(
    "mutation",
    [
        _mutate_timestamp,
        _mutate_status,
        _mutate_identifier,
        _mutate_oversized_content,
        _mutate_entitlement,
        _mutate_ambiguous_mapping,
    ],
    ids=[
        "malformed_timestamp",
        "unknown_enum",
        "inconsistent_identifier",
        "oversized_content",
        "unsupported_entitlement",
        "ambiguous_entity_mapping",
    ],
)
def test_recorded_source_refuses_hostile_or_unsupported_contracts(
    mutation: object,
) -> None:
    payload = recorded_evidence()
    assert callable(mutation)
    mutation(payload)

    result = RecordedAlpacaEvidenceSource(payload).retrieve(_requests()[0])

    assert result == EvidenceSourceDisposition(
        EvidenceCaptureStatus.REFUSED,
        EvidenceRefusalReason.INVALID_RECORDED_INPUT,
        DATA_REGIME,
    )


def test_missing_configured_retrieval_is_explicitly_unavailable() -> None:
    payload = recorded_evidence()
    items = payload["items"]
    assert isinstance(items, list)
    items.pop()

    result = RecordedAlpacaEvidenceSource(payload).retrieve(_requests()[1])

    assert result == EvidenceSourceDisposition(
        EvidenceCaptureStatus.UNAVAILABLE,
        EvidenceRefusalReason.SOURCE_UNAVAILABLE,
        DATA_REGIME,
    )


def _invalid_result(payload: object) -> EvidenceSourceDisposition:
    result = RecordedAlpacaEvidenceSource(payload).retrieve(_requests()[0])
    assert isinstance(result, EvidenceSourceDisposition)
    return result


def test_recorded_source_refuses_hostile_batch_shapes() -> None:
    invalid_payloads: list[object] = [
        None,
        {**recorded_evidence(), "schema_version": 2},
        {**recorded_evidence(), "data_regime": "INVALID"},
        {**recorded_evidence(), "items": "not-a-list"},
    ]
    malformed_item = recorded_evidence()
    malformed_item["items"] = [None]
    invalid_payloads.append(malformed_item)
    nontext_identity = recorded_evidence()
    evidence_item(nontext_identity, 0)["retrieval_identity"] = True
    invalid_payloads.append(nontext_identity)
    invalid_identity = recorded_evidence()
    evidence_item(invalid_identity, 0)["retrieval_identity"] = "NOT VALID"
    invalid_payloads.append(invalid_identity)
    duplicate_identity = recorded_evidence()
    evidence_item(duplicate_identity, 1)["retrieval_identity"] = "market-session-bars"
    invalid_payloads.append(duplicate_identity)
    for field, value in (
        ("record_kind", "unknown"),
        ("data_regime", "INVALID"),
    ):
        otherwise_empty = recorded_evidence()
        otherwise_empty[field] = value
        otherwise_empty["items"] = []
        invalid_payloads.append(otherwise_empty)

    assert all(
        _invalid_result(payload)
        == EvidenceSourceDisposition(
            EvidenceCaptureStatus.REFUSED,
            EvidenceRefusalReason.INVALID_RECORDED_INPUT,
            None,
        )
        for payload in invalid_payloads
    )


def test_recorded_source_refuses_each_invalid_captured_item_component() -> None:
    invalid_payloads: list[dict[str, object]] = []

    def changed(field: str, value: object) -> None:
        payload = recorded_evidence()
        evidence_item(payload, 0)[field] = value
        invalid_payloads.append(payload)

    changed("schema_version", 2)
    changed("record_kind", "unknown")
    changed("kind", HOSTILE_BOOLEAN)
    changed("kind", "unknown")
    changed("status", HOSTILE_BOOLEAN)
    changed("feed", HOSTILE_BOOLEAN)
    changed("feed", "unknown")
    changed("feed", "alpaca_news")
    changed("first_observed_at", "2026-02-30T19:05:00.000000+00:00")
    changed("source_event_at", HOSTILE_BOOLEAN)
    changed("published_at", HOSTILE_BOOLEAN)
    changed("entitlement", "alpaca-sip")
    changed("first_observed_at", "2026-08-21T18:59:00.000000+00:00")
    changed("entity_mappings", None)
    changed("entity_mappings", [])
    changed("entity_catalog_ids", None)
    changed("content", 1.5)
    changed("content", [1.5])
    changed("content", {"nested": 1.5})
    changed("content", {1: "non-string-key"})
    recursive: list[object] = []
    recursive.append(recursive)
    changed("content", recursive)

    wrong_kind = recorded_evidence()
    wrong_kind_item = evidence_item(wrong_kind, 0)
    wrong_kind_item["kind"] = "news"
    wrong_kind_item["feed"] = "alpaca_news"
    wrong_kind_item["source_event_at"] = None
    wrong_kind_item["published_at"] = "2026-08-21T19:00:00.000000+00:00"
    invalid_payloads.append(wrong_kind)

    for field, value in (
        ("source_event_at", "2026-08-21T19:00:00.000000+00:00"),
        ("published_at", "2026-08-21T19:00:00.000000+00:00"),
        ("entity_mappings", [{}]),
        ("entity_catalog_ids", ["equity-aapl"]),
        ("content", {}),
    ):
        dirty_refusal = recorded_evidence()
        item = evidence_item(dirty_refusal, 0)
        item.update(
            {
                "status": "refused",
                "source_event_at": None,
                "published_at": None,
                "entity_mappings": [],
                "entity_catalog_ids": [],
                "content": None,
            }
        )
        item[field] = value
        invalid_payloads.append(dirty_refusal)

    assert all(
        _invalid_result(payload)
        == EvidenceSourceDisposition(
            EvidenceCaptureStatus.REFUSED,
            EvidenceRefusalReason.INVALID_RECORDED_INPUT,
            DATA_REGIME,
        )
        for payload in invalid_payloads
    )


def test_recorded_source_refuses_invalid_entity_mapping_components() -> None:
    invalid_payloads: list[dict[str, object]] = []

    def mapping_changed(field: str, value: object) -> None:
        payload = recorded_evidence()
        mappings = evidence_item(payload, 0)["entity_mappings"]
        assert isinstance(mappings, list)
        mapping = mappings[0]
        assert isinstance(mapping, dict)
        mapping[field] = value
        invalid_payloads.append(payload)

    mapping_changed("confidence", "ambiguous")
    mapping_changed("extra", HOSTILE_BOOLEAN)
    mapping_changed("identity", None)
    mapping_changed(
        "identity",
        CryptoSpotInstrumentIdentity(
            "alpaca-paper",
            "crypto-btc-usd",
            "BTC",
            "USD",
            "ALPACA",
        ).to_payload(),
    )
    wrong_namespace = deepcopy(evidence_item(recorded_evidence(), 0)["entity_mappings"])
    assert isinstance(wrong_namespace, list)
    mapping = wrong_namespace[0]
    assert isinstance(mapping, dict)
    identity = mapping["identity"]
    assert isinstance(identity, dict)
    identity["catalog_namespace"] = "live-account"
    wrong_namespace_payload = recorded_evidence()
    evidence_item(wrong_namespace_payload, 0)["entity_mappings"] = wrong_namespace
    invalid_payloads.append(wrong_namespace_payload)

    changed_catalog_ids = (None, [True], ["equity-msft"], ["equity-aapl", "equity-aapl"])
    for value in changed_catalog_ids:
        payload = recorded_evidence()
        evidence_item(payload, 0)["entity_catalog_ids"] = value
        invalid_payloads.append(payload)

    assert all(
        _invalid_result(payload)
        == EvidenceSourceDisposition(
            EvidenceCaptureStatus.REFUSED,
            EvidenceRefusalReason.INVALID_RECORDED_INPUT,
            DATA_REGIME,
        )
        for payload in invalid_payloads
    )


def test_recorded_source_canonicalizes_entity_mapping_order() -> None:
    payload = recorded_evidence()
    item = evidence_item(payload, 0)
    mappings = item["entity_mappings"]
    assert isinstance(mappings, list)
    second = {
        "identity": EquityInstrumentIdentity(
            "alpaca-paper",
            "equity-msft",
            "NASDAQ",
        ).to_payload(),
        "confidence": "exact",
    }
    item["entity_mappings"] = [second, deepcopy(mappings[0])]
    item["entity_catalog_ids"] = ["equity-msft", "equity-aapl"]
    content = item["content"]
    assert isinstance(content, dict)
    bars = content["bars"]
    assert isinstance(bars, list)
    bars.append(
        {
            "asset_id": "equity-msft",
            "close": "410.25",
            "timestamp": "2026-08-21T19:00:00.000000+00:00",
        }
    )

    result = RecordedAlpacaEvidenceSource(payload).retrieve(_requests()[0])

    assert isinstance(result, EvidenceCandidate)
    assert tuple(mapping.identity.catalog_id for mapping in result.entity_mappings) == (
        "equity-aapl",
        "equity-msft",
    )
