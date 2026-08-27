from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from agentic_investment_os.adapters.recorded_model import (
    RecordedModelFixture,
    RecordedResearchModel,
)
from agentic_investment_os.application.lifecycle import Advance, Status
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.lifecycle import (
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRecovery,
    InvalidLifecycleStateError,
    LifecyclePersistenceError,
    LifecyclePhase,
    NoActionReason,
)
from agentic_investment_os.entrypoints.configuration import ConfigurationSource
from agentic_investment_os.entrypoints.lifecycle import configure_advance, configure_status
from agentic_investment_os.memory.admission import RecordRefusalCode
from agentic_investment_os.memory.beliefs import (
    BeliefGraph,
    BeliefGraphRefusal,
    BeliefGraphRefusalCode,
    BeliefPersistenceError,
    RecordReceipt,
)
from agentic_investment_os.research.model import (
    MAXIMUM_MODEL_OUTPUT_BYTES,
    LabObservationDisposition,
    ModelCallDisposition,
    ModelCallRequest,
    ModelCallResponse,
    ModelTimingDisposition,
    ResearchRole,
    ResearchRoleModel,
)
from agentic_investment_os.research.resolution import (
    ScenarioForecast,
    SkepticResult,
    Thesis,
    parse_scenario_forecast,
    parse_skeptic_result,
    parse_thesis,
)
from tests._governance import RecordedSessionEligibility
from tests._production_research import (
    ValidProductionModel,
    production_recorded_evidence,
    production_recorded_official_evidence,
)
from tests._replay import (
    ARTIFACT_ID,
    SUBJECT,
    dossier,
    dossier_payload,
    forecast_payload,
    resolution_payload,
    skeptic_payload,
    thesis_payload,
)
from tests._universe import recorded_universe, reseal_recorded_snapshot, runtime_configuration

if TYPE_CHECKING:
    from agentic_investment_os.application.memory import Record

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODEL_IDENTITY = "codex-subscription/test-model"
EXPECTED_SUBJECTS = 2
EXPECTED_CALLS = 10
EXPECTED_RESOLUTION_ARTIFACTS = 8
EXPECTED_NO_THESIS_CALLS = 2
EXPECTED_INVALID_THESIS_CALLS = 2


@dataclass(frozen=True, slots=True)
class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 21, 22, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _ClockAt:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


@dataclass(slots=True)
class _ValidProductionModel:
    unique_effect_count: int = 0

    def call(self, request: ModelCallRequest) -> ModelCallResponse:
        self.unique_effect_count += 1
        model_input = json.loads(request.model_input_json)
        assert isinstance(model_input, dict)
        evidence = model_input["evidence"]
        assert isinstance(evidence, list)
        assert evidence
        first_evidence = evidence[0]
        assert isinstance(first_evidence, dict)
        artifact_id = first_evidence["artifact_id"]
        available_at = first_evidence["available_at"]
        subject = model_input["subject"]
        assert isinstance(artifact_id, str)
        assert isinstance(available_at, str)
        assert isinstance(subject, dict)
        output = _role_output(request.role, model_input)
        output = _replace_fixture(output, subject, artifact_id)
        if request.role is ResearchRole.EVIDENCE_COLLECTOR:
            facts = output["facts"]
            interpretations = output["interpretations"]
            assert isinstance(facts, list)
            assert isinstance(interpretations, list)
            for assertion in (*facts, *interpretations):
                assert isinstance(assertion, dict)
                assertion["relevant_at"] = available_at
        return ModelCallResponse(
            ModelCallDisposition.RESPONDED,
            json.dumps(output, sort_keys=True, separators=(",", ":")).encode(),
            MODEL_IDENTITY,
            input_tokens=100,
            output_tokens=200,
            turns=1,
            elapsed_milliseconds=10,
            timing_disposition=ModelTimingDisposition.WITHIN_BUDGET,
        )


class _SimulatedModelInterruptionError(RuntimeError):
    pass


@dataclass(slots=True)
class _InterruptingRoleModel:
    role: ResearchRole
    delegate: _ValidProductionModel
    interrupted: bool = False

    def call(self, request: ModelCallRequest) -> ModelCallResponse:
        if request.role is self.role and not self.interrupted:
            self.interrupted = True
            raise _SimulatedModelInterruptionError
        return self.delegate.call(request)


@dataclass(slots=True)
class _ActiveUninvestableModel:
    delegate: _ValidProductionModel

    @property
    def unique_effect_count(self) -> int:
        return self.delegate.unique_effect_count

    def call(self, request: ModelCallRequest) -> ModelCallResponse:
        response = self.delegate.call(request)
        if request.role is not ResearchRole.THESIS_BUILDER:
            return response
        assert response.raw_response is not None
        payload = json.loads(response.raw_response)
        assert isinstance(payload, dict)
        conditions = payload["uninvestable_conditions"]
        assert isinstance(conditions, list)
        condition = conditions[0]
        assert isinstance(condition, dict)
        condition["active"] = True
        return replace(
            response,
            raw_response=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        )


