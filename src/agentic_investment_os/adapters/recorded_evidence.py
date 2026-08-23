"""Validate recorded Alpaca market and news representations at the provider boundary."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import assert_never

from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    parse_instrument_identity,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.domain.universe import is_data_regime
from agentic_investment_os.evidence.capture import (
    EvidenceCandidate,
    EvidenceCaptureStatus,
    EvidenceEntityMapping,
    EvidenceFeed,
    EvidenceKind,
    EvidenceRefusalReason,
    EvidenceRetrieval,
    EvidenceSourceDisposition,
    EvidenceSourceResult,
    InvalidEvidenceError,
    is_normalized_evidence_content,
)

__all__ = ("RecordedAlpacaEvidenceSource",)

_BATCH_FIELDS = frozenset({"schema_version", "record_kind", "data_regime", "items"})
_ITEM_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "retrieval_identity",
        "kind",
        "status",
        "feed",
        "entitlement",
        "source_event_at",
        "published_at",
        "first_observed_at",
        "entity_mappings",
        "entity_catalog_ids",
        "content",
    }
)
_MAPPING_FIELDS = frozenset({"identity", "confidence"})
_PROVIDER_INSTANT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})\Z"
)
_ENTITLEMENT = "alpaca-basic"
_CAPTURED_CONTENT_REQUIRED = "captured source result requires content"


@dataclass(frozen=True, slots=True)
class RecordedAlpacaEvidenceSource:
    """Translate one inert recorded batch into owner-defined evidence results."""

    payload: object

    def retrieve(self, request: EvidenceRetrieval) -> EvidenceSourceResult:
        batch = _parse_batch(self.payload)
        if batch is None:
            return _invalid_recorded_input()
        data_regime, items = batch
        matching = tuple(
            item for item in items if item["retrieval_identity"] == request.retrieval_identity
        )
        if not matching:
            return EvidenceSourceDisposition(
                EvidenceCaptureStatus.UNAVAILABLE,
                EvidenceRefusalReason.SOURCE_UNAVAILABLE,
                data_regime,
            )
        return _parse_item(matching[0], request, data_regime)


def _parse_batch(  # noqa: PLR0911 - reject recorded batch fields independently.
    value: object,
) -> tuple[str, tuple[dict[str, object], ...]] | None:
    root = _exact_mapping(value, _BATCH_FIELDS)
    if root is None:
        return None
    if root["schema_version"] != 1:
        return None
    if root["record_kind"] != "recorded_alpaca_evidence":
        return None
    if not is_data_regime(root["data_regime"]):
        return None
    if type(root["items"]) is not list:
        return None
    items: list[dict[str, object]] = []
    identities: set[str] = set()
    for raw_item in root["items"]:
        item = _exact_mapping(raw_item, _ITEM_FIELDS)
        if item is None:
            return None
        retrieval_identity = item["retrieval_identity"]
        if type(retrieval_identity) is not str:
            return None
        if not EvidenceRetrieval.is_valid_identity(retrieval_identity):
            return None
        if retrieval_identity in identities:
            return None
        identities.add(retrieval_identity)
        items.append(item)
    return root["data_regime"], tuple(items)


def _parse_item(  # noqa: PLR0911, PLR0912 - reject provider components independently.
    item: dict[str, object],
    request: EvidenceRetrieval,
    data_regime: str,
) -> EvidenceSourceResult:
    if item["schema_version"] != 1:
        return _invalid_recorded_input(data_regime)
    if item["record_kind"] != "alpaca_evidence_result":
        return _invalid_recorded_input(data_regime)
    kind = _kind(item["kind"])
    status = _status(item["status"])
    feed = _feed(item["feed"])
    first_observed_at = _provider_instant(item["first_observed_at"])
    valid_source_event, source_event_at = _optional_provider_instant(item["source_event_at"])
    valid_publication, published_at = _optional_provider_instant(item["published_at"])
    if kind is None:
        return _invalid_recorded_input(data_regime)
    if status is None:
        return _invalid_recorded_input(data_regime)
    if feed is None:
        return _invalid_recorded_input(data_regime)
    if first_observed_at is None:
        return _invalid_recorded_input(data_regime)
    if not valid_source_event:
        return _invalid_recorded_input(data_regime)
    if not valid_publication:
        return _invalid_recorded_input(data_regime)
    if item["entitlement"] != _ENTITLEMENT:
        return _invalid_recorded_input(data_regime)
    if kind is not request.kind:
        return _invalid_recorded_input(data_regime)
    if feed is not _feed_for(kind):
        return _invalid_recorded_input(data_regime)
    if status is not EvidenceCaptureStatus.CAPTURED:
        if not _empty_non_capture_payload(item):
            return _invalid_recorded_input(data_regime)
        return EvidenceSourceDisposition(status, _source_reason(status), data_regime)
    mappings = _entity_mappings(item["entity_mappings"], item["entity_catalog_ids"])
    if mappings is None:
        return _invalid_recorded_input(data_regime)
    content = _canonical_content(
        item["content"],
        kind=kind,
        source_event_at=source_event_at,
        entity_mappings=mappings,
    )
    if content is None:
        return _invalid_recorded_input(data_regime)
    try:
        return EvidenceCandidate.create(
            retrieval_identity=request.retrieval_identity,
            kind=kind,
            source_event_at=source_event_at,
            published_at=published_at,
            first_observed_at=first_observed_at,
            data_regime=data_regime,
            feed=feed,
            entity_mappings=mappings,
            content=content,
        )
    except InvalidEvidenceError:
        return _invalid_recorded_input(data_regime)


def _kind(value: object) -> EvidenceKind | None:
    if type(value) is not str:
        return None
    try:
        return EvidenceKind(value)
    except ValueError:
        return None


def _status(value: object) -> EvidenceCaptureStatus | None:
    if type(value) is not str:
        return None
    try:
        return EvidenceCaptureStatus(value)
    except ValueError:
        return None


def _feed(value: object) -> EvidenceFeed | None:
    if type(value) is not str:
        return None
    try:
        return EvidenceFeed(value)
    except ValueError:
        return None


def _feed_for(kind: EvidenceKind) -> EvidenceFeed:
    if kind is EvidenceKind.MARKET:
        return EvidenceFeed.IEX
    if kind is EvidenceKind.NEWS:
        return EvidenceFeed.ALPACA_NEWS
    assert_never(kind)  # pragma: no cover - enum is closed.


def _source_reason(status: EvidenceCaptureStatus) -> EvidenceRefusalReason:
    if status is EvidenceCaptureStatus.UNAVAILABLE:
        return EvidenceRefusalReason.SOURCE_UNAVAILABLE
    if status is EvidenceCaptureStatus.STALE:
        return EvidenceRefusalReason.SOURCE_STALE
    if status is EvidenceCaptureStatus.REFUSED:
        return EvidenceRefusalReason.SOURCE_REFUSED
    if status is EvidenceCaptureStatus.CAPTURED:  # pragma: no cover - caller excludes captured.
        raise InvalidEvidenceError(
            _CAPTURED_CONTENT_REQUIRED
        )  # pragma: no cover  # caller excludes captured.
    assert_never(status)  # pragma: no cover - enum is closed.


def _empty_non_capture_payload(item: dict[str, object]) -> bool:
    if item["source_event_at"] is not None:
        return False
    if item["published_at"] is not None:
        return False
    if item["entity_mappings"] != []:
        return False
    if item["entity_catalog_ids"] != []:
        return False
    return item["content"] is None


def _entity_mappings(  # noqa: PLR0911 - reject mapping components independently.
    value: object,
    catalog_ids_value: object,
) -> tuple[EvidenceEntityMapping, ...] | None:
    if type(value) is not list:
        return None
    if not value:
        return None
    if type(catalog_ids_value) is not list:
        return None
    mappings: list[EvidenceEntityMapping] = []
    for raw_mapping in value:
        fields = _exact_mapping(raw_mapping, _MAPPING_FIELDS)
        if fields is None:
            return None
        if fields["confidence"] != "exact":
            return None
        identity = parse_instrument_identity(fields["identity"])
        if type(identity) is not EquityInstrumentIdentity:
            return None
        if identity.catalog_namespace != "alpaca-paper":
            return None
        mappings.append(EvidenceEntityMapping.exact(identity))
    expected_ids = sorted(mapping.identity.catalog_id for mapping in mappings)
    # Catalog equality and uniqueness below also reject repeated identities; keep this explicit
    # boundary invariant for local clarity.
    if len({mapping.identity for mapping in mappings}) != len(mappings):
        return None
    if any(type(value) is not str for value in catalog_ids_value):
        return None
    if len(set(catalog_ids_value)) != len(catalog_ids_value):
        return None
    if sorted(catalog_ids_value) != expected_ids:
        return None
    return tuple(sorted(mappings, key=lambda mapping: mapping.identity.catalog_id))


def _canonical_content(
    value: object,
    *,
    kind: EvidenceKind,
    source_event_at: UtcInstant | None,
    entity_mappings: tuple[EvidenceEntityMapping, ...],
) -> bytes | None:
    try:
        if not _is_json_value(value):
            return None
        content = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        if not is_normalized_evidence_content(
            kind,
            content,
            source_event_at,
            entity_mappings,
        ):
            return None
        return content
    except (RecursionError, ValueError):
        return None


def _is_json_value(value: object) -> bool:
    if value is None or type(value) in (bool, int, str):
        return True
    if type(value) is list:
        return all(_is_json_value(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _is_json_value(item) for key, item in value.items())
    return False


def _optional_provider_instant(value: object) -> tuple[bool, UtcInstant | None]:
    if value is None:
        return True, None
    parsed = _provider_instant(value)
    return parsed is not None, parsed


def _provider_instant(value: object) -> UtcInstant | None:
    if type(value) is not str or _PROVIDER_INSTANT.fullmatch(value) is None:
        return None
    try:
        return UtcInstant.from_datetime(datetime.fromisoformat(value))
    except (InvalidUtcInstantError, ValueError):
        return None


def _invalid_recorded_input(data_regime: str | None = None) -> EvidenceSourceDisposition:
    return EvidenceSourceDisposition(
        EvidenceCaptureStatus.REFUSED,
        EvidenceRefusalReason.INVALID_RECORDED_INPUT,
        data_regime,
    )


def _exact_mapping(value: object, fields: frozenset[str]) -> dict[str, object] | None:
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != fields
    ):
        return None
    return value
