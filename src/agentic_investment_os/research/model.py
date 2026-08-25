"""Define the model-call and Lab durability ports owned by research."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agentic_investment_os.domain.temporal import UtcInstant
    from agentic_investment_os.research.dossier import Dossier, DossierRefusalReason

__all__ = (
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
)


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


class LabObservationDisposition(StrEnum):
    """Classify the terminal observation appended for one logical model call."""

    VALIDATED = "validated"
    MODEL_TIMEOUT = "model_timeout"
    QUOTA_EXHAUSTED = "quota_exhausted"
    ADAPTER_REFUSED = "adapter_refused"
    OVERSIZED_OUTPUT = "oversized_output"
    INVALID_JSON = "invalid_json"
    INVALID_DOSSIER = "invalid_dossier"
    MODEL_IDENTITY_MISMATCH = "model_identity_mismatch"


class LabCallPreparationDisposition(StrEnum):
    """Select a new effect, completed replay, or changed-material conflict."""

    EFFECT_REQUIRED = "effect_required"
    REPLAY = "replay"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class ModelCallRequest:
    """Supply one reconstructable stateless Evidence Collector invocation."""

    call_id: str
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


class EvidenceCollectorModel(Protocol):
    """Invoke one stateless, effect-idempotent Evidence Collector call."""

    def call(self, request: ModelCallRequest) -> ModelCallResponse: ...


@dataclass(frozen=True, slots=True)
class LabCallIntent:
    """Persist every material model-visible input before invoking the model port."""

    call_id: str
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

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record_kind": "lab_model_call_intent",
            "call_id": self.call_id,
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
    exposed_model_identity: str | None
    input_tokens: int
    output_tokens: int
    turns: int
    elapsed_milliseconds: int | None
    timing_disposition: ModelTimingDisposition
    dossier: Dossier | None
    dossier_refusal: DossierRefusalReason | None

    @classmethod
    def create(
        cls,
        *,
        call_id: str,
        disposition: LabObservationDisposition,
        response: ModelCallResponse,
        dossier: Dossier | None = None,
        dossier_refusal: DossierRefusalReason | None = None,
    ) -> LabCallObservation:
        raw_hash = (
            None
            if response.raw_response is None
            else hashlib.sha256(response.raw_response).hexdigest()
        )
        return cls(
            call_id,
            disposition,
            response.raw_response,
            raw_hash,
            response.exposed_model_identity,
            response.input_tokens,
            response.output_tokens,
            response.turns,
            response.elapsed_milliseconds,
            response.timing_disposition,
            dossier,
            dossier_refusal,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record_kind": "lab_model_call_observation",
            "call_id": self.call_id,
            "disposition": self.disposition.value,
            "raw_response_hash": self.raw_response_hash,
            "exposed_model_identity": self.exposed_model_identity,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "turns": self.turns,
            "elapsed_milliseconds": self.elapsed_milliseconds,
            "timing_disposition": self.timing_disposition.value,
            "dossier": None if self.dossier is None else self.dossier.to_payload(),
            "dossier_refusal": (
                None if self.dossier_refusal is None else self.dossier_refusal.value
            ),
        }


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