@dataclass(slots=True)
class _InvalidThesisModel:
    delegate: _ValidProductionModel

    @property
    def unique_effect_count(self) -> int:
        return self.delegate.unique_effect_count

    def call(self, request: ModelCallRequest) -> ModelCallResponse:
        response = self.delegate.call(request)
        return (
            replace(response, raw_response=b"{}")
            if request.role is ResearchRole.THESIS_BUILDER
            else response
        )


@dataclass(frozen=True, slots=True)
class _RefusingMemory:
    delegate: Record

    def __call__(self, _event: object) -> RecordReceipt:
        return RecordReceipt.refused(RecordRefusalCode.INVALID_AUTHORITATIVE_HISTORY)

    def graph(self, query: object) -> BeliefGraph | BeliefGraphRefusal:
        return self.delegate.graph(query)


@dataclass(frozen=True, slots=True)
class _RefusingCurrentBeliefHead:
    delegate: Record

    def __call__(self, event: object) -> RecordReceipt:
        return self.delegate(event)

    def graph(self, query: object) -> BeliefGraph | BeliefGraphRefusal:
        if isinstance(query, dict) and query.get("cutoff") == "2026-08-21T22:00:00.000000+00:00":
            return BeliefGraphRefusal(BeliefGraphRefusalCode.INVALID_AUTHORITATIVE_HISTORY)
        return self.delegate.graph(query)


def _role_output(role: ResearchRole, model_input: dict[str, object]) -> dict[str, object]:
    if role is ResearchRole.EVIDENCE_COLLECTOR:
        return dossier_payload()
    fixed_dossier = dossier()
    fixed_thesis = parse_thesis(thesis_payload(), dossier=fixed_dossier)
    assert isinstance(fixed_thesis, Thesis)
    dossier_input = model_input["dossier"]
    assert isinstance(dossier_input, dict)
    dossier_id = dossier_input["content_hash"]
    assert isinstance(dossier_id, str)
    if role is ResearchRole.THESIS_BUILDER:
        payload = thesis_payload()
        payload["dossier_id"] = dossier_id
        return payload
    thesis_input = model_input["thesis"]
    assert isinstance(thesis_input, dict)
    thesis_id = thesis_input["content_hash"]
    assert isinstance(thesis_id, str)
    if role is ResearchRole.INDEPENDENT_SKEPTIC:
        payload = skeptic_payload(fixed_thesis)
        payload["dossier_id"] = dossier_id
        payload["thesis_id"] = thesis_id
        return payload
    fixed_skeptic = parse_skeptic_result(
        skeptic_payload(fixed_thesis),
        dossier=fixed_dossier,
        thesis=fixed_thesis,
    )
    assert isinstance(fixed_skeptic, SkepticResult)
    skeptic_input = model_input["skeptic"]
    assert isinstance(skeptic_input, dict)
    skeptic_id = skeptic_input["content_hash"]
    assert isinstance(skeptic_id, str)
    if role is ResearchRole.SCENARIO_FORECASTER:
        payload = forecast_payload(fixed_thesis, fixed_skeptic)
        payload["dossier_id"] = dossier_id
        payload["thesis_id"] = thesis_id
        payload["skeptic_id"] = skeptic_id
        return payload
    forecast_input = model_input["forecast"]
    assert isinstance(forecast_input, dict)
    forecast_id = forecast_input["content_hash"]
    assert isinstance(forecast_id, str)
    fixed_forecast = parse_scenario_forecast(
        forecast_payload(fixed_thesis, fixed_skeptic),
        dossier=fixed_dossier,
        thesis=fixed_thesis,
        skeptic=fixed_skeptic,
    )
    assert isinstance(fixed_forecast, ScenarioForecast)
    payload = resolution_payload(fixed_thesis, fixed_skeptic, fixed_forecast)
    payload["dossier_id"] = dossier_id
    payload["thesis_id"] = thesis_id
    payload["skeptic_id"] = skeptic_id
    payload["forecast_id"] = forecast_id
    rationale = payload["rationale"]
    assert isinstance(rationale, dict)
    rationale["resolution_artifact_ids"] = sorted((thesis_id, skeptic_id, forecast_id))
    return payload


def _replace_fixture(
    value: dict[str, object],
    subject: dict[str, object],
    artifact_id: str,
) -> dict[str, object]:
    def replace(item: object) -> object:
        if item == SUBJECT.to_payload():
            return deepcopy(subject)
        if item == ARTIFACT_ID:
            return artifact_id
        if isinstance(item, dict):
            result = {key: replace(child) for key, child in item.items()}
            if "authority_scope" in result:
                result["authority_scope"] = "production_research"
                result["non_production"] = False
            return result
        if isinstance(item, list):
            return [replace(child) for child in item]
        return item

    replaced = replace(value)
    assert isinstance(replaced, dict)
    return replaced


