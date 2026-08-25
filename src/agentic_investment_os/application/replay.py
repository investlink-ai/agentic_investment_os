"""Replay stateless research-role calls inside an isolated Research Lab."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from agentic_investment_os.domain.governance import ConstitutionArtifact
from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    parse_instrument_identity,
)
from agentic_investment_os.domain.lifecycle import is_sha256
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.domain.universe import is_data_regime
from agentic_investment_os.memory.admission import validate_belief_event
from agentic_investment_os.memory.beliefs import (
    BeliefGraph,
    BeliefGraphBeliefNode,
    BeliefGraphEdge,
    BeliefGraphEdgeKind,
    BeliefGraphEvidenceNode,
)
from agentic_investment_os.research.dossier import Dossier, DossierRefusalReason, parse_dossier
from agentic_investment_os.research.model import (
    MAXIMUM_MODEL_OUTPUT_BYTES,
    LabCallIntent,
    LabCallLedger,
    LabCallObservation,
    LabCallPreparationDisposition,
    LabObservationDisposition,
    ModelCallDisposition,
    ModelCallResponse,
    ModelTimingDisposition,
    ResearchRole,
    ResearchRoleModel,
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

if TYPE_CHECKING:
    from datetime import datetime

__all__ = (
    "Replay",
    "ReplayClock",
    "ReplayDisposition",
    "ReplayReceipt",
    "ReplayRefusalReason",
    "RoleCallReceipt",
)

_REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "request_id",
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
_EVIDENCE_FIELDS = frozenset({"artifact_id", "content_hash", "available_at", "subject", "content"})
_PROMPT_FIELDS = frozenset({"schema_version", "prompt_id", "content", "content_hash"})
_MODEL_FIELDS = frozenset({"schema_version", "model_identity", "reasoning", "content_hash"})
_REASONING_FIELDS = frozenset({"effort", "maximum_output_tokens", "maximum_turns"})
_TOOL_FIELDS = frozenset({"name", "schema_json", "schema_hash"})
_RESOLUTION_REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "request_id",
        "namespace",
        "input_kind",
        "subject",
        "evidence",
        "evidence_cutoff",
        "data_regime",
        "constitution",
        "belief_graph",
        "portfolio_context_fingerprint",
        "dossier",
        "role_contracts",
        "material_input_hashes",
    }
)
_ROLE_CONTRACT_FIELDS = frozenset({"role", "prompt", "model_configuration", "tools"})
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_MODEL_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_MAXIMUM_EVIDENCE_ITEMS = 50
_MAXIMUM_EVIDENCE_CHARACTERS = 250_000
_MAXIMUM_PROMPT_CHARACTERS = 20_000
_MAXIMUM_OUTPUT_TOKENS = 20_000
_MAXIMUM_TURNS = 10
_MAXIMUM_TOOL_FINGERPRINTS = 10
_MAXIMUM_TOOL_SCHEMA_CHARACTERS = 20_000
_CLOCK_INVALID = "Research Lab clock must return a timezone-aware instant representable in UTC"
_AUTHORITY_SCOPE = "research_lab_non_production"
_INVALID_RECEIPT = "invalid non-production Replay receipt"
_RESOLUTION_SCHEMA_VERSION = 2
_RESOLUTION_ROLES = (
    ResearchRole.THESIS_BUILDER,
    ResearchRole.INDEPENDENT_SKEPTIC,
    ResearchRole.SCENARIO_FORECASTER,
    ResearchRole.CIO,
)


class ReplayDisposition(StrEnum):
    """Distinguish fresh completion, exact replay, and bounded refusal."""

    COMPLETED = "completed"
    REPLAYED = "replayed"
    REFUSED = "refused"
    CONFLICTED = "conflicted"


class ReplayRefusalReason(StrEnum):
    """Bound invalid input, adapter, validation, and idempotency outcomes."""

    INVALID_REQUEST = "invalid_request"
    NAMESPACE_MISMATCH = "namespace_mismatch"
    UNAVAILABLE_EVIDENCE = "unavailable_evidence"
    IDENTITY_CONFLICT = "identity_conflict"
    MODEL_TIMEOUT = "model_timeout"
    QUOTA_EXHAUSTED = "quota_exhausted"
    ADAPTER_REFUSED = "adapter_refused"
    OVERSIZED_OUTPUT = "oversized_output"
    INVALID_JSON = "invalid_json"
    INCOMPATIBLE_SCHEMA = "incompatible_schema"
    MODEL_IDENTITY_MISMATCH = "model_identity_mismatch"
    INDETERMINATE_MODEL_EFFECT = "indeterminate_model_effect"


class ReplayClock(Protocol):
    """Supply trusted observation instants at the Lab composition boundary."""

    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class LabEvidenceInput:
    """Carry one copied or synthetic evidence artifact into the isolated call."""

    artifact_id: str
    content_hash: str
    available_at: UtcInstant
    subject: EquityInstrumentIdentity
    content: str

    def to_payload(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "content_hash": self.content_hash,
            "available_at": self.available_at.isoformat(),
            "subject": self.subject.to_payload(),
            "content": self.content,
        }

    def provenance_payload(self) -> dict[str, object]:
        """Return the immutable evidence binding without copied content."""
        return {
            "artifact_id": self.artifact_id,
            "content_hash": self.content_hash,
            "available_at": self.available_at.isoformat(),
            "subject": self.subject.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class PromptArtifact:
    """Pin one exact Evidence Collector prompt and its versioned identity."""

    prompt_id: str
    content: str
    content_hash: str

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "prompt_id": self.prompt_id,
            "content": self.content,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class ModelConfiguration:
    """Pin the exposed model identity and bounded reasoning resource policy."""

    model_identity: str
    effort: str
    maximum_output_tokens: int
    maximum_turns: int
    content_hash: str

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "model_identity": self.model_identity,
            "reasoning": {
                "effort": self.effort,
                "maximum_output_tokens": self.maximum_output_tokens,
                "maximum_turns": self.maximum_turns,
            },
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class ToolContract:
    """Pin a reconstructable tool schema without granting a callable tool capability."""

    name: str
    schema_json: str
    schema_hash: str

    def to_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "schema_json": self.schema_json,
            "schema_hash": self.schema_hash,
        }


@dataclass(frozen=True, slots=True)
class RoleContract:
    """Pin one role's prompt, model configuration, and inert tool schemas."""

    role: ResearchRole
    prompt: PromptArtifact
    model_configuration: ModelConfiguration
    tools: tuple[ToolContract, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "role": self.role.value,
            "prompt": self.prompt.to_payload(),
            "model_configuration": self.model_configuration.to_payload(),
            "tools": [item.to_payload() for item in self.tools],
        }


