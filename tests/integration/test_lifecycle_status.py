from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from agentic_investment_os.adapters.sqlite_lifecycle import (
    PreparedRuntimeDatabase,
    RuntimeRootRefusal,
    open_runtime_database,
    prepare_runtime_database,
)
from agentic_investment_os.application.lifecycle import Advance, Status
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.lifecycle import (
    AdvanceFailureReason,
    InvalidLifecycleStateError,
    LifecycleLiveness,
    LifecyclePersistenceError,
    LifecyclePhase,
    LifecycleStatus,
)
from agentic_investment_os.entrypoints.configuration import (
    ConfigurationRefusal,
    ConfigurationRefusalCode,
    ConfigurationSource,
)
from agentic_investment_os.entrypoints.lifecycle import configure_advance, configure_status
from tests._universe import recorded_universe, runtime_configuration

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _cycle_payload(value: object) -> object:
    if type(value) is not str:
        return value
    try:
        trading_date = date.fromisoformat(value)
    except ValueError:
        return value
    return MarketSession(trading_date).to_payload()


@dataclass(frozen=True)
class FixedClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


def _sources(state_root: Path) -> tuple[ConfigurationSource, ...]:
    return (
        ConfigurationSource(
            "test",
            runtime_configuration(state_root),
        ),
    )


def _advance(state_root: Path) -> Advance:
    return _advance_at(state_root, datetime(2026, 8, 21, 22, 0, tzinfo=UTC))


def _advance_at(state_root: Path, instant: datetime) -> Advance:
    capability = configure_advance(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        clock=FixedClock(instant),
    )
    assert isinstance(capability, Advance)
    return capability


def _status(state_root: Path) -> Status:
    capability = configure_status(_sources(state_root), repository_root=REPOSITORY_ROOT)
    assert isinstance(capability, Status)
    return capability


def _projection(database: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT * FROM lifecycle_status_projection ORDER BY singleton"
        ).fetchall()


def _authoritative_counts(database: Path) -> tuple[int, int, int]:
    with sqlite3.connect(database) as connection:
        event_count = connection.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()
        refusal_count = connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone()
        conflict_count = connection.execute("SELECT COUNT(*) FROM advance_conflicts").fetchone()
    assert event_count is not None
    assert refusal_count is not None
    assert conflict_count is not None
    return int(event_count[0]), int(refusal_count[0]), int(conflict_count[0])


