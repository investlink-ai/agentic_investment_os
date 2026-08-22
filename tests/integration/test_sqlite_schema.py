from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from agentic_investment_os.adapters.sqlite_lifecycle import SQLiteLifecycleLedger
from agentic_investment_os.domain.lifecycle import (
    AdvanceFailureReason,
    AdvanceRequest,
    IdempotencyKey,
    LifecyclePersistenceError,
    LifecyclePhase,
    PinnedRunIdentity,
)

CURRENT_DATABASE_VERSION = 1
CONFIGURATION_HASH = "a" * 64
RECORDED_AT = "2026-08-21T22:00:00+00:00"
MAX_DIAGNOSTIC_LENGTH = 100


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


def _populate_current_history(database: Path) -> tuple[AdvanceRequest, PinnedRunIdentity]:
    request = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key="current-complete",
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
            VALUES ('current-refusal', 'invalid_session', ?)
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


def test_unversioned_nonempty_database_is_not_supported(tmp_path: Path) -> None:
    database = tmp_path / "unversioned.sqlite3"
    SQLiteLifecycleLedger(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 0")
    before = database.read_bytes()

    with pytest.raises(
        LifecyclePersistenceError,
        match="unsupported SQLite database version",
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


@pytest.mark.parametrize("unsupported_version", [2, 99])
def test_unsupported_database_version_fails_before_schema_or_history_changes(
    tmp_path: Path,
    unsupported_version: int,
) -> None:
    database = tmp_path / "unsupported-version.sqlite3"
    SQLiteLifecycleLedger(database)
    with sqlite3.connect(database) as connection:
        connection.execute(f"PRAGMA user_version = {unsupported_version}")
    before = database.read_bytes()

    with pytest.raises(
        LifecyclePersistenceError,
        match="unsupported SQLite database version",
    ):
        SQLiteLifecycleLedger(database)

    assert database.read_bytes() == before


def test_projection_name_does_not_hide_an_object_owned_by_authoritative_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "projection-name-collision.sqlite3"
    SQLiteLifecycleLedger(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER lifecycle_status_projection
            BEFORE INSERT ON lifecycle_events
            BEGIN SELECT RAISE(ABORT, 'unexpected authoritative trigger'); END
            """
        )
    before = database.read_bytes()

    with pytest.raises(
        LifecyclePersistenceError,
        match="SQLite schema does not match database version",
    ):
        SQLiteLifecycleLedger(database)

    assert database.read_bytes() == before


def test_projection_name_does_not_hide_a_view_that_status_cannot_replace(
    tmp_path: Path,
) -> None:
    database = tmp_path / "projection-view-collision.sqlite3"
    SQLiteLifecycleLedger(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE VIEW lifecycle_status_projection AS
            SELECT stream_id FROM lifecycle_events
            """
        )
    before = database.read_bytes()

    with pytest.raises(
        LifecyclePersistenceError,
        match="SQLite schema does not match database version",
    ):
        SQLiteLifecycleLedger(database)

    assert database.read_bytes() == before


def test_current_version_with_a_different_schema_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "schema-mismatch.sqlite3"
    SQLiteLifecycleLedger(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER advance_conflicts_are_append_only_delete")
    before = database.read_bytes()

    with pytest.raises(
        LifecyclePersistenceError,
        match="SQLite schema does not match database version",
    ):
        SQLiteLifecycleLedger(database)

    assert database.read_bytes() == before


def test_corrupt_current_rows_fail_before_lifecycle_writes_resume(tmp_path: Path) -> None:
    database = tmp_path / "corrupt-rows.sqlite3"
    SQLiteLifecycleLedger(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            INSERT INTO advance_refusals (idempotency_key, reason_code, recorded_at)
            VALUES ('corrupt-row', 'not-a-reason', ?)
            """,
            (RECORDED_AT,),
        )
    before = database.read_bytes()

    with pytest.raises(
        LifecyclePersistenceError,
        match="SQLite database integrity check failed",
    ):
        SQLiteLifecycleLedger(database)

    assert database.read_bytes() == before


def test_index_content_corruption_fails_before_lifecycle_writes_resume(tmp_path: Path) -> None:
    database = tmp_path / "corrupt-index.sqlite3"
    SQLiteLifecycleLedger(database)
    _populate_current_history(database)
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

    assert _database_version(database) == CURRENT_DATABASE_VERSION
    assert _authoritative_rows(database) == before


def test_invalid_durable_key_fails_current_schema_validation(tmp_path: Path) -> None:
    database = tmp_path / "invalid-key.sqlite3"
    SQLiteLifecycleLedger(database)
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

    assert _database_version(database) == CURRENT_DATABASE_VERSION


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


def test_refusal_that_overlaps_a_completed_stream_fails_current_validation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "overlapping-refusal.sqlite3"
    SQLiteLifecycleLedger(database)
    request, _ = _populate_current_history(database)
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

    assert _database_version(database) == CURRENT_DATABASE_VERSION
    assert _authoritative_rows(database) == before


def test_failed_fresh_initialization_rolls_back_and_retries_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "initialization-failure.sqlite3"
    connect = sqlite3.connect

    def reject_conflict_table(
        action: int,
        object_name: str | None,
        _table_name: str | None,
        _database_name: str | None,
        _trigger_name: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_CREATE_TABLE and object_name == "advance_conflicts":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def connect_with_authorizer(database_uri: str, *, uri: bool) -> sqlite3.Connection:
        connection = connect(database_uri, uri=uri)
        connection.set_authorizer(reject_conflict_table)
        return connection

    with monkeypatch.context() as fault:
        fault.setattr(sqlite3, "connect", connect_with_authorizer)
        with pytest.raises(
            LifecyclePersistenceError,
            match="SQLite database initialization failed",
        ):
            SQLiteLifecycleLedger(database)

    assert _database_version(database) == 0
    assert _schema(database) == []

    SQLiteLifecycleLedger(database)

    assert _database_version(database) == CURRENT_DATABASE_VERSION


def test_current_append_only_objects_continue_to_reject_rewrites(tmp_path: Path) -> None:
    database = tmp_path / "append-only.sqlite3"
    SQLiteLifecycleLedger(database)
    _populate_current_history(database)

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


def test_initialization_diagnostic_is_bounded_for_corrupt_database_input(
    tmp_path: Path,
) -> None:
    database = tmp_path / "not-a-database.sqlite3"
    database.write_text("hostile" * 2_000, encoding="utf-8")

    with pytest.raises(LifecyclePersistenceError) as captured:
        SQLiteLifecycleLedger(database)

    assert str(captured.value) == "SQLite database initialization failed"
    assert len(str(captured.value)) < MAX_DIAGNOSTIC_LENGTH


def test_current_ledger_remains_usable_after_startup_validation(tmp_path: Path) -> None:
    database = tmp_path / "usable.sqlite3"
    ledger = SQLiteLifecycleLedger(database)
    refusal = ledger.record_refusal(
        IdempotencyKey("usable-refusal"),
        AdvanceFailureReason.INVALID_SESSION,
        datetime(2026, 8, 21, 22, 0, tzinfo=UTC),
    )

    reopened = SQLiteLifecycleLedger(database)

    assert refusal.failure_reason is AdvanceFailureReason.INVALID_SESSION
    assert reopened.load_by_idempotency_key(IdempotencyKey("usable-refusal")) is not None
