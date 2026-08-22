"""Persist domain-selected lifecycle records in a private SQLite database."""

from __future__ import annotations

import os
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Self, TypeVar

from agentic_investment_os.domain.lifecycle import (
    AdvanceAttempt,
    AdvanceFailureReason,
    AdvanceRequest,
    AppendLifecycleRecord,
    AppendTerminalLifecycleRecord,
    DurableAdvanceConflict,
    DurableAdvanceRefusal,
    IdempotencyKey,
    InputRefusal,
    InvalidLifecycleStateError,
    LifecycleCommand,
    LifecycleDecision,
    LifecycleEvent,
    LifecycleEventKind,
    LifecycleHistory,
    LifecyclePersistenceError,
    LifecyclePhase,
    LifecycleRecord,
    LifecycleStatus,
    PinnedRunIdentity,
    decide_advance,
    decide_invalid_history,
    decide_terminal_refusal,
    derive_lifecycle_status,
    is_sha256,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_DATABASE_NAME = "lifecycle.sqlite3"
_T = TypeVar("_T")
_PRIVATE_DATABASE_FLAGS = os.O_CREAT | os.O_EXCL | os.O_WRONLY
_STREAM_EXISTS_SQL = "SELECT 1 FROM lifecycle_events WHERE stream_id = ? LIMIT 1"
_NEXT_REFUSAL_SEQUENCE_SQL = "SELECT COALESCE(MAX(refusal_id), 0) + 1 FROM advance_refusals"
_ENABLE_FOREIGN_KEYS_SQL = "PRAGMA foreign_keys = ON"
_BUSY_TIMEOUT_SQL = "PRAGMA busy_timeout = 5000"
_BEGIN_IMMEDIATE_SQL = "BEGIN IMMEDIATE"
_DROP_PROJECTION_SQL = (
    "DROP TABLE IF EXISTS lifecycle_status_projection"  # pragma: no mutate
    # SQLite keywords and identifiers are case-insensitive, so case-only mutants are equivalent.
)
_USER_VERSION_SQL = "PRAGMA user_version"
_USER_SCHEMA_OBJECT_EXISTS_SQL = """
SELECT EXISTS (
    SELECT 1 FROM sqlite_schema
    WHERE name NOT LIKE 'sqlite!_%' ESCAPE '!'
)
"""
_INTEGRITY_CHECK_SQL = "PRAGMA integrity_check"
_INTEGRITY_SAVEPOINT_SQL = "SAVEPOINT validate_without_projection"
_ROLLBACK_INTEGRITY_SAVEPOINT_SQL = "ROLLBACK TO validate_without_projection"
_RELEASE_INTEGRITY_SAVEPOINT_SQL = "RELEASE validate_without_projection"
_CHECKPOINT_FAILED = "SQLite lifecycle checkpoint failed"
_DATABASE_INITIALIZATION_FAILED = "SQLite database initialization failed"
_UNSUPPORTED_DATABASE_VERSION = "unsupported SQLite database version"
_SCHEMA_VERSION_MISMATCH = "SQLite schema does not match database version"
_DATABASE_INTEGRITY_FAILED = "SQLite database integrity check failed"
_INVALID_REQUEST = "invalid request values in lifecycle ledger"
_INVALID_RECORDED_AT = "invalid recorded_at in lifecycle ledger"
_UNKNOWN_REASON_CODE = "unknown reason_code in lifecycle ledger"
_INVALID_REFUSAL_KEY = "invalid idempotency_key in lifecycle refusal ledger"
_INVALID_CONFLICT_KEY = "invalid idempotency_key in lifecycle conflict ledger"
_RECORDED_AT_NOT_AWARE = "recorded_at must be timezone-aware"
_CLOCK_NOT_AWARE = "lifecycle clock must return a timezone-aware timestamp"
_INVALID_CHECKPOINT_ORDER = "lifecycle stream checkpoint order is invalid"

_CURRENT_SCHEMA = (
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
BEFORE UPDATE ON lifecycle_events BEGIN SELECT RAISE(ABORT, 'append-only lifecycle ledger'); END
""",
    """
CREATE TRIGGER lifecycle_events_are_append_only_delete
BEFORE DELETE ON lifecycle_events BEGIN SELECT RAISE(ABORT, 'append-only lifecycle ledger'); END
""",
    """
CREATE TRIGGER advance_refusals_are_append_only_update
BEFORE UPDATE ON advance_refusals BEGIN SELECT RAISE(ABORT, 'append-only refusal ledger'); END
    """,
    """
CREATE TRIGGER advance_refusals_are_append_only_delete
BEFORE DELETE ON advance_refusals BEGIN SELECT RAISE(ABORT, 'append-only refusal ledger'); END
""",
    """
CREATE TABLE advance_conflicts (
    idempotency_key TEXT PRIMARY KEY,
    reason_code TEXT NOT NULL CHECK (reason_code = 'idempotency_key_conflict'),
    recorded_at TEXT NOT NULL
) STRICT
""",
    """
CREATE TRIGGER advance_conflicts_are_append_only_update
BEFORE UPDATE ON advance_conflicts BEGIN SELECT RAISE(ABORT, 'append-only conflict ledger'); END
""",
    """
CREATE TRIGGER advance_conflicts_are_append_only_delete
BEFORE DELETE ON advance_conflicts BEGIN SELECT RAISE(ABORT, 'append-only conflict ledger'); END
""",
)


class _DatabaseOpenMode(StrEnum):
    CREATE_IF_MISSING = "rwc"
    EXISTING_ONLY = "rw"


_CURRENT_DATABASE_VERSION = 1
_CURRENT_SCHEMA_SIGNATURE = frozenset(" ".join(statement.split()) for statement in _CURRENT_SCHEMA)

_PROJECTION_SCHEMA = """
CREATE TABLE lifecycle_status_projection (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    active_phase TEXT,
    last_completed_session TEXT,
    run_id TEXT,
    configuration_version INTEGER,
    configuration_hash TEXT,
    liveness TEXT NOT NULL,
    durable_reason TEXT
) STRICT;
"""


@dataclass(frozen=True, slots=True)
class RuntimeRootRefusal:
    """Signal that private runtime storage cannot be established safely."""


@dataclass(frozen=True, slots=True)
class PreparedRuntimeDatabase:
    """Identify a validated database path and whether this call created it."""

    path: Path
    created: bool


@dataclass(frozen=True, slots=True)
class _RuntimeDatabaseLocation:
    path: Path
    root_created: bool
    database_exists: bool


def prepare_runtime_database(state_root: Path) -> PreparedRuntimeDatabase | RuntimeRootRefusal:
    """Validate private runtime storage and create a missing database."""
    location = _locate_runtime_database(state_root)
    if isinstance(location, RuntimeRootRefusal):
        return location
    if location.database_exists:
        return PreparedRuntimeDatabase(path=location.path, created=False)
    return _create_runtime_database(location.path)


def open_runtime_database(state_root: Path) -> PreparedRuntimeDatabase | RuntimeRootRefusal:
    """Validate runtime storage without replacing a database missing from an existing root."""
    location = _locate_runtime_database(state_root)
    if isinstance(location, RuntimeRootRefusal):
        return location
    if location.database_exists:
        return PreparedRuntimeDatabase(path=location.path, created=False)
    if location.root_created:
        return _create_runtime_database(location.path)
    return PreparedRuntimeDatabase(path=location.path, created=False)


def _locate_runtime_database(
    state_root: Path,
) -> _RuntimeDatabaseLocation | RuntimeRootRefusal:
    try:
        if state_root.is_symlink():
            return _invalid_root()
        root_created = not state_root.exists()
        if not root_created:
            if not state_root.is_dir() or stat.S_IMODE(state_root.stat().st_mode) & 0o077:
                return _invalid_root()
        else:
            state_root.mkdir(mode=0o700)
        database = state_root / _DATABASE_NAME
        if database.is_symlink():
            return _invalid_root()
        database_exists = database.exists()
        if database_exists and (
            not database.is_file() or stat.S_IMODE(database.stat().st_mode) & 0o077
        ):
            return _invalid_root()
        return _RuntimeDatabaseLocation(database, root_created, database_exists)
    except OSError:
        return _invalid_root()


def _create_runtime_database(database: Path) -> PreparedRuntimeDatabase | RuntimeRootRefusal:
    try:
        descriptor = os.open(database, _PRIVATE_DATABASE_FLAGS, 0o600)
        os.close(descriptor)
    except OSError:
        return _invalid_root()
    return PreparedRuntimeDatabase(path=database, created=True)


def _invalid_root() -> RuntimeRootRefusal:
    return RuntimeRootRefusal()


def _prepare_database(database: Path, *, mode: _DatabaseOpenMode) -> None:
    try:
        with closing(_connect_database(database, mode=mode)) as connection:
            _initialize_or_validate_database(connection)
    except sqlite3.Error as error:
        raise LifecyclePersistenceError(_DATABASE_INITIALIZATION_FAILED) from error


def _initialize_or_validate_database(connection: sqlite3.Connection) -> None:
    with connection:
        connection.execute(_BEGIN_IMMEDIATE_SQL)
        recorded_version = _database_version(connection)
        is_fresh = recorded_version == 0 and not _user_schema_has_objects(connection)
        if is_fresh:
            for statement in _CURRENT_SCHEMA:
                connection.execute(statement)
            _set_current_database_version(connection)
        elif recorded_version != _CURRENT_DATABASE_VERSION:
            raise LifecyclePersistenceError(_UNSUPPORTED_DATABASE_VERSION)
        _validate_current_database(connection, _schema_signature(connection))
        if is_fresh:
            connection.commit()
        else:
            connection.rollback()


def _database_version(connection: sqlite3.Connection) -> int:
    value = connection.execute(_USER_VERSION_SQL).fetchall()[0][0]
    return _integer(value, "database_version")


def _set_current_database_version(connection: sqlite3.Connection) -> None:
    connection.execute(f"PRAGMA user_version = {_CURRENT_DATABASE_VERSION}")


def _user_schema_has_objects(connection: sqlite3.Connection) -> bool:
    row: tuple[object, ...] | None = connection.execute(_USER_SCHEMA_OBJECT_EXISTS_SQL).fetchone()
    return row == (1,)


def _schema_signature(connection: sqlite3.Connection) -> frozenset[str]:
    rows = connection.execute(
        """
        SELECT sql FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite!_%' ESCAPE '!'
          AND NOT (
              type IN ('table', 'index', 'trigger')
              AND tbl_name = 'lifecycle_status_projection'
          )
          AND sql IS NOT NULL
        """
    ).fetchall()
    return frozenset(" ".join(str(row[0]).split()) for row in rows)


def _validate_current_database(
    connection: sqlite3.Connection,
    schema: frozenset[str],
) -> None:
    if schema != _CURRENT_SCHEMA_SIGNATURE:
        raise LifecyclePersistenceError(_SCHEMA_VERSION_MISMATCH)
    _validate_database_integrity(connection)


def _validate_database_integrity(connection: sqlite3.Connection) -> None:
    connection.execute(_INTEGRITY_SAVEPOINT_SQL)
    try:
        # The projection is disposable, so exclude its b-trees while retaining a full
        # database check for authoritative objects and global SQLite consistency.
        connection.execute(_DROP_PROJECTION_SQL)
        rows = connection.execute(_INTEGRITY_CHECK_SQL).fetchall()
    finally:
        connection.execute(_ROLLBACK_INTEGRITY_SAVEPOINT_SQL)
        connection.execute(_RELEASE_INTEGRITY_SAVEPOINT_SQL)
    if rows != [("ok",)]:
        raise LifecyclePersistenceError(_DATABASE_INTEGRITY_FAILED)


def _connect_database(database: Path, *, mode: _DatabaseOpenMode) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{database.as_uri()}?mode={mode}", uri=True)
    connection.execute(_ENABLE_FOREIGN_KEYS_SQL)
    connection.execute(_BUSY_TIMEOUT_SQL)
    return connection


class SQLiteLifecycleLedger:
    """Initialize or validate current storage, then append domain-selected records.

    Construction fails with ``LifecyclePersistenceError`` when the physical database
    version, schema, or integrity cannot be trusted. Capability operations validate the
    authoritative rows within their owning request or global reconstruction scope.
    """

    def __init__(self, database: Path) -> None:
        self._database = database
        _prepare_database(database, mode=_DatabaseOpenMode.CREATE_IF_MISSING)

    @classmethod
    def open_existing(cls, database: Path) -> Self:
        """Validate existing current storage without recreating a missing database."""
        instance = cls.__new__(cls)
        instance._database = database
        if database.exists():
            _prepare_database(database, mode=_DatabaseOpenMode.EXISTING_ONLY)
        return instance

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        recorded_at: datetime,
    ) -> LifecycleDecision:
        """Apply one pure lifecycle decision inside an append transaction."""

        def operation(connection: sqlite3.Connection) -> LifecycleDecision:
            key = _command_key(command)
            refusals = _load_refusals(connection, key=key, command=command)
            terminal = decide_terminal_refusal(tuple(refusals), command)
            if terminal is not None:
                return terminal
            next_refusal_sequence = _next_refusal_sequence(connection)
            try:
                history = LifecycleHistory(
                    events=() if key is None else tuple(_load_events(connection, key=key)),
                    conflicts=(() if key is None else tuple(_load_conflicts(connection, key=key))),
                    occupied_stream_ids=_occupied_stream_ids(connection, command),
                    next_refusal_sequence=next_refusal_sequence,
                )
            except InvalidLifecycleStateError:
                decision = decide_invalid_history(
                    tuple(refusals),
                    command,
                    next_refusal_sequence=next_refusal_sequence,
                )
            else:
                decision = decide_advance(history, command, attempt)
            if isinstance(decision, (AppendLifecycleRecord, AppendTerminalLifecycleRecord)):
                _append_record(connection, decision.record, recorded_at)
            return decision

        return self._write(operation)

    def rebuild_status(self) -> LifecycleStatus:
        """Replace the disposable projection after validating all authoritative history."""

        def operation(connection: sqlite3.Connection) -> LifecycleStatus:
            history = LifecycleHistory(
                events=tuple(_load_events(connection)),
                refusals=tuple(_load_refusals(connection)),
                conflicts=tuple(_load_conflicts(connection)),
            )
            status = derive_lifecycle_status(history)
            _replace_status_projection(connection, status)
            return status

        return self._write(operation)

    def _connect(self) -> sqlite3.Connection:
        return _connect_database(self._database, mode=_DatabaseOpenMode.EXISTING_ONLY)

    def _write(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_BEGIN_IMMEDIATE_SQL)
                return operation(connection)
        except sqlite3.Error as error:
            raise LifecyclePersistenceError(_CHECKPOINT_FAILED) from error


def _load_events(
    connection: sqlite3.Connection,
    *,
    key: IdempotencyKey | None = None,
) -> list[LifecycleEvent]:
    if key is None:
        rows = connection.execute(
            """
            SELECT stream_id, sequence, idempotency_key, session, mode,
                   configuration_version, configuration_hash, run_id,
                   event_kind, completed_phase, recorded_at
            FROM lifecycle_events ORDER BY stream_id, sequence
            """
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT stream_id, sequence, idempotency_key, session, mode,
                   configuration_version, configuration_hash, run_id,
                   event_kind, completed_phase, recorded_at
            FROM lifecycle_events WHERE idempotency_key = ? ORDER BY sequence
            """,
            (key.value,),
        ).fetchall()
    events: list[LifecycleEvent] = []
    for row in rows:
        request = AdvanceRequest.parse(session=row[3], mode=row[4], idempotency_key=row[2])
        if isinstance(request, InputRefusal):
            raise InvalidLifecycleStateError(_INVALID_REQUEST)
        stream_id = _text(row[0], "stream_id")
        sequence = _integer(row[1], "sequence")
        version = _integer(row[5], "configuration_version")
        configuration_hash = _hash(row[6], "configuration_hash")
        run_id = _hash(row[7], "run_id")
        try:
            event_kind = LifecycleEventKind(_text(row[8], "event_kind"))
        except ValueError as error:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER) from error
        phase_text = _optional_text(row[9])
        try:
            completed_phase = None if phase_text is None else LifecyclePhase(phase_text)
        except ValueError as error:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER) from error
        _aware_timestamp(row[10])
        events.append(
            LifecycleEvent(
                stream_id,
                sequence,
                request,
                PinnedRunIdentity(run_id, version, configuration_hash),
                event_kind,
                completed_phase,
            )
        )
    return events


