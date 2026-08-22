from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from agentic_investment_os.adapters.sqlite_lifecycle import SQLiteLifecycleLedger
from agentic_investment_os.application.lifecycle import Advance, Status
from agentic_investment_os.domain.lifecycle import (
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceRecovery,
    AdvanceRequest,
    IdempotencyKey,
    LifecyclePersistenceError,
    LifecyclePhase,
    PinnedRunIdentity,
)
from agentic_investment_os.entrypoints.configuration import ConfigurationSource
from agentic_investment_os.entrypoints.lifecycle import configure_status

CURRENT_DATABASE_VERSION = 3
LEGACY_DATABASE_VERSION = 1
INTERMEDIATE_DATABASE_VERSION = 2
CONFIGURATION_HASH = "a" * 64
RECORDED_AT = "2026-08-21T22:00:00+00:00"
CONFLICT_RECORDED_AT = "2026-08-22T22:00:00+00:00"
MAX_DIAGNOSTIC_LENGTH = 100

_LEGACY_SCHEMA = (
    """
    CREATE TABLE lifecycle_events (
        stream_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        idempotency_key TEXT NOT NULL,
        session TEXT NOT NULL,
        mode TEXT NOT NULL CHECK (mode = 'champion'),
        configuration_version INTEGER NOT NULL CHECK (configuration_version = 1),
        configuration_hash TEXT NOT NULL CHECK (length(configuration_hash) = 64),
        run_id TEXT NOT NULL CHECK (length(run_id) = 64),
        event_kind TEXT NOT NULL CHECK (
            event_kind IN ('advance_requested', 'phase_completed', 'run_inputs_pinned')
        ),
        completed_phase TEXT CHECK (
            completed_phase IS NULL
            OR completed_phase IN ('ReconcilePriorState', 'PinRunInputs')
        ),
        recorded_at TEXT NOT NULL,
        PRIMARY KEY (stream_id, sequence),
        UNIQUE (idempotency_key, sequence)
    ) STRICT
    """,
    """
    CREATE UNIQUE INDEX one_initial_event_per_stream
    ON lifecycle_events(stream_id)
    WHERE event_kind = 'advance_requested'
    """,
    """
    CREATE TABLE advance_refusals (
        refusal_id INTEGER PRIMARY KEY,
        idempotency_key TEXT UNIQUE,
        reason_code TEXT NOT NULL CHECK (
            reason_code IN (
                'invalid_session', 'invalid_mode', 'invalid_idempotency_key',
                'session_stream_conflict', 'idempotency_key_conflict',
                'invalid_durable_state'
            )
        ),
        recorded_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE UNIQUE INDEX one_unkeyed_refusal_per_reason
    ON advance_refusals(reason_code)
    WHERE idempotency_key IS NULL
    """,
    """
    CREATE TRIGGER lifecycle_events_are_append_only_update
    BEFORE UPDATE ON lifecycle_events
    BEGIN SELECT RAISE(ABORT, 'append-only lifecycle ledger'); END
    """,
    """
    CREATE TRIGGER lifecycle_events_are_append_only_delete
    BEFORE DELETE ON lifecycle_events
    BEGIN SELECT RAISE(ABORT, 'append-only lifecycle ledger'); END
    """,
    """
    CREATE TRIGGER advance_refusals_are_append_only_update
    BEFORE UPDATE ON advance_refusals
    BEGIN SELECT RAISE(ABORT, 'append-only refusal ledger'); END
    """,
    """
    CREATE TRIGGER advance_refusals_are_append_only_delete
    BEFORE DELETE ON advance_refusals
    BEGIN SELECT RAISE(ABORT, 'append-only refusal ledger'); END
    """,
)
_CONFLICT_TABLE = """
CREATE TABLE advance_conflicts (
    idempotency_key TEXT PRIMARY KEY,
    reason_code TEXT NOT NULL CHECK (reason_code = 'idempotency_key_conflict'),
    recorded_at TEXT NOT NULL
) STRICT
"""
_CONFLICT_TRIGGERS = (
    """
    CREATE TRIGGER advance_conflicts_are_append_only_update
    BEFORE UPDATE ON advance_conflicts
    BEGIN SELECT RAISE(ABORT, 'append-only conflict ledger'); END
    """,
    """
    CREATE TRIGGER advance_conflicts_are_append_only_delete
    BEFORE DELETE ON advance_conflicts
    BEGIN SELECT RAISE(ABORT, 'append-only conflict ledger'); END
    """,
)


