"""Persist scheduler claims and observations in a private append-only ledger."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
from contextlib import closing, contextmanager
from datetime import date
from typing import TYPE_CHECKING

from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.lifecycle import AdvanceReceipt, parse_advance_receipt
from agentic_investment_os.domain.scheduler import (
    ScheduledRunDisposition,
    ScheduledSessionStatus,
    SchedulerClaim,
    SchedulerClaimDisposition,
    SchedulerPolicy,
    SchedulerSnapshot,
    SessionWindow,
    build_session_window,
    receipt_matches_session,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = (
    "SQLiteSchedulerLedger",
    "SchedulerPersistenceError",
    "SchedulerPolicyConflictError",
)

_DATABASE_NAME = "scheduler.sqlite3"
_LOCK_NAME = "scheduler.lock"
_BEGIN_IMMEDIATE = "BEGIN IMMEDIATE"
_SCHEMA_VERSION = 1
_HASH_LENGTH = 64
_EVENT_COLUMN_COUNT = 10
_SCHEMA = (
    """
    CREATE TABLE scheduler_metadata (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        policy_id TEXT NOT NULL CHECK (length(policy_id) = 64),
        policy_payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE scheduler_events (
        cycle TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        event_kind TEXT NOT NULL CHECK (
            event_kind IN ('started', 'resumed', 'completed', 'missed', 'refused')
        ),
        scheduled_at TEXT NOT NULL,
        missed_at TEXT NOT NULL,
        attempt INTEGER NOT NULL CHECK (attempt >= 0),
        lifecycle_receipt TEXT,
        lifecycle_receipt_hash TEXT CHECK (
            lifecycle_receipt_hash IS NULL OR length(lifecycle_receipt_hash) = 64
        ),
        recorded_at TEXT NOT NULL,
        event_hash TEXT NOT NULL CHECK (length(event_hash) = 64),
        PRIMARY KEY (cycle, sequence),
        CHECK (
            (event_kind IN ('started', 'resumed')
                AND attempt >= 1
                AND lifecycle_receipt IS NULL
                AND lifecycle_receipt_hash IS NULL)
            OR (event_kind IN ('completed', 'refused')
                AND attempt >= 1
                AND lifecycle_receipt IS NOT NULL
                AND lifecycle_receipt_hash IS NOT NULL)
            OR (event_kind = 'missed'
                AND attempt = 0
                AND lifecycle_receipt IS NULL
                AND lifecycle_receipt_hash IS NULL)
        )
    )
    """,
    """
    CREATE TRIGGER scheduler_events_no_update
    BEFORE UPDATE ON scheduler_events
    BEGIN SELECT RAISE(ABORT, 'scheduler events are append-only'); END
    """,
    """
    CREATE TRIGGER scheduler_events_no_delete
    BEFORE DELETE ON scheduler_events
    BEGIN SELECT RAISE(ABORT, 'scheduler events are append-only'); END
    """,
    """
    CREATE TRIGGER scheduler_metadata_no_update
    BEFORE UPDATE ON scheduler_metadata
    BEGIN SELECT RAISE(ABORT, 'scheduler policy is immutable'); END
    """,
    """
    CREATE TRIGGER scheduler_metadata_no_delete
    BEFORE DELETE ON scheduler_metadata
    BEGIN SELECT RAISE(ABORT, 'scheduler policy is immutable'); END
    """,
)
_EXPECTED_SCHEMA = frozenset(" ".join(statement.split()) for statement in _SCHEMA)


class SchedulerPersistenceError(RuntimeError):
    """Report scheduler storage that cannot be trusted or safely changed."""


class SchedulerPolicyConflictError(SchedulerPersistenceError):
    """Report an existing ledger pinned to a different scheduler policy."""


def _invalid() -> SchedulerPersistenceError:
    return SchedulerPersistenceError("scheduler durable state is invalid")


def _policy_conflict() -> SchedulerPolicyConflictError:
    return SchedulerPolicyConflictError("scheduler policy conflicts with durable history")


class SQLiteSchedulerLedger:
    """Serialize claims and validate all authoritative scheduler history."""

    def __init__(self, state_root: Path, policy: SchedulerPolicy) -> None:
        self._database = _prepare_database(state_root, policy)
        self._lock = _prepare_lock(state_root)

    @contextmanager
    def exclusive_run(self) -> Iterator[None]:
        """Hold one OS-released lock across claim, lifecycle call, and observation."""
        descriptor: int | None = None
        try:
            descriptor = os.open(self._lock, os.O_RDWR | os.O_NOFOLLOW)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as error:
            if descriptor is not None:
                os.close(descriptor)
            raise _invalid() from error
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def claim(
        self,
        policy: SchedulerPolicy,
        window: SessionWindow,
        recorded_at: UtcInstant,
    ) -> SchedulerClaim:
        """Atomically acquire or observe the bounded attempt for one session."""
        _require_policy(policy)
        with closing(_connect(self._database)) as connection, connection:
            connection.execute(_BEGIN_IMMEDIATE)
            _validate_database(connection, policy)
            history = _load_cycle(connection, window, validate_all=True)
            current = _status_from_history(window, history)
            if current is not None:
                if recorded_at.value < current.recorded_at.value:
                    raise _invalid()
                if current.disposition not in (
                    ScheduledRunDisposition.STARTED,
                    ScheduledRunDisposition.RESUMED,
                ):
                    return SchedulerClaim(
                        window, SchedulerClaimDisposition.TERMINAL, current.attempts, current
                    )
                elapsed = (recorded_at.value - current.recorded_at.value).total_seconds()
                if elapsed < policy.recovery_delay_seconds:
                    return SchedulerClaim(
                        window, SchedulerClaimDisposition.WAIT, current.attempts, current
                    )
                attempt = current.attempts + 1
                status = _append_event(
                    connection,
                    window,
                    "resumed",
                    attempt,
                    recorded_at,
                )
                return SchedulerClaim(window, SchedulerClaimDisposition.RESUME, attempt, status)
            if recorded_at.value > window.missed_at.value:
                status = _append_event(connection, window, "missed", 0, recorded_at)
                return SchedulerClaim(window, SchedulerClaimDisposition.MISSED, 0, status)
            if recorded_at.value < window.scheduled_at.value:
                raise _invalid()
            status = _append_event(connection, window, "started", 1, recorded_at)
            return SchedulerClaim(window, SchedulerClaimDisposition.START, 1, status)

    def record_outcome(
        self,
        policy: SchedulerPolicy,
        claim: SchedulerClaim,
        receipt: AdvanceReceipt,
        recorded_at: UtcInstant,
    ) -> ScheduledSessionStatus:
        """Append the exact public lifecycle receipt observed for an owned claim."""
        _require_policy(policy)
        if type(receipt) is not AdvanceReceipt:
            raise _invalid()
        if not receipt_matches_session(receipt, claim.window.cycle):
            raise _invalid()
        payload = receipt.to_payload()
        receipt_text = _canonical_json(payload)
        receipt_hash = payload.get("content_hash")
        if type(receipt_hash) is not str or len(receipt_hash) != _HASH_LENGTH:
            raise _invalid()
        kind = "refused" if receipt.disposition.value == "failed_closed" else "completed"
        with closing(_connect(self._database)) as connection, connection:
            connection.execute(_BEGIN_IMMEDIATE)
            _validate_database(connection, policy)
            history = _load_cycle(connection, claim.window, validate_all=True)
            current = _status_from_history(claim.window, history)
            if current is None or current.disposition not in (
                ScheduledRunDisposition.STARTED,
                ScheduledRunDisposition.RESUMED,
            ):
                raise _invalid()
            if current.attempts != claim.attempt or recorded_at.value < current.recorded_at.value:
                raise _invalid()
            return _append_event(
                connection,
                claim.window,
                kind,
                claim.attempt,
                recorded_at,
                receipt_text=receipt_text,
                receipt_hash=receipt_hash,
            )

    def snapshot(self, policy: SchedulerPolicy) -> SchedulerSnapshot:
        """Rebuild bounded scheduler status from validated append-only events."""
        _require_policy(policy)
        with closing(_connect(self._database)) as connection:
            _validate_database(connection, policy)
            cycles = connection.execute(
                "SELECT DISTINCT cycle FROM scheduler_events ORDER BY cycle"
            ).fetchall()
            sessions: list[ScheduledSessionStatus] = []
            for row in cycles:
                cycle = _parse_cycle(row[0])
                window = build_session_window(
                    cycle,
                    policy.advance_minutes_before_open,
                    policy.maximum_lateness_minutes,
                )
                if window is None or cycle.trading_date < policy.first_session.trading_date:
                    raise _invalid()
                status = _status_from_history(
                    window, _load_cycle(connection, window, validate_all=False)
                )
                if status is None:
                    raise _invalid()
                sessions.append(status)
            return SchedulerSnapshot(policy.policy_id, tuple(sessions))


def _prepare_database(  # noqa: PLR0912 - storage validation fails each unsafe state directly.
    state_root: Path, policy: SchedulerPolicy
) -> Path:
    try:
        if state_root.is_symlink():
            raise _invalid()
        if state_root.exists():
            if not state_root.is_dir() or stat.S_IMODE(state_root.stat().st_mode) & 0o077:
                raise _invalid()
        else:
            state_root.mkdir(mode=0o700)
        database = state_root / _DATABASE_NAME
        if database.is_symlink():
            raise _invalid()
        if not database.exists():
            descriptor = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        elif not database.is_file() or stat.S_IMODE(database.stat().st_mode) & 0o077:
            raise _invalid()
        with closing(_connect(database)) as connection, connection:
            connection.execute(_BEGIN_IMMEDIATE)
            version = connection.execute("PRAGMA user_version").fetchone()
            schema = _schema_signature(connection)
            if version == (0,) and not schema:
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
                connection.execute(
                    "INSERT INTO scheduler_metadata VALUES (1, ?, ?)",
                    (policy.policy_id, _canonical_json(policy.to_payload())),
                )
            elif version != (_SCHEMA_VERSION,) or schema != _EXPECTED_SCHEMA:
                raise _invalid()
            _validate_policy(connection, policy)
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise _invalid()
        return database
    except sqlite3.Error as error:
        raise _invalid() from error
    except OSError as error:
        raise _invalid() from error


def _prepare_lock(state_root: Path) -> Path:
    lock = state_root / _LOCK_NAME
    try:
        if lock.is_symlink():
            raise _invalid()
        if not lock.exists():
            descriptor = os.open(
                lock,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            )
            os.close(descriptor)
        elif not lock.is_file() or stat.S_IMODE(lock.stat().st_mode) & 0o077:
            raise _invalid()
        return lock
    except OSError as error:
        raise _invalid() from error


def _connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{database.as_uri()}?mode=rw", uri=True, timeout=5)
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _schema_signature(connection: sqlite3.Connection) -> frozenset[str]:
    rows = connection.execute(
        """
        SELECT sql FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite!_%' ESCAPE '!' AND sql IS NOT NULL
        """
    ).fetchall()
    return frozenset(" ".join(str(row[0]).split()) for row in rows)


def _validate_policy(connection: sqlite3.Connection, policy: SchedulerPolicy) -> None:
    row = connection.execute(
        "SELECT policy_id, policy_payload FROM scheduler_metadata WHERE singleton = 1"
    ).fetchone()
    expected = (policy.policy_id, _canonical_json(policy.to_payload()))
    if row != expected:
        raise _policy_conflict()


def _validate_database(connection: sqlite3.Connection, policy: SchedulerPolicy) -> None:
    if (
        connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,)
        or _schema_signature(connection) != _EXPECTED_SCHEMA
        or connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
    ):
        raise _invalid()
    _validate_policy(connection, policy)


def _require_policy(policy: SchedulerPolicy) -> None:
    if type(policy) is not SchedulerPolicy:
        raise _invalid()


def _load_cycle(
    connection: sqlite3.Connection,
    window: SessionWindow,
    *,
    validate_all: bool,
) -> list[tuple[object, ...]]:
    if validate_all:
        rows = connection.execute(
            """
            SELECT cycle, sequence, event_kind, scheduled_at, missed_at, attempt,
                   lifecycle_receipt, lifecycle_receipt_hash, recorded_at, event_hash
            FROM scheduler_events ORDER BY cycle, sequence
            """
        ).fetchall()
        grouped = [row for row in rows if row[0] == window.cycle.isoformat()]
        for cycle_text in sorted({row[0] for row in rows}):
            cycle = _parse_cycle(cycle_text)
            other_window = build_session_window(
                cycle,
                int((window.opens_at.value - window.scheduled_at.value).total_seconds() // 60),
                int((window.missed_at.value - window.scheduled_at.value).total_seconds() // 60),
            )
            if other_window is None:
                raise _invalid()
            _validate_history(other_window, [row for row in rows if row[0] == cycle_text])
        return grouped
    rows = connection.execute(
        """
        SELECT cycle, sequence, event_kind, scheduled_at, missed_at, attempt,
               lifecycle_receipt, lifecycle_receipt_hash, recorded_at, event_hash
        FROM scheduler_events WHERE cycle = ? ORDER BY sequence
        """,
        (window.cycle.isoformat(),),
    ).fetchall()
    _validate_history(window, rows)
    return rows


def _validate_history(  # noqa: PLR0912 - each invalid event transition fails closed.
    window: SessionWindow, rows: list[tuple[object, ...]]
) -> None:
    terminal = False
    attempts = 0
    previous_at: UtcInstant | None = None
    for expected_sequence, row in enumerate(rows, start=1):
        if (
            len(row) != _EVENT_COLUMN_COUNT
            or row[0] != window.cycle.isoformat()
            or row[1] != expected_sequence
        ):
            raise _invalid()
        kind = row[2]
        if row[3] != window.scheduled_at.isoformat() or row[4] != window.missed_at.isoformat():
            raise _invalid()
        attempt = row[5]
        if type(attempt) is not int:
            raise _invalid()
        try:
            recorded_at = UtcInstant.parse(row[8])
        except InvalidUtcInstantError as error:
            raise _invalid() from error
        if previous_at is not None and recorded_at.value < previous_at.value:
            raise _invalid()
        previous_at = recorded_at
        material = {
            "cycle": row[0],
            "sequence": row[1],
            "event_kind": kind,
            "scheduled_at": row[3],
            "missed_at": row[4],
            "attempt": attempt,
            "lifecycle_receipt": row[6],
            "lifecycle_receipt_hash": row[7],
            "recorded_at": row[8],
        }
        if row[9] != _hash(material):
            raise _invalid()
        if terminal:
            raise _invalid()
        if kind == "started":
            if (
                expected_sequence != 1
                or attempt != 1
                or recorded_at.value < window.scheduled_at.value
                or recorded_at.value > window.missed_at.value
            ):
                raise _invalid()
            attempts = 1
        elif kind == "resumed":
            if (
                attempts == 0
                or attempt != attempts + 1
                or recorded_at.value < window.scheduled_at.value
            ):
                raise _invalid()
            attempts = attempt
        elif kind in ("completed", "refused"):
            if attempts == 0 or attempt != attempts or type(row[6]) is not str:
                raise _invalid()
            _validate_receipt_text(row[6], row[7], kind, window.cycle)
            terminal = True
        elif kind == "missed":
            if (
                expected_sequence != 1
                or attempt != 0
                or recorded_at.value <= window.missed_at.value
            ):
                raise _invalid()
            terminal = True
        else:
            raise _invalid()


def _validate_receipt_text(
    value: str,
    expected_hash: object,
    event_kind: object,
    cycle: MarketSession,
) -> None:
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError) as error:
        raise _invalid() from error
    if type(payload) is not dict or _canonical_json(payload) != value:
        raise _invalid()
    parsed = parse_advance_receipt(payload)
    if (
        type(parsed) is not AdvanceReceipt
        or _canonical_json(parsed.to_payload()) != value
        or payload.get("content_hash") != expected_hash
        or (parsed.disposition.value == "failed_closed") != (event_kind == "refused")
        or not receipt_matches_session(parsed, cycle)
    ):
        raise _invalid()


def _status_from_history(
    window: SessionWindow, rows: list[tuple[object, ...]]
) -> ScheduledSessionStatus | None:
    if not rows:
        return None
    row = rows[-1]
    kind = row[2]
    attempt = row[5]
    if type(kind) is not str or type(attempt) is not int:
        raise _invalid()
    disposition = {
        "started": ScheduledRunDisposition.STARTED,
        "resumed": ScheduledRunDisposition.RESUMED,
        "completed": ScheduledRunDisposition.COMPLETED,
        "missed": ScheduledRunDisposition.MISSED,
        "refused": ScheduledRunDisposition.REFUSED,
    }.get(kind)
    if disposition is None:
        raise _invalid()
    return ScheduledSessionStatus(
        window.cycle,
        window.scheduled_at,
        window.missed_at,
        disposition,
        attempt,
        UtcInstant.parse(row[8]),
        None if row[7] is None else str(row[7]),
    )


def _append_event(  # noqa: PLR0913 - one event carries its complete durable material.
    connection: sqlite3.Connection,
    window: SessionWindow,
    kind: str,
    attempt: int,
    recorded_at: UtcInstant,
    *,
    receipt_text: str | None = None,
    receipt_hash: str | None = None,
) -> ScheduledSessionStatus:
    row = connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM scheduler_events WHERE cycle = ?",
        (window.cycle.isoformat(),),
    ).fetchone()
    if row is None or type(row[0]) is not int:
        raise _invalid()
    material: dict[str, object] = {
        "cycle": window.cycle.isoformat(),
        "sequence": row[0],
        "event_kind": kind,
        "scheduled_at": window.scheduled_at.isoformat(),
        "missed_at": window.missed_at.isoformat(),
        "attempt": attempt,
        "lifecycle_receipt": receipt_text,
        "lifecycle_receipt_hash": receipt_hash,
        "recorded_at": recorded_at.isoformat(),
    }
    connection.execute(
        "INSERT INTO scheduler_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*material.values(), _hash(material)),
    )
    status = _status_from_history(
        window,
        connection.execute(
            """
            SELECT cycle, sequence, event_kind, scheduled_at, missed_at, attempt,
                   lifecycle_receipt, lifecycle_receipt_hash, recorded_at, event_hash
            FROM scheduler_events WHERE cycle = ? ORDER BY sequence
            """,
            (window.cycle.isoformat(),),
        ).fetchall(),
    )
    if status is None:
        raise _invalid()
    return status


def _parse_cycle(value: object) -> MarketSession:
    if type(value) is not str:
        raise _invalid()
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise _invalid() from error
    if parsed.isoformat() != value:
        raise _invalid()
    return MarketSession(parsed)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()
