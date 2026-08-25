from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_investment_os.adapters.recorded_model import (
    RecordedEvidenceCollector,
    RecordedModelFixture,
)
from agentic_investment_os.adapters.sqlite_lab import LabPersistenceError, SQLiteLabCallLedger
from agentic_investment_os.application.replay import (
    Replay,
    ReplayDisposition,
    ReplayRefusalReason,
)
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.entrypoints.lab import (
    LabCompositionRefusal,
    LabCompositionRefusalCode,
    configure_replay,
)
from agentic_investment_os.research.model import (
    EvidenceCollectorModel,
    LabCallIntent,
    LabCallLedger,
    LabCallObservation,
    LabCallPreparation,
    LabObservationDisposition,
    ModelCallDisposition,
    ModelCallRequest,
    ModelCallResponse,
    ModelTimingDisposition,
)
from tests._replay import dossier_bytes, replay_request

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "lab.synthetic.aapl"


class SimulatedInterruptionError(RuntimeError):
    """Stop after the recorded adapter completes but before observation append."""


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime = datetime(2026, 8, 24, 20, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


@dataclass(slots=True)
class InterruptAfterModelEffect:
    delegate: RecordedEvidenceCollector
    interrupted: bool = False

    def call(self, request: ModelCallRequest) -> ModelCallResponse:
        response = self.delegate.call(request)
        if not self.interrupted:
            self.interrupted = True
            raise SimulatedInterruptionError
        return response


@dataclass(frozen=True, slots=True)
class IntentCheckingModel:
    database: Path
    delegate: RecordedEvidenceCollector

    def call(self, request: ModelCallRequest) -> ModelCallResponse:
        with sqlite3.connect(self.database) as connection:
            intents = connection.execute("SELECT COUNT(*) FROM lab_call_intents").fetchone()
            observations = connection.execute(
                "SELECT COUNT(*) FROM lab_call_observations"
            ).fetchone()
        assert intents == (1,)
        assert observations == (0,)
        return self.delegate.call(request)


@dataclass(slots=True)
class CapturingLabLedger:
    delegate: LabCallLedger
    intent: LabCallIntent | None = None

    def prepare_call(self, intent: LabCallIntent, recorded_at: UtcInstant) -> LabCallPreparation:
        self.intent = intent
        return self.delegate.prepare_call(intent, recorded_at)

    def append_observation(
        self,
        intent: LabCallIntent,
        observation: LabCallObservation,
        recorded_at: UtcInstant,
    ) -> LabCallObservation:
        return self.delegate.append_observation(intent, observation, recorded_at)


def _fixture() -> RecordedModelFixture:
    return RecordedModelFixture(
        ModelCallDisposition.RESPONDED,
        dossier_bytes(),
        "codex-subscription/test-model",
        input_tokens=100,
        output_tokens=50,
        turns=1,
        elapsed_milliseconds=25,
    )


def _configure(
    lab_root: Path,
    production_root: Path,
    model: EvidenceCollectorModel,
) -> Replay | LabCompositionRefusal:
    return configure_replay(
        namespace=NAMESPACE,
        lab_state_root=str(lab_root),
        production_state_roots=(production_root,),
        repository_root=REPOSITORY_ROOT,
        model=model,
        clock=FixedClock(),
    )


def test_replay_persists_intent_before_effect_and_reopens_without_another_call(
    tmp_path: Path,
) -> None:
    lab_root = tmp_path / "lab"
    production_root = tmp_path / "production"
    recorded = RecordedEvidenceCollector((_fixture(),))
    model = IntentCheckingModel(lab_root / "research-lab.sqlite3", recorded)
    replay = _configure(lab_root, production_root, model)
    assert isinstance(replay, Replay)

    completed = replay(replay_request())
    replayed = replay(replay_request())

    assert completed.disposition is ReplayDisposition.COMPLETED
    assert completed.dossier is not None
    assert completed.dossier.non_production is True
    assert completed.authority_scope == "research_lab_non_production"
    assert completed.non_production is True
    assert replayed.disposition is ReplayDisposition.REPLAYED
    assert replayed.authority_scope == "research_lab_non_production"
    assert replayed.non_production is True
    assert replayed.dossier_id == completed.dossier_id
    assert replayed.raw_response_hash == completed.raw_response_hash
    assert recorded.unique_effect_count == 1
    with sqlite3.connect(lab_root / "research-lab.sqlite3") as connection:
        counts = (
            connection.execute("SELECT COUNT(*) FROM lab_call_intents").fetchone(),
            connection.execute("SELECT COUNT(*) FROM lab_call_observations").fetchone(),
        )
    assert counts == ((1,), (1,))

    unused_model = RecordedEvidenceCollector(())
    reopened = _configure(lab_root, production_root, unused_model)
    assert isinstance(reopened, Replay)
    reopened_receipt = reopened(replay_request())
    assert reopened_receipt.disposition is ReplayDisposition.REPLAYED
    assert reopened_receipt.dossier_id == completed.dossier_id
    assert unused_model.unique_effect_count == 0


def test_interruption_after_model_effect_fails_closed_across_process_reopen(
    tmp_path: Path,
) -> None:
    lab_root = tmp_path / "lab"
    production_root = tmp_path / "production"
    recorded = RecordedEvidenceCollector((_fixture(),))
    replay = _configure(lab_root, production_root, InterruptAfterModelEffect(recorded))
    assert isinstance(replay, Replay)

    with pytest.raises(SimulatedInterruptionError):
        replay(replay_request())
    with sqlite3.connect(lab_root / "research-lab.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM lab_call_intents").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM lab_call_observations").fetchone() == (0,)

    fresh_recorded = RecordedEvidenceCollector((_fixture(),))
    reopened = _configure(lab_root, production_root, fresh_recorded)
    assert isinstance(reopened, Replay)
    refused = reopened(replay_request())

    assert refused.disposition is ReplayDisposition.REFUSED
    assert refused.refusal_reason is ReplayRefusalReason.INDETERMINATE_MODEL_EFFECT
    assert refused.authority_scope == "research_lab_non_production"
    assert refused.non_production is True
    assert recorded.unique_effect_count == 1
    assert fresh_recorded.unique_effect_count == 0
    with sqlite3.connect(lab_root / "research-lab.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM lab_call_observations").fetchone() == (0,)


def test_changed_request_conflicts_and_append_only_history_refuses_mutation(
    tmp_path: Path,
) -> None:
    lab_root = tmp_path / "lab"
    recorded = RecordedEvidenceCollector((_fixture(),))
    replay = _configure(lab_root, tmp_path / "production", recorded)
    assert isinstance(replay, Replay)
    completed = replay(replay_request())
    assert completed.disposition is ReplayDisposition.COMPLETED

    conflict = replay(
        replay_request(prompt_content="Changed prompt under the same request identity.")
    )

    assert conflict.disposition is ReplayDisposition.CONFLICTED
    assert conflict.refusal_reason is ReplayRefusalReason.IDENTITY_CONFLICT
    assert conflict.authority_scope == "research_lab_non_production"
    assert conflict.non_production is True
    assert recorded.unique_effect_count == 1
    database = lab_root / "research-lab.sqlite3"
    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM lab_call_intents")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE lab_call_observations SET raw_response = NULL")