@dataclass(frozen=True)
class FixedClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


def _create_database(
    database: Path,
    statements: tuple[str, ...],
    *,
    version: int = 0,
) -> None:
    with sqlite3.connect(database) as connection:
        for statement in statements:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {version}")


def _database_version(database: Path) -> int:
    with sqlite3.connect(database) as connection:
        row = connection.execute("PRAGMA user_version").fetchone()
    assert row is not None
    return int(row[0])


def _schema(database: Path) -> list[tuple[str, str, str, str]]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_schema
            WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
            ORDER BY type, name
            """
        ).fetchall()
    return [(str(kind), str(name), str(table), str(sql)) for kind, name, table, sql in rows]


def _populate_legacy_history(database: Path) -> tuple[AdvanceRequest, PinnedRunIdentity]:
    request = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key="legacy-complete",
    )
    assert isinstance(request, AdvanceRequest)
    identity = PinnedRunIdentity.create(
        request,
        configuration_version=1,
        configuration_hash=CONFIGURATION_HASH,
    )
    rows = (
        (0, "advance_requested", None),
        (1, "phase_completed", LifecyclePhase.RECONCILE_PRIOR_STATE.value),
        (2, "run_inputs_pinned", LifecyclePhase.PIN_RUN_INPUTS.value),
    )
    with sqlite3.connect(database) as connection:
        for sequence, event_kind, completed_phase in rows:
            connection.execute(
                """
                INSERT INTO lifecycle_events (
                    stream_id, sequence, idempotency_key, session, mode,
                    configuration_version, configuration_hash, run_id,
                    event_kind, completed_phase, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.stream_id,
                    sequence,
                    request.idempotency_key.value,
                    request.session.isoformat(),
                    request.mode.value,
                    identity.configuration_version,
                    identity.configuration_hash,
                    identity.run_id,
                    event_kind,
                    completed_phase,
                    RECORDED_AT,
                ),
            )
        connection.execute(
            """
            INSERT INTO advance_refusals (idempotency_key, reason_code, recorded_at)
            VALUES ('legacy-refusal', 'invalid_session', ?)
            """,
            (RECORDED_AT,),
        )
        connection.execute(
            """
            INSERT INTO advance_refusals (idempotency_key, reason_code, recorded_at)
            VALUES (NULL, 'invalid_idempotency_key', ?)
            """,
            (RECORDED_AT,),
        )
    return request, identity


def _authoritative_rows(database: Path) -> tuple[list[tuple[object, ...]], ...]:
    with sqlite3.connect(database) as connection:
        events = connection.execute(
            "SELECT * FROM lifecycle_events ORDER BY stream_id, sequence"
        ).fetchall()
        refusals = connection.execute(
            "SELECT * FROM advance_refusals ORDER BY refusal_id"
        ).fetchall()
    return events, refusals


def _swap_index_roots(database: Path, first: str, second: str) -> None:
    with sqlite3.connect(database) as connection:
        roots = dict(
            connection.execute(
                "SELECT name, rootpage FROM sqlite_schema WHERE name IN (?, ?)",
                (first, second),
            ).fetchall()
        )
        assert set(roots) == {first, second}
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute(
            "UPDATE sqlite_schema SET rootpage = ? WHERE name = ?",
            (roots[second], first),
        )
        connection.execute(
            "UPDATE sqlite_schema SET rootpage = ? WHERE name = ?",
            (roots[first], second),
        )
        connection.execute("PRAGMA writable_schema = OFF")


def test_fresh_database_records_its_physical_schema_version(tmp_path: Path) -> None:
    database = tmp_path / "fresh.sqlite3"

    SQLiteLifecycleLedger(database)

    assert _database_version(database) == CURRENT_DATABASE_VERSION
    assert {name for _, name, _, _ in _schema(database)} == {
        "advance_conflicts",
        "advance_conflicts_are_append_only_delete",
        "advance_conflicts_are_append_only_update",
        "advance_refusals",
        "advance_refusals_are_append_only_delete",
        "advance_refusals_are_append_only_update",
        "lifecycle_events",
        "lifecycle_events_are_append_only_delete",
        "lifecycle_events_are_append_only_update",
        "one_initial_event_per_stream",
        "one_unkeyed_refusal_per_reason",
    }


def test_populated_pre_conflict_database_upgrades_without_rewriting_history(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.sqlite3"
    _create_database(database, _LEGACY_SCHEMA)
    request, identity = _populate_legacy_history(database)
    before = _authoritative_rows(database)

    ledger = SQLiteLifecycleLedger(database)
    capability = Advance(
        ledger=ledger,
        configuration_version=identity.configuration_version,
        configuration_hash=identity.configuration_hash,
        clock=FixedClock(datetime(2026, 8, 22, 22, 0, tzinfo=UTC)),
    )
    replay = capability(
        session=request.session.isoformat(),
        mode=request.mode.value,
        idempotency_key=request.idempotency_key.value,
    )
    keyed_refusal = ledger.record_refusal(
        IdempotencyKey("legacy-refusal"),
        AdvanceFailureReason.INVALID_SESSION,
        datetime(2026, 8, 23, 22, 0, tzinfo=UTC),
    )
    unkeyed_refusal = ledger.record_refusal(
        None,
        AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
        datetime(2026, 8, 23, 22, 0, tzinfo=UTC),
    )
    conflict = capability(
        session="2026-08-22",
        mode=request.mode.value,
        idempotency_key=request.idempotency_key.value,
    )

    assert _database_version(database) == CURRENT_DATABASE_VERSION
    assert _authoritative_rows(database) == before
    assert replay.disposition is AdvanceDisposition.ADVANCED
    assert replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED
    assert replay.pinned_run_identity == identity
    assert keyed_refusal.failure_reason is AdvanceFailureReason.INVALID_SESSION
    assert unkeyed_refusal.failure_reason is AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY
    assert conflict.failure_reason is AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM advance_conflicts").fetchall() == [
            (
                request.idempotency_key.value,
                "idempotency_key_conflict",
                CONFLICT_RECORDED_AT,
            )
        ]


def test_intermediate_database_applies_the_remaining_ordered_migration(tmp_path: Path) -> None:
    database = tmp_path / "intermediate.sqlite3"
    _create_database(
        database,
        (*_LEGACY_SCHEMA, _CONFLICT_TABLE),
        version=INTERMEDIATE_DATABASE_VERSION,
    )

    SQLiteLifecycleLedger(database)

    assert _database_version(database) == CURRENT_DATABASE_VERSION
    assert {name for _, name, _, _ in _schema(database) if "advance_conflicts" in name} == {
        "advance_conflicts",
        "advance_conflicts_are_append_only_delete",
        "advance_conflicts_are_append_only_update",
    }


def test_unversioned_post_conflict_database_is_recognized_exactly(tmp_path: Path) -> None:
    database = tmp_path / "unversioned-current.sqlite3"
    _create_database(database, (*_LEGACY_SCHEMA, _CONFLICT_TABLE, *_CONFLICT_TRIGGERS))
    before = _schema(database)

    SQLiteLifecycleLedger(database)

    assert _database_version(database) == CURRENT_DATABASE_VERSION
    assert _schema(database) == before


def test_status_open_migrates_a_populated_legacy_database(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    state_root.mkdir(mode=0o700)
    database = state_root / "lifecycle.sqlite3"
    _create_database(database, _LEGACY_SCHEMA)
    database.chmod(0o600)
    _, identity = _populate_legacy_history(database)

    capability = configure_status(
        (
            ConfigurationSource(
                "test",
                {"schema_version": 1, "state_root": str(state_root)},
            ),
        ),
        repository_root=tmp_path / "repository",
    )

    assert isinstance(capability, Status)
    status = capability()
    assert _database_version(database) == CURRENT_DATABASE_VERSION
    assert status.pinned_run_identity == identity
    assert status.pinned_run_identity is not None
    assert status.pinned_run_identity.run_id == identity.run_id


def test_unversioned_internal_migration_shape_is_not_treated_as_a_legacy_release(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unversioned-intermediate.sqlite3"
    _create_database(database, (*_LEGACY_SCHEMA, _CONFLICT_TABLE))
    before = database.read_bytes()

    with pytest.raises(
        LifecyclePersistenceError,
        match="unrecognized unversioned SQLite schema",
    ):
        SQLiteLifecycleLedger(database)

    assert database.read_bytes() == before


def test_opening_a_current_database_is_a_no_op(tmp_path: Path) -> None:
    database = tmp_path / "current.sqlite3"
    SQLiteLifecycleLedger(database)
    before = database.read_bytes()

    SQLiteLifecycleLedger(database)

    assert database.read_bytes() == before


def test_projection_owned_table_and_index_are_outside_the_authoritative_signature(
    tmp_path: Path,
) -> None:
    database = tmp_path / "projection-owned-objects.sqlite3"
    SQLiteLifecycleLedger(database)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE lifecycle_status_projection (payload TEXT)")
        connection.execute(
            """
            CREATE INDEX lifecycle_status_projection_payload
            ON lifecycle_status_projection(payload)
            """
        )
    before = _schema(database)

    SQLiteLifecycleLedger(database)

    assert _database_version(database) == CURRENT_DATABASE_VERSION
    assert _schema(database) == before


def test_projection_only_index_corruption_is_ignored_then_rebuilt(tmp_path: Path) -> None:
    database = tmp_path / "corrupt-projection-index.sqlite3"
    SQLiteLifecycleLedger(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE lifecycle_status_projection (
                singleton INTEGER PRIMARY KEY,
                first_payload TEXT,
                second_payload TEXT
            ) STRICT
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX projection_first_payload
            ON lifecycle_status_projection(first_payload)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX projection_second_payload
            ON lifecycle_status_projection(second_payload)
            """
        )
        connection.execute("INSERT INTO lifecycle_status_projection VALUES (1, 'first', 'second')")
    _swap_index_roots(database, "projection_first_payload", "projection_second_payload")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
    before = database.read_bytes()

    ledger = SQLiteLifecycleLedger(database)

    assert database.read_bytes() == before
    ledger.rebuild_status()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


@pytest.mark.parametrize("unsupported_version", [-1, CURRENT_DATABASE_VERSION + 1])
def test_unsupported_database_version_fails_before_schema_or_history_changes(
    tmp_path: Path,
    unsupported_version: int,
) -> None:
    database = tmp_path / "unsupported-version.sqlite3"
    SQLiteLifecycleLedger(database)
    with sqlite3.connect(database) as connection:
        connection.execute(f"PRAGMA user_version = {unsupported_version}")
    before = _schema(database)

    with pytest.raises(
        LifecyclePersistenceError,
        match="unsupported SQLite database version",
    ):
        SQLiteLifecycleLedger(database)

    assert _database_version(database) == unsupported_version
    assert _schema(database) == before


def test_unrecognized_unversioned_schema_fails_without_guessing(tmp_path: Path) -> None:
    database = tmp_path / "malformed-legacy.sqlite3"
    _create_database(database, (_LEGACY_SCHEMA[0],))
    before = _schema(database)

    with pytest.raises(
        LifecyclePersistenceError,
        match="unrecognized unversioned SQLite schema",
    ):
        SQLiteLifecycleLedger(database)

    assert _database_version(database) == 0
    assert _schema(database) == before


def test_projection_name_does_not_hide_an_object_owned_by_authoritative_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "projection-name-collision.sqlite3"
    _create_database(database, _LEGACY_SCHEMA)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER lifecycle_status_projection
            BEFORE INSERT ON lifecycle_events
            BEGIN SELECT RAISE(ABORT, 'unexpected authoritative trigger'); END
            """
        )
    before = _schema(database)

    with pytest.raises(
        LifecyclePersistenceError,
        match="unrecognized unversioned SQLite schema",
    ):
        SQLiteLifecycleLedger(database)

    assert _database_version(database) == 0
    assert _schema(database) == before


def test_projection_name_does_not_hide_a_view_that_status_cannot_replace(
    tmp_path: Path,
) -> None:
    database = tmp_path / "projection-view-collision.sqlite3"
    _create_database(database, _LEGACY_SCHEMA)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE VIEW lifecycle_status_projection AS
            SELECT stream_id FROM lifecycle_events
            """
        )
    before = _schema(database)

    with pytest.raises(
        LifecyclePersistenceError,
        match="unrecognized unversioned SQLite schema",
    ):
        SQLiteLifecycleLedger(database)

    assert _database_version(database) == 0
    assert _schema(database) == before


def test_versioned_schema_mismatch_fails_before_migration(tmp_path: Path) -> None:
    database = tmp_path / "corrupt-input.sqlite3"
    _create_database(
        database,
        (*_LEGACY_SCHEMA, "CREATE TABLE advance_conflicts (unexpected TEXT) STRICT"),
        version=LEGACY_DATABASE_VERSION,
    )
    before = _schema(database)

    with pytest.raises(
        LifecyclePersistenceError,
        match="SQLite schema does not match database version",
    ):
        SQLiteLifecycleLedger(database)

    assert _database_version(database) == LEGACY_DATABASE_VERSION
    assert _schema(database) == before


def test_corrupt_migration_rows_fail_before_schema_or_version_changes(tmp_path: Path) -> None:
    database = tmp_path / "corrupt-rows.sqlite3"
    _create_database(database, _LEGACY_SCHEMA, version=LEGACY_DATABASE_VERSION)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            INSERT INTO advance_refusals (idempotency_key, reason_code, recorded_at)
            VALUES ('corrupt-row', 'not-a-reason', ?)
            """,
            (RECORDED_AT,),
        )
    before = _schema(database)

    with pytest.raises(
        LifecyclePersistenceError,
        match="SQLite database integrity check failed",
    ):
        SQLiteLifecycleLedger(database)

    assert _database_version(database) == LEGACY_DATABASE_VERSION
    assert _schema(database) == before


def test_index_content_corruption_fails_before_lifecycle_writes_resume(tmp_path: Path) -> None:
    database = tmp_path / "corrupt-index.sqlite3"
    _create_database(database, _LEGACY_SCHEMA, version=LEGACY_DATABASE_VERSION)
    _populate_legacy_history(database)
    before = _authoritative_rows(database)
    _swap_index_roots(
        database,
        "one_unkeyed_refusal_per_reason",
        "one_initial_event_per_stream",
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]

    with pytest.raises(
        LifecyclePersistenceError,
        match="SQLite database integrity check failed",
    ):
        SQLiteLifecycleLedger(database)

    assert _database_version(database) == LEGACY_DATABASE_VERSION
    assert _authoritative_rows(database) == before


def test_invalid_durable_key_fails_before_migration(tmp_path: Path) -> None:
    database = tmp_path / "invalid-key.sqlite3"
    _create_database(database, _LEGACY_SCHEMA, version=LEGACY_DATABASE_VERSION)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO advance_refusals (idempotency_key, reason_code, recorded_at)
            VALUES ('not valid', 'invalid_session', ?)
            """,
            (RECORDED_AT,),
        )

    with pytest.raises(
        LifecyclePersistenceError,
        match="invalid idempotency_key in lifecycle refusal ledger",
    ):
        SQLiteLifecycleLedger(database)

    assert _database_version(database) == LEGACY_DATABASE_VERSION


def test_orphan_conflict_fails_on_current_database_reopen(tmp_path: Path) -> None:
    database = tmp_path / "orphan-conflict.sqlite3"
    SQLiteLifecycleLedger(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO advance_conflicts (idempotency_key, reason_code, recorded_at)
            VALUES ('orphan', 'idempotency_key_conflict', ?)
            """,
            (RECORDED_AT,),
        )

    with pytest.raises(
        LifecyclePersistenceError,
        match="invalid conflict association",
    ):
        SQLiteLifecycleLedger(database)

    assert _database_version(database) == CURRENT_DATABASE_VERSION