def test_database_preparation_reports_whether_it_created_authoritative_storage(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    state_root.mkdir(mode=0o700)

    created = prepare_runtime_database(state_root)
    assert isinstance(created, PreparedRuntimeDatabase)
    assert created.created is True
    assert created.path.exists()

    existing = prepare_runtime_database(state_root)
    assert isinstance(existing, PreparedRuntimeDatabase)
    assert existing.created is False
    assert existing.path == created.path

    opened = open_runtime_database(state_root)
    assert isinstance(opened, PreparedRuntimeDatabase)
    assert opened.created is False
    assert opened.path == created.path

    missing_root = tmp_path / "missing-database"
    missing_root.mkdir(mode=0o700)
    missing = open_runtime_database(missing_root)
    assert isinstance(missing, PreparedRuntimeDatabase)
    assert missing.created is False
    assert not missing.path.exists()


def test_database_preparation_refuses_a_database_create_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "runtime"
    state_root.mkdir(mode=0o700)

    def fail_create(_path: object, _flags: int, _mode: int) -> int:
        raise OSError

    monkeypatch.setattr(os, "open", fail_create)

    assert isinstance(prepare_runtime_database(state_root), RuntimeRootRefusal)


def test_status_reports_empty_incomplete_and_universe_snapshot_history(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    status = _status(state_root)
    assert status() == LifecycleStatus.not_started()
    advance = _advance(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER stop_after_start
            BEFORE INSERT ON lifecycle_events
            WHEN NEW.event_kind = 'phase_completed'
            BEGIN
                SELECT RAISE(ABORT, 'injected interruption');
            END
            """
        )

    with pytest.raises(LifecyclePersistenceError, match="SQLite lifecycle checkpoint failed"):
        advance(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="status-session",
        )

    incomplete = status()
    assert incomplete.liveness is LifecycleLiveness.ACTIVE
    assert incomplete.active_phase is not None
    assert incomplete.active_phase.phase is LifecyclePhase.RECONCILE_PRIOR_STATE
    assert incomplete.last_completed_cycle is None
    assert incomplete.universe_snapshot_cycle is None
    assert incomplete.pinned_run_identity is not None
    assert incomplete.durable_reason is None
    projection = _projection(database)
    assert projection[0][1] == json.dumps(
        incomplete.active_phase.to_payload(),
        sort_keys=True,
        separators=(",", ":"),
    )

    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER stop_after_start")
        connection.execute(
            """
            CREATE TRIGGER stop_before_pin
            BEFORE INSERT ON lifecycle_events
            WHEN NEW.event_kind = 'run_inputs_pinned'
            BEGIN
                SELECT RAISE(ABORT, 'injected interruption');
            END
            """
        )
    with pytest.raises(LifecyclePersistenceError, match="SQLite lifecycle checkpoint failed"):
        advance(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="status-session",
        )

    reconciled = status()
    assert reconciled.liveness is LifecycleLiveness.ACTIVE
    assert reconciled.active_phase is not None
    assert reconciled.active_phase.phase is LifecyclePhase.PIN_RUN_INPUTS

    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER stop_before_pin")
    receipt = advance(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="status-session",
    )

    pinned = status()
    assert pinned.liveness is LifecycleLiveness.ACTIVE
    assert pinned.active_phase is None
    assert pinned.last_completed_cycle is None
    assert pinned.universe_snapshot_cycle == MarketSession(date(2026, 8, 21))
    assert pinned.pinned_run_identity == receipt.pinned_run_identity
    assert pinned.durable_reason is None
    assert pinned.universe_snapshot_id == receipt.universe_snapshot_id
    assert receipt.pinned_run_identity is not None
    assert _projection(database) == [
        (
            1,
            None,
            None,
            receipt.pinned_run_identity.run_id,
            receipt.pinned_run_identity.configuration_version,
            receipt.pinned_run_identity.configuration_hash,
            receipt.pinned_run_identity.data_regime,
            receipt.pinned_run_identity.evidence_cutoff.isoformat(),
            receipt.pinned_run_identity.instrument_snapshot_hash,
            receipt.pinned_run_identity.position_snapshot_hash,
            receipt.pinned_run_identity.eligibility_policy_hash,
            "active",
            None,
            (
                '{"asset_class":"us_equity","cycle_type":"market_session",'
                '"payload":{"trading_date":"2026-08-21"},'
                '"payload_schema_version":1,"schema_version":1}'
            ),
            receipt.universe_snapshot_id,
        )
    ]


def test_status_exposes_a_durable_fail_closed_reason(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    _advance(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="invalid",
        idempotency_key="status-refusal",
    )

    status = _status(state_root)()

    assert status == LifecycleStatus(
        active_phase=None,
        last_completed_cycle=None,
        universe_snapshot_cycle=None,
        pinned_run_identity=None,
        liveness=LifecycleLiveness.FAILED_CLOSED,
        durable_reason=AdvanceFailureReason.INVALID_MODE,
        universe_snapshot_id=None,
    )

    _advance(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="not valid",
    )
    assert _status(state_root)().durable_reason is AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY


def test_status_retains_pinned_identity_for_a_terminal_partial_stream(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    advance = _advance_at(state_root, datetime(2026, 8, 21, 23, 0, tzinfo=UTC))
    refusal = _advance_at(state_root, datetime(2026, 8, 21, 22, 0, tzinfo=UTC))
    invalid_key = _advance_at(state_root, datetime(2026, 8, 21, 23, 30, tzinfo=UTC))
    status_capability = _status(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER stop_after_start
            BEFORE INSERT ON lifecycle_events
            WHEN NEW.event_kind = 'phase_completed'
            BEGIN
                SELECT RAISE(ABORT, 'injected interruption');
            END
            """
        )
    with pytest.raises(LifecyclePersistenceError):
        advance(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="terminal-partial",
        )
    refusal(
        cycle=_cycle_payload("2026-08-21"),
        mode="invalid",
        idempotency_key="terminal-partial",
    )
    invalid_key(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="not valid",
    )

    status = status_capability()

    assert status.liveness is LifecycleLiveness.FAILED_CLOSED
    assert status.active_phase is None
    assert status.pinned_run_identity is not None
    assert status.durable_reason is AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT


def test_status_reports_an_unrelated_refusal_without_terminating_the_current_stream(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    advance = _advance_at(state_root, datetime(2026, 8, 21, 23, 0, tzinfo=UTC))
    receipt = advance(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="active-before-refusal",
    )
    _advance_at(state_root, datetime(2026, 8, 21, 21, 0, tzinfo=UTC))(
        cycle=_cycle_payload("2026-08-22"),
        mode="invalid",
        idempotency_key="later-refusal",
    )

    status = _status(state_root)()

    assert status.liveness is LifecycleLiveness.ACTIVE
    assert status.active_phase is None
    assert status.pinned_run_identity == receipt.pinned_run_identity
    assert status.durable_reason is AdvanceFailureReason.INVALID_MODE


def test_status_reports_completed_conflicts_without_terminating_a_stream(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    advance = _advance(state_root)
    original = advance(
        cycle=_cycle_payload("2026-08-01"),
        mode="champion",
        idempotency_key="completed-before-conflict",
    )
    conflict = advance(
        cycle=_cycle_payload("2026-08-02"),
        mode="champion",
        idempotency_key="completed-before-conflict",
    )

    assert conflict.failure_reason is AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
    conflicted_status = _status(state_root)()
    assert conflicted_status.liveness is LifecycleLiveness.ACTIVE
    assert conflicted_status.active_phase is None
    assert conflicted_status.pinned_run_identity == original.pinned_run_identity
    assert conflicted_status.durable_reason is AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT

    latest = advance(
        cycle=_cycle_payload("2026-08-02"),
        mode="champion",
        idempotency_key="latest-clean-stream",
    )

    latest_status = _status(state_root)()
    assert latest_status.liveness is LifecycleLiveness.ACTIVE
    assert latest_status.pinned_run_identity == latest.pinned_run_identity
    assert latest_status.durable_reason is AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT


def test_status_selects_the_latest_market_session_independent_of_stream_order(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    advance = _advance(state_root)
    advance(
        cycle=_cycle_payload("2026-08-01"),
        mode="champion",
        idempotency_key="earlier-session",
    )
    latest = advance(
        cycle=_cycle_payload("2026-08-02"),
        mode="champion",
        idempotency_key="latest-session",
    )

    status = _status(state_root)()

    assert status.pinned_run_identity == latest.pinned_run_identity


def test_status_rebuild_replaces_deleted_or_corrupt_projection_without_mutating_history(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    _advance(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="projection-rebuild",
    )
    capability = _status(state_root)
    expected = capability()
    database = state_root / "lifecycle.sqlite3"
    expected_projection = _projection(database)
    expected_counts = _authoritative_counts(database)

    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM lifecycle_status_projection")
    assert capability() == expected
    assert _projection(database) == expected_projection

    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE lifecycle_status_projection")
        connection.execute("CREATE TABLE lifecycle_status_projection (payload TEXT)")
        connection.execute("INSERT INTO lifecycle_status_projection VALUES ('manufactured')")
    assert capability() == expected
    assert _projection(database) == expected_projection
    assert _authoritative_counts(database) == expected_counts

    assert capability() == expected
    assert _projection(database) == expected_projection
    assert _authoritative_counts(database) == expected_counts


def test_status_rejects_corrupt_authoritative_history_instead_of_using_projection(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    _advance(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="corrupt-status-history",
    )
    capability = _status(state_root)
    healthy = capability()
    assert healthy.liveness is LifecycleLiveness.ACTIVE
    database = state_root / "lifecycle.sqlite3"

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT stream_id, sequence, idempotency_key, cycle_identity, mode,
                   configuration_version, configuration_hash, run_id,
                   data_regime, evidence_cutoff, instrument_snapshot_hash,
                   position_snapshot_hash, eligibility_policy_hash,
                   event_kind, completed_phase, universe_snapshot_id,
                   universe_snapshot, event_envelope, recorded_at
            FROM lifecycle_events ORDER BY sequence
            """
        ).fetchall()
        connection.execute("DROP TABLE lifecycle_events")
        connection.execute(
            """
            CREATE TABLE lifecycle_events (
                stream_id, sequence, idempotency_key, cycle_identity, mode,
                configuration_version, configuration_hash, run_id,
                data_regime, evidence_cutoff, instrument_snapshot_hash,
                position_snapshot_hash, eligibility_policy_hash,
                event_kind, completed_phase, universe_snapshot_id,
                universe_snapshot, event_envelope, recorded_at
            )
            """
        )
        connection.executemany(
            "INSERT INTO lifecycle_events VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.execute(
            "UPDATE lifecycle_events SET completed_phase = 'PinRunInputs' WHERE sequence = 1"
        )

    corrupt_rows_before = _authoritative_counts(database)
    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream checkpoint order is invalid",
    ):
        capability()

    assert _authoritative_counts(database) == corrupt_rows_before


def test_status_rejects_one_idempotency_key_across_multiple_streams(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    advance = _advance(state_root)
    advance(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="first-stream",
    )
    advance(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="second-stream",
    )
    status = _status(state_root)
    database = state_root / "lifecycle.sqlite3"
    _replace_event_table(
        database,
        "UPDATE lifecycle_events SET idempotency_key = 'first-stream' "
        "WHERE cycle_identity LIKE '%2026-08-22%'",
    )

    with pytest.raises(InvalidLifecycleStateError, match="checkpoint order is invalid"):
        status()


def test_status_rejects_a_refusal_for_a_completed_stream(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    advance = _advance(state_root)
    advance(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="not valid",
    )
    advance(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="completed-with-refusal",
    )
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO advance_refusals (idempotency_key, reason_code, recorded_at)
            VALUES (
                'completed-with-refusal',
                'invalid_session',
                '2026-08-21T22:00:00.000000+00:00'
            )
            """
        )

    with pytest.raises(InvalidLifecycleStateError, match="invalid refusal association"):
        _status(state_root)()


def test_status_rejects_an_invalid_refusal_reason_for_a_partial_stream(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    advance = _advance(state_root)
    status = _status(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER stop_after_start
            BEFORE INSERT ON lifecycle_events
            WHEN NEW.event_kind = 'phase_completed'
            BEGIN
                SELECT RAISE(ABORT, 'injected interruption');
            END
            """
        )
    with pytest.raises(LifecyclePersistenceError):
        advance(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="partial-with-invalid-refusal",
        )
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO advance_refusals (idempotency_key, reason_code, recorded_at)
            VALUES (
                'partial-with-invalid-refusal',
                'invalid_session',
                '2026-08-21T22:00:00.000000+00:00'
            )
            """
        )

    with pytest.raises(InvalidLifecycleStateError, match="invalid refusal association"):
        status()


@pytest.mark.parametrize(
    ("corruption", "expected_message"),
    [
        ("UPDATE advance_refusals SET refusal_id = 2", "refusal order is invalid"),
        (
            "UPDATE advance_refusals SET refusal_id = 'invalid'",
            "invalid refusal_id in lifecycle ledger",
        ),
        (
            "UPDATE advance_refusals SET idempotency_key = 'not valid'",
            "invalid idempotency_key in lifecycle refusal ledger",
        ),
        (
            "UPDATE advance_refusals SET idempotency_key = NULL",
            "unkeyed lifecycle refusal reason is invalid",
        ),
        (
            "UPDATE advance_refusals SET reason_code = 'invalid_idempotency_key'",
            "unkeyed lifecycle refusal reason is invalid",
        ),
        (
            "UPDATE advance_refusals SET reason_code = 'idempotency_key_conflict'",
            "invalid refusal association",
        ),
        (
            "UPDATE advance_refusals SET reason_code = 'invalid_durable_state'",
            "invalid refusal association",
        ),
        (
            """
                INSERT INTO advance_refusals
                SELECT 2, idempotency_key, cycle_identity, reason_code, recorded_at
                FROM advance_refusals
                """,
            "refusal uniqueness is invalid",
        ),
    ],
)
def test_status_rejects_corrupt_refusal_history(
    tmp_path: Path,
    corruption: str,
    expected_message: str,
) -> None:
    state_root = tmp_path / "runtime"
    _advance(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="invalid",
        idempotency_key="corrupt-status-refusal",
    )
    status = _status(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT refusal_id, idempotency_key, cycle_identity, reason_code, recorded_at "
            "FROM advance_refusals"
        ).fetchall()
        connection.execute("DROP TABLE advance_refusals")
        connection.execute(
            "CREATE TABLE advance_refusals "
            "(refusal_id, idempotency_key, cycle_identity, reason_code, recorded_at)"
        )
        connection.executemany("INSERT INTO advance_refusals VALUES (?, ?, ?, ?, ?)", rows)
        connection.execute(corruption)

    with pytest.raises(InvalidLifecycleStateError, match=expected_message):
        status()


def test_status_rejects_duplicate_unkeyed_refusal_authority(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    _advance(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="not valid",
    )
    status = _status(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT refusal_id, idempotency_key, cycle_identity, reason_code, recorded_at "
            "FROM advance_refusals"
        ).fetchall()
        connection.execute("DROP TABLE advance_refusals")
        connection.execute(
            "CREATE TABLE advance_refusals "
            "(refusal_id, idempotency_key, cycle_identity, reason_code, recorded_at)"
        )
        connection.executemany("INSERT INTO advance_refusals VALUES (?, ?, ?, ?, ?)", rows)
        connection.execute(
            """
            INSERT INTO advance_refusals
            SELECT 2, idempotency_key, cycle_identity, reason_code, recorded_at
            FROM advance_refusals
            """
        )

    with pytest.raises(InvalidLifecycleStateError, match="refusal uniqueness is invalid"):
        status()


@pytest.mark.parametrize(
    ("corruption", "expected_message"),
    [
        (
            "UPDATE advance_conflicts SET idempotency_key = 'not valid'",
            "invalid idempotency_key in lifecycle conflict ledger",
        ),
        (
            "UPDATE advance_conflicts SET idempotency_key = NULL",
            "invalid idempotency_key in lifecycle conflict ledger",
        ),
        (
            "UPDATE advance_conflicts SET idempotency_key = 'orphan-conflict'",
            "invalid conflict association",
        ),
        (
            "UPDATE advance_conflicts SET reason_code = 'invalid_session'",
            "invalid conflict association",
        ),
        (
            "UPDATE advance_conflicts SET recorded_at = 'not-a-timestamp'",
            "recorded_at must use canonical UTC format",
        ),
        (
            """
            INSERT INTO advance_conflicts
            SELECT idempotency_key, reason_code, recorded_at FROM advance_conflicts
            """,
            "conflict uniqueness is invalid",
        ),
    ],
)
def test_status_rejects_corrupt_conflict_history(
    tmp_path: Path,
    corruption: str,
    expected_message: str,
) -> None:
    state_root = tmp_path / "runtime"
    advance = _advance(state_root)
    advance(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="corrupt-status-conflict",
    )
    advance(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="corrupt-status-conflict",
    )
    status = _status(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT idempotency_key, reason_code, recorded_at FROM advance_conflicts"
        ).fetchall()
        connection.execute("DROP TABLE advance_conflicts")
        connection.execute(
            "CREATE TABLE advance_conflicts (idempotency_key, reason_code, recorded_at)"
        )
        connection.executemany("INSERT INTO advance_conflicts VALUES (?, ?, ?)", rows)
        connection.execute(corruption)

    with pytest.raises(InvalidLifecycleStateError, match=expected_message):
        status()


def test_status_configuration_refuses_invalid_configuration_and_runtime_root(
    tmp_path: Path,
) -> None:
    invalid_configuration = configure_status(
        (
            ConfigurationSource(
                "test",
                {**runtime_configuration(tmp_path / "runtime"), "schema_version": 2},
            ),
        ),
        repository_root=REPOSITORY_ROOT,
    )
    assert isinstance(invalid_configuration, ConfigurationRefusal)

    public_root = tmp_path / "public-runtime"
    public_root.mkdir(mode=0o755)
    invalid_root = configure_status(_sources(public_root), repository_root=REPOSITORY_ROOT)
    assert invalid_root == ConfigurationRefusal(
        code=ConfigurationRefusalCode.INVALID_STATE_ROOT,
        fields=("state_root",),
    )


def test_status_does_not_recreate_missing_authoritative_tables(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    _advance(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="missing-authoritative-history",
    )
    status = _status(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE lifecycle_events")
        connection.execute("DROP TABLE advance_refusals")
        connection.execute("DROP TABLE advance_conflicts")

    with pytest.raises(LifecyclePersistenceError, match="SQLite lifecycle checkpoint failed"):
        status()

    with sqlite3.connect(database) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert "lifecycle_events" not in tables
    assert "advance_refusals" not in tables
    assert "advance_conflicts" not in tables


def test_status_does_not_recreate_a_missing_authoritative_database(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    _advance(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="missing-authoritative-database",
    )
    database = state_root / "lifecycle.sqlite3"
    database.unlink()

    with pytest.raises(LifecyclePersistenceError, match="SQLite lifecycle checkpoint failed"):
        _status(state_root)()

    assert not database.exists()


def test_status_bounds_an_invalid_stream_identifier_diagnostic(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    _advance(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="invalid-stream-id",
    )
    status = _status(state_root)
    database = state_root / "lifecycle.sqlite3"
    _replace_event_table(database, "UPDATE lifecycle_events SET stream_id = 3")

    with pytest.raises(InvalidLifecycleStateError, match="invalid stream_id in lifecycle ledger"):
        status()


def _replace_event_table(database: Path, statement: str) -> None:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT stream_id, sequence, idempotency_key, cycle_identity, mode,
                   configuration_version, configuration_hash, run_id,
                   data_regime, evidence_cutoff, instrument_snapshot_hash,
                   position_snapshot_hash, eligibility_policy_hash,
                   event_kind, completed_phase, universe_snapshot_id,
                   universe_snapshot, event_envelope, recorded_at
            FROM lifecycle_events ORDER BY stream_id, sequence
            """
        ).fetchall()
        connection.execute("DROP TABLE lifecycle_events")
        connection.execute(
            """
            CREATE TABLE lifecycle_events (
                stream_id, sequence, idempotency_key, cycle_identity, mode,
                configuration_version, configuration_hash, run_id,
                data_regime, evidence_cutoff, instrument_snapshot_hash,
                position_snapshot_hash, eligibility_policy_hash,
                event_kind, completed_phase, universe_snapshot_id,
                universe_snapshot, event_envelope, recorded_at
            )
            """
        )
        connection.executemany(
            "INSERT INTO lifecycle_events VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.execute(statement)
