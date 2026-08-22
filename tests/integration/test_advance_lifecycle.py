from __future__ import annotations

import sqlite3
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

from agentic_investment_os.adapters.sqlite_lifecycle import (
    RuntimeRootRefusal,
    SQLiteLifecycleLedger,
    prepare_runtime_database,
)
from agentic_investment_os.application.lifecycle import Advance
from agentic_investment_os.domain.lifecycle import (
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRecovery,
    AdvanceRequest,
    CheckpointResult,
    CheckpointWrite,
    IdempotencyKey,
    LifecyclePersistenceError,
    LifecyclePhase,
    LifecycleProgress,
    PinnedRunIdentity,
    StartResult,
)
from agentic_investment_os.entrypoints.configuration import (
    ConfigurationRefusal,
    ConfigurationRefusalCode,
    ConfigurationSource,
)
from agentic_investment_os.entrypoints.lifecycle import SystemClock, configure_advance

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SHA256_HEX_LENGTH = 64
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PINNED_EVENT_COUNT = 3
RECEIPT_FIELD_COUNT = 4
_T = TypeVar("_T")
CORRUPTIONS = {
    "invalid_request": (
        ("UPDATE lifecycle_events SET mode = 'invalid'",),
        "invalid request values in lifecycle ledger",
    ),
    "too_many_events": (
        (
            """
            INSERT INTO lifecycle_events
            SELECT stream_id, 3, idempotency_key, session, mode,
                   configuration_version, configuration_hash, run_id,
                   event_kind, completed_phase, recorded_at
            FROM lifecycle_events WHERE sequence = 2
            """,
        ),
        "lifecycle stream contains unsupported later phases",
    ),
    "sequence_gap": (
        (
            "DELETE FROM lifecycle_events WHERE sequence = 2",
            "UPDATE lifecycle_events SET sequence = 2 WHERE sequence = 1",
        ),
        "lifecycle stream sequence is not contiguous",
    ),
    "changed_invariant": (
        ("UPDATE lifecycle_events SET session = '2026-08-22' WHERE sequence = 1",),
        "lifecycle stream changed pinned request facts",
    ),
    "wrong_checkpoint": (
        ("UPDATE lifecycle_events SET event_kind = 'advance_requested' WHERE sequence = 1",),
        "lifecycle stream checkpoint order is invalid",
    ),
    "non_text_phase": (
        ("UPDATE lifecycle_events SET completed_phase = 3 WHERE sequence = 1",),
        "invalid completed_phase in lifecycle ledger",
    ),
    "non_integer_version": (
        ("UPDATE lifecycle_events SET configuration_version = 'invalid'",),
        "invalid configuration_version in lifecycle ledger",
    ),
    "non_integer_sequence": (
        ("UPDATE lifecycle_events SET sequence = 'invalid'",),
        "invalid sequence in lifecycle ledger",
    ),
    "non_text_hash": (
        ("UPDATE lifecycle_events SET configuration_hash = 3",),
        "invalid configuration_hash in lifecycle ledger",
    ),
    "invalid_hash": (
        ("UPDATE lifecycle_events SET configuration_hash = 'invalid'",),
        "invalid configuration_hash in lifecycle ledger",
    ),
    "invalid_run_id": (
        ("UPDATE lifecycle_events SET run_id = 'invalid'",),
        "invalid run_id in lifecycle ledger",
    ),
    "forged_run_id": (
        (
            """UPDATE lifecycle_events SET run_id =
            'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'""",
        ),
        "lifecycle stream derived identity is invalid",
    ),
    "forged_stream_id": (
        (
            """UPDATE lifecycle_events SET stream_id =
            'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'""",
        ),
        "lifecycle stream derived identity is invalid",
    ),
    "unsupported_version": (
        ("UPDATE lifecycle_events SET configuration_version = 2",),
        "unsupported configuration_version in lifecycle ledger",
    ),
    "empty_stream": (
        ("UPDATE lifecycle_events SET stream_id = ''",),
        "invalid stream_id in lifecycle ledger",
    ),
    "non_text_stream": (
        ("UPDATE lifecycle_events SET stream_id = 3",),
        "invalid stream_id in lifecycle ledger",
    ),
    "non_text_event_kind": (
        ("UPDATE lifecycle_events SET event_kind = 3 WHERE sequence = 1",),
        "invalid event_kind in lifecycle ledger",
    ),
    "non_text_timestamp": (
        ("UPDATE lifecycle_events SET recorded_at = 3",),
        "invalid recorded_at in lifecycle ledger",
    ),
    "invalid_timestamp": (
        ("UPDATE lifecycle_events SET recorded_at = 'invalid'",),
        "invalid recorded_at in lifecycle ledger",
    ),
    "naive_timestamp": (
        ("UPDATE lifecycle_events SET recorded_at = '2026-08-21T22:00:00'",),
        "recorded_at must be timezone-aware",
    ),
}
ROLLBACK_TRIGGERS = {
    "advance_requested": """
        CREATE TRIGGER fail_advance_requested_before_insert
        BEFORE INSERT ON lifecycle_events
        WHEN NEW.event_kind = 'advance_requested'
        BEGIN SELECT RAISE(ABORT, 'injected advance request failure'); END
    """,
    "phase_completed": """
        CREATE TRIGGER fail_phase_completed_before_insert
        BEFORE INSERT ON lifecycle_events
        WHEN NEW.event_kind = 'phase_completed'
        BEGIN SELECT RAISE(ABORT, 'injected reconciliation failure'); END
    """,
    "run_inputs_pinned": """
        CREATE TRIGGER fail_run_inputs_pinned_before_insert
        BEFORE INSERT ON lifecycle_events
        WHEN NEW.event_kind = 'run_inputs_pinned'
        BEGIN SELECT RAISE(ABORT, 'injected pin failure'); END
    """,
    "advance_refusal": """
        CREATE TRIGGER fail_advance_refusal_before_insert
        BEFORE INSERT ON advance_refusals
        BEGIN SELECT RAISE(ABORT, 'injected refusal failure'); END
    """,
    "advance_conflict": """
        CREATE TRIGGER fail_advance_conflict_before_insert
        BEFORE INSERT ON advance_conflicts
        BEGIN SELECT RAISE(ABORT, 'injected conflict failure'); END
    """,
}


