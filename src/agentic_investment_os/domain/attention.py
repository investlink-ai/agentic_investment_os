"""Select bounded research attention from pinned local inputs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Self, TypeGuard, assert_never

from agentic_investment_os.domain.identity import (
    InstrumentIdentity,
    MarketSession,
    canonical_instrument_bytes,
    parse_decision_cycle_identity,
    parse_instrument_identity,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.domain.universe import (
    PositionDisposition,
    UniverseExclusionReason,
    UniverseSubject,
    is_data_regime,
)

__all__ = (
    "AdapterQuotaDisposition",
    "AttentionArtifact",
    "AttentionDisposition",
    "AttentionFeature",
    "AttentionInputs",
    "AttentionNoActionReason",
    "AttentionPolicy",
    "AttentionRefusalReason",
    "AttentionState",
    "AttentionSubjectInput",
    "AttentionTransitionExitReason",
    "CandidateCard",
    "CandidateDisposition",
    "CandidateReason",
    "DossierRequest",
    "DossierSelectionKind",
    "HoldingRefresh",
    "HoldingRefreshDisposition",
    "InvalidAttentionError",
    "ResourceAccounting",
    "attention_history_fingerprint",
    "parse_attention_artifact",
    "select_attention",
    "validate_attention_history",
)

_SCHEMA_VERSION = 1
_MAXIMUM_CANDIDATE_CARDS = 20
_MAXIMUM_NEW_DOSSIERS = 5
_MAXIMUM_WEEKLY_DOSSIERS = 25
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INVALID_POLICY = "invalid attention policy"
_INVALID_INPUTS = "invalid attention inputs"
_INVALID_ARTIFACT = "invalid attention artifact"


class InvalidAttentionError(ValueError):
    """Report attention state that cannot preserve deterministic policy."""


class AttentionFeature(StrEnum):
    """Name the complete approved local attention feature set."""

    FRESHNESS = "freshness"
    PRICE_CHANGE = "price_change"
    LIQUIDITY_CHANGE = "liquidity_change"
    GAP = "gap"
    UNUSUAL_VOLUME = "unusual_volume"
    NEWS_ARRIVAL = "news_arrival"
    FILING_ARRIVAL = "filing_arrival"
    KNOWN_EVENT_PROXIMITY = "known_event_proximity"
    EXISTING_BELIEF = "existing_belief"
    THESIS_EXPIRY = "thesis_expiry"
    CURRENT_HOLDING = "current_holding"


class AttentionState(StrEnum):
    """Represent one subject's position in the attention funnel."""

    OBSERVED = "Observed"
    WATCH = "Watch"
    CANDIDATE = "Candidate"
    DOSSIER = "Dossier"
    THESIS = "Thesis"
    PORTFOLIO = "Portfolio"
    REJECTED = "Rejected"
    REFUTED = "Refuted"
    DORMANT = "Dormant"
    ARCHIVED = "Archived"


class AttentionTransitionExitReason(StrEnum):
    """Preserve why a subject leaves the active attention path."""

    INELIGIBLE = "ineligible"
    REFUTED_BY_EVIDENCE = "refuted_by_evidence"
    DORMANT_WITHOUT_NEW_EVIDENCE = "dormant_without_new_evidence"
    ARCHIVED_AS_SUPERSEDED = "archived_as_superseded"


class CandidateDisposition(StrEnum):
    """Explain whether one card advances or consumes research capacity."""

    ADVANCED_ONE_STATE = "advanced_one_state"
    NEW_DOSSIER_REQUESTED = "new_dossier_requested"
    DEFERRED_AT_CAPACITY = "deferred_at_capacity"
    ALREADY_IN_RESEARCH = "already_in_research"
    EXITED_ACTIVE_PATH = "exited_active_path"


class CandidateReason(StrEnum):
    """Give bounded, reconstructable reasons without producing an alpha score."""

    FILING_ARRIVAL = "filing_arrival"
    NEWS_ARRIVAL = "news_arrival"
    THESIS_EXPIRY = "thesis_expiry"
    KNOWN_EVENT_PROXIMITY = "known_event_proximity"
    EXISTING_BELIEF = "existing_belief"
    UNUSUAL_VOLUME = "unusual_volume"
    GAP = "gap"
    LIQUIDITY_CHANGE = "liquidity_change"
    PRICE_CHANGE = "price_change"
    FRESH_EVIDENCE = "fresh_evidence"
    EXPLORATION_FUNNEL = "exploration_funnel"
    FUNNEL_PROGRESSION = "funnel_progression"
    DOSSIER_CAPACITY = "dossier_capacity"
    ALREADY_IN_RESEARCH = "already_in_research"
    TERMINAL_STATE = "terminal_state"


class DossierSelectionKind(StrEnum):
    """Distinguish ordinary research capacity from reserved exploration."""

    PRIORITY = "priority"
    EXPLORATION = "exploration"


class HoldingRefreshDisposition(StrEnum):
    """Keep every due holding explicit outside the new-Dossier cap."""

    REQUIRED = "required"
    PORTFOLIO_MISMATCH = "portfolio_mismatch"


class AdapterQuotaDisposition(StrEnum):
    """State the local scan's adapter quota effect precisely."""

    NOT_CONSULTED = "not_consulted"


class AttentionDisposition(StrEnum):
    """Describe whether the scan published attention or a durable no-action result."""

    SELECTED = "selected"
    NO_ACTION = "no_action"


class AttentionNoActionReason(StrEnum):
    """Bound successful scans that have no eligible attention output."""

    NO_ELIGIBLE_ATTENTION = "no_eligible_attention"


class AttentionRefusalReason(StrEnum):
    """Classify evidence or history that cannot safely produce attention."""

    MISSING_EVIDENCE = "missing_evidence"
    STALE_EVIDENCE = "stale_evidence"
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"
    CORRUPT_EVIDENCE = "corrupt_evidence"


@dataclass(frozen=True, slots=True)
class AttentionPolicy:
    """Carry the complete versioned bounds for local attention selection."""

    candidate_card_limit: int
    new_dossier_limit: int
    weekly_dossier_budget: int
    weekly_exploration_budget: int
    exploration_seed: str

    @classmethod
    def parse(cls, value: object) -> Self | None:
        """Validate one exact policy representation without applying defaults."""
        root = _exact_mapping(
            value,
            {
                "schema_version",
                "policy_type",
                "candidate_card_limit",
                "new_dossier_limit",
                "weekly_dossier_budget",
                "weekly_exploration_budget",
                "exploration_seed",
            },
        )
        if root is None:
            return None
        if (
            type(root["schema_version"]) is not int
            or root["schema_version"] != _SCHEMA_VERSION
            or type(root["policy_type"]) is not str
            or root["policy_type"] != "v0_attention"
        ):
            return None
        candidate_limit = root["candidate_card_limit"]
        dossier_limit = root["new_dossier_limit"]
        weekly_budget = root["weekly_dossier_budget"]
        exploration_budget = root["weekly_exploration_budget"]
        seed = root["exploration_seed"]
        parsed_values = _parse_policy_values(
            candidate_limit,
            dossier_limit,
            weekly_budget,
            exploration_budget,
            seed,
        )
        if parsed_values is None:
            return None
        (
            candidate_limit,
            dossier_limit,
            weekly_budget,
            exploration_budget,
            seed,
        ) = parsed_values
        return cls(
            candidate_limit,
            dossier_limit,
            weekly_budget,
            exploration_budget,
            seed,
        )

    @property
    def policy_id(self) -> str:
        return _content_hash(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "policy_type": "v0_attention",
            "candidate_card_limit": self.candidate_card_limit,
            "new_dossier_limit": self.new_dossier_limit,
            "weekly_dossier_budget": self.weekly_dossier_budget,
            "weekly_exploration_budget": self.weekly_exploration_budget,
            "exploration_seed": self.exploration_seed,
        }

    def __post_init__(self) -> None:
        if not _policy_values_are_valid(
            self.candidate_card_limit,
            self.new_dossier_limit,
            self.weekly_dossier_budget,
            self.weekly_exploration_budget,
            self.exploration_seed,
        ):
            raise InvalidAttentionError(_INVALID_POLICY)


@dataclass(frozen=True, slots=True)
class AttentionSubjectInput:
    """Bind approved feature observations to one canonical universe subject."""

    subject: UniverseSubject
    observed_features: tuple[AttentionFeature, ...]
    missing_features: tuple[AttentionFeature, ...]
    evidence_artifact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        observed = self.observed_features
        missing = self.missing_features
        if (
            type(self.subject) is not UniverseSubject
            or type(observed) is not tuple
            or type(missing) is not tuple
            or any(type(feature) is not AttentionFeature for feature in (*observed, *missing))
            or len(set(observed)) != len(observed)
            or len(set(missing)) != len(missing)
            or set(observed) & set(missing)
            or (AttentionFeature.CURRENT_HOLDING in observed) != self.subject.is_position
            or type(self.evidence_artifact_ids) is not tuple
            or tuple(sorted(set(self.evidence_artifact_ids))) != self.evidence_artifact_ids
            or any(not _is_sha256(item) for item in self.evidence_artifact_ids)
        ):
            raise InvalidAttentionError(_INVALID_INPUTS)

    def to_payload(self) -> dict[str, object]:
        return {
            "subject": {
                "identity": self.subject.identity.to_payload(),
                "is_position": self.subject.is_position,
                "eligible_for_new_entry": self.subject.eligible_for_new_entry,
                "position_disposition": self.subject.position_disposition.value,
                "exclusion_reasons": [reason.value for reason in self.subject.exclusion_reasons],
            },
            "observed_features": sorted(feature.value for feature in self.observed_features),
            "missing_features": sorted(feature.value for feature in self.missing_features),
            "evidence_artifact_ids": list(self.evidence_artifact_ids),
        }


