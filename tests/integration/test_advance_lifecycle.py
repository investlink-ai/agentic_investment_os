from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, override

import pytest
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from agentic_investment_os.adapters.sqlite_lifecycle import (
    RuntimeRootRefusal,
    SQLiteLifecycleLedger,
    prepare_runtime_database,
)
from agentic_investment_os.application.lifecycle import Advance
from agentic_investment_os.domain.lifecycle import (
    AdvanceAttempt,
    AdvanceCommand,
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRecovery,
    AdvanceRequest,
    AppendLifecycleRecord,
    AppendTerminalLifecycleRecord,
    DurableAdvanceConflict,
    DurableAdvanceRefusal,
    InputRefusal,
    InvalidLifecycleStateError,
    LifecycleCommand,
    LifecycleDecision,
    LifecycleEvent,
    LifecyclePersistenceError,
    LifecyclePhase,
    PinnedRunIdentity,
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
SQLiteValue = str | bytes | int | float | None
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
    "unknown_event_kind": (
        ("UPDATE lifecycle_events SET event_kind = 'unknown' WHERE sequence = 1",),
        "lifecycle stream checkpoint order is invalid",
    ),
    "unknown_phase": (
        ("UPDATE lifecycle_events SET completed_phase = 'Unknown' WHERE sequence = 1",),
        "lifecycle stream checkpoint order is invalid",
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


class MissingAggregateRowCursor(sqlite3.Cursor):
    """Simulate a hostile driver violating SQLite aggregate-row guarantees."""

    @override
    def fetchone(self) -> tuple[object, ...] | None:
        return None


class MissingAggregateRowConnection(sqlite3.Connection):
    """Return no row only for the refusal-sequence aggregate query."""

    @override
    def execute(
        self,
        sql: str,
        # The adapter passes positional SQLite scalars; typeshed's private parameter alias cannot be
        # named in this hostile-driver test override.
        parameters: tuple[SQLiteValue, ...] = (),  # type: ignore[override]
        /,
    ) -> sqlite3.Cursor:
        if "MAX(refusal_id)" in sql:
            cursor = self.cursor(factory=MissingAggregateRowCursor)
            cursor.execute(sql, parameters)
            return cursor
        return super().execute(sql, parameters)


class MissingAggregateRowLedger(SQLiteLifecycleLedger):
    """Open the test database through the hostile aggregate-row connection."""

    def __init__(self, database: Path) -> None:
        self.database = database
        super().__init__(database)

    @override
    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(
            f"{self.database.as_uri()}?mode=rw",
            uri=True,
            factory=MissingAggregateRowConnection,
        )


class InvalidAggregateRowCursor(sqlite3.Cursor):
    """Simulate a hostile driver returning a non-integer aggregate."""

    @override
    def fetchone(self) -> tuple[object, ...] | None:
        return ("invalid",)


class InvalidAggregateRowConnection(sqlite3.Connection):
    """Return a non-integer row only for the refusal-sequence aggregate query."""

    @override
    def execute(
        self,
        sql: str,
        # The adapter passes positional SQLite scalars; typeshed's private parameter alias cannot be
        # named in this hostile-driver test override.
        parameters: tuple[SQLiteValue, ...] = (),  # type: ignore[override]
        /,
    ) -> sqlite3.Cursor:
        if "MAX(refusal_id)" in sql:
            cursor = self.cursor(factory=InvalidAggregateRowCursor)
            cursor.execute(sql, parameters)
            return cursor
        return super().execute(sql, parameters)


class InvalidAggregateRowLedger(SQLiteLifecycleLedger):
    """Open the test database through the invalid aggregate-row connection."""

    def __init__(self, database: Path) -> None:
        self.database = database
        super().__init__(database)

    @override
    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(
            f"{self.database.as_uri()}?mode=rw",
            uri=True,
            factory=InvalidAggregateRowConnection,
        )


@dataclass(frozen=True)
class InterruptingLedger:
    delegate: SQLiteLifecycleLedger
    operation: str
    timing: str

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        recorded_at: datetime,
    ) -> LifecycleDecision:
        expected_operation = _attempt_operation(command, attempt)
        if self.operation == expected_operation and self.timing == "before":
            raise SimulatedInterruptionError
        result = self.delegate.advance_step(command, attempt, recorded_at)
        actual_operation = _decision_operation(result, expected_operation)
        if self.operation == actual_operation and self.timing == "after":
            raise SimulatedInterruptionError
        return result


@dataclass
class RacingStartLedger:
    delegate: SQLiteLifecycleLedger
    winner_phase: LifecyclePhase | None
    raced: bool = False

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        recorded_at: datetime,
    ) -> LifecycleDecision:
        if not self.raced:
            winner = self.delegate.advance_step(command, AdvanceAttempt(), recorded_at)
            assert isinstance(winner, AppendLifecycleRecord)
            if self.winner_phase is LifecyclePhase.RECONCILE_PRIOR_STATE:
                reconciled = self.delegate.advance_step(command, winner.attempt, recorded_at)
                assert isinstance(reconciled, AppendLifecycleRecord)
            self.raced = True
        return self.delegate.advance_step(command, attempt, recorded_at)


