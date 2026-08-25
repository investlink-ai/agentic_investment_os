"""Persist isolated Research Lab model-call intent and observations in SQLite."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    parse_instrument_identity,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.research.dossier import Dossier, DossierRefusalReason, parse_dossier
from agentic_investment_os.research.model import (
    MAXIMUM_MODEL_OUTPUT_BYTES,
    LabCallIntent,
    LabCallObservation,
    LabCallPreparation,
    LabCallPreparationDisposition,
    LabObservationDisposition,
    ModelTimingDisposition,
    ResearchRole,
    observation_matches_role,
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
    parse_thesis,
)

__all__ = (
    "LabPersistenceError",
    "LabRootRefusal",
    "PreparedLabDatabase",
    "SQLiteLabCallLedger",
    "prepare_lab_database",
)

_DATABASE_NAME = "research-lab.sqlite3"
_DATABASE_VERSION = 2
_BEGIN_IMMEDIATE = "BEGIN IMMEDIATE"
_PRIVATE_DATABASE_FLAGS = os.O_CREAT | os.O_EXCL | os.O_WRONLY
_INTENT_ROW_LENGTH = 7
_OBSERVATION_ROW_LENGTH = 5
_SHA256_LENGTH = 64
_SCHEMA = (
    """
    CREATE TABLE lab_metadata (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        namespace TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE lab_call_intents (
        call_id TEXT PRIMARY KEY CHECK (length(call_id) = 64),
        request_id TEXT NOT NULL,
        role TEXT NOT NULL,
        request_fingerprint TEXT NOT NULL CHECK (length(request_fingerprint) = 64),
        intent_json TEXT NOT NULL,
        intent_hash TEXT NOT NULL CHECK (length(intent_hash) = 64),
        recorded_at TEXT NOT NULL,
        UNIQUE (request_id, role)
    ) STRICT
    """,
    """
    CREATE TABLE lab_call_observations (
        call_id TEXT PRIMARY KEY REFERENCES lab_call_intents(call_id),
        observation_json TEXT NOT NULL,
        raw_response BLOB,
        observation_hash TEXT NOT NULL CHECK (length(observation_hash) = 64),
        recorded_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TRIGGER lab_metadata_no_update
    BEFORE UPDATE ON lab_metadata BEGIN SELECT RAISE(ABORT, 'lab metadata is immutable'); END
    """,
    """
    CREATE TRIGGER lab_metadata_no_delete
    BEFORE DELETE ON lab_metadata BEGIN SELECT RAISE(ABORT, 'lab metadata is immutable'); END
    """,
    """
    CREATE TRIGGER lab_call_intents_no_update
    BEFORE UPDATE ON lab_call_intents BEGIN SELECT RAISE(ABORT, 'Lab intents are append-only'); END
    """,
    """
    CREATE TRIGGER lab_call_intents_no_delete
    BEFORE DELETE ON lab_call_intents BEGIN SELECT RAISE(ABORT, 'Lab intents are append-only'); END
    """,
    """
    CREATE TRIGGER lab_call_observations_no_update
    BEFORE UPDATE ON lab_call_observations
    BEGIN SELECT RAISE(ABORT, 'Lab observations are append-only'); END
    """,
    """
    CREATE TRIGGER lab_call_observations_no_delete
    BEFORE DELETE ON lab_call_observations
    BEGIN SELECT RAISE(ABORT, 'Lab observations are append-only'); END
    """,
)
_EXPECTED_OBJECTS = frozenset(
    {
        "table:lab_metadata",
        "table:lab_call_intents",
        "table:lab_call_observations",
        "index:sqlite_autoindex_lab_call_intents_1",
        "index:sqlite_autoindex_lab_call_intents_2",
        "index:sqlite_autoindex_lab_call_observations_1",
        "trigger:lab_metadata_no_update",
        "trigger:lab_metadata_no_delete",
        "trigger:lab_call_intents_no_update",
        "trigger:lab_call_intents_no_delete",
        "trigger:lab_call_observations_no_update",
        "trigger:lab_call_observations_no_delete",
    }
)
_EXPECTED_SCHEMA_SQL = frozenset(" ".join(statement.split()) for statement in _SCHEMA)
_PREPARATION_FAILED = "isolated Research Lab persistence could not prepare a model call"
_OBSERVATION_FAILED = "isolated Research Lab persistence could not append an observation"
_CORRUPT_HISTORY = "isolated Research Lab history is invalid"


class LabPersistenceError(RuntimeError):
    """Report Lab-local history that cannot be trusted or appended."""


@dataclass(frozen=True, slots=True)
class LabRootRefusal:
    """Signal that a private isolated Lab root cannot be established safely."""


@dataclass(frozen=True, slots=True)
class PreparedLabDatabase:
    """Identify the private database path for one Research Lab namespace."""

    path: Path
    created: bool


def prepare_lab_database(  # noqa: PLR0911 - reject each unsafe filesystem shape directly.
    lab_root: Path,
    *,
    production_state_roots: tuple[Path, ...],
) -> PreparedLabDatabase | LabRootRefusal:
    """Create private Lab storage only when it is disjoint from production state."""
    try:
        if not lab_root.is_absolute() or _has_symlink_component(lab_root):
            return LabRootRefusal()
        resolved_lab = lab_root.resolve()
        if any(
            not root.is_absolute() or _has_symlink_component(root)
            for root in production_state_roots
        ):
            return LabRootRefusal()
        production_roots = tuple(root.resolve() for root in production_state_roots)
        if any(_paths_overlap(resolved_lab, root) for root in production_roots):
            return LabRootRefusal()
        created_root = not resolved_lab.exists()
        if created_root:
            resolved_lab.mkdir(mode=0o700)
        elif not resolved_lab.is_dir() or stat.S_IMODE(resolved_lab.stat().st_mode) & 0o077:
            return LabRootRefusal()
        database = resolved_lab / _DATABASE_NAME
        if database.is_symlink():
            return LabRootRefusal()
        if database.exists():
            if not database.is_file() or stat.S_IMODE(database.stat().st_mode) & 0o077:
                return LabRootRefusal()
            return PreparedLabDatabase(database, created=False)
        descriptor = os.open(database, _PRIVATE_DATABASE_FLAGS, 0o600)
        os.close(descriptor)
        return PreparedLabDatabase(database, created=True)
    except (OSError, RuntimeError, ValueError):
        return LabRootRefusal()


class SQLiteLabCallLedger:
    """Append and reconstruct one namespace's model-call intent and observations."""

    def __init__(self, database: Path, namespace: str) -> None:
        self._database = database
        self._namespace = namespace
        self._initialize_or_validate()

    def prepare_call(self, intent: LabCallIntent, recorded_at: UtcInstant) -> LabCallPreparation:
        intent_json = _canonical_json(intent.to_payload())
        timestamp = recorded_at.isoformat()
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_BEGIN_IMMEDIATE)
                self._validate_database(connection)
                row = connection.execute(
                    "SELECT call_id, role, request_fingerprint, intent_json, intent_hash "
                    "FROM lab_call_intents WHERE request_id = ? AND role = ?",
                    (intent.request_id, intent.role.value),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO lab_call_intents "
                        "(call_id, request_id, role, request_fingerprint, "
                        "intent_json, intent_hash, "
                        "recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            intent.call_id,
                            intent.request_id,
                            intent.role.value,
                            intent.request_fingerprint,
                            intent_json,
                            intent.content_hash,
                            timestamp,
                        ),
                    )
                    return LabCallPreparation(LabCallPreparationDisposition.EFFECT_REQUIRED)
                call_id, stored_role, fingerprint, stored_json, stored_hash = row
                if (
                    stored_role != intent.role.value
                    or type(stored_json) is not str
                    or type(stored_hash) is not str
                    or hashlib.sha256(stored_json.encode()).hexdigest() != stored_hash
                    or not _is_canonical_json(stored_json)
                ):
                    raise LabPersistenceError(_CORRUPT_HISTORY)
                if (
                    call_id != intent.call_id
                    or fingerprint != intent.request_fingerprint
                    or stored_json != intent_json
                    or stored_hash != intent.content_hash
                ):
                    return LabCallPreparation(LabCallPreparationDisposition.CONFLICT)
                observation = self._load_observation(connection, intent)
                if observation is None:
                    return LabCallPreparation(LabCallPreparationDisposition.INDETERMINATE_EFFECT)
                return LabCallPreparation(LabCallPreparationDisposition.REPLAY, observation)
        except sqlite3.Error as error:
            raise LabPersistenceError(_PREPARATION_FAILED) from error

    def append_observation(
        self,
        intent: LabCallIntent,
        observation: LabCallObservation,
        recorded_at: UtcInstant,
    ) -> LabCallObservation:
        try:
            observation.__post_init__()
        except (AttributeError, TypeError, ValueError) as error:
            raise LabPersistenceError(_OBSERVATION_FAILED) from error
        if not observation_matches_role(observation, intent.role):
            raise LabPersistenceError(_OBSERVATION_FAILED)
        observation_json = _canonical_json(observation.to_payload())
        observation_hash = hashlib.sha256(observation_json.encode()).hexdigest()
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_BEGIN_IMMEDIATE)
                self._validate_database(connection)
                _require_matching_intent(connection, intent)
                prior = self._load_observation(connection, intent)
                if prior is not None:
                    if prior != observation:
                        raise LabPersistenceError(_CORRUPT_HISTORY)
                    return prior
                connection.execute(
                    "INSERT INTO lab_call_observations "
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
            raise LabPersistenceError(_OBSERVATION_FAILED) from error

    def _initialize_or_validate(self) -> None:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_BEGIN_IMMEDIATE)
                version = connection.execute("PRAGMA user_version").fetchone()
                if version == (0,) and not _user_objects(connection):
                    for statement in _SCHEMA:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO lab_metadata (singleton, namespace) VALUES (1, ?)",
                        (self._namespace,),
                    )
                    connection.execute(f"PRAGMA user_version = {_DATABASE_VERSION}")
                elif version != (_DATABASE_VERSION,):
                    raise LabPersistenceError(_CORRUPT_HISTORY)
                self._validate_database(connection)
        except sqlite3.Error as error:
            raise LabPersistenceError(_CORRUPT_HISTORY) from error

    def _validate_database(self, connection: sqlite3.Connection) -> None:
        if (
            connection.execute("PRAGMA user_version").fetchone() != (_DATABASE_VERSION,)
            or _object_signature(connection) != _EXPECTED_OBJECTS
            or _schema_signature(connection) != _EXPECTED_SCHEMA_SQL
            or connection.execute(
                "SELECT namespace FROM lab_metadata WHERE singleton = 1"
            ).fetchone()
            != (self._namespace,)
            or connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
        ):
            raise LabPersistenceError(_CORRUPT_HISTORY)
        _validate_history(connection)

    def _load_observation(
        self,
        connection: sqlite3.Connection,
        intent: LabCallIntent,
    ) -> LabCallObservation | None:
        row = connection.execute(
            "SELECT observation_json, raw_response, observation_hash "
            "FROM lab_call_observations WHERE call_id = ?",
            (intent.call_id,),
        ).fetchone()
        if row is None:
            return None
        observation_json, raw_response, observation_hash = row
        if (
            type(observation_json) is not str
            or type(observation_hash) is not str
            or hashlib.sha256(observation_json.encode()).hexdigest() != observation_hash
            or not _is_canonical_json(observation_json)
            or (raw_response is not None and type(raw_response) is not bytes)
        ):
            raise LabPersistenceError(_CORRUPT_HISTORY)
        return _parse_observation(observation_json, raw_response, intent)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