@dataclass(frozen=True, slots=True)
class AttentionInputs:
    """Pin the complete local feature input consumed by one attention scan."""

    run_id: str
    cycle: MarketSession
    universe_snapshot_id: str
    cutoff: UtcInstant
    data_regime: str
    evidence_policy_id: str
    evidence_artifact_ids: tuple[str, ...]
    subjects: tuple[AttentionSubjectInput, ...]

    def __post_init__(self) -> None:
        if (
            not _is_sha256(self.run_id)
            or type(self.cycle) is not MarketSession
            or not _is_sha256(self.universe_snapshot_id)
            or type(self.cutoff) is not UtcInstant
            or not is_data_regime(self.data_regime)
            or not _is_sha256(self.evidence_policy_id)
            or type(self.evidence_artifact_ids) is not tuple
            or tuple(sorted(set(self.evidence_artifact_ids))) != self.evidence_artifact_ids
            or not self.evidence_artifact_ids
            or any(not _is_sha256(item) for item in self.evidence_artifact_ids)
            or type(self.subjects) is not tuple
            or any(type(subject) is not AttentionSubjectInput for subject in self.subjects)
        ):
            raise InvalidAttentionError(_INVALID_INPUTS)
        try:
            self.cutoff.isoformat()
        except InvalidUtcInstantError as error:
            raise InvalidAttentionError(_INVALID_INPUTS) from error
        identities = tuple(
            canonical_instrument_bytes(subject.subject.identity) for subject in self.subjects
        )
        if len(set(identities)) != len(identities) or any(
            not set(subject.evidence_artifact_ids) <= set(self.evidence_artifact_ids)
            for subject in self.subjects
        ):
            raise InvalidAttentionError(_INVALID_INPUTS)

    @property
    def fingerprint(self) -> str:
        return _content_hash(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        ordered = sorted(
            self.subjects,
            key=lambda subject: canonical_instrument_bytes(subject.subject.identity),
        )
        return {
            "run_id": self.run_id,
            "cycle": self.cycle.to_payload(),
            "universe_snapshot_id": self.universe_snapshot_id,
            "cutoff": self.cutoff.isoformat(),
            "data_regime": self.data_regime,
            "evidence_policy_id": self.evidence_policy_id,
            "evidence_artifact_ids": list(self.evidence_artifact_ids),
            "subjects": [subject.to_payload() for subject in ordered],
        }


@dataclass(frozen=True, slots=True)
class CandidateCard:
    """Explain one bounded funnel transition and its research-capacity disposition."""

    card_id: str
    identity: InstrumentIdentity
    previous_state: AttentionState
    next_state: AttentionState
    disposition: CandidateDisposition
    observed_features: tuple[AttentionFeature, ...]
    reasons: tuple[CandidateReason, ...]
    evidence_artifact_ids: tuple[str, ...]
    missing_features: tuple[AttentionFeature, ...]
    exit_reason: AttentionTransitionExitReason | None = None

    @classmethod
    def create(  # noqa: PLR0913 - card identity binds every reconstructable reason.
        cls,
        *,
        run_id: str,
        identity: InstrumentIdentity,
        previous_state: AttentionState,
        next_state: AttentionState,
        disposition: CandidateDisposition,
        observed_features: tuple[AttentionFeature, ...],
        reasons: tuple[CandidateReason, ...],
        evidence_artifact_ids: tuple[str, ...],
        missing_features: tuple[AttentionFeature, ...],
        exit_reason: AttentionTransitionExitReason | None = None,
    ) -> Self:
        material = _candidate_material(
            run_id=run_id,
            identity=identity,
            previous_state=previous_state,
            next_state=next_state,
            disposition=disposition,
            observed_features=observed_features,
            reasons=reasons,
            evidence_artifact_ids=evidence_artifact_ids,
            missing_features=missing_features,
            exit_reason=exit_reason,
        )
        return cls(
            card_id=_content_hash(material),
            identity=identity,
            previous_state=previous_state,
            next_state=next_state,
            disposition=disposition,
            observed_features=tuple(
                sorted(set(observed_features), key=lambda feature: feature.value)
            ),
            reasons=tuple(sorted(set(reasons), key=lambda reason: reason.value)),
            evidence_artifact_ids=evidence_artifact_ids,
            missing_features=tuple(
                sorted(set(missing_features), key=lambda feature: feature.value)
            ),
            exit_reason=exit_reason,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "card_id": self.card_id,
            "identity": self.identity.to_payload(),
            "previous_state": self.previous_state.value,
            "next_state": self.next_state.value,
            "disposition": self.disposition.value,
            "observed_features": [feature.value for feature in self.observed_features],
            "reasons": [reason.value for reason in self.reasons],
            "evidence_artifact_ids": list(self.evidence_artifact_ids),
            "missing_features": [feature.value for feature in self.missing_features],
            "exit_reason": None if self.exit_reason is None else self.exit_reason.value,
        }

    def __post_init__(self) -> None:
        terminal = self.next_state in _TERMINAL_STATES
        allowed_active = self.next_state == self.previous_state or (
            _NEXT_ACTIVE_STATE.get(self.previous_state) is self.next_state
        )
        valid_disposition = (
            (
                self.disposition is CandidateDisposition.NEW_DOSSIER_REQUESTED
                and self.previous_state is AttentionState.CANDIDATE
                and self.next_state is AttentionState.DOSSIER
            )
            or (
                self.disposition is CandidateDisposition.ADVANCED_ONE_STATE
                and self.previous_state in (AttentionState.OBSERVED, AttentionState.WATCH)
                and _NEXT_ACTIVE_STATE[self.previous_state] is self.next_state
            )
            or (
                self.disposition is CandidateDisposition.DEFERRED_AT_CAPACITY
                and self.previous_state is AttentionState.CANDIDATE
                and self.next_state is self.previous_state
            )
            or (
                self.disposition is CandidateDisposition.ALREADY_IN_RESEARCH
                and self.previous_state
                in (
                    AttentionState.DOSSIER,
                    AttentionState.THESIS,
                    AttentionState.PORTFOLIO,
                )
                and self.next_state is self.previous_state
            )
            or (
                self.disposition is CandidateDisposition.EXITED_ACTIVE_PATH
                and self.previous_state not in _TERMINAL_STATES
                and terminal
                and self.exit_reason is _exit_reason_for(self.next_state)
                and CandidateReason.TERMINAL_STATE in self.reasons
            )
        )
        feature_reasons = frozenset(_feature_reasons(self.observed_features))
        workflow_reasons = set(self.reasons) - feature_reasons
        expected_workflow_reasons: tuple[frozenset[CandidateReason], ...] = ()
        if self.disposition is CandidateDisposition.NEW_DOSSIER_REQUESTED:
            expected_workflow_reasons = (
                frozenset(),
                frozenset((CandidateReason.EXPLORATION_FUNNEL,)),
            )
        elif self.disposition is CandidateDisposition.ADVANCED_ONE_STATE:
            expected_workflow_reasons = (
                frozenset((CandidateReason.FUNNEL_PROGRESSION,)),
                frozenset(
                    (
                        CandidateReason.FUNNEL_PROGRESSION,
                        CandidateReason.EXPLORATION_FUNNEL,
                    )
                ),
            )
        elif self.disposition is CandidateDisposition.DEFERRED_AT_CAPACITY:
            expected_workflow_reasons = (frozenset((CandidateReason.DOSSIER_CAPACITY,)),)
        elif self.disposition is CandidateDisposition.ALREADY_IN_RESEARCH:
            expected_workflow_reasons = (frozenset((CandidateReason.ALREADY_IN_RESEARCH,)),)
        elif self.disposition is CandidateDisposition.EXITED_ACTIVE_PATH:
            expected_workflow_reasons = (frozenset((CandidateReason.TERMINAL_STATE,)),)
        if (
            not _is_sha256(self.card_id)
            or type(self.previous_state) is not AttentionState
            or type(self.next_state) is not AttentionState
            or type(self.disposition) is not CandidateDisposition
            or type(self.observed_features) is not tuple
            or any(type(feature) is not AttentionFeature for feature in self.observed_features)
            or tuple(sorted(set(self.observed_features), key=lambda feature: feature.value))
            != self.observed_features
            or AttentionFeature.CURRENT_HOLDING in self.observed_features
            or not self.reasons
            or any(type(reason) is not CandidateReason for reason in self.reasons)
            or tuple(sorted(set(self.reasons), key=lambda reason: reason.value)) != self.reasons
            or type(self.evidence_artifact_ids) is not tuple
            or tuple(sorted(set(self.evidence_artifact_ids))) != self.evidence_artifact_ids
            or any(not _is_sha256(item) for item in self.evidence_artifact_ids)
            or type(self.missing_features) is not tuple
            or any(type(feature) is not AttentionFeature for feature in self.missing_features)
            or tuple(sorted(set(self.missing_features), key=lambda feature: feature.value))
            != self.missing_features
            or (
                self.exit_reason is not None
                and type(self.exit_reason) is not AttentionTransitionExitReason
            )
            or (terminal != (self.exit_reason is not None))
            or (not terminal and not allowed_active)
            or not valid_disposition
            or not feature_reasons <= set(self.reasons)
            or frozenset(workflow_reasons) not in expected_workflow_reasons
        ):
            raise InvalidAttentionError(_INVALID_ARTIFACT)


@dataclass(frozen=True, slots=True)
class DossierRequest:
    """Request bounded downstream research without carrying stance or execution authority."""

    request_id: str
    candidate_card_id: str
    identity: InstrumentIdentity
    selection_kind: DossierSelectionKind

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        card: CandidateCard,
        selection_kind: DossierSelectionKind,
    ) -> Self:
        material = {
            "identity_kind": "dossier_request",
            "schema_version": _SCHEMA_VERSION,
            "run_id": run_id,
            "candidate_card_id": card.card_id,
            "identity": card.identity.to_payload(),
            "selection_kind": selection_kind.value,
        }
        return cls(_content_hash(material), card.card_id, card.identity, selection_kind)

    def to_payload(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "candidate_card_id": self.candidate_card_id,
            "identity": self.identity.to_payload(),
            "selection_kind": self.selection_kind.value,
        }

    def __post_init__(self) -> None:
        if (
            not _is_sha256(self.request_id)
            or not _is_sha256(self.candidate_card_id)
            or type(self.selection_kind) is not DossierSelectionKind
        ):
            raise InvalidAttentionError(_INVALID_ARTIFACT)


@dataclass(frozen=True, slots=True)
class HoldingRefresh:
    """Record one due holding's explicit refresh disposition outside new research capacity."""

    refresh_id: str
    identity: InstrumentIdentity
    disposition: HoldingRefreshDisposition
    exclusion_reasons: tuple[UniverseExclusionReason, ...]
    evidence_artifact_ids: tuple[str, ...]
    missing_features: tuple[AttentionFeature, ...]

    @classmethod
    def create(cls, run_id: str, subject: AttentionSubjectInput) -> Self:
        disposition = (
            HoldingRefreshDisposition.PORTFOLIO_MISMATCH
            if subject.subject.position_disposition is PositionDisposition.PORTFOLIO_MISMATCH
            else HoldingRefreshDisposition.REQUIRED
        )
        material = {
            "identity_kind": "holding_refresh",
            "schema_version": _SCHEMA_VERSION,
            "run_id": run_id,
            "identity": subject.subject.identity.to_payload(),
            "disposition": disposition.value,
            "exclusion_reasons": sorted(
                reason.value for reason in subject.subject.exclusion_reasons
            ),
            "evidence_artifact_ids": list(subject.evidence_artifact_ids),
            "missing_features": sorted(feature.value for feature in subject.missing_features),
        }
        return cls(
            _content_hash(material),
            subject.subject.identity,
            disposition,
            tuple(sorted(subject.subject.exclusion_reasons, key=lambda reason: reason.value)),
            subject.evidence_artifact_ids,
            tuple(sorted(subject.missing_features, key=lambda feature: feature.value)),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "refresh_id": self.refresh_id,
            "identity": self.identity.to_payload(),
            "disposition": self.disposition.value,
            "exclusion_reasons": [reason.value for reason in self.exclusion_reasons],
            "evidence_artifact_ids": list(self.evidence_artifact_ids),
            "missing_features": [feature.value for feature in self.missing_features],
        }

    def __post_init__(self) -> None:
        if (
            not _is_sha256(self.refresh_id)
            or type(self.disposition) is not HoldingRefreshDisposition
            or type(self.exclusion_reasons) is not tuple
            or any(type(reason) is not UniverseExclusionReason for reason in self.exclusion_reasons)
            or tuple(sorted(set(self.exclusion_reasons), key=lambda reason: reason.value))
            != self.exclusion_reasons
            or type(self.evidence_artifact_ids) is not tuple
            or tuple(sorted(set(self.evidence_artifact_ids))) != self.evidence_artifact_ids
            or any(not _is_sha256(item) for item in self.evidence_artifact_ids)
            or type(self.missing_features) is not tuple
            or any(type(feature) is not AttentionFeature for feature in self.missing_features)
            or tuple(sorted(set(self.missing_features), key=lambda feature: feature.value))
            != self.missing_features
            or AttentionFeature.CURRENT_HOLDING in self.missing_features
            or (
                self.disposition is HoldingRefreshDisposition.PORTFOLIO_MISMATCH
                and not self.exclusion_reasons
            )
        ):
            raise InvalidAttentionError(_INVALID_ARTIFACT)


@dataclass(frozen=True, slots=True)
class ResourceAccounting:
    """Report exact bounded capacity and the absence of model or adapter use."""

    candidate_card_count: int
    new_dossier_count: int
    holding_refresh_count: int
    candidate_card_limit: int
    new_dossier_limit: int
    weekly_dossier_budget: int
    weekly_dossiers_before: int
    weekly_dossiers_after: int
    weekly_exploration_budget: int
    weekly_exploration_before: int
    weekly_exploration_after: int
    model_tokens: int
    model_turns: int
    adapter_quota_disposition: AdapterQuotaDisposition

    def to_payload(self) -> dict[str, object]:
        return {
            "candidate_card_count": self.candidate_card_count,
            "new_dossier_count": self.new_dossier_count,
            "holding_refresh_count": self.holding_refresh_count,
            "candidate_card_limit": self.candidate_card_limit,
            "new_dossier_limit": self.new_dossier_limit,
            "weekly_dossier_budget": self.weekly_dossier_budget,
            "weekly_dossiers_before": self.weekly_dossiers_before,
            "weekly_dossiers_after": self.weekly_dossiers_after,
            "weekly_exploration_budget": self.weekly_exploration_budget,
            "weekly_exploration_before": self.weekly_exploration_before,
            "weekly_exploration_after": self.weekly_exploration_after,
            "model_tokens": self.model_tokens,
            "model_turns": self.model_turns,
            "adapter_quota_disposition": self.adapter_quota_disposition.value,
        }

    def __post_init__(self) -> None:
        integers = (
            self.candidate_card_count,
            self.new_dossier_count,
            self.holding_refresh_count,
            self.candidate_card_limit,
            self.new_dossier_limit,
            self.weekly_dossier_budget,
            self.weekly_dossiers_before,
            self.weekly_dossiers_after,
            self.weekly_exploration_budget,
            self.weekly_exploration_before,
            self.weekly_exploration_after,
            self.model_tokens,
            self.model_turns,
        )
        if (
            any(type(value) is not int or value < 0 for value in integers)
            or self.candidate_card_count > self.candidate_card_limit
            or not 1 <= self.candidate_card_limit <= _MAXIMUM_CANDIDATE_CARDS
            or self.new_dossier_count > self.new_dossier_limit
            or not 1 <= self.new_dossier_limit <= _MAXIMUM_NEW_DOSSIERS
            or self.new_dossier_limit > self.candidate_card_limit
            or not self.new_dossier_limit <= self.weekly_dossier_budget <= _MAXIMUM_WEEKLY_DOSSIERS
            or not (
                1 <= self.weekly_exploration_budget < self.weekly_dossier_budget
                and 10 * self.weekly_dossier_budget
                <= 100 * self.weekly_exploration_budget
                <= 20 * self.weekly_dossier_budget
            )
            or self.weekly_dossiers_before > self.weekly_dossier_budget
            or self.weekly_dossiers_after != self.weekly_dossiers_before + self.new_dossier_count
            or self.weekly_dossiers_after > self.weekly_dossier_budget
            or not (
                self.weekly_exploration_before
                <= self.weekly_exploration_after
                <= self.weekly_exploration_budget
            )
            or self.weekly_exploration_before > self.weekly_dossiers_before
            or self.weekly_exploration_after > self.weekly_dossiers_after
            or self.model_tokens != 0
            or self.model_turns != 0
            or self.adapter_quota_disposition is not AdapterQuotaDisposition.NOT_CONSULTED
        ):
            raise InvalidAttentionError(_INVALID_ARTIFACT)