def _production_universe_with_active_holding() -> dict[str, object]:
    universe = recorded_universe()
    instruments = universe["instruments"]
    assert isinstance(instruments, dict)
    instrument_payload = instruments["payload"]
    assert isinstance(instrument_payload, dict)
    items = instrument_payload["items"]
    assert isinstance(items, list)
    holding = items[1]
    assert isinstance(holding, dict)
    holding_facts = holding["payload"]
    assert isinstance(holding_facts, dict)
    holding_facts["status"] = "active"
    holding_facts["tradable"] = True
    instrument_payload["items"] = [items[index] for index in (0, 1, 2, 4)]
    reseal_recorded_snapshot(universe, "instruments")
    return universe


def _content_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _role_contract(role: str) -> dict[str, object]:
    prompt_content = f"Return only the exact production {role} schema from declared inputs."
    prompt: dict[str, object] = {
        "schema_version": 1,
        "prompt_id": f"production-{role}-v1",
        "content": prompt_content,
        "content_hash": hashlib.sha256(prompt_content.encode()).hexdigest(),
    }
    model_material: dict[str, object] = {
        "schema_version": 1,
        "model_identity": MODEL_IDENTITY,
        "reasoning": {
            "effort": "medium",
            "maximum_output_tokens": 4_000,
            "maximum_turns": 1,
        },
    }
    return {
        "role": role,
        "prompt": prompt,
        "model_configuration": {
            **model_material,
            "content_hash": _content_hash(model_material),
        },
        "tools": [],
    }


def _research_policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_type": "v0_production_research",
        "maximum_belief_events": 20,
        "maximum_evidence_artifacts": 50,
        "role_contracts": [
            _role_contract(role)
            for role in (
                "evidence_collector",
                "thesis_builder",
                "independent_skeptic",
                "scenario_forecaster",
                "cio",
            )
        ],
    }


def _configuration(state_root: Path) -> dict[str, object]:
    return {**runtime_configuration(state_root), "research_policy": _research_policy()}


def _configure(
    state_root: Path,
    *,
    universe: dict[str, object],
    model: ResearchRoleModel,
    clock: _FixedClock | _ClockAt | None = None,
) -> Advance:
    capability = configure_advance(
        (ConfigurationSource("test", _configuration(state_root)),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=universe,
        recorded_evidence=production_recorded_evidence(),
        recorded_official_evidence=production_recorded_official_evidence(),
        recorded_model=model,
        session_eligibility=RecordedSessionEligibility(),
        clock=_FixedClock() if clock is None else clock,
    )
    assert isinstance(capability, Advance)
    return capability


def _advance_on(capability: Advance, key: str, trading_date: date) -> AdvanceReceipt:
    return capability(
        cycle=MarketSession(trading_date).to_payload(),
        mode="champion",
        idempotency_key=key,
    )


def _advance(capability: Advance, key: str) -> AdvanceReceipt:
    return _advance_on(capability, key, date(2026, 8, 21))


def _configured_status(state_root: Path) -> Status:
    status = configure_status(
        (ConfigurationSource("test", _configuration(state_root)),),
        repository_root=REPOSITORY_ROOT,
    )
    assert isinstance(status, Status)
    return status


def _complete_research(
    state_root: Path,
    *,
    model: ResearchRoleModel | None = None,
) -> tuple[AdvanceReceipt, Status]:
    configured = _configure(
        state_root,
        universe=recorded_universe(),
        model=_ValidProductionModel() if model is None else model,
    )
    receipt = _advance(configured, "stage-three-adversarial-history")
    assert receipt.disposition in (AdvanceDisposition.ADVANCED, AdvanceDisposition.NO_ACTION)
    status = _configured_status(state_root)
    status()
    return receipt, status


def _rewrite_research_checkpoint(database: Path, *, wrong_call_owner: bool) -> None:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT research_checkpoint, event_envelope FROM lifecycle_events "
            "WHERE event_kind = 'research_run'"
        ).fetchone()
        assert row is not None
        checkpoint = json.loads(row[0])
        envelope = json.loads(row[1])
        assert isinstance(checkpoint, dict)
        assert isinstance(envelope, dict)
        if wrong_call_owner:
            build_call = connection.execute(
                "SELECT call_id FROM production_research_call_intents "
                "WHERE role = 'evidence_collector' ORDER BY call_id LIMIT 1"
            ).fetchone()
            assert build_call is not None
            call_ids = checkpoint["call_ids"]
            assert isinstance(call_ids, list)
            call_ids[0] = build_call[0]
            call_ids.sort()
        else:
            input_tokens = checkpoint["input_tokens"]
            assert isinstance(input_tokens, int)
            checkpoint["input_tokens"] = input_tokens + 1
        payload = envelope["payload"]
        assert isinstance(payload, dict)
        payload["research_checkpoint"] = checkpoint
        envelope_without_hash = {
            key: value for key, value in envelope.items() if key != "content_hash"
        }
        envelope["content_hash"] = _content_hash(envelope_without_hash)
        connection.execute("DROP TRIGGER lifecycle_events_are_append_only_update")
        connection.execute(
            "UPDATE lifecycle_events SET research_checkpoint = ?, event_envelope = ? "
            "WHERE event_kind = 'research_run'",
            (
                json.dumps(checkpoint, sort_keys=True, separators=(",", ":")),
                json.dumps(envelope, sort_keys=True, separators=(",", ":")),
            ),
        )


