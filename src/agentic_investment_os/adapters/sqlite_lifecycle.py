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
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRequest,
    IdempotencyKey,
    InputRefusal,
    InvalidLifecycleStateError,
    LifecyclePersistenceError,
    LifecyclePhase,
    LifecycleProgress,
    PinnedRunIdentity,
    StartResult,
    StreamConflict,
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
_ENABLE_FOREIGN_KEYS_SQL = "PRAGMA foreign_keys = ON"
_BUSY_TIMEOUT_SQL = "PRAGMA busy_timeout = 5000"
_BEGIN_IMMEDIATE_SQL = "BEGIN IMMEDIATE"
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

CREATE TRIGGER IF NOT EXISTS lifecycle_events_are_append_only_update
BEFORE UPDATE ON lifecycle_events BEGIN SELECT RAISE(ABORT, 'append-only lifecycle ledger'); END;
CREATE TRIGGER IF NOT EXISTS lifecycle_events_are_append_only_delete
BEFORE DELETE ON lifecycle_events BEGIN SELECT RAISE(ABORT, 'append-only lifecycle ledger'); END;
CREATE TRIGGER IF NOT EXISTS advance_refusals_are_append_only_update
BEFORE UPDATE ON advance_refusals BEGIN SELECT RAISE(ABORT, 'append-only refusal ledger'); END;
CREATE TRIGGER IF NOT EXISTS advance_refusals_are_append_only_delete
BEFORE DELETE ON advance_refusals BEGIN SELECT RAISE(ABORT, 'append-only refusal ledger'); END;
"""


@dataclass(frozen=True, slots=True)
class RuntimeRootRefusal:
    """Signal that private runtime storage cannot be established safely."""


def prepare_runtime_database(state_root: Path) -> Path | RuntimeRootRefusal:
    """Create a private state root and database file without following a final symlink."""
    try:
        if state_root.is_symlink():
            return _invalid_root()
        if state_root.exists():
            if not state_root.is_dir() or stat.S_IMODE(state_root.stat().st_mode) & 0o077:
                return _invalid_root()
        else:
            state_root.mkdir(mode=0o700)
        database = state_root / _DATABASE_NAME
        if database.is_symlink():
            return _invalid_root()
        if database.exists():
            if not database.is_file() or stat.S_IMODE(database.stat().st_mode) & 0o077:
                return _invalid_root()
        else:
            descriptor = os.open(database, _PRIVATE_DATABASE_FLAGS, 0o600)
            os.close(descriptor)
        return database
    except OSError:
        return _invalid_root()


def _invalid_root() -> RuntimeRootRefusal:
    return RuntimeRootRefusal()


class SQLiteLifecycleLedger:
    """Store each lifecycle transition in its own atomic append transaction."""

    def __init__(self, database: Path) -> None:
        self._database = database
        self._write(lambda connection: connection.executescript(_SCHEMA))

    def load_by_idempotency_key(
        self, key: IdempotencyKey
    ) -> LifecycleProgress | AdvanceReceipt | None:
        return self._read(lambda connection: self._load(connection, key))

    def resolve_for_advance(
        self,
        key: IdempotencyKey,
        recorded_at: datetime,
    ) -> LifecycleProgress | AdvanceReceipt | None:
        def operation(
            connection: sqlite3.Connection,
        ) -> LifecycleProgress | AdvanceReceipt | None:
            refusal = self._load_refusal(connection, key)
            if refusal is not None:
                return refusal
            try:
                return self._load_events(connection, key)
            except InvalidLifecycleStateError:
                return self._append_refusal(
                    connection,
                    key,
                    AdvanceFailureReason.INVALID_DURABLE_STATE,
                    recorded_at,
                )

        return self._write(operation)

    def start(
        self,
        request: AdvanceRequest,
        identity: PinnedRunIdentity,
        recorded_at: datetime,
    ) -> StartResult:
        def operation(connection: sqlite3.Connection) -> StartResult:
            existing = self._load(connection, request.idempotency_key)
            if existing is not None:
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
            return LifecycleProgress(request, identity, None, 0)

        return self._write(operation)

    def complete_reconciliation(
        self, key: IdempotencyKey, recorded_at: datetime
    ) -> LifecycleProgress | AdvanceReceipt:
        def operation(connection: sqlite3.Connection) -> LifecycleProgress | AdvanceReceipt:
            progress = self._required_progress(connection, key)
            receipt = progress.receipt
            if receipt is not None:
                return receipt
            if progress.completed_phase is LifecyclePhase.RECONCILE_PRIOR_STATE:
                return progress
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
            return LifecycleProgress(
                progress.request,
                progress.pinned_run_identity,
                LifecyclePhase.RECONCILE_PRIOR_STATE,
                1,
            )

        return self._write(operation)

    def pin_run_inputs(self, key: IdempotencyKey, recorded_at: datetime) -> AdvanceReceipt:
        def operation(connection: sqlite3.Connection) -> AdvanceReceipt:
            progress = self._required_progress(connection, key)
            receipt = progress.receipt
            if receipt is not None:
                return receipt
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
            return AdvanceReceipt(
                disposition=AdvanceDisposition.ADVANCED,
                completed_phase=LifecyclePhase.PIN_RUN_INPUTS,
                pinned_run_identity=progress.pinned_run_identity,
                failure_reason=None,
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
            if key is not None:
                existing = self._load_refusal(connection, key)
                if existing is not None:
                    return existing
                progress = self._load_events(connection, key)
                if progress is not None:
                    receipt = progress.receipt
                    if receipt is not None:
                        return receipt
                    resolved_reason = AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
            return self._append_refusal(connection, key, resolved_reason, recorded_at)

        return self._write(operation)

    def _load(
        self, connection: sqlite3.Connection, key: IdempotencyKey
    ) -> LifecycleProgress | AdvanceReceipt | None:
        refusal = self._load_refusal(connection, key)
        if refusal is not None:
            return refusal
        progress = self._load_events(connection, key)
        if progress is not None:
            return progress
        return None

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

    def _required_progress(
        self, connection: sqlite3.Connection, key: IdempotencyKey
    ) -> LifecycleProgress:
        state = self._load(connection, key)
        if not isinstance(state, LifecycleProgress):
            raise LifecyclePersistenceError(_REQUIRED_STREAM_MISSING)
        return state

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
        connection = sqlite3.connect(self._database)
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