@dataclass(frozen=True, slots=True)
class RoleCallReceipt:
    """Expose one effect-local role call without raw model content."""

    role: ResearchRole
    call_id: str
    retry_disposition: LabCallPreparationDisposition
    disposition: LabObservationDisposition
    model_input_hash: str
    prompt_fingerprint: str
    requested_model_identity: str
    exposed_model_identity: str | None
    model_configuration_fingerprint: str
    tool_fingerprints: tuple[str, ...]
    material_input_hashes: tuple[str, ...]
    raw_response_hash: str | None
    input_tokens: int
    output_tokens: int
    turns: int
    elapsed_milliseconds: int | None
    timing_disposition: ModelTimingDisposition

    def __post_init__(self) -> None:
        valid = (
            type(self.role) is ResearchRole
            and is_sha256(self.call_id)
            and type(self.retry_disposition) is LabCallPreparationDisposition
            and type(self.disposition) is LabObservationDisposition
            and is_sha256(self.model_input_hash)
            and is_sha256(self.prompt_fingerprint)
            and _MODEL_IDENTITY.fullmatch(self.requested_model_identity) is not None
            and (
                self.exposed_model_identity is None
                or _MODEL_IDENTITY.fullmatch(self.exposed_model_identity) is not None
            )
            and is_sha256(self.model_configuration_fingerprint)
            and all(is_sha256(value) for value in self.tool_fingerprints)
            and bool(self.material_input_hashes)
            and all(is_sha256(value) for value in self.material_input_hashes)
            and (self.raw_response_hash is None or is_sha256(self.raw_response_hash))
            and type(self.input_tokens) is int
            and self.input_tokens >= 0
            and type(self.output_tokens) is int
            and self.output_tokens >= 0
            and type(self.turns) is int
            and self.turns >= 0
            and (
                self.elapsed_milliseconds is None
                or (type(self.elapsed_milliseconds) is int and self.elapsed_milliseconds >= 0)
            )
            and type(self.timing_disposition) is ModelTimingDisposition
        )
        if not valid:
            raise ValueError(_INVALID_RECEIPT)


@dataclass(frozen=True, slots=True)
class ResolutionReplayRequest:
    """Carry one validated Dossier and four independently pinned role contracts."""

    request_id: str
    namespace: str
    input_kind: str
    subject: EquityInstrumentIdentity
    evidence: tuple[LabEvidenceInput, ...]
    evidence_cutoff: UtcInstant
    data_regime: str
    constitution: ConstitutionArtifact
    belief_graph: BeliefGraph
    portfolio_context_fingerprint: str
    dossier: Dossier
    role_contracts: tuple[RoleContract, ...]
    material_input_hashes: tuple[str, ...]

    def role_call(
        self,
        contract: RoleContract,
        *,
        thesis: Thesis | None,
        skeptic: SkepticResult | None,
        forecast: ScenarioForecast | None,
    ) -> ResolutionRoleCall:
        return ResolutionRoleCall(self, contract, thesis, skeptic, forecast)


@dataclass(frozen=True, slots=True)
class ResolutionRoleCall:
    """Construct one clean stateless role context from validated artifacts."""

    request: ResolutionReplayRequest
    contract: RoleContract
    thesis: Thesis | None
    skeptic: SkepticResult | None
    forecast: ScenarioForecast | None

    @property
    def role(self) -> ResearchRole:
        return self.contract.role

    @property
    def material_input_hashes(self) -> tuple[str, ...]:
        values = {
            *(item.content_hash for item in self.request.evidence),
            self.request.constitution.content_hash,
            self.request.belief_graph.content_hash,
            self.request.portfolio_context_fingerprint,
            self.request.dossier.content_hash,
            self.contract.prompt.content_hash,
            self.contract.model_configuration.content_hash,
            *(item.schema_hash for item in self.contract.tools),
        }
        for artifact in (self.thesis, self.skeptic, self.forecast):
            if artifact is not None:
                values.add(artifact.content_hash)
        return tuple(sorted(values))

    def model_input_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "role": self.role.value,
            "authority_scope": _AUTHORITY_SCOPE,
            "non_production": True,
            "namespace": self.request.namespace,
            "input_kind": self.request.input_kind,
            "subject": self.request.subject.to_payload(),
            "evidence_cutoff": self.request.evidence_cutoff.isoformat(),
            "available_artifact_ids": [item.artifact_id for item in self.request.evidence],
            "evidence_manifest": [item.provenance_payload() for item in self.request.evidence],
            "data_regime": self.request.data_regime,
            "constitution": self.request.constitution.to_payload(),
            "belief_graph": self.request.belief_graph.to_payload(),
            "portfolio_context_fingerprint": self.request.portfolio_context_fingerprint,
            "dossier": self.request.dossier.to_payload(),
            "thesis": None if self.thesis is None else self.thesis.to_payload(),
            "skeptic": None if self.skeptic is None else self.skeptic.to_payload(),
            "forecast": None if self.forecast is None else self.forecast.to_payload(),
            "prompt": self.contract.prompt.to_payload(),
            "model_configuration": self.contract.model_configuration.to_payload(),
            "tools": [item.to_payload() for item in self.contract.tools],
            "material_input_hashes": list(self.material_input_hashes),
        }

    @property
    def model_input_json(self) -> str:
        return _canonical_json(self.model_input_payload())

    @property
    def model_input_hash(self) -> str:
        return hashlib.sha256(self.model_input_json.encode()).hexdigest()

    @property
    def call_id(self) -> str:
        return _content_hash(
            {
                "namespace": self.request.namespace,
                "request_id": self.request.request_id,
                "role": self.role.value,
            }
        )

    def intent(self) -> LabCallIntent:
        return LabCallIntent(
            call_id=self.call_id,
            role=self.role,
            namespace=self.request.namespace,
            request_id=self.request.request_id,
            request_fingerprint=_content_hash(
                {
                    "request_id": self.request.request_id,
                    "namespace": self.request.namespace,
                    "model_input_hash": self.model_input_hash,
                }
            ),
            model_input_json=self.model_input_json,
            model_input_hash=self.model_input_hash,
            prompt_fingerprint=self.contract.prompt.content_hash,
            requested_model_identity=self.contract.model_configuration.model_identity,
            model_configuration_fingerprint=self.contract.model_configuration.content_hash,
            tool_fingerprints=tuple(item.schema_hash for item in self.contract.tools),
            material_input_hashes=self.material_input_hashes,
            maximum_output_bytes=MAXIMUM_MODEL_OUTPUT_BYTES,
        )


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    """Carry the complete validated input to one isolated Evidence Collector replay."""

    request_id: str
    namespace: str
    input_kind: str
    subject: EquityInstrumentIdentity
    evidence: tuple[LabEvidenceInput, ...]
    evidence_cutoff: UtcInstant
    data_regime: str
    constitution: ConstitutionArtifact
    belief_graph: BeliefGraph
    portfolio_context_fingerprint: str
    prompt: PromptArtifact
    model_configuration: ModelConfiguration
    tools: tuple[ToolContract, ...]
    material_input_hashes: tuple[str, ...]

    def model_input_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "role": "evidence_collector",
            "authority_scope": _AUTHORITY_SCOPE,
            "non_production": True,
            "namespace": self.namespace,
            "input_kind": self.input_kind,
            "subject": self.subject.to_payload(),
            "evidence": [item.to_payload() for item in self.evidence],
            "evidence_cutoff": self.evidence_cutoff.isoformat(),
            "data_regime": self.data_regime,
            "constitution": self.constitution.to_payload(),
            "belief_graph": self.belief_graph.to_payload(),
            "portfolio_context_fingerprint": self.portfolio_context_fingerprint,
            "prompt": self.prompt.to_payload(),
            "model_configuration": self.model_configuration.to_payload(),
            "tools": [item.to_payload() for item in self.tools],
            "material_input_hashes": list(self.material_input_hashes),
        }

    @property
    def model_input_json(self) -> str:
        return _canonical_json(self.model_input_payload())

    @property
    def model_input_hash(self) -> str:
        return hashlib.sha256(self.model_input_json.encode()).hexdigest()

    @property
    def request_fingerprint(self) -> str:
        return _content_hash(
            {
                "request_id": self.request_id,
                "namespace": self.namespace,
                "model_input_hash": self.model_input_hash,
            }
        )

    @property
    def call_id(self) -> str:
        return _content_hash(
            {
                "namespace": self.namespace,
                "request_id": self.request_id,
                "role": ResearchRole.EVIDENCE_COLLECTOR.value,
            }
        )

    def intent(self) -> LabCallIntent:
        return LabCallIntent(
            call_id=self.call_id,
            role=ResearchRole.EVIDENCE_COLLECTOR,
            namespace=self.namespace,
            request_id=self.request_id,
            request_fingerprint=self.request_fingerprint,
            model_input_json=self.model_input_json,
            model_input_hash=self.model_input_hash,
            prompt_fingerprint=self.prompt.content_hash,
            requested_model_identity=self.model_configuration.model_identity,
            model_configuration_fingerprint=self.model_configuration.content_hash,
            tool_fingerprints=tuple(item.schema_hash for item in self.tools),
            material_input_hashes=self.material_input_hashes,
            maximum_output_bytes=MAXIMUM_MODEL_OUTPUT_BYTES,
        )


