"""Provide deterministic scripted research-role model observations."""

from __future__ import annotations

from dataclasses import dataclass, field

from agentic_investment_os.research.model import (
    ModelCallDisposition,
    ModelCallRequest,
    ModelCallResponse,
    ModelTimingDisposition,
)

__all__ = ("RecordedEvidenceCollector", "RecordedModelFixture", "RecordedResearchModel")


@dataclass(frozen=True, slots=True)
class RecordedModelFixture:
    """Describe one keyless, network-free model boundary result."""

    disposition: ModelCallDisposition
    raw_response: bytes | None
    exposed_model_identity: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    elapsed_milliseconds: int | None = None
    timing_disposition: ModelTimingDisposition = ModelTimingDisposition.WITHIN_BUDGET
    expected_input_hash: str | None = None

    def response(self) -> ModelCallResponse:
        return ModelCallResponse(
            self.disposition,
            self.raw_response,
            self.exposed_model_identity,
            self.input_tokens,
            self.output_tokens,
            self.turns,
            self.elapsed_milliseconds,
            self.timing_disposition,
        )


@dataclass(slots=True)
class RecordedResearchModel:
    """Replay scripted results once per stable call identity without external effects."""

    fixtures: tuple[RecordedModelFixture, ...]
    _responses: dict[str, ModelCallResponse] = field(default_factory=dict, init=False)
    _unique_effect_count: int = field(default=0, init=False)

    @property
    def unique_effect_count(self) -> int:
        return self._unique_effect_count

    def call(self, request: ModelCallRequest) -> ModelCallResponse:
        prior = self._responses.get(request.call_id)
        if prior is not None:
            return prior
        fixture = (
            self.fixtures[self._unique_effect_count]
            if self._unique_effect_count < len(self.fixtures)
            else None
        )
        self._unique_effect_count += 1
        if fixture is None or (
            fixture.expected_input_hash is not None
            and fixture.expected_input_hash != request.model_input_hash
        ):
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
        else:
            response = fixture.response()
        self._responses[request.call_id] = response
        return response


RecordedEvidenceCollector = RecordedResearchModel