def _load_refusals(
    connection: sqlite3.Connection,
    *,
    key: IdempotencyKey | None = None,
    command: LifecycleCommand | None = None,
) -> list[DurableAdvanceRefusal]:
    if command is None:
        rows = connection.execute(
            """
            SELECT refusal_id, idempotency_key, reason_code, recorded_at
            FROM advance_refusals ORDER BY refusal_id
            """
        ).fetchall()
    elif key is None:
        rows = connection.execute(
            """
            SELECT refusal_id, idempotency_key, reason_code, recorded_at
            FROM advance_refusals
            WHERE idempotency_key IS NULL AND reason_code = ?
            ORDER BY refusal_id
            """,
            (AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY.value,),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT refusal_id, idempotency_key, reason_code, recorded_at
            FROM advance_refusals WHERE idempotency_key = ? ORDER BY refusal_id
            """,
            (key.value,),
        ).fetchall()
    refusals: list[DurableAdvanceRefusal] = []
    for row in rows:
        sequence = _integer(row[0], "refusal_id")
        key = _optional_refusal_key(row[1])
        reason = _failure_reason(row[2])
        _aware_timestamp(row[3])
        refusals.append(DurableAdvanceRefusal(sequence, key, reason))
    return refusals


def _load_conflicts(
    connection: sqlite3.Connection,
    *,
    key: IdempotencyKey | None = None,
) -> list[DurableAdvanceConflict]:
    if key is None:
        rows = connection.execute(
            """
            SELECT idempotency_key, reason_code, recorded_at
            FROM advance_conflicts ORDER BY idempotency_key
            """
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT idempotency_key, reason_code, recorded_at
            FROM advance_conflicts WHERE idempotency_key = ? ORDER BY idempotency_key
            """,
            (key.value,),
        ).fetchall()
    conflicts: list[DurableAdvanceConflict] = []
    for row in rows:
        key = _conflict_key(row[0])
        reason = _failure_reason(row[1])
        _aware_timestamp(row[2])
        conflicts.append(DurableAdvanceConflict(key, reason))
    return conflicts