@dataclass(frozen=True, slots=True)
class ReplayReceipt:
    """Expose bounded Lab artifacts and reconstructable call provenance."""

    disposition: ReplayDisposition
    request_id: str | None
    call_id: str | None
    dossier: Dossier | None
    refusal_reason: ReplayRefusalReason | None
    dossier_refusal: DossierRefusalReason | None
    model_input_hash: str | None
    prompt_fingerprint: str | None
    requested_model_identity: str | None
    exposed_model_identity: str | None
    model_configuration_fingerprint: str | None
    tool_fingerprints: tuple[str, ...]
    material_input_hashes: tuple[str, ...]
    raw_response_hash: str | None
    input_tokens: int
    output_tokens: int
    turns: int
    elapsed_milliseconds: int | None
    timing_disposition: ModelTimingDisposition | None
    thesis: Thesis | None = None
    skeptic: SkepticResult | None = None
    forecast: ScenarioForecast | None = None
    cio_resolution: CioResolution | None = None
    role_calls: tuple[RoleCallReceipt, ...] = ()
    failed_role: ResearchRole | None = None
    artifact_refusal: (
        ThesisRefusalReason | SkepticRefusalReason | ForecastRefusalReason | CioRefusalReason | None
    ) = None
    authority_scope: str = _AUTHORITY_SCOPE
    non_production: bool = True

    def __post_init__(self) -> None:
        if self.authority_scope != _AUTHORITY_SCOPE or self.non_production is not True:
            raise ValueError(_INVALID_RECEIPT)

    @property
    def dossier_id(self) -> str | None:
        return None if self.dossier is None else self.dossier.content_hash