def _parse_observation(
    observation_json: str,
    raw_response: bytes | None,
    intent: LabCallIntent,
) -> LabCallObservation:
    try:
        payload = json.loads(observation_json)
    except json.JSONDecodeError as error:
        raise LabPersistenceError(_CORRUPT_HISTORY) from error
    fields = _exact_mapping(
        payload,
        frozenset(
            {
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
        ),
    )
    if (
        fields is None
        or fields["schema_version"] != 1
        or fields["record_kind"] != "lab_model_call_observation"
        or fields["call_id"] != intent.call_id
    ):
        raise LabPersistenceError(_CORRUPT_HISTORY)
    retained = fields["raw_response_retained"]
    stored_raw_hash = fields["raw_response_hash"]
    if (
        type(retained) is not bool
        or (stored_raw_hash is not None and not _is_sha256(stored_raw_hash))
        or retained != (raw_response is not None)
        or (
            raw_response is not None
            and (
                len(raw_response) > MAXIMUM_MODEL_OUTPUT_BYTES
                or hashlib.sha256(raw_response).hexdigest() != stored_raw_hash
            )
        )
        or (retained and stored_raw_hash is None)
    ):
        raise LabPersistenceError(_CORRUPT_HISTORY)
    raw_hash = None if stored_raw_hash is None else _require_string(stored_raw_hash)
    disposition_value = fields["disposition"]
    timing_value = fields["timing_disposition"]
    if type(disposition_value) is not str or type(timing_value) is not str:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    try:
        disposition = LabObservationDisposition(disposition_value)
        timing = ModelTimingDisposition(timing_value)
    except (TypeError, ValueError) as error:
        raise LabPersistenceError(_CORRUPT_HISTORY) from error
    artifact = _parse_stored_artifact(fields["artifact"], intent)
    artifact_refusal_value = fields["artifact_refusal"]
    if artifact_refusal_value is not None and type(artifact_refusal_value) is not str:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    try:
        artifact_refusal = _parse_artifact_refusal(artifact_refusal_value, intent.role)
    except (TypeError, ValueError) as error:
        raise LabPersistenceError(_CORRUPT_HISTORY) from error
    exposed = fields["exposed_model_identity"]
    elapsed = fields["elapsed_milliseconds"]
    input_tokens = fields["input_tokens"]
    output_tokens = fields["output_tokens"]
    turns = fields["turns"]
    if (
        (exposed is not None and type(exposed) is not str)
        or type(input_tokens) is not int
        or input_tokens < 0
        or type(output_tokens) is not int
        or output_tokens < 0
        or type(turns) is not int
        or turns < 0
        or (elapsed is not None and (type(elapsed) is not int or elapsed < 0))
    ):
        raise LabPersistenceError(_CORRUPT_HISTORY)
    validated = disposition is LabObservationDisposition.VALIDATED
    invalid_artifact = disposition in (
        LabObservationDisposition.INVALID_ARTIFACT,
        LabObservationDisposition.INVALID_DOSSIER,
    )
    oversized = disposition is LabObservationDisposition.OVERSIZED_OUTPUT
    if (
        validated != (artifact is not None and artifact_refusal is None)
        or invalid_artifact != (artifact is None and artifact_refusal is not None)
        or (
            not validated
            and not invalid_artifact
            and (artifact is not None or artifact_refusal is not None)
        )
        or (oversized and (retained or stored_raw_hash is None))
        or (not oversized and not retained and stored_raw_hash is not None)
    ):
        raise LabPersistenceError(_CORRUPT_HISTORY)
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
        raise LabPersistenceError(_CORRUPT_HISTORY) from error
    if not observation_matches_role(observation, intent.role):
        raise LabPersistenceError(_CORRUPT_HISTORY)
    return observation


def _parse_stored_artifact(
    value: object, intent: LabCallIntent
) -> Dossier | Thesis | SkepticResult | ScenarioForecast | CioResolution | None:
    if value is None:
        return None
    if intent.role is not ResearchRole.EVIDENCE_COLLECTOR:
        return _parse_stored_resolution_artifact(value, intent)
    legacy_fields = frozenset(
        {
            "schema_version",
            "record_kind",
            "authority_scope",
            "non_production",
            "subject",
            "facts",
            "interpretations",
            "contradicting_evidence",
            "missing_evidence",
            "lenses",
            "content_hash",
        }
    )
    if (
        type(value) is not dict
        or set(value) not in (legacy_fields, legacy_fields | {"evidence_manifest_hash"})
        or type(value.get("content_hash")) is not str
    ):
        raise LabPersistenceError(_CORRUPT_HISTORY)
    try:
        model_input = json.loads(intent.model_input_json)
        subject = parse_instrument_identity(model_input["subject"])
        cutoff = UtcInstant.parse(model_input["evidence_cutoff"])
        evidence = model_input["evidence"]
    except (InvalidUtcInstantError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise LabPersistenceError(_CORRUPT_HISTORY) from error
    if type(subject) is not EquityInstrumentIdentity:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    bindings = _stored_evidence_bindings(
        evidence,
        subject,
        cutoff,
        intent.material_input_hashes,
    )
    if bindings is None:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    artifact_ids = tuple(item[0] for item in bindings)
    manifest_bound = "evidence_manifest_hash" in value
    raw = {
        key: item
        for key, item in value.items()
        if key not in ("content_hash", "evidence_manifest_hash")
    }
    parsed = parse_dossier(
        raw,
        expected_subject=subject,
        available_artifact_ids=artifact_ids,
        available_artifact_bindings=bindings if manifest_bound else None,
        cutoff=cutoff,
    )
    if not isinstance(parsed, Dossier) or parsed.to_payload() != value:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    return parsed


def _parse_artifact_refusal(
    value: object, role: ResearchRole
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
        raise ValueError(_CORRUPT_HISTORY)
    if role is ResearchRole.EVIDENCE_COLLECTOR:
        return DossierRefusalReason(value)
    if role is ResearchRole.THESIS_BUILDER:
        return ThesisRefusalReason(value)
    if role is ResearchRole.INDEPENDENT_SKEPTIC:
        return SkepticRefusalReason(value)
    if role is ResearchRole.SCENARIO_FORECASTER:
        return ForecastRefusalReason(value)
    return CioRefusalReason(value)


def _parse_stored_resolution_artifact(
    value: object, intent: LabCallIntent
) -> Thesis | SkepticResult | ScenarioForecast | CioResolution:
    try:
        model_input = json.loads(intent.model_input_json)
        subject = parse_instrument_identity(model_input["subject"])
        cutoff = UtcInstant.parse(model_input["evidence_cutoff"])
        artifact_ids_value = model_input["available_artifact_ids"]
        evidence_manifest = model_input["evidence_manifest"]
        dossier_value = model_input["dossier"]
    except (InvalidUtcInstantError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise LabPersistenceError(_CORRUPT_HISTORY) from error
    if (
        type(subject) is not EquityInstrumentIdentity
        or type(artifact_ids_value) is not list
        or any(not _is_sha256(item) for item in artifact_ids_value)
        or not _valid_evidence_manifest(
            evidence_manifest,
            subject,
            cutoff,
            artifact_ids_value,
            intent.material_input_hashes,
        )
    ):
        raise LabPersistenceError(_CORRUPT_HISTORY)
    artifact_ids = tuple(artifact_ids_value)
    dossier = _parse_dossier_payload(
        dossier_value,
        subject,
        artifact_ids,
        cutoff,
        _evidence_bindings(evidence_manifest),
    )
    thesis = _parse_prior_thesis(model_input.get("thesis"), dossier)
    skeptic = _parse_prior_skeptic(model_input.get("skeptic"), dossier, thesis)
    forecast = _parse_prior_forecast(model_input.get("forecast"), dossier, thesis, skeptic)
    raw, expected_hash = _split_stored_artifact(value)
    parsed: (
        Thesis
        | ThesisRefusalReason
        | SkepticResult
        | SkepticRefusalReason
        | ScenarioForecast
        | ForecastRefusalReason
        | CioResolution
        | CioRefusalReason
    )
    if intent.role is ResearchRole.THESIS_BUILDER:
        parsed = parse_thesis(raw, dossier=dossier)
    elif intent.role is ResearchRole.INDEPENDENT_SKEPTIC and thesis is not None:
        parsed = parse_skeptic_result(raw, dossier=dossier, thesis=thesis)
    elif (
        intent.role is ResearchRole.SCENARIO_FORECASTER
        and thesis is not None
        and skeptic is not None
    ):
        parsed = parse_scenario_forecast(raw, dossier=dossier, thesis=thesis, skeptic=skeptic)
    elif (
        intent.role is ResearchRole.CIO
        and thesis is not None
        and skeptic is not None
        and forecast is not None
    ):
        parsed = parse_cio_resolution(
            raw,
            dossier=dossier,
            thesis=thesis,
            skeptic=skeptic,
            forecast=forecast,
        )
    else:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    if not isinstance(parsed, (Thesis, SkepticResult, ScenarioForecast, CioResolution)):
        raise LabPersistenceError(_CORRUPT_HISTORY)
    if parsed.content_hash != expected_hash:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    return parsed


def _parse_dossier_payload(
    value: object,
    subject: EquityInstrumentIdentity,
    artifact_ids: tuple[str, ...],
    cutoff: UtcInstant,
    evidence_bindings: tuple[tuple[str, str], ...] | None,
) -> Dossier:
    if evidence_bindings is None:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    raw, expected_hash = _split_stored_artifact(value)
    raw.pop("evidence_manifest_hash", None)
    parsed = parse_dossier(
        raw,
        expected_subject=subject,
        available_artifact_ids=artifact_ids,
        available_artifact_bindings=evidence_bindings,
        cutoff=cutoff,
    )
    if (
        not isinstance(parsed, Dossier)
        or parsed.content_hash != expected_hash
        or parsed.to_payload() != value
    ):
        raise LabPersistenceError(_CORRUPT_HISTORY)
    return parsed


def _parse_prior_thesis(value: object, dossier: Dossier) -> Thesis | None:
    if value is None:
        return None
    raw, expected_hash = _split_stored_artifact(value)
    parsed = parse_thesis(raw, dossier=dossier)
    if not isinstance(parsed, Thesis) or parsed.content_hash != expected_hash:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    return parsed


def _parse_prior_skeptic(
    value: object, dossier: Dossier, thesis: Thesis | None
) -> SkepticResult | None:
    if value is None:
        return None
    if thesis is None:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    raw, expected_hash = _split_stored_artifact(value)
    parsed = parse_skeptic_result(raw, dossier=dossier, thesis=thesis)
    if not isinstance(parsed, SkepticResult) or parsed.content_hash != expected_hash:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    return parsed


def _parse_prior_forecast(
    value: object,
    dossier: Dossier,
    thesis: Thesis | None,
    skeptic: SkepticResult | None,
) -> ScenarioForecast | None:
    if value is None:
        return None
    if thesis is None or skeptic is None:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    raw, expected_hash = _split_stored_artifact(value)
    parsed = parse_scenario_forecast(raw, dossier=dossier, thesis=thesis, skeptic=skeptic)
    if not isinstance(parsed, ScenarioForecast) or parsed.content_hash != expected_hash:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    return parsed


def _split_stored_artifact(value: object) -> tuple[dict[str, object], str]:
    if type(value) is not dict or type(value.get("content_hash")) is not str:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    expected_hash = _require_string(value["content_hash"])
    raw = {key: item for key, item in value.items() if key != "content_hash"}
    return raw, expected_hash


def _valid_evidence_manifest(
    value: object,
    subject: EquityInstrumentIdentity,
    cutoff: UtcInstant,
    artifact_ids: list[object],
    material_input_hashes: tuple[str, ...],
) -> bool:
    if type(value) is not list or len(value) != len(artifact_ids):
        return False
    parsed_ids: list[str] = []
    for item in value:
        fields = _exact_mapping(
            item,
            frozenset({"artifact_id", "content_hash", "available_at", "subject"}),
        )
        if fields is None:
            return False
        artifact_id = fields["artifact_id"]
        parsed_subject = parse_instrument_identity(fields["subject"])
        try:
            available_at = UtcInstant.parse(fields["available_at"])
        except InvalidUtcInstantError:
            return False
        if (
            type(artifact_id) is not str
            or not _is_sha256(fields["content_hash"])
            or fields["content_hash"] not in material_input_hashes
            or type(parsed_subject) is not EquityInstrumentIdentity
            or parsed_subject != subject
            or available_at.value > cutoff.value
        ):
            return False
        parsed_ids.append(artifact_id)
    return parsed_ids == artifact_ids and parsed_ids == sorted(set(parsed_ids))


def _evidence_bindings(value: object) -> tuple[tuple[str, str], ...] | None:
    if type(value) is not list:
        return None
    bindings: list[tuple[str, str]] = []
    for item in value:
        if type(item) is not dict:
            return None
        artifact_id = item.get("artifact_id")
        content_hash = item.get("content_hash")
        if (
            type(artifact_id) is not str
            or not _is_sha256(artifact_id)
            or type(content_hash) is not str
            or not _is_sha256(content_hash)
        ):
            return None
        bindings.append((artifact_id, content_hash))
    result = tuple(bindings)
    return result if result == tuple(sorted(set(result))) else None


def _stored_evidence_bindings(
    value: object,
    subject: EquityInstrumentIdentity,
    cutoff: UtcInstant,
    material_input_hashes: tuple[str, ...],
) -> tuple[tuple[str, str], ...] | None:
    if type(value) is not list or not value:
        return None
    bindings: list[tuple[str, str]] = []
    for item in value:
        fields = _exact_mapping(
            item,
            frozenset({"artifact_id", "content_hash", "available_at", "subject", "content"}),
        )
        if fields is None:
            return None
        artifact_id = fields["artifact_id"]
        content_hash = fields["content_hash"]
        content = fields["content"]
        parsed_subject = parse_instrument_identity(fields["subject"])
        try:
            available_at = UtcInstant.parse(fields["available_at"])
        except InvalidUtcInstantError:
            return None
        if (
            type(artifact_id) is not str
            or not _is_sha256(artifact_id)
            or type(content_hash) is not str
            or not _is_sha256(content_hash)
            or content_hash not in material_input_hashes
            or type(content) is not str
            or not content
            or hashlib.sha256(content.encode()).hexdigest() != content_hash
            or type(parsed_subject) is not EquityInstrumentIdentity
            or parsed_subject != subject
            or available_at.value > cutoff.value
        ):
            return None
        bindings.append((artifact_id, content_hash))
    result = tuple(bindings)
    return result if result == tuple(sorted(set(result))) else None


def _require_matching_intent(connection: sqlite3.Connection, intent: LabCallIntent) -> None:
    row = connection.execute(
        "SELECT request_id, role, request_fingerprint, intent_json, intent_hash "
        "FROM lab_call_intents WHERE call_id = ?",
        (intent.call_id,),
    ).fetchone()
    if row != (
        intent.request_id,
        intent.role.value,
        intent.request_fingerprint,
        _canonical_json(intent.to_payload()),
        intent.content_hash,
    ):
        raise LabPersistenceError(_CORRUPT_HISTORY)


def _validate_history(connection: sqlite3.Connection) -> None:
    intents: dict[str, tuple[LabCallIntent, UtcInstant]] = {}
    rows = connection.execute(
        "SELECT call_id, request_id, role, request_fingerprint, intent_json, intent_hash, "
        "recorded_at "
        "FROM lab_call_intents ORDER BY request_id, role"
    ).fetchall()
    for row in rows:
        intent, recorded_at = _parse_intent_row(row)
        if intent.call_id in intents:
            raise LabPersistenceError(_CORRUPT_HISTORY)
        intents[intent.call_id] = (intent, recorded_at)
    observation_rows = connection.execute(
        "SELECT call_id, observation_json, raw_response, observation_hash, recorded_at "
        "FROM lab_call_observations ORDER BY call_id"
    ).fetchall()
    for row in observation_rows:
        if type(row) is not tuple or len(row) != _OBSERVATION_ROW_LENGTH or type(row[0]) is not str:
            raise LabPersistenceError(_CORRUPT_HISTORY)
        stored_intent = intents.get(row[0])
        if stored_intent is None:
            raise LabPersistenceError(_CORRUPT_HISTORY)
        intent, intent_recorded_at = stored_intent
        observation_json, raw_response, observation_hash = row[1:4]
        observed_at = _parse_recorded_at(row[4])
        if (
            observed_at.value < intent_recorded_at.value
            or type(observation_json) is not str
            or type(observation_hash) is not str
            or hashlib.sha256(observation_json.encode()).hexdigest() != observation_hash
            or not _is_canonical_json(observation_json)
            or (raw_response is not None and type(raw_response) is not bytes)
        ):
            raise LabPersistenceError(_CORRUPT_HISTORY)
        _parse_observation(observation_json, raw_response, intent)


def _parse_intent_row(row: object) -> tuple[LabCallIntent, UtcInstant]:
    if type(row) is not tuple or len(row) != _INTENT_ROW_LENGTH:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    call_id, request_id, stored_role, request_fingerprint, intent_json, intent_hash, recorded_at = (
        row
    )
    if (
        type(call_id) is not str
        or type(request_id) is not str
        or type(stored_role) is not str
        or type(request_fingerprint) is not str
        or type(intent_json) is not str
        or type(intent_hash) is not str
        or hashlib.sha256(intent_json.encode()).hexdigest() != intent_hash
        or not _is_canonical_json(intent_json)
    ):
        raise LabPersistenceError(_CORRUPT_HISTORY)
    try:
        payload = json.loads(intent_json)
    except json.JSONDecodeError as error:
        raise LabPersistenceError(_CORRUPT_HISTORY) from error
    fields = _exact_mapping(
        payload,
        frozenset(
            {
                "schema_version",
                "record_kind",
                "call_id",
                "role",
                "namespace",
                "request_id",
                "request_fingerprint",
                "model_input_json",
                "model_input_hash",
                "prompt_fingerprint",
                "requested_model_identity",
                "model_configuration_fingerprint",
                "tool_fingerprints",
                "material_input_hashes",
                "maximum_output_bytes",
            }
        ),
    )
    if (
        fields is None
        or fields["schema_version"] != 1
        or fields["record_kind"] != "lab_model_call_intent"
    ):
        raise LabPersistenceError(_CORRUPT_HISTORY)
    parsed_call_id = _require_string(fields["call_id"])
    role_value = _require_string(fields["role"])
    namespace = _require_string(fields["namespace"])
    parsed_request_id = _require_string(fields["request_id"])
    parsed_request_fingerprint = _require_string(fields["request_fingerprint"])
    model_input_json = _require_string(fields["model_input_json"])
    model_input_hash = _require_string(fields["model_input_hash"])
    prompt_fingerprint = _require_string(fields["prompt_fingerprint"])
    requested_model_identity = _require_string(fields["requested_model_identity"])
    model_configuration_fingerprint = _require_string(fields["model_configuration_fingerprint"])
    tool_fingerprints = _string_tuple(fields["tool_fingerprints"])
    material_input_hashes = _string_tuple(fields["material_input_hashes"])
    maximum_output_bytes = fields["maximum_output_bytes"]
    if (
        tool_fingerprints is None
        or material_input_hashes is None
        or type(maximum_output_bytes) is not int
    ):
        raise LabPersistenceError(_CORRUPT_HISTORY)
    try:
        role = ResearchRole(role_value)
        intent = LabCallIntent(
            parsed_call_id,
            role,
            namespace,
            parsed_request_id,
            parsed_request_fingerprint,
            model_input_json,
            model_input_hash,
            prompt_fingerprint,
            requested_model_identity,
            model_configuration_fingerprint,
            tool_fingerprints,
            material_input_hashes,
            maximum_output_bytes,
        )
    except (TypeError, ValueError) as error:
        raise LabPersistenceError(_CORRUPT_HISTORY) from error
    if (
        call_id != intent.call_id
        or request_id != intent.request_id
        or stored_role != intent.role.value
        or request_fingerprint != intent.request_fingerprint
        or intent_hash != intent.content_hash
    ):
        raise LabPersistenceError(_CORRUPT_HISTORY)
    return intent, _parse_recorded_at(recorded_at)


def _parse_recorded_at(value: object) -> UtcInstant:
    try:
        return UtcInstant.parse(value)
    except InvalidUtcInstantError as error:
        raise LabPersistenceError(_CORRUPT_HISTORY) from error


def _string_tuple(value: object) -> tuple[str, ...] | None:
    if type(value) is not list or any(type(item) is not str for item in value):
        return None
    return tuple(value)


def _require_string(value: object) -> str:
    if type(value) is not str:
        raise LabPersistenceError(_CORRUPT_HISTORY)
    return value


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _user_objects(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    return tuple(
        connection.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    )


def _object_signature(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        f"{kind}:{name}"
        for kind, name in connection.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_sequence' ORDER BY type, name"
        ).fetchall()
    )


def _schema_signature(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        " ".join(sql.split())
        for (sql,) in connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    )


def _is_canonical_json(value: str) -> bool:
    try:
        return _canonical_json(json.loads(value)) == value
    except json.JSONDecodeError:
        return False


def _exact_mapping(value: object, fields: frozenset[str]) -> dict[str, object] | None:
    if (
        type(value) is not dict
        or set(value) != fields
        or any(type(key) is not str for key in value)
    ):
        return None
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