def _command_key(command: LifecycleCommand) -> IdempotencyKey | None:
    if isinstance(command, InputRefusal):
        return command.idempotency_key
    return command.request.idempotency_key


def _occupied_stream_ids(
    connection: sqlite3.Connection,
    command: LifecycleCommand,
) -> frozenset[str]:
    if isinstance(command, InputRefusal):
        return frozenset()
    stream_id = command.request.stream_id
    row = connection.execute(_STREAM_EXISTS_SQL, (stream_id,)).fetchone()
    return frozenset() if row is None else frozenset((stream_id,))


def _next_refusal_sequence(connection: sqlite3.Connection) -> int:
    row = connection.execute(_NEXT_REFUSAL_SEQUENCE_SQL).fetchone()
    if row is None:
        raise InvalidLifecycleStateError(_INVALID_REFUSAL_KEY)
    return _integer(row[0], "refusal_id")


def _append_record(
    connection: sqlite3.Connection,
    record: LifecycleRecord,
    recorded_at: datetime,
) -> None:
    timestamp = _timestamp(recorded_at)
    if isinstance(record, LifecycleEvent):
        request = record.request
        identity = record.pinned_run_identity
        connection.execute(
            """
            INSERT INTO lifecycle_events (
                stream_id, sequence, idempotency_key, session, mode,
                configuration_version, configuration_hash, run_id,
                event_kind, completed_phase, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.stream_id,
                record.sequence,
                request.idempotency_key.value,
                request.session.isoformat(),
                request.mode.value,
                identity.configuration_version,
                identity.configuration_hash,
                identity.run_id,
                record.event_kind.value,
                None if record.completed_phase is None else record.completed_phase.value,
                timestamp,
            ),
        )
    elif isinstance(record, DurableAdvanceRefusal):
        connection.execute(
            """
            INSERT INTO advance_refusals (
                refusal_id, idempotency_key, reason_code, recorded_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                record.sequence,
                None if record.idempotency_key is None else record.idempotency_key.value,
                record.reason.value,
                timestamp,
            ),
        )
    else:
        connection.execute(
            """
            INSERT INTO advance_conflicts (idempotency_key, reason_code, recorded_at)
            VALUES (?, ?, ?)
            """,
            (record.idempotency_key.value, record.reason.value, timestamp),
        )


def _replace_status_projection(
    connection: sqlite3.Connection,
    status: LifecycleStatus,
) -> None:
    connection.execute(_DROP_PROJECTION_SQL)
    connection.execute(_PROJECTION_SCHEMA)
    identity = status.pinned_run_identity
    connection.execute(
        """
        INSERT INTO lifecycle_status_projection (
            singleton, active_phase, last_completed_session, run_id,
            configuration_version, configuration_hash, liveness, durable_reason
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            None if status.active_phase is None else status.active_phase.value,
            (
                None
                if status.last_completed_session is None
                else status.last_completed_session.isoformat()
            ),
            None if identity is None else identity.run_id,
            None if identity is None else identity.configuration_version,
            None if identity is None else identity.configuration_hash,
            status.liveness.value,
            None if status.durable_reason is None else status.durable_reason.value,
        ),
    )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        message = f"invalid {field} in lifecycle ledger"
        raise InvalidLifecycleStateError(message)
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _text(value, "completed_phase")


def _integer(value: object, field: str) -> int:
    message = f"invalid {field} in lifecycle ledger"
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidLifecycleStateError(message)
    return value


def _hash(value: object, field: str) -> str:
    if not is_sha256(value):
        message = f"invalid {field} in lifecycle ledger"
        raise InvalidLifecycleStateError(message)
    return value


def _failure_reason(value: object) -> AdvanceFailureReason:
    text = _text(value, "reason_code")
    try:
        return AdvanceFailureReason(text)
    except ValueError as error:
        raise InvalidLifecycleStateError(_UNKNOWN_REASON_CODE) from error


def _optional_refusal_key(value: object) -> IdempotencyKey | None:
    if value is None:
        return None
    key = IdempotencyKey.parse(value)
    if key is None:
        raise InvalidLifecycleStateError(_INVALID_REFUSAL_KEY)
    return key


def _conflict_key(value: object) -> IdempotencyKey:
    key = IdempotencyKey.parse(value)
    if key is None:
        raise InvalidLifecycleStateError(_INVALID_CONFLICT_KEY)
    return key


def _aware_timestamp(value: object) -> datetime:
    text = _text(value, "recorded_at")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise InvalidLifecycleStateError(_INVALID_RECORDED_AT) from error
    if parsed.utcoffset() is None:
        raise InvalidLifecycleStateError(_RECORDED_AT_NOT_AWARE)
    return parsed


def _timestamp(value: datetime) -> str:
    if value.utcoffset() is None:
        raise LifecyclePersistenceError(_CLOCK_NOT_AWARE)
    return value.isoformat()
