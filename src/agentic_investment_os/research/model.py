"""Define the model-call and Lab durability ports owned by research."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from agentic_investment_os.research.dossier import Dossier, DossierRefusalReason
from agentic_investment_os.research.resolution import (
    CioRefusalReason,
    CioResolution,
    ForecastRefusalReason,
    ResearchArtifact,
    ResearchArtifactRefusal,
    ScenarioForecast,
    SkepticRefusalReason,
    SkepticResult,
    Thesis,
    ThesisRefusalReason,
)

if TYPE_CHECKING:
    from agentic_investment_os.domain.temporal import UtcInstant

__all__ = (
    "MAXIMUM_MODEL_OUTPUT_BYTES",
    "EvidenceCollectorModel",
    "LabCallIntent",
    "LabCallLedger",
    "LabCallObservation",
    "LabCallPreparation",
    "LabCallPreparationDisposition",
    "LabObservationDisposition",
    "ModelCallDisposition",
    "ModelCallRequest",
    "ModelCallResponse",
    "ModelTimingDisposition",
    "ResearchRole",
    "ResearchRoleModel",
    "observation_matches_role",
    "parse_model_configuration_contract_payload",
    "parse_prompt_contract_payload",
    "parse_tool_contract_payloads",
)

MAXIMUM_MODEL_OUTPUT_BYTES = 200_000
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_MODEL_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INVALID_INTENT = "invalid Research Lab model-call intent"
_INVALID_OBSERVATION = "invalid Research Lab model-call observation"
_EVIDENCE_COLLECTOR_INPUT_FIELDS = frozenset(
    {
        "schema_version",
        "role",
        "authority_scope",
        "non_production",
        "namespace",
        "input_kind",
        "subject",
        "evidence",
        "evidence_cutoff",
        "data_regime",
        "constitution",
        "belief_graph",
        "portfolio_context_fingerprint",
        "prompt",
        "model_configuration",
        "tools",
        "material_input_hashes",
    }
)
_RESOLUTION_INPUT_FIELDS = frozenset(
    {
        "schema_version",
        "role",
        "authority_scope",
        "non_production",
        "namespace",
        "input_kind",
        "subject",
        "evidence_cutoff",
        "available_artifact_ids",
        "evidence_manifest",
        "data_regime",
        "constitution",
        "belief_graph",
        "portfolio_context_fingerprint",
        "dossier",
        "thesis",
        "skeptic",
        "forecast",
        "prompt",
        "model_configuration",
        "tools",
        "material_input_hashes",
    }
)
_PROMPT_FIELDS = frozenset({"schema_version", "prompt_id", "content", "content_hash"})
_MODEL_CONFIGURATION_FIELDS = frozenset(
    {"schema_version", "model_identity", "reasoning", "content_hash"}
)
_REASONING_FIELDS = frozenset({"effort", "maximum_output_tokens", "maximum_turns"})
_TOOL_FIELDS = frozenset({"name", "schema_json", "schema_hash"})
_MAXIMUM_PROMPT_CHARACTERS = 20_000
_MAXIMUM_OUTPUT_TOKENS = 20_000
_MAXIMUM_TURNS = 10
_MAXIMUM_TOOL_FINGERPRINTS = 10
_MAXIMUM_TOOL_SCHEMA_CHARACTERS = 20_000


class ModelCallDisposition(StrEnum):
    """Classify the recorded adapter outcome without throwing across the port."""

    RESPONDED = "responded"
    TIMED_OUT = "timed_out"
    QUOTA_EXHAUSTED = "quota_exhausted"
    REFUSED = "refused"


class ModelTimingDisposition(StrEnum):
    """Record whether the adapter completed inside its declared budget."""

    WITHIN_BUDGET = "within_budget"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"


class ResearchRole(StrEnum):
    """Identify one stateless role in the fixed research workflow."""

    EVIDENCE_COLLECTOR = "evidence_collector"
    THESIS_BUILDER = "thesis_builder"
    INDEPENDENT_SKEPTIC = "independent_skeptic"
    SCENARIO_FORECASTER = "scenario_forecaster"
    CIO = "cio"


def parse_prompt_contract_payload(value: object) -> tuple[str, str, str, str] | None:
    """Validate one prompt contract and return its identity-bound material."""
    fields = _exact_mapping(value, _PROMPT_FIELDS)
    if fields is None:
        return None
    prompt_id = fields["prompt_id"]
    content = fields["content"]
    content_hash = fields["content_hash"]
    if (
        fields["schema_version"] != 1
        or type(prompt_id) is not str
        or _IDENTIFIER.fullmatch(prompt_id) is None
        or type(content) is not str
        or not content
        or len(content) > _MAXIMUM_PROMPT_CHARACTERS
        or type(content_hash) is not str
        or _SHA256.fullmatch(content_hash) is None
        or hashlib.sha256(content.encode()).hexdigest() != content_hash
    ):
        return None
    return prompt_id, content, content_hash, _content_hash(fields)


def parse_model_configuration_contract_payload(
    value: object,
) -> tuple[str, str, int, int, str] | None:
    """Validate one model configuration and return its bounded material."""
    fields = _exact_mapping(value, _MODEL_CONFIGURATION_FIELDS)
    if fields is None:
        return None
    reasoning = _exact_mapping(fields["reasoning"], _REASONING_FIELDS)
    if reasoning is None:
        return None
    model_identity = fields["model_identity"]
    effort = reasoning["effort"]
    maximum_output_tokens = reasoning["maximum_output_tokens"]
    maximum_turns = reasoning["maximum_turns"]
    content_hash = fields["content_hash"]
    material = {key: item for key, item in fields.items() if key != "content_hash"}
    if (
        fields["schema_version"] != 1
        or type(model_identity) is not str
        or _MODEL_IDENTITY.fullmatch(model_identity) is None
        or type(effort) is not str
        or effort not in ("low", "medium", "high", "xhigh")
        or type(maximum_output_tokens) is not int
        or not 1 <= maximum_output_tokens <= _MAXIMUM_OUTPUT_TOKENS
        or type(maximum_turns) is not int
        or not 1 <= maximum_turns <= _MAXIMUM_TURNS
        or type(content_hash) is not str
        or _SHA256.fullmatch(content_hash) is None
        or _content_hash(material) != content_hash
    ):
        return None
    return model_identity, effort, maximum_output_tokens, maximum_turns, content_hash


def parse_tool_contract_payloads(
    value: object,
) -> tuple[tuple[str, str, str, str], ...] | None:
    """Validate ordered inert tool contracts and bind names to their schemas."""
    if type(value) is not list or len(value) > _MAXIMUM_TOOL_FINGERPRINTS:
        return None
    tools: list[tuple[str, str, str, str]] = []
    for item in value:
        fields = _exact_mapping(item, _TOOL_FIELDS)
        if fields is None:
            return None
        name = fields["name"]
        schema_json = fields["schema_json"]
        schema_hash = fields["schema_hash"]
        if (
            type(name) is not str
            or _IDENTIFIER.fullmatch(name) is None
            or type(schema_json) is not str
            or not schema_json
            or len(schema_json) > _MAXIMUM_TOOL_SCHEMA_CHARACTERS
            or not _is_canonical_json_object(schema_json)
            or type(schema_hash) is not str
            or _SHA256.fullmatch(schema_hash) is None
            or hashlib.sha256(schema_json.encode()).hexdigest() != schema_hash
        ):
            return None
        tools.append((name, schema_json, schema_hash, _content_hash(fields)))
    if tuple(item[0] for item in tools) != tuple(sorted({item[0] for item in tools})):
        return None
    return tuple(tools)


class LabObservationDisposition(StrEnum):
    """Classify the terminal observation appended for one logical model call."""

    VALIDATED = "validated"
    MODEL_TIMEOUT = "model_timeout"
    QUOTA_EXHAUSTED = "quota_exhausted"
    ADAPTER_REFUSED = "adapter_refused"
    INDETERMINATE_EFFECT = "indeterminate_effect"
    OVERSIZED_OUTPUT = "oversized_output"
    INVALID_JSON = "invalid_json"
    INVALID_ARTIFACT = "invalid_artifact"
    INVALID_DOSSIER = "invalid_dossier"
    MODEL_IDENTITY_MISMATCH = "model_identity_mismatch"


class LabCallPreparationDisposition(StrEnum):
    """Select a new effect, completed replay, or changed-material conflict."""

    EFFECT_REQUIRED = "effect_required"
    REPLAY = "replay"
    CONFLICT = "conflict"
    INDETERMINATE_EFFECT = "indeterminate_effect"


@dataclass(frozen=True, slots=True)
class ModelCallRequest:
    """Supply one reconstructable stateless research-role invocation."""

    call_id: str
    role: ResearchRole
    requested_model_identity: str
    model_input_json: str
    model_input_hash: str
    maximum_output_bytes: int


@dataclass(frozen=True, slots=True)
class ModelCallResponse:
    """Return one bounded recorded adapter observation."""

    disposition: ModelCallDisposition
    raw_response: bytes | None
    exposed_model_identity: str | None
    input_tokens: int
    output_tokens: int
    turns: int
    elapsed_milliseconds: int | None
    timing_disposition: ModelTimingDisposition


class ResearchRoleModel(Protocol):
    """Invoke one stateless, effect-idempotent research-role call."""

    def call(self, request: ModelCallRequest) -> ModelCallResponse: ...


EvidenceCollectorModel = ResearchRoleModel


@dataclass(frozen=True, slots=True)
class LabCallIntent:
    """Persist every material model-visible input before invoking the model port."""

    call_id: str
    role: ResearchRole
    namespace: str
    request_id: str
    request_fingerprint: str
    model_input_json: str
    model_input_hash: str
    prompt_fingerprint: str
    requested_model_identity: str
    model_configuration_fingerprint: str
    tool_fingerprints: tuple[str, ...]
    material_input_hashes: tuple[str, ...]
    maximum_output_bytes: int

    def __post_init__(self) -> None:
        if not _intent_is_valid(self):
            raise ValueError(_INVALID_INTENT)

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record_kind": "lab_model_call_intent",
            "call_id": self.call_id,
            "role": self.role.value,
            "namespace": self.namespace,
            "request_id": self.request_id,
            "request_fingerprint": self.request_fingerprint,
            "model_input_json": self.model_input_json,
            "model_input_hash": self.model_input_hash,
            "prompt_fingerprint": self.prompt_fingerprint,
            "requested_model_identity": self.requested_model_identity,
            "model_configuration_fingerprint": self.model_configuration_fingerprint,
            "tool_fingerprints": list(self.tool_fingerprints),
            "material_input_hashes": list(self.material_input_hashes),
            "maximum_output_bytes": self.maximum_output_bytes,
        }

    @property
    def content_hash(self) -> str:
        return _content_hash(self.to_payload())

    def model_request(self) -> ModelCallRequest:
        return ModelCallRequest(
            call_id=self.call_id,
            role=self.role,
            requested_model_identity=self.requested_model_identity,
            model_input_json=self.model_input_json,
            model_input_hash=self.model_input_hash,
            maximum_output_bytes=self.maximum_output_bytes,
        )


@dataclass(frozen=True, slots=True)
class LabCallObservation:
    """Append the raw-response identity and validated artifact or bounded refusal."""

    call_id: str
    disposition: LabObservationDisposition
    raw_response: bytes | None
    raw_response_hash: str | None
    raw_response_retained: bool
    exposed_model_identity: str | None
    input_tokens: int
    output_tokens: int
    turns: int
    elapsed_milliseconds: int | None
    timing_disposition: ModelTimingDisposition
    artifact: ResearchArtifact | None
    artifact_refusal: DossierRefusalReason | ResearchArtifactRefusal | None

    def __post_init__(self) -> None:
        if not _observation_is_valid(self):
            raise ValueError(_INVALID_OBSERVATION)

    @classmethod
    def create(  # noqa: PLR0913 - observation identity binds effect data and retention.
        cls,
        *,
        call_id: str,
        disposition: LabObservationDisposition,
        response: ModelCallResponse,
        artifact: ResearchArtifact | None = None,
        artifact_refusal: DossierRefusalReason | ResearchArtifactRefusal | None = None,
        retain_raw_response: bool = True,
    ) -> LabCallObservation:
        raw_hash = (
            None
            if response.raw_response is None
            else hashlib.sha256(response.raw_response).hexdigest()
        )
        return cls(
            call_id,
            disposition,
            response.raw_response if retain_raw_response else None,
            raw_hash,
            response.raw_response is not None and retain_raw_response,
            response.exposed_model_identity,
            response.input_tokens,
            response.output_tokens,
            response.turns,
            response.elapsed_milliseconds,
            response.timing_disposition,
            artifact,
            artifact_refusal,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record_kind": "lab_model_call_observation",
            "call_id": self.call_id,
            "disposition": self.disposition.value,
            "raw_response_hash": self.raw_response_hash,
            "raw_response_retained": self.raw_response_retained,
            "exposed_model_identity": self.exposed_model_identity,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "turns": self.turns,
            "elapsed_milliseconds": self.elapsed_milliseconds,
            "timing_disposition": self.timing_disposition.value,
            "artifact": None if self.artifact is None else self.artifact.to_payload(),
            "artifact_refusal": (
                None if self.artifact_refusal is None else self.artifact_refusal.value
            ),
        }

    @property
    def dossier(self) -> Dossier | None:
        return self.artifact if type(self.artifact) is Dossier else None

    @property
    def dossier_refusal(self) -> DossierRefusalReason | None:
        return (
            self.artifact_refusal if type(self.artifact_refusal) is DossierRefusalReason else None
        )


@dataclass(frozen=True, slots=True)
class LabCallPreparation:
    """Return whether Replay must invoke, replay, or refuse one logical call."""

    disposition: LabCallPreparationDisposition
    observation: LabCallObservation | None = None


class LabCallLedger(Protocol):
    """Persist Lab-local intent and terminal model observations append-only."""

    def prepare_call(
        self, intent: LabCallIntent, recorded_at: UtcInstant
    ) -> LabCallPreparation: ...

    def append_observation(
        self,
        intent: LabCallIntent,
        observation: LabCallObservation,
        recorded_at: UtcInstant,
    ) -> LabCallObservation: ...


def _content_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _intent_is_valid(intent: LabCallIntent) -> bool:
    try:
        model_input = json.loads(intent.model_input_json)
    except (TypeError, json.JSONDecodeError):
        return False
    return (
        type(intent.role) is ResearchRole
        and _IDENTIFIER.fullmatch(intent.namespace) is not None
        and _IDENTIFIER.fullmatch(intent.request_id) is not None
        and _SHA256.fullmatch(intent.call_id) is not None
        and intent.call_id
        == _content_hash(
            {
                "namespace": intent.namespace,
                "request_id": intent.request_id,
                "role": intent.role.value,
            }
        )
        and _SHA256.fullmatch(intent.request_fingerprint) is not None
        and intent.request_fingerprint
        == _content_hash(
            {
                "request_id": intent.request_id,
                "namespace": intent.namespace,
                "model_input_hash": intent.model_input_hash,
            }
        )
        and type(model_input) is dict
        and json.dumps(model_input, sort_keys=True, separators=(",", ":"))
        == intent.model_input_json
        and _SHA256.fullmatch(intent.model_input_hash) is not None
        and hashlib.sha256(intent.model_input_json.encode()).hexdigest() == intent.model_input_hash
        and _model_input_matches_intent(model_input, intent)
        and _SHA256.fullmatch(intent.prompt_fingerprint) is not None
        and _MODEL_IDENTITY.fullmatch(intent.requested_model_identity) is not None
        and _SHA256.fullmatch(intent.model_configuration_fingerprint) is not None
        and _valid_hash_tuple(intent.tool_fingerprints, allow_empty=True)
        and _valid_hash_tuple(intent.material_input_hashes, allow_empty=False)
        and intent.maximum_output_bytes == MAXIMUM_MODEL_OUTPUT_BYTES
    )


def _model_input_matches_intent(model_input: dict[object, object], intent: LabCallIntent) -> bool:
    prompt = model_input.get("prompt")
    model_configuration = model_input.get("model_configuration")
    tools = model_input.get("tools")
    material_hashes = model_input.get("material_input_hashes")
    parsed_prompt = parse_prompt_contract_payload(prompt)
    parsed_model = parse_model_configuration_contract_payload(model_configuration)
    parsed_tools = parse_tool_contract_payloads(tools)
    return (
        _model_input_has_exact_role_context(model_input, intent.role)
        and model_input.get("schema_version") == 1
        and model_input.get("role") == intent.role.value
        and model_input.get("authority_scope") == "research_lab_non_production"
        and model_input.get("non_production") is True
        and model_input.get("namespace") == intent.namespace
        and parsed_prompt is not None
        and parsed_prompt[3] == intent.prompt_fingerprint
        and parsed_model is not None
        and parsed_model[0] == intent.requested_model_identity
        and parsed_model[4] == intent.model_configuration_fingerprint
        and parsed_tools is not None
        and tuple(item[3] for item in parsed_tools) == intent.tool_fingerprints
        and type(material_hashes) is list
        and tuple(material_hashes) == intent.material_input_hashes
    )


def _model_input_has_exact_role_context(
    model_input: dict[object, object], role: ResearchRole
) -> bool:
    if role is ResearchRole.EVIDENCE_COLLECTOR:
        return set(model_input) == _EVIDENCE_COLLECTOR_INPUT_FIELDS
    if set(model_input) != _RESOLUTION_INPUT_FIELDS:
        return False
    thesis = model_input.get("thesis")
    skeptic = model_input.get("skeptic")
    forecast = model_input.get("forecast")
    if role is ResearchRole.THESIS_BUILDER:
        return thesis is None and skeptic is None and forecast is None
    if role is ResearchRole.INDEPENDENT_SKEPTIC:
        return type(thesis) is dict and skeptic is None and forecast is None
    if role is ResearchRole.SCENARIO_FORECASTER:
        return type(thesis) is dict and type(skeptic) is dict and forecast is None
    return type(thesis) is dict and type(skeptic) is dict and type(forecast) is dict


def _exact_mapping(value: object, fields: frozenset[str]) -> dict[object, object] | None:
    return value if type(value) is dict and set(value) == fields else None


def _valid_hash_tuple(value: object, *, allow_empty: bool) -> bool:
    return (
        type(value) is tuple
        and (allow_empty or bool(value))
        and value == tuple(sorted(set(value)))
        and all(type(item) is str and _SHA256.fullmatch(item) is not None for item in value)
    )


def _is_canonical_json_object(value: str) -> bool:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return False
    return (
        type(parsed) is dict and json.dumps(parsed, sort_keys=True, separators=(",", ":")) == value
    )


def _observation_is_valid(observation: LabCallObservation) -> bool:
    if (
        type(observation.call_id) is not str
        or _SHA256.fullmatch(observation.call_id) is None
        or type(observation.disposition) is not LabObservationDisposition
        or (observation.raw_response is not None and type(observation.raw_response) is not bytes)
        or (
            observation.raw_response_hash is not None
            and (
                type(observation.raw_response_hash) is not str
                or _SHA256.fullmatch(observation.raw_response_hash) is None
            )
        )
        or type(observation.raw_response_retained) is not bool
        or observation.raw_response_retained != (observation.raw_response is not None)
        or (
            observation.raw_response is not None
            and (
                len(observation.raw_response) > MAXIMUM_MODEL_OUTPUT_BYTES
                or hashlib.sha256(observation.raw_response).hexdigest()
                != observation.raw_response_hash
            )
        )
        or (
            observation.exposed_model_identity is not None
            and (
                type(observation.exposed_model_identity) is not str
                or _MODEL_IDENTITY.fullmatch(observation.exposed_model_identity) is None
            )
        )
        or type(observation.input_tokens) is not int
        or observation.input_tokens < 0
        or type(observation.output_tokens) is not int
        or observation.output_tokens < 0
        or type(observation.turns) is not int
        or observation.turns < 0
        or (
            observation.elapsed_milliseconds is not None
            and (
                type(observation.elapsed_milliseconds) is not int
                or observation.elapsed_milliseconds < 0
            )
        )
        or type(observation.timing_disposition) is not ModelTimingDisposition
        or (observation.artifact is not None and not _artifact_is_exact(observation.artifact))
        or (
            observation.artifact_refusal is not None
            and not _artifact_refusal_is_exact(observation.artifact_refusal)
        )
    ):
        return False
    validated = observation.disposition is LabObservationDisposition.VALIDATED
    invalid_artifact = observation.disposition in (
        LabObservationDisposition.INVALID_ARTIFACT,
        LabObservationDisposition.INVALID_DOSSIER,
    )
    oversized = observation.disposition is LabObservationDisposition.OVERSIZED_OUTPUT
    if (
        validated != (observation.artifact is not None and observation.artifact_refusal is None)
        or invalid_artifact
        != (observation.artifact is None and observation.artifact_refusal is not None)
        or (
            not validated
            and not invalid_artifact
            and (observation.artifact is not None or observation.artifact_refusal is not None)
        )
        or (
            oversized
            and (observation.raw_response_retained or observation.raw_response_hash is None)
        )
        or (
            not oversized
            and not observation.raw_response_retained
            and observation.raw_response_hash is not None
        )
    ):
        return False
    try:
        if observation.artifact is not None:
            observation.artifact.__post_init__()
    except (AttributeError, TypeError, ValueError):
        return False
    return True


def _artifact_is_exact(value: object) -> bool:
    return type(value) in (Dossier, Thesis, SkepticResult, ScenarioForecast, CioResolution)


def _artifact_refusal_is_exact(value: object) -> bool:
    return type(value) in (
        DossierRefusalReason,
        ThesisRefusalReason,
        SkepticRefusalReason,
        ForecastRefusalReason,
        CioRefusalReason,
    )


def observation_matches_role(observation: LabCallObservation, role: ResearchRole) -> bool:
    """Return whether an observation carries the exact artifact type owned by its role."""
    artifact_types = {
        ResearchRole.EVIDENCE_COLLECTOR: Dossier,
        ResearchRole.THESIS_BUILDER: Thesis,
        ResearchRole.INDEPENDENT_SKEPTIC: SkepticResult,
        ResearchRole.SCENARIO_FORECASTER: ScenarioForecast,
        ResearchRole.CIO: CioResolution,
    }
    refusal_types = {
        ResearchRole.EVIDENCE_COLLECTOR: DossierRefusalReason,
        ResearchRole.THESIS_BUILDER: ThesisRefusalReason,
        ResearchRole.INDEPENDENT_SKEPTIC: SkepticRefusalReason,
        ResearchRole.SCENARIO_FORECASTER: ForecastRefusalReason,
        ResearchRole.CIO: CioRefusalReason,
    }
    if observation.disposition is LabObservationDisposition.VALIDATED:
        return type(observation.artifact) is artifact_types[role]
    if observation.disposition is LabObservationDisposition.INVALID_DOSSIER:
        return (
            role is ResearchRole.EVIDENCE_COLLECTOR
            and type(observation.artifact_refusal) is DossierRefusalReason
        )
    if observation.disposition is LabObservationDisposition.INVALID_ARTIFACT:
        return (
            role is not ResearchRole.EVIDENCE_COLLECTOR
            and type(observation.artifact_refusal) is refusal_types[role]
        )
    return observation.artifact is None and observation.artifact_refusal is None
