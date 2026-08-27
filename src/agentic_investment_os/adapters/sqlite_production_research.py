"""Persist production research model-call intent and observations in runtime SQLite."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from agentic_investment_os.domain.attention import (
    HoldingRefreshDisposition,
    parse_attention_artifact,
)
from agentic_investment_os.domain.governance import ConstitutionArtifact
from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    parse_instrument_identity,
)
from agentic_investment_os.domain.lifecycle import (
    LifecyclePersistenceError,
    LifecyclePhase,
    NoActionReason,
    ProductionResearchReference,
    ResearchCheckpoint,
    is_sha256,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.evidence.capture import (
    EvidenceFeed,
    EvidencePersistenceError,
    EvidenceVault,
    InvalidEvidenceError,
    parse_evidence_artifact,
)
from agentic_investment_os.memory.admission import (
    BeliefClaimKind,
    BeliefEvidenceReference,
    BeliefEvidenceRelationship,
    RecordRefusalCode,
)
from agentic_investment_os.memory.beliefs import (
    BeliefEvidenceResolver,
    BeliefGraphRefusal,
    BeliefLedger,
    BeliefLifecycleReference,
    BeliefPersistenceError,
    parse_belief_graph_query,
)
from agentic_investment_os.research.authority import ResearchAuthority
from agentic_investment_os.research.dossier import (
    Dossier,
    DossierRefusalReason,
    parse_dossier,
    parse_stored_dossier,
)
from agentic_investment_os.research.model import (
    MAXIMUM_MODEL_OUTPUT_BYTES,
    LabCallObservation,
    LabCallPreparationDisposition,
    LabObservationDisposition,
    ModelCallDisposition,
    ModelCallResponse,
    ModelTimingDisposition,
    ResearchRole,
    observation_matches_role,
)
from agentic_investment_os.research.production import (
    ProductionCallIntent,
    ProductionCallPreparation,
    ProductionModelResponseRecord,
    ProductionResearchEvidence,
    production_call_refusal_identity,
    production_response_disposition,
)
from agentic_investment_os.research.resolution import (
    CioRefusalReason,
    CioResolution,
    CioStance,
    ForecastRefusalReason,
    ScenarioForecast,
    SkepticDecision,
    SkepticRefusalReason,
    SkepticResult,
    Thesis,
    ThesisRefusalReason,
    parse_cio_resolution,
    parse_scenario_forecast,
    parse_skeptic_result,
    parse_stored_cio_resolution,
    parse_stored_scenario_forecast,
    parse_stored_skeptic_result,
    parse_stored_thesis,
    parse_thesis,
)

__all__ = ("SQLiteProductionCallLedger",)

if TYPE_CHECKING:
    from pathlib import Path

    from agentic_investment_os.research.policy import ModelConfiguration, ProductionResearchPolicy

_CORRUPT = "invalid production research call history"
_WRITE_FAILED = "production research call persistence failed"
_BEGIN_IMMEDIATE = "BEGIN IMMEDIATE"
_INTENT_COLUMN_COUNT = 7
_MEMORY_REFUSAL_REASONS = (
    "current_belief_history_unavailable",
    "unknown_record_refusal",
    *(reason.value for reason in RecordRefusalCode),
)


@dataclass(frozen=True, slots=True)
class _ReconstructedPhase:
    refusal_id: str | None = None
    terminal_call_id: str | None = None
    active_resolutions: tuple[tuple[str, Dossier, Thesis, CioResolution], ...] = ()
    no_action_reason: NoActionReason | None = None


class _ProductionEvidenceVault(EvidenceVault, BeliefEvidenceResolver, Protocol):
    """Expose exact research records and evidence-bound belief resolution."""


class SQLiteProductionCallLedger:
    """Append and replay exact production research effects in the runtime database."""

    def __init__(
        self,
        database: Path,
        policy: ProductionResearchPolicy,
        belief_ledger: BeliefLedger,
        evidence_vault: _ProductionEvidenceVault,
    ) -> None:
        self._database = database
        self._policy = policy
        self._belief_ledger = belief_ledger
        self._evidence_vault = evidence_vault

    def prepare_call(
        self,
        intent: ProductionCallIntent,
        recorded_at: UtcInstant,
    ) -> ProductionCallPreparation:
        intent.__post_init__()
        intent_json = _canonical_json(intent.to_payload())
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_BEGIN_IMMEDIATE)
                row = connection.execute(
                    "SELECT call_id, intent_json, intent_hash "
                    "FROM production_research_call_intents "
                    "WHERE run_id = ? AND request_id = ? AND role = ?",
                    (intent.run_id, intent.request_id, intent.role.value),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO production_research_call_intents "
                        "(call_id, run_id, request_id, role, intent_json, intent_hash, "
                        "recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            intent.call_id,
                            intent.run_id,
                            intent.request_id,
                            intent.role.value,
                            intent_json,
                            intent.content_hash,
                            recorded_at.isoformat(),
                        ),
                    )
                    return ProductionCallPreparation(LabCallPreparationDisposition.EFFECT_REQUIRED)
                call_id, stored_json, stored_hash = row
                if (
                    type(stored_json) is not str
                    or type(stored_hash) is not str
                    or hashlib.sha256(stored_json.encode()).hexdigest() != stored_hash
                    or not _is_canonical_json(stored_json)
                ):
                    raise LifecyclePersistenceError(_CORRUPT)
                if (
                    call_id != intent.call_id
                    or stored_json != intent_json
                    or stored_hash != intent.content_hash
                ):
                    return ProductionCallPreparation(LabCallPreparationDisposition.CONFLICT)
                stored_observation = self._load_observation(connection, intent)
                if stored_observation is None:
                    return ProductionCallPreparation(
                        LabCallPreparationDisposition.INDETERMINATE_EFFECT
                    )
                return ProductionCallPreparation(
                    LabCallPreparationDisposition.REPLAY,
                    stored_observation[0],
                )
        except sqlite3.Error as error:
            raise LifecyclePersistenceError(_WRITE_FAILED) from error

    def append_observation(
        self,
        intent: ProductionCallIntent,
        observation: LabCallObservation,
        response_record: ProductionModelResponseRecord,
        recorded_at: UtcInstant,
    ) -> LabCallObservation:
        observation.__post_init__()
        response_record.__post_init__()
        normalized_response = response_record.normalized_response()
        if not observation_matches_role(observation, intent.role):
            raise LifecyclePersistenceError(_WRITE_FAILED)
        observation_payload = observation.to_payload()
        observation_payload["record_kind"] = "production_research_call_observation"
        observation_payload["model_disposition"] = normalized_response.disposition.value
        observation_payload["reported_response"] = response_record.to_payload()
        observation_json = _canonical_json(observation_payload)
        observation_hash = hashlib.sha256(observation_json.encode()).hexdigest()
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_BEGIN_IMMEDIATE)
                self._require_intent(connection, intent)
                prior = self._load_observation(connection, intent)
                if prior is not None:
                    if (
                        prior[0] != observation
                        or prior[1].to_payload() != response_record.to_payload()
                    ):
                        raise LifecyclePersistenceError(_CORRUPT)
                    return prior[0]
                connection.execute(
                    "INSERT INTO production_research_call_observations "
                    "(call_id, observation_json, raw_response, observation_hash, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        intent.call_id,
                        observation_json,
                        response_record.persisted_raw_response,
                        observation_hash,
                        recorded_at.isoformat(),
                    ),
                )
                return observation
        except sqlite3.Error as error:
            raise LifecyclePersistenceError(_WRITE_FAILED) from error

    def validate_history(self, references: tuple[ProductionResearchReference, ...]) -> None:
        """Revalidate production rows against their exact lifecycle and authority owners."""
        try:
            with closing(self._connect()) as connection:
                intent_rows = connection.execute(
                    "SELECT call_id, run_id, request_id, role, intent_json, intent_hash, "
                    "recorded_at FROM production_research_call_intents ORDER BY call_id"
                ).fetchall()
                intents: dict[str, ProductionCallIntent] = {}
                intent_times: dict[str, UtcInstant] = {}
                for row in intent_rows:
                    intent = _parse_intent_row(row)
                    if intent.call_id in intents:
                        raise LifecyclePersistenceError(_CORRUPT)
                    intents[intent.call_id] = intent
                    intent_times[intent.call_id] = _canonical_instant(row[6])
                observation_rows = connection.execute(
                    "SELECT call_id, recorded_at FROM production_research_call_observations "
                    "ORDER BY call_id"
                ).fetchall()
                observations: dict[str, LabCallObservation] = {}
                for call_id_value, recorded_at_value in observation_rows:
                    if (
                        type(call_id_value) is not str
                        or call_id_value in observations
                        or call_id_value not in intents
                    ):
                        raise LifecyclePersistenceError(_CORRUPT)
                    observation_time = _canonical_instant(recorded_at_value)
                    if observation_time.value < intent_times[call_id_value].value:
                        raise LifecyclePersistenceError(_CORRUPT)
                    stored_observation = self._load_observation(
                        connection,
                        intents[call_id_value],
                    )
                    if stored_observation is None:  # pragma: no cover - row proves presence.
                        raise LifecyclePersistenceError(_CORRUPT)
                    observations[call_id_value] = stored_observation[0]
                self._validate_references(references, intents, observations)
        except (
            BeliefPersistenceError,
            EvidencePersistenceError,
            InvalidEvidenceError,
            sqlite3.Error,
        ) as error:
            raise LifecyclePersistenceError(_CORRUPT) from error

    def _validate_references(
        self,
        references: tuple[ProductionResearchReference, ...],
        intents: dict[str, ProductionCallIntent],
        observations: dict[str, LabCallObservation],
    ) -> None:
        owners: dict[tuple[str, LifecyclePhase], ProductionResearchReference] = {}
        for reference in references:
            reference.__post_init__()
            owner = (reference.pinned_run_identity.run_id, reference.phase)
            if owner in owners:
                raise LifecyclePersistenceError(_CORRUPT)
            owners[owner] = reference
        grouped: dict[tuple[str, LifecyclePhase], list[ProductionCallIntent]] = {
            owner: [] for owner in owners
        }
        for intent in intents.values():
            phase = _phase_for_role(intent.role)
            owner = (intent.run_id, phase)
            matched_reference = owners.get(owner)
            if matched_reference is None:
                raise LifecyclePersistenceError(_CORRUPT)
            self._validate_intent_authority(intent, matched_reference)
            grouped[owner].append(intent)
        if any(call_id not in intents for call_id in observations):  # pragma: no cover
            raise LifecyclePersistenceError(_CORRUPT)
        self._validate_role_predecessors(tuple(intents.values()), observations)
        dossiers = {
            (intent.run_id, intent.request_id): observation.artifact
            for call_id, observation in observations.items()
            if (intent := intents[call_id]).role is ResearchRole.EVIDENCE_COLLECTOR
            and type(observation.artifact) is Dossier
        }
        belief_references: list[BeliefLifecycleReference] = []
        for owner, reference in owners.items():
            owner_intents = tuple(sorted(grouped[owner], key=lambda item: item.call_id))
            self._validate_checkpoint(reference.checkpoint, owner_intents, observations)
            by_request = _intents_by_request(owner_intents)
            outcome = (
                self._reconstruct_build(reference, by_request, observations)
                if reference.phase is LifecyclePhase.BUILD_DOSSIERS
                else self._reconstruct_run(reference, by_request, observations, dossiers)
            )
            _validate_terminal_outcome(reference, outcome)
            if reference.memory_refusal_id is not None:
                if outcome is None or outcome.refusal_id is not None:
                    raise LifecyclePersistenceError(_CORRUPT)
                failed_index = _memory_refusal_index(reference, outcome.active_resolutions)
                belief_references.extend(
                    self._memory_references(
                        reference,
                        outcome.active_resolutions[:failed_index],
                    )
                )
            if reference.memory_checkpoint is not None:
                if outcome is None or outcome.refusal_id is not None:
                    raise LifecyclePersistenceError(_CORRUPT)
                belief_references.extend(
                    self._memory_references(reference, outcome.active_resolutions)
                )
                expected_event_ids = tuple(
                    sorted(item.event_id for item in belief_references if item.run_id == owner[0])
                )
                if (
                    reference.memory_checkpoint.artifact_ids != expected_event_ids
                    or reference.no_action_reason != outcome.no_action_reason
                ):
                    raise LifecyclePersistenceError(_CORRUPT)
        if belief_references:
            self._belief_ledger.validate_lifecycle_references(
                tuple(belief_references),
                self._evidence_vault,
            )

    @staticmethod
    def _validate_checkpoint(
        checkpoint: ResearchCheckpoint | None,
        owner_intents: tuple[ProductionCallIntent, ...],
        observations: dict[str, LabCallObservation],
    ) -> None:
        if checkpoint is None:
            return
        owner_call_ids = tuple(intent.call_id for intent in owner_intents)
        owner_observations = tuple(
            observations[call_id] for call_id in owner_call_ids if call_id in observations
        )
        if len(owner_observations) != len(owner_call_ids):
            raise LifecyclePersistenceError(_CORRUPT)
        artifact_ids = tuple(
            sorted(
                observation.artifact.content_hash
                for observation in owner_observations
                if observation.artifact is not None
            )
        )
        expected = ResearchCheckpoint(
            artifact_ids,
            owner_call_ids,
            sum(item.input_tokens for item in owner_observations),
            sum(item.output_tokens for item in owner_observations),
            sum(item.turns for item in owner_observations),
        )
        if checkpoint != expected:
            raise LifecyclePersistenceError(_CORRUPT)

    def _reconstruct_build(
        self,
        reference: ProductionResearchReference,
        by_request: dict[str, dict[ResearchRole, ProductionCallIntent]],
        observations: dict[str, LabCallObservation],
    ) -> _ReconstructedPhase | None:
        request_ids = _request_ids(reference)
        for index, request_id in enumerate(request_ids):
            subject_owner = _subject_owner(reference, request_id)
            if subject_owner is None:  # pragma: no cover - typed attention proves ownership.
                raise LifecyclePersistenceError(_CORRUPT)
            evidence = _expected_subject_evidence(self._evidence_vault, subject_owner[1])
            if (
                not evidence
                or len(evidence) > self._policy.maximum_evidence_artifacts
                or not any(item.is_official for item in evidence)
            ):
                _require_no_later_intents(by_request, request_ids, index, frozenset())
                return _ReconstructedPhase(
                    _input_refusal_identity(
                        reference.pinned_run_identity.run_id,
                        request_id,
                        "invalid_subject_evidence_coverage",
                    )
                )
            roles = by_request.get(request_id, {})
            intent = roles.get(ResearchRole.EVIDENCE_COLLECTOR)
            if intent is None:
                _require_no_later_intents(by_request, request_ids, index, frozenset())
                return None
            observation = observations.get(intent.call_id)
            if observation is None:
                _require_no_later_intents(
                    by_request,
                    request_ids,
                    index,
                    frozenset({ResearchRole.EVIDENCE_COLLECTOR}),
                )
                return None
            if type(observation.artifact) is not Dossier:
                _require_no_later_intents(
                    by_request,
                    request_ids,
                    index,
                    frozenset({ResearchRole.EVIDENCE_COLLECTOR}),
                )
                return _call_failure_outcome(intent, observation)
        return _ReconstructedPhase()

    def _reconstruct_run(  # noqa: PLR0911, PLR0912, PLR0915 - mirror terminal paths.
        self,
        reference: ProductionResearchReference,
        by_request: dict[str, dict[ResearchRole, ProductionCallIntent]],
        observations: dict[str, LabCallObservation],
        dossiers: dict[tuple[str, str], Dossier],
    ) -> _ReconstructedPhase | None:
        request_ids = _request_ids(reference)
        active: list[tuple[str, Dossier, Thesis, CioResolution]] = []
        no_action: NoActionReason | None = None
        run_id = reference.pinned_run_identity.run_id
        if not request_ids:
            return _ReconstructedPhase(no_action_reason=NoActionReason.NO_ATTENTION)
        for index, request_id in enumerate(request_ids):
            dossier = dossiers.get((run_id, request_id))
            if dossier is None:
                raise LifecyclePersistenceError(_CORRUPT)
            roles = by_request.get(request_id, {})
            allowed: set[ResearchRole] = set()
            thesis_result = _role_artifact(
                roles,
                observations,
                ResearchRole.THESIS_BUILDER,
                Thesis,
            )
            allowed.add(ResearchRole.THESIS_BUILDER)
            if thesis_result is None:
                _require_no_later_intents(by_request, request_ids, index, frozenset(allowed))
                return None
            thesis_intent, thesis_observation, thesis = thesis_result
            if thesis is None:
                _require_no_later_intents(by_request, request_ids, index, frozenset(allowed))
                return _call_failure_outcome(thesis_intent, thesis_observation)
            if any(condition.active for condition in thesis.uninvestable_conditions):
                _require_no_current_roles(roles, frozenset(allowed))
                no_action = NoActionReason.NO_VALID_THESIS
                continue
            skeptic_result = _role_artifact(
                roles,
                observations,
                ResearchRole.INDEPENDENT_SKEPTIC,
                SkepticResult,
            )
            allowed.add(ResearchRole.INDEPENDENT_SKEPTIC)
            if skeptic_result is None:
                _require_no_later_intents(by_request, request_ids, index, frozenset(allowed))
                return None
            skeptic_intent, skeptic_observation, skeptic = skeptic_result
            if skeptic is None:
                _require_no_later_intents(by_request, request_ids, index, frozenset(allowed))
                return _call_failure_outcome(skeptic_intent, skeptic_observation)
            if skeptic.decision is SkepticDecision.REJECT:
                _require_no_current_roles(roles, frozenset(allowed))
                no_action = NoActionReason.SKEPTIC_REJECTED
                continue
            if skeptic.decision is SkepticDecision.REQUEST_EVIDENCE:
                _require_no_later_intents(by_request, request_ids, index, frozenset(allowed))
                return _ReconstructedPhase(
                    _input_refusal_identity(run_id, request_id, "skeptic_requested_evidence")
                )
            forecast_result = _role_artifact(
                roles,
                observations,
                ResearchRole.SCENARIO_FORECASTER,
                ScenarioForecast,
            )
            allowed.add(ResearchRole.SCENARIO_FORECASTER)
            if forecast_result is None:
                _require_no_later_intents(by_request, request_ids, index, frozenset(allowed))
                return None
            forecast_intent, forecast_observation, forecast = forecast_result
            if forecast is None:
                _require_no_later_intents(by_request, request_ids, index, frozenset(allowed))
                return _call_failure_outcome(forecast_intent, forecast_observation)
            cio_result = _role_artifact(
                roles,
                observations,
                ResearchRole.CIO,
                CioResolution,
            )
            allowed.add(ResearchRole.CIO)
            if cio_result is None:
                _require_no_later_intents(by_request, request_ids, index, frozenset(allowed))
                return None
            cio_intent, cio_observation, cio = cio_result
            if cio is None:
                _require_no_later_intents(by_request, request_ids, index, frozenset(allowed))
                return _call_failure_outcome(cio_intent, cio_observation)
            _require_no_current_roles(roles, frozenset(allowed))
            if cio.stance is CioStance.ABSTAIN:
                no_action = NoActionReason.CIO_ABSTAINED
            else:
                active.append((request_id, dossier, thesis, cio))
        if active:
            no_action = None
        return _ReconstructedPhase(
            active_resolutions=tuple(active),
            no_action_reason=no_action,
        )

    def _memory_references(
        self,
        reference: ProductionResearchReference,
        active: tuple[tuple[str, Dossier, Thesis, CioResolution], ...],
    ) -> tuple[BeliefLifecycleReference, ...]:
        recorded_at = reference.memory_recorded_at
        if recorded_at is None:  # pragma: no cover - reference invariant rejects this.
            raise LifecyclePersistenceError(_CORRUPT)
        expected: list[BeliefLifecycleReference] = []
        for request_id, dossier, thesis, cio in active:
            assertions = {
                assertion.assertion_id: assertion
                for assertion in (*dossier.facts, *dossier.interpretations)
            }
            try:
                supporting_ids = tuple(
                    sorted(
                        {
                            citation
                            for assertion_id in thesis.variant_view.supporting_assertion_ids
                            for citation in assertions[assertion_id].citation_artifact_ids
                        }
                    )
                )
            except KeyError as error:  # pragma: no cover - parsed thesis binds the Dossier.
                raise LifecyclePersistenceError(_CORRUPT) from error
            owner = _subject_owner(reference, request_id)
            if owner is None:  # pragma: no cover - reference authority validates this.
                raise LifecyclePersistenceError(_CORRUPT)
            records = {
                item.artifact_id: item
                for item in _expected_subject_evidence(self._evidence_vault, owner[1])
            }
            contradicting_ids = thesis.variant_view.contradicting_artifact_ids
            try:
                evidence = tuple(
                    sorted(
                        (
                            *(
                                BeliefEvidenceReference(
                                    artifact_id,
                                    records[artifact_id].content_hash,
                                    BeliefEvidenceRelationship.SUPPORTING,
                                )
                                for artifact_id in supporting_ids
                            ),
                            *(
                                BeliefEvidenceReference(
                                    artifact_id,
                                    records[artifact_id].content_hash,
                                    BeliefEvidenceRelationship.CONTRADICTING,
                                )
                                for artifact_id in contradicting_ids
                            ),
                        ),
                        key=lambda item: (item.artifact_id, item.relationship.value),
                    )
                )
            except KeyError as error:
                raise LifecyclePersistenceError(_CORRUPT) from error
            belief_id, event_id = _belief_event_identity(cio)
            expected.append(
                BeliefLifecycleReference(
                    run_id=reference.pinned_run_identity.run_id,
                    cio_resolution_id=cio.content_hash,
                    event_id=event_id,
                    belief_id=belief_id,
                    subject=cio.subject,
                    claim=thesis.variant_view.text,
                    valid_at=reference.pinned_run_identity.evidence_cutoff,
                    evidence_cutoff=reference.pinned_run_identity.evidence_cutoff,
                    confidence={"low": "0.75", "medium": "0.5", "high": "0.25"}[cio.uncertainty],
                    evidence=evidence,
                    falsifiers=tuple(sorted(claim.text for claim in thesis.invalidators)),
                    lifecycle_recorded_at=recorded_at,
                )
            )
        return tuple(expected)

    def _validate_intent_authority(
        self,
        intent: ProductionCallIntent,
        reference: ProductionResearchReference,
    ) -> None:
        try:
            model_input = json.loads(intent.model_input_json)
        except json.JSONDecodeError as error:  # pragma: no cover - intent parser rejects this.
            raise LifecyclePersistenceError(_CORRUPT) from error
        if type(model_input) is not dict:
            raise LifecyclePersistenceError(_CORRUPT)
        identity = reference.pinned_run_identity
        attention = parse_attention_artifact(model_input.get("attention_artifact"))
        constitution = ConstitutionArtifact.parse(model_input.get("constitution"))
        subject = parse_instrument_identity(model_input.get("subject"))
        subject_owner = _subject_owner(reference, intent.request_id)
        expected_subject = None if subject_owner is None else subject_owner[0]
        contract = self._policy.contract_for(intent.role)
        if (
            attention != reference.attention_artifact
            or constitution is None
            or constitution.content_hash != identity.constitution_hash
            or model_input.get("portfolio_context") != reference.position_snapshot.to_payload()
            or model_input.get("run_id") != identity.run_id
            or model_input.get("evidence_cutoff") != identity.evidence_cutoff.isoformat()
            or model_input.get("data_regime") != identity.data_regime
            or subject is None
            or expected_subject is None
            or subject != expected_subject
            or model_input.get("prompt") != contract.prompt.to_payload()
            or model_input.get("model_configuration") != contract.model_configuration.to_payload()
            or model_input.get("tools") != [tool.to_payload() for tool in contract.tools]
        ):
            raise LifecyclePersistenceError(_CORRUPT)
        evidence = _parse_intent_evidence(model_input.get("evidence"), subject)
        if subject_owner is None:  # pragma: no cover - expected_subject gate rejects this above.
            raise LifecyclePersistenceError(_CORRUPT)
        evidence_ids = subject_owner[1]
        expected_evidence = _expected_subject_evidence(self._evidence_vault, evidence_ids)
        if (
            evidence is None
            or evidence != expected_evidence
            or len(evidence) > self._policy.maximum_evidence_artifacts
            or not any(item.is_official for item in evidence)
        ):
            raise LifecyclePersistenceError(_CORRUPT)
        graph_payload = model_input.get("belief_graph")
        if type(graph_payload) is not dict:
            raise LifecyclePersistenceError(_CORRUPT)
        query = parse_belief_graph_query(graph_payload.get("query"))
        if (
            query is None
            or query.cutoff != identity.evidence_cutoff
            or query.subjects != (subject,)
            or query.maximum_belief_events != self._policy.maximum_belief_events
            or query.maximum_evidence_artifacts != self._policy.maximum_evidence_artifacts
        ):
            raise LifecyclePersistenceError(_CORRUPT)
        graph = self._belief_ledger.rebuild_graph(query, self._evidence_vault)
        if isinstance(graph, BeliefGraphRefusal) or graph.to_payload() != graph_payload:
            raise LifecyclePersistenceError(_CORRUPT)
        expected_material_hashes = {
            attention.content_hash,
            constitution.content_hash,
            reference.position_snapshot.fingerprint,
            graph.content_hash,
            contract.prompt.fingerprint,
            contract.model_configuration.content_hash,
            *(tool.fingerprint for tool in contract.tools),
            *(item.content_hash for item in evidence),
        }
        for field in ("dossier", "thesis", "skeptic", "forecast"):
            artifact = model_input.get(field)
            if type(artifact) is dict:
                content_hash = artifact.get("content_hash")
                if not is_sha256(content_hash):
                    raise LifecyclePersistenceError(_CORRUPT)
                expected_material_hashes.add(content_hash)
        if intent.material_input_hashes != tuple(
            sorted(expected_material_hashes)
        ) or model_input.get("material_input_hashes") != sorted(expected_material_hashes):
            raise LifecyclePersistenceError(_CORRUPT)

    def _validate_role_predecessors(
        self,
        intents: tuple[ProductionCallIntent, ...],
        observations: dict[str, LabCallObservation],
    ) -> None:
        by_request: dict[str, dict[ResearchRole, ProductionCallIntent]] = {}
        for intent in intents:
            roles = by_request.setdefault(intent.request_id, {})
            if intent.role in roles:
                raise LifecyclePersistenceError(_CORRUPT)
            roles[intent.role] = intent
        order = (
            ResearchRole.EVIDENCE_COLLECTOR,
            ResearchRole.THESIS_BUILDER,
            ResearchRole.INDEPENDENT_SKEPTIC,
            ResearchRole.SCENARIO_FORECASTER,
            ResearchRole.CIO,
        )
        fields = ("dossier", "thesis", "skeptic", "forecast")
        for roles in by_request.values():
            prior_artifacts: list[object] = []
            for index, role in enumerate(order):
                candidate = roles.get(role)
                if candidate is None:
                    if any(later in roles for later in order[index + 1 :]):
                        raise LifecyclePersistenceError(_CORRUPT)
                    break
                model_input = json.loads(candidate.model_input_json)
                for field_index, field in enumerate(fields):
                    expected = (
                        prior_artifacts[field_index] if field_index < len(prior_artifacts) else None
                    )
                    if model_input[field] != expected:
                        raise LifecyclePersistenceError(_CORRUPT)
                observation = observations.get(candidate.call_id)
                if observation is None or observation.artifact is None:
                    if any(later in roles for later in order[index + 1 :]):
                        raise LifecyclePersistenceError(_CORRUPT)
                    break
                prior_artifacts.append(observation.artifact.to_payload())

    def _require_intent(
        self,
        connection: sqlite3.Connection,
        intent: ProductionCallIntent,
    ) -> None:
        row = connection.execute(
            "SELECT intent_json, intent_hash FROM production_research_call_intents "
            "WHERE call_id = ?",
            (intent.call_id,),
        ).fetchone()
        if row != (_canonical_json(intent.to_payload()), intent.content_hash):
            raise LifecyclePersistenceError(_CORRUPT)

    def _load_observation(
        self,
        connection: sqlite3.Connection,
        intent: ProductionCallIntent,
    ) -> tuple[LabCallObservation, ProductionModelResponseRecord] | None:
        row = connection.execute(
            "SELECT observation_json, raw_response, observation_hash, recorded_at "
            "FROM production_research_call_observations WHERE call_id = ?",
            (intent.call_id,),
        ).fetchone()
        if row is None:
            return None
        observation_json, raw_response, observation_hash, recorded_at = row
        if (
            type(observation_json) is not str
            or type(observation_hash) is not str
            or hashlib.sha256(observation_json.encode()).hexdigest() != observation_hash
            or not _is_canonical_json(observation_json)
            or (raw_response is not None and type(raw_response) is not bytes)
        ):
            raise LifecyclePersistenceError(_CORRUPT)
        _canonical_instant(recorded_at)
        return _parse_observation(
            observation_json,
            raw_response,
            intent,
            self._policy.contract_for(intent.role).model_configuration,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


def _phase_for_role(role: ResearchRole) -> LifecyclePhase:
    return (
        LifecyclePhase.BUILD_DOSSIERS
        if role is ResearchRole.EVIDENCE_COLLECTOR
        else LifecyclePhase.RUN_RESEARCH
    )


def _artifact_is_official(feed: EvidenceFeed) -> bool:
    return feed in (
        EvidenceFeed.SEC_EDGAR,
        EvidenceFeed.ISSUER_INVESTOR_RELATIONS,
        EvidenceFeed.FEDERAL_RESERVE,
        EvidenceFeed.BLS,
        EvidenceFeed.BEA,
    )


def _expected_subject_evidence(
    vault: _ProductionEvidenceVault,
    evidence_ids: tuple[str, ...],
) -> tuple[ProductionResearchEvidence, ...]:
    records = vault.stored_records_for_artifacts(evidence_ids)
    records_by_id = {record.artifact.artifact_id: record for record in records}
    if set(records_by_id) != set(evidence_ids):
        raise LifecyclePersistenceError(_CORRUPT)
    return tuple(
        ProductionResearchEvidence(
            record.artifact.artifact_id,
            record.artifact.content_hash,
            record.artifact.available_at,
            _canonical_json(record.artifact.to_payload()),
            record.content,
            _artifact_is_official(record.artifact.feed),
        )
        for record in (records_by_id[artifact_id] for artifact_id in evidence_ids)
    )


def _request_ids(reference: ProductionResearchReference) -> tuple[str, ...]:
    return tuple(
        sorted(
            (
                *(request.request_id for request in reference.attention_artifact.dossier_requests),
                *(
                    refresh.refresh_id
                    for refresh in reference.attention_artifact.holding_refreshes
                    if refresh.disposition is HoldingRefreshDisposition.REQUIRED
                ),
            )
        )
    )


def _intents_by_request(
    intents: tuple[ProductionCallIntent, ...],
) -> dict[str, dict[ResearchRole, ProductionCallIntent]]:
    grouped: dict[str, dict[ResearchRole, ProductionCallIntent]] = {}
    for intent in intents:
        roles = grouped.setdefault(intent.request_id, {})
        if intent.role in roles:
            raise LifecyclePersistenceError(_CORRUPT)
        roles[intent.role] = intent
    return grouped


def _role_artifact[ArtifactT: Dossier | Thesis | SkepticResult | ScenarioForecast | CioResolution](
    roles: dict[ResearchRole, ProductionCallIntent],
    observations: dict[str, LabCallObservation],
    role: ResearchRole,
    expected_type: type[ArtifactT],
) -> tuple[ProductionCallIntent, LabCallObservation, ArtifactT | None] | None:
    intent = roles.get(role)
    if intent is None:
        return None
    observation = observations.get(intent.call_id)
    if observation is None:
        return None
    artifact = observation.artifact
    return (
        (intent, observation, artifact)
        if isinstance(artifact, expected_type)
        else (intent, observation, None)
    )


def _require_no_current_roles(
    roles: dict[ResearchRole, ProductionCallIntent],
    allowed: frozenset[ResearchRole],
) -> None:
    if not set(roles).issubset(allowed):
        raise LifecyclePersistenceError(_CORRUPT)


def _require_no_later_intents(
    by_request: dict[str, dict[ResearchRole, ProductionCallIntent]],
    request_ids: tuple[str, ...],
    index: int,
    allowed_current: frozenset[ResearchRole],
) -> None:
    _require_no_current_roles(by_request.get(request_ids[index], {}), allowed_current)
    if any(by_request.get(request_id) for request_id in request_ids[index + 1 :]):
        raise LifecyclePersistenceError(_CORRUPT)


def _call_failure_outcome(
    intent: ProductionCallIntent,
    observation: LabCallObservation,
) -> _ReconstructedPhase:
    return _ReconstructedPhase(
        production_call_refusal_identity(intent.call_id, observation),
        intent.call_id,
    )


def _input_refusal_identity(run_id: str, request_id: str, reason: str) -> str:
    return _content_hash({"run_id": run_id, "request_id": request_id, "reason": reason})


def _belief_event_identity(cio: CioResolution) -> tuple[str, str]:
    belief_id = _content_hash(
        {
            "subject": cio.subject.to_payload(),
            "claim_kind": BeliefClaimKind.EXPECTATION.value,
        }
    )
    return belief_id, _content_hash({"cio_resolution_id": cio.content_hash, "belief_id": belief_id})


def _memory_refusal_index(
    reference: ProductionResearchReference,
    active: tuple[tuple[str, Dossier, Thesis, CioResolution], ...],
) -> int:
    refusal_id = reference.memory_refusal_id
    if refusal_id is None:  # pragma: no cover - caller narrows the reference.
        raise LifecyclePersistenceError(_CORRUPT)
    matches = tuple(
        index
        for index, (_, _, _, cio) in enumerate(active)
        for reason in _MEMORY_REFUSAL_REASONS
        if refusal_id
        == _content_hash(
            {
                "run_id": reference.pinned_run_identity.run_id,
                "event_id": _belief_event_identity(cio)[1],
                "reason": reason,
            }
        )
    )
    if len(matches) != 1:
        raise LifecyclePersistenceError(_CORRUPT)
    return matches[0]


def _validate_terminal_outcome(
    reference: ProductionResearchReference,
    outcome: _ReconstructedPhase | None,
) -> None:
    if reference.checkpoint is None:
        return
    if (
        outcome is None
        or reference.refusal_id != outcome.refusal_id
        or reference.terminal_call_id != outcome.terminal_call_id
    ):
        raise LifecyclePersistenceError(_CORRUPT)


def _subject_owner(
    reference: ProductionResearchReference,
    request_id: str,
) -> tuple[EquityInstrumentIdentity, tuple[str, ...]] | None:
    cards = {card.card_id: card for card in reference.attention_artifact.candidate_cards}
    for request in reference.attention_artifact.dossier_requests:
        card = cards.get(request.candidate_card_id)
        if request.request_id == request_id and card is not None:
            return (
                (request.identity, card.evidence_artifact_ids)
                if type(request.identity) is EquityInstrumentIdentity
                else None
            )
    for refresh in reference.attention_artifact.holding_refreshes:
        if (
            refresh.refresh_id == request_id
            and refresh.disposition is HoldingRefreshDisposition.REQUIRED
        ):
            return (
                (refresh.identity, refresh.evidence_artifact_ids)
                if type(refresh.identity) is EquityInstrumentIdentity
                else None
            )
    return None


def _parse_intent_evidence(  # noqa: PLR0911 - reject each hostile evidence mismatch locally.
    value: object,
    subject: EquityInstrumentIdentity,
) -> tuple[ProductionResearchEvidence, ...] | None:
    fields = {
        "artifact_id",
        "content_hash",
        "available_at",
        "subject",
        "content",
        "provenance",
    }
    if type(value) is not list or not value:
        return None
    parsed: list[ProductionResearchEvidence] = []
    try:
        for item in value:
            if type(item) is not dict or set(item) != fields:
                return None
            if parse_instrument_identity(item["subject"]) != subject:
                return None
            artifact = parse_evidence_artifact(item["provenance"])
            available_at = UtcInstant.parse(item["available_at"])
            if (
                artifact is None
                or type(item["artifact_id"]) is not str
                or type(item["content_hash"]) is not str
                or type(item["content"]) is not str
                or artifact.artifact_id != item["artifact_id"]
                or artifact.content_hash != item["content_hash"]
                or artifact.available_at != available_at
                or not (
                    any(mapping.identity == subject for mapping in artifact.entity_mappings)
                    or artifact.feed
                    in (EvidenceFeed.FEDERAL_RESERVE, EvidenceFeed.BLS, EvidenceFeed.BEA)
                )
            ):
                return None
            parsed.append(
                ProductionResearchEvidence(
                    item["artifact_id"],
                    item["content_hash"],
                    available_at,
                    _canonical_json(item["provenance"]),
                    item["content"].encode(),
                    _artifact_is_official(artifact.feed),
                )
            )
    except (InvalidUtcInstantError, TypeError, ValueError):
        return None
    result = tuple(parsed)
    if tuple(sorted(result, key=lambda item: item.artifact_id)) != result:
        return None
    return result


def _parse_intent_row(row: tuple[object, ...]) -> ProductionCallIntent:
    if len(row) != _INTENT_COLUMN_COUNT:
        raise LifecyclePersistenceError(_CORRUPT)
    call_id, run_id, request_id, role_value, intent_json, intent_hash, recorded_at = row
    if (
        type(call_id) is not str
        or type(run_id) is not str
        or type(request_id) is not str
        or type(role_value) is not str
        or type(intent_json) is not str
        or type(intent_hash) is not str
        or not is_sha256(call_id)
        or not is_sha256(run_id)
        or not is_sha256(request_id)
        or not is_sha256(intent_hash)
        or not _is_canonical_json(intent_json)
        or hashlib.sha256(intent_json.encode()).hexdigest() != intent_hash
    ):
        raise LifecyclePersistenceError(_CORRUPT)
    _canonical_instant(recorded_at)
    try:
        role = ResearchRole(role_value)
        payload = json.loads(intent_json)
    except (ValueError, RecursionError) as error:
        raise LifecyclePersistenceError(_CORRUPT) from error
    intent = _parse_intent_payload(payload)
    if (
        intent is None
        or intent.call_id != call_id
        or intent.run_id != run_id
        or intent.request_id != request_id
        or intent.role is not role
        or intent.content_hash != intent_hash
    ):
        raise LifecyclePersistenceError(_CORRUPT)
    return intent


def _parse_intent_payload(value: object) -> ProductionCallIntent | None:
    required = {
        "schema_version",
        "record_kind",
        "call_id",
        "run_id",
        "request_id",
        "role",
        "model_input_json",
        "model_input_hash",
        "prompt_fingerprint",
        "requested_model_identity",
        "model_configuration_fingerprint",
        "tool_fingerprints",
        "material_input_hashes",
        "maximum_output_bytes",
    }
    if type(value) is not dict or set(value) != required:
        return None
    if (
        value["schema_version"] != 1
        or value["record_kind"] != "production_research_call_intent"
        or type(value["role"]) is not str
        or type(value["model_input_json"]) is not str
        or type(value["requested_model_identity"]) is not str
        or not value["requested_model_identity"]
        or type(value["tool_fingerprints"]) is not list
        or type(value["material_input_hashes"]) is not list
    ):
        return None
    hash_fields = (
        value["call_id"],
        value["run_id"],
        value["request_id"],
        value["model_input_hash"],
        value["prompt_fingerprint"],
        value["model_configuration_fingerprint"],
    )
    tool_fingerprints = value["tool_fingerprints"]
    material_input_hashes = value["material_input_hashes"]
    if (
        any(not is_sha256(item) for item in hash_fields)
        or any(not is_sha256(item) for item in tool_fingerprints)
        or any(not is_sha256(item) for item in material_input_hashes)
    ):
        return None
    try:
        return ProductionCallIntent(
            call_id=value["call_id"],
            run_id=value["run_id"],
            request_id=value["request_id"],
            role=ResearchRole(value["role"]),
            model_input_json=value["model_input_json"],
            model_input_hash=value["model_input_hash"],
            prompt_fingerprint=value["prompt_fingerprint"],
            requested_model_identity=value["requested_model_identity"],
            model_configuration_fingerprint=value["model_configuration_fingerprint"],
            tool_fingerprints=tuple(tool_fingerprints),
            material_input_hashes=tuple(material_input_hashes),
            maximum_output_bytes=value["maximum_output_bytes"],
        )
    except (TypeError, ValueError):
        return None


def _canonical_instant(value: object) -> UtcInstant:
    if type(value) is not str:
        raise LifecyclePersistenceError(_CORRUPT)
    try:
        instant = UtcInstant.parse(value)
    except InvalidUtcInstantError as error:
        raise LifecyclePersistenceError(_CORRUPT) from error
    if instant.isoformat() != value:
        raise LifecyclePersistenceError(_CORRUPT)
    return instant


def _parse_observation(  # noqa: PLR0912 - validate every hostile record field locally.
    observation_json: str,
    raw_response: bytes | None,
    intent: ProductionCallIntent,
    configuration: ModelConfiguration,
) -> tuple[LabCallObservation, ProductionModelResponseRecord]:
    try:
        fields = json.loads(observation_json)
    except (ValueError, RecursionError) as error:
        raise LifecyclePersistenceError(_CORRUPT) from error
    required = {
        "schema_version",
        "record_kind",
        "call_id",
        "disposition",
        "model_disposition",
        "reported_response",
        "raw_response_hash",
        "raw_response_retained",
        "exposed_model_identity",
        "input_tokens",
        "output_tokens",
        "turns",
        "elapsed_milliseconds",
        "timing_disposition",
        "artifact",
        "artifact_refusal",
    }
    if (
        type(fields) is not dict
        or set(fields) != required
        or fields["schema_version"] != 1
        or fields["record_kind"] != "production_research_call_observation"
        or fields["call_id"] != intent.call_id
    ):
        raise LifecyclePersistenceError(_CORRUPT)
    try:
        disposition = LabObservationDisposition(fields["disposition"])
        model_disposition = ModelCallDisposition(fields["model_disposition"])
        timing = ModelTimingDisposition(fields["timing_disposition"])
    except (TypeError, ValueError) as error:
        raise LifecyclePersistenceError(_CORRUPT) from error
    response_record = ProductionModelResponseRecord.parse(
        fields["reported_response"],
        raw_response,
    )
    if response_record is None:
        raise LifecyclePersistenceError(_CORRUPT)
    normalized_response = response_record.normalized_response()
    raw_hash = fields["raw_response_hash"]
    retained = fields["raw_response_retained"]
    if (
        type(retained) is not bool
        or retained != (raw_response is not None)
        or response_record.raw_response_hash != raw_hash
        or response_record.raw_response_retained is not retained
        or normalized_response.disposition is not model_disposition
        or (raw_hash is not None and not is_sha256(raw_hash))
        or (
            raw_response is not None
            and (
                len(raw_response) > MAXIMUM_MODEL_OUTPUT_BYTES
                or hashlib.sha256(raw_response).hexdigest() != raw_hash
            )
        )
    ):
        raise LifecyclePersistenceError(_CORRUPT)
    artifact = _parse_artifact(fields["artifact"], intent)
    artifact_refusal = _parse_refusal(fields["artifact_refusal"], intent.role)
    input_tokens = fields["input_tokens"]
    output_tokens = fields["output_tokens"]
    turns = fields["turns"]
    elapsed = fields["elapsed_milliseconds"]
    exposed = fields["exposed_model_identity"]
    if (
        type(input_tokens) is not int
        or type(output_tokens) is not int
        or type(turns) is not int
        or min(input_tokens, output_tokens, turns) < 0
        or (elapsed is not None and (type(elapsed) is not int or elapsed < 0))
        or (exposed is not None and type(exposed) is not str)
    ):
        raise LifecyclePersistenceError(_CORRUPT)
    try:
        observation = LabCallObservation(
            intent.call_id,
            disposition,
            raw_response,
            raw_hash,
            retained,
            exposed,
            input_tokens,
            output_tokens,
            turns,
            elapsed,
            timing,
            artifact,
            artifact_refusal,
        )
    except (TypeError, ValueError) as error:
        raise LifecyclePersistenceError(_CORRUPT) from error
    if not observation_matches_role(observation, intent.role):
        raise LifecyclePersistenceError(_CORRUPT)
    if (
        observation.exposed_model_identity != normalized_response.exposed_model_identity
        or observation.input_tokens != normalized_response.input_tokens
        or observation.output_tokens != normalized_response.output_tokens
        or observation.turns != normalized_response.turns
        or observation.elapsed_milliseconds != normalized_response.elapsed_milliseconds
        or observation.timing_disposition is not normalized_response.timing_disposition
    ):
        raise LifecyclePersistenceError(_CORRUPT)
    if raw_response is not None:
        expected_disposition, expected_artifact, expected_refusal = (
            _rederive_response_backed_observation(
                raw_response,
                observation,
                model_disposition,
                intent,
                configuration,
            )
        )
        if (
            observation.disposition is not expected_disposition
            or observation.artifact != expected_artifact
            or observation.artifact_refusal != expected_refusal
        ):
            raise LifecyclePersistenceError(_CORRUPT)
    else:
        if not _model_timing_is_valid(model_disposition, observation.timing_disposition):
            raise LifecyclePersistenceError(_CORRUPT)
        expected_disposition = _response_less_disposition(
            model_disposition,
            observation,
            configuration,
        )
        if observation.disposition is not expected_disposition:
            raise LifecyclePersistenceError(_CORRUPT)
    return observation, response_record


def _rederive_response_backed_observation(
    raw_response: bytes,
    observation: LabCallObservation,
    model_disposition: ModelCallDisposition,
    intent: ProductionCallIntent,
    configuration: ModelConfiguration,
) -> tuple[
    LabObservationDisposition,
    Dossier | Thesis | SkepticResult | ScenarioForecast | CioResolution | None,
    DossierRefusalReason
    | ThesisRefusalReason
    | SkepticRefusalReason
    | ForecastRefusalReason
    | CioRefusalReason
    | None,
]:
    if not _model_timing_is_valid(model_disposition, observation.timing_disposition):
        raise LifecyclePersistenceError(_CORRUPT)
    response = ModelCallResponse(
        model_disposition,
        raw_response,
        observation.exposed_model_identity,
        observation.input_tokens,
        observation.output_tokens,
        observation.turns,
        observation.elapsed_milliseconds,
        observation.timing_disposition,
    )
    disposition = production_response_disposition(configuration, response)
    if disposition is not LabObservationDisposition.INVALID_ARTIFACT:
        return disposition, None, None
    try:
        decoded = json.loads(raw_response.decode())
        artifact, refusal = _parse_raw_role_output(decoded, intent)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return LabObservationDisposition.INVALID_JSON, None, None
    if artifact is not None:
        return LabObservationDisposition.VALIDATED, artifact, None
    return (
        (
            LabObservationDisposition.INVALID_DOSSIER
            if intent.role is ResearchRole.EVIDENCE_COLLECTOR
            else LabObservationDisposition.INVALID_ARTIFACT
        ),
        None,
        refusal,
    )


def _response_less_disposition(
    model_disposition: ModelCallDisposition,
    observation: LabCallObservation,
    configuration: ModelConfiguration,
) -> LabObservationDisposition:
    if observation.disposition is LabObservationDisposition.INDETERMINATE_EFFECT:
        if (
            model_disposition is not ModelCallDisposition.REFUSED
            or observation.raw_response_hash is not None
        ):
            raise LifecyclePersistenceError(_CORRUPT)
        return LabObservationDisposition.INDETERMINATE_EFFECT
    if observation.disposition is LabObservationDisposition.OVERSIZED_OUTPUT:
        if observation.raw_response_hash is None:
            raise LifecyclePersistenceError(_CORRUPT)
        return LabObservationDisposition.OVERSIZED_OUTPUT
    response = ModelCallResponse(
        model_disposition,
        None,
        observation.exposed_model_identity,
        observation.input_tokens,
        observation.output_tokens,
        observation.turns,
        observation.elapsed_milliseconds,
        observation.timing_disposition,
    )
    return production_response_disposition(configuration, response)


def _model_timing_is_valid(
    disposition: ModelCallDisposition,
    timing: ModelTimingDisposition,
) -> bool:
    if disposition is ModelCallDisposition.TIMED_OUT:
        return timing is ModelTimingDisposition.TIMED_OUT
    if disposition in (ModelCallDisposition.QUOTA_EXHAUSTED, ModelCallDisposition.REFUSED):
        return timing is ModelTimingDisposition.UNAVAILABLE
    return timing is ModelTimingDisposition.WITHIN_BUDGET


def _parse_raw_role_output(
    value: object,
    intent: ProductionCallIntent,
) -> tuple[
    Dossier | Thesis | SkepticResult | ScenarioForecast | CioResolution | None,
    DossierRefusalReason
    | ThesisRefusalReason
    | SkepticRefusalReason
    | ForecastRefusalReason
    | CioRefusalReason
    | None,
]:
    try:
        model_input = json.loads(intent.model_input_json)
        subject = parse_instrument_identity(model_input["subject"])
        cutoff = UtcInstant.parse(model_input["evidence_cutoff"])
        evidence = model_input["evidence"]
    except (InvalidUtcInstantError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise LifecyclePersistenceError(_CORRUPT) from error
    if type(subject) is not EquityInstrumentIdentity or type(evidence) is not list:
        raise LifecyclePersistenceError(_CORRUPT)
    bindings = tuple(
        (item["artifact_id"], item["content_hash"])
        for item in evidence
        if type(item) is dict
        and type(item.get("artifact_id")) is str
        and type(item.get("content_hash")) is str
    )
    if len(bindings) != len(evidence):
        raise LifecyclePersistenceError(_CORRUPT)
    if intent.role is ResearchRole.EVIDENCE_COLLECTOR:
        parsed = parse_dossier(
            value,
            expected_subject=subject,
            available_artifact_ids=tuple(item[0] for item in bindings),
            available_artifact_bindings=bindings,
            cutoff=cutoff,
            authority=ResearchAuthority.PRODUCTION,
        )
        return (parsed, None) if isinstance(parsed, Dossier) else (None, parsed)
    dossier = parse_stored_dossier(
        model_input["dossier"],
        expected_subject=subject,
        available_artifact_bindings=bindings,
        cutoff=cutoff,
        authority=ResearchAuthority.PRODUCTION,
    )
    if dossier is None:
        raise LifecyclePersistenceError(_CORRUPT)
    if intent.role is ResearchRole.THESIS_BUILDER:
        parsed_thesis = parse_thesis(value, dossier=dossier)
        return (parsed_thesis, None) if isinstance(parsed_thesis, Thesis) else (None, parsed_thesis)
    thesis = parse_stored_thesis(model_input["thesis"], dossier=dossier)
    if thesis is None:
        raise LifecyclePersistenceError(_CORRUPT)
    if intent.role is ResearchRole.INDEPENDENT_SKEPTIC:
        parsed_skeptic = parse_skeptic_result(value, dossier=dossier, thesis=thesis)
        return (
            (parsed_skeptic, None)
            if isinstance(parsed_skeptic, SkepticResult)
            else (None, parsed_skeptic)
        )
    skeptic = parse_stored_skeptic_result(model_input["skeptic"], dossier=dossier, thesis=thesis)
    if skeptic is None:
        raise LifecyclePersistenceError(_CORRUPT)
    if intent.role is ResearchRole.SCENARIO_FORECASTER:
        parsed_forecast = parse_scenario_forecast(
            value,
            dossier=dossier,
            thesis=thesis,
            skeptic=skeptic,
        )
        return (
            (parsed_forecast, None)
            if isinstance(parsed_forecast, ScenarioForecast)
            else (None, parsed_forecast)
        )
    forecast = parse_stored_scenario_forecast(
        model_input["forecast"],
        dossier=dossier,
        thesis=thesis,
        skeptic=skeptic,
    )
    if forecast is None:
        raise LifecyclePersistenceError(_CORRUPT)
    parsed_cio = parse_cio_resolution(
        value,
        dossier=dossier,
        thesis=thesis,
        skeptic=skeptic,
        forecast=forecast,
    )
    return (parsed_cio, None) if isinstance(parsed_cio, CioResolution) else (None, parsed_cio)


def _parse_artifact(  # noqa: PLR0912 - revalidate each predecessor by fixed role.
    value: object,
    intent: ProductionCallIntent,
) -> Dossier | Thesis | SkepticResult | ScenarioForecast | CioResolution | None:
    if value is None:
        return None
    try:
        model_input = json.loads(intent.model_input_json)
        subject = parse_instrument_identity(model_input["subject"])
        cutoff = UtcInstant.parse(model_input["evidence_cutoff"])
        evidence = model_input["evidence"]
    except (InvalidUtcInstantError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise LifecyclePersistenceError(_CORRUPT) from error
    if type(subject) is not EquityInstrumentIdentity or type(evidence) is not list:
        raise LifecyclePersistenceError(_CORRUPT)
    bindings: list[tuple[str, str]] = []
    for item in evidence:
        if type(item) is not dict:
            raise LifecyclePersistenceError(_CORRUPT)
        artifact_id = item.get("artifact_id")
        content_hash = item.get("content_hash")
        if not is_sha256(artifact_id) or not is_sha256(content_hash):
            raise LifecyclePersistenceError(_CORRUPT)
        bindings.append((artifact_id, content_hash))
    ordered_bindings = tuple(bindings)
    dossier_value = (
        value if intent.role is ResearchRole.EVIDENCE_COLLECTOR else model_input["dossier"]
    )
    dossier = parse_stored_dossier(
        dossier_value,
        expected_subject=subject,
        available_artifact_bindings=ordered_bindings,
        cutoff=cutoff,
        authority=ResearchAuthority.PRODUCTION,
    )
    if dossier is None:
        raise LifecyclePersistenceError(_CORRUPT)
    if intent.role is ResearchRole.EVIDENCE_COLLECTOR:
        return dossier
    thesis_value = value if intent.role is ResearchRole.THESIS_BUILDER else model_input["thesis"]
    thesis = parse_stored_thesis(thesis_value, dossier=dossier)
    if thesis is None:
        raise LifecyclePersistenceError(_CORRUPT)
    if intent.role is ResearchRole.THESIS_BUILDER:
        return thesis
    skeptic_value = (
        value if intent.role is ResearchRole.INDEPENDENT_SKEPTIC else model_input["skeptic"]
    )
    skeptic = parse_stored_skeptic_result(skeptic_value, dossier=dossier, thesis=thesis)
    if skeptic is None:
        raise LifecyclePersistenceError(_CORRUPT)
    if intent.role is ResearchRole.INDEPENDENT_SKEPTIC:
        return skeptic
    forecast_value = (
        value if intent.role is ResearchRole.SCENARIO_FORECASTER else model_input["forecast"]
    )
    forecast = parse_stored_scenario_forecast(
        forecast_value,
        dossier=dossier,
        thesis=thesis,
        skeptic=skeptic,
    )
    if forecast is None:
        raise LifecyclePersistenceError(_CORRUPT)
    if intent.role is ResearchRole.SCENARIO_FORECASTER:
        return forecast
    cio = parse_stored_cio_resolution(
        value,
        dossier=dossier,
        thesis=thesis,
        skeptic=skeptic,
        forecast=forecast,
    )
    if cio is None:
        raise LifecyclePersistenceError(_CORRUPT)
    return cio


def _parse_refusal(
    value: object,
    role: ResearchRole,
) -> (
    DossierRefusalReason
    | ThesisRefusalReason
    | SkepticRefusalReason
    | ForecastRefusalReason
    | CioRefusalReason
    | None
):
    if value is None:
        return None
    if type(value) is not str:
        raise LifecyclePersistenceError(_CORRUPT)
    try:
        if role is ResearchRole.EVIDENCE_COLLECTOR:
            return DossierRefusalReason(value)
        if role is ResearchRole.THESIS_BUILDER:
            return ThesisRefusalReason(value)
        if role is ResearchRole.INDEPENDENT_SKEPTIC:
            return SkepticRefusalReason(value)
        if role is ResearchRole.SCENARIO_FORECASTER:
            return ForecastRefusalReason(value)
        return CioRefusalReason(value)
    except ValueError as error:
        raise LifecyclePersistenceError(_CORRUPT) from error


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _content_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _is_canonical_json(value: str) -> bool:
    try:
        parsed = json.loads(value)
        return _canonical_json(parsed) == value
    except (ValueError, RecursionError):
        return False