def test_composition_refuses_production_roots_and_namespace_mismatch_before_model(
    tmp_path: Path,
) -> None:
    production_root = tmp_path / "production"
    production_root.mkdir(mode=0o700)
    model = RecordedEvidenceCollector((_fixture(),))

    overlap = _configure(production_root, production_root, model)

    assert overlap == LabCompositionRefusal(LabCompositionRefusalCode.INVALID_STATE_ROOT)
    assert tuple(production_root.iterdir()) == ()
    lab_root = tmp_path / "lab"
    replay = _configure(lab_root, production_root, model)
    assert isinstance(replay, Replay)
    wrong_namespace = replay(replay_request(namespace="lab.synthetic.other"))
    assert wrong_namespace.disposition is ReplayDisposition.REFUSED
    assert wrong_namespace.refusal_reason is ReplayRefusalReason.NAMESPACE_MISMATCH
    assert model.unique_effect_count == 0
    with sqlite3.connect(lab_root / "research-lab.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM lab_call_intents").fetchone() == (0,)


def test_composition_refuses_incomplete_or_unsafe_isolation_before_state_creation(
    tmp_path: Path,
) -> None:
    model = RecordedEvidenceCollector((_fixture(),))
    lab_root = tmp_path / "lab"
    broad_root = tmp_path / "broad"
    broad_root.mkdir(mode=0o755)

    invalid_namespace = configure_replay(
        namespace="Production Lab",
        lab_state_root=str(lab_root),
        production_state_roots=(tmp_path / "production",),
        repository_root=REPOSITORY_ROOT,
        model=model,
        clock=FixedClock(),
    )
    missing_production_roots = configure_replay(
        namespace=NAMESPACE,
        lab_state_root=str(lab_root),
        production_state_roots=(),
        repository_root=REPOSITORY_ROOT,
        model=model,
        clock=FixedClock(),
    )
    unsafe_root = _configure(broad_root, tmp_path / "production", model)
    relative_production_root = configure_replay(
        namespace=NAMESPACE,
        lab_state_root=str(lab_root),
        production_state_roots=(Path("relative-production"),),
        repository_root=REPOSITORY_ROOT,
        model=model,
        clock=FixedClock(),
    )

    assert invalid_namespace == LabCompositionRefusal(LabCompositionRefusalCode.INVALID_NAMESPACE)
    assert missing_production_roots == LabCompositionRefusal(
        LabCompositionRefusalCode.PRODUCTION_ROOTS_REQUIRED
    )
    assert unsafe_root == LabCompositionRefusal(LabCompositionRefusalCode.INVALID_STATE_ROOT)
    assert relative_production_root == LabCompositionRefusal(
        LabCompositionRefusalCode.INVALID_STATE_ROOT
    )
    assert not lab_root.exists()
    assert model.unique_effect_count == 0