@dataclass(frozen=True, slots=True)
class Replay:
    """Run one idempotent research replay in a fixed Lab namespace."""

    namespace: str
    ledger: LabCallLedger
    model: ResearchRoleModel
    clock: ReplayClock

    def __call__(  # noqa: PLR0911 - preserve each typed replay disposition at the boundary.
        self, request: object
    ) -> ReplayReceipt:
        if type(request) is dict and request.get("schema_version") == _RESOLUTION_SCHEMA_VERSION:
            return self._resolve(request)
        parsed, refusal = _parse_replay_request(request)
        if parsed is None:
            return _input_refusal(refusal or ReplayRefusalReason.INVALID_REQUEST)
        if parsed.namespace != self.namespace:
            return _input_refusal(ReplayRefusalReason.NAMESPACE_MISMATCH, parsed.request_id)
        intent = parsed.intent()
        preparation = self.ledger.prepare_call(intent, self._now())
        if preparation.disposition is LabCallPreparationDisposition.CONFLICT:
            return _receipt_from_intent(
                intent,
                disposition=ReplayDisposition.CONFLICTED,
                refusal=ReplayRefusalReason.IDENTITY_CONFLICT,
            )
        if preparation.disposition is LabCallPreparationDisposition.INDETERMINATE_EFFECT:
            return _receipt_from_intent(
                intent,
                disposition=ReplayDisposition.REFUSED,
                refusal=ReplayRefusalReason.INDETERMINATE_MODEL_EFFECT,
            )
        if preparation.disposition is LabCallPreparationDisposition.REPLAY:
            if preparation.observation is None:
                return _receipt_from_intent(
                    intent,
                    disposition=ReplayDisposition.REFUSED,
                    refusal=ReplayRefusalReason.ADAPTER_REFUSED,
                )
            return _receipt_from_observation(
                intent,
                preparation.observation,
                disposition=ReplayDisposition.REPLAYED,
            )
        response = self.model.call(intent.model_request())
        if not _model_response_is_valid(response):
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
        observation = _observe_response(parsed, response)
        stored = self.ledger.append_observation(intent, observation, self._now())
        return _receipt_from_observation(
            intent,
            stored,
            disposition=(
                ReplayDisposition.COMPLETED
                if stored.disposition is LabObservationDisposition.VALIDATED
                else ReplayDisposition.REFUSED
            ),
        )

    def _resolve(self, request: object) -> ReplayReceipt:  # noqa: PLR0911, PLR0912
        parsed, refusal = _parse_resolution_request(request)
        if parsed is None:
            return _input_refusal(refusal or ReplayRefusalReason.INVALID_REQUEST)
        if parsed.namespace != self.namespace:
            return _input_refusal(ReplayRefusalReason.NAMESPACE_MISMATCH, parsed.request_id)
        thesis: Thesis | None = None
        skeptic: SkepticResult | None = None
        forecast: ScenarioForecast | None = None
        cio: CioResolution | None = None
        calls: list[RoleCallReceipt] = []
        fresh_effect = False
        for contract in parsed.role_contracts:
            call = parsed.role_call(
                contract,
                thesis=thesis,
                skeptic=skeptic,
                forecast=forecast,
            )
            intent = call.intent()
            preparation = self.ledger.prepare_call(intent, self._now())
            if preparation.disposition is LabCallPreparationDisposition.CONFLICT:
                return _resolution_receipt(
                    parsed,
                    intent,
                    calls,
                    thesis,
                    skeptic,
                    forecast,
                    cio,
                    disposition=ReplayDisposition.CONFLICTED,
                    refusal=ReplayRefusalReason.IDENTITY_CONFLICT,
                    failed_role=contract.role,
                )
            if preparation.disposition is LabCallPreparationDisposition.INDETERMINATE_EFFECT:
                return _resolution_receipt(
                    parsed,
                    intent,
                    calls,
                    thesis,
                    skeptic,
                    forecast,
                    cio,
                    disposition=ReplayDisposition.REFUSED,
                    refusal=ReplayRefusalReason.INDETERMINATE_MODEL_EFFECT,
                    failed_role=contract.role,
                )
            if preparation.disposition is LabCallPreparationDisposition.REPLAY:
                observation = preparation.observation
                if observation is None:
                    return _resolution_receipt(
                        parsed,
                        intent,
                        calls,
                        thesis,
                        skeptic,
                        forecast,
                        cio,
                        disposition=ReplayDisposition.REFUSED,
                        refusal=ReplayRefusalReason.ADAPTER_REFUSED,
                        failed_role=contract.role,
                    )
            else:
                fresh_effect = True
                response = self.model.call(intent.model_request())
                if not _model_response_is_valid(response):
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
                observation = _observe_resolution_response(call, response)
                observation = self.ledger.append_observation(intent, observation, self._now())
            role_receipt = _role_call_receipt(intent, observation, preparation.disposition)
            calls.append(role_receipt)
            if observation.disposition is not LabObservationDisposition.VALIDATED:
                return _resolution_receipt(
                    parsed,
                    intent,
                    calls,
                    thesis,
                    skeptic,
                    forecast,
                    cio,
                    disposition=ReplayDisposition.REFUSED,
                    refusal=_refusal_from_observation(observation.disposition),
                    failed_role=contract.role,
                    artifact_refusal=observation.artifact_refusal,
                )
            artifact = observation.artifact
            if contract.role is ResearchRole.THESIS_BUILDER and type(artifact) is Thesis:
                thesis = artifact
            elif (
                contract.role is ResearchRole.INDEPENDENT_SKEPTIC
                and type(artifact) is SkepticResult
            ):
                skeptic = artifact
            elif (
                contract.role is ResearchRole.SCENARIO_FORECASTER
                and type(artifact) is ScenarioForecast
            ):
                forecast = artifact
            elif contract.role is ResearchRole.CIO and type(artifact) is CioResolution:
                cio = artifact
            else:
                return _resolution_receipt(
                    parsed,
                    intent,
                    calls,
                    thesis,
                    skeptic,
                    forecast,
                    cio,
                    disposition=ReplayDisposition.REFUSED,
                    refusal=ReplayRefusalReason.INCOMPATIBLE_SCHEMA,
                    failed_role=contract.role,
                )
        final_intent = parsed.role_call(
            parsed.role_contracts[-1],
            thesis=thesis,
            skeptic=skeptic,
            forecast=forecast,
        ).intent()
        return _resolution_receipt(
            parsed,
            final_intent,
            calls,
            thesis,
            skeptic,
            forecast,
            cio,
            disposition=(
                ReplayDisposition.COMPLETED if fresh_effect else ReplayDisposition.REPLAYED
            ),
            refusal=None,
            failed_role=None,
        )

    def _now(self) -> UtcInstant:
        try:
            return UtcInstant.from_datetime(self.clock.now())
        except (AttributeError, InvalidUtcInstantError, TypeError) as error:
            raise RuntimeError(_CLOCK_INVALID) from error


def _parse_replay_request(  # noqa: PLR0911 - refuse each hostile boundary dimension directly.
    value: object,
) -> tuple[ReplayRequest | None, ReplayRefusalReason | None]:
    root = _exact_mapping(value, _REQUEST_FIELDS)
    if root is None or root["schema_version"] != 1 or root["record_kind"] != "lab_replay_request":
        return None, ReplayRefusalReason.INVALID_REQUEST
    request_id = _bounded_identifier(root["request_id"])
    namespace = _bounded_identifier(root["namespace"])
    input_kind = root["input_kind"]
    subject = parse_instrument_identity(root["subject"])
    if (
        request_id is None
        or namespace is None
        or input_kind not in ("copied", "synthetic")
        or type(subject) is not EquityInstrumentIdentity
        or not is_data_regime(root["data_regime"])
        or not is_sha256(root["portfolio_context_fingerprint"])
    ):
        return None, ReplayRefusalReason.INVALID_REQUEST
    try:
        cutoff = UtcInstant.parse(root["evidence_cutoff"])
    except InvalidUtcInstantError:
        return None, ReplayRefusalReason.INVALID_REQUEST
    constitution = root["constitution"]
    belief_graph = root["belief_graph"]
    if type(constitution) is not ConstitutionArtifact or type(belief_graph) is not BeliefGraph:
        return None, ReplayRefusalReason.INVALID_REQUEST
    evidence, evidence_refusal = _parse_evidence(root["evidence"], subject, cutoff)
    if evidence is None:
        return None, evidence_refusal
    prompt = _parse_prompt(root["prompt"])
    model_configuration = _parse_model_configuration(root["model_configuration"])
    tools = _parse_tools(root["tools"])
    material_hashes = _parse_hashes(root["material_input_hashes"])
    if prompt is None or model_configuration is None or tools is None or material_hashes is None:
        return None, ReplayRefusalReason.INVALID_REQUEST
    if not _belief_graph_matches_replay(belief_graph, subject, cutoff, evidence):
        return None, ReplayRefusalReason.INVALID_REQUEST
    required_hashes = {
        *(item.content_hash for item in evidence),
        constitution.content_hash,
        belief_graph.content_hash,
        root["portfolio_context_fingerprint"],
        prompt.content_hash,
        model_configuration.content_hash,
        *(item.schema_hash for item in tools),
    }
    if material_hashes != tuple(sorted(required_hashes)):
        return None, ReplayRefusalReason.INVALID_REQUEST
    return (
        ReplayRequest(
            request_id,
            namespace,
            input_kind,
            subject,
            evidence,
            cutoff,
            root["data_regime"],
            constitution,
            belief_graph,
            root["portfolio_context_fingerprint"],
            prompt,
            model_configuration,
            tools,
            material_hashes,
        ),
        None,
    )