@dataclass(frozen=True)
class FixedClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


class SimulatedInterruptionError(RuntimeError):
    """Stop one Advance call at an exact ledger write boundary."""


@dataclass(frozen=True)
class InterruptingLedger:
    delegate: SQLiteLifecycleLedger
    operation: str
    timing: str

    def load_by_idempotency_key(
        self, key: IdempotencyKey
    ) -> LifecycleProgress | AdvanceReceipt | None:
        return self.delegate.load_by_idempotency_key(key)

    def start(
        self,
        request: AdvanceRequest,
        identity: PinnedRunIdentity,
        recorded_at: datetime,
    ) -> StartResult:
        return self._around(
            "start",
            lambda: self.delegate.start(request, identity, recorded_at),
        )

    def complete_reconciliation(
        self,
        key: IdempotencyKey,
        recorded_at: datetime,
    ) -> CheckpointResult | AdvanceReceipt:
        return self._around(
            "reconcile",
            lambda: self.delegate.complete_reconciliation(key, recorded_at),
        )

    def pin_run_inputs(
        self, key: IdempotencyKey, recorded_at: datetime
    ) -> CheckpointResult | AdvanceReceipt:
        return self._around(
            "pin",
            lambda: self.delegate.pin_run_inputs(key, recorded_at),
        )

    def record_refusal(
        self,
        key: IdempotencyKey | None,
        reason_code: AdvanceFailureReason,
        recorded_at: datetime,
    ) -> AdvanceReceipt:
        return self._around(
            "refuse",
            lambda: self.delegate.record_refusal(key, reason_code, recorded_at),
        )

    def _around(self, operation: str, action: Callable[[], _T]) -> _T:
        if self.operation == operation and self.timing == "before":
            raise SimulatedInterruptionError
        result = action()
        if self.operation == operation and self.timing == "after":
            raise SimulatedInterruptionError
        return result


@dataclass(frozen=True)
class RacingStartLedger:
    delegate: SQLiteLifecycleLedger
    winner_phase: LifecyclePhase | None

    def load_by_idempotency_key(
        self, key: IdempotencyKey
    ) -> LifecycleProgress | AdvanceReceipt | None:
        return self.delegate.load_by_idempotency_key(key)

    def start(
        self,
        request: AdvanceRequest,
        identity: PinnedRunIdentity,
        recorded_at: datetime,
    ) -> StartResult:
        winner = self.delegate.start(request, identity, recorded_at)
        assert isinstance(winner, CheckpointResult)
        if self.winner_phase is LifecyclePhase.RECONCILE_PRIOR_STATE:
            reconciled = self.delegate.complete_reconciliation(request.idempotency_key, recorded_at)
            assert isinstance(reconciled, CheckpointResult)
        return self.delegate.start(request, identity, recorded_at)

    def complete_reconciliation(
        self,
        key: IdempotencyKey,
        recorded_at: datetime,
    ) -> CheckpointResult | AdvanceReceipt:
        return self.delegate.complete_reconciliation(key, recorded_at)

    def pin_run_inputs(
        self, key: IdempotencyKey, recorded_at: datetime
    ) -> CheckpointResult | AdvanceReceipt:
        return self.delegate.pin_run_inputs(key, recorded_at)

    def record_refusal(
        self,
        key: IdempotencyKey | None,
        reason_code: AdvanceFailureReason,
        recorded_at: datetime,
    ) -> AdvanceReceipt:
        return self.delegate.record_refusal(key, reason_code, recorded_at)


def _configure(state_root: Path) -> Advance:
    configured = configure_advance(
        (
            ConfigurationSource(
                "test",
                {"schema_version": 1, "state_root": str(state_root)},
            ),
        ),
        repository_root=REPOSITORY_ROOT,
        clock=FixedClock(datetime(2026, 8, 21, 22, 0, tzinfo=UTC)),
    )
    assert isinstance(configured, Advance)
    return configured


def _events(database: Path) -> list[tuple[str, str | None]]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT event_kind, completed_phase FROM lifecycle_events ORDER BY sequence"
        ).fetchall()
    return [(str(kind), None if phase is None else str(phase)) for kind, phase in rows]