def _attempt_operation(command: LifecycleCommand, attempt: AdvanceAttempt) -> str:
    if isinstance(command, InputRefusal):
        return "refuse"
    return {None: "start", 0: "reconcile", 1: "pin"}.get(
        attempt.last_sequence,
        "pin",
    )


def _decision_operation(decision: LifecycleDecision, fallback: str) -> str:
    if not isinstance(decision, AppendLifecycleRecord):
        return fallback
    if isinstance(decision.record, DurableAdvanceRefusal):
        return "refuse"
    if isinstance(decision.record, DurableAdvanceConflict):
        return "start"
    assert isinstance(decision.record, LifecycleEvent)
    return {0: "start", 1: "reconcile", 2: "pin"}[decision.record.sequence]


@dataclass
class _ReferenceStream:
    session: str
    events: int
    pinned_run_identity: PinnedRunIdentity


@dataclass
class _LifecycleReferenceModel:
    configuration_version: int
    configuration_hash: str
    streams: dict[str, _ReferenceStream] = field(default_factory=dict)
    sessions: dict[str, str] = field(default_factory=dict)
    refusals: dict[str, AdvanceFailureReason] = field(default_factory=dict)
    conflicts: set[str] = field(default_factory=set)
    has_unkeyed_refusal: bool = False

    def advance(
        self,
        *,
        session: str,
        mode: str,
        key: str,
    ) -> AdvanceReceipt:
        if " " in key:
            self.has_unkeyed_refusal = True
            return self._failed(AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY)
        invalid_reason = (
            AdvanceFailureReason.INVALID_SESSION
            if session == "invalid"
            else AdvanceFailureReason.INVALID_MODE
            if mode != "champion"
            else None
        )
        if key in self.refusals:
            return self._failed(self.refusals[key])
        stream = self.streams.get(key)
        if invalid_reason is not None:
            return self._advance_invalid(key, stream, invalid_reason)
        return self._advance_valid(session, key, stream)

    def _advance_valid(
        self,
        session: str,
        key: str,
        stream: _ReferenceStream | None,
    ) -> AdvanceReceipt:
        if stream is not None:
            if stream.session != session:
                return self._record_idempotency_conflict(key, stream)
            recovery = (
                AdvanceRecovery.PREVIOUSLY_COMPLETED
                if stream.events == PINNED_EVENT_COUNT
                else AdvanceRecovery.RESUMED
            )
            stream.events = PINNED_EVENT_COUNT
            return AdvanceReceipt.advanced(stream.pinned_run_identity, recovery)
        if session in self.sessions:
            self.refusals[key] = AdvanceFailureReason.SESSION_STREAM_CONFLICT
            return self._failed(AdvanceFailureReason.SESSION_STREAM_CONFLICT)
        identity = self._identity(session)
        self.streams[key] = _ReferenceStream(session, PINNED_EVENT_COUNT, identity)
        self.sessions[session] = key
        return AdvanceReceipt.advanced(identity, AdvanceRecovery.FRESH)

    def _advance_invalid(
        self,
        key: str,
        stream: _ReferenceStream | None,
        reason: AdvanceFailureReason,
    ) -> AdvanceReceipt:
        if stream is not None:
            return self._record_idempotency_conflict(key, stream)
        self.refusals[key] = reason
        return self._failed(reason)

    def interrupt(self, *, session: str, key: str, committed_events: int) -> None:
        self.streams[key] = _ReferenceStream(session, committed_events, self._identity(session))
        self.sessions[session] = key

    def counts(self) -> tuple[int, int, int]:
        return (
            sum(stream.events for stream in self.streams.values()),
            len(self.refusals) + int(self.has_unkeyed_refusal),
            len(self.conflicts),
        )

    def _record_idempotency_conflict(
        self,
        key: str,
        stream: _ReferenceStream,
    ) -> AdvanceReceipt:
        if stream.events == PINNED_EVENT_COUNT:
            self.conflicts.add(key)
        else:
            self.refusals[key] = AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
        return self._failed(AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT)

    @staticmethod
    def _failed(reason: AdvanceFailureReason) -> AdvanceReceipt:
        return AdvanceReceipt.failed_closed(reason)

    def _identity(self, session: str) -> PinnedRunIdentity:
        encoded = json.dumps(
            (self.configuration_hash, self.configuration_version, "champion", session)
        ).encode()
        return PinnedRunIdentity(
            run_id=hashlib.sha256(encoded).hexdigest(),
            configuration_version=self.configuration_version,
            configuration_hash=self.configuration_hash,
        )


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