def test_namespace_schema_and_content_corruption_fail_closed_before_another_effect(
    tmp_path: Path,
) -> None:
    lab_root = tmp_path / "lab"
    model = RecordedEvidenceCollector((_fixture(),))
    replay = _configure(lab_root, tmp_path / "production", model)
    assert isinstance(replay, Replay)
    assert replay(replay_request()).disposition is ReplayDisposition.COMPLETED
    database = lab_root / "research-lab.sqlite3"

    with pytest.raises(LabPersistenceError, match="history is invalid"):
        SQLiteLabCallLedger(database, "lab.synthetic.other")

    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER lab_call_intents_no_update")
        connection.execute("UPDATE lab_call_intents SET intent_json = '{}' ")
        connection.execute(
            "CREATE TRIGGER lab_call_intents_no_update "
            "BEFORE UPDATE ON lab_call_intents "
            "BEGIN SELECT RAISE(ABORT, 'Lab intents are append-only'); END"
        )

    with pytest.raises(LabPersistenceError, match="history is invalid"):
        replay(replay_request())
    assert model.unique_effect_count == 1

    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER lab_call_observations_no_update")

    with pytest.raises(LabPersistenceError, match="history is invalid"):
        SQLiteLabCallLedger(database, NAMESPACE)


def test_live_schema_corruption_stops_replay_before_the_first_model_effect(
    tmp_path: Path,
) -> None:
    lab_root = tmp_path / "lab"
    model = RecordedEvidenceCollector((_fixture(),))
    replay = _configure(lab_root, tmp_path / "production", model)
    assert isinstance(replay, Replay)
    database = lab_root / "research-lab.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER lab_call_intents_no_update")

    with pytest.raises(LabPersistenceError, match="history is invalid"):
        replay(replay_request())

    assert model.unique_effect_count == 0


def test_oversized_adapter_output_persists_only_its_bounded_identity(tmp_path: Path) -> None:
    lab_root = tmp_path / "lab"
    oversized = b"x" * 200_001
    model = RecordedEvidenceCollector(
        (
            RecordedModelFixture(
                ModelCallDisposition.TIMED_OUT,
                oversized,
                "codex-subscription/test-model",
                timing_disposition=ModelTimingDisposition.TIMED_OUT,
            ),
        )
    )
    replay = _configure(lab_root, tmp_path / "production", model)
    assert isinstance(replay, Replay)

    receipt = replay(replay_request())

    assert receipt.disposition is ReplayDisposition.REFUSED
    assert receipt.refusal_reason is ReplayRefusalReason.OVERSIZED_OUTPUT
    assert receipt.raw_response_hash == hashlib.sha256(oversized).hexdigest()
    with sqlite3.connect(lab_root / "research-lab.sqlite3") as connection:
        row = connection.execute(
            "SELECT observation_json, raw_response FROM lab_call_observations"
        ).fetchone()
    assert row is not None
    assert isinstance(row[0], str)
    payload = json.loads(row[0])
    assert isinstance(payload, dict)
    assert payload["raw_response_retained"] is False
    assert row[1] is None