@dataclass(frozen=True, slots=True)
class AttentionArtifact:
    """Publish one reconstructable bounded attention selection without later-stage authority."""

    artifact_id: str
    content_hash: str
    run_id: str
    cycle: MarketSession
    universe_snapshot_id: str
    cutoff: UtcInstant
    available_at: UtcInstant
    data_regime: str
    evidence_policy_id: str
    evidence_artifact_ids: tuple[str, ...]
    attention_policy_id: str
    attention_policy: AttentionPolicy
    input_fingerprint: str
    history_fingerprint: str
    week: str
    disposition: AttentionDisposition
    no_action_reason: AttentionNoActionReason | None
    candidate_cards: tuple[CandidateCard, ...]
    dossier_requests: tuple[DossierRequest, ...]
    holding_refreshes: tuple[HoldingRefresh, ...]
    resource_accounting: ResourceAccounting

    @classmethod
    def create(  # noqa: PLR0913 - the artifact binds every material authority input.
        cls,
        *,
        inputs: AttentionInputs,
        policy: AttentionPolicy,
        available_at: UtcInstant,
        history_fingerprint: str,
        candidate_cards: tuple[CandidateCard, ...],
        dossier_requests: tuple[DossierRequest, ...],
        holding_refreshes: tuple[HoldingRefresh, ...],
        resource_accounting: ResourceAccounting,
    ) -> Self:
        if (
            type(available_at) is not UtcInstant
            or available_at.value < inputs.cutoff.value
            or not _selection_outputs_match_inputs(
                inputs,
                policy,
                candidate_cards,
                dossier_requests,
                holding_refreshes,
                resource_accounting,
            )
        ):
            raise InvalidAttentionError(_INVALID_ARTIFACT)
        disposition = (
            AttentionDisposition.NO_ACTION
            if not candidate_cards and not dossier_requests and not holding_refreshes
            else AttentionDisposition.SELECTED
        )
        no_action_reason = (
            AttentionNoActionReason.NO_ELIGIBLE_ATTENTION
            if disposition is AttentionDisposition.NO_ACTION
            else None
        )
        week = _week_key(inputs.cycle)
        identity_material = _artifact_identity_material(
            inputs=inputs,
            policy=policy,
            available_at=available_at,
            history_fingerprint=history_fingerprint,
            week=week,
            disposition=disposition,
            no_action_reason=no_action_reason,
            cards=candidate_cards,
            requests=dossier_requests,
            refreshes=holding_refreshes,
            accounting=resource_accounting,
        )
        artifact_id = _content_hash(identity_material)
        envelope = _artifact_envelope(
            artifact_id=artifact_id,
            inputs=inputs,
            policy=policy,
            available_at=available_at,
            history_fingerprint=history_fingerprint,
            week=week,
            disposition=disposition,
            no_action_reason=no_action_reason,
            cards=candidate_cards,
            requests=dossier_requests,
            refreshes=holding_refreshes,
            accounting=resource_accounting,
        )
        return cls(
            artifact_id,
            _content_hash(envelope),
            inputs.run_id,
            inputs.cycle,
            inputs.universe_snapshot_id,
            inputs.cutoff,
            available_at,
            inputs.data_regime,
            inputs.evidence_policy_id,
            inputs.evidence_artifact_ids,
            policy.policy_id,
            policy,
            inputs.fingerprint,
            history_fingerprint,
            week,
            disposition,
            no_action_reason,
            candidate_cards,
            dossier_requests,
            holding_refreshes,
            resource_accounting,
        )

    def to_payload(self) -> dict[str, object]:
        envelope = _artifact_envelope_from_artifact(self)
        return {**envelope, "content_hash": self.content_hash}

    def matches_inputs(self, inputs: AttentionInputs, policy: AttentionPolicy) -> bool:
        """Confirm outputs remain derived within the exact typed inputs and policy."""
        return (
            self.run_id == inputs.run_id
            and self.cycle == inputs.cycle
            and self.universe_snapshot_id == inputs.universe_snapshot_id
            and self.cutoff == inputs.cutoff
            and self.data_regime == inputs.data_regime
            and self.evidence_policy_id == inputs.evidence_policy_id
            and self.evidence_artifact_ids == inputs.evidence_artifact_ids
            and self.input_fingerprint == inputs.fingerprint
            and self.attention_policy == policy
            and _selection_outputs_match_inputs(
                inputs,
                policy,
                self.candidate_cards,
                self.dossier_requests,
                self.holding_refreshes,
                self.resource_accounting,
            )
        )

    def __post_init__(self) -> None:
        card_ids = {card.card_id for card in self.candidate_cards}
        card_identities = {
            canonical_instrument_bytes(card.identity) for card in self.candidate_cards
        }
        cards_by_id = {card.card_id: card for card in self.candidate_cards}
        request_ids = {request.request_id for request in self.dossier_requests}
        request_card_ids = {request.candidate_card_id for request in self.dossier_requests}
        refresh_ids = {refresh.refresh_id for refresh in self.holding_refreshes}
        refresh_identities = {
            canonical_instrument_bytes(refresh.identity) for refresh in self.holding_refreshes
        }
        expected_envelope = _artifact_envelope_from_artifact(self)
        if (
            not _is_sha256(self.artifact_id)
            or not _is_sha256(self.content_hash)
            or not _is_sha256(self.run_id)
            or type(self.cycle) is not MarketSession
            or not _is_sha256(self.universe_snapshot_id)
            or type(self.cutoff) is not UtcInstant
            or type(self.available_at) is not UtcInstant
            or self.available_at.value < self.cutoff.value
            or not is_data_regime(self.data_regime)
            or not _is_sha256(self.evidence_policy_id)
            or type(self.evidence_artifact_ids) is not tuple
            or not self.evidence_artifact_ids
            or tuple(sorted(set(self.evidence_artifact_ids))) != self.evidence_artifact_ids
            or any(not _is_sha256(item) for item in self.evidence_artifact_ids)
            or not _is_sha256(self.attention_policy_id)
            or type(self.attention_policy) is not AttentionPolicy
            or self.attention_policy.policy_id != self.attention_policy_id
            or not _is_sha256(self.input_fingerprint)
            or not _is_sha256(self.history_fingerprint)
            or type(self.week) is not str
            or self.week != _week_key(self.cycle)
            or type(self.disposition) is not AttentionDisposition
            or (
                self.no_action_reason is not None
                and type(self.no_action_reason) is not AttentionNoActionReason
            )
            or type(self.candidate_cards) is not tuple
            or any(type(card) is not CandidateCard for card in self.candidate_cards)
            or type(self.dossier_requests) is not tuple
            or any(type(request) is not DossierRequest for request in self.dossier_requests)
            or type(self.holding_refreshes) is not tuple
            or any(type(refresh) is not HoldingRefresh for refresh in self.holding_refreshes)
            or type(self.resource_accounting) is not ResourceAccounting
            or len(card_ids) != len(self.candidate_cards)
            or len(card_identities) != len(self.candidate_cards)
            or len(request_ids) != len(self.dossier_requests)
            or len(request_card_ids) != len(self.dossier_requests)
            or len(refresh_ids) != len(self.holding_refreshes)
            or len(refresh_identities) != len(self.holding_refreshes)
            or card_identities & refresh_identities
            or len(self.candidate_cards) > _MAXIMUM_CANDIDATE_CARDS
            or len(self.dossier_requests) > _MAXIMUM_NEW_DOSSIERS
            or len(self.candidate_cards) > self.attention_policy.candidate_card_limit
            or len(self.dossier_requests) > self.attention_policy.new_dossier_limit
            or self.resource_accounting.candidate_card_limit
            != self.attention_policy.candidate_card_limit
            or self.resource_accounting.new_dossier_limit != self.attention_policy.new_dossier_limit
            or self.resource_accounting.weekly_dossier_budget
            != self.attention_policy.weekly_dossier_budget
            or self.resource_accounting.weekly_exploration_budget
            != self.attention_policy.weekly_exploration_budget
            or any(
                CandidateCard.create(
                    run_id=self.run_id,
                    identity=card.identity,
                    previous_state=card.previous_state,
                    next_state=card.next_state,
                    disposition=card.disposition,
                    observed_features=card.observed_features,
                    reasons=card.reasons,
                    evidence_artifact_ids=card.evidence_artifact_ids,
                    missing_features=card.missing_features,
                    exit_reason=card.exit_reason,
                )
                != card
                or not set(card.evidence_artifact_ids) <= set(self.evidence_artifact_ids)
                for card in self.candidate_cards
            )
            or any(
                request.candidate_card_id not in card_ids
                or cards_by_id[request.candidate_card_id].identity != request.identity
                or cards_by_id[request.candidate_card_id].disposition
                is not CandidateDisposition.NEW_DOSSIER_REQUESTED
                or (
                    CandidateReason.EXPLORATION_FUNNEL
                    in cards_by_id[request.candidate_card_id].reasons
                )
                != (request.selection_kind is DossierSelectionKind.EXPLORATION)
                or DossierRequest.create(
                    run_id=self.run_id,
                    card=cards_by_id[request.candidate_card_id],
                    selection_kind=request.selection_kind,
                )
                != request
                for request in self.dossier_requests
            )
            or request_card_ids
            != {
                card.card_id
                for card in self.candidate_cards
                if card.disposition is CandidateDisposition.NEW_DOSSIER_REQUESTED
            }
            or any(
                not set(refresh.evidence_artifact_ids) <= set(self.evidence_artifact_ids)
                or refresh.refresh_id != _holding_refresh_id(self.run_id, refresh)
                for refresh in self.holding_refreshes
            )
            or self.resource_accounting.candidate_card_count != len(self.candidate_cards)
            or self.resource_accounting.new_dossier_count != len(self.dossier_requests)
            or self.resource_accounting.holding_refresh_count != len(self.holding_refreshes)
            or (
                self.resource_accounting.weekly_exploration_after
                - self.resource_accounting.weekly_exploration_before
                != sum(
                    request.selection_kind is DossierSelectionKind.EXPLORATION
                    for request in self.dossier_requests
                )
            )
            or (self.disposition is AttentionDisposition.NO_ACTION)
            != (self.no_action_reason is not None)
            or (
                self.disposition is AttentionDisposition.NO_ACTION
                and (self.candidate_cards or self.dossier_requests or self.holding_refreshes)
            )
            or (
                self.disposition is AttentionDisposition.SELECTED
                and not (self.candidate_cards or self.dossier_requests or self.holding_refreshes)
            )
            or self.artifact_id != _content_hash(_identity_from_envelope(expected_envelope))
            or self.content_hash != _content_hash(expected_envelope)
        ):
            raise InvalidAttentionError(_INVALID_ARTIFACT)


def attention_history_fingerprint(history: tuple[AttentionArtifact, ...]) -> str:
    """Bind the ordered durable attention records consumed by one selection."""
    return _content_hash(
        {
            "schema_version": _SCHEMA_VERSION,
            "history": [
                {
                    "artifact_id": artifact.artifact_id,
                    "content_hash": artifact.content_hash,
                }
                for artifact in history
            ],
        }
    )


def validate_attention_history(history: tuple[AttentionArtifact, ...]) -> None:
    """Reconstruct attention transitions and weekly accounting or fail closed."""
    if type(history) is not tuple or any(type(item) is not AttentionArtifact for item in history):
        raise InvalidAttentionError(_INVALID_INPUTS)
    states: dict[bytes, AttentionState] = {}
    weekly_counts: dict[str, tuple[int, int]] = {}
    prior: list[AttentionArtifact] = []
    prior_date = None
    for artifact in history:
        artifact.__post_init__()
        trading_date = artifact.cycle.trading_date
        if prior_date is not None and trading_date <= prior_date:
            raise InvalidAttentionError(_INVALID_INPUTS)
        if artifact.history_fingerprint != attention_history_fingerprint(tuple(prior)):
            raise InvalidAttentionError(_INVALID_INPUTS)
        regular_before, exploration_before = weekly_counts.get(artifact.week, (0, 0))
        accounting = artifact.resource_accounting
        if (
            accounting.weekly_dossiers_before != regular_before + exploration_before
            or accounting.weekly_exploration_before != exploration_before
        ):
            raise InvalidAttentionError(_INVALID_INPUTS)
        for card in artifact.candidate_cards:
            key = canonical_instrument_bytes(card.identity)
            if card.previous_state is not states.get(key, AttentionState.OBSERVED):
                raise InvalidAttentionError(_INVALID_INPUTS)
            states[key] = card.next_state
        regular_after = regular_before + sum(
            request.selection_kind is DossierSelectionKind.PRIORITY
            for request in artifact.dossier_requests
        )
        exploration_after = exploration_before + sum(
            request.selection_kind is DossierSelectionKind.EXPLORATION
            for request in artifact.dossier_requests
        )
        weekly_counts[artifact.week] = (regular_after, exploration_after)
        prior.append(artifact)
        prior_date = trading_date


