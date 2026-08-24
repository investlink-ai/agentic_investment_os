"""Derive approved local attention features from captured evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import TYPE_CHECKING

from agentic_investment_os.domain.attention import (
    AttentionFeature,
    AttentionInputs,
    AttentionRefusalReason,
    AttentionSubjectInput,
)
from agentic_investment_os.domain.identity import MarketSession, canonical_instrument_bytes
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.evidence.capture import (
    CaptureIntent,
    EvidenceCaptureStatus,
    EvidenceKind,
)

if TYPE_CHECKING:
    from agentic_investment_os.domain.universe import UniverseSnapshot, UniverseSubject
    from agentic_investment_os.evidence.capture import EvidenceStoredRecord, EvidenceVault

__all__ = ("BuildAttentionInputs",)

_MINIMUM_MARKET_OBSERVATIONS_FOR_CHANGE = 2


class _MarketChange(StrEnum):
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    INSUFFICIENT = "insufficient"
    CORRUPT = "corrupt"


_UNAVAILABLE_FEATURES = frozenset(
    {
        AttentionFeature.LIQUIDITY_CHANGE,
        AttentionFeature.GAP,
        AttentionFeature.UNUSUAL_VOLUME,
        AttentionFeature.KNOWN_EVENT_PROXIMITY,
        AttentionFeature.EXISTING_BELIEF,
        AttentionFeature.THESIS_EXPIRY,
    }
)


@dataclass(frozen=True, slots=True)
class BuildAttentionInputs:
    """Build typed zero-token features from the exact lifecycle evidence checkpoint."""

    vault: EvidenceVault

    def __call__(  # noqa: PLR0911,PLR0913 - fail closed at each checkpoint seam.
        self,
        *,
        run_id: str,
        cycle: MarketSession,
        universe_snapshot: UniverseSnapshot,
        cutoff: UtcInstant,
        data_regime: str,
        evidence_policy_id: str,
        evidence_artifact_ids: tuple[str, ...],
    ) -> AttentionInputs | AttentionRefusalReason:
        policy = self.vault.load_policy(evidence_policy_id)
        if policy.policy_id != evidence_policy_id or policy.data_regime != data_regime:
            return AttentionRefusalReason.CONTRADICTORY_EVIDENCE
        outcomes = []
        for request in policy.requests:
            outcome = self.vault.load_outcome(
                CaptureIntent.create(
                    run_id=run_id,
                    universe_snapshot_id=universe_snapshot.snapshot_id,
                    cutoff=cutoff,
                    data_regime=data_regime,
                    request=request,
                )
            )
            if outcome is None:
                return AttentionRefusalReason.CORRUPT_EVIDENCE
            outcomes.append((request, outcome))
        captured_ids = tuple(
            sorted(
                outcome.artifact.artifact_id
                for _, outcome in outcomes
                if outcome.status is EvidenceCaptureStatus.CAPTURED and outcome.artifact is not None
            )
        )
        if captured_ids != evidence_artifact_ids:
            return AttentionRefusalReason.CONTRADICTORY_EVIDENCE
        unavailable_kinds = frozenset(
            request.kind
            for request, outcome in outcomes
            if outcome.status is not EvidenceCaptureStatus.CAPTURED
        )
        records = self.vault.stored_records_for_artifacts(evidence_artifact_ids)
        by_id = {record.artifact.artifact_id: record for record in records}
        if any(artifact_id not in by_id for artifact_id in evidence_artifact_ids):
            return AttentionRefusalReason.MISSING_EVIDENCE
        selected = tuple(by_id[artifact_id] for artifact_id in evidence_artifact_ids)
        if any(
            record.artifact.data_regime != data_regime
            or record.artifact.available_at.value > cutoff.value
            for record in selected
        ):
            return AttentionRefusalReason.CONTRADICTORY_EVIDENCE
        if (
            universe_snapshot.run_id != run_id
            or universe_snapshot.cycle != cycle
            or universe_snapshot.inputs.evidence_cutoff != cutoff
            or universe_snapshot.inputs.data_regime != data_regime
        ):
            return AttentionRefusalReason.CONTRADICTORY_EVIDENCE
        subjects: list[AttentionSubjectInput] = []
        for subject in sorted(
            universe_snapshot.attention_subjects,
            key=lambda item: canonical_instrument_bytes(item.identity),
        ):
            subject_input = _subject_input(subject, selected, unavailable_kinds)
            if isinstance(subject_input, AttentionRefusalReason):
                return subject_input
            subjects.append(subject_input)
        return AttentionInputs(
            run_id=run_id,
            cycle=cycle,
            universe_snapshot_id=universe_snapshot.snapshot_id,
            cutoff=cutoff,
            data_regime=data_regime,
            evidence_policy_id=evidence_policy_id,
            evidence_artifact_ids=evidence_artifact_ids,
            subjects=tuple(subjects),
        )


def _subject_input(
    subject: UniverseSubject,
    records: tuple[EvidenceStoredRecord, ...],
    unavailable_kinds: frozenset[EvidenceKind],
) -> AttentionSubjectInput | AttentionRefusalReason:
    identity_key = canonical_instrument_bytes(subject.identity)
    relevant = tuple(
        record
        for record in records
        if any(
            canonical_instrument_bytes(mapping.identity) == identity_key
            for mapping in record.artifact.entity_mappings
        )
    )
    observed: set[AttentionFeature] = set()
    missing = set(_UNAVAILABLE_FEATURES)
    if subject.is_position:
        observed.add(AttentionFeature.CURRENT_HOLDING)
    if relevant:
        observed.add(AttentionFeature.FRESHNESS)
    else:
        missing.add(AttentionFeature.FRESHNESS)
    market_records = tuple(
        record for record in relevant if record.artifact.kind is EvidenceKind.MARKET
    )
    market_change = _has_market_change(subject, market_records)
    if market_change is _MarketChange.CORRUPT:
        return AttentionRefusalReason.CORRUPT_EVIDENCE
    if market_change is _MarketChange.INSUFFICIENT:
        missing.add(AttentionFeature.PRICE_CHANGE)
    elif market_change is _MarketChange.CHANGED:
        observed.add(AttentionFeature.PRICE_CHANGE)
    if any(
        record.artifact.kind in (EvidenceKind.NEWS, EvidenceKind.ISSUER_RELEASE)
        for record in relevant
    ):
        observed.add(AttentionFeature.NEWS_ARRIVAL)
    if any(record.artifact.kind is EvidenceKind.SEC_FILING for record in relevant):
        observed.add(AttentionFeature.FILING_ARRIVAL)
    if (
        EvidenceKind.SEC_FILING in unavailable_kinds
        and AttentionFeature.FILING_ARRIVAL not in observed
    ):
        missing.add(AttentionFeature.FILING_ARRIVAL)
    if (
        unavailable_kinds
        & {
            EvidenceKind.NEWS,
            EvidenceKind.ISSUER_RELEASE,
        }
        and AttentionFeature.NEWS_ARRIVAL not in observed
    ):
        missing.add(AttentionFeature.NEWS_ARRIVAL)
    return AttentionSubjectInput(
        subject=subject,
        observed_features=tuple(sorted(observed, key=lambda feature: feature.value)),
        missing_features=tuple(sorted(missing, key=lambda feature: feature.value)),
        evidence_artifact_ids=tuple(sorted(record.artifact.artifact_id for record in relevant)),
    )


def _has_market_change(  # noqa: PLR0911 - distinguish each corrupt market shape.
    subject: UniverseSubject,
    records: tuple[EvidenceStoredRecord, ...],
) -> _MarketChange:
    observations: list[tuple[UtcInstant, Decimal]] = []
    for record in records:
        parsed = _parse_json(record.content)
        if parsed is None:
            return _MarketChange.CORRUPT
        bars = parsed.get("bars")
        if type(bars) is not list:
            return _MarketChange.CORRUPT
        for item in bars:
            bar = _string_mapping(item, {"asset_id", "close", "timestamp"})
            if bar is None:
                return _MarketChange.CORRUPT
            if bar["asset_id"] != subject.identity.catalog_id:
                continue
            try:
                timestamp = UtcInstant.parse(bar["timestamp"])
                close = Decimal(bar["close"])
            except (InvalidOperation, InvalidUtcInstantError):
                return _MarketChange.CORRUPT
            if not close.is_finite():
                return _MarketChange.CORRUPT
            observations.append((timestamp, close))
    if len(observations) < _MINIMUM_MARKET_OBSERVATIONS_FOR_CHANGE:
        return _MarketChange.INSUFFICIENT
    ordered = sorted(observations, key=lambda item: item[0].value)
    return _MarketChange.CHANGED if ordered[0][1] != ordered[-1][1] else _MarketChange.UNCHANGED


def _parse_json(content: bytes) -> dict[str, object] | None:
    try:
        value: object = json.loads(content)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if type(value) is not dict or any(type(key) is not str for key in value):
        return None
    return value


def _string_mapping(value: object, fields: set[str]) -> dict[str, str] | None:
    if (
        type(value) is not dict
        or set(value) != fields
        or any(type(key) is not str or type(item) is not str for key, item in value.items())
    ):
        return None
    return value