def _belief_evidence_relationships(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        event_rows = connection.execute(
            "SELECT event_json FROM belief_events ORDER BY ledger_position"
        ).fetchall()
    return {
        reference["relationship"]
        for row in event_rows
        for reference in json.loads(row[0])["evidence"]
    }


def test_production_advance_completes_stage_three_without_model_effect_for_no_attention(
    tmp_path: Path,
) -> None:
    universe = deepcopy(recorded_universe())
    instruments = universe["instruments"]
    positions = universe["positions"]
    assert isinstance(instruments, dict)
    assert isinstance(positions, dict)
    instrument_payload = instruments["payload"]
    position_payload = positions["payload"]
    assert isinstance(instrument_payload, dict)
    assert isinstance(position_payload, dict)
    items = instrument_payload["items"]
    assert isinstance(items, list)
    instrument_payload["items"] = items[2:]
    position_payload["items"] = []
    reseal_recorded_snapshot(universe, "instruments")
    reseal_recorded_snapshot(universe, "positions")
    model = RecordedResearchModel(())
    capability = _configure(tmp_path / "runtime", universe=universe, model=model)

    receipt = _advance(capability, "stage-three-no-attention")
    replayed = _advance(capability, "stage-three-no-attention")

    assert receipt.disposition is AdvanceDisposition.NO_ACTION
    assert receipt.completed_phase is not None
    assert receipt.completed_phase.phase is LifecyclePhase.UPDATE_MEMORY
    assert receipt.research_resolution_ids == ()
    assert receipt.belief_event_ids == ()
    assert replayed == replace(
        receipt,
        recovery=AdvanceRecovery.PREVIOUSLY_COMPLETED,
    )
    assert model.unique_effect_count == 0


def test_production_advance_fails_closed_on_invalid_evidence_collector_output(
    tmp_path: Path,
) -> None:
    model = RecordedResearchModel(
        (
            RecordedModelFixture(
                ModelCallDisposition.RESPONDED,
                b"{}",
                MODEL_IDENTITY,
                timing_disposition=ModelTimingDisposition.WITHIN_BUDGET,
            ),
        )
    )
    capability = _configure(
        tmp_path / "runtime",
        universe=recorded_universe(),
        model=model,
    )

    receipt = _advance(capability, "stage-three-invalid-dossier")
    replayed = _advance(capability, "stage-three-invalid-dossier")

    assert receipt.disposition is AdvanceDisposition.FAILED_CLOSED
    assert receipt.failure_reason is AdvanceFailureReason.RESEARCH_FAILED
    assert receipt.completed_phase is None
    assert replayed == receipt
    assert model.unique_effect_count == 1


@pytest.mark.parametrize("corruption", ["invalid_json", "mismatched_identity"])
def test_status_rejects_corrupt_structured_research_refusal(
    tmp_path: Path,
    corruption: str,
) -> None:
    state_root = tmp_path / "runtime"
    model = _InvalidThesisModel(_ValidProductionModel())
    capability = _configure(state_root, universe=recorded_universe(), model=model)
    receipt = _advance(capability, "stage-three-invalid-thesis")
    status = _configured_status(state_root)

    assert receipt.failure_reason is AdvanceFailureReason.RESEARCH_FAILED
    assert model.unique_effect_count == EXPECTED_INVALID_THESIS_CALLS
    status()
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        connection.execute("DROP TRIGGER advance_refusals_are_append_only_update")
        if corruption == "invalid_json":
            connection.execute("UPDATE advance_refusals SET research_refusal = 'not-json'")
        else:
            connection.execute(
                "UPDATE advance_refusals SET research_refusal_id = ?",
                ("e" * 64,),
            )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="invalid idempotency_key in lifecycle refusal ledger",
    ):
        status()