def _selection_outputs_match_inputs(  # noqa: PLR0913,PLR0917 - validate one complete selection.
    inputs: AttentionInputs,
    policy: AttentionPolicy,
    cards: tuple[CandidateCard, ...],
    requests: tuple[DossierRequest, ...],
    refreshes: tuple[HoldingRefresh, ...],
    accounting: ResourceAccounting,
) -> bool:
    subjects = {canonical_instrument_bytes(item.subject.identity): item for item in inputs.subjects}
    holding_subjects = {
        key: subject for key, subject in subjects.items() if subject.subject.is_position
    }
    if (
        len(cards) > policy.candidate_card_limit
        or len(requests) > policy.new_dossier_limit
        or accounting.candidate_card_limit != policy.candidate_card_limit
        or accounting.new_dossier_limit != policy.new_dossier_limit
        or accounting.weekly_dossier_budget != policy.weekly_dossier_budget
        or accounting.weekly_exploration_budget != policy.weekly_exploration_budget
        or {canonical_instrument_bytes(refresh.identity): refresh for refresh in refreshes}.keys()
        != holding_subjects.keys()
    ):
        return False
    for card in cards:
        subject = subjects.get(canonical_instrument_bytes(card.identity))
        if (
            subject is None
            or subject.subject.is_position
            or card.observed_features
            != tuple(sorted(subject.observed_features, key=lambda feature: feature.value))
            or card.missing_features
            != tuple(sorted(subject.missing_features, key=lambda feature: feature.value))
            or card.evidence_artifact_ids != subject.evidence_artifact_ids
            or (
                card.disposition is CandidateDisposition.EXITED_ACTIVE_PATH
                and (
                    subject.subject.eligible_for_new_entry
                    or card.next_state is not AttentionState.REJECTED
                    or card.exit_reason is not AttentionTransitionExitReason.INELIGIBLE
                )
            )
            or (
                card.disposition is not CandidateDisposition.EXITED_ACTIVE_PATH
                and not subject.subject.eligible_for_new_entry
            )
        ):
            return False
    return all(
        refresh == HoldingRefresh.create(inputs.run_id, holding_subjects[key])
        for key, refresh in (
            (canonical_instrument_bytes(item.identity), item) for item in refreshes
        )
    )


def select_attention(
    policy: AttentionPolicy,
    inputs: AttentionInputs,
    history: tuple[AttentionArtifact, ...],
    *,
    available_at: UtcInstant,
) -> AttentionArtifact:
    """Select cards, due refreshes, and Dossier requests without model or execution effects."""
    policy.__post_init__()
    inputs.__post_init__()
    if type(history) is not tuple or any(type(item) is not AttentionArtifact for item in history):
        raise InvalidAttentionError(_INVALID_INPUTS)
    for item in history:
        item.__post_init__()
    ordered_history = tuple(
        sorted(history, key=lambda item: (item.cycle.trading_date, item.artifact_id))
    )
    if len({item.cycle.trading_date for item in ordered_history}) != len(ordered_history) or any(
        item.cycle.trading_date >= inputs.cycle.trading_date for item in ordered_history
    ):
        raise InvalidAttentionError(_INVALID_INPUTS)
    validate_attention_history(ordered_history)

    ordered_subjects = tuple(
        sorted(
            inputs.subjects,
            key=lambda subject: canonical_instrument_bytes(subject.subject.identity),
        )
    )
    holding_refreshes = tuple(
        HoldingRefresh.create(inputs.run_id, subject)
        for subject in ordered_subjects
        if subject.subject.is_position
    )
    previous_states = _latest_states(ordered_history)
    exit_subjects = tuple(
        subject
        for subject in ordered_subjects
        if not subject.subject.is_position
        and not subject.subject.eligible_for_new_entry
        and canonical_instrument_bytes(subject.subject.identity) in previous_states
        and previous_states[canonical_instrument_bytes(subject.subject.identity)]
        not in _TERMINAL_STATES
    )
    selected_exit_subjects = exit_subjects[: policy.candidate_card_limit]
    remaining_card_capacity = policy.candidate_card_limit - len(selected_exit_subjects)
    eligible = tuple(
        subject
        for subject in ordered_subjects
        if not subject.subject.is_position and subject.subject.eligible_for_new_entry
        if previous_states.get(
            canonical_instrument_bytes(subject.subject.identity),
            AttentionState.OBSERVED,
        )
        in (AttentionState.OBSERVED, AttentionState.WATCH, AttentionState.CANDIDATE)
    )
    selected_subjects, exploration_funnel_identity = _select_card_subjects(
        policy,
        inputs.cycle,
        eligible,
        card_limit=remaining_card_capacity,
        history=ordered_history,
    )
    regular_before, exploration_before = _weekly_dossier_counts(
        inputs.cycle,
        ordered_history,
    )
    if (
        regular_before > policy.weekly_dossier_budget - policy.weekly_exploration_budget
        or exploration_before > policy.weekly_exploration_budget
    ):
        raise InvalidAttentionError(_INVALID_INPUTS)

    regular_capacity = min(
        max(
            policy.new_dossier_limit
            - (1 if exploration_before < policy.weekly_exploration_budget else 0),
            0,
        ),
        policy.weekly_dossier_budget - policy.weekly_exploration_budget - regular_before,
    )
    ready = tuple(
        subject
        for subject in selected_subjects
        if previous_states.get(
            canonical_instrument_bytes(subject.subject.identity),
            AttentionState.OBSERVED,
        )
        is AttentionState.CANDIDATE
    )
    priority_ready = tuple(subject for subject in ready if _has_priority_trigger(subject))
    priority_selected = tuple(sorted(priority_ready, key=_priority_key)[:regular_capacity])
    priority_keys = {
        canonical_instrument_bytes(subject.subject.identity) for subject in priority_selected
    }
    exploration_capacity = min(
        policy.new_dossier_limit - len(priority_selected),
        policy.weekly_exploration_budget - exploration_before,
    )
    exploration_pool = tuple(
        subject
        for subject in ready
        if canonical_instrument_bytes(subject.subject.identity) not in priority_keys
    )
    exploration_selected = tuple(
        sorted(
            exploration_pool,
            key=lambda subject: _exploration_key(
                policy,
                inputs.cycle,
                subject.subject.identity,
            ),
        )[:exploration_capacity]
    )
    exploration_keys = {
        canonical_instrument_bytes(subject.subject.identity) for subject in exploration_selected
    }

    active_cards = tuple(
        _build_card(
            inputs.run_id,
            subject,
            previous_states.get(
                canonical_instrument_bytes(subject.subject.identity),
                AttentionState.OBSERVED,
            ),
            priority_selected=(
                canonical_instrument_bytes(subject.subject.identity) in priority_keys
            ),
            exploration_selected=(
                canonical_instrument_bytes(subject.subject.identity) in exploration_keys
            ),
            exploration_funnel=(
                exploration_funnel_identity == canonical_instrument_bytes(subject.subject.identity)
            ),
        )
        for subject in selected_subjects
    )
    exit_cards = tuple(
        _build_exit_card(
            inputs.run_id,
            subject,
            previous_states[canonical_instrument_bytes(subject.subject.identity)],
        )
        for subject in selected_exit_subjects
    )
    cards = tuple(
        sorted(
            (*active_cards, *exit_cards),
            key=lambda card: canonical_instrument_bytes(card.identity),
        )
    )
    cards_by_identity = {canonical_instrument_bytes(card.identity): card for card in cards}
    requests = tuple(
        sorted(
            (
                DossierRequest.create(
                    run_id=inputs.run_id,
                    card=cards_by_identity[canonical_instrument_bytes(subject.subject.identity)],
                    selection_kind=kind,
                )
                for kind, subjects in (
                    (DossierSelectionKind.PRIORITY, priority_selected),
                    (DossierSelectionKind.EXPLORATION, exploration_selected),
                )
                for subject in subjects
            ),
            key=lambda request: canonical_instrument_bytes(request.identity),
        )
    )
    regular_after = regular_before + len(priority_selected)
    exploration_after = exploration_before + len(exploration_selected)
    accounting = ResourceAccounting(
        candidate_card_count=len(cards),
        new_dossier_count=len(requests),
        holding_refresh_count=len(holding_refreshes),
        candidate_card_limit=policy.candidate_card_limit,
        new_dossier_limit=policy.new_dossier_limit,
        weekly_dossier_budget=policy.weekly_dossier_budget,
        weekly_dossiers_before=regular_before + exploration_before,
        weekly_dossiers_after=regular_after + exploration_after,
        weekly_exploration_budget=policy.weekly_exploration_budget,
        weekly_exploration_before=exploration_before,
        weekly_exploration_after=exploration_after,
        model_tokens=0,
        model_turns=0,
        adapter_quota_disposition=AdapterQuotaDisposition.NOT_CONSULTED,
    )
    return AttentionArtifact.create(
        inputs=inputs,
        policy=policy,
        available_at=available_at,
        history_fingerprint=attention_history_fingerprint(ordered_history),
        candidate_cards=cards,
        dossier_requests=requests,
        holding_refreshes=holding_refreshes,
        resource_accounting=accounting,
    )


def _select_card_subjects(
    policy: AttentionPolicy,
    cycle: MarketSession,
    subjects: tuple[AttentionSubjectInput, ...],
    *,
    card_limit: int,
    history: tuple[AttentionArtifact, ...],
) -> tuple[tuple[AttentionSubjectInput, ...], bytes | None]:
    if card_limit == 0:
        return (), None
    if len(subjects) <= card_limit:
        return subjects, None
    _, exploration_before = _weekly_dossier_counts(cycle, history)
    reserve_exploration_card = exploration_before < policy.weekly_exploration_budget
    regular_limit = card_limit - (1 if reserve_exploration_card else 0)
    regular = tuple(sorted(subjects, key=_priority_key)[:regular_limit])
    regular_keys = {canonical_instrument_bytes(subject.subject.identity) for subject in regular}
    if not reserve_exploration_card:
        return regular, None
    exploration_pool = tuple(
        subject
        for subject in subjects
        if canonical_instrument_bytes(subject.subject.identity) not in regular_keys
    )
    if not exploration_pool:
        return regular, None
    exploration = min(
        exploration_pool,
        key=lambda subject: _exploration_key(policy, cycle, subject.subject.identity),
    )
    selected = tuple(
        sorted(
            (*regular, exploration),
            key=lambda subject: canonical_instrument_bytes(subject.subject.identity),
        )
    )
    return selected, canonical_instrument_bytes(exploration.subject.identity)