def _authoritative_counts(database: Path) -> tuple[int, int, int]:
    with sqlite3.connect(database) as connection:
        event_count = connection.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()
        refusal_count = connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone()
        conflict_count = connection.execute("SELECT COUNT(*) FROM advance_conflicts").fetchone()
    assert event_count is not None
    assert refusal_count is not None
    assert conflict_count is not None
    return int(event_count[0]), int(refusal_count[0]), int(conflict_count[0])


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


class LifecycleStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self._temporary_directory = TemporaryDirectory(
            prefix="agentic-investment-os-lifecycle-state-machine-"
        )
        self.state_root = Path(self._temporary_directory.name).resolve() / "runtime"
        self.capability = _configure(self.state_root)
        self.reference = _LifecycleReferenceModel(
            self.capability.configuration_version,
            self.capability.configuration_hash,
        )
        self.next_identity = 0

    @override
    def teardown(self) -> None:
        self._temporary_directory.cleanup()

    def _new_request(self) -> tuple[str, str]:
        self.next_identity += 1
        session = date(2026, 9, 1) + timedelta(days=self.next_identity)
        return session.isoformat(), f"generated-{self.next_identity}"

    def _advance(self, *, session: str, mode: str, key: str) -> None:
        expected = self.reference.advance(session=session, mode=mode, key=key)
        observed = self.capability(session=session, mode=mode, idempotency_key=key)
        assert observed == expected

    def _stream_key(self, selection: int) -> str:
        streams = sorted(self.reference.streams)
        return streams[selection % len(streams)]

    @rule()
    def advance_fresh(self) -> None:
        session, key = self._new_request()
        self._advance(session=session, mode="champion", key=key)

    @precondition(lambda self: bool(self.reference.streams))
    @rule(selection=st.integers(min_value=0, max_value=255))
    def replay(self, selection: int) -> None:
        key = self._stream_key(selection)
        self._advance(
            session=self.reference.streams[key].session,
            mode="champion",
            key=key,
        )

    @precondition(lambda self: bool(self.reference.streams))
    @rule(selection=st.integers(min_value=0, max_value=255))
    def reuse_idempotency_key_for_another_session(self, selection: int) -> None:
        key = self._stream_key(selection)
        session, _ = self._new_request()
        self._advance(session=session, mode="champion", key=key)

    @precondition(lambda self: bool(self.reference.streams))
    @rule(selection=st.integers(min_value=0, max_value=255))
    def reuse_session_for_another_idempotency_key(self, selection: int) -> None:
        existing = self.reference.streams[self._stream_key(selection)]
        _, key = self._new_request()
        self._advance(session=existing.session, mode="champion", key=key)

    @rule(invalid_field=st.sampled_from(("session", "mode", "idempotency_key")))
    def submit_invalid_input(
        self,
        invalid_field: Literal["session", "mode", "idempotency_key"],
    ) -> None:
        session, key = self._new_request()
        mode = "champion"
        if invalid_field == "session":
            session = "invalid"
        elif invalid_field == "mode":
            mode = "invalid"
        else:
            key = f"not valid {self.next_identity}"
        self._advance(session=session, mode=mode, key=key)

    @precondition(lambda self: bool(self.reference.refusals))
    @rule(selection=st.integers(min_value=0, max_value=255))
    def replay_durable_refusal(self, selection: int) -> None:
        keys = sorted(self.reference.refusals)
        key = keys[selection % len(keys)]
        self._advance(session="2026-08-21", mode="champion", key=key)

    @rule(
        interrupted_write=st.sampled_from(
            (("start", 1), ("reconcile", 2), ("pin", PINNED_EVENT_COUNT))
        )
    )
    def interrupt_fresh_advance(self, interrupted_write: tuple[str, int]) -> None:
        session, key = self._new_request()
        operation, committed_events = interrupted_write
        ledger = self.capability.ledger
        assert isinstance(ledger, SQLiteLifecycleLedger)
        interrupted = Advance(
            InterruptingLedger(ledger, operation, "after"),
            self.capability.configuration_version,
            self.capability.configuration_hash,
            self.capability.clock,
        )
        with pytest.raises(SimulatedInterruptionError):
            interrupted(session=session, mode="champion", idempotency_key=key)
        self.reference.interrupt(
            session=session,
            key=key,
            committed_events=committed_events,
        )

    @rule()
    def reopen_database(self) -> None:
        self.capability = _configure(self.state_root)

    @invariant()
    def authoritative_counts_match_reference_model(self) -> None:
        assert (
            _authoritative_counts(self.state_root / "lifecycle.sqlite3") == self.reference.counts()
        )