@pytest.mark.parametrize(
    ("fixture", "expected_disposition"),
    [
        (
            RecordedModelFixture(
                ModelCallDisposition.RESPONDED,
                b"not-json",
                MODEL_IDENTITY,
            ),
            LabObservationDisposition.INVALID_JSON,
        ),
        (
            RecordedModelFixture(
                ModelCallDisposition.RESPONDED,
                b"{" + b"x" * MAXIMUM_MODEL_OUTPUT_BYTES,
                MODEL_IDENTITY,
            ),
            LabObservationDisposition.OVERSIZED_OUTPUT,
        ),
        (
            RecordedModelFixture(
                ModelCallDisposition.TIMED_OUT,
                None,
                MODEL_IDENTITY,
                timing_disposition=ModelTimingDisposition.TIMED_OUT,
            ),
            LabObservationDisposition.MODEL_TIMEOUT,
        ),
        (
            RecordedModelFixture(
                ModelCallDisposition.QUOTA_EXHAUSTED,
                None,
                MODEL_IDENTITY,
                timing_disposition=ModelTimingDisposition.UNAVAILABLE,
            ),
            LabObservationDisposition.QUOTA_EXHAUSTED,
        ),
        (
            RecordedModelFixture(
                ModelCallDisposition.RESPONDED,
                b"{}",
                MODEL_IDENTITY,
                output_tokens=4_001,
            ),
            LabObservationDisposition.ADAPTER_REFUSED,
        ),
        (
            RecordedModelFixture(
                ModelCallDisposition.RESPONDED,
                b"{}",
                MODEL_IDENTITY,
                turns=2,
            ),
            LabObservationDisposition.ADAPTER_REFUSED,
        ),
    ],
)
def test_production_research_resource_or_boundary_failure_stops_before_memory(
    tmp_path: Path,
    fixture: RecordedModelFixture,
    expected_disposition: LabObservationDisposition,
) -> None:
    state_root = tmp_path / "runtime"
    model = RecordedResearchModel((fixture,))
    capability = _configure(state_root, universe=recorded_universe(), model=model)

    receipt = _advance(capability, "stage-three-model-boundary-refusal")

    assert receipt.disposition is AdvanceDisposition.FAILED_CLOSED
    assert receipt.failure_reason is AdvanceFailureReason.RESEARCH_FAILED
    assert receipt.belief_event_ids == ()
    assert model.unique_effect_count == 1
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        observation_json, raw_response = connection.execute(
            "SELECT observation_json, raw_response FROM production_research_call_observations"
        ).fetchone()
        observation = json.loads(observation_json)
        assert observation["disposition"] == expected_disposition.value
        assert connection.execute("SELECT COUNT(*) FROM belief_events").fetchone() == (0,)
    if expected_disposition is LabObservationDisposition.OVERSIZED_OUTPUT:
        assert raw_response is None


@pytest.mark.parametrize("role", tuple(ResearchRole))
def test_production_research_interruption_after_intent_never_repeats_the_effect(
    tmp_path: Path,
    role: ResearchRole,
) -> None:
    state_root = tmp_path / "runtime"
    interrupted_model = _InterruptingRoleModel(role, _ValidProductionModel())
    capability = _configure(
        state_root,
        universe=recorded_universe(),
        model=interrupted_model,
    )

    with pytest.raises(_SimulatedModelInterruptionError):
        _advance(capability, f"stage-three-interrupt-{role.value}")

    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        intents_before = connection.execute(
            "SELECT COUNT(*) FROM production_research_call_intents"
        ).fetchone()[0]
        observations_before = connection.execute(
            "SELECT COUNT(*) FROM production_research_call_observations"
        ).fetchone()[0]
    assert intents_before == observations_before + 1

    resumed_model = _ValidProductionModel()
    resumed = _configure(
        state_root,
        universe=recorded_universe(),
        model=resumed_model,
    )
    receipt = _advance(resumed, f"stage-three-interrupt-{role.value}")

    assert receipt.disposition is AdvanceDisposition.FAILED_CLOSED
    assert receipt.failure_reason is AdvanceFailureReason.RESEARCH_FAILED
    assert resumed_model.unique_effect_count == 0
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM production_research_call_intents"
        ).fetchone() == (intents_before,)
        assert connection.execute(
            "SELECT COUNT(*) FROM production_research_call_observations"
        ).fetchone() == (observations_before,)