def _advance_in_fresh_process(
    state_root: Path,
    idempotency_key: str,
    *,
    session: str = "2026-08-21",
    mode: str = "champion",
) -> tuple[str, str, str, str]:
    completed = subprocess.run(  # noqa: S603
        (
            sys.executable,
            "-m",
            "tests.integration._advance_process",
            str(state_root),
            session,
            mode,
            idempotency_key,
        ),
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    fields = completed.stdout.split("\t")
    assert len(fields) == RECEIPT_FIELD_COUNT
    return fields[0], fields[1], fields[2], fields[3]


def test_advance_pins_one_stream_and_reconstructs_its_receipt_after_reopen(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    first_capability = _configure(state_root)

    first = first_capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="advance-2026-08-21",
    )
    reopened = _configure(state_root)
    replay = reopened(
        session="2026-08-21",
        mode="champion",
        idempotency_key="advance-2026-08-21",
    )

    assert first.recovery is AdvanceRecovery.FRESH
    assert replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED
    assert first.disposition is replay.disposition
    assert first.completed_phase is replay.completed_phase
    assert first.pinned_run_identity == replay.pinned_run_identity
    assert first.disposition is AdvanceDisposition.ADVANCED
    assert first.completed_phase is LifecyclePhase.PIN_RUN_INPUTS
    assert first.pinned_run_identity is not None
    assert len(first.pinned_run_identity.run_id) == SHA256_HEX_LENGTH
    assert len(first.pinned_run_identity.configuration_hash) == SHA256_HEX_LENGTH
    assert first.failure_reason is None
    assert _events(state_root / "lifecycle.sqlite3") == [
        ("advance_requested", None),
        ("phase_completed", "ReconcilePriorState"),
        ("run_inputs_pinned", "PinRunInputs"),
    ]
    assert stat.S_IMODE(state_root.stat().st_mode) == PRIVATE_DIRECTORY_MODE
    assert stat.S_IMODE((state_root / "lifecycle.sqlite3").stat().st_mode) == PRIVATE_FILE_MODE


@pytest.mark.parametrize(
    ("operation", "timing", "committed_events", "expected_recovery"),
    [
        ("start", "before", 0, AdvanceRecovery.FRESH),
        ("start", "after", 1, AdvanceRecovery.RESUMED),
        ("reconcile", "before", 1, AdvanceRecovery.RESUMED),
        ("reconcile", "after", 2, AdvanceRecovery.RESUMED),
        ("pin", "before", 2, AdvanceRecovery.RESUMED),
        ("pin", "after", 3, AdvanceRecovery.PREVIOUSLY_COMPLETED),
    ],
)
def test_fresh_process_resumes_at_every_stage_1_write_boundary(
    tmp_path: Path,
    operation: str,
    timing: str,
    committed_events: int,
    expected_recovery: AdvanceRecovery,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    interrupted = Advance(
        ledger=InterruptingLedger(ledger, operation, timing),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        clock=capability.clock,
    )

    with pytest.raises(SimulatedInterruptionError):
        interrupted(
            session="2026-08-21",
            mode="champion",
            idempotency_key="write-boundary-recovery",
        )

    database = state_root / "lifecycle.sqlite3"
    assert len(_events(database)) == committed_events
    disposition, phase, recovery, run_id = _advance_in_fresh_process(
        state_root, "write-boundary-recovery"
    )

    assert disposition == AdvanceDisposition.ADVANCED.value
    assert phase == LifecyclePhase.PIN_RUN_INPUTS.value
    assert recovery == expected_recovery.value
    assert len(run_id) == SHA256_HEX_LENGTH
    assert len(_events(database)) == PINNED_EVENT_COUNT


@pytest.mark.parametrize(
    "refusal_case",
    [
        ("invalid", "keyed-refusal", AdvanceFailureReason.INVALID_SESSION),
        ("2026-08-21", "not valid", AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY),
    ],
)
@pytest.mark.parametrize(("timing", "committed_refusals"), [("before", 0), ("after", 1)])
def test_fresh_process_recovers_at_each_refusal_write_boundary(
    tmp_path: Path,
    refusal_case: tuple[str, str, AdvanceFailureReason],
    timing: str,
    committed_refusals: int,
) -> None:
    session, idempotency_key, reason = refusal_case
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    interrupted = Advance(
        ledger=InterruptingLedger(ledger, "refuse", timing),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        clock=capability.clock,
    )

    with pytest.raises(SimulatedInterruptionError):
        interrupted(
            session=session,
            mode="champion",
            idempotency_key=idempotency_key,
        )

    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone() == (
            committed_refusals,
        )
    disposition, phase, recovery, failure_reason = _advance_in_fresh_process(
        state_root,
        idempotency_key,
        session=session,
    )

    assert disposition == AdvanceDisposition.FAILED_CLOSED.value
    assert phase == ""
    assert recovery == ""
    assert failure_reason == reason.value
    assert _events(database) == []
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT reason_code FROM advance_refusals").fetchall() == [
            (reason.value,)
        ]


@pytest.mark.parametrize(("timing", "committed_conflicts"), [("before", 0), ("after", 1)])
def test_fresh_process_recovers_at_each_completed_conflict_write_boundary(
    tmp_path: Path,
    timing: str,
    committed_conflicts: int,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="interrupted-conflict",
    )
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    interrupted = Advance(
        ledger=InterruptingLedger(ledger, "start", timing),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        clock=capability.clock,
    )

    with pytest.raises(SimulatedInterruptionError):
        interrupted(
            session="2026-08-22",
            mode="champion",
            idempotency_key="interrupted-conflict",
        )

    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM advance_conflicts").fetchone() == (
            committed_conflicts,
        )
    disposition, phase, recovery, failure_reason = _advance_in_fresh_process(
        state_root,
        "interrupted-conflict",
        session="2026-08-22",
    )

    assert disposition == AdvanceDisposition.FAILED_CLOSED.value
    assert phase == ""
    assert recovery == ""
    assert failure_reason == AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT.value
    assert len(_events(database)) == PINNED_EVENT_COUNT
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT reason_code FROM advance_conflicts").fetchall() == [
            (AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT.value,)
        ]


