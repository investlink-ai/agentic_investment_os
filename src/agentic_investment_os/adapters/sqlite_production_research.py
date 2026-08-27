"""Persist production research model-call intent and observations in runtime SQLite."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    parse_instrument_identity,
)
from agentic_investment_os.domain.lifecycle import (
    LifecyclePersistenceError,
    ResearchCheckpoint,
    is_sha256,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.research.authority import ResearchAuthority
from agentic_investment_os.research.dossier import (
    Dossier,
    DossierRefusalReason,
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
    parse_stored_cio_resolution,
    parse_stored_scenario_forecast,
    parse_stored_skeptic_result,
    parse_stored_thesis,
)

__all__ = ("SQLiteProductionCallLedger",)

if TYPE_CHECKING:
    from pathlib import Path

_CORRUPT = "invalid production research call history"
_WRITE_FAILED = "production research call persistence failed"
_BEGIN_IMMEDIATE = "BEGIN IMMEDIATE"
_INTENT_COLUMN_COUNT = 7


class SQLiteProductionCallLedger:
    """Append and replay exact production research effects in the runtime database."""

    def __init__(self, database: Path) -> None:
        self._database = database

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

    def validate_history(self, checkpoints: tuple[ResearchCheckpoint, ...]) -> None:
        """Revalidate production rows and every lifecycle checkpoint reference."""
        try:
            with closing(self._connect()) as connection:
                intent_rows = connection.execute(
                    "SELECT call_id, run_id, request_id, role, intent_json, intent_hash, "
                    "recorded_at FROM production_research_call_intents ORDER BY call_id"
                ).fetchall()
                intents: dict[str, ProductionCallIntent] = {}
                for row in intent_rows:
                    intent = _parse_intent_row(row)
                    if intent.call_id in intents:
                        raise LifecyclePersistenceError(_CORRUPT)
                    intents[intent.call_id] = intent
                observation_rows = connection.execute(
                    "SELECT call_id, recorded_at FROM production_research_call_observations "
                    "ORDER BY call_id"
                ).fetchall()
                observed_call_ids: set[str] = set()
                observed_artifact_ids: set[str] = set()
                for call_id_value, recorded_at_value in observation_rows:
                    if (
                        type(call_id_value) is not str
                        or call_id_value in observed_call_ids
                        or call_id_value not in intents
                    ):
                        raise LifecyclePersistenceError(_CORRUPT)
                    _canonical_instant(recorded_at_value)
                    observed_call_ids.add(call_id_value)
                    observation = self._load_observation(
                        connection,
                        intents[call_id_value],
                    )
                    if observation is None:  # pragma: no cover - selected row proves presence.
                        raise LifecyclePersistenceError(_CORRUPT)
                    if observation.artifact is not None:
                        observed_artifact_ids.add(observation.artifact.content_hash)
                referenced_call_ids = {
                    call_id for checkpoint in checkpoints for call_id in checkpoint.call_ids
                }
                referenced_artifact_ids = {
                    artifact_id
                    for checkpoint in checkpoints
                    for artifact_id in checkpoint.artifact_ids
                }
                if not referenced_call_ids.issubset(observed_call_ids) or not (
                    referenced_artifact_ids.issubset(observed_artifact_ids)
                ):
                    raise LifecyclePersistenceError(_CORRUPT)
        except sqlite3.Error as error:
            raise LifecyclePersistenceError(_CORRUPT) from error

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
    if type(fields) is not dict or set(fields) != required or fields["call_id"] != intent.call_id:
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
    return observation


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