TestLifecycleStateMachine = LifecycleStateMachine.TestCase


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
    interrupted = Advance(
        InterruptingLedger(ledger, "start", "after"),
        capability.configuration_version,
        capability.configuration_hash,
        capability.clock,
    )
    with pytest.raises(SimulatedInterruptionError):
        interrupted(
            session="2026-08-21",
            mode="champion",
            idempotency_key="partial-conflict",
        )

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


def test_lifecycle_ledger_applies_domain_steps_idempotently(tmp_path: Path) -> None:
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

    command = AdvanceCommand(parsed, identity)
    started = ledger.advance_step(command, AdvanceAttempt(), now)
    assert isinstance(started, AppendLifecycleRecord)
    assert isinstance(started.record, LifecycleEvent)
    assert started.record.sequence == 0

    conflicting = AdvanceRequest.parse(
        session="2026-08-25",
        mode="champion",
        idempotency_key="adapter-idempotency",
    )
    assert isinstance(conflicting, AdvanceRequest)
    conflict_command = AdvanceCommand(
        conflicting,
        PinnedRunIdentity.create(
            conflicting,
            configuration_version=1,
            configuration_hash="a" * SHA256_HEX_LENGTH,
        ),
    )
    partial_conflict = ledger.advance_step(conflict_command, AdvanceAttempt(), now)
    assert isinstance(partial_conflict, AppendTerminalLifecycleRecord)
    assert isinstance(partial_conflict.record, DurableAdvanceRefusal)
    assert partial_conflict.receipt == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
    )
    replay = ledger.advance_step(conflict_command, AdvanceAttempt(), now)
    assert replay == partial_conflict.receipt
    assert ledger.advance_step(command, AdvanceAttempt(), now) == partial_conflict.receipt
    assert not hasattr(ledger, "start")
    assert not hasattr(ledger, "complete_reconciliation")
    assert not hasattr(ledger, "pin_run_inputs")
    assert not hasattr(ledger, "record_refusal")


