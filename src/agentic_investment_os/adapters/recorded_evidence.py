"""Validate recorded provider and official-source evidence at the adapter boundary."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, assert_never

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

__all__ = (
    "RecordedAlpacaEvidenceSource",
    "RecordedEvidenceSource",
    "RecordedOfficialEvidenceSource",
)

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
_OFFICIAL_BATCH_FIELDS = frozenset({"schema_version", "record_kind", "data_regime", "items"})
_OFFICIAL_ITEM_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "retrieval_identity",
        "kind",
        "status",
        "source",
        "source_identity",
        "published_at",
        "first_observed_at",
        "entity_mappings",
        "content",
    }
)
_OFFICIAL_MAPPING_FIELDS = frozenset({"identity", "confidence", "mapping_version", "available_at"})
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


@dataclass(frozen=True, slots=True)
class RecordedOfficialEvidenceSource:
    """Translate inert SEC, issuer, and official macro records into evidence results."""

    payload: object

    def retrieve(self, request: EvidenceRetrieval) -> EvidenceSourceResult:
        batch = _parse_official_batch(self.payload)
        if batch is None:
            return _invalid_official_input()
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
        return _parse_official_item(matching[0], request, data_regime)


@dataclass(frozen=True, slots=True)
class RecordedEvidenceSource:
    """Dispatch one configured retrieval to its recorded provider-owned boundary."""

    alpaca_payload: object
    official_payload: object
    data_regime: str

    def retrieve(self, request: EvidenceRetrieval) -> EvidenceSourceResult:
        if request.kind in (EvidenceKind.MARKET, EvidenceKind.NEWS):
            return RecordedAlpacaEvidenceSource(self.alpaca_payload).retrieve(request)
        if request.kind in (
            EvidenceKind.SEC_FILING,
            EvidenceKind.ISSUER_RELEASE,
            EvidenceKind.OFFICIAL_MACRO,
        ):
            if self.official_payload is None:
                return EvidenceSourceDisposition(
                    EvidenceCaptureStatus.UNAVAILABLE,
                    EvidenceRefusalReason.SOURCE_UNAVAILABLE,
                    self.data_regime,
                )
            return RecordedOfficialEvidenceSource(self.official_payload).retrieve(request)
        assert_never(request.kind)  # pragma: no cover - enum is closed.


def _parse_official_batch(
    value: object,
) -> tuple[str, tuple[dict[str, object], ...]] | None:
    root = _exact_mapping(value, _OFFICIAL_BATCH_FIELDS)
    if (
        root is None
        or root["schema_version"] != 1
        or root["record_kind"] != "recorded_official_evidence"
        or not is_data_regime(root["data_regime"])
        or type(root["items"]) is not list
    ):
        return None
    items: list[dict[str, object]] = []
    identities: set[str] = set()
    for raw_item in root["items"]:
        item = _exact_mapping(raw_item, _OFFICIAL_ITEM_FIELDS)
        if item is None:
            return None
        retrieval_identity = item["retrieval_identity"]
        if (
            type(retrieval_identity) is not str
            or not EvidenceRetrieval.is_valid_identity(retrieval_identity)
            or retrieval_identity in identities
        ):
            return None
        identities.add(retrieval_identity)
        items.append(item)
    return root["data_regime"], tuple(items)


def _parse_official_item(  # noqa: PLR0911 - reject source fields independently.
    item: dict[str, object],
    request: EvidenceRetrieval,
    data_regime: str,
) -> EvidenceSourceResult:
    if item["schema_version"] != 1 or item["record_kind"] != "official_evidence_result":
        return _invalid_official_input(data_regime)
    kind = _kind(item["kind"])
    status = _status(item["status"])
    source = _feed(item["source"])
    first_observed_at = _provider_instant(item["first_observed_at"])
    published_at = _provider_instant(item["published_at"])
    if (
        kind
        not in (
            EvidenceKind.SEC_FILING,
            EvidenceKind.ISSUER_RELEASE,
            EvidenceKind.OFFICIAL_MACRO,
        )
        or status is None
        or source is None
        or first_observed_at is None
        or kind is not request.kind
        or source is not request.source
    ):
        return _invalid_official_input(data_regime)
    if status is not EvidenceCaptureStatus.CAPTURED:
        if not _empty_official_non_capture_payload(item):
            return _invalid_official_input(data_regime)
        return EvidenceSourceDisposition(status, _source_reason(status), data_regime)
    if published_at is None or type(item["source_identity"]) is not str:
        return _invalid_official_input(data_regime)
    source_identity = item["source_identity"]
    mappings = _official_entity_mappings(item["entity_mappings"])
    if mappings == "ambiguous":
        return EvidenceSourceDisposition(
            EvidenceCaptureStatus.AMBIGUOUS,
            EvidenceRefusalReason.AMBIGUOUS_ENTITY_MAPPING,
            data_regime,
        )
    if mappings is None or (kind is EvidenceKind.OFFICIAL_MACRO and mappings):
        return _invalid_official_input(data_regime)
    if kind is not EvidenceKind.OFFICIAL_MACRO and len(mappings) != 1:
        return EvidenceSourceDisposition(
            EvidenceCaptureStatus.AMBIGUOUS,
            EvidenceRefusalReason.AMBIGUOUS_ENTITY_MAPPING,
            data_regime,
        )
    content = _canonical_content(
        item["content"],
        kind=kind,
        source_identity=source_identity,
        source_event_at=None,
        entity_mappings=mappings,
    )
    if content is None:
        return _invalid_official_input(data_regime)
    try:
        return EvidenceCandidate.create(
            retrieval_identity=request.retrieval_identity,
            source_identity=source_identity,
            kind=kind,
            source_event_at=None,
            published_at=published_at,
            first_observed_at=first_observed_at,
            data_regime=data_regime,
            feed=source,
            entity_mappings=mappings,
            content=content,
        )
    except InvalidEvidenceError:
        return _invalid_official_input(data_regime)


def _empty_official_non_capture_payload(item: dict[str, object]) -> bool:
    return (
        item["source_identity"] is None
        and item["published_at"] is None
        and item["entity_mappings"] == []
        and item["content"] is None
    )


def _official_entity_mappings(  # noqa: PLR0911 - reject hostile mapping fields locally.
    value: object,
) -> tuple[EvidenceEntityMapping, ...] | Literal["ambiguous"] | None:
    if type(value) is not list:
        return None
    mappings: list[EvidenceEntityMapping] = []
    for raw_mapping in value:
        fields = _exact_mapping(raw_mapping, _OFFICIAL_MAPPING_FIELDS)
        if fields is None:
            return None
        if fields["confidence"] == "ambiguous" and fields["identity"] is None:
            return "ambiguous"
        if fields["confidence"] != "exact":
            return None
        identity = parse_instrument_identity(fields["identity"])
        mapping_version = fields["mapping_version"]
        available_at = _provider_instant(fields["available_at"])
        if (
            type(identity) is not EquityInstrumentIdentity
            or identity.catalog_namespace != "alpaca-paper"
            or type(mapping_version) is not str
            or available_at is None
        ):
            return None
        try:
            mappings.append(
                EvidenceEntityMapping.exact(
                    identity,
                    mapping_version=mapping_version,
                    available_at=available_at,
                )
            )
        except InvalidEvidenceError:
            return None
    if len({mapping.identity for mapping in mappings}) != len(mappings):
        return "ambiguous"
    return tuple(sorted(mappings, key=lambda mapping: mapping.identity.catalog_id))


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
    if kind not in (EvidenceKind.MARKET, EvidenceKind.NEWS):
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
    if feed is not request.source:
        return _invalid_recorded_input(data_regime)
    if status is not EvidenceCaptureStatus.CAPTURED:
        if not _empty_non_capture_payload(item):
            return _invalid_recorded_input(data_regime)
        return EvidenceSourceDisposition(status, _source_reason(status), data_regime)
    mappings = _entity_mappings(
        item["entity_mappings"],
        item["entity_catalog_ids"],
        available_at=first_observed_at,
    )
    if mappings is None:
        return _invalid_recorded_input(data_regime)
    source_identity = _alpaca_source_identity(kind, item["content"], request)
    if source_identity is None:
        return _invalid_recorded_input(data_regime)
    content = _canonical_content(
        item["content"],
        kind=kind,
        source_identity=source_identity,
        source_event_at=source_event_at,
        entity_mappings=mappings,
    )
    if content is None:
        return _invalid_recorded_input(data_regime)
    try:
        return EvidenceCandidate.create(
            retrieval_identity=request.retrieval_identity,
            source_identity=source_identity,
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


def _source_reason(status: EvidenceCaptureStatus) -> EvidenceRefusalReason:
    if status is EvidenceCaptureStatus.UNAVAILABLE:
        return EvidenceRefusalReason.SOURCE_UNAVAILABLE
    if status is EvidenceCaptureStatus.STALE:
        return EvidenceRefusalReason.SOURCE_STALE
    if status is EvidenceCaptureStatus.INVALID:
        return EvidenceRefusalReason.INVALID_RECORDED_INPUT
    if status is EvidenceCaptureStatus.AMBIGUOUS:
        return EvidenceRefusalReason.AMBIGUOUS_ENTITY_MAPPING
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
    *,
    available_at: UtcInstant,
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
        mappings.append(
            EvidenceEntityMapping.exact(
                identity,
                mapping_version="alpaca-paper-catalog-v1",
                available_at=available_at,
            )
        )
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
    source_identity: str,
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
            source_identity,
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


def _invalid_official_input(data_regime: str | None = None) -> EvidenceSourceDisposition:
    return EvidenceSourceDisposition(
        EvidenceCaptureStatus.INVALID,
        EvidenceRefusalReason.INVALID_RECORDED_INPUT,
        data_regime,
    )


def _alpaca_source_identity(
    kind: EvidenceKind,
    content: object,
    request: EvidenceRetrieval,
) -> str | None:
    if kind is EvidenceKind.MARKET:
        return request.retrieval_identity
    if kind is EvidenceKind.NEWS and type(content) is dict:
        identifier = content.get("id")
        return identifier if type(identifier) is str else None
    return None


def _exact_mapping(value: object, fields: frozenset[str]) -> dict[str, object] | None:
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != fields
    ):
        return None
    return value
