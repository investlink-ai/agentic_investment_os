"""Run the bounded production research workflow over exact Stage 2 selections."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeGuard

from agentic_investment_os.domain.lifecycle import (
    NoActionReason,
    ProductionResearchReference,
    ResearchCheckpoint,
    ResearchRefusal,
)
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.research.authority import ResearchAuthority
from agentic_investment_os.research.dossier import Dossier, DossierRefusalReason, parse_dossier
from agentic_investment_os.research.model import (
    MAXIMUM_MODEL_OUTPUT_BYTES,
    LabCallObservation,
    LabCallPreparationDisposition,
    LabObservationDisposition,
    ModelCallDisposition,
    ModelCallRequest,
    ModelCallResponse,
    ModelTimingDisposition,
    ResearchRole,
    ResearchRoleModel,
    parse_model_configuration_contract_payload,
    parse_prompt_contract_payload,
    parse_tool_contract_payloads,
)
from agentic_investment_os.research.resolution import (
    CioResolution,
    CioStance,
    ResearchArtifactRefusal,
    ScenarioForecast,
    SkepticDecision,
    SkepticResult,
    Thesis,
    parse_cio_resolution,
    parse_scenario_forecast,
    parse_skeptic_result,
    parse_thesis,
)

if TYPE_CHECKING:
    from agentic_investment_os.domain.attention import AttentionArtifact
    from agentic_investment_os.domain.governance import ConstitutionArtifact
    from agentic_investment_os.domain.identity import EquityInstrumentIdentity
    from agentic_investment_os.domain.universe import PositionSnapshot
    from agentic_investment_os.research.policy import (
        ModelConfiguration,
        ProductionResearchPolicy,
        ResearchRoleContract,
    )

__all__ = (
    "ProductionBeliefGraph",
    "ProductionCallIntent",
    "ProductionCallLedger",
    "ProductionCallPreparation",
    "ProductionModelResponseRecord",
    "ProductionResearch",
    "ProductionResearchBuild",
    "ProductionResearchContext",
    "ProductionResearchEvidence",
    "ProductionResearchHistoryValidator",
    "ProductionResearchResolution",
    "ProductionResearchRun",
    "ProductionResearchSubject",
    "production_call_refusal_identity",
    "production_response_disposition",
)

_INVALID_INTENT = "invalid production research call intent"
_MISSING_REPLAY_OBSERVATION = "production research replay omitted its observation"
_SHA256_LENGTH = 64
_MAXIMUM_MODEL_RESOURCE_VALUE = 2**63 - 1
_MAXIMUM_REPORTED_TEXT_LENGTH = 256
_MODEL_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_MODEL_INPUT_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "authority_scope",
        "non_production",
        "run_id",
        "request_id",
        "role",
        "subject",
        "evidence_cutoff",
        "data_regime",
        "attention_artifact",
        "evidence",
        "constitution",
        "belief_graph",
        "portfolio_context",
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


@dataclass(frozen=True, slots=True)
class ProductionResearchEvidence:
    """Pin immutable evidence content and its complete provenance envelope."""

    artifact_id: str
    content_hash: str
    available_at: UtcInstant
    artifact_payload_json: str
    content: bytes
    official: bool

    def __post_init__(self) -> None:
        try:
            payload = json.loads(self.artifact_payload_json)
        except json.JSONDecodeError as error:
            raise ValueError(_INVALID_INTENT) from error
        material_fingerprints = (
            payload.get("material_fingerprints") if type(payload) is dict else None
        )
        if (
            not _is_sha256(self.artifact_id)
            or not _is_sha256(self.content_hash)
            or hashlib.sha256(self.content).hexdigest() != self.content_hash
            or type(self.available_at) is not UtcInstant
            or _canonical_json(payload) != self.artifact_payload_json
            or type(payload) is not dict
            or payload.get("content_hash") != self.artifact_id
            or payload.get("available_at") != self.available_at.isoformat()
            or type(material_fingerprints) is not dict
            or material_fingerprints.get("source_content") != self.content_hash
            or type(self.official) is not bool
        ):
            raise ValueError(_INVALID_INTENT)

    @property
    def is_official(self) -> bool:
        """Return whether this record came from an admitted official source."""
        return self.official


@dataclass(frozen=True, slots=True)
class ProductionBeliefGraph:
    """Carry a hash-pinned graph payload and deterministic prior-event identities."""

    payload_json: str
    content_hash: str
    belief_events: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        try:
            payload = json.loads(self.payload_json)
        except json.JSONDecodeError as error:
            raise ValueError(_INVALID_INTENT) from error
        if (
            not _is_sha256(self.content_hash)
            or type(payload) is not dict
            or payload.get("content_hash") != self.content_hash
            or _canonical_json(payload) != self.payload_json
            or type(self.belief_events) is not tuple
            or any(
                not _is_sha256(belief_id) or not _is_sha256(event_id)
                for belief_id, event_id in self.belief_events
            )
        ):
            raise ValueError(_INVALID_INTENT)

    def to_payload(self) -> dict[str, object]:
        payload = json.loads(self.payload_json)
        if type(payload) is not dict:  # pragma: no cover - construction proves this invariant.
            raise ValueError(_INVALID_INTENT)
        return payload

    def prior_event_id(self, belief_id: str) -> str | None:
        matches = tuple(
            event_id
            for candidate_belief_id, event_id in self.belief_events
            if candidate_belief_id == belief_id
        )
        return None if not matches else matches[-1]


@dataclass(frozen=True, slots=True)
class ProductionResearchSubject:
    """Bind one exact attention request to its subject-local captured evidence."""

    request_id: str
    subject: EquityInstrumentIdentity
    evidence: tuple[ProductionResearchEvidence, ...]


@dataclass(frozen=True, slots=True)
class ProductionResearchContext:
    """Carry every model-visible production input pinned for one run."""

    run_id: str
    cutoff: UtcInstant
    data_regime: str
    constitution: ConstitutionArtifact
    position_snapshot: PositionSnapshot
    attention_artifact: AttentionArtifact
    subjects: tuple[ProductionResearchSubject, ...]
    belief_graphs: tuple[tuple[str, ProductionBeliefGraph], ...]

    def graph_for(self, request_id: str) -> ProductionBeliefGraph:
        return dict(self.belief_graphs)[request_id]


@dataclass(frozen=True, slots=True)
class ProductionCallIntent:
    """Persist an exact production model-call intent before invoking its effect."""

    call_id: str
    run_id: str
    request_id: str
    role: ResearchRole
    model_input_json: str
    model_input_hash: str
    prompt_fingerprint: str
    requested_model_identity: str
    model_configuration_fingerprint: str
    tool_fingerprints: tuple[str, ...]
    material_input_hashes: tuple[str, ...]
    maximum_output_bytes: int

    def __post_init__(self) -> None:
        if type(self.role) is not ResearchRole:
            raise ValueError(_INVALID_INTENT)
        expected_call_id = _content_hash(
            {
                "run_id": self.run_id,
                "request_id": self.request_id,
                "role": self.role.value,
            }
        )
        try:
            parsed = json.loads(self.model_input_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(_INVALID_INTENT) from error
        if (
            not _is_sha256(self.call_id)
            or not _is_sha256(self.run_id)
            or not _is_sha256(self.request_id)
            or self.call_id != expected_call_id
            or hashlib.sha256(self.model_input_json.encode()).hexdigest() != self.model_input_hash
            or self.maximum_output_bytes != MAXIMUM_MODEL_OUTPUT_BYTES
            or tuple(sorted(set(self.tool_fingerprints))) != self.tool_fingerprints
            or tuple(sorted(set(self.material_input_hashes))) != self.material_input_hashes
            or not self.material_input_hashes
            or _canonical_json(parsed) != self.model_input_json
            or not _model_input_matches_intent(parsed, self)
        ):
            raise ValueError(_INVALID_INTENT)

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record_kind": "production_research_call_intent",
            "call_id": self.call_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "role": self.role.value,
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
            self.call_id,
            self.role,
            self.requested_model_identity,
            self.model_input_json,
            self.model_input_hash,
            self.maximum_output_bytes,
        )


@dataclass(frozen=True, slots=True)
class ProductionCallPreparation:
    """Select one new model effect, exact replay, conflict, or indeterminate effect."""

    disposition: LabCallPreparationDisposition
    observation: LabCallObservation | None = None


@dataclass(frozen=True, slots=True)
class ProductionModelResponseRecord:
    """Preserve safe reported model fields and their boundary-validation results."""

    response_type_valid: bool
    metadata_valid: bool
    disposition_valid: bool
    disposition: str | None
    raw_response_valid: bool
    raw_response: bytes | None
    raw_response_hash: str | None
    raw_response_retained: bool
    exposed_model_identity_valid: bool
    exposed_model_identity: str | None
    input_tokens_valid: bool
    input_tokens: int | None
    output_tokens_valid: bool
    output_tokens: int | None
    turns_valid: bool
    turns: int | None
    elapsed_milliseconds_valid: bool
    elapsed_milliseconds: int | None
    timing_disposition_valid: bool
    timing_disposition: str | None

    def __post_init__(self) -> None:
        validity = (
            self.response_type_valid,
            self.metadata_valid,
            self.disposition_valid,
            self.raw_response_valid,
            self.raw_response_retained,
            self.exposed_model_identity_valid,
            self.input_tokens_valid,
            self.output_tokens_valid,
            self.turns_valid,
            self.elapsed_milliseconds_valid,
            self.timing_disposition_valid,
        )
        numeric_values = (
            self.input_tokens,
            self.output_tokens,
            self.turns,
            self.elapsed_milliseconds,
        )
        if (
            any(type(item) is not bool for item in validity)
            or (self.disposition is not None and type(self.disposition) is not str)
            or (
                self.exposed_model_identity is not None
                and type(self.exposed_model_identity) is not str
            )
            or (self.timing_disposition is not None and type(self.timing_disposition) is not str)
            or any(
                item is not None
                and (type(item) is not int or abs(item) > _MAXIMUM_MODEL_RESOURCE_VALUE)
                for item in numeric_values
            )
            or (self.raw_response is not None and type(self.raw_response) is not bytes)
            or (self.raw_response_hash is not None and not _is_sha256(self.raw_response_hash))
            or (
                not self.raw_response_valid
                and (
                    self.raw_response is not None
                    or self.raw_response_hash is not None
                    or self.raw_response_retained
                )
            )
            or (self.raw_response_retained and self.raw_response is None)
            or (
                self.raw_response is not None
                and (
                    self.raw_response_retained
                    != (len(self.raw_response) <= MAXIMUM_MODEL_OUTPUT_BYTES)
                    or hashlib.sha256(self.raw_response).hexdigest() != self.raw_response_hash
                )
            )
            or (
                not self.response_type_valid
                and (
                    any(validity[2:])
                    or self.disposition is not None
                    or self.raw_response_hash is not None
                    or self.exposed_model_identity is not None
                    or any(item is not None for item in numeric_values)
                    or self.timing_disposition is not None
                )
            )
            or (
                self.disposition_valid
                and self.disposition not in {item.value for item in ModelCallDisposition}
            )
            or (
                self.raw_response_valid
                and (
                    (self.raw_response_hash is None) != (self.raw_response is None)
                    and not (
                        self.raw_response is None
                        and self.raw_response_hash is not None
                        and not self.raw_response_retained
                    )
                )
            )
            or (
                self.exposed_model_identity_valid
                and self.exposed_model_identity is not None
                and _MODEL_IDENTITY.fullmatch(self.exposed_model_identity) is None
            )
            or (
                not self.exposed_model_identity_valid
                and self.exposed_model_identity is not None
                and _MODEL_IDENTITY.fullmatch(self.exposed_model_identity) is not None
            )
            or (self.input_tokens_valid and not _model_resource_is_valid(self.input_tokens))
            or (not self.input_tokens_valid and _model_resource_is_valid(self.input_tokens))
            or (self.output_tokens_valid and not _model_resource_is_valid(self.output_tokens))
            or (not self.output_tokens_valid and _model_resource_is_valid(self.output_tokens))
            or (self.turns_valid and not _model_resource_is_valid(self.turns))
            or (not self.turns_valid and _model_resource_is_valid(self.turns))
            or (
                self.elapsed_milliseconds_valid
                and self.elapsed_milliseconds is not None
                and not _model_resource_is_valid(self.elapsed_milliseconds)
            )
            or (
                not self.elapsed_milliseconds_valid
                and self.elapsed_milliseconds is not None
                and _model_resource_is_valid(self.elapsed_milliseconds)
            )
            or (
                self.timing_disposition_valid
                and self.timing_disposition not in {item.value for item in ModelTimingDisposition}
            )
            or (
                self.metadata_valid
                != (
                    self.response_type_valid
                    and all(
                        (
                            self.disposition_valid,
                            self.raw_response_valid,
                            self.exposed_model_identity_valid,
                            self.input_tokens_valid,
                            self.output_tokens_valid,
                            self.turns_valid,
                            self.elapsed_milliseconds_valid,
                            self.timing_disposition_valid,
                        )
                    )
                    and _reported_timing_matches(self.disposition, self.timing_disposition)
                )
            )
        ):
            raise ValueError(_INVALID_INTENT)

    def normalized_response(self) -> ModelCallResponse:
        """Return validated model facts or a bounded refusal retaining safe raw bytes."""
        if not self.metadata_valid:
            return ModelCallResponse(
                ModelCallDisposition.REFUSED,
                self.raw_response if self.raw_response_valid else None,
                (self.exposed_model_identity if self.exposed_model_identity_valid else None),
                self.input_tokens
                if self.input_tokens_valid and self.input_tokens is not None
                else 0,
                (
                    self.output_tokens
                    if self.output_tokens_valid and self.output_tokens is not None
                    else 0
                ),
                self.turns if self.turns_valid and self.turns is not None else 0,
                self.elapsed_milliseconds if self.elapsed_milliseconds_valid else None,
                ModelTimingDisposition.UNAVAILABLE,
            )
        if (
            self.disposition is None
            or self.input_tokens is None
            or self.output_tokens is None
            or self.turns is None
            or self.timing_disposition is None
        ):  # pragma: no cover - construction proves complete valid metadata.
            raise ValueError(_INVALID_INTENT)
        return ModelCallResponse(
            ModelCallDisposition(self.disposition),
            self.raw_response,
            self.exposed_model_identity,
            self.input_tokens,
            self.output_tokens,
            self.turns,
            self.elapsed_milliseconds,
            ModelTimingDisposition(self.timing_disposition),
        )

    @property
    def persisted_raw_response(self) -> bytes | None:
        """Return raw bytes only when the production retention bound admits them."""
        return self.raw_response if self.raw_response_retained else None

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record_kind": "production_model_response_record",
            "response_type_valid": self.response_type_valid,
            "metadata_valid": self.metadata_valid,
            "disposition_valid": self.disposition_valid,
            "disposition": self.disposition,
            "raw_response_valid": self.raw_response_valid,
            "raw_response_hash": self.raw_response_hash,
            "raw_response_retained": self.raw_response_retained,
            "exposed_model_identity_valid": self.exposed_model_identity_valid,
            "exposed_model_identity": self.exposed_model_identity,
            "input_tokens_valid": self.input_tokens_valid,
            "input_tokens": self.input_tokens,
            "output_tokens_valid": self.output_tokens_valid,
            "output_tokens": self.output_tokens,
            "turns_valid": self.turns_valid,
            "turns": self.turns,
            "elapsed_milliseconds_valid": self.elapsed_milliseconds_valid,
            "elapsed_milliseconds": self.elapsed_milliseconds,
            "timing_disposition_valid": self.timing_disposition_valid,
            "timing_disposition": self.timing_disposition,
        }

    @classmethod
    def parse(
        cls,
        value: object,
        raw_response: bytes | None,
    ) -> ProductionModelResponseRecord | None:
        """Reconstruct one canonical durable response-provenance record."""
        required = {
            "schema_version",
            "record_kind",
            "response_type_valid",
            "metadata_valid",
            "disposition_valid",
            "disposition",
            "raw_response_valid",
            "raw_response_hash",
            "raw_response_retained",
            "exposed_model_identity_valid",
            "exposed_model_identity",
            "input_tokens_valid",
            "input_tokens",
            "output_tokens_valid",
            "output_tokens",
            "turns_valid",
            "turns",
            "elapsed_milliseconds_valid",
            "elapsed_milliseconds",
            "timing_disposition_valid",
            "timing_disposition",
        }
        if (
            type(value) is not dict
            or set(value) != required
            or value["schema_version"] != 1
            or value["record_kind"] != "production_model_response_record"
            or any(
                type(value[field]) is not bool
                for field in (
                    "response_type_valid",
                    "metadata_valid",
                    "disposition_valid",
                    "raw_response_valid",
                    "raw_response_retained",
                    "exposed_model_identity_valid",
                    "input_tokens_valid",
                    "output_tokens_valid",
                    "turns_valid",
                    "elapsed_milliseconds_valid",
                    "timing_disposition_valid",
                )
            )
            or any(
                value[field] is not None and type(value[field]) is not str
                for field in (
                    "disposition",
                    "raw_response_hash",
                    "exposed_model_identity",
                    "timing_disposition",
                )
            )
            or any(
                value[field] is not None and type(value[field]) is not int
                for field in (
                    "input_tokens",
                    "output_tokens",
                    "turns",
                    "elapsed_milliseconds",
                )
            )
        ):
            return None
        try:
            record = cls(
                value["response_type_valid"],
                value["metadata_valid"],
                value["disposition_valid"],
                value["disposition"],
                value["raw_response_valid"],
                raw_response,
                value["raw_response_hash"],
                value["raw_response_retained"],
                value["exposed_model_identity_valid"],
                value["exposed_model_identity"],
                value["input_tokens_valid"],
                value["input_tokens"],
                value["output_tokens_valid"],
                value["output_tokens"],
                value["turns_valid"],
                value["turns"],
                value["elapsed_milliseconds_valid"],
                value["elapsed_milliseconds"],
                value["timing_disposition_valid"],
                value["timing_disposition"],
            )
        except (TypeError, ValueError):
            return None
        return record if record.to_payload() == value else None


class ProductionCallLedger(Protocol):
    """Persist production research intents and observations append-only."""

    def prepare_call(
        self,
        intent: ProductionCallIntent,
        recorded_at: UtcInstant,
    ) -> ProductionCallPreparation: ...

    def append_observation(
        self,
        intent: ProductionCallIntent,
        observation: LabCallObservation,
        response_record: ProductionModelResponseRecord,
        recorded_at: UtcInstant,
    ) -> LabCallObservation: ...


class ProductionResearchHistoryValidator(Protocol):
    """Validate complete durable production research history without invoking effects."""

    def validate_history(self, references: tuple[ProductionResearchReference, ...]) -> None: ...


@dataclass(frozen=True, slots=True)
class ProductionResearchBuild:
    """Return validated Dossiers or one bounded fail-closed identity."""

    checkpoint: ResearchCheckpoint | None
    dossiers: tuple[tuple[str, Dossier], ...]
    refusal: ResearchRefusal | None


@dataclass(frozen=True, slots=True)
class ProductionResearchResolution:
    """Carry one validated subject workflow for deterministic memory admission."""

    request_id: str
    dossier: Dossier
    thesis: Thesis
    skeptic: SkepticResult
    forecast: ScenarioForecast | None
    cio: CioResolution | None


@dataclass(frozen=True, slots=True)
class ProductionResearchRun:
    """Return validated resolutions, their resources, or one bounded refusal."""

    checkpoint: ResearchCheckpoint | None
    resolutions: tuple[ProductionResearchResolution, ...]
    no_action_reason: NoActionReason | None
    refusal: ResearchRefusal | None


@dataclass(frozen=True, slots=True)
class _CallResult:
    intent: ProductionCallIntent
    observation: LabCallObservation


@dataclass(frozen=True, slots=True)
class ProductionResearch:
    """Run fixed-order stateless roles through intent-first durable model calls."""

    policy: ProductionResearchPolicy
    ledger: ProductionCallLedger
    model: ResearchRoleModel

    def build_dossiers(
        self,
        context: ProductionResearchContext,
        recorded_at: UtcInstant,
    ) -> ProductionResearchBuild:
        if not context.subjects:
            return ProductionResearchBuild(ResearchCheckpoint(), (), None)
        dossiers: list[tuple[str, Dossier]] = []
        calls: list[_CallResult] = []
        for subject in context.subjects:
            if (
                not subject.evidence
                or len(subject.evidence) > self.policy.maximum_evidence_artifacts
                or not any(item.is_official for item in subject.evidence)
            ):
                return ProductionResearchBuild(
                    None,
                    (),
                    _input_refusal(
                        context,
                        subject.request_id,
                        "invalid_subject_evidence_coverage",
                        calls,
                        tuple(dossier.content_hash for _, dossier in dossiers),
                    ),
                )
            call = self._call(
                context,
                subject,
                self.policy.contract_for(ResearchRole.EVIDENCE_COLLECTOR),
                recorded_at,
                dossier=None,
                thesis=None,
                skeptic=None,
                forecast=None,
            )
            if call.observation.disposition is not LabObservationDisposition.VALIDATED:
                return ProductionResearchBuild(
                    None,
                    (),
                    _call_refusal(
                        (*calls, call), tuple(dossier.content_hash for _, dossier in dossiers)
                    ),
                )
            dossier = call.observation.artifact
            if type(dossier) is not Dossier:
                return ProductionResearchBuild(
                    None,
                    (),
                    _call_refusal((*calls, call), tuple(item.content_hash for _, item in dossiers)),
                )
            dossiers.append((subject.request_id, dossier))
            calls.append(call)
        return ProductionResearchBuild(
            _checkpoint(calls, tuple(dossier.content_hash for _, dossier in dossiers)),
            tuple(dossiers),
            None,
        )

    def run_research(  # noqa: PLR0911 - preserve role-local fail-closed outcomes.
        self,
        context: ProductionResearchContext,
        build: ProductionResearchBuild,
        recorded_at: UtcInstant,
    ) -> ProductionResearchRun:
        if build.refusal is not None or build.checkpoint is None:
            return ProductionResearchRun(None, (), None, build.refusal)
        if not build.dossiers:
            return ProductionResearchRun(
                ResearchCheckpoint(),
                (),
                NoActionReason.NO_ATTENTION,
                None,
            )
        subjects = {item.request_id: item for item in context.subjects}
        resolutions: list[ProductionResearchResolution] = []
        calls: list[_CallResult] = []
        artifact_ids: list[str] = []
        no_action: NoActionReason | None = None
        for request_id, dossier in build.dossiers:
            subject = subjects[request_id]
            thesis_call = self._call(
                context,
                subject,
                self.policy.contract_for(ResearchRole.THESIS_BUILDER),
                recorded_at,
                dossier=dossier,
                thesis=None,
                skeptic=None,
                forecast=None,
            )
            thesis = thesis_call.observation.artifact
            if type(thesis) is not Thesis:
                return ProductionResearchRun(
                    None,
                    (),
                    None,
                    _call_refusal((*calls, thesis_call), tuple(artifact_ids)),
                )
            calls.append(thesis_call)
            artifact_ids.append(thesis.content_hash)
            if any(condition.active for condition in thesis.uninvestable_conditions):
                no_action = NoActionReason.NO_VALID_THESIS
                continue
            skeptic_call = self._call(
                context,
                subject,
                self.policy.contract_for(ResearchRole.INDEPENDENT_SKEPTIC),
                recorded_at,
                dossier=dossier,
                thesis=thesis,
                skeptic=None,
                forecast=None,
            )
            skeptic = skeptic_call.observation.artifact
            if type(skeptic) is not SkepticResult:
                return ProductionResearchRun(
                    None,
                    (),
                    None,
                    _call_refusal((*calls, skeptic_call), tuple(artifact_ids)),
                )
            calls.append(skeptic_call)
            artifact_ids.append(skeptic.content_hash)
            if skeptic.decision is SkepticDecision.REJECT:
                no_action = NoActionReason.SKEPTIC_REJECTED
                resolutions.append(
                    ProductionResearchResolution(request_id, dossier, thesis, skeptic, None, None)
                )
                continue
            if skeptic.decision is SkepticDecision.REQUEST_EVIDENCE:
                return ProductionResearchRun(
                    None,
                    (),
                    None,
                    _input_refusal(
                        context,
                        request_id,
                        "skeptic_requested_evidence",
                        calls,
                        tuple(artifact_ids),
                    ),
                )
            forecast_call = self._call(
                context,
                subject,
                self.policy.contract_for(ResearchRole.SCENARIO_FORECASTER),
                recorded_at,
                dossier=dossier,
                thesis=thesis,
                skeptic=skeptic,
                forecast=None,
            )
            forecast = forecast_call.observation.artifact
            if type(forecast) is not ScenarioForecast:
                return ProductionResearchRun(
                    None,
                    (),
                    None,
                    _call_refusal((*calls, forecast_call), tuple(artifact_ids)),
                )
            calls.append(forecast_call)
            artifact_ids.append(forecast.content_hash)
            cio_call = self._call(
                context,
                subject,
                self.policy.contract_for(ResearchRole.CIO),
                recorded_at,
                dossier=dossier,
                thesis=thesis,
                skeptic=skeptic,
                forecast=forecast,
            )
            cio = cio_call.observation.artifact
            if type(cio) is not CioResolution:
                return ProductionResearchRun(
                    None,
                    (),
                    None,
                    _call_refusal((*calls, cio_call), tuple(artifact_ids)),
                )
            calls.append(cio_call)
            artifact_ids.append(cio.content_hash)
            if cio.stance is CioStance.ABSTAIN:
                no_action = NoActionReason.CIO_ABSTAINED
            resolutions.append(
                ProductionResearchResolution(request_id, dossier, thesis, skeptic, forecast, cio)
            )
        active = tuple(
            item
            for item in resolutions
            if item.cio is not None and item.cio.stance is not CioStance.ABSTAIN
        )
        if active and no_action is not None:
            no_action = None
        return ProductionResearchRun(
            _checkpoint(calls, tuple(artifact_ids)),
            tuple(resolutions),
            no_action,
            None,
        )

    def _call(  # noqa: PLR0913 - every role predecessor remains explicit.
        self,
        context: ProductionResearchContext,
        subject: ProductionResearchSubject,
        contract: ResearchRoleContract,
        recorded_at: UtcInstant,
        *,
        dossier: Dossier | None,
        thesis: Thesis | None,
        skeptic: SkepticResult | None,
        forecast: ScenarioForecast | None,
    ) -> _CallResult:
        intent = _intent(
            context,
            subject,
            contract,
            dossier=dossier,
            thesis=thesis,
            skeptic=skeptic,
            forecast=forecast,
        )
        preparation = self.ledger.prepare_call(intent, recorded_at)
        if preparation.disposition is LabCallPreparationDisposition.REPLAY:
            observation = preparation.observation
            if observation is None:
                raise ValueError(_MISSING_REPLAY_OBSERVATION)
            return _CallResult(intent, observation)
        if preparation.disposition is not LabCallPreparationDisposition.EFFECT_REQUIRED:
            observation = _preparation_refusal(intent, preparation.disposition)
            if preparation.disposition is LabCallPreparationDisposition.INDETERMINATE_EFFECT:
                observation = self.ledger.append_observation(
                    intent,
                    observation,
                    _model_response_record(None),
                    recorded_at,
                )
            return _CallResult(intent, observation)
        response_record = _model_response_record(self.model.call(intent.model_request()))
        response = response_record.normalized_response()
        observation = _observe(
            intent,
            contract.model_configuration,
            response,
            subject,
            dossier=dossier,
            thesis=thesis,
            skeptic=skeptic,
            forecast=forecast,
        )
        stored = self.ledger.append_observation(
            intent,
            observation,
            response_record,
            recorded_at,
        )
        return _CallResult(intent, stored)


def _intent(  # noqa: PLR0913 - the role context deliberately names all predecessors.
    context: ProductionResearchContext,
    subject: ProductionResearchSubject,
    contract: ResearchRoleContract,
    *,
    dossier: Dossier | None,
    thesis: Thesis | None,
    skeptic: SkepticResult | None,
    forecast: ScenarioForecast | None,
) -> ProductionCallIntent:
    graph = context.graph_for(subject.request_id)
    evidence = [_evidence_payload(item, subject.subject) for item in subject.evidence]
    material_hashes = {
        context.attention_artifact.content_hash,
        context.constitution.content_hash,
        context.position_snapshot.fingerprint,
        graph.content_hash,
        contract.prompt.fingerprint,
        contract.model_configuration.content_hash,
        *(tool.fingerprint for tool in contract.tools),
        *(item.content_hash for item in subject.evidence),
    }
    for artifact in (dossier, thesis, skeptic, forecast):
        if artifact is not None:
            material_hashes.add(artifact.content_hash)
    payload = {
        "schema_version": 1,
        "record_kind": "production_research_role_input",
        "authority_scope": ResearchAuthority.PRODUCTION.value,
        "non_production": False,
        "run_id": context.run_id,
        "request_id": subject.request_id,
        "role": contract.role.value,
        "subject": subject.subject.to_payload(),
        "evidence_cutoff": context.cutoff.isoformat(),
        "data_regime": context.data_regime,
        "attention_artifact": context.attention_artifact.to_payload(),
        "evidence": evidence,
        "constitution": context.constitution.to_payload(),
        "belief_graph": graph.to_payload(),
        "portfolio_context": context.position_snapshot.to_payload(),
        "dossier": None if dossier is None else dossier.to_payload(),
        "thesis": None if thesis is None else thesis.to_payload(),
        "skeptic": None if skeptic is None else skeptic.to_payload(),
        "forecast": None if forecast is None else forecast.to_payload(),
        "prompt": contract.prompt.to_payload(),
        "model_configuration": contract.model_configuration.to_payload(),
        "tools": [tool.to_payload() for tool in contract.tools],
        "material_input_hashes": sorted(material_hashes),
    }
    model_input_json = _canonical_json(payload)
    call_id = _content_hash(
        {
            "run_id": context.run_id,
            "request_id": subject.request_id,
            "role": contract.role.value,
        }
    )
    return ProductionCallIntent(
        call_id,
        context.run_id,
        subject.request_id,
        contract.role,
        model_input_json,
        hashlib.sha256(model_input_json.encode()).hexdigest(),
        contract.prompt.fingerprint,
        contract.model_configuration.model_identity,
        contract.model_configuration.content_hash,
        tuple(tool.fingerprint for tool in contract.tools),
        tuple(sorted(material_hashes)),
        MAXIMUM_MODEL_OUTPUT_BYTES,
    )


def _evidence_payload(
    record: ProductionResearchEvidence,
    subject: EquityInstrumentIdentity,
) -> dict[str, object]:
    return {
        "artifact_id": record.artifact_id,
        "content_hash": record.content_hash,
        "available_at": record.available_at.isoformat(),
        "subject": subject.to_payload(),
        "content": record.content.decode(),
        "provenance": json.loads(record.artifact_payload_json),
    }


def _observe(  # noqa: PLR0912, PLR0913 - validate every role predecessor explicitly.
    intent: ProductionCallIntent,
    configuration: ModelConfiguration,
    response: ModelCallResponse,
    subject: ProductionResearchSubject,
    *,
    dossier: Dossier | None,
    thesis: Thesis | None,
    skeptic: SkepticResult | None,
    forecast: ScenarioForecast | None,
) -> LabCallObservation:
    disposition = production_response_disposition(configuration, response)
    artifact: Dossier | Thesis | SkepticResult | ScenarioForecast | CioResolution | None = None
    artifact_refusal: DossierRefusalReason | ResearchArtifactRefusal | None = None
    if (
        disposition is LabObservationDisposition.INVALID_ARTIFACT
        and response.raw_response is not None
    ):
        try:
            decoded = json.loads(response.raw_response.decode())
            available_ids = tuple(item.artifact_id for item in subject.evidence)
            bindings = tuple((item.artifact_id, item.content_hash) for item in subject.evidence)
            if intent.role is ResearchRole.EVIDENCE_COLLECTOR:
                parsed_dossier = parse_dossier(
                    decoded,
                    expected_subject=subject.subject,
                    available_artifact_ids=available_ids,
                    available_artifact_bindings=bindings,
                    cutoff=UtcInstant.parse(json.loads(intent.model_input_json)["evidence_cutoff"]),
                    authority=ResearchAuthority.PRODUCTION,
                )
                if isinstance(parsed_dossier, Dossier):
                    artifact = parsed_dossier
                    disposition = LabObservationDisposition.VALIDATED
                else:
                    artifact_refusal = parsed_dossier
                    disposition = LabObservationDisposition.INVALID_DOSSIER
            elif intent.role is ResearchRole.THESIS_BUILDER and dossier is not None:
                parsed_thesis = parse_thesis(decoded, dossier=dossier)
                if isinstance(parsed_thesis, Thesis):
                    artifact = parsed_thesis
                    disposition = LabObservationDisposition.VALIDATED
                else:
                    artifact_refusal = parsed_thesis
            elif (
                intent.role is ResearchRole.INDEPENDENT_SKEPTIC
                and dossier is not None
                and thesis is not None
            ):
                parsed_skeptic = parse_skeptic_result(decoded, dossier=dossier, thesis=thesis)
                if isinstance(parsed_skeptic, SkepticResult):
                    artifact = parsed_skeptic
                    disposition = LabObservationDisposition.VALIDATED
                else:
                    artifact_refusal = parsed_skeptic
            elif (
                intent.role is ResearchRole.SCENARIO_FORECASTER
                and dossier is not None
                and thesis is not None
                and skeptic is not None
            ):
                parsed_forecast = parse_scenario_forecast(
                    decoded,
                    dossier=dossier,
                    thesis=thesis,
                    skeptic=skeptic,
                )
                if isinstance(parsed_forecast, ScenarioForecast):
                    artifact = parsed_forecast
                    disposition = LabObservationDisposition.VALIDATED
                else:
                    artifact_refusal = parsed_forecast
            elif (
                intent.role is ResearchRole.CIO
                and dossier is not None
                and thesis is not None
                and skeptic is not None
                and forecast is not None
            ):
                parsed_cio = parse_cio_resolution(
                    decoded,
                    dossier=dossier,
                    thesis=thesis,
                    skeptic=skeptic,
                    forecast=forecast,
                )
                if isinstance(parsed_cio, CioResolution):
                    artifact = parsed_cio
                    disposition = LabObservationDisposition.VALIDATED
                else:
                    artifact_refusal = parsed_cio
        except (UnicodeDecodeError, ValueError, RecursionError):
            disposition = LabObservationDisposition.INVALID_JSON
    return LabCallObservation.create(
        call_id=intent.call_id,
        disposition=disposition,
        response=response,
        artifact=artifact,
        artifact_refusal=artifact_refusal,
        retain_raw_response=disposition is not LabObservationDisposition.OVERSIZED_OUTPUT,
    )


def production_response_disposition(  # noqa: PLR0911 - each failure is distinct.
    configuration: ModelConfiguration,
    response: ModelCallResponse,
) -> LabObservationDisposition:
    """Classify one validated adapter response under the pinned role configuration."""
    if (
        response.raw_response is not None
        and len(response.raw_response) > MAXIMUM_MODEL_OUTPUT_BYTES
    ):
        return LabObservationDisposition.OVERSIZED_OUTPUT
    if response.disposition is ModelCallDisposition.TIMED_OUT:
        return LabObservationDisposition.MODEL_TIMEOUT
    if response.disposition is ModelCallDisposition.QUOTA_EXHAUSTED:
        return LabObservationDisposition.QUOTA_EXHAUSTED
    if response.disposition is not ModelCallDisposition.RESPONDED:
        return LabObservationDisposition.ADAPTER_REFUSED
    if (
        response.output_tokens > configuration.maximum_output_tokens
        or response.turns > configuration.maximum_turns
    ):
        return LabObservationDisposition.ADAPTER_REFUSED
    if response.exposed_model_identity != configuration.model_identity:
        return LabObservationDisposition.MODEL_IDENTITY_MISMATCH
    if response.raw_response is None:
        return LabObservationDisposition.ADAPTER_REFUSED
    return LabObservationDisposition.INVALID_ARTIFACT


def _model_response_is_valid(value: object) -> TypeGuard[ModelCallResponse]:
    return (
        type(value) is ModelCallResponse
        and type(value.disposition) is ModelCallDisposition
        and (value.raw_response is None or type(value.raw_response) is bytes)
        and type(value.input_tokens) is int
        and 0 <= value.input_tokens <= _MAXIMUM_MODEL_RESOURCE_VALUE
        and type(value.output_tokens) is int
        and 0 <= value.output_tokens <= _MAXIMUM_MODEL_RESOURCE_VALUE
        and type(value.turns) is int
        and 0 <= value.turns <= _MAXIMUM_MODEL_RESOURCE_VALUE
        and (
            value.exposed_model_identity is None
            or (
                type(value.exposed_model_identity) is str
                and _MODEL_IDENTITY.fullmatch(value.exposed_model_identity) is not None
            )
        )
        and (
            value.elapsed_milliseconds is None
            or (
                type(value.elapsed_milliseconds) is int
                and 0 <= value.elapsed_milliseconds <= _MAXIMUM_MODEL_RESOURCE_VALUE
            )
        )
        and type(value.timing_disposition) is ModelTimingDisposition
        and value.timing_disposition
        is {
            ModelCallDisposition.RESPONDED: ModelTimingDisposition.WITHIN_BUDGET,
            ModelCallDisposition.TIMED_OUT: ModelTimingDisposition.TIMED_OUT,
            ModelCallDisposition.QUOTA_EXHAUSTED: ModelTimingDisposition.UNAVAILABLE,
            ModelCallDisposition.REFUSED: ModelTimingDisposition.UNAVAILABLE,
        }.get(value.disposition)
    )


def _model_response_record(value: object) -> ProductionModelResponseRecord:
    if type(value) is not ModelCallResponse:
        return ProductionModelResponseRecord(
            response_type_valid=False,
            metadata_valid=False,
            disposition_valid=False,
            disposition=None,
            raw_response_valid=False,
            raw_response=None,
            raw_response_hash=None,
            raw_response_retained=False,
            exposed_model_identity_valid=False,
            exposed_model_identity=None,
            input_tokens_valid=False,
            input_tokens=None,
            output_tokens_valid=False,
            output_tokens=None,
            turns_valid=False,
            turns=None,
            elapsed_milliseconds_valid=False,
            elapsed_milliseconds=None,
            timing_disposition_valid=False,
            timing_disposition=None,
        )
    disposition_valid = type(value.disposition) is ModelCallDisposition
    raw_response_valid = value.raw_response is None or type(value.raw_response) is bytes
    exposed_identity_valid = value.exposed_model_identity is None or (
        type(value.exposed_model_identity) is str
        and _MODEL_IDENTITY.fullmatch(value.exposed_model_identity) is not None
    )
    input_tokens_valid = _model_resource_is_valid(value.input_tokens)
    output_tokens_valid = _model_resource_is_valid(value.output_tokens)
    turns_valid = _model_resource_is_valid(value.turns)
    elapsed_valid = value.elapsed_milliseconds is None or _model_resource_is_valid(
        value.elapsed_milliseconds
    )
    timing_valid = type(value.timing_disposition) is ModelTimingDisposition
    raw_response = value.raw_response if type(value.raw_response) is bytes else None
    retain_raw = raw_response is not None and len(raw_response) <= MAXIMUM_MODEL_OUTPUT_BYTES
    disposition = _safe_reported_text(
        value.disposition.value
        if type(value.disposition) is ModelCallDisposition
        else value.disposition
    )
    timing = _safe_reported_text(
        value.timing_disposition.value
        if type(value.timing_disposition) is ModelTimingDisposition
        else value.timing_disposition
    )
    return ProductionModelResponseRecord(
        response_type_valid=True,
        metadata_valid=_model_response_is_valid(value),
        disposition_valid=disposition_valid,
        disposition=disposition,
        raw_response_valid=raw_response_valid,
        raw_response=raw_response,
        raw_response_hash=(
            None if raw_response is None else hashlib.sha256(raw_response).hexdigest()
        ),
        raw_response_retained=retain_raw,
        exposed_model_identity_valid=exposed_identity_valid,
        exposed_model_identity=_safe_reported_text(value.exposed_model_identity),
        input_tokens_valid=input_tokens_valid,
        input_tokens=_safe_reported_integer(value.input_tokens),
        output_tokens_valid=output_tokens_valid,
        output_tokens=_safe_reported_integer(value.output_tokens),
        turns_valid=turns_valid,
        turns=_safe_reported_integer(value.turns),
        elapsed_milliseconds_valid=elapsed_valid,
        elapsed_milliseconds=_safe_reported_integer(value.elapsed_milliseconds),
        timing_disposition_valid=timing_valid,
        timing_disposition=timing,
    )


def _model_resource_is_valid(value: object) -> TypeGuard[int]:
    return type(value) is int and 0 <= value <= _MAXIMUM_MODEL_RESOURCE_VALUE


def _safe_reported_integer(value: object) -> int | None:
    return value if type(value) is int and abs(value) <= _MAXIMUM_MODEL_RESOURCE_VALUE else None


def _safe_reported_text(value: object) -> str | None:
    return value if type(value) is str and len(value) <= _MAXIMUM_REPORTED_TEXT_LENGTH else None


def _reported_timing_matches(disposition: str | None, timing: str | None) -> bool:
    if disposition is None or timing is None:
        return False
    try:
        reported_disposition = ModelCallDisposition(disposition)
        reported_timing = ModelTimingDisposition(timing)
    except (TypeError, ValueError):
        return False
    return (
        reported_timing
        is {
            ModelCallDisposition.RESPONDED: ModelTimingDisposition.WITHIN_BUDGET,
            ModelCallDisposition.TIMED_OUT: ModelTimingDisposition.TIMED_OUT,
            ModelCallDisposition.QUOTA_EXHAUSTED: ModelTimingDisposition.UNAVAILABLE,
            ModelCallDisposition.REFUSED: ModelTimingDisposition.UNAVAILABLE,
        }[reported_disposition]
    )


def _model_input_matches_intent(value: object, intent: ProductionCallIntent) -> bool:
    if type(value) is not dict or set(value) != _MODEL_INPUT_FIELDS:
        return False
    prompt = parse_prompt_contract_payload(value["prompt"])
    model = parse_model_configuration_contract_payload(value["model_configuration"])
    tools = parse_tool_contract_payloads(value["tools"])
    material_hashes = value["material_input_hashes"]
    evidence = value["evidence"]
    return (
        value["schema_version"] == 1
        and value["record_kind"] == "production_research_role_input"
        and value["authority_scope"] == ResearchAuthority.PRODUCTION.value
        and value["non_production"] is False
        and value["run_id"] == intent.run_id
        and value["request_id"] == intent.request_id
        and value["role"] == intent.role.value
        and type(value["subject"]) is dict
        and type(value["evidence_cutoff"]) is str
        and type(value["data_regime"]) is str
        and type(value["attention_artifact"]) is dict
        and type(evidence) is list
        and bool(evidence)
        and all(type(item) is dict for item in evidence)
        and type(value["constitution"]) is dict
        and type(value["belief_graph"]) is dict
        and type(value["portfolio_context"]) is dict
        and _model_input_has_exact_role_context(value, intent.role)
        and prompt is not None
        and prompt[3] == intent.prompt_fingerprint
        and model is not None
        and model[0] == intent.requested_model_identity
        and model[4] == intent.model_configuration_fingerprint
        and tools is not None
        and tuple(item[3] for item in tools) == intent.tool_fingerprints
        and type(material_hashes) is list
        and tuple(material_hashes) == intent.material_input_hashes
    )


def _model_input_has_exact_role_context(
    value: dict[object, object],
    role: ResearchRole,
) -> bool:
    dossier = value["dossier"]
    thesis = value["thesis"]
    skeptic = value["skeptic"]
    forecast = value["forecast"]
    if role is ResearchRole.EVIDENCE_COLLECTOR:
        return dossier is None and thesis is None and skeptic is None and forecast is None
    if role is ResearchRole.THESIS_BUILDER:
        return type(dossier) is dict and thesis is None and skeptic is None and forecast is None
    if role is ResearchRole.INDEPENDENT_SKEPTIC:
        return (
            type(dossier) is dict and type(thesis) is dict and skeptic is None and forecast is None
        )
    if role is ResearchRole.SCENARIO_FORECASTER:
        return (
            type(dossier) is dict
            and type(thesis) is dict
            and type(skeptic) is dict
            and forecast is None
        )
    return (
        type(dossier) is dict
        and type(thesis) is dict
        and type(skeptic) is dict
        and type(forecast) is dict
    )


def _preparation_refusal(
    intent: ProductionCallIntent,
    disposition: LabCallPreparationDisposition,
) -> LabCallObservation:
    response = ModelCallResponse(
        ModelCallDisposition.REFUSED,
        None,
        None,
        0,
        0,
        0,
        None,
        ModelTimingDisposition.UNAVAILABLE,
    )
    refusal_disposition = (
        LabObservationDisposition.ADAPTER_REFUSED
        if disposition is LabCallPreparationDisposition.CONFLICT
        else LabObservationDisposition.INDETERMINATE_EFFECT
    )
    return LabCallObservation.create(
        call_id=intent.call_id,
        disposition=refusal_disposition,
        response=response,
    )


def _checkpoint(
    calls: list[_CallResult],
    artifact_ids: tuple[str, ...],
) -> ResearchCheckpoint:
    return ResearchCheckpoint(
        tuple(sorted(set(artifact_ids))),
        tuple(sorted(call.intent.call_id for call in calls)),
        sum(call.observation.input_tokens for call in calls),
        sum(call.observation.output_tokens for call in calls),
        sum(call.observation.turns for call in calls),
    )


def _call_refusal(
    calls: tuple[_CallResult, ...],
    artifact_ids: tuple[str, ...],
) -> ResearchRefusal:
    call = calls[-1]
    return ResearchRefusal(
        production_call_refusal_identity(call.intent.call_id, call.observation),
        _checkpoint(list(calls), artifact_ids),
        call.intent.call_id,
    )


def production_call_refusal_identity(
    call_id: str,
    observation: LabCallObservation,
) -> str:
    """Derive the lifecycle refusal identity for one terminal failed call."""
    return _content_hash(
        {
            "call_id": call_id,
            "disposition": observation.disposition.value,
            "artifact_refusal": (
                None if observation.artifact_refusal is None else observation.artifact_refusal.value
            ),
        }
    )


def _input_refusal(
    context: ProductionResearchContext,
    request_id: str,
    reason: str,
    calls: list[_CallResult] | None = None,
    artifact_ids: tuple[str, ...] = (),
) -> ResearchRefusal:
    return ResearchRefusal(
        _content_hash({"run_id": context.run_id, "request_id": request_id, "reason": reason}),
        _checkpoint([] if calls is None else calls, artifact_ids),
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _content_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )
