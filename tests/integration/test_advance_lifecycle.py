from __future__ import annotations

import sqlite3
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
    AdvanceRequest,
    IdempotencyKey,
    LifecyclePersistenceError,
    LifecyclePhase,
    LifecycleProgress,
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


@dataclass(frozen=True)
class FixedClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


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

    assert first == replay
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
    assert original_replay == original
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
    assert invalid_key.failure_reason == "invalid_idempotency_key"


def test_completed_request_cannot_be_shadowed_by_a_later_invalid_reuse(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    completed = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="completed-key",
    )

    malformed_reuse = capability(
        session="invalid",
        mode="champion",
        idempotency_key="completed-key",
    )
    valid_replay = _configure(state_root)(
        session="2026-08-21",
        mode="champion",
        idempotency_key="completed-key",
    )

    assert malformed_reuse == completed
    assert valid_replay == completed
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM advance_refusals WHERE idempotency_key = 'completed-key'"
        ).fetchone() == (0,)


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
        session="invalid",
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


def test_advance_resumes_after_a_checkpoint_transaction_rolls_back(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_pin_before_insert
            BEFORE INSERT ON lifecycle_events
            WHEN NEW.event_kind = 'run_inputs_pinned'
            BEGIN
                SELECT RAISE(ABORT, 'injected pin failure');
            END
            """
        )

    with pytest.raises(LifecyclePersistenceError, match="SQLite lifecycle checkpoint failed"):
        capability(
            session="2026-08-21",
            mode="champion",
            idempotency_key="rollback-key",
        )

    assert _events(database) == [
        ("advance_requested", None),
        ("phase_completed", "ReconcilePriorState"),
    ]
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER fail_pin_before_insert")

    recovered = _configure(state_root)(
        session="2026-08-21",
        mode="champion",
        idempotency_key="rollback-key",
    )

    assert recovered.disposition is AdvanceDisposition.ADVANCED
    assert recovered.completed_phase is LifecyclePhase.PIN_RUN_INPUTS
    assert len(_events(database)) == PINNED_EVENT_COUNT


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
    assert isinstance(started, LifecycleProgress)
    partial_conflict = ledger.record_refusal(
        parsed.idempotency_key, AdvanceFailureReason.INVALID_SESSION, now
    )
    assert partial_conflict.failure_reason == "idempotency_key_conflict"
    assert ledger.start(parsed, identity, now) == partial_conflict
    assert (
        ledger.record_refusal(parsed.idempotency_key, AdvanceFailureReason.INVALID_SESSION, now)
        == partial_conflict
    )
    with pytest.raises(
        LifecyclePersistenceError, match="required lifecycle stream is missing or terminal"
    ):
        ledger.complete_reconciliation(parsed.idempotency_key, now)

    resumable = AdvanceRequest.parse(
        session="2026-08-26",
        mode="champion",
        idempotency_key="adapter-idempotency-resumable",
    )
    assert isinstance(resumable, AdvanceRequest)
    resumable_identity = PinnedRunIdentity.create(
        resumable,
        configuration_version=1,
        configuration_hash="c" * SHA256_HEX_LENGTH,
    )
    resumable_started = ledger.start(resumable, resumable_identity, now)
    assert isinstance(resumable_started, LifecycleProgress)
    assert ledger.start(resumable, resumable_identity, now) == resumable_started
    reconciled = ledger.complete_reconciliation(resumable.idempotency_key, now)
    assert isinstance(reconciled, LifecycleProgress)
    assert ledger.complete_reconciliation(resumable.idempotency_key, now) == reconciled
    pinned = ledger.pin_run_inputs(resumable.idempotency_key, now)
    assert ledger.pin_run_inputs(resumable.idempotency_key, now) == pinned
    assert ledger.complete_reconciliation(resumable.idempotency_key, now) == pinned

    missing = IdempotencyKey("missing-stream")
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
    database = state_root / "lifecycle.sqlite3"

    statements = (
        "UPDATE lifecycle_events SET mode = 'champion'",
        "DELETE FROM lifecycle_events",
        "UPDATE advance_refusals SET reason_code = 'changed'",
        "DELETE FROM advance_refusals",
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