def _build_card(  # noqa: PLR0913 - transition inputs remain explicit and auditable.
    run_id: str,
    subject: AttentionSubjectInput,
    previous_state: AttentionState,
    *,
    priority_selected: bool,
    exploration_selected: bool,
    exploration_funnel: bool,
) -> CandidateCard:
    reasons = list(_feature_reasons(subject.observed_features))
    if priority_selected or exploration_selected:
        next_state = AttentionState.DOSSIER
        disposition = CandidateDisposition.NEW_DOSSIER_REQUESTED
        if exploration_selected:
            reasons.append(CandidateReason.EXPLORATION_FUNNEL)
    elif previous_state in (AttentionState.OBSERVED, AttentionState.WATCH):
        next_state = _NEXT_ACTIVE_STATE[previous_state]
        disposition = CandidateDisposition.ADVANCED_ONE_STATE
        reasons.append(CandidateReason.FUNNEL_PROGRESSION)
        if exploration_funnel:
            reasons.append(CandidateReason.EXPLORATION_FUNNEL)
    elif previous_state is AttentionState.CANDIDATE:
        next_state = previous_state
        disposition = CandidateDisposition.DEFERRED_AT_CAPACITY
        reasons.append(CandidateReason.DOSSIER_CAPACITY)
    else:
        next_state = previous_state
        disposition = CandidateDisposition.ALREADY_IN_RESEARCH
        reasons.append(CandidateReason.ALREADY_IN_RESEARCH)
    exit_reason = _exit_reason_for(previous_state) if next_state in _TERMINAL_STATES else None
    return CandidateCard.create(
        run_id=run_id,
        identity=subject.subject.identity,
        previous_state=previous_state,
        next_state=next_state,
        disposition=disposition,
        observed_features=subject.observed_features,
        reasons=tuple(reasons),
        evidence_artifact_ids=subject.evidence_artifact_ids,
        missing_features=subject.missing_features,
        exit_reason=exit_reason,
    )


def _build_exit_card(
    run_id: str,
    subject: AttentionSubjectInput,
    previous_state: AttentionState,
) -> CandidateCard:
    return CandidateCard.create(
        run_id=run_id,
        identity=subject.subject.identity,
        previous_state=previous_state,
        next_state=AttentionState.REJECTED,
        disposition=CandidateDisposition.EXITED_ACTIVE_PATH,
        observed_features=subject.observed_features,
        reasons=(*_feature_reasons(subject.observed_features), CandidateReason.TERMINAL_STATE),
        evidence_artifact_ids=subject.evidence_artifact_ids,
        missing_features=subject.missing_features,
        exit_reason=AttentionTransitionExitReason.INELIGIBLE,
    )


def _feature_reasons(features: tuple[AttentionFeature, ...]) -> tuple[CandidateReason, ...]:
    mapping = {
        AttentionFeature.FRESHNESS: CandidateReason.FRESH_EVIDENCE,
        AttentionFeature.PRICE_CHANGE: CandidateReason.PRICE_CHANGE,
        AttentionFeature.LIQUIDITY_CHANGE: CandidateReason.LIQUIDITY_CHANGE,
        AttentionFeature.GAP: CandidateReason.GAP,
        AttentionFeature.UNUSUAL_VOLUME: CandidateReason.UNUSUAL_VOLUME,
        AttentionFeature.NEWS_ARRIVAL: CandidateReason.NEWS_ARRIVAL,
        AttentionFeature.FILING_ARRIVAL: CandidateReason.FILING_ARRIVAL,
        AttentionFeature.KNOWN_EVENT_PROXIMITY: CandidateReason.KNOWN_EVENT_PROXIMITY,
        AttentionFeature.EXISTING_BELIEF: CandidateReason.EXISTING_BELIEF,
        AttentionFeature.THESIS_EXPIRY: CandidateReason.THESIS_EXPIRY,
    }
    return tuple(mapping[feature] for feature in features if feature in mapping)


def _has_priority_trigger(subject: AttentionSubjectInput) -> bool:
    return bool(
        set(subject.observed_features)
        & {
            AttentionFeature.PRICE_CHANGE,
            AttentionFeature.LIQUIDITY_CHANGE,
            AttentionFeature.GAP,
            AttentionFeature.UNUSUAL_VOLUME,
            AttentionFeature.NEWS_ARRIVAL,
            AttentionFeature.FILING_ARRIVAL,
            AttentionFeature.KNOWN_EVENT_PROXIMITY,
            AttentionFeature.THESIS_EXPIRY,
        }
    )


def _priority_key(subject: AttentionSubjectInput) -> tuple[object, ...]:
    features = set(subject.observed_features)
    ordered = (
        AttentionFeature.FILING_ARRIVAL,
        AttentionFeature.NEWS_ARRIVAL,
        AttentionFeature.THESIS_EXPIRY,
        AttentionFeature.KNOWN_EVENT_PROXIMITY,
        AttentionFeature.UNUSUAL_VOLUME,
        AttentionFeature.GAP,
        AttentionFeature.LIQUIDITY_CHANGE,
        AttentionFeature.PRICE_CHANGE,
        AttentionFeature.FRESHNESS,
    )
    # Lexicographic presence ordering is an attention queue, never a return or alpha score.
    return (
        *tuple(feature not in features for feature in ordered),
        canonical_instrument_bytes(subject.subject.identity),
    )


def _exploration_key(
    policy: AttentionPolicy,
    cycle: MarketSession,
    identity: InstrumentIdentity,
) -> bytes:
    material = b"\0".join(
        (
            policy.exploration_seed.encode(),
            _week_key(cycle).encode(),
            canonical_instrument_bytes(identity),
        )
    )
    return hashlib.sha256(material).digest()


def _latest_states(history: tuple[AttentionArtifact, ...]) -> dict[bytes, AttentionState]:
    states: dict[bytes, AttentionState] = {}
    for artifact in history:
        for card in artifact.candidate_cards:
            states[canonical_instrument_bytes(card.identity)] = card.next_state
    return states


def _weekly_dossier_counts(
    cycle: MarketSession,
    history: tuple[AttentionArtifact, ...],
) -> tuple[int, int]:
    week = _week_key(cycle)
    regular = 0
    exploration = 0
    for artifact in history:
        if artifact.week != week:
            continue
        for request in artifact.dossier_requests:
            if request.selection_kind is DossierSelectionKind.PRIORITY:
                regular += 1
            elif request.selection_kind is DossierSelectionKind.EXPLORATION:
                exploration += 1
            else:
                assert_never(request.selection_kind)  # pragma: no cover
    return regular, exploration


def _exit_reason_for(state: AttentionState) -> AttentionTransitionExitReason:
    if state is AttentionState.REJECTED:
        return AttentionTransitionExitReason.INELIGIBLE
    if state is AttentionState.REFUTED:
        return AttentionTransitionExitReason.REFUTED_BY_EVIDENCE
    if state is AttentionState.DORMANT:
        return AttentionTransitionExitReason.DORMANT_WITHOUT_NEW_EVIDENCE
    if state is AttentionState.ARCHIVED:
        return AttentionTransitionExitReason.ARCHIVED_AS_SUPERSEDED
    raise InvalidAttentionError(_INVALID_ARTIFACT)


_NEXT_ACTIVE_STATE = {
    AttentionState.OBSERVED: AttentionState.WATCH,
    AttentionState.WATCH: AttentionState.CANDIDATE,
    AttentionState.CANDIDATE: AttentionState.DOSSIER,
    AttentionState.DOSSIER: AttentionState.THESIS,
    AttentionState.THESIS: AttentionState.PORTFOLIO,
}
_TERMINAL_STATES = frozenset(
    {
        AttentionState.REJECTED,
        AttentionState.REFUTED,
        AttentionState.DORMANT,
        AttentionState.ARCHIVED,
    }
)


def _candidate_material(  # noqa: PLR0913 - card identity binds every explanatory field.
    *,
    run_id: str,
    identity: InstrumentIdentity,
    previous_state: AttentionState,
    next_state: AttentionState,
    disposition: CandidateDisposition,
    observed_features: tuple[AttentionFeature, ...],
    reasons: tuple[CandidateReason, ...],
    evidence_artifact_ids: tuple[str, ...],
    missing_features: tuple[AttentionFeature, ...],
    exit_reason: AttentionTransitionExitReason | None,
) -> dict[str, object]:
    return {
        "identity_kind": "candidate_card",
        "schema_version": _SCHEMA_VERSION,
        "run_id": run_id,
        "identity": identity.to_payload(),
        "previous_state": previous_state.value,
        "next_state": next_state.value,
        "disposition": disposition.value,
        "observed_features": sorted({feature.value for feature in observed_features}),
        "reasons": sorted({reason.value for reason in reasons}),
        "evidence_artifact_ids": list(evidence_artifact_ids),
        "missing_features": sorted({feature.value for feature in missing_features}),
        "exit_reason": None if exit_reason is None else exit_reason.value,
    }


def _holding_refresh_id(run_id: str, refresh: HoldingRefresh) -> str:
    material = {
        "identity_kind": "holding_refresh",
        "schema_version": _SCHEMA_VERSION,
        "run_id": run_id,
        "identity": refresh.identity.to_payload(),
        "disposition": refresh.disposition.value,
        "exclusion_reasons": [reason.value for reason in refresh.exclusion_reasons],
        "evidence_artifact_ids": list(refresh.evidence_artifact_ids),
        "missing_features": [feature.value for feature in refresh.missing_features],
    }
    return _content_hash(material)


def _artifact_identity_material(  # noqa: PLR0913 - artifact identity is deliberately complete.
    *,
    inputs: AttentionInputs,
    policy: AttentionPolicy,
    available_at: UtcInstant,
    history_fingerprint: str,
    week: str,
    disposition: AttentionDisposition,
    no_action_reason: AttentionNoActionReason | None,
    cards: tuple[CandidateCard, ...],
    requests: tuple[DossierRequest, ...],
    refreshes: tuple[HoldingRefresh, ...],
    accounting: ResourceAccounting,
) -> dict[str, object]:
    return {
        "identity_kind": "attention_selection_artifact",
        "identity_schema_version": _SCHEMA_VERSION,
        "run_id": inputs.run_id,
        "cycle": inputs.cycle.to_payload(),
        "universe_snapshot_id": inputs.universe_snapshot_id,
        "cutoff": inputs.cutoff.isoformat(),
        "available_at": available_at.isoformat(),
        "data_regime": inputs.data_regime,
        "evidence_policy_id": inputs.evidence_policy_id,
        "evidence_artifact_ids": list(inputs.evidence_artifact_ids),
        "attention_policy_id": policy.policy_id,
        "attention_policy": policy.to_payload(),
        "input_fingerprint": inputs.fingerprint,
        "history_fingerprint": history_fingerprint,
        "week": week,
        "disposition": disposition.value,
        "no_action_reason": (None if no_action_reason is None else no_action_reason.value),
        "candidate_cards": [card.to_payload() for card in cards],
        "dossier_requests": [request.to_payload() for request in requests],
        "holding_refreshes": [refresh.to_payload() for refresh in refreshes],
        "resource_accounting": accounting.to_payload(),
    }