def _parse_resolution_request(  # noqa: PLR0911
    value: object,
) -> tuple[ResolutionReplayRequest | None, ReplayRefusalReason | None]:
    root = _exact_mapping(value, _RESOLUTION_REQUEST_FIELDS)
    if (
        root is None
        or root["schema_version"] != _RESOLUTION_SCHEMA_VERSION
        or root["record_kind"] != "lab_replay_request"
    ):
        return None, ReplayRefusalReason.INVALID_REQUEST
    request_id = _bounded_identifier(root["request_id"])
    namespace = _bounded_identifier(root["namespace"])
    subject = parse_instrument_identity(root["subject"])
    if (
        request_id is None
        or namespace is None
        or root["input_kind"] not in ("copied", "synthetic")
        or type(subject) is not EquityInstrumentIdentity
        or not is_data_regime(root["data_regime"])
        or not is_sha256(root["portfolio_context_fingerprint"])
    ):
        return None, ReplayRefusalReason.INVALID_REQUEST
    try:
        cutoff = UtcInstant.parse(root["evidence_cutoff"])
    except InvalidUtcInstantError:
        return None, ReplayRefusalReason.INVALID_REQUEST
    constitution = root["constitution"]
    graph = root["belief_graph"]
    if type(constitution) is not ConstitutionArtifact or type(graph) is not BeliefGraph:
        return None, ReplayRefusalReason.INVALID_REQUEST
    evidence, evidence_refusal = _parse_evidence(root["evidence"], subject, cutoff)
    if evidence is None:
        return None, evidence_refusal
    if not _belief_graph_matches_replay(graph, subject, cutoff, evidence):
        return None, ReplayRefusalReason.INVALID_REQUEST
    dossier_value = root["dossier"]
    if type(dossier_value) is not Dossier:
        return None, ReplayRefusalReason.INVALID_REQUEST
    try:
        dossier_value.__post_init__()
    except (AttributeError, TypeError, ValueError):
        return None, ReplayRefusalReason.INVALID_REQUEST
    reparsed_dossier = parse_dossier(
        dossier_value.model_output_payload(),
        expected_subject=subject,
        available_artifact_ids=tuple(item.artifact_id for item in evidence),
        available_artifact_bindings=tuple(
            (item.artifact_id, item.content_hash) for item in evidence
        ),
        cutoff=cutoff,
    )
    if not isinstance(reparsed_dossier, Dossier) or reparsed_dossier != dossier_value:
        return None, ReplayRefusalReason.INVALID_REQUEST
    role_contracts = _parse_role_contracts(root["role_contracts"])
    material_hashes = _parse_hashes(root["material_input_hashes"])
    if role_contracts is None or material_hashes is None:
        return None, ReplayRefusalReason.INVALID_REQUEST
    required_hashes = {
        *(item.content_hash for item in evidence),
        constitution.content_hash,
        graph.content_hash,
        root["portfolio_context_fingerprint"],
        dossier_value.content_hash,
        *(contract.prompt.content_hash for contract in role_contracts),
        *(contract.model_configuration.content_hash for contract in role_contracts),
        *(tool.schema_hash for contract in role_contracts for tool in contract.tools),
    }
    if material_hashes != tuple(sorted(required_hashes)):
        return None, ReplayRefusalReason.INVALID_REQUEST
    return (
        ResolutionReplayRequest(
            request_id,
            namespace,
            root["input_kind"],
            subject,
            evidence,
            cutoff,
            root["data_regime"],
            constitution,
            graph,
            root["portfolio_context_fingerprint"],
            dossier_value,
            role_contracts,
            material_hashes,
        ),
        None,
    )


def _parse_role_contracts(value: object) -> tuple[RoleContract, ...] | None:
    if type(value) is not list or len(value) != len(_RESOLUTION_ROLES):
        return None
    contracts: list[RoleContract] = []
    for item in value:
        fields = _exact_mapping(item, _ROLE_CONTRACT_FIELDS)
        if fields is None or type(fields["role"]) is not str:
            return None
        try:
            role = ResearchRole(fields["role"])
        except ValueError:
            return None
        prompt = _parse_prompt(fields["prompt"])
        model_configuration = _parse_model_configuration(fields["model_configuration"])
        tools = _parse_tools(fields["tools"])
        if prompt is None or model_configuration is None or tools is None:
            return None
        contracts.append(RoleContract(role, prompt, model_configuration, tools))
    if tuple(item.role for item in contracts) != _RESOLUTION_ROLES:
        return None
    return tuple(contracts)


def _parse_evidence(  # noqa: PLR0911 - distinguish unavailable evidence from invalid input.
    value: object,
    subject: EquityInstrumentIdentity,
    cutoff: UtcInstant,
) -> tuple[tuple[LabEvidenceInput, ...] | None, ReplayRefusalReason]:
    if type(value) is not list or not 1 <= len(value) <= _MAXIMUM_EVIDENCE_ITEMS:
        return None, ReplayRefusalReason.INVALID_REQUEST
    evidence: list[LabEvidenceInput] = []
    for item in value:
        fields = _exact_mapping(item, _EVIDENCE_FIELDS)
        if fields is None:
            return None, ReplayRefusalReason.INVALID_REQUEST
        artifact_id = fields["artifact_id"]
        content_hash = fields["content_hash"]
        content = fields["content"]
        mapped_subject = parse_instrument_identity(fields["subject"])
        try:
            available_at = UtcInstant.parse(fields["available_at"])
        except InvalidUtcInstantError:
            return None, ReplayRefusalReason.INVALID_REQUEST
        if (
            not is_sha256(artifact_id)
            or not is_sha256(content_hash)
            or type(content) is not str
            or not content
            or type(mapped_subject) is not EquityInstrumentIdentity
            or mapped_subject != subject
            or hashlib.sha256(content.encode()).hexdigest() != content_hash
        ):
            return None, ReplayRefusalReason.INVALID_REQUEST
        if available_at.value > cutoff.value:
            return None, ReplayRefusalReason.UNAVAILABLE_EVIDENCE
        evidence.append(LabEvidenceInput(artifact_id, content_hash, available_at, subject, content))
    if sum(len(item.content) for item in evidence) > _MAXIMUM_EVIDENCE_CHARACTERS or tuple(
        item.artifact_id for item in evidence
    ) != tuple(sorted({item.artifact_id for item in evidence})):
        return None, ReplayRefusalReason.INVALID_REQUEST
    return tuple(evidence), ReplayRefusalReason.INVALID_REQUEST


