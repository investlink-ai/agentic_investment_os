"""Persist append-only lifecycle checkpoints in a private SQLite database."""

from __future__ import annotations

import os
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, TypeVar

from agentic_investment_os.domain.lifecycle import (
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRequest,
    CheckpointResult,
    CheckpointWrite,
    DurableAdvanceConflict,
    DurableAdvanceRefusal,
    IdempotencyKey,
    InputRefusal,
    InvalidLifecycleStateError,
    LifecyclePersistenceError,
    LifecyclePhase,
    LifecycleProgress,
    LifecycleStatus,
    PinnedRunIdentity,
    StartResult,
    StreamConflict,
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
_LOAD_REFUSAL_SQL = (
    "SELECT reason_code, recorded_at FROM advance_refusals WHERE idempotency_key = ?"
)
_LOAD_UNKEYED_REFUSAL_SQL = """
SELECT reason_code, recorded_at FROM advance_refusals
WHERE idempotency_key IS NULL AND reason_code = ?
"""
_LOAD_CONFLICT_SQL = (
    "SELECT reason_code, recorded_at FROM advance_conflicts WHERE idempotency_key = ?"
)
_ENABLE_FOREIGN_KEYS_SQL = "PRAGMA foreign_keys = ON"
_BUSY_TIMEOUT_SQL = "PRAGMA busy_timeout = 5000"
_BEGIN_IMMEDIATE_SQL = "BEGIN IMMEDIATE"
_DROP_PROJECTION_SQL = (
    "DROP TABLE IF EXISTS lifecycle_status_projection"  # pragma: no mutate
    # SQLite keywords and identifiers are case-insensitive, so case-only mutants are equivalent.
)
_PIN_REQUIRES_RECONCILIATION = "PinRunInputs requires the ReconcilePriorState checkpoint"
_REQUIRED_STREAM_MISSING = "required lifecycle stream is missing or terminal"
_READ_FAILED = "SQLite lifecycle read failed"
_CHECKPOINT_FAILED = "SQLite lifecycle checkpoint failed"
_INVALID_REQUEST = "invalid request values in lifecycle ledger"
_UNSUPPORTED_CONFIGURATION_VERSION = "unsupported configuration_version in lifecycle ledger"
_INVALID_DERIVED_IDENTITY = "lifecycle stream derived identity is invalid"
_UNSUPPORTED_LATER_PHASES = "lifecycle stream contains unsupported later phases"
_NONCONTIGUOUS_SEQUENCE = "lifecycle stream sequence is not contiguous"
_CHANGED_PINNED_FACTS = "lifecycle stream changed pinned request facts"
_INVALID_CHECKPOINT_ORDER = "lifecycle stream checkpoint order is invalid"
_INVALID_RECORDED_AT = "invalid recorded_at in lifecycle ledger"
_UNKNOWN_REASON_CODE = "unknown reason_code in lifecycle ledger"
_CONFLICT_WITHOUT_COMPLETION = "lifecycle conflict does not belong to a completed stream"
_INVALID_REFUSAL_ORDER = "lifecycle refusal order is invalid"
_INVALID_REFUSAL_KEY = "invalid idempotency_key in lifecycle refusal ledger"
_INVALID_UNKEYED_REASON = "unkeyed lifecycle refusal reason is invalid"
_INVALID_CONFLICT_KEY = "invalid idempotency_key in lifecycle conflict ledger"
_RECORDED_AT_NOT_AWARE = "recorded_at must be timezone-aware"
_CLOCK_NOT_AWARE = "lifecycle clock must return a timezone-aware timestamp"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lifecycle_events (
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
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS one_initial_event_per_stream
ON lifecycle_events(stream_id)
WHERE event_kind = 'advance_requested';

CREATE TABLE IF NOT EXISTS advance_refusals (
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
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS one_unkeyed_refusal_per_reason
ON advance_refusals(reason_code)
WHERE idempotency_key IS NULL;

CREATE TABLE IF NOT EXISTS advance_conflicts (
    idempotency_key TEXT PRIMARY KEY,
    reason_code TEXT NOT NULL CHECK (reason_code = 'idempotency_key_conflict'),
    recorded_at TEXT NOT NULL
) STRICT;

CREATE TRIGGER IF NOT EXISTS lifecycle_events_are_append_only_update
BEFORE UPDATE ON lifecycle_events BEGIN SELECT RAISE(ABORT, 'append-only lifecycle ledger'); END;
CREATE TRIGGER IF NOT EXISTS lifecycle_events_are_append_only_delete
BEFORE DELETE ON lifecycle_events BEGIN SELECT RAISE(ABORT, 'append-only lifecycle ledger'); END;
CREATE TRIGGER IF NOT EXISTS advance_refusals_are_append_only_update
BEFORE UPDATE ON advance_refusals BEGIN SELECT RAISE(ABORT, 'append-only refusal ledger'); END;
CREATE TRIGGER IF NOT EXISTS advance_refusals_are_append_only_delete
BEFORE DELETE ON advance_refusals BEGIN SELECT RAISE(ABORT, 'append-only refusal ledger'); END;
CREATE TRIGGER IF NOT EXISTS advance_conflicts_are_append_only_update
BEFORE UPDATE ON advance_conflicts BEGIN SELECT RAISE(ABORT, 'append-only conflict ledger'); END;
CREATE TRIGGER IF NOT EXISTS advance_conflicts_are_append_only_delete
BEFORE DELETE ON advance_conflicts BEGIN SELECT RAISE(ABORT, 'append-only conflict ledger'); END;
"""

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
class _RequestHistory:
    progress: LifecycleProgress | None
    conflict: AdvanceReceipt | None


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
        return _RuntimeDatabaseLocation(
            path=database,
            root_created=root_created,
            database_exists=database_exists,
        )
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


class SQLiteLifecycleLedger:
    """Store each lifecycle transition in its own atomic append transaction."""

    def __init__(self, database: Path, *, initialize_schema: bool = True) -> None:
        self._database = database
        if initialize_schema:
            self._write(lambda connection: connection.executescript(_SCHEMA))

    def load_by_idempotency_key(
        self, key: IdempotencyKey
    ) -> LifecycleProgress | AdvanceReceipt | None:
        return self._read(lambda connection: self._load(connection, key))

    def rebuild_status(self) -> LifecycleStatus:
        """Replace the disposable status projection after validating all source history."""

        def operation(connection: sqlite3.Connection) -> LifecycleStatus:
            status = _reconstruct_status(
                connection.execute(
                    """
                    SELECT stream_id, sequence, idempotency_key, session, mode,
                           configuration_version, configuration_hash, run_id,
                           event_kind, completed_phase, recorded_at
                    FROM lifecycle_events ORDER BY stream_id, sequence
                    """
                ).fetchall(),
                connection.execute(
                    """
                    SELECT refusal_id, idempotency_key, reason_code, recorded_at
                    FROM advance_refusals ORDER BY refusal_id
                    """
                ).fetchall(),
                connection.execute(
                    """
                    SELECT idempotency_key, reason_code, recorded_at
                    FROM advance_conflicts ORDER BY idempotency_key
                    """
                ).fetchall(),
            )
            _replace_status_projection(connection, status)
            return status

        return self._write(operation)

    def start(
        self,
        request: AdvanceRequest,
        identity: PinnedRunIdentity,
        recorded_at: datetime,
    ) -> StartResult:
        def operation(connection: sqlite3.Connection) -> StartResult:
            existing = self._resolve_matching_progress(
                connection,
                request,
                identity,
                recorded_at,
            )
            if existing is not None:
                if isinstance(existing, LifecycleProgress):
                    return CheckpointResult(existing, CheckpointWrite.OBSERVED)
                return existing
            stream_row = connection.execute(_STREAM_EXISTS_SQL, (request.stream_id,)).fetchone()
            if stream_row is not None:
                return StreamConflict()
            connection.execute(
                """
                INSERT INTO lifecycle_events (
                    stream_id, sequence, idempotency_key, session, mode,
                    configuration_version, configuration_hash, run_id,
                    event_kind, completed_phase, recorded_at
                ) VALUES (?, 0, ?, ?, ?, ?, ?, ?, 'advance_requested', NULL, ?)
                """,
                (
                    request.stream_id,
                    request.idempotency_key.value,
                    request.session.isoformat(),
                    request.mode.value,
                    identity.configuration_version,
                    identity.configuration_hash,
                    identity.run_id,
                    _timestamp(recorded_at),
                ),
            )
            return CheckpointResult(
                LifecycleProgress(request, identity, None, 0),
                CheckpointWrite.APPENDED,
            )

        return self._write(operation)

    def complete_reconciliation(
        self, key: IdempotencyKey, recorded_at: datetime
    ) -> CheckpointResult | AdvanceReceipt:
        def operation(connection: sqlite3.Connection) -> CheckpointResult | AdvanceReceipt:
            state = self._required_progress(connection, key, recorded_at)
            if isinstance(state, AdvanceReceipt):
                return state
            progress = state
            if (
                progress.is_complete
                or progress.completed_phase is LifecyclePhase.RECONCILE_PRIOR_STATE
            ):
                return CheckpointResult(progress, CheckpointWrite.OBSERVED)
            connection.execute(
                """
                INSERT INTO lifecycle_events (
                    stream_id, sequence, idempotency_key, session, mode,
                    configuration_version, configuration_hash, run_id,
                    event_kind, completed_phase, recorded_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, 'phase_completed', 'ReconcilePriorState', ?)
                """,
                self._event_values(progress, recorded_at),
            )
            return CheckpointResult(
                LifecycleProgress(
                    progress.request,
                    progress.pinned_run_identity,
                    LifecyclePhase.RECONCILE_PRIOR_STATE,
                    1,
                ),
                CheckpointWrite.APPENDED,
            )

        return self._write(operation)

    def pin_run_inputs(
        self, key: IdempotencyKey, recorded_at: datetime
    ) -> CheckpointResult | AdvanceReceipt:
        def operation(connection: sqlite3.Connection) -> CheckpointResult | AdvanceReceipt:
            state = self._required_progress(connection, key, recorded_at)
            if isinstance(state, AdvanceReceipt):
                return state
            progress = state
            if progress.is_complete:
                return CheckpointResult(progress, CheckpointWrite.OBSERVED)
            if progress.completed_phase is not LifecyclePhase.RECONCILE_PRIOR_STATE:
                raise LifecyclePersistenceError(_PIN_REQUIRES_RECONCILIATION)
            connection.execute(
                """
                INSERT INTO lifecycle_events (
                    stream_id, sequence, idempotency_key, session, mode,
                    configuration_version, configuration_hash, run_id,
                    event_kind, completed_phase, recorded_at
                ) VALUES (?, 2, ?, ?, ?, ?, ?, ?, 'run_inputs_pinned', 'PinRunInputs', ?)
                """,
                self._event_values(progress, recorded_at),
            )
            return CheckpointResult(
                LifecycleProgress(
                    progress.request,
                    progress.pinned_run_identity,
                    LifecyclePhase.PIN_RUN_INPUTS,
                    2,
                ),
                CheckpointWrite.APPENDED,
            )

        return self._write(operation)

    def record_refusal(
        self,
        key: IdempotencyKey | None,
        reason_code: AdvanceFailureReason,
        recorded_at: datetime,
    ) -> AdvanceReceipt:
        def operation(connection: sqlite3.Connection) -> AdvanceReceipt:
            resolved_reason = reason_code
            if key is None:
                existing = self._load_unkeyed_refusal(connection, reason_code)
                if existing is not None:
                    return existing
            else:
                history = self._load_request_history(connection, key, recorded_at)
                if isinstance(history, AdvanceReceipt):
                    return history
                progress = history.progress
                if progress is not None:
                    if progress.is_complete:
                        if history.conflict is not None:
                            return history.conflict
                        return self._append_completed_conflict(connection, key, recorded_at)
                    resolved_reason = AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
            return self._append_refusal(connection, key, resolved_reason, recorded_at)

        return self._write(operation)

    def _resolve_matching_progress(
        self,
        connection: sqlite3.Connection,
        request: AdvanceRequest,
        identity: PinnedRunIdentity,
        recorded_at: datetime,
    ) -> LifecycleProgress | AdvanceReceipt | None:
        key = request.idempotency_key
        history = self._load_request_history(connection, key, recorded_at)
        if isinstance(history, AdvanceReceipt):
            return history
        progress = history.progress
        if progress is None:
            return None
        if progress.request == request and progress.pinned_run_identity == identity:
            return progress
        if progress.is_complete:
            if history.conflict is not None:
                return history.conflict
            return self._append_completed_conflict(connection, key, recorded_at)
        return self._append_refusal(
            connection,
            key,
            AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
            recorded_at,
        )

    def _load_request_history(
        self,
        connection: sqlite3.Connection,
        key: IdempotencyKey,
        recorded_at: datetime,
    ) -> _RequestHistory | AdvanceReceipt:
        refusal = self._load_refusal(connection, key)
        if refusal is not None:
            return refusal
        try:
            progress = self._load_events(connection, key)
            conflict = self._load_conflict(connection, key)
        except InvalidLifecycleStateError:
            return self._append_refusal(
                connection,
                key,
                AdvanceFailureReason.INVALID_DURABLE_STATE,
                recorded_at,
            )
        if conflict is not None and (progress is None or not progress.is_complete):
            return self._append_refusal(
                connection,
                key,
                AdvanceFailureReason.INVALID_DURABLE_STATE,
                recorded_at,
            )
        return _RequestHistory(progress, conflict)

    def _load(
        self, connection: sqlite3.Connection, key: IdempotencyKey
    ) -> LifecycleProgress | AdvanceReceipt | None:
        refusal = self._load_refusal(connection, key)
        if refusal is not None:
            return refusal
        progress = self._load_events(connection, key)
        conflict = self._load_conflict(connection, key)
        if conflict is not None and (progress is None or not progress.is_complete):
            raise InvalidLifecycleStateError(_CONFLICT_WITHOUT_COMPLETION)
        return progress

    @staticmethod
    def _append_refusal(
        connection: sqlite3.Connection,
        key: IdempotencyKey | None,
        reason_code: AdvanceFailureReason,
        recorded_at: datetime,
    ) -> AdvanceReceipt:
        connection.execute(
            """
            INSERT INTO advance_refusals (idempotency_key, reason_code, recorded_at)
            VALUES (?, ?, ?)
            """,
            (
                None if key is None else key.value,
                reason_code.value,
                _timestamp(recorded_at),
            ),
        )
        return AdvanceReceipt.failed_closed(reason_code)

    @staticmethod
    def _append_completed_conflict(
        connection: sqlite3.Connection,
        key: IdempotencyKey,
        recorded_at: datetime,
    ) -> AdvanceReceipt:
        reason = AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
        connection.execute(
            """
            INSERT INTO advance_conflicts (idempotency_key, reason_code, recorded_at)
            VALUES (?, ?, ?)
            """,
            (key.value, reason.value, _timestamp(recorded_at)),
        )
        return AdvanceReceipt.failed_closed(reason)

    @staticmethod
    def _load_events(
        connection: sqlite3.Connection, key: IdempotencyKey
    ) -> LifecycleProgress | None:
        rows = connection.execute(
            """
            SELECT stream_id, sequence, idempotency_key, session, mode,
                   configuration_version, configuration_hash, run_id,
                   event_kind, completed_phase, recorded_at
            FROM lifecycle_events WHERE idempotency_key = ? ORDER BY sequence
            """,
            (key.value,),
        ).fetchall()
        if not rows:
            return None
        return _reconstruct_progress(rows)

    @staticmethod
    def _load_refusal(connection: sqlite3.Connection, key: IdempotencyKey) -> AdvanceReceipt | None:
        row = connection.execute(_LOAD_REFUSAL_SQL, (key.value,)).fetchone()
        if row is None:
            return None
        reason = _failure_reason(row[0])
        _aware_timestamp(row[1])
        return AdvanceReceipt.failed_closed(reason)

    @staticmethod
    def _load_unkeyed_refusal(
        connection: sqlite3.Connection,
        reason_code: AdvanceFailureReason,
    ) -> AdvanceReceipt | None:
        row = connection.execute(_LOAD_UNKEYED_REFUSAL_SQL, (reason_code.value,)).fetchone()
        if row is None:
            return None
        reason = _failure_reason(row[0])
        _aware_timestamp(row[1])
        return AdvanceReceipt.failed_closed(reason)

    @staticmethod
    def _load_conflict(
        connection: sqlite3.Connection,
        key: IdempotencyKey,
    ) -> AdvanceReceipt | None:
        row = connection.execute(_LOAD_CONFLICT_SQL, (key.value,)).fetchone()
        if row is None:
            return None
        reason = _failure_reason(row[0])
        if reason is not AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT:
            raise InvalidLifecycleStateError(_UNKNOWN_REASON_CODE)
        _aware_timestamp(row[1])
        return AdvanceReceipt.failed_closed(reason)

    def _required_progress(
        self,
        connection: sqlite3.Connection,
        key: IdempotencyKey,
        recorded_at: datetime,
    ) -> LifecycleProgress | AdvanceReceipt:
        state = self._load_request_history(connection, key, recorded_at)
        if isinstance(state, AdvanceReceipt):
            return state
        if state.progress is None:
            raise LifecyclePersistenceError(_REQUIRED_STREAM_MISSING)
        return state.progress

    @staticmethod
    def _event_values(progress: LifecycleProgress, recorded_at: datetime) -> tuple[object, ...]:
        request = progress.request
        identity = progress.pinned_run_identity
        return (
            request.stream_id,
            request.idempotency_key.value,
            request.session.isoformat(),
            request.mode.value,
            identity.configuration_version,
            identity.configuration_hash,
            identity.run_id,
            _timestamp(recorded_at),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"{self._database.as_uri()}?mode=rw", uri=True)
        connection.execute(_ENABLE_FOREIGN_KEYS_SQL)
        connection.execute(_BUSY_TIMEOUT_SQL)
        return connection

    def _read(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        try:
            with closing(self._connect()) as connection:
                return operation(connection)
        except sqlite3.Error as error:
            raise LifecyclePersistenceError(_READ_FAILED) from error

    def _write(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_BEGIN_IMMEDIATE_SQL)
                return operation(connection)
        except sqlite3.Error as error:
            raise LifecyclePersistenceError(_CHECKPOINT_FAILED) from error


def _reconstruct_progress(rows: list[tuple[object, ...]]) -> LifecycleProgress:
    first = rows[0]
    request = AdvanceRequest.parse(session=first[3], mode=first[4], idempotency_key=first[2])
    if isinstance(request, InputRefusal):
        raise InvalidLifecycleStateError(_INVALID_REQUEST)
    stream_id = _text(first[0], "stream_id")
    version = _integer(first[5], "configuration_version")
    if version != 1:
        raise InvalidLifecycleStateError(_UNSUPPORTED_CONFIGURATION_VERSION)
    configuration_hash = _hash(first[6], "configuration_hash")
    run_id = _hash(first[7], "run_id")
    identity = PinnedRunIdentity.create(
        request,
        configuration_version=version,
        configuration_hash=configuration_hash,
    )
    if stream_id != request.stream_id or run_id != identity.run_id:
        raise InvalidLifecycleStateError(_INVALID_DERIVED_IDENTITY)
    expected = (
        ("advance_requested", None),
        ("phase_completed", LifecyclePhase.RECONCILE_PRIOR_STATE.value),
        ("run_inputs_pinned", LifecyclePhase.PIN_RUN_INPUTS.value),
    )
    if len(rows) > len(expected):
        raise InvalidLifecycleStateError(_UNSUPPORTED_LATER_PHASES)
    for sequence, row in enumerate(rows):
        if _integer(row[1], "sequence") != sequence:
            raise InvalidLifecycleStateError(_NONCONTIGUOUS_SEQUENCE)
        invariant_values = (row[0], row[2], row[3], row[4], row[5], row[6], row[7])
        expected_invariants = (
            stream_id,
            first[2],
            first[3],
            first[4],
            version,
            configuration_hash,
            run_id,
        )
        if invariant_values != expected_invariants:
            raise InvalidLifecycleStateError(_CHANGED_PINNED_FACTS)
        if (_text(row[8], "event_kind"), _optional_text(row[9])) != expected[sequence]:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
        _aware_timestamp(row[10])
    phase_by_event_count = (
        None,
        LifecyclePhase.RECONCILE_PRIOR_STATE,
        LifecyclePhase.PIN_RUN_INPUTS,
    )
    phase = phase_by_event_count[len(rows) - 1]
    return LifecycleProgress(request, identity, phase, len(rows) - 1)


def _reconstruct_status(
    event_rows: list[tuple[object, ...]],
    refusal_rows: list[tuple[object, ...]],
    conflict_rows: list[tuple[object, ...]],
) -> LifecycleStatus:
    streams: dict[str, list[tuple[object, ...]]] = {}
    for row in event_rows:
        stream_id = _text(row[0], "stream_id")
        streams.setdefault(stream_id, []).append(row)

    progresses: list[LifecycleProgress] = []
    for rows in streams.values():
        progress = _reconstruct_progress(rows)
        progresses.append(progress)

    refusals = _reconstruct_refusals(refusal_rows)
    conflicts = _reconstruct_conflicts(conflict_rows)
    return derive_lifecycle_status(tuple(progresses), tuple(refusals), tuple(conflicts))


def _reconstruct_refusals(rows: list[tuple[object, ...]]) -> list[DurableAdvanceRefusal]:
    refusals: list[DurableAdvanceRefusal] = []
    for expected_id, row in enumerate(rows, start=1):
        refusal_id = _integer(row[0], "refusal_id")
        if refusal_id != expected_id:
            raise InvalidLifecycleStateError(_INVALID_REFUSAL_ORDER)
        key = _optional_idempotency_key(row[1])
        reason = _failure_reason(row[2])
        if (key is None) != (reason is AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY):
            raise InvalidLifecycleStateError(_INVALID_UNKEYED_REASON)
        _aware_timestamp(row[3])
        refusals.append(DurableAdvanceRefusal(key, reason))
    return refusals


def _reconstruct_conflicts(rows: list[tuple[object, ...]]) -> list[DurableAdvanceConflict]:
    conflicts: list[DurableAdvanceConflict] = []
    for row in rows:
        key = IdempotencyKey.parse(row[0])
        if key is None:
            raise InvalidLifecycleStateError(_INVALID_CONFLICT_KEY)
        reason = _failure_reason(row[1])
        _aware_timestamp(row[2])
        conflicts.append(DurableAdvanceConflict(key, reason))
    return conflicts


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
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"invalid {field} in lifecycle ledger"
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


def _optional_idempotency_key(value: object) -> IdempotencyKey | None:
    if value is None:
        return None
    key = IdempotencyKey.parse(value)
    if key is None:
        raise InvalidLifecycleStateError(_INVALID_REFUSAL_KEY)
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