@pytest.mark.parametrize(
    "winner_phase",
    [None, LifecyclePhase.RECONCILE_PRIOR_STATE],
)
def test_duplicate_delivery_reports_progress_committed_by_the_winner_as_resumed(
    tmp_path: Path,
    winner_phase: LifecyclePhase | None,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    raced = Advance(
        ledger=RacingStartLedger(ledger, winner_phase),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        clock=capability.clock,
    )

    receipt = raced(
        session="2026-08-21",
        mode="champion",
        idempotency_key="concurrent-duplicate",
    )

    assert receipt.disposition is AdvanceDisposition.ADVANCED
    assert receipt.recovery is AdvanceRecovery.RESUMED
    assert len(_events(state_root / "lifecycle.sqlite3")) == PINNED_EVENT_COUNT


def test_conflicting_valid_idempotency_reuse_fails_without_shadowing_completed_work(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    completed = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="conflicting-valid-reuse",
    )

    conflict = capability(
        session="2026-08-22",
        mode="champion",
        idempotency_key="conflicting-valid-reuse",
    )
    conflict_replay = _configure(state_root)(
        session="2026-08-22",
        mode="champion",
        idempotency_key="conflicting-valid-reuse",
    )
    original_replay = _configure(state_root)(
        session="2026-08-21",
        mode="champion",
        idempotency_key="conflicting-valid-reuse",
    )

    expected_conflict = AdvanceReceipt.failed_closed(AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT)
    assert conflict == expected_conflict
    assert conflict_replay == expected_conflict
    assert original_replay.disposition is completed.disposition
    assert original_replay.pinned_run_identity == completed.pinned_run_identity
    assert original_replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED
    assert len(_events(state_root / "lifecycle.sqlite3")) == PINNED_EVENT_COUNT
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT reason_code FROM advance_conflicts WHERE idempotency_key = ?",
            ("conflicting-valid-reuse",),
        ).fetchall() == [("idempotency_key_conflict",)]


def test_advance_rejects_a_second_initial_stream_for_the_same_session(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    original = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="first-key",
    )

    conflict = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="second-key",
    )
    original_replay = _configure(state_root)(
        session="2026-08-21",
        mode="champion",
        idempotency_key="first-key",
    )
    conflict_replay = _configure(state_root)(
        session="2026-08-21",
        mode="champion",
        idempotency_key="second-key",
    )

    assert conflict.disposition is AdvanceDisposition.FAILED_CLOSED
    assert conflict.completed_phase is None
    assert conflict.pinned_run_identity is None
    assert conflict.failure_reason == "session_stream_conflict"
    assert conflict_replay == conflict
    assert original_replay.disposition is original.disposition
    assert original_replay.pinned_run_identity == original.pinned_run_identity
    assert original_replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED
    assert (
        sum(kind == "advance_requested" for kind, _ in _events(state_root / "lifecycle.sqlite3"))
        == 1
    )