def test_ledger_revalidates_a_forged_terminal_observation_before_append(
    tmp_path: Path,
) -> None:
    recorded = RecordedEvidenceCollector((_fixture(),))
    configured = _configure(
        tmp_path / "lab",
        tmp_path / "production",
        InterruptAfterModelEffect(recorded),
    )
    assert isinstance(configured, Replay)
    capturing = CapturingLabLedger(configured.ledger)
    replay = Replay(NAMESPACE, capturing, configured.model, configured.clock)
    with pytest.raises(SimulatedInterruptionError):
        replay(replay_request())
    assert capturing.intent is not None
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
    valid = LabCallObservation.create(
        call_id=capturing.intent.call_id,
        disposition=LabObservationDisposition.ADAPTER_REFUSED,
        response=response,
    )
    forged = copy(valid)
    object.__setattr__(forged, "disposition", LabObservationDisposition.VALIDATED)

    with pytest.raises(LabPersistenceError, match="could not append"):
        configured.ledger.append_observation(
            capturing.intent,
            forged,
            UtcInstant.from_datetime(FixedClock().now()),
        )
    assert recorded.unique_effect_count == 1
    with sqlite3.connect(tmp_path / "lab" / "research-lab.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM lab_call_observations").fetchone() == (0,)


@pytest.mark.parametrize(
    "corruption",
    ["intent_record_kind", "observation_record_kind", "observation_noncanonical"],
)
def test_every_existing_row_is_reconstructed_before_reopen_or_another_effect(
    tmp_path: Path,
    corruption: str,
) -> None:
    lab_root = tmp_path / "lab"
    model = RecordedEvidenceCollector((_fixture(), _fixture()))
    replay = _configure(lab_root, tmp_path / "production", model)
    assert isinstance(replay, Replay)
    assert replay(replay_request()).disposition is ReplayDisposition.COMPLETED
    database = lab_root / "research-lab.sqlite3"

    with sqlite3.connect(database) as connection:
        if corruption == "intent_record_kind":
            connection.execute("DROP TRIGGER lab_call_intents_no_update")
            value = connection.execute("SELECT intent_json FROM lab_call_intents").fetchone()
            assert value is not None
            assert isinstance(value[0], str)
            payload = json.loads(value[0])
            assert isinstance(payload, dict)
            payload["record_kind"] = "wrong_kind"
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            connection.execute(
                "UPDATE lab_call_intents SET intent_json = ?, intent_hash = ?",
                (encoded, hashlib.sha256(encoded.encode()).hexdigest()),
            )
            connection.execute(
                "CREATE TRIGGER lab_call_intents_no_update "
                "BEFORE UPDATE ON lab_call_intents "
                "BEGIN SELECT RAISE(ABORT, 'Lab intents are append-only'); END"
            )
        else:
            connection.execute("DROP TRIGGER lab_call_observations_no_update")
            value = connection.execute(
                "SELECT observation_json FROM lab_call_observations"
            ).fetchone()
            assert value is not None
            assert isinstance(value[0], str)
            payload = json.loads(value[0])
            assert isinstance(payload, dict)
            if corruption == "observation_record_kind":
                payload["record_kind"] = "wrong_kind"
                encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            else:
                encoded = json.dumps(payload, indent=2, sort_keys=True)
            connection.execute(
                "UPDATE lab_call_observations SET observation_json = ?, observation_hash = ?",
                (encoded, hashlib.sha256(encoded.encode()).hexdigest()),
            )
            connection.execute(
                "CREATE TRIGGER lab_call_observations_no_update "
                "BEFORE UPDATE ON lab_call_observations "
                "BEGIN SELECT RAISE(ABORT, 'Lab observations are append-only'); END"
            )

    with pytest.raises(LabPersistenceError, match="history is invalid"):
        replay(replay_request(request_id="replay-aapl-2026-08-24-next"))
    with pytest.raises(LabPersistenceError, match="history is invalid"):
        SQLiteLabCallLedger(database, NAMESPACE)
    assert model.unique_effect_count == 1