def test_lifecycle_ledger_appends_generic_checkpoint_records(tmp_path: Path) -> None:
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

    command = AdvanceCommand(request, identity)
    attempt = AdvanceAttempt()
    terminal: AppendTerminalLifecycleRecord | None = None
    for expected_sequence in range(PINNED_EVENT_COUNT):
        decision = ledger.advance_step(command, attempt, now)
        assert not isinstance(decision, AdvanceReceipt)
        assert isinstance(decision.record, LifecycleEvent)
        assert decision.record.sequence == expected_sequence
        if isinstance(decision, AppendTerminalLifecycleRecord):
            terminal = decision
        else:
            attempt = decision.attempt
    assert terminal is not None
    assert terminal.receipt == AdvanceReceipt.advanced(identity, AdvanceRecovery.FRESH)
    assert ledger.advance_step(command, AdvanceAttempt(), now) == AdvanceReceipt.advanced(
        identity,
        AdvanceRecovery.PREVIOUSLY_COMPLETED,
    )

    conflicting_identity = PinnedRunIdentity.create(
        request,
        configuration_version=1,
        configuration_hash="d" * SHA256_HEX_LENGTH,
    )
    concurrent_conflict = ledger.advance_step(
        AdvanceCommand(request, conflicting_identity),
        AdvanceAttempt(),
        now,
    )
    assert isinstance(concurrent_conflict, AppendTerminalLifecycleRecord)
    assert isinstance(concurrent_conflict.record, DurableAdvanceConflict)
    assert concurrent_conflict.receipt == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
    )
    assert (
        ledger.advance_step(
            AdvanceCommand(request, conflicting_identity),
            AdvanceAttempt(),
            now,
        )
        == concurrent_conflict.receipt
    )


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