def test_invalid_required_input_has_a_durable_fail_closed_receipt(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)

    refused = capability(
        session="not-a-session",
        mode="champion",
        idempotency_key="invalid-request",
    )
    replay = _configure(state_root)(
        session="not-a-session",
        mode="champion",
        idempotency_key="invalid-request",
    )

    assert refused == replay
    assert refused == AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_SESSION)
    assert _events(state_root / "lifecycle.sqlite3") == []
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute("SELECT reason_code FROM advance_refusals").fetchall() == [
            ("invalid_session",)
        ]

    invalid_key = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="not valid",
    )
    invalid_key_replay = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="not valid",
    )
    another_invalid_key = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="also not valid",
    )
    assert invalid_key.failure_reason == "invalid_idempotency_key"
    assert invalid_key_replay == invalid_key
    assert another_invalid_key == invalid_key
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM advance_refusals
            WHERE idempotency_key IS NULL AND reason_code = 'invalid_idempotency_key'
            """
        ).fetchone() == (1,)


@pytest.mark.parametrize(
    ("conflicting_session", "conflicting_mode"),
    [("invalid", "champion"), ("2026-08-21", "research-lab")],
)
def test_invalid_idempotency_reuse_fails_without_shadowing_completed_work(
    tmp_path: Path,
    conflicting_session: str,
    conflicting_mode: str,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    completed = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="completed-key",
    )

    malformed_reuse = capability(
        session=conflicting_session,
        mode=conflicting_mode,
        idempotency_key="completed-key",
    )
    valid_replay = _configure(state_root)(
        session="2026-08-21",
        mode="champion",
        idempotency_key="completed-key",
    )

    assert malformed_reuse == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
    )
    assert valid_replay.disposition is completed.disposition
    assert valid_replay.pinned_run_identity == completed.pinned_run_identity
    assert valid_replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM advance_refusals WHERE idempotency_key = 'completed-key'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT reason_code FROM advance_conflicts WHERE idempotency_key = 'completed-key'"
        ).fetchall() == [("idempotency_key_conflict",)]


def test_conflicting_pinned_identity_fails_without_rewriting_completed_work(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    completed = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="pinned-identity-conflict",
    )
    database = state_root / "lifecycle.sqlite3"
    conflicting_capability = Advance(
        ledger=SQLiteLifecycleLedger(database),
        configuration_version=capability.configuration_version,
        configuration_hash="f" * SHA256_HEX_LENGTH,
        clock=capability.clock,
    )

    conflict = conflicting_capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="pinned-identity-conflict",
    )
    original_replay = _configure(state_root)(
        session="2026-08-21",
        mode="champion",
        idempotency_key="pinned-identity-conflict",
    )

    assert conflict == AdvanceReceipt.failed_closed(AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT)
    assert original_replay.disposition is completed.disposition
    assert original_replay.pinned_run_identity == completed.pinned_run_identity
    assert original_replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED
    assert len(_events(database)) == PINNED_EVENT_COUNT


def test_terminal_conflict_refusal_prevents_partial_stream_resumption(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    parsed = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key="partial-conflict",
    )
    assert isinstance(parsed, AdvanceRequest)
    identity = PinnedRunIdentity.create(
        parsed,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    now = datetime(2026, 8, 21, 22, 0, tzinfo=UTC)
    ledger.start(parsed, identity, now)

    refused = capability(
        session="2026-08-22",
        mode="champion",
        idempotency_key="partial-conflict",
    )
    replay = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="partial-conflict",
    )

    assert refused == AdvanceReceipt.failed_closed(AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT)
    assert replay == refused
    assert _events(state_root / "lifecycle.sqlite3") == [("advance_requested", None)]


@pytest.mark.parametrize(
    ("event_kind", "committed_events", "expected_recovery"),
    [
        ("advance_requested", 0, AdvanceRecovery.FRESH),
        ("phase_completed", 1, AdvanceRecovery.RESUMED),
        ("run_inputs_pinned", 2, AdvanceRecovery.RESUMED),
    ],
)
def test_advance_resumes_after_each_checkpoint_transaction_rolls_back(
    tmp_path: Path,
    event_kind: str,
    committed_events: int,
    expected_recovery: AdvanceRecovery,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(ROLLBACK_TRIGGERS[event_kind])

    with pytest.raises(LifecyclePersistenceError, match="SQLite lifecycle checkpoint failed"):
        capability(
            session="2026-08-21",
            mode="champion",
            idempotency_key="rollback-key",
        )

    assert len(_events(database)) == committed_events
    with sqlite3.connect(database) as connection:
        trigger_name = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'fail_%'"
        ).fetchone()
        assert trigger_name is not None
        known_drop_statements = {
            "fail_advance_requested_before_insert": (
                "DROP TRIGGER fail_advance_requested_before_insert"
            ),
            "fail_phase_completed_before_insert": "DROP TRIGGER fail_phase_completed_before_insert",
            "fail_run_inputs_pinned_before_insert": (
                "DROP TRIGGER fail_run_inputs_pinned_before_insert"
            ),
        }
        connection.execute(known_drop_statements[str(trigger_name[0])])

    disposition, phase, recovery, run_id = _advance_in_fresh_process(state_root, "rollback-key")

    assert disposition == AdvanceDisposition.ADVANCED.value
    assert phase == LifecyclePhase.PIN_RUN_INPUTS.value
    assert recovery == expected_recovery.value
    assert len(run_id) == SHA256_HEX_LENGTH
    assert len(_events(database)) == PINNED_EVENT_COUNT


@pytest.mark.parametrize(
    ("session", "idempotency_key", "reason"),
    [
        ("invalid", "rolled-back-refusal", AdvanceFailureReason.INVALID_SESSION),
        ("2026-08-21", "not valid", AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY),
    ],
)
def test_refusal_write_rollback_is_not_observed_as_terminal(
    tmp_path: Path,
    session: str,
    idempotency_key: str,
    reason: AdvanceFailureReason,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(ROLLBACK_TRIGGERS["advance_refusal"])

    with pytest.raises(LifecyclePersistenceError, match="SQLite lifecycle checkpoint failed"):
        capability(
            session=session,
            mode="champion",
            idempotency_key=idempotency_key,
        )

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone() == (0,)
        connection.execute("DROP TRIGGER fail_advance_refusal_before_insert")
    disposition, phase, recovery, failure_reason = _advance_in_fresh_process(
        state_root,
        idempotency_key,
        session=session,
    )

    assert disposition == AdvanceDisposition.FAILED_CLOSED.value
    assert phase == ""
    assert recovery == ""
    assert failure_reason == reason.value
    assert _events(database) == []
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT reason_code FROM advance_refusals").fetchall() == [
            (reason.value,)
        ]


def test_completed_conflict_write_rollback_is_not_observed_as_terminal(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="rolled-back-conflict",
    )
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(ROLLBACK_TRIGGERS["advance_conflict"])

    with pytest.raises(LifecyclePersistenceError, match="SQLite lifecycle checkpoint failed"):
        capability(
            session="2026-08-22",
            mode="champion",
            idempotency_key="rolled-back-conflict",
        )

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM advance_conflicts").fetchone() == (0,)
        connection.execute("DROP TRIGGER fail_advance_conflict_before_insert")
    disposition, phase, recovery, failure_reason = _advance_in_fresh_process(
        state_root,
        "rolled-back-conflict",
        session="2026-08-22",
    )

    assert disposition == AdvanceDisposition.FAILED_CLOSED.value
    assert phase == ""
    assert recovery == ""
    assert failure_reason == AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT.value
    assert len(_events(database)) == PINNED_EVENT_COUNT
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT reason_code FROM advance_conflicts").fetchall() == [
            (AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT.value,)
        ]


def test_runtime_state_root_cannot_point_into_source_directories(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    source = repository / "src"
    source.mkdir(parents=True)
    (repository / ".gitignore").write_text("/var/\n", encoding="utf-8")

    configured = configure_advance(
        (
            ConfigurationSource(
                "test",
                {"schema_version": 1, "state_root": str(source / "runtime")},
            ),
        ),
        repository_root=repository,
        clock=FixedClock(datetime(2026, 8, 21, 22, 0, tzinfo=UTC)),
    )

    assert configured == ConfigurationRefusal(
        code=ConfigurationRefusalCode.INVALID_STATE_ROOT,
        fields=("state_root",),
    )
    assert not (source / "runtime").exists()


def test_runtime_state_storage_refuses_links_public_modes_and_unsafe_shapes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target, target_is_directory=True)
    assert isinstance(prepare_runtime_database(root_link), RuntimeRootRefusal)

    root_file = tmp_path / "root-file"
    root_file.write_text("not a directory", encoding="utf-8")

    public_root = tmp_path / "public-root"
    public_root.mkdir(mode=0o755)

    database_link_root = tmp_path / "database-link-root"
    database_link_root.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    database_target = tmp_path / "database-target"
    database_target.write_text("", encoding="utf-8")
    (database_link_root / "lifecycle.sqlite3").symlink_to(database_target)

    database_directory_root = tmp_path / "database-directory-root"
    database_directory_root.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    (database_directory_root / "lifecycle.sqlite3").mkdir()

    public_database_root = tmp_path / "public-database-root"
    public_database_root.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    public_database = public_database_root / "lifecycle.sqlite3"
    public_database.write_text("", encoding="utf-8")
    public_database.chmod(0o644)

    unsafe_roots = (
        root_link,
        root_file,
        public_root,
        database_link_root,
        database_directory_root,
        public_database_root,
        tmp_path / "missing-parent" / "runtime",
    )
    for state_root in unsafe_roots:
        configured = configure_advance(
            (
                ConfigurationSource(
                    "test",
                    {"schema_version": 1, "state_root": str(state_root)},
                ),
            ),
            repository_root=REPOSITORY_ROOT,
            clock=FixedClock(datetime(2026, 8, 21, 22, 0, tzinfo=UTC)),
        )
        assert configured == ConfigurationRefusal(
            code=ConfigurationRefusalCode.INVALID_STATE_ROOT,
            fields=("state_root",),
        ), state_root


def test_default_clock_is_aware() -> None:
    instant = SystemClock().now()

    assert instant.tzinfo is UTC


def test_lifecycle_ledger_operations_are_individually_idempotent(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    parsed = AdvanceRequest.parse(
        session="2026-08-24",
        mode="champion",
        idempotency_key="adapter-idempotency",
    )
    assert isinstance(parsed, AdvanceRequest)
    identity = PinnedRunIdentity.create(
        parsed,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    now = datetime(2026, 8, 22, 22, 0, tzinfo=UTC)

    started = ledger.start(parsed, identity, now)
    assert isinstance(started, CheckpointResult)
    assert started.write is CheckpointWrite.APPENDED
    partial_conflict = ledger.record_refusal(
        parsed.idempotency_key, AdvanceFailureReason.INVALID_SESSION, now
    )
    assert partial_conflict.failure_reason == "idempotency_key_conflict"
    assert ledger.start(parsed, identity, now) == partial_conflict
    assert (
        ledger.record_refusal(parsed.idempotency_key, AdvanceFailureReason.INVALID_SESSION, now)
        == partial_conflict
    )
    assert ledger.complete_reconciliation(parsed.idempotency_key, now) == partial_conflict
    assert ledger.pin_run_inputs(parsed.idempotency_key, now) == partial_conflict
    assert ledger.load_by_idempotency_key(parsed.idempotency_key) == partial_conflict

    missing = IdempotencyKey("missing-stream")
    assert ledger.load_by_idempotency_key(missing) is None
    with pytest.raises(
        LifecyclePersistenceError, match="required lifecycle stream is missing or terminal"
    ):
        ledger.complete_reconciliation(missing, now)

    second = AdvanceRequest.parse(
        session="2026-08-25",
        mode="champion",
        idempotency_key="pin-too-soon",
    )
    assert isinstance(second, AdvanceRequest)
    second_identity = PinnedRunIdentity.create(
        second,
        configuration_version=1,
        configuration_hash="b" * SHA256_HEX_LENGTH,
    )
    ledger.start(second, second_identity, now)
    with pytest.raises(
        LifecyclePersistenceError,
        match="PinRunInputs requires the ReconcilePriorState checkpoint",
    ):
        ledger.pin_run_inputs(second.idempotency_key, now)


def test_lifecycle_checkpoints_report_appended_and_observed_writes(tmp_path: Path) -> None:
    capability = _configure(tmp_path / "runtime")
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    request = AdvanceRequest.parse(
        session="2026-08-26",
        mode="champion",
        idempotency_key="adapter-idempotency-resumable",
    )
    assert isinstance(request, AdvanceRequest)
    identity = PinnedRunIdentity.create(
        request,
        configuration_version=1,
        configuration_hash="c" * SHA256_HEX_LENGTH,
    )
    now = datetime(2026, 8, 22, 22, 0, tzinfo=UTC)

    started = ledger.start(request, identity, now)
    assert isinstance(started, CheckpointResult)
    assert started.write is CheckpointWrite.APPENDED
    resumed_start = ledger.start(request, identity, now)
    assert isinstance(resumed_start, CheckpointResult)
    assert resumed_start.progress == started.progress
    assert resumed_start.write is CheckpointWrite.OBSERVED

    reconciled = ledger.complete_reconciliation(request.idempotency_key, now)
    assert isinstance(reconciled, CheckpointResult)
    assert reconciled.write is CheckpointWrite.APPENDED
    resumed_reconciliation = ledger.complete_reconciliation(request.idempotency_key, now)
    assert isinstance(resumed_reconciliation, CheckpointResult)
    assert resumed_reconciliation.progress == reconciled.progress
    assert resumed_reconciliation.write is CheckpointWrite.OBSERVED

    pinned = ledger.pin_run_inputs(request.idempotency_key, now)
    assert isinstance(pinned, CheckpointResult)
    assert pinned.write is CheckpointWrite.APPENDED
    pinned_replay = ledger.pin_run_inputs(request.idempotency_key, now)
    reconciliation_replay = ledger.complete_reconciliation(request.idempotency_key, now)
    assert isinstance(pinned_replay, CheckpointResult)
    assert pinned_replay.progress == pinned.progress
    assert pinned_replay.write is CheckpointWrite.OBSERVED
    assert reconciliation_replay == pinned_replay

    conflicting_identity = PinnedRunIdentity.create(
        request,
        configuration_version=1,
        configuration_hash="d" * SHA256_HEX_LENGTH,
    )
    concurrent_conflict = ledger.start(request, conflicting_identity, now)
    assert concurrent_conflict == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
    )
    assert (
        ledger.record_refusal(request.idempotency_key, AdvanceFailureReason.INVALID_MODE, now)
        == concurrent_conflict
    )
    assert ledger.load_by_idempotency_key(request.idempotency_key) == pinned.progress


def test_naive_clock_cannot_create_a_checkpoint(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    configured = configure_advance(
        (ConfigurationSource("test", {"schema_version": 1, "state_root": str(state_root)}),),
        repository_root=REPOSITORY_ROOT,
        # Intentionally hostile clock value proves the boundary refuses naive time.
        clock=FixedClock(datetime(2026, 8, 21, 22, 0)),  # noqa: DTZ001
    )
    assert isinstance(configured, Advance)

    with pytest.raises(
        LifecyclePersistenceError,
        match="lifecycle clock must return a timezone-aware timestamp",
    ):
        configured(
            session="2026-08-21",
            mode="champion",
            idempotency_key="naive-clock",
        )


def test_append_only_tables_reject_rewrites(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="append-only",
    )
    capability(
        session="invalid",
        mode="champion",
        idempotency_key="append-only-refusal",
    )
    capability(
        session="2026-08-22",
        mode="champion",
        idempotency_key="append-only",
    )
    database = state_root / "lifecycle.sqlite3"

    statements = (
        "UPDATE lifecycle_events SET mode = 'champion'",
        "DELETE FROM lifecycle_events",
        "UPDATE advance_refusals SET reason_code = 'changed'",
        "DELETE FROM advance_refusals",
        "UPDATE advance_conflicts SET reason_code = 'changed'",
        "DELETE FROM advance_conflicts",
    )
    for statement in statements:
        with (
            sqlite3.connect(database) as connection,
            pytest.raises(sqlite3.IntegrityError, match="append-only"),
        ):
            connection.execute(statement)


def test_sqlite_read_failures_are_translated(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE lifecycle_events")

    with pytest.raises(LifecyclePersistenceError, match="read failed"):
        ledger.load_by_idempotency_key(IdempotencyKey("missing-after-drop"))


def test_operational_read_failure_does_not_create_a_terminal_refusal(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE lifecycle_events")

    with pytest.raises(LifecyclePersistenceError, match="checkpoint failed"):
        capability(
            session="2026-08-21",
            mode="champion",
            idempotency_key="operational-read-failure",
        )

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone() == (0,)


@pytest.mark.parametrize(
    ("reason_code", "expected_message"),
    [
        (3, "invalid reason_code in lifecycle ledger"),
        ("x" * 5_000, "unknown reason_code in lifecycle ledger"),
    ],
)
def test_corrupt_refusal_rows_fail_closed_with_a_bounded_diagnostic(
    tmp_path: Path,
    reason_code: object,
    expected_message: str,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        session="invalid",
        mode="champion",
        idempotency_key="corrupt-refusal",
    )
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT refusal_id, idempotency_key, reason_code, recorded_at FROM advance_refusals"
        ).fetchone()
        connection.execute("DROP TABLE advance_refusals")
        connection.execute(
            "CREATE TABLE advance_refusals (refusal_id, idempotency_key, reason_code, recorded_at)"
        )
        assert row is not None
        connection.execute(
            "INSERT INTO advance_refusals VALUES (?, ?, ?, ?)",
            (row[0], row[1], reason_code, row[3]),
        )

    with pytest.raises(LifecyclePersistenceError, match=expected_message):
        capability(
            session="invalid",
            mode="champion",
            idempotency_key="corrupt-refusal",
        )


@pytest.mark.parametrize(
    ("reason_code", "recorded_at", "expected_message"),
    [
        (
            "invalid_session",
            "2026-08-21T22:00:00+00:00",
            "unknown reason_code in lifecycle ledger",
        ),
        (
            "idempotency_key_conflict",
            "invalid",
            "invalid recorded_at in lifecycle ledger",
        ),
    ],
)
def test_corrupt_completed_conflict_rows_append_a_durable_invalid_state_refusal(
    tmp_path: Path,
    reason_code: str,
    recorded_at: str,
    expected_message: str,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="corrupt-conflict",
    )
    capability(
        session="2026-08-22",
        mode="champion",
        idempotency_key="corrupt-conflict",
    )
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE advance_conflicts")
        connection.execute(
            "CREATE TABLE advance_conflicts (idempotency_key, reason_code, recorded_at)"
        )
        connection.execute(
            "INSERT INTO advance_conflicts VALUES (?, ?, ?)",
            ("corrupt-conflict", reason_code, recorded_at),
        )

    with pytest.raises(LifecyclePersistenceError, match=expected_message):
        capability.ledger.load_by_idempotency_key(IdempotencyKey("corrupt-conflict"))

    original_replay = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="corrupt-conflict",
    )
    conflicting_replay = capability(
        session="2026-08-22",
        mode="champion",
        idempotency_key="corrupt-conflict",
    )

    expected = AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
    assert original_replay == expected
    assert conflicting_replay == expected
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT reason_code FROM advance_refusals WHERE idempotency_key = ?",
            ("corrupt-conflict",),
        ).fetchall() == [("invalid_durable_state",)]


def test_orphan_completed_conflict_refuses_before_starting_a_stream(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO advance_conflicts VALUES (?, ?, ?)",
            (
                "orphan-conflict",
                "idempotency_key_conflict",
                "2026-08-21T22:00:00+00:00",
            ),
        )

    with pytest.raises(
        LifecyclePersistenceError,
        match="lifecycle conflict does not belong to a completed stream",
    ):
        capability.ledger.load_by_idempotency_key(IdempotencyKey("orphan-conflict"))

    refused = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="orphan-conflict",
    )

    assert refused == AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
    assert _events(database) == []
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT reason_code FROM advance_refusals WHERE idempotency_key = ?",
            ("orphan-conflict",),
        ).fetchall() == [("invalid_durable_state",)]


@pytest.mark.parametrize("operation", ["reconcile", "pin", "refuse"])
def test_corrupt_checkpoint_history_uses_the_call_timestamp_for_its_refusal(
    tmp_path: Path,
    operation: str,
) -> None:
    capability = _configure(tmp_path / "runtime")
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    request = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key=f"corrupt-before-{operation}",
    )
    assert isinstance(request, AdvanceRequest)
    identity = PinnedRunIdentity.create(
        request,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    started_at = datetime(2026, 8, 21, 22, 0, tzinfo=UTC)
    refused_at = datetime(2026, 8, 22, 23, 30, tzinfo=UTC)
    ledger.start(request, identity, started_at)
    if operation == "pin":
        ledger.complete_reconciliation(request.idempotency_key, started_at)
    database = tmp_path / "runtime" / "lifecycle.sqlite3"
    _replace_with_corrupt_events(database, (("UPDATE lifecycle_events SET mode = 'invalid'",)))

    if operation == "reconcile":
        receipt = ledger.complete_reconciliation(request.idempotency_key, refused_at)
    elif operation == "pin":
        receipt = ledger.pin_run_inputs(request.idempotency_key, refused_at)
    else:
        receipt = ledger.record_refusal(
            request.idempotency_key,
            AdvanceFailureReason.INVALID_MODE,
            refused_at,
        )

    assert receipt == AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT idempotency_key, reason_code, recorded_at FROM advance_refusals"
        ).fetchall() == [
            (
                request.idempotency_key.value,
                AdvanceFailureReason.INVALID_DURABLE_STATE.value,
                refused_at.isoformat(),
            )
        ]


@pytest.mark.parametrize("corruption", CORRUPTIONS)
def test_advance_fails_closed_on_corrupt_durable_rows(
    tmp_path: Path,
    corruption: str,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="corrupt-stream",
    )
    database = state_root / "lifecycle.sqlite3"
    statements, expected_message = CORRUPTIONS[corruption]
    _replace_with_corrupt_events(database, statements)

    with pytest.raises(LifecyclePersistenceError, match=expected_message):
        capability.ledger.load_by_idempotency_key(IdempotencyKey("corrupt-stream"))

    refused = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="corrupt-stream",
    )
    replay = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="corrupt-stream",
    )

    assert refused == AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
    assert replay == refused
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT reason_code FROM advance_refusals WHERE idempotency_key = ?",
            ("corrupt-stream",),
        ).fetchall() == [("invalid_durable_state",)]


def _replace_with_corrupt_events(database: Path, statements: tuple[str, ...]) -> None:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT stream_id, sequence, idempotency_key, session, mode,
                   configuration_version, configuration_hash, run_id,
                   event_kind, completed_phase, recorded_at
            FROM lifecycle_events ORDER BY sequence
            """
        ).fetchall()
        connection.execute("DROP TABLE lifecycle_events")
        connection.execute(
            """
            CREATE TABLE lifecycle_events (
                stream_id, sequence, idempotency_key, session, mode,
                configuration_version, configuration_hash, run_id,
                event_kind, completed_phase, recorded_at
            )
            """
        )
        connection.executemany(
            "INSERT INTO lifecycle_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
        for statement in statements:
            connection.execute(statement)