def test_skeptic_rejection_is_durable_no_action_without_memory(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    model = ValidProductionModel(skeptic_decision="reject")
    capability = _configure(state_root, universe=recorded_universe(), model=model)

    receipt = _advance(capability, "stage-three-skeptic-reject")
    replayed = _advance(capability, "stage-three-skeptic-reject")

    assert receipt.disposition is AdvanceDisposition.NO_ACTION
    assert receipt.no_action_reason is NoActionReason.SKEPTIC_REJECTED
    assert receipt.completed_phase is not None
    assert receipt.completed_phase.phase is LifecyclePhase.UPDATE_MEMORY
    assert receipt.belief_event_ids == ()
    assert replayed == replace(receipt, recovery=AdvanceRecovery.PREVIOUSLY_COMPLETED)
    assert model.unique_effect_count > 0
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM belief_events").fetchone() == (0,)


def test_memory_refusal_is_durable_after_model_observations(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    model = _ValidProductionModel()
    capability = _configure(state_root, universe=recorded_universe(), model=model)
    refusing = replace(
        capability,
        memory=cast("Record", _RefusingMemory(capability.memory)),
    )

    receipt = _advance(refusing, "stage-three-memory-refusal")
    effects_after_first = model.unique_effect_count
    replayed = _advance(refusing, "stage-three-memory-refusal")

    assert receipt.disposition is AdvanceDisposition.FAILED_CLOSED
    assert receipt.failure_reason is AdvanceFailureReason.MEMORY_UPDATE_FAILED
    assert replayed == receipt
    assert model.unique_effect_count == effects_after_first
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM production_research_call_observations"
        ).fetchone() == (effects_after_first,)
        assert connection.execute("SELECT COUNT(*) FROM belief_events").fetchone() == (0,)


def test_unavailable_current_belief_head_refuses_memory_without_appending(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    model = _ValidProductionModel()
    capability = _configure(state_root, universe=recorded_universe(), model=model)
    refusing = replace(
        capability,
        memory=cast("Record", _RefusingCurrentBeliefHead(capability.memory)),
    )

    receipt = _advance(refusing, "stage-three-current-belief-head-refusal")

    assert receipt.disposition is AdvanceDisposition.FAILED_CLOSED
    assert receipt.failure_reason is AdvanceFailureReason.MEMORY_UPDATE_FAILED
    assert model.unique_effect_count > 0
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM belief_events").fetchone() == (0,)


def test_production_advance_records_validated_research_and_belief_events_once(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    model = _ValidProductionModel()
    universe = _production_universe_with_active_holding()

    discovery_universe = deepcopy(universe)
    discovery_positions = discovery_universe["positions"]
    assert isinstance(discovery_positions, dict)
    discovery_position_payload = discovery_positions["payload"]
    assert isinstance(discovery_position_payload, dict)
    discovery_position_payload["items"] = []
    reseal_recorded_snapshot(discovery_universe, "positions")

    first_discovery = _configure(
        state_root,
        universe=discovery_universe,
        model=model,
    )
    first_receipt = _advance_on(
        first_discovery,
        "stage-three-discovery-observed",
        date(2026, 8, 19),
    )
    second_discovery = _configure(
        state_root,
        universe=discovery_universe,
        model=model,
    )
    second_receipt = _advance_on(
        second_discovery,
        "stage-three-discovery-watch",
        date(2026, 8, 20),
    )
    assert first_receipt.disposition is AdvanceDisposition.NO_ACTION
    assert second_receipt.disposition is AdvanceDisposition.NO_ACTION
    assert model.unique_effect_count == 0

    capability = _configure(
        state_root,
        universe=universe,
        model=model,
    )

    receipt = _advance(capability, "stage-three-valid-research")
    replayed = _advance(capability, "stage-three-valid-research")

    assert receipt.disposition is AdvanceDisposition.ADVANCED
    assert receipt.completed_phase is not None
    assert receipt.completed_phase.phase is LifecyclePhase.UPDATE_MEMORY
    assert len(receipt.dossier_ids) == EXPECTED_SUBJECTS
    assert len(receipt.research_call_ids) == EXPECTED_CALLS
    assert len(receipt.research_resolution_ids) == EXPECTED_RESOLUTION_ARTIFACTS
    assert len(receipt.belief_event_ids) == EXPECTED_SUBJECTS
    assert replayed == replace(
        receipt,
        recovery=AdvanceRecovery.PREVIOUSLY_COMPLETED,
    )
    assert model.unique_effect_count == EXPECTED_CALLS
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM production_research_call_intents"
        ).fetchone() == (EXPECTED_CALLS,)
        assert connection.execute(
            "SELECT COUNT(*) FROM production_research_call_observations"
        ).fetchone() == (EXPECTED_CALLS,)
        assert connection.execute("SELECT COUNT(*) FROM belief_events").fetchone() == (
            EXPECTED_SUBJECTS,
        )
    assert _belief_evidence_relationships(state_root / "lifecycle.sqlite3") == {
        "supporting",
        "contradicting",
    }

    status = _configured_status(state_root)
    status()

    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        connection.execute(
            "DROP TRIGGER production_research_call_observations_are_append_only_update"
        )
        connection.execute(
            "UPDATE production_research_call_observations "
            "SET observation_hash = ? WHERE call_id = "
            "(SELECT call_id FROM production_research_call_observations ORDER BY call_id LIMIT 1)",
            ("0" * 64,),
        )

    with pytest.raises(
        LifecyclePersistenceError,
        match="invalid production research call history",
    ):
        status()

    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        observation_json = connection.execute(
            "SELECT observation_json FROM production_research_call_observations "
            "ORDER BY call_id LIMIT 1"
        ).fetchone()
        assert observation_json is not None
        assert isinstance(observation_json[0], str)
        connection.execute(
            "UPDATE production_research_call_observations SET observation_hash = ? "
            "WHERE call_id = (SELECT call_id FROM production_research_call_observations "
            "ORDER BY call_id LIMIT 1)",
            (hashlib.sha256(observation_json[0].encode()).hexdigest(),),
        )
        connection.execute(
            "DROP TRIGGER production_research_call_observations_are_append_only_delete"
        )
        connection.execute(
            "DELETE FROM production_research_call_observations WHERE call_id = "
            "(SELECT call_id FROM production_research_call_intents ORDER BY call_id LIMIT 1)"
        )

    with pytest.raises(
        LifecyclePersistenceError,
        match="invalid production research call history",
    ):
        status()


def test_production_research_requires_official_evidence_before_any_model_effect(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    model = _ValidProductionModel()
    capability = configure_advance(
        (ConfigurationSource("test", _configuration(state_root)),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=production_recorded_evidence(),
        recorded_model=model,
        session_eligibility=RecordedSessionEligibility(),
        clock=_FixedClock(),
    )
    assert isinstance(capability, Advance)

    receipt = _advance(capability, "stage-three-missing-official-evidence")

    assert receipt.disposition is AdvanceDisposition.FAILED_CLOSED
    assert receipt.failure_reason is AdvanceFailureReason.RESEARCH_FAILED
    assert model.unique_effect_count == 0
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM production_research_call_intents"
        ).fetchone() == (0,)


def test_production_research_enforces_subject_evidence_bound_before_model_effect(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    configuration = _configuration(state_root)
    policy = configuration["research_policy"]
    assert isinstance(policy, dict)
    policy["maximum_evidence_artifacts"] = 1
    model = _ValidProductionModel()
    capability = configure_advance(
        (ConfigurationSource("test", configuration),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=production_recorded_evidence(),
        recorded_official_evidence=production_recorded_official_evidence(),
        recorded_model=model,
        session_eligibility=RecordedSessionEligibility(),
        clock=_FixedClock(),
    )
    assert isinstance(capability, Advance)

    receipt = _advance(capability, "stage-three-bounded-evidence")

    assert receipt.failure_reason is AdvanceFailureReason.RESEARCH_FAILED
    assert model.unique_effect_count == 0


def test_active_uninvestable_thesis_is_typed_no_action_and_stops_later_roles(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    model = _ActiveUninvestableModel(_ValidProductionModel())
    capability = _configure(state_root, universe=recorded_universe(), model=model)

    receipt = _advance(capability, "stage-three-no-valid-thesis")
    status = _configured_status(state_root)()

    assert receipt.disposition is AdvanceDisposition.NO_ACTION
    assert receipt.no_action_reason is NoActionReason.NO_VALID_THESIS
    assert status.no_action_reason is NoActionReason.NO_VALID_THESIS
    assert status.last_completed_cycle is None
    assert model.unique_effect_count == EXPECTED_NO_THESIS_CALLS
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM belief_events").fetchone() == (0,)


def test_memory_retry_replays_committed_event_with_its_original_transaction_time(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    model = _ValidProductionModel()
    first = _configure(state_root, universe=recorded_universe(), model=model)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER stop_memory_checkpoint
            BEFORE INSERT ON lifecycle_events
            WHEN NEW.event_kind = 'memory_updated'
            BEGIN SELECT RAISE(ABORT, 'injected memory checkpoint interruption'); END
            """
        )

    with pytest.raises(LifecyclePersistenceError, match="SQLite lifecycle checkpoint failed"):
        _advance(first, "stage-three-memory-redelivery")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM belief_events").fetchone() == (1,)
        connection.execute("DROP TRIGGER stop_memory_checkpoint")

    retry = _configure(
        state_root,
        universe=recorded_universe(),
        model=model,
        clock=_ClockAt(datetime(2026, 8, 21, 23, tzinfo=UTC)),
    )
    receipt = _advance(retry, "stage-three-memory-redelivery")

    assert receipt.disposition is AdvanceDisposition.ADVANCED
    assert len(receipt.belief_event_ids) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM belief_events").fetchone() == (1,)


def test_status_rejects_deleted_belief_event_referenced_by_memory_checkpoint(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    receipt, status = _complete_research(state_root)
    assert receipt.belief_event_ids
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        connection.execute("DROP TRIGGER belief_events_are_append_only_delete")
        connection.execute(
            "DELETE FROM belief_events WHERE event_id = ?",
            (receipt.belief_event_ids[0],),
        )

    with pytest.raises(BeliefPersistenceError, match="invalid authoritative belief history"):
        status.memory_history_validator.validate_history(receipt.belief_event_ids)
    with pytest.raises(
        LifecyclePersistenceError,
        match="invalid production research call history",
    ):
        status()


@pytest.mark.parametrize("corruption", ["resource_total", "call_owner"])
def test_status_rejects_coherently_resealed_checkpoint_resources_or_phase_ownership(
    tmp_path: Path,
    corruption: str,
) -> None:
    state_root = tmp_path / "runtime"
    _, status = _complete_research(state_root)
    _rewrite_research_checkpoint(
        state_root / "lifecycle.sqlite3",
        wrong_call_owner=corruption == "call_owner",
    )

    with pytest.raises(
        LifecyclePersistenceError,
        match="invalid production research call history",
    ):
        status()


@pytest.mark.parametrize(
    "corruption",
    ["raw_response", "recorded_at", "schema_version", "record_kind"],
)
def test_status_rederives_observation_content_timing_and_discriminator(
    tmp_path: Path,
    corruption: str,
) -> None:
    state_root = tmp_path / "runtime"
    _, status = _complete_research(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DROP TRIGGER production_research_call_observations_are_append_only_update"
        )
        if corruption == "recorded_at":
            connection.execute(
                "UPDATE production_research_call_observations SET recorded_at = "
                "'2026-08-21T21:59:59.000000+00:00' WHERE call_id = "
                "(SELECT call_id FROM production_research_call_observations "
                "ORDER BY call_id LIMIT 1)"
            )
        else:
            row = connection.execute(
                "SELECT call_id, observation_json, raw_response "
                "FROM production_research_call_observations "
                "WHERE raw_response IS NOT NULL ORDER BY call_id LIMIT 1"
            ).fetchone()
            assert row is not None
            observation = json.loads(row[1])
            raw_response = row[2]
            assert isinstance(observation, dict)
            assert isinstance(raw_response, bytes)
            if corruption == "raw_response":
                raw_response = b"{}"
                observation["raw_response_hash"] = hashlib.sha256(raw_response).hexdigest()
            elif corruption == "schema_version":
                observation["schema_version"] = 2
            else:
                observation["record_kind"] = "coherently_resealed_wrong_kind"
            observation_json = json.dumps(
                observation,
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                "UPDATE production_research_call_observations "
                "SET observation_json = ?, raw_response = ?, observation_hash = ? "
                "WHERE call_id = ?",
                (
                    observation_json,
                    raw_response,
                    hashlib.sha256(observation_json.encode()).hexdigest(),
                    row[0],
                ),
            )

    with pytest.raises(
        LifecyclePersistenceError,
        match="invalid production research call history",
    ):
        status()


def test_status_rejects_coherently_rehashed_model_input_outside_pinned_run(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    _, status = _complete_research(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT call_id, intent_json FROM production_research_call_intents "
            "ORDER BY call_id LIMIT 1"
        ).fetchone()
        assert row is not None
        intent = json.loads(row[1])
        assert isinstance(intent, dict)
        model_input = json.loads(intent["model_input_json"])
        assert isinstance(model_input, dict)
        model_input["data_regime"] = "coherently-altered-regime-v1"
        model_input_json = json.dumps(model_input, sort_keys=True, separators=(",", ":"))
        intent["model_input_json"] = model_input_json
        intent["model_input_hash"] = hashlib.sha256(model_input_json.encode()).hexdigest()
        intent_json = json.dumps(intent, sort_keys=True, separators=(",", ":"))
        connection.execute("DROP TRIGGER production_research_call_intents_are_append_only_update")
        connection.execute(
            "UPDATE production_research_call_intents SET intent_json = ?, intent_hash = ? "
            "WHERE call_id = ?",
            (intent_json, _content_hash(intent), row[0]),
        )

    with pytest.raises(
        LifecyclePersistenceError,
        match="invalid production research call history",
    ):
        status()


def test_status_rejects_deletion_of_structurally_referenced_failed_call(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    model = RecordedResearchModel(
        (
            RecordedModelFixture(
                ModelCallDisposition.RESPONDED,
                b"{}",
                MODEL_IDENTITY,
                timing_disposition=ModelTimingDisposition.WITHIN_BUDGET,
            ),
        )
    )
    capability = _configure(state_root, universe=recorded_universe(), model=model)
    receipt = _advance(capability, "stage-three-structured-failed-call")
    assert receipt.failure_reason is AdvanceFailureReason.RESEARCH_FAILED
    status = _configured_status(state_root)
    status()
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DROP TRIGGER production_research_call_observations_are_append_only_delete"
        )
        connection.execute("DROP TRIGGER production_research_call_intents_are_append_only_delete")
        connection.execute("DELETE FROM production_research_call_observations")
        connection.execute("DELETE FROM production_research_call_intents")

    with pytest.raises(
        LifecyclePersistenceError,
        match="invalid production research call history",
    ):
        status()


def test_invalid_response_timing_is_normalized_to_bounded_adapter_refusal(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    model = RecordedResearchModel(
        (
            RecordedModelFixture(
                ModelCallDisposition.RESPONDED,
                b"{}",
                MODEL_IDENTITY,
                timing_disposition=ModelTimingDisposition.TIMED_OUT,
            ),
        )
    )
    capability = _configure(state_root, universe=recorded_universe(), model=model)

    receipt = _advance(capability, "stage-three-invalid-response-timing")

    assert receipt.failure_reason is AdvanceFailureReason.RESEARCH_FAILED
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        row = connection.execute(
            "SELECT observation_json FROM production_research_call_observations"
        ).fetchone()
    assert row is not None
    observation = json.loads(row[0])
    assert observation["disposition"] == LabObservationDisposition.ADAPTER_REFUSED.value
    assert observation["timing_disposition"] == ModelTimingDisposition.UNAVAILABLE.value