def _artifact_envelope(  # noqa: PLR0913 - durable envelope names each material reference.
    *,
    artifact_id: str,
    inputs: AttentionInputs,
    policy: AttentionPolicy,
    available_at: UtcInstant,
    history_fingerprint: str,
    week: str,
    disposition: AttentionDisposition,
    no_action_reason: AttentionNoActionReason | None,
    cards: tuple[CandidateCard, ...],
    requests: tuple[DossierRequest, ...],
    refreshes: tuple[HoldingRefresh, ...],
    accounting: ResourceAccounting,
) -> dict[str, object]:
    return {
        "envelope_schema_version": _SCHEMA_VERSION,
        "record_kind": "attention_selection_artifact",
        "payload_discriminator": "equity_attention_selection",
        "payload_schema_version": _SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "cycle": inputs.cycle.to_payload(),
        "relevant_at": inputs.cutoff.isoformat(),
        "available_at": available_at.isoformat(),
        "data_regime": inputs.data_regime,
        "authority_scope": "research_attention",
        "material_fingerprints": {
            "universe_snapshot": inputs.universe_snapshot_id,
            "evidence_policy": inputs.evidence_policy_id,
            "attention_policy": policy.policy_id,
            "attention_inputs": inputs.fingerprint,
            "attention_history": history_fingerprint,
        },
        "payload": {
            "run_id": inputs.run_id,
            "attention_policy": policy.to_payload(),
            "evidence_artifact_ids": list(inputs.evidence_artifact_ids),
            "week": week,
            "disposition": disposition.value,
            "no_action_reason": (None if no_action_reason is None else no_action_reason.value),
            "candidate_cards": [card.to_payload() for card in cards],
            "dossier_requests": [request.to_payload() for request in requests],
            "holding_refreshes": [refresh.to_payload() for refresh in refreshes],
            "resource_accounting": accounting.to_payload(),
        },
    }


def _artifact_envelope_from_artifact(artifact: AttentionArtifact) -> dict[str, object]:
    return {
        "envelope_schema_version": _SCHEMA_VERSION,
        "record_kind": "attention_selection_artifact",
        "payload_discriminator": "equity_attention_selection",
        "payload_schema_version": _SCHEMA_VERSION,
        "artifact_id": artifact.artifact_id,
        "cycle": artifact.cycle.to_payload(),
        "relevant_at": artifact.cutoff.isoformat(),
        "available_at": artifact.available_at.isoformat(),
        "data_regime": artifact.data_regime,
        "authority_scope": "research_attention",
        "material_fingerprints": {
            "universe_snapshot": artifact.universe_snapshot_id,
            "evidence_policy": artifact.evidence_policy_id,
            "attention_policy": artifact.attention_policy_id,
            "attention_inputs": artifact.input_fingerprint,
            "attention_history": artifact.history_fingerprint,
        },
        "payload": {
            "run_id": artifact.run_id,
            "attention_policy": artifact.attention_policy.to_payload(),
            "evidence_artifact_ids": list(artifact.evidence_artifact_ids),
            "week": artifact.week,
            "disposition": artifact.disposition.value,
            "no_action_reason": (
                None if artifact.no_action_reason is None else artifact.no_action_reason.value
            ),
            "candidate_cards": [card.to_payload() for card in artifact.candidate_cards],
            "dossier_requests": [request.to_payload() for request in artifact.dossier_requests],
            "holding_refreshes": [refresh.to_payload() for refresh in artifact.holding_refreshes],
            "resource_accounting": artifact.resource_accounting.to_payload(),
        },
    }


def parse_attention_artifact(value: object) -> AttentionArtifact | None:  # noqa: PLR0911
    """Validate one hostile durable attention envelope before reconstructing it."""
    root = _exact_mapping(
        value,
        {
            "envelope_schema_version",
            "record_kind",
            "payload_discriminator",
            "payload_schema_version",
            "artifact_id",
            "cycle",
            "relevant_at",
            "available_at",
            "data_regime",
            "authority_scope",
            "material_fingerprints",
            "payload",
            "content_hash",
        },
    )
    if root is None:
        return None
    fingerprints = _exact_mapping(
        root["material_fingerprints"],
        {
            "universe_snapshot",
            "evidence_policy",
            "attention_policy",
            "attention_inputs",
            "attention_history",
        },
    )
    payload = _exact_mapping(
        root["payload"],
        {
            "run_id",
            "attention_policy",
            "evidence_artifact_ids",
            "week",
            "disposition",
            "no_action_reason",
            "candidate_cards",
            "dossier_requests",
            "holding_refreshes",
            "resource_accounting",
        },
    )
    cycle = parse_decision_cycle_identity(root["cycle"])
    cutoff = _parse_instant(root["relevant_at"])
    available = _parse_instant(root["available_at"])
    artifact_id = root["artifact_id"]
    content_hash = root["content_hash"]
    data_regime = root["data_regime"]
    run_id = None if payload is None else payload["run_id"]
    universe_snapshot_id = None if fingerprints is None else fingerprints["universe_snapshot"]
    evidence_policy_id = None if fingerprints is None else fingerprints["evidence_policy"]
    attention_policy_id = None if fingerprints is None else fingerprints["attention_policy"]
    input_fingerprint = None if fingerprints is None else fingerprints["attention_inputs"]
    history_fingerprint = None if fingerprints is None else fingerprints["attention_history"]
    parsed_policy = None if payload is None else AttentionPolicy.parse(payload["attention_policy"])
    if (
        fingerprints is None
        or payload is None
        or type(cycle) is not MarketSession
        or cutoff is None
        or available is None
        or available.value < cutoff.value
        or type(root["envelope_schema_version"]) is not int
        or root["envelope_schema_version"] != _SCHEMA_VERSION
        or type(root["payload_schema_version"]) is not int
        or root["payload_schema_version"] != _SCHEMA_VERSION
        or root["record_kind"] != "attention_selection_artifact"
        or root["payload_discriminator"] != "equity_attention_selection"
        or root["authority_scope"] != "research_attention"
        or not is_data_regime(data_regime)
        or not _is_sha256(artifact_id)
        or not _is_sha256(content_hash)
        or any(not _is_sha256(item) for item in fingerprints.values())
        or not _is_sha256(run_id)
        or not _is_sha256(universe_snapshot_id)
        or not _is_sha256(evidence_policy_id)
        or not _is_sha256(attention_policy_id)
        or not _is_sha256(input_fingerprint)
        or not _is_sha256(history_fingerprint)
        or parsed_policy is None
        or parsed_policy.policy_id != attention_policy_id
    ):
        return None
    evidence_ids = _parse_hash_tuple(payload["evidence_artifact_ids"])
    cards = _parse_cards(payload["candidate_cards"], run_id)
    requests = _parse_requests(payload["dossier_requests"], run_id, cards)
    refreshes = _parse_refreshes(payload["holding_refreshes"], run_id)
    accounting = _parse_resource_accounting(payload["resource_accounting"])
    disposition = _enum(AttentionDisposition, payload["disposition"])
    valid_no_action, no_action = _optional_enum(
        AttentionNoActionReason, payload["no_action_reason"]
    )
    if (
        evidence_ids is None
        or cards is None
        or requests is None
        or refreshes is None
        or accounting is None
        or disposition is None
        or not valid_no_action
        or type(payload["week"]) is not str
    ):
        return None
    try:
        artifact = AttentionArtifact(
            artifact_id=artifact_id,
            content_hash=content_hash,
            run_id=run_id,
            cycle=cycle,
            universe_snapshot_id=universe_snapshot_id,
            cutoff=cutoff,
            available_at=available,
            data_regime=data_regime,
            evidence_policy_id=evidence_policy_id,
            evidence_artifact_ids=evidence_ids,
            attention_policy_id=attention_policy_id,
            attention_policy=parsed_policy,
            input_fingerprint=input_fingerprint,
            history_fingerprint=history_fingerprint,
            week=payload["week"],
            disposition=disposition,
            no_action_reason=no_action,
            candidate_cards=cards,
            dossier_requests=requests,
            holding_refreshes=refreshes,
            resource_accounting=accounting,
        )
    except (InvalidAttentionError, ValueError):
        return None
    expected_envelope = _artifact_envelope_from_artifact(artifact)
    if artifact.artifact_id != _content_hash(_identity_from_envelope(expected_envelope)):
        return None
    if artifact.content_hash != _content_hash(expected_envelope):
        return None
    return artifact if artifact.to_payload() == root else None


def _identity_from_envelope(envelope: dict[str, object]) -> dict[str, object]:
    fingerprints = envelope["material_fingerprints"]
    payload = envelope["payload"]
    if type(fingerprints) is not dict or type(payload) is not dict:  # pragma: no cover
        raise InvalidAttentionError(_INVALID_ARTIFACT)
    return {
        "identity_kind": "attention_selection_artifact",
        "identity_schema_version": _SCHEMA_VERSION,
        "run_id": payload["run_id"],
        "cycle": envelope["cycle"],
        "universe_snapshot_id": fingerprints["universe_snapshot"],
        "cutoff": envelope["relevant_at"],
        "available_at": envelope["available_at"],
        "data_regime": envelope["data_regime"],
        "evidence_policy_id": fingerprints["evidence_policy"],
        "evidence_artifact_ids": payload["evidence_artifact_ids"],
        "attention_policy_id": fingerprints["attention_policy"],
        "attention_policy": payload["attention_policy"],
        "input_fingerprint": fingerprints["attention_inputs"],
        "history_fingerprint": fingerprints["attention_history"],
        "week": payload["week"],
        "disposition": payload["disposition"],
        "no_action_reason": payload["no_action_reason"],
        "candidate_cards": payload["candidate_cards"],
        "dossier_requests": payload["dossier_requests"],
        "holding_refreshes": payload["holding_refreshes"],
        "resource_accounting": payload["resource_accounting"],
    }


