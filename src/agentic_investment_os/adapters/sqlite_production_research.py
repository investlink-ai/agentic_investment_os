"""Persist production research model-call intent and observations in runtime SQLite."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
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
from agentic_investment_os.memory.beliefs import (
    BeliefEvidenceResolver,
    BeliefGraphRefusal,
    BeliefLedger,
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
    ModelTimingDisposition,
    ResearchRole,
    observation_matches_role,
)
from agentic_investment_os.research.production import (
    ProductionCallIntent,
    ProductionCallPreparation,
    ProductionResearchEvidence,
)
from agentic_investment_os.research.resolution import (
    CioRefusalReason,
    CioResolution,
    ForecastRefusalReason,
    ScenarioForecast,
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

    from agentic_investment_os.research.policy import ProductionResearchPolicy

_CORRUPT = "invalid production research call history"
_WRITE_FAILED = "production research call persistence failed"
_BEGIN_IMMEDIATE = "BEGIN IMMEDIATE"
_INTENT_COLUMN_COUNT = 7


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
                observation = self._load_observation(connection, intent)
                if observation is None:
                    return ProductionCallPreparation(
                        LabCallPreparationDisposition.INDETERMINATE_EFFECT
                    )
                return ProductionCallPreparation(
                    LabCallPreparationDisposition.REPLAY,
                    observation,
                )
        except sqlite3.Error as error:
            raise LifecyclePersistenceError(_WRITE_FAILED) from error

    def append_observation(
        self,
        intent: ProductionCallIntent,
        observation: LabCallObservation,
        recorded_at: UtcInstant,
    ) -> LabCallObservation:
        observation.__post_init__()
        if not observation_matches_role(observation, intent.role):
            raise LifecyclePersistenceError(_WRITE_FAILED)
        observation_json = _canonical_json(observation.to_payload())
        observation_hash = hashlib.sha256(observation_json.encode()).hexdigest()
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_BEGIN_IMMEDIATE)
                self._require_intent(connection, intent)
                prior = self._load_observation(connection, intent)
                if prior is not None:
                    if prior != observation:
                        raise LifecyclePersistenceError(_CORRUPT)
                    return prior
                connection.execute(
                    "INSERT INTO production_research_call_observations "
                    "(call_id, observation_json, raw_response, observation_hash, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        intent.call_id,
                        observation_json,
                        observation.raw_response,
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
                    observation = self._load_observation(
                        connection,
                        intents[call_id_value],
                    )
                    if observation is None:  # pragma: no cover - selected row proves presence.
                        raise LifecyclePersistenceError(_CORRUPT)
                    observations[call_id_value] = observation
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
        for owner, reference in owners.items():
            owner_intents = tuple(sorted(grouped[owner], key=lambda item: item.call_id))
            checkpoint = reference.checkpoint
            if checkpoint is None:
                continue
            owner_call_ids = tuple(intent.call_id for intent in owner_intents)
            owner_observations = tuple(
                observations[call_id] for call_id in owner_call_ids if call_id in observations
            )
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
        expected_subject = _subject_owner(reference, intent.request_id)
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
        records = self._evidence_vault.stored_records_for_artifacts(
            reference.attention_artifact.evidence_artifact_ids
        )
        expected_evidence = tuple(
            ProductionResearchEvidence(
                record.artifact.artifact_id,
                record.artifact.content_hash,
                record.artifact.available_at,
                _canonical_json(record.artifact.to_payload()),
                record.content,
                _artifact_is_official(record.artifact.feed),
            )
            for record in records
            if any(mapping.identity == subject for mapping in record.artifact.entity_mappings)
            or record.artifact.feed
            in (EvidenceFeed.FEDERAL_RESERVE, EvidenceFeed.BLS, EvidenceFeed.BEA)
        )
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
    ) -> LabCallObservation | None:
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
        return _parse_observation(observation_json, raw_response, intent)

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


def _subject_owner(
    reference: ProductionResearchReference,
    request_id: str,
) -> EquityInstrumentIdentity | None:
    cards = {card.card_id: card for card in reference.attention_artifact.candidate_cards}
    for request in reference.attention_artifact.dossier_requests:
        card = cards.get(request.candidate_card_id)
        if request.request_id == request_id and card is not None:
            return request.identity if type(request.identity) is EquityInstrumentIdentity else None
    for refresh in reference.attention_artifact.holding_refreshes:
        if (
            refresh.refresh_id == request_id
            and refresh.disposition is HoldingRefreshDisposition.REQUIRED
        ):
            return refresh.identity if type(refresh.identity) is EquityInstrumentIdentity else None
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
    except (ValueError, json.JSONDecodeError) as error:
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


def _parse_observation(
    observation_json: str,
    raw_response: bytes | None,
    intent: ProductionCallIntent,
) -> LabCallObservation:
    try:
        fields = json.loads(observation_json)
    except json.JSONDecodeError as error:
        raise LifecyclePersistenceError(_CORRUPT) from error
    required = {
        "schema_version",
        "record_kind",
        "call_id",
        "disposition",
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
        or fields["record_kind"] != "lab_model_call_observation"
        or fields["call_id"] != intent.call_id
    ):
        raise LifecyclePersistenceError(_CORRUPT)
    try:
        disposition = LabObservationDisposition(fields["disposition"])
        timing = ModelTimingDisposition(fields["timing_disposition"])
    except (TypeError, ValueError) as error:
        raise LifecyclePersistenceError(_CORRUPT) from error
    raw_hash = fields["raw_response_hash"]
    retained = fields["raw_response_retained"]
    if (
        type(retained) is not bool
        or retained != (raw_response is not None)
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
    if not _observation_timing_is_valid(observation):
        raise LifecyclePersistenceError(_CORRUPT)
    if observation.disposition in (
        LabObservationDisposition.VALIDATED,
        LabObservationDisposition.INVALID_DOSSIER,
        LabObservationDisposition.INVALID_ARTIFACT,
    ):
        if raw_response is None:
            raise LifecyclePersistenceError(_CORRUPT)
        try:
            decoded = json.loads(raw_response.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LifecyclePersistenceError(_CORRUPT) from error
        artifact, refusal = _parse_raw_role_output(decoded, intent)
        if observation.artifact != artifact or observation.artifact_refusal != refusal:
            raise LifecyclePersistenceError(_CORRUPT)
    return observation


def _observation_timing_is_valid(observation: LabCallObservation) -> bool:
    if observation.disposition is LabObservationDisposition.MODEL_TIMEOUT:
        return observation.timing_disposition is ModelTimingDisposition.TIMED_OUT
    if observation.disposition in (
        LabObservationDisposition.QUOTA_EXHAUSTED,
        LabObservationDisposition.ADAPTER_REFUSED,
    ):
        return observation.timing_disposition is ModelTimingDisposition.UNAVAILABLE
    return observation.timing_disposition is ModelTimingDisposition.WITHIN_BUDGET


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


def _is_canonical_json(value: str) -> bool:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return False
    return _canonical_json(parsed) == value