def test_refusal_that_overlaps_a_completed_stream_fails_before_migration(
    tmp_path: Path,
) -> None:
    database = tmp_path / "overlapping-refusal.sqlite3"
    _create_database(database, _LEGACY_SCHEMA, version=LEGACY_DATABASE_VERSION)
    request, _ = _populate_legacy_history(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO advance_refusals (idempotency_key, reason_code, recorded_at)
            VALUES (?, 'invalid_session', ?)
            """,
            (request.idempotency_key.value, RECORDED_AT),
        )
    before = _authoritative_rows(database)

    with pytest.raises(
        LifecyclePersistenceError,
        match="invalid refusal association",
    ):
        SQLiteLifecycleLedger(database)

    assert _database_version(database) == LEGACY_DATABASE_VERSION
    assert _authoritative_rows(database) == before


def test_failed_migration_rolls_back_and_a_retry_reaches_current(
    tmp_path: Path,
) -> None:
    database = tmp_path / "retry.sqlite3"
    _create_database(database, _LEGACY_SCHEMA, version=LEGACY_DATABASE_VERSION)
    before_schema = _schema(database)
    database.chmod(0o400)
    try:
        with pytest.raises(
            LifecyclePersistenceError,
            match="SQLite database migration failed",
        ):
            SQLiteLifecycleLedger(database)
    finally:
        database.chmod(0o600)

    assert _database_version(database) == LEGACY_DATABASE_VERSION
    assert _schema(database) == before_schema

    SQLiteLifecycleLedger(database)

    assert _database_version(database) == CURRENT_DATABASE_VERSION


def test_failure_after_partial_migration_ddl_rolls_back_the_complete_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "partial-ddl-failure.sqlite3"
    _create_database(
        database,
        (*_LEGACY_SCHEMA, _CONFLICT_TABLE),
        version=INTERMEDIATE_DATABASE_VERSION,
    )
    before_schema = _schema(database)
    connect = sqlite3.connect

    def reject_delete_trigger(
        action: int,
        object_name: str | None,
        _table_name: str | None,
        _database_name: str | None,
        _trigger_name: str | None,
    ) -> int:
        if (
            action == sqlite3.SQLITE_CREATE_TRIGGER
            and object_name == "advance_conflicts_are_append_only_delete"
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def connect_with_authorizer(database_uri: str, *, uri: bool) -> sqlite3.Connection:
        connection = connect(database_uri, uri=uri)
        connection.set_authorizer(reject_delete_trigger)
        return connection

    with monkeypatch.context() as fault:
        fault.setattr(sqlite3, "connect", connect_with_authorizer)
        with pytest.raises(
            LifecyclePersistenceError,
            match="SQLite database migration failed",
        ):
            SQLiteLifecycleLedger(database)

    assert _database_version(database) == INTERMEDIATE_DATABASE_VERSION
    assert _schema(database) == before_schema

    SQLiteLifecycleLedger(database)

    assert _database_version(database) == CURRENT_DATABASE_VERSION


def test_migrated_append_only_objects_continue_to_reject_rewrites(tmp_path: Path) -> None:
    database = tmp_path / "append-only.sqlite3"
    _create_database(database, _LEGACY_SCHEMA)
    _populate_legacy_history(database)
    SQLiteLifecycleLedger(database)

    statements = (
        "UPDATE lifecycle_events SET mode = 'champion'",
        "DELETE FROM lifecycle_events",
        "UPDATE advance_refusals SET reason_code = 'invalid_session'",
        "DELETE FROM advance_refusals",
        (
            "INSERT INTO advance_conflicts VALUES "
            "('conflict', 'idempotency_key_conflict', '2026-08-21T22:00:00+00:00')"
        ),
    )
    with sqlite3.connect(database) as connection:
        connection.execute(statements[-1])
    for statement in (
        *statements[:-1],
        "UPDATE advance_conflicts SET reason_code = 'idempotency_key_conflict'",
        "DELETE FROM advance_conflicts",
    ):
        with (
            sqlite3.connect(database) as connection,
            pytest.raises(sqlite3.IntegrityError, match="append-only"),
        ):
            connection.execute(statement)


def test_migration_diagnostics_are_bounded_for_corrupt_database_input(tmp_path: Path) -> None:
    database = tmp_path / "not-a-database.sqlite3"
    database.write_text("hostile" * 2_000, encoding="utf-8")

    with pytest.raises(LifecyclePersistenceError) as captured:
        SQLiteLifecycleLedger(database)

    assert str(captured.value) == "SQLite database migration failed"
    assert len(str(captured.value)) < MAX_DIAGNOSTIC_LENGTH


def test_database_schema_version_is_independent_of_run_configuration_version(
    tmp_path: Path,
) -> None:
    database = tmp_path / "independent-versions.sqlite3"
    ledger = SQLiteLifecycleLedger(database)
    request = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key="independent-versions",
    )
    assert isinstance(request, AdvanceRequest)
    identity = PinnedRunIdentity.create(
        request,
        configuration_version=1,
        configuration_hash=CONFIGURATION_HASH,
    )

    ledger.start(request, identity, datetime(2026, 8, 21, 22, 0, tzinfo=UTC))

    with sqlite3.connect(database) as connection:
        stored = connection.execute("SELECT configuration_version FROM lifecycle_events").fetchone()
    assert stored == (1,)
    assert _database_version(database) == CURRENT_DATABASE_VERSION
    assert _database_version(database) != identity.configuration_version
    assert ledger.load_by_idempotency_key(IdempotencyKey(request.idempotency_key.value)) is not None
