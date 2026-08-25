from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_investment_os.adapters.recorded_model import (
    RecordedEvidenceCollector,
    RecordedModelFixture,
)
from agentic_investment_os.adapters.sqlite_lab import LabPersistenceError, SQLiteLabCallLedger
from agentic_investment_os.application.replay import Replay, ReplayDisposition, ReplayRefusalReason
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.entrypoints.lab import LabCompositionRefusal, configure_replay
from agentic_investment_os.research.dossier import DossierRefusalReason
from agentic_investment_os.research.model import (
    LabCallIntent,
    LabCallLedger,
    LabCallObservation,
    LabCallPreparation,
    LabCallPreparationDisposition,
    LabObservationDisposition,
    ModelCallDisposition,
    ModelCallRequest,
    ModelCallResponse,
    ModelTimingDisposition,
    ResearchRole,
)
from agentic_investment_os.research.resolution import CioRefusalReason, CioStance
from tests._replay import (
    AVAILABLE_AT,
    SUBJECT,
    dossier,
    resolution_bytes,
    resolution_replay_request,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "lab.synthetic.aapl"
ROLE_COUNT = 4
TOTAL_INPUT_TOKENS = 40
TOTAL_OUTPUT_TOKENS = 80
INTERRUPTED_EFFECT_COUNT = 2


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime = datetime(2026, 8, 24, 20, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


@dataclass(slots=True)
class CapturingModel:
    delegate: RecordedEvidenceCollector
    requests: list[ModelCallRequest] = field(default_factory=list)

    def call(self, request: ModelCallRequest) -> ModelCallResponse:
        self.requests.append(request)
        return self.delegate.call(request)


class SimulatedInterruptionError(RuntimeError):
    """Stop after one declared role effect but before its observation append."""


@dataclass(slots=True)
class InterruptAtRole:
    delegate: RecordedEvidenceCollector
    role: ResearchRole

    def call(self, request: ModelCallRequest) -> ModelCallResponse:
        response = self.delegate.call(request)
        if request.role is self.role:
            raise SimulatedInterruptionError
        return response


@dataclass(slots=True)
class CapturingLedger:
    delegate: LabCallLedger
    intents: list[LabCallIntent] = field(default_factory=list)

    def prepare_call(self, intent: LabCallIntent, recorded_at: UtcInstant) -> LabCallPreparation:
        self.intents.append(intent)
        return self.delegate.prepare_call(intent, recorded_at)

    def append_observation(
        self, intent: LabCallIntent, observation: LabCallObservation, recorded_at: UtcInstant
    ) -> LabCallObservation:
        return self.delegate.append_observation(intent, observation, recorded_at)


def _fixtures(
    *, skeptic_decision: str = "accept", stance: str = "hold"
) -> tuple[RecordedModelFixture, ...]:
    return tuple(
        RecordedModelFixture(
            ModelCallDisposition.RESPONDED,
            payload,
            "codex-subscription/test-model",
            input_tokens=10,
            output_tokens=20,
            turns=1,
            elapsed_milliseconds=5,
        )
        for payload in resolution_bytes(skeptic_decision=skeptic_decision, stance=stance)
    )


def _configure(
    lab_root: Path,
    production_root: Path,
    model: CapturingModel | RecordedEvidenceCollector | InterruptAtRole,
) -> Replay | LabCompositionRefusal:
    return configure_replay(
        namespace=NAMESPACE,
        lab_state_root=str(lab_root),
        production_state_roots=(production_root,),
        repository_root=REPOSITORY_ROOT,
        model=model,
        clock=FixedClock(),
    )


def test_replay_resolves_dossier_through_distinct_clean_context_roles_and_reopens(
    tmp_path: Path,
) -> None:
    recorded = RecordedEvidenceCollector(_fixtures())
    capturing = CapturingModel(recorded)
    replay = _configure(tmp_path / "lab", tmp_path / "production", capturing)
    assert isinstance(replay, Replay)

    completed = replay(resolution_replay_request())

    assert completed.disposition is ReplayDisposition.COMPLETED
    assert completed.thesis is not None
    assert completed.skeptic is not None
    assert completed.forecast is not None
    assert completed.cio_resolution is not None
    assert completed.cio_resolution.stance is CioStance.HOLD
    assert tuple(item.role for item in completed.role_calls) == (
        ResearchRole.THESIS_BUILDER,
        ResearchRole.INDEPENDENT_SKEPTIC,
        ResearchRole.SCENARIO_FORECASTER,
        ResearchRole.CIO,
    )
    assert len({item.call_id for item in completed.role_calls}) == ROLE_COUNT
    assert all(
        item.retry_disposition is LabCallPreparationDisposition.EFFECT_REQUIRED
        for item in completed.role_calls
    )
    assert completed.input_tokens == TOTAL_INPUT_TOKENS
    assert completed.output_tokens == TOTAL_OUTPUT_TOKENS
    assert completed.turns == ROLE_COUNT
    assert recorded.unique_effect_count == ROLE_COUNT
    skeptic_request = capturing.requests[1]
    skeptic_input = json.loads(skeptic_request.model_input_json)
    assert skeptic_request.role is ResearchRole.INDEPENDENT_SKEPTIC
    assert skeptic_input["thesis"] == completed.thesis.to_payload()
    assert skeptic_input["skeptic"] is None
    assert skeptic_input["forecast"] is None
    assert "evidence" not in skeptic_input
    assert "raw_response" not in skeptic_request.model_input_json
    assert "scratch" not in skeptic_request.model_input_json
    with sqlite3.connect(tmp_path / "lab" / "research-lab.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM lab_call_intents").fetchone() == (4,)
        assert connection.execute("SELECT COUNT(*) FROM lab_call_observations").fetchone() == (4,)

    unused = RecordedEvidenceCollector(())
    reopened = _configure(tmp_path / "lab", tmp_path / "production", unused)
    assert isinstance(reopened, Replay)
    replayed = reopened(resolution_replay_request())

    assert replayed.disposition is ReplayDisposition.REPLAYED
    assert replayed.cio_resolution == completed.cio_resolution
    assert all(
        item.retry_disposition is LabCallPreparationDisposition.REPLAY
        for item in replayed.role_calls
    )
    assert unused.unique_effect_count == 0


def test_changed_later_role_contract_replays_prior_roles_then_conflicts_without_effect(
    tmp_path: Path,
) -> None:
    recorded = RecordedEvidenceCollector(_fixtures())
    replay = _configure(tmp_path / "lab", tmp_path / "production", recorded)
    assert isinstance(replay, Replay)
    assert replay(resolution_replay_request()).disposition is ReplayDisposition.COMPLETED

    conflict = replay(resolution_replay_request(changed_role="cio"))

    assert conflict.disposition is ReplayDisposition.CONFLICTED
    assert conflict.refusal_reason is ReplayRefusalReason.IDENTITY_CONFLICT
    assert conflict.failed_role is ResearchRole.CIO
    assert tuple(item.role for item in conflict.role_calls) == (
        ResearchRole.THESIS_BUILDER,
        ResearchRole.INDEPENDENT_SKEPTIC,
        ResearchRole.SCENARIO_FORECASTER,
    )
    assert recorded.unique_effect_count == ROLE_COUNT


def test_interrupted_skeptic_is_indeterminate_after_reopen_and_never_repeated(
    tmp_path: Path,
) -> None:
    first_recorded = RecordedEvidenceCollector(_fixtures())
    replay = _configure(
        tmp_path / "lab",
        tmp_path / "production",
        InterruptAtRole(first_recorded, ResearchRole.INDEPENDENT_SKEPTIC),
    )
    assert isinstance(replay, Replay)

    with pytest.raises(SimulatedInterruptionError):
        replay(resolution_replay_request())

    fresh_recorded = RecordedEvidenceCollector(_fixtures())
    reopened = _configure(tmp_path / "lab", tmp_path / "production", fresh_recorded)
    assert isinstance(reopened, Replay)
    refused = reopened(resolution_replay_request())

    assert refused.disposition is ReplayDisposition.REFUSED
    assert refused.refusal_reason is ReplayRefusalReason.INDETERMINATE_MODEL_EFFECT
    assert refused.failed_role is ResearchRole.INDEPENDENT_SKEPTIC
    assert first_recorded.unique_effect_count == INTERRUPTED_EFFECT_COUNT
    assert fresh_recorded.unique_effect_count == 0


def test_requested_evidence_with_active_cio_stance_is_a_typed_refusal(tmp_path: Path) -> None:
    recorded = RecordedEvidenceCollector(
        _fixtures(skeptic_decision="request_evidence", stance="long")
    )
    replay = _configure(tmp_path / "lab", tmp_path / "production", recorded)
    assert isinstance(replay, Replay)

    refused = replay(resolution_replay_request())

    assert refused.disposition is ReplayDisposition.REFUSED
    assert refused.refusal_reason is ReplayRefusalReason.INCOMPATIBLE_SCHEMA
    assert refused.failed_role is ResearchRole.CIO
    assert refused.artifact_refusal is CioRefusalReason.UNRESOLVED_SKEPTIC
    assert refused.skeptic is not None
    assert refused.cio_resolution is None
    assert recorded.unique_effect_count == ROLE_COUNT


def test_requested_evidence_with_cio_abstention_remains_a_valid_no_action(tmp_path: Path) -> None:
    recorded = RecordedEvidenceCollector(
        _fixtures(skeptic_decision="request_evidence", stance="abstain")
    )
    replay = _configure(tmp_path / "lab", tmp_path / "production", recorded)
    assert isinstance(replay, Replay)

    completed = replay(resolution_replay_request())

    assert completed.disposition is ReplayDisposition.COMPLETED
    assert completed.skeptic is not None
    assert completed.cio_resolution is not None
    assert completed.cio_resolution.stance is CioStance.ABSTAIN
    assert completed.refusal_reason is None


def test_skeptic_rejection_remains_visible_and_cannot_become_hold(tmp_path: Path) -> None:
    rejected = RecordedEvidenceCollector(_fixtures(skeptic_decision="reject", stance="hold"))
    replay = _configure(tmp_path / "rejected", tmp_path / "production", rejected)
    assert isinstance(replay, Replay)

    refused = replay(resolution_replay_request())

    assert refused.disposition is ReplayDisposition.REFUSED
    assert refused.artifact_refusal is CioRefusalReason.UNRESOLVED_SKEPTIC
    assert refused.skeptic is not None
    assert refused.skeptic.decision.value == "reject"

    resolved = RecordedEvidenceCollector(_fixtures(skeptic_decision="reject", stance="exit"))
    exit_replay = _configure(tmp_path / "resolved", tmp_path / "production", resolved)
    assert isinstance(exit_replay, Replay)
    completed = exit_replay(resolution_replay_request())

    assert completed.disposition is ReplayDisposition.COMPLETED
    assert completed.skeptic is not None
    assert completed.skeptic.decision.value == "reject"
    assert completed.cio_resolution is not None
    assert completed.cio_resolution.stance is CioStance.EXIT


def test_changed_unreferenced_evidence_binding_conflicts_before_another_role_effect(
    tmp_path: Path,
) -> None:
    request = resolution_replay_request()
    evidence = request["evidence"]
    material_hashes = request["material_input_hashes"]
    assert isinstance(evidence, list)
    assert isinstance(material_hashes, list)
    added: list[dict[str, object]] = []
    for artifact_id, content in (("b" * 64, "Copied source B."), ("c" * 64, "Copied source C.")):
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        added.append(
            {
                "artifact_id": artifact_id,
                "content_hash": content_hash,
                "available_at": AVAILABLE_AT.isoformat(),
                "subject": SUBJECT.to_payload(),
                "content": content,
            }
        )
        material_hashes.append(content_hash)
    evidence.extend(added)
    material_hashes.sort()
    changed = deepcopy(request)
    changed_evidence = changed["evidence"]
    assert isinstance(changed_evidence, list)
    first_extra = changed_evidence[1]
    second_extra = changed_evidence[2]
    assert isinstance(first_extra, dict)
    assert isinstance(second_extra, dict)
    first_extra["content"], second_extra["content"] = (
        second_extra["content"],
        first_extra["content"],
    )
    first_extra["content_hash"], second_extra["content_hash"] = (
        second_extra["content_hash"],
        first_extra["content_hash"],
    )
    recorded = RecordedEvidenceCollector(_fixtures())
    replay = _configure(tmp_path / "lab", tmp_path / "production", recorded)
    assert isinstance(replay, Replay)
    assert replay(request).disposition is ReplayDisposition.COMPLETED

    conflict = replay(changed)

    assert conflict.disposition is ReplayDisposition.CONFLICTED
    assert conflict.failed_role is ResearchRole.THESIS_BUILDER
    assert recorded.unique_effect_count == ROLE_COUNT


def test_physical_role_column_corruption_fails_before_reopen(tmp_path: Path) -> None:
    lab_root = tmp_path / "lab"
    replay = _configure(
        lab_root,
        tmp_path / "production",
        RecordedEvidenceCollector(_fixtures()),
    )
    assert isinstance(replay, Replay)
    assert replay(resolution_replay_request()).disposition is ReplayDisposition.COMPLETED
    database = lab_root / "research-lab.sqlite3"
    with sqlite3.connect(database) as connection:
        trigger_row = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name = 'lab_call_intents_no_update'"
        ).fetchone()
        assert trigger_row is not None
        trigger_sql = trigger_row[0]
        assert isinstance(trigger_sql, str)
        connection.execute("DROP TRIGGER lab_call_intents_no_update")
        connection.execute(
            "UPDATE lab_call_intents SET role = 'evidence_collector' WHERE role = 'thesis_builder'"
        )
        connection.execute(trigger_sql)

    with pytest.raises(LabPersistenceError, match="Research Lab history is invalid"):
        SQLiteLabCallLedger(database, NAMESPACE)


def test_sqlite_ledger_refuses_artifact_owned_by_another_role(tmp_path: Path) -> None:
    recorded = RecordedEvidenceCollector(_fixtures())
    configured = _configure(
        tmp_path / "lab",
        tmp_path / "production",
        InterruptAtRole(recorded, ResearchRole.THESIS_BUILDER),
    )
    assert isinstance(configured, Replay)
    ledger = CapturingLedger(configured.ledger)
    replay = Replay(NAMESPACE, ledger, configured.model, FixedClock())
    with pytest.raises(SimulatedInterruptionError):
        replay(resolution_replay_request())
    assert len(ledger.intents) == 1
    intent = ledger.intents[0]
    response = ModelCallResponse(
        ModelCallDisposition.RESPONDED,
        b"{}",
        "codex-subscription/test-model",
        1,
        1,
        1,
        1,
        ModelTimingDisposition.WITHIN_BUDGET,
    )
    wrong_role = LabCallObservation.create(
        call_id=intent.call_id,
        disposition=LabObservationDisposition.VALIDATED,
        response=response,
        artifact=dossier(),
    )

    with pytest.raises(LabPersistenceError, match="could not append an observation"):
        configured.ledger.append_observation(
            intent,
            wrong_role,
            UtcInstant.from_datetime(FixedClock().now()),
        )

    wrong_disposition = LabCallObservation.create(
        call_id=intent.call_id,
        disposition=LabObservationDisposition.INVALID_DOSSIER,
        response=response,
        artifact_refusal=DossierRefusalReason.INVALID_SCHEMA,
    )
    with pytest.raises(LabPersistenceError, match="could not append an observation"):
        configured.ledger.append_observation(
            intent,
            wrong_disposition,
            UtcInstant.from_datetime(FixedClock().now()),
        )