def _parse_prompt(value: object) -> PromptArtifact | None:
    fields = _exact_mapping(value, _PROMPT_FIELDS)
    if fields is None:
        return None
    prompt_id = _bounded_identifier(fields["prompt_id"])
    content = fields["content"]
    content_hash = fields["content_hash"]
    if (
        fields["schema_version"] != 1
        or prompt_id is None
        or type(content) is not str
        or not content
        or len(content) > _MAXIMUM_PROMPT_CHARACTERS
        or not is_sha256(content_hash)
        or hashlib.sha256(content.encode()).hexdigest() != content_hash
    ):
        return None
    return PromptArtifact(prompt_id, content, content_hash)


def _parse_model_configuration(value: object) -> ModelConfiguration | None:
    fields = _exact_mapping(value, _MODEL_FIELDS)
    if fields is None:
        return None
    reasoning = _exact_mapping(fields["reasoning"], _REASONING_FIELDS)
    model_identity = fields["model_identity"]
    content_hash = fields["content_hash"]
    material = {key: item for key, item in fields.items() if key != "content_hash"}
    if (
        reasoning is None
        or fields["schema_version"] != 1
        or type(model_identity) is not str
        or _MODEL_IDENTITY.fullmatch(model_identity) is None
        or reasoning["effort"] not in ("low", "medium", "high", "xhigh")
        or type(reasoning["maximum_output_tokens"]) is not int
        or not 1 <= reasoning["maximum_output_tokens"] <= _MAXIMUM_OUTPUT_TOKENS
        or type(reasoning["maximum_turns"]) is not int
        or not 1 <= reasoning["maximum_turns"] <= _MAXIMUM_TURNS
        or content_hash != _content_hash(material)
    ):
        return None
    return ModelConfiguration(
        model_identity,
        reasoning["effort"],
        reasoning["maximum_output_tokens"],
        reasoning["maximum_turns"],
        content_hash,
    )


def _parse_tools(value: object) -> tuple[ToolContract, ...] | None:
    if type(value) is not list or len(value) > _MAXIMUM_TOOL_FINGERPRINTS:
        return None
    tools: list[ToolContract] = []
    for item in value:
        fields = _exact_mapping(item, _TOOL_FIELDS)
        if fields is None:
            return None
        name = _bounded_identifier(fields["name"])
        schema_json = fields["schema_json"]
        schema_hash = fields["schema_hash"]
        if (
            name is None
            or type(schema_json) is not str
            or not schema_json
            or len(schema_json) > _MAXIMUM_TOOL_SCHEMA_CHARACTERS
            or not _is_canonical_json_object(schema_json)
            or not is_sha256(schema_hash)
            or hashlib.sha256(schema_json.encode()).hexdigest() != schema_hash
        ):
            return None
        tools.append(ToolContract(name, schema_json, schema_hash))
    if tuple(item.name for item in tools) != tuple(sorted({item.name for item in tools})):
        return None
    return tuple(tools)


def _parse_hashes(value: object) -> tuple[str, ...] | None:
    if (
        type(value) is not list
        or not value
        or any(not is_sha256(item) for item in value)
        or value != sorted(set(value))
    ):
        return None
    return tuple(value)