def test_sqlite_status_read_failures_are_translated(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE lifecycle_events")

    with pytest.raises(LifecyclePersistenceError, match="checkpoint failed"):
        ledger.rebuild_status()


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


def test_missing_refusal_sequence_row_fails_closed(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    hostile = Advance(
        ledger=MissingAggregateRowLedger(state_root / "lifecycle.sqlite3"),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        clock=capability.clock,
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="invalid idempotency_key in lifecycle refusal ledger",
    ):
        hostile(
            session="2026-08-21",
            mode="champion",
            idempotency_key="missing-aggregate-row",
        )


def test_invalid_refusal_sequence_row_has_a_bounded_diagnostic(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    hostile = Advance(
        ledger=InvalidAggregateRowLedger(state_root / "lifecycle.sqlite3"),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        clock=capability.clock,
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="invalid refusal_id in lifecycle ledger",
    ):
        hostile(
            session="2026-08-21",
            mode="champion",
            idempotency_key="invalid-aggregate-row",
        )


def test_terminal_refusal_replays_without_reading_unrelated_missing_history(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    refused = capability(
        session="invalid",
        mode="champion",
        idempotency_key="terminal-before-missing-history",
    )
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE lifecycle_events")

    replay = capability(
        session="invalid",
        mode="champion",
        idempotency_key="terminal-before-missing-history",
    )

    assert replay == refused


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
            "invalid conflict association",
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

    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    with pytest.raises(LifecyclePersistenceError, match=expected_message):
        ledger.rebuild_status()

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

    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    with pytest.raises(
        LifecyclePersistenceError,
        match="invalid conflict association",
    ):
        ledger.rebuild_status()

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
    command = AdvanceCommand(request, identity)
    started = ledger.advance_step(command, AdvanceAttempt(), started_at)
    assert isinstance(started, AppendLifecycleRecord)
    if operation == "pin":
        reconciled = ledger.advance_step(command, started.attempt, started_at)
        assert isinstance(reconciled, AppendLifecycleRecord)
    database = tmp_path / "runtime" / "lifecycle.sqlite3"
    _replace_with_corrupt_events(database, (("UPDATE lifecycle_events SET mode = 'invalid'",)))

    refused = Advance(
        ledger,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
        clock=FixedClock(refused_at),
    )
    receipt = refused(
        session=request.session.isoformat(),
        mode="research-lab" if operation == "refuse" else request.mode.value,
        idempotency_key=request.idempotency_key.value,
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

    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    with pytest.raises(LifecyclePersistenceError, match=expected_message):
        ledger.rebuild_status()

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


def test_unrelated_corrupt_history_does_not_change_a_fresh_request(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="unrelated-corrupt-stream",
    )
    capability(
        session="invalid",
        mode="champion",
        idempotency_key="unrelated-corrupt-refusal",
    )
    database = state_root / "lifecycle.sqlite3"
    _replace_with_corrupt_events(database, ("UPDATE lifecycle_events SET mode = 'invalid'",))
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
            (row[0], row[1], "unknown", row[3]),
        )

    receipt = capability(
        session="2026-08-22",
        mode="champion",
        idempotency_key="fresh-beside-corruption",
    )

    assert receipt.disposition is AdvanceDisposition.ADVANCED
    assert receipt.recovery is AdvanceRecovery.FRESH
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM lifecycle_events WHERE idempotency_key = ?",
            ("fresh-beside-corruption",),
        ).fetchone() == (PINNED_EVENT_COUNT,)
        assert connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone() == (1,)


def test_restart_does_not_globally_validate_unrelated_history(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO advance_conflicts (idempotency_key, reason_code, recorded_at)
            VALUES ('unrelated-orphan', 'idempotency_key_conflict', ?)
            """,
            (datetime(2026, 8, 21, 10, tzinfo=UTC).isoformat(),),
        )

    restarted = _configure(state_root)
    receipt = restarted(
        session="2026-08-22",
        mode="champion",
        idempotency_key="fresh-after-restart",
    )

    assert receipt.disposition is AdvanceDisposition.ADVANCED
    assert receipt.recovery is AdvanceRecovery.FRESH


def test_invalid_key_replays_without_reading_corrupt_unrelated_history(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="corrupt-before-invalid-key",
    )
    database = state_root / "lifecycle.sqlite3"
    _replace_with_corrupt_events(database, ("UPDATE lifecycle_events SET mode = 'invalid'",))

    refused = capability(
        session="2026-08-22",
        mode="champion",
        idempotency_key="not valid",
    )
    replay = capability(
        session="2026-08-22",
        mode="champion",
        idempotency_key="not valid",
    )

    expected = AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY)
    assert refused == expected
    assert replay == expected
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT idempotency_key, reason_code FROM advance_refusals"
        ).fetchall() == [(None, AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY.value)]


def test_invalid_key_refuses_when_unrelated_history_tables_are_missing(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE lifecycle_events")
        connection.execute("DROP TABLE advance_conflicts")

    refused = capability(
        session="2026-08-22",
        mode="champion",
        idempotency_key="not valid",
    )

    assert refused == AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT idempotency_key, reason_code FROM advance_refusals"
        ).fetchall() == [(None, AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY.value)]


def test_corrupt_request_appends_after_unrelated_refusal_sequence(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        session="invalid",
        mode="champion",
        idempotency_key="earlier-unrelated-refusal",
    )
    capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="corrupt-after-refusal",
    )
    database = state_root / "lifecycle.sqlite3"
    _replace_with_corrupt_events(database, ("UPDATE lifecycle_events SET mode = 'invalid'",))

    refused = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="corrupt-after-refusal",
    )

    assert refused == AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT refusal_id, idempotency_key, reason_code FROM advance_refusals "
            "ORDER BY refusal_id"
        ).fetchall() == [
            (1, "earlier-unrelated-refusal", AdvanceFailureReason.INVALID_SESSION.value),
            (2, "corrupt-after-refusal", AdvanceFailureReason.INVALID_DURABLE_STATE.value),
        ]


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