def _parse_cards(  # noqa: PLR0911 - fail closed at each hostile card field.
    value: object,
    run_id: object,
) -> tuple[CandidateCard, ...] | None:
    if type(value) is not list or type(run_id) is not str:
        return None
    cards: list[CandidateCard] = []
    for item in value:
        root = _exact_mapping(
            item,
            {
                "card_id",
                "identity",
                "previous_state",
                "next_state",
                "disposition",
                "observed_features",
                "reasons",
                "evidence_artifact_ids",
                "missing_features",
                "exit_reason",
            },
        )
        if root is None:
            return None
        identity = parse_instrument_identity(root["identity"])
        previous_state = _enum(AttentionState, root["previous_state"])
        next_state = _enum(AttentionState, root["next_state"])
        disposition = _enum(CandidateDisposition, root["disposition"])
        observed = _enum_tuple(AttentionFeature, root["observed_features"])
        reasons = _enum_tuple(CandidateReason, root["reasons"])
        evidence_ids = _parse_hash_tuple(root["evidence_artifact_ids"])
        missing = _enum_tuple(AttentionFeature, root["missing_features"])
        valid_exit, exit_reason = _optional_enum(AttentionTransitionExitReason, root["exit_reason"])
        if (
            identity is None
            or previous_state is None
            or next_state is None
            or disposition is None
            or observed is None
            or reasons is None
            or evidence_ids is None
            or missing is None
            or not valid_exit
        ):
            return None
        try:
            card = CandidateCard.create(
                run_id=run_id,
                identity=identity,
                previous_state=previous_state,
                next_state=next_state,
                disposition=disposition,
                observed_features=observed,
                reasons=reasons,
                evidence_artifact_ids=evidence_ids,
                missing_features=missing,
                exit_reason=exit_reason,
            )
        except InvalidAttentionError:
            return None
        if card.card_id != root["card_id"] or card.to_payload() != root:
            return None
        cards.append(card)
    result = tuple(cards)
    if tuple(sorted(result, key=lambda card: canonical_instrument_bytes(card.identity))) != result:
        return None
    return result


def _parse_requests(
    value: object,
    run_id: object,
    cards: tuple[CandidateCard, ...] | None,
) -> tuple[DossierRequest, ...] | None:
    if type(value) is not list or type(run_id) is not str or cards is None:
        return None
    cards_by_id = {card.card_id: card for card in cards}
    requests: list[DossierRequest] = []
    for item in value:
        root = _exact_mapping(
            item,
            {"request_id", "candidate_card_id", "identity", "selection_kind"},
        )
        if root is None or type(root["candidate_card_id"]) is not str:
            return None
        identity = parse_instrument_identity(root["identity"])
        kind = _enum(DossierSelectionKind, root["selection_kind"])
        card = cards_by_id.get(root["candidate_card_id"])
        if identity is None or kind is None or card is None or card.identity != identity:
            return None
        request = DossierRequest.create(run_id=run_id, card=card, selection_kind=kind)
        if request.request_id != root["request_id"] or request.to_payload() != root:
            return None
        requests.append(request)
    result = tuple(requests)
    if tuple(sorted(result, key=lambda item: canonical_instrument_bytes(item.identity))) != result:
        return None
    return result


def _parse_refreshes(  # noqa: PLR0911 - fail closed at each hostile refresh field.
    value: object,
    run_id: object,
) -> tuple[HoldingRefresh, ...] | None:
    if type(value) is not list or type(run_id) is not str:
        return None
    refreshes: list[HoldingRefresh] = []
    for item in value:
        root = _exact_mapping(
            item,
            {
                "refresh_id",
                "identity",
                "disposition",
                "exclusion_reasons",
                "evidence_artifact_ids",
                "missing_features",
            },
        )
        if root is None:
            return None
        identity = parse_instrument_identity(root["identity"])
        disposition = _enum(HoldingRefreshDisposition, root["disposition"])
        exclusions = _enum_tuple(UniverseExclusionReason, root["exclusion_reasons"])
        evidence_ids = _parse_hash_tuple(root["evidence_artifact_ids"])
        missing = _enum_tuple(AttentionFeature, root["missing_features"])
        if (
            identity is None
            or disposition is None
            or exclusions is None
            or evidence_ids is None
            or missing is None
        ):
            return None
        material = {
            "identity_kind": "holding_refresh",
            "schema_version": _SCHEMA_VERSION,
            "run_id": run_id,
            "identity": identity.to_payload(),
            "disposition": disposition.value,
            "exclusion_reasons": [reason.value for reason in exclusions],
            "evidence_artifact_ids": list(evidence_ids),
            "missing_features": [feature.value for feature in missing],
        }
        try:
            refresh = HoldingRefresh(
                _content_hash(material),
                identity,
                disposition,
                exclusions,
                evidence_ids,
                missing,
            )
        except InvalidAttentionError:
            return None
        if refresh.refresh_id != root["refresh_id"] or refresh.to_payload() != root:
            return None
        refreshes.append(refresh)
    result = tuple(refreshes)
    if tuple(sorted(result, key=lambda item: canonical_instrument_bytes(item.identity))) != result:
        return None
    return result


def _parse_resource_accounting(value: object) -> ResourceAccounting | None:
    fields = {
        "candidate_card_count",
        "new_dossier_count",
        "holding_refresh_count",
        "candidate_card_limit",
        "new_dossier_limit",
        "weekly_dossier_budget",
        "weekly_dossiers_before",
        "weekly_dossiers_after",
        "weekly_exploration_budget",
        "weekly_exploration_before",
        "weekly_exploration_after",
        "model_tokens",
        "model_turns",
        "adapter_quota_disposition",
    }
    root = _exact_mapping(value, fields)
    if root is None:
        return None
    quota = _enum(AdapterQuotaDisposition, root["adapter_quota_disposition"])
    integer_fields = fields - {"adapter_quota_disposition"}
    integers = _integer_mapping(root, integer_fields)
    if quota is None or integers is None:
        return None
    try:
        return ResourceAccounting(
            candidate_card_count=integers["candidate_card_count"],
            new_dossier_count=integers["new_dossier_count"],
            holding_refresh_count=integers["holding_refresh_count"],
            candidate_card_limit=integers["candidate_card_limit"],
            new_dossier_limit=integers["new_dossier_limit"],
            weekly_dossier_budget=integers["weekly_dossier_budget"],
            weekly_dossiers_before=integers["weekly_dossiers_before"],
            weekly_dossiers_after=integers["weekly_dossiers_after"],
            weekly_exploration_budget=integers["weekly_exploration_budget"],
            weekly_exploration_before=integers["weekly_exploration_before"],
            weekly_exploration_after=integers["weekly_exploration_after"],
            model_tokens=integers["model_tokens"],
            model_turns=integers["model_turns"],
            adapter_quota_disposition=quota,
        )
    except InvalidAttentionError:
        return None


def _exact_mapping(value: object, fields: set[str]) -> dict[str, object] | None:
    if type(value) is not dict or any(type(key) is not str for key in value):
        return None
    if set(value) != fields:
        return None
    return value


def _integer_mapping(value: dict[str, object], fields: set[str]) -> dict[str, int] | None:
    result: dict[str, int] = {}
    for field in fields:
        item = value[field]
        if type(item) is not int:
            return None
        result[field] = item
    return result


def _enum[T: StrEnum](enum_type: type[T], value: object) -> T | None:
    if type(value) is not str:
        return None
    try:
        return enum_type(value)
    except ValueError:
        return None


def _optional_enum[T: StrEnum](enum_type: type[T], value: object) -> tuple[bool, T | None]:
    if value is None:
        return True, None
    parsed = _enum(enum_type, value)
    return parsed is not None, parsed


def _enum_tuple[T: StrEnum](enum_type: type[T], value: object) -> tuple[T, ...] | None:
    if type(value) is not list:
        return None
    parsed: list[T] = []
    for item in value:
        member = _enum(enum_type, item)
        if member is None:
            return None
        parsed.append(member)
    result = tuple(parsed)
    if tuple(sorted(set(result), key=lambda member: member.value)) != result:
        return None
    return result


def _parse_hash_tuple(value: object) -> tuple[str, ...] | None:
    if type(value) is not list or any(not _is_sha256(item) for item in value):
        return None
    result = tuple(value)
    return result if tuple(sorted(set(result))) == result else None


def _parse_instant(value: object) -> UtcInstant | None:
    try:
        return UtcInstant.parse(value)
    except InvalidUtcInstantError:
        return None


def _policy_values_are_valid(
    candidate_limit: object,
    dossier_limit: object,
    weekly_budget: object,
    exploration_budget: object,
    seed: object,
) -> bool:
    return (
        _parse_policy_values(
            candidate_limit,
            dossier_limit,
            weekly_budget,
            exploration_budget,
            seed,
        )
        is not None
    )


def _parse_policy_values(
    candidate_limit: object,
    dossier_limit: object,
    weekly_budget: object,
    exploration_budget: object,
    seed: object,
) -> tuple[int, int, int, int, str] | None:
    if not (
        type(candidate_limit) is int
        and 1 <= candidate_limit <= _MAXIMUM_CANDIDATE_CARDS
        and type(dossier_limit) is int
        and 1 <= dossier_limit <= _MAXIMUM_NEW_DOSSIERS
        and dossier_limit <= candidate_limit
        and type(weekly_budget) is int
        and dossier_limit <= weekly_budget <= _MAXIMUM_WEEKLY_DOSSIERS
        and type(exploration_budget) is int
        and 1 <= exploration_budget < weekly_budget
        and 10 * weekly_budget <= 100 * exploration_budget <= 20 * weekly_budget
        and type(seed) is str
        and _IDENTIFIER.fullmatch(seed) is not None
    ):
        return None
    return candidate_limit, dossier_limit, weekly_budget, exploration_budget, seed


def _week_key(cycle: MarketSession) -> str:
    iso_year, iso_week, _ = cycle.trading_date.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def _is_sha256(value: object) -> TypeGuard[str]:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _content_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