def _observe_resolution_response(
    call: ResolutionRoleCall, response: ModelCallResponse
) -> LabCallObservation:
    disposition = _response_disposition_for_configuration(
        call.contract.model_configuration, response
    )
    artifact: Thesis | SkepticResult | ScenarioForecast | CioResolution | None = None
    artifact_refusal: (
        ThesisRefusalReason | SkepticRefusalReason | ForecastRefusalReason | CioRefusalReason | None
    ) = None
    if (
        disposition is LabObservationDisposition.INVALID_ARTIFACT
        and response.raw_response is not None
    ):
        try:
            decoded = json.loads(response.raw_response.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            disposition = LabObservationDisposition.INVALID_JSON
        else:
            parsed = _parse_role_output(call, decoded)
            if isinstance(parsed, (Thesis, SkepticResult, ScenarioForecast, CioResolution)):
                artifact = parsed
                disposition = LabObservationDisposition.VALIDATED
            else:
                artifact_refusal = parsed
    return LabCallObservation.create(
        call_id=call.call_id,
        disposition=disposition,
        response=response,
        artifact=artifact,
        artifact_refusal=artifact_refusal,
        retain_raw_response=disposition is not LabObservationDisposition.OVERSIZED_OUTPUT,
    )


def _parse_role_output(
    call: ResolutionRoleCall, value: object
) -> (
    Thesis
    | SkepticResult
    | ScenarioForecast
    | CioResolution
    | (ThesisRefusalReason | SkepticRefusalReason | ForecastRefusalReason | CioRefusalReason)
):
    if call.role is ResearchRole.THESIS_BUILDER:
        return parse_thesis(value, dossier=call.request.dossier)
    if call.role is ResearchRole.INDEPENDENT_SKEPTIC and call.thesis is not None:
        return parse_skeptic_result(value, dossier=call.request.dossier, thesis=call.thesis)
    if (
        call.role is ResearchRole.SCENARIO_FORECASTER
        and call.thesis is not None
        and call.skeptic is not None
    ):
        return parse_scenario_forecast(
            value,
            dossier=call.request.dossier,
            thesis=call.thesis,
            skeptic=call.skeptic,
        )
    if (
        call.role is ResearchRole.CIO
        and call.thesis is not None
        and call.skeptic is not None
        and call.forecast is not None
    ):
        return parse_cio_resolution(
            value,
            dossier=call.request.dossier,
            thesis=call.thesis,
            skeptic=call.skeptic,
            forecast=call.forecast,
        )
    return ThesisRefusalReason.INVALID_SCHEMA


def _observe_response(request: ReplayRequest, response: ModelCallResponse) -> LabCallObservation:
    disposition = _response_disposition(request, response)
    dossier: Dossier | None = None
    dossier_refusal: DossierRefusalReason | None = None
    if (
        disposition is LabObservationDisposition.INVALID_DOSSIER
        and response.raw_response is not None
    ):
        try:
            decoded = json.loads(response.raw_response.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            disposition = LabObservationDisposition.INVALID_JSON
        else:
            parsed = parse_dossier(
                decoded,
                expected_subject=request.subject,
                available_artifact_ids=tuple(item.artifact_id for item in request.evidence),
                available_artifact_bindings=tuple(
                    (item.artifact_id, item.content_hash) for item in request.evidence
                ),
                cutoff=request.evidence_cutoff,
            )
            if isinstance(parsed, Dossier):
                dossier = parsed
                disposition = LabObservationDisposition.VALIDATED
            else:
                dossier_refusal = parsed
    return LabCallObservation.create(
        call_id=request.call_id,
        disposition=disposition,
        response=response,
        artifact=dossier,
        artifact_refusal=dossier_refusal,
        retain_raw_response=disposition is not LabObservationDisposition.OVERSIZED_OUTPUT,
    )


def _response_disposition(
    request: ReplayRequest,
    response: ModelCallResponse,
) -> LabObservationDisposition:
    disposition = _response_disposition_for_configuration(request.model_configuration, response)
    if disposition is LabObservationDisposition.INVALID_ARTIFACT:
        return LabObservationDisposition.INVALID_DOSSIER
    return disposition


def _response_disposition_for_configuration(  # noqa: PLR0911
    configuration: ModelConfiguration,
    response: ModelCallResponse,
) -> LabObservationDisposition:
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


def _belief_graph_matches_replay(
    graph: BeliefGraph,
    subject: EquityInstrumentIdentity,
    cutoff: UtcInstant,
    evidence: tuple[LabEvidenceInput, ...],
) -> bool:
    try:
        graph.__post_init__()
        graph.query.__post_init__()
    except (AttributeError, TypeError, ValueError):
        return False
    evidence_by_id = {item.artifact_id: item for item in evidence}
    if (
        graph.query.subjects != (subject,)
        or graph.query.cutoff != cutoff
        or len(graph.belief_nodes) > graph.query.maximum_belief_events
        or len(graph.evidence_nodes) > graph.query.maximum_evidence_artifacts
        or any(type(node) is not BeliefGraphBeliefNode for node in graph.belief_nodes)
        or any(type(node) is not BeliefGraphEvidenceNode for node in graph.evidence_nodes)
        or any(type(edge) is not BeliefGraphEdge for edge in graph.edges)
    ):
        return False
    belief_event_ids: set[str] = set()
    ledger_positions: set[int] = set()
    for belief_node in graph.belief_nodes:
        event = belief_node.event
        if (
            type(belief_node.ledger_position) is not int
            or belief_node.ledger_position <= 0
            or belief_node.ledger_position in ledger_positions
            or type(belief_node.recorded_at) is not UtcInstant
            or belief_node.recorded_at.value > cutoff.value
            or not validate_belief_event(event)
            or event.subject != subject
            or event.valid_at.value > cutoff.value
            or event.transaction_at.value > cutoff.value
            or event.evidence_cutoff.value > cutoff.value
            or belief_node.recorded_at.value < event.transaction_at.value
            or event.event_id in belief_event_ids
            or any(
                reference.artifact_id not in evidence_by_id
                or evidence_by_id[reference.artifact_id].content_hash != reference.content_hash
                or evidence_by_id[reference.artifact_id].available_at.value
                > event.evidence_cutoff.value
                for reference in event.evidence
            )
        ):
            return False
        belief_event_ids.add(event.event_id)
        ledger_positions.add(belief_node.ledger_position)
    graph_evidence_ids: set[str] = set()
    for evidence_node in graph.evidence_nodes:
        replay_evidence = evidence_by_id.get(evidence_node.artifact_id)
        if (
            replay_evidence is None
            or evidence_node.artifact_id in graph_evidence_ids
            or evidence_node.content_hash != replay_evidence.content_hash
            or evidence_node.available_at != replay_evidence.available_at
            or evidence_node.available_at.value > cutoff.value
        ):
            return False
        graph_evidence_ids.add(evidence_node.artifact_id)
    expected_edges = tuple(
        sorted(
            (
                *(
                    BeliefGraphEdge(
                        BeliefGraphEdgeKind.SUPPORTS,
                        reference.artifact_id,
                        node.event.event_id,
                    )
                    for node in graph.belief_nodes
                    for reference in node.event.evidence
                    if reference.artifact_id in graph_evidence_ids
                ),
                *(
                    BeliefGraphEdge(kind, node.event.event_id, target)
                    for node in graph.belief_nodes
                    for kind, target in (
                        (BeliefGraphEdgeKind.TRANSITION_FROM, node.event.transition_from_event_id),
                        (BeliefGraphEdgeKind.SUPERSEDES, node.event.supersedes_event_id),
                    )
                    if target is not None and target in belief_event_ids
                ),
            ),
            key=lambda edge: (edge.kind.value, edge.source_id, edge.target_id),
        )
    )
    return graph.edges == expected_edges


def _model_response_is_valid(value: object) -> bool:
    return (
        type(value) is ModelCallResponse
        and type(value.disposition) is ModelCallDisposition
        and (value.raw_response is None or type(value.raw_response) is bytes)
        and (
            value.exposed_model_identity is None
            or (
                type(value.exposed_model_identity) is str
                and _MODEL_IDENTITY.fullmatch(value.exposed_model_identity) is not None
            )
        )
        and type(value.input_tokens) is int
        and value.input_tokens >= 0
        and type(value.output_tokens) is int
        and value.output_tokens >= 0
        and type(value.turns) is int
        and value.turns >= 0
        and (
            value.elapsed_milliseconds is None
            or (type(value.elapsed_milliseconds) is int and value.elapsed_milliseconds >= 0)
        )
        and type(value.timing_disposition) is ModelTimingDisposition
    )


def _receipt_from_observation(
    intent: LabCallIntent,
    observation: LabCallObservation,
    *,
    disposition: ReplayDisposition,
) -> ReplayReceipt:
    refusal = {
        LabObservationDisposition.VALIDATED: None,
        LabObservationDisposition.MODEL_TIMEOUT: ReplayRefusalReason.MODEL_TIMEOUT,
        LabObservationDisposition.QUOTA_EXHAUSTED: ReplayRefusalReason.QUOTA_EXHAUSTED,
        LabObservationDisposition.ADAPTER_REFUSED: ReplayRefusalReason.ADAPTER_REFUSED,
        LabObservationDisposition.OVERSIZED_OUTPUT: ReplayRefusalReason.OVERSIZED_OUTPUT,
        LabObservationDisposition.INVALID_JSON: ReplayRefusalReason.INVALID_JSON,
        LabObservationDisposition.INVALID_DOSSIER: ReplayRefusalReason.INCOMPATIBLE_SCHEMA,
        LabObservationDisposition.MODEL_IDENTITY_MISMATCH: (
            ReplayRefusalReason.MODEL_IDENTITY_MISMATCH
        ),
    }[observation.disposition]
    return ReplayReceipt(
        disposition,
        intent.request_id,
        intent.call_id,
        observation.dossier,
        refusal,
        observation.dossier_refusal,
        intent.model_input_hash,
        intent.prompt_fingerprint,
        intent.requested_model_identity,
        observation.exposed_model_identity,
        intent.model_configuration_fingerprint,
        intent.tool_fingerprints,
        intent.material_input_hashes,
        observation.raw_response_hash,
        observation.input_tokens,
        observation.output_tokens,
        observation.turns,
        observation.elapsed_milliseconds,
        observation.timing_disposition,
    )


def _receipt_from_intent(
    intent: LabCallIntent,
    *,
    disposition: ReplayDisposition,
    refusal: ReplayRefusalReason,
) -> ReplayReceipt:
    return ReplayReceipt(
        disposition,
        intent.request_id,
        intent.call_id,
        None,
        refusal,
        None,
        intent.model_input_hash,
        intent.prompt_fingerprint,
        intent.requested_model_identity,
        None,
        intent.model_configuration_fingerprint,
        intent.tool_fingerprints,
        intent.material_input_hashes,
        None,
        0,
        0,
        0,
        None,
        None,
    )


def _role_call_receipt(
    intent: LabCallIntent,
    observation: LabCallObservation,
    retry_disposition: LabCallPreparationDisposition,
) -> RoleCallReceipt:
    timing = observation.timing_disposition
    return RoleCallReceipt(
        intent.role,
        intent.call_id,
        retry_disposition,
        observation.disposition,
        intent.model_input_hash,
        intent.prompt_fingerprint,
        intent.requested_model_identity,
        observation.exposed_model_identity,
        intent.model_configuration_fingerprint,
        intent.tool_fingerprints,
        intent.material_input_hashes,
        observation.raw_response_hash,
        observation.input_tokens,
        observation.output_tokens,
        observation.turns,
        observation.elapsed_milliseconds,
        timing,
    )


def _refusal_from_observation(
    disposition: LabObservationDisposition,
) -> ReplayRefusalReason:
    return {
        LabObservationDisposition.VALIDATED: ReplayRefusalReason.ADAPTER_REFUSED,
        LabObservationDisposition.MODEL_TIMEOUT: ReplayRefusalReason.MODEL_TIMEOUT,
        LabObservationDisposition.QUOTA_EXHAUSTED: ReplayRefusalReason.QUOTA_EXHAUSTED,
        LabObservationDisposition.ADAPTER_REFUSED: ReplayRefusalReason.ADAPTER_REFUSED,
        LabObservationDisposition.OVERSIZED_OUTPUT: ReplayRefusalReason.OVERSIZED_OUTPUT,
        LabObservationDisposition.INVALID_JSON: ReplayRefusalReason.INVALID_JSON,
        LabObservationDisposition.INVALID_ARTIFACT: ReplayRefusalReason.INCOMPATIBLE_SCHEMA,
        LabObservationDisposition.INVALID_DOSSIER: ReplayRefusalReason.INCOMPATIBLE_SCHEMA,
        LabObservationDisposition.MODEL_IDENTITY_MISMATCH: (
            ReplayRefusalReason.MODEL_IDENTITY_MISMATCH
        ),
    }[disposition]


def _resolution_receipt(  # noqa: PLR0913, PLR0917
    request: ResolutionReplayRequest,
    intent: LabCallIntent,
    calls: list[RoleCallReceipt],
    thesis: Thesis | None,
    skeptic: SkepticResult | None,
    forecast: ScenarioForecast | None,
    cio: CioResolution | None,
    *,
    disposition: ReplayDisposition,
    refusal: ReplayRefusalReason | None,
    failed_role: ResearchRole | None,
    artifact_refusal: (
        DossierRefusalReason
        | ThesisRefusalReason
        | SkepticRefusalReason
        | ForecastRefusalReason
        | CioRefusalReason
        | None
    ) = None,
) -> ReplayReceipt:
    last = calls[-1] if calls and calls[-1].role is intent.role else None
    return ReplayReceipt(
        disposition=disposition,
        request_id=request.request_id,
        call_id=intent.call_id,
        dossier=request.dossier,
        refusal_reason=refusal,
        dossier_refusal=None,
        model_input_hash=intent.model_input_hash,
        prompt_fingerprint=intent.prompt_fingerprint,
        requested_model_identity=intent.requested_model_identity,
        exposed_model_identity=None if last is None else last.exposed_model_identity,
        model_configuration_fingerprint=intent.model_configuration_fingerprint,
        tool_fingerprints=intent.tool_fingerprints,
        material_input_hashes=intent.material_input_hashes,
        raw_response_hash=None if last is None else last.raw_response_hash,
        input_tokens=sum(item.input_tokens for item in calls),
        output_tokens=sum(item.output_tokens for item in calls),
        turns=sum(item.turns for item in calls),
        elapsed_milliseconds=(
            None
            if any(item.elapsed_milliseconds is None for item in calls)
            else sum(item.elapsed_milliseconds or 0 for item in calls)
        ),
        timing_disposition=None if last is None else last.timing_disposition,
        thesis=thesis,
        skeptic=skeptic,
        forecast=forecast,
        cio_resolution=cio,
        role_calls=tuple(calls),
        failed_role=failed_role,
        artifact_refusal=(
            artifact_refusal
            if isinstance(
                artifact_refusal,
                (
                    ThesisRefusalReason,
                    SkepticRefusalReason,
                    ForecastRefusalReason,
                    CioRefusalReason,
                ),
            )
            else None
        ),
    )


def _input_refusal(
    reason: ReplayRefusalReason,
    request_id: str | None = None,
) -> ReplayReceipt:
    return ReplayReceipt(
        ReplayDisposition.REFUSED,
        request_id,
        None,
        None,
        reason,
        None,
        None,
        None,
        None,
        None,
        None,
        (),
        (),
        None,
        0,
        0,
        0,
        None,
        None,
    )


def _bounded_identifier(value: object) -> str | None:
    return value if type(value) is str and _IDENTIFIER.fullmatch(value) is not None else None


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


def _content_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _is_canonical_json_object(value: str) -> bool:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return False
    return type(parsed) is dict and _canonical_json(parsed) == value
