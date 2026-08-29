from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest

from agentic_investment_os.adapters.sqlite_scheduler import (
    SchedulerPersistenceError,
    SQLiteSchedulerLedger,
)
from agentic_investment_os.application.scheduler import (
    AdvanceCapability,
    Scheduler,
    SchedulerCalendarError,
)
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.lifecycle import (
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceReceipt,
    LifecycleStatus,
)
from agentic_investment_os.domain.scheduler import (
    PINNED_XNYS_CALENDAR_ID,
    ScheduledRunDisposition,
    SchedulerClaim,
    SchedulerClaimDisposition,
    SchedulerPolicy,
    build_session_window,
)
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.entrypoints.scheduler import (
    SchedulerConfigurationRefusal,
    configure_scheduler,
)
from tests._universe import runtime_configuration


def _policy(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "policy_type": "market_session_advance",
        "asset_class": "us_equity",
        "calendar_id": PINNED_XNYS_CALENDAR_ID,
        "first_session": "2026-08-28",
        "advance_minutes_before_open": 60,
        "maximum_lateness_minutes": 30,
        "recovery_delay_seconds": 300,
        "maximum_actions_per_run": 20,
    }
    values.update(overrides)
    return values


def _parsed_policy(**overrides: object) -> SchedulerPolicy:
    parsed = SchedulerPolicy.parse(_policy(**overrides))
    assert isinstance(parsed, SchedulerPolicy)
    return parsed


@dataclass
class FrozenClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


@dataclass
class RefusingAdvance:
    calls: list[tuple[object, object, object]]

    def __call__(self, *, cycle: object, mode: object, idempotency_key: object) -> AdvanceReceipt:
        self.calls.append((cycle, mode, idempotency_key))
        return AdvanceReceipt.failed_closed(
            AdvanceFailureReason.INVALID_DURABLE_STATE,
        )


@dataclass
class RaisingAdvance:
    calls: list[tuple[object, object, object]]

    def __call__(self, *, cycle: object, mode: object, idempotency_key: object) -> AdvanceReceipt:
        self.calls.append((cycle, mode, idempotency_key))
        message = "simulated process interruption"
        raise RuntimeError(message)


@dataclass
class RaisingOSErrorAdvance:
    def __call__(self, *, cycle: object, mode: object, idempotency_key: object) -> AdvanceReceipt:
        _ = cycle, mode, idempotency_key
        message = "simulated lifecycle filesystem failure"
        raise OSError(message)


@dataclass
class WrongCycleAdvance:
    def __call__(self, *, cycle: object, mode: object, idempotency_key: object) -> AdvanceReceipt:
        _ = cycle, mode, idempotency_key
        return AdvanceReceipt.failed_closed(
            AdvanceFailureReason.INVALID_DURABLE_STATE,
            cycle=MarketSession(date(2026, 9, 1)),
        )


@dataclass
class BlockingAdvance:
    calls: list[tuple[object, object, object]]
    entered: Event
    release: Event

    def __call__(self, *, cycle: object, mode: object, idempotency_key: object) -> AdvanceReceipt:
        self.calls.append((cycle, mode, idempotency_key))
        self.entered.set()
        if not self.release.wait(timeout=5):
            message = "test did not release blocking advance"
            raise RuntimeError(message)
        return AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)


class InvalidAdvanceReceipt(AdvanceReceipt):
    """Represent an exact-type boundary violation without constructing invalid production data."""


@dataclass(frozen=True)
class InvalidAdvanceResult:
    def __call__(self, *, cycle: object, mode: object, idempotency_key: object) -> AdvanceReceipt:
        _ = cycle, mode, idempotency_key
        return object.__new__(InvalidAdvanceReceipt)


@dataclass(frozen=True)
class IncompleteAdvanceResult:
    def __call__(self, *, cycle: object, mode: object, idempotency_key: object) -> AdvanceReceipt:
        _ = cycle, mode, idempotency_key
        receipt = object.__new__(AdvanceReceipt)
        object.__setattr__(receipt, "disposition", AdvanceDisposition.ADVANCED)
        return receipt


class InvalidLifecycleStatus(LifecycleStatus):
    """Represent an exact-type boundary violation without constructing invalid production data."""


@dataclass(frozen=True)
class InvalidStatus:
    def __call__(self) -> LifecycleStatus:
        return object.__new__(InvalidLifecycleStatus)


@dataclass(frozen=True)
class NotStartedStatus:
    def __call__(self) -> LifecycleStatus:
        return LifecycleStatus.not_started()


def _scheduler(
    root: Path,
    *,
    now: datetime,
    advance: AdvanceCapability,
    policy: object | None = None,
) -> Scheduler:
    configured = configure_scheduler(
        runtime_configuration(root),
        scheduler_policy=_policy() if policy is None else policy,
        repository_root=Path(__file__).resolve().parents[2],
        advance=advance,
        status=NotStartedStatus(),
        clock=FrozenClock(now),
    )
    assert isinstance(configured, Scheduler)
    return configured


def test_scheduler_records_missed_sessions_without_backfilling_lifecycle(tmp_path: Path) -> None:
    calls: list[tuple[object, object, object]] = []
    scheduler = _scheduler(
        tmp_path / "runtime",
        now=datetime(2026, 8, 31, 15, 0, tzinfo=UTC),
        advance=RefusingAdvance(calls),
    )

    first = scheduler()
    replayed = scheduler()
    status = scheduler.status()

    assert calls == []
    assert [item.disposition for item in first.sessions] == [
        ScheduledRunDisposition.MISSED,
        ScheduledRunDisposition.MISSED,
    ]
    assert replayed.sessions == first.sessions
    assert status.sessions == first.sessions
    assert [item.cycle.trading_date.isoformat() for item in status.sessions] == [
        "2026-08-28",
        "2026-08-31",
    ]
    assert status.pending is not None
    assert status.pending.disposition is ScheduledRunDisposition.PENDING
    assert status.pending.cycle.trading_date.isoformat() == "2026-09-01"


def test_scheduler_reports_no_pending_session_after_the_pinned_calendar_horizon(
    tmp_path: Path,
) -> None:
    scheduler = _scheduler(
        tmp_path / "runtime",
        now=datetime(2026, 12, 31, 20, 0, tzinfo=UTC),
        advance=RefusingAdvance([]),
        policy=_policy(first_session="2026-12-31"),
    )

    receipt = scheduler()

    assert receipt.sessions[0].disposition is ScheduledRunDisposition.MISSED
    assert receipt.pending is None
    assert receipt.next_scheduled_at is None


def test_action_bound_reports_oldest_unclaimed_due_session_as_pending(tmp_path: Path) -> None:
    scheduler = _scheduler(
        tmp_path / "runtime",
        now=datetime(2026, 8, 31, 15, 0, tzinfo=UTC),
        advance=RefusingAdvance([]),
        policy=_policy(maximum_actions_per_run=1),
    )

    receipt = scheduler()

    assert [item.cycle.isoformat() for item in receipt.sessions] == ["2026-08-28"]
    assert receipt.pending is not None
    assert receipt.pending.disposition is ScheduledRunDisposition.PENDING
    assert receipt.pending.cycle.isoformat() == "2026-08-31"


def test_action_bound_prioritizes_a_currently_eligible_session_over_missed_bookkeeping(
    tmp_path: Path,
) -> None:
    calls: list[tuple[object, object, object]] = []
    scheduler = _scheduler(
        tmp_path / "runtime",
        now=datetime(2026, 8, 28, 12, 45, tzinfo=UTC),
        advance=RefusingAdvance(calls),
        policy=_policy(first_session="2026-08-25", maximum_actions_per_run=1),
    )

    receipt = scheduler()

    assert len(calls) == 1
    assert calls[0][0] == MarketSession(date(2026, 8, 28)).to_payload()
    assert receipt.sessions[0].cycle == MarketSession(date(2026, 8, 28))
    assert receipt.pending is not None
    assert receipt.pending.cycle == MarketSession(date(2026, 8, 25))


def test_scheduler_invokes_one_public_advance_with_a_stable_identity(tmp_path: Path) -> None:
    calls: list[tuple[object, object, object]] = []
    scheduler = _scheduler(
        tmp_path / "runtime",
        now=datetime(2026, 8, 28, 12, 45, tzinfo=UTC),
        advance=RefusingAdvance(calls),
    )

    receipt = scheduler()
    replay = scheduler()

    assert len(calls) == 1
    assert calls[0][1] == "champion"
    assert calls[0][2] == f"schedule:{scheduler.policy.policy_id}:2026-08-28:advance"
    assert receipt.sessions[0].disposition is ScheduledRunDisposition.REFUSED
    assert replay.sessions == receipt.sessions


def test_scheduler_rejects_invalid_public_lifecycle_observations(tmp_path: Path) -> None:
    invalid_advance = _scheduler(
        tmp_path / "invalid-advance",
        now=datetime(2026, 8, 28, 12, 45, tzinfo=UTC),
        advance=InvalidAdvanceResult(),
    )
    with pytest.raises(RuntimeError, match="Advance"):
        invalid_advance()

    invalid_status = configure_scheduler(
        runtime_configuration(tmp_path / "invalid-status"),
        scheduler_policy=_policy(),
        repository_root=Path(__file__).resolve().parents[2],
        advance=RefusingAdvance([]),
        status=InvalidStatus(),
        clock=FrozenClock(datetime(2026, 8, 28, 12, 45, tzinfo=UTC)),
    )
    assert isinstance(invalid_status, Scheduler)
    with pytest.raises(RuntimeError, match="Status"):
        invalid_status()

    mismatched_status = _scheduler(
        tmp_path / "mismatched-status",
        now=datetime(2026, 8, 28, 12, 45, tzinfo=UTC),
        advance=IncompleteAdvanceResult(),
    )
    with pytest.raises(RuntimeError, match="Status"):
        mismatched_status()

    wrong_cycle = _scheduler(
        tmp_path / "wrong-cycle",
        now=datetime(2026, 8, 28, 12, 45, tzinfo=UTC),
        advance=WrongCycleAdvance(),
    )
    with pytest.raises(RuntimeError, match="Advance"):
        wrong_cycle()


def test_interrupted_run_waits_then_resumes_with_the_same_advance_identity(
    tmp_path: Path,
) -> None:
    calls: list[tuple[object, object, object]] = []
    root = tmp_path / "runtime"
    first = _scheduler(
        root,
        now=datetime(2026, 8, 28, 12, 45, tzinfo=UTC),
        advance=RaisingAdvance(calls),
    )

    with pytest.raises(RuntimeError, match="simulated process interruption"):
        first()

    immediate = _scheduler(
        root,
        now=datetime(2026, 8, 28, 12, 46, tzinfo=UTC),
        advance=RefusingAdvance(calls),
    )
    waiting = immediate()
    assert len(calls) == 1
    assert waiting.sessions[0].disposition is ScheduledRunDisposition.STARTED

    interrupted_retry = _scheduler(
        root,
        now=datetime(2026, 8, 28, 12, 51, tzinfo=UTC),
        advance=RaisingAdvance(calls),
    )
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        interrupted_retry()
    resumed_status = interrupted_retry.status()
    assert resumed_status.sessions[0].disposition is ScheduledRunDisposition.RESUMED
    assert resumed_status.sessions[0].attempts == len(calls)

    resumed = _scheduler(
        root,
        now=datetime(2026, 8, 28, 12, 57, tzinfo=UTC),
        advance=RefusingAdvance(calls),
    )()
    assert len(calls) == resumed.sessions[0].attempts
    assert calls[0] == calls[1]
    assert calls[1] == calls[2]
    assert resumed.sessions[0].disposition is ScheduledRunDisposition.REFUSED
    assert resumed.sessions[0].attempts == len(calls)


def test_clock_rollback_after_a_started_attempt_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    first = _scheduler(
        root,
        now=datetime(2026, 8, 28, 12, 45, tzinfo=UTC),
        advance=RaisingAdvance([]),
    )
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        first()

    rolled_back = _scheduler(
        root,
        now=datetime(2026, 8, 28, 12, 44, tzinfo=UTC),
        advance=RefusingAdvance([]),
    )
    with pytest.raises(RuntimeError, match="precedes durable scheduler history"):
        rolled_back()


def test_clock_rollback_after_a_terminal_observation_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _scheduler(
        root,
        now=datetime(2026, 8, 28, 12, 45, tzinfo=UTC),
        advance=RefusingAdvance([]),
    )()
    rolled_back = _scheduler(
        root,
        now=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        advance=RefusingAdvance([]),
    )

    with pytest.raises(RuntimeError, match="precedes durable scheduler history"):
        rolled_back.status()
    with pytest.raises(RuntimeError, match="precedes durable scheduler history"):
        rolled_back()


def test_lifecycle_oserror_preserves_its_failure_domain(tmp_path: Path) -> None:
    scheduler = _scheduler(
        tmp_path / "runtime",
        now=datetime(2026, 8, 28, 12, 45, tzinfo=UTC),
        advance=RaisingOSErrorAdvance(),
    )

    with pytest.raises(OSError, match="lifecycle filesystem failure"):
        scheduler()
    assert scheduler.status().sessions[0].disposition is ScheduledRunDisposition.STARTED


def test_changed_scheduler_policy_conflicts_without_invoking_advance(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    calls: list[tuple[object, object, object]] = []
    _scheduler(
        root,
        now=datetime(2026, 8, 28, 12, 45, tzinfo=UTC),
        advance=RefusingAdvance(calls),
    )()

    changed = configure_scheduler(
        runtime_configuration(root),
        scheduler_policy=_policy(maximum_lateness_minutes=45),
        repository_root=Path(__file__).resolve().parents[2],
        advance=RefusingAdvance(calls),
        status=NotStartedStatus(),
        clock=FrozenClock(datetime(2026, 8, 28, 12, 50, tzinfo=UTC)),
    )

    assert isinstance(changed, SchedulerConfigurationRefusal)
    assert changed.fields == ("scheduler_policy",)
    assert len(calls) == 1


def test_concurrent_scheduler_instances_create_one_public_advance_request(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    calls: list[tuple[object, object, object]] = []
    entered = Event()
    release = Event()
    clock = datetime(2026, 8, 28, 12, 45, tzinfo=UTC)
    first = _scheduler(
        root,
        now=clock,
        advance=BlockingAdvance(calls, entered, release),
    )
    second = _scheduler(root, now=clock, advance=RefusingAdvance(calls))

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_result = executor.submit(first)
        assert entered.wait(timeout=5)
        second_result = executor.submit(second)
        release.set()
        first_receipt = first_result.result(timeout=5)
        second_receipt = second_result.result(timeout=5)

    assert len(calls) == 1
    assert first_receipt.sessions == second_receipt.sessions


def test_scheduler_normalizes_explicit_host_zone_and_refuses_naive_time(
    tmp_path: Path,
) -> None:
    calls: list[tuple[object, object, object]] = []
    host_zone = timezone(timedelta(hours=10))
    scheduler = _scheduler(
        tmp_path / "host-zone",
        now=datetime(2026, 8, 28, 22, 45, tzinfo=host_zone),
        advance=RefusingAdvance(calls),
    )

    receipt = scheduler()

    assert receipt.recorded_at.value == datetime(2026, 8, 28, 12, 45, tzinfo=UTC)
    assert len(calls) == 1

    naive = _scheduler(
        tmp_path / "naive",
        now=datetime(2026, 8, 28, 12, 45),  # noqa: DTZ001 - hostile naive clock fixture.
        advance=RefusingAdvance([]),
    )
    with pytest.raises(RuntimeError, match="timezone-aware"):
        naive()


def test_scheduler_refuses_when_pinned_calendar_does_not_cover_current_year(
    tmp_path: Path,
) -> None:
    scheduler = _scheduler(
        tmp_path / "unsupported-year",
        now=datetime(2027, 1, 4, 12, 45, tzinfo=UTC),
        advance=RefusingAdvance([]),
    )

    with pytest.raises(SchedulerCalendarError, match="calendar"):
        scheduler()
    with pytest.raises(SchedulerCalendarError, match="calendar"):
        scheduler.status()


def test_scheduler_history_is_append_only_and_schema_validated_on_every_read(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    scheduler = _scheduler(
        root,
        now=datetime(2026, 8, 28, 15, 0, tzinfo=UTC),
        advance=RefusingAdvance([]),
    )
    scheduler()
    database = root / "scheduler.sqlite3"

    with (
        sqlite3.connect(database) as connection,
        pytest.raises(sqlite3.DatabaseError, match="append-only"),
    ):
        connection.execute("DELETE FROM scheduler_events")
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER scheduler_events_no_update")

    with pytest.raises(SchedulerPersistenceError):
        scheduler.status()


def test_scheduler_storage_refuses_unsafe_root_database_and_lock_paths(
    tmp_path: Path,
) -> None:
    policy = _parsed_policy()
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(target, target_is_directory=True)
    with pytest.raises(SchedulerPersistenceError):
        SQLiteSchedulerLedger(linked_root, policy)

    public_root = tmp_path / "public-root"
    public_root.mkdir(mode=0o755)
    public_root.chmod(0o755)
    with pytest.raises(SchedulerPersistenceError):
        SQLiteSchedulerLedger(public_root, policy)

    database_root = tmp_path / "database-root"
    database_root.mkdir(mode=0o700)
    database_target = tmp_path / "database-target"
    database_target.touch(mode=0o600)
    (database_root / "scheduler.sqlite3").symlink_to(database_target)
    with pytest.raises(SchedulerPersistenceError):
        SQLiteSchedulerLedger(database_root, policy)

    public_database_root = tmp_path / "public-database-root"
    public_database_root.mkdir(mode=0o700)
    public_database = public_database_root / "scheduler.sqlite3"
    public_database.touch(mode=0o644)
    public_database.chmod(0o644)
    with pytest.raises(SchedulerPersistenceError):
        SQLiteSchedulerLedger(public_database_root, policy)

    lock_root = tmp_path / "lock-root"
    SQLiteSchedulerLedger(lock_root, policy)
    lock = lock_root / "scheduler.lock"
    lock.unlink()
    lock.symlink_to(database_target)
    with pytest.raises(SchedulerPersistenceError):
        SQLiteSchedulerLedger(lock_root, policy)

    public_lock_root = tmp_path / "public-lock-root"
    SQLiteSchedulerLedger(public_lock_root, policy)
    public_lock = public_lock_root / "scheduler.lock"
    public_lock.chmod(0o644)
    with pytest.raises(SchedulerPersistenceError):
        SQLiteSchedulerLedger(public_lock_root, policy)

    missing_lock_root = tmp_path / "missing-lock-root"
    missing_lock = SQLiteSchedulerLedger(missing_lock_root, policy)
    (missing_lock_root / "scheduler.lock").unlink()
    with pytest.raises(SchedulerPersistenceError), missing_lock.exclusive_run():
        pass


def test_scheduler_rejects_terminal_suffix_and_unknown_calendar_cycle(
    tmp_path: Path,
) -> None:
    terminal_root = tmp_path / "terminal"
    terminal = _scheduler(
        terminal_root,
        now=datetime(2026, 8, 28, 15, 0, tzinfo=UTC),
        advance=RefusingAdvance([]),
    )
    terminal()
    database = terminal_root / "scheduler.sqlite3"
    with sqlite3.connect(database) as connection:
        first = connection.execute(
            "SELECT cycle, scheduled_at, missed_at FROM scheduler_events"
        ).fetchone()
        assert first is not None
        _insert_scheduler_event(
            connection,
            cycle=str(first[0]),
            sequence=2,
            event_kind="resumed",
            scheduled_at=str(first[1]),
            missed_at=str(first[2]),
            attempt=1,
            recorded_at="2026-08-28T15:01:00.000000+00:00",
        )
    with pytest.raises(SchedulerPersistenceError):
        terminal.status()

    unknown_root = tmp_path / "unknown"
    unknown = _scheduler(
        unknown_root,
        now=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        advance=RefusingAdvance([]),
    )
    with sqlite3.connect(unknown_root / "scheduler.sqlite3") as connection:
        _insert_scheduler_event(
            connection,
            cycle="not-a-date",
            sequence=1,
            event_kind="started",
            scheduled_at="2026-08-28T12:30:00.000000+00:00",
            missed_at="2026-08-28T13:00:00.000000+00:00",
            attempt=1,
            recorded_at="2026-08-28T12:45:00.000000+00:00",
        )
    with pytest.raises(SchedulerPersistenceError):
        unknown.status()


def test_scheduler_reparses_durable_lifecycle_receipt(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    scheduler = _scheduler(
        root,
        now=datetime(2026, 8, 28, 12, 45, tzinfo=UTC),
        advance=RefusingAdvance([]),
    )
    scheduler()
    database = root / "scheduler.sqlite3"
    with sqlite3.connect(database) as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name = 'scheduler_events_no_update'"
        ).fetchone()
        row = connection.execute(
            """
            SELECT cycle, sequence, event_kind, scheduled_at, missed_at, attempt, recorded_at
            FROM scheduler_events WHERE event_kind = 'refused'
            """
        ).fetchone()
        assert trigger is not None
        assert isinstance(trigger[0], str)
        assert row is not None
        bad_receipt = "{}"
        bad_hash = "0" * 64
        material = {
            "cycle": row[0],
            "sequence": row[1],
            "event_kind": row[2],
            "scheduled_at": row[3],
            "missed_at": row[4],
            "attempt": row[5],
            "lifecycle_receipt": bad_receipt,
            "lifecycle_receipt_hash": bad_hash,
            "recorded_at": row[6],
        }
        connection.execute("DROP TRIGGER scheduler_events_no_update")
        connection.execute(
            """
            UPDATE scheduler_events
            SET lifecycle_receipt = ?, lifecycle_receipt_hash = ?, event_hash = ?
            WHERE event_kind = 'refused'
            """,
            (bad_receipt, bad_hash, _scheduler_hash(material)),
        )
        connection.execute(trigger[0])
    with pytest.raises(SchedulerPersistenceError):
        scheduler.status()


def test_scheduler_rejects_a_reopened_receipt_bound_to_another_cycle(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    scheduler = _scheduler(
        root,
        now=datetime(2026, 8, 28, 12, 45, tzinfo=UTC),
        advance=RefusingAdvance([]),
    )
    scheduler()
    database = root / "scheduler.sqlite3"
    wrong_receipt = AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=MarketSession(date(2026, 9, 1)),
    ).to_payload()
    wrong_receipt_text = json.dumps(wrong_receipt, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(database) as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name = 'scheduler_events_no_update'"
        ).fetchone()
        row = connection.execute(
            """
            SELECT cycle, sequence, event_kind, scheduled_at, missed_at, attempt, recorded_at
            FROM scheduler_events WHERE event_kind = 'refused'
            """
        ).fetchone()
        assert trigger is not None
        assert isinstance(trigger[0], str)
        assert row is not None
        material = {
            "cycle": row[0],
            "sequence": row[1],
            "event_kind": row[2],
            "scheduled_at": row[3],
            "missed_at": row[4],
            "attempt": row[5],
            "lifecycle_receipt": wrong_receipt_text,
            "lifecycle_receipt_hash": wrong_receipt["content_hash"],
            "recorded_at": row[6],
        }
        connection.execute("DROP TRIGGER scheduler_events_no_update")
        connection.execute(
            """
            UPDATE scheduler_events
            SET lifecycle_receipt = ?, lifecycle_receipt_hash = ?, event_hash = ?
            WHERE event_kind = 'refused'
            """,
            (
                wrong_receipt_text,
                wrong_receipt["content_hash"],
                _scheduler_hash(material),
            ),
        )
        connection.execute(trigger[0])

    with pytest.raises(SchedulerPersistenceError):
        scheduler.status()


@pytest.mark.parametrize(
    ("scheduled_at", "missed_at", "event_kind", "attempt", "recorded_at", "event_hash"),
    [
        (
            "2026-08-28T12:31:00.000000+00:00",
            "2026-08-28T13:00:00.000000+00:00",
            "started",
            1,
            "2026-08-28T12:45:00.000000+00:00",
            None,
        ),
        (
            "2026-08-28T12:30:00.000000+00:00",
            "2026-08-28T13:00:00.000000+00:00",
            "started",
            1,
            "invalid-time",
            None,
        ),
        (
            "2026-08-28T12:30:00.000000+00:00",
            "2026-08-28T13:00:00.000000+00:00",
            "started",
            1,
            "2026-08-28T12:45:00.000000+00:00",
            "0" * 64,
        ),
        (
            "2026-08-28T12:30:00.000000+00:00",
            "2026-08-28T13:00:00.000000+00:00",
            "started",
            1,
            "2026-08-28T12:00:00.000000+00:00",
            None,
        ),
        (
            "2026-08-28T12:30:00.000000+00:00",
            "2026-08-28T13:00:00.000000+00:00",
            "resumed",
            1,
            "2026-08-28T12:45:00.000000+00:00",
            None,
        ),
        (
            "2026-08-28T12:30:00.000000+00:00",
            "2026-08-28T13:00:00.000000+00:00",
            "missed",
            0,
            "2026-08-28T13:00:00.000000+00:00",
            None,
        ),
    ],
)
def test_scheduler_rejects_corrupt_event_history(  # noqa: PLR0913, PLR0917 - explicit durable envelope cases.
    tmp_path: Path,
    scheduled_at: str,
    missed_at: str,
    event_kind: str,
    attempt: int,
    recorded_at: str,
    event_hash: str | None,
) -> None:
    root = tmp_path / "runtime"
    scheduler = _scheduler(
        root,
        now=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        advance=RefusingAdvance([]),
    )
    with sqlite3.connect(root / "scheduler.sqlite3") as connection:
        _insert_scheduler_event(
            connection,
            cycle="2026-08-28",
            sequence=1,
            event_kind=event_kind,
            scheduled_at=scheduled_at,
            missed_at=missed_at,
            attempt=attempt,
            recorded_at=recorded_at,
            event_hash=event_hash,
        )

    with pytest.raises(SchedulerPersistenceError):
        scheduler.status()


def test_scheduler_ledger_rejects_invalid_claim_and_terminal_redelivery(
    tmp_path: Path,
) -> None:
    policy = _parsed_policy()
    window = build_session_window(MarketSession(date(2026, 8, 28)), 60, 30)
    assert window is not None
    now = UtcInstant.from_datetime(datetime(2026, 8, 28, 12, 45, tzinfo=UTC))
    later = UtcInstant.from_datetime(datetime(2026, 8, 28, 12, 46, tzinfo=UTC))
    ledger = SQLiteSchedulerLedger(tmp_path / "terminal", policy)
    claim = ledger.claim(policy, window, now)
    receipt = AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
    ledger.record_outcome(policy, claim, receipt, now)

    replay = ledger.claim(policy, window, later)
    assert replay.disposition is SchedulerClaimDisposition.TERMINAL
    with pytest.raises(SchedulerPersistenceError):
        ledger.record_outcome(policy, claim, receipt, later)

    active = SQLiteSchedulerLedger(tmp_path / "active", policy)
    active_claim = active.claim(policy, window, now)
    changed_claim = SchedulerClaim(window, active_claim.disposition, 2, active_claim.status)
    with pytest.raises(SchedulerPersistenceError):
        active.record_outcome(policy, changed_claim, receipt, later)

    class InvalidReceipt(AdvanceReceipt):
        pass

    invalid_receipt = object.__new__(InvalidReceipt)
    with pytest.raises(SchedulerPersistenceError):
        active.record_outcome(policy, active_claim, invalid_receipt, later)

    wrong_cycle_receipt = AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=MarketSession(date(2026, 9, 1)),
    )
    with pytest.raises(SchedulerPersistenceError):
        active.record_outcome(policy, active_claim, wrong_cycle_receipt, later)


def test_scheduler_claim_rejects_an_unrelated_calendar_cycle_in_history(
    tmp_path: Path,
) -> None:
    policy = _parsed_policy()
    root = tmp_path / "runtime"
    ledger = SQLiteSchedulerLedger(root, policy)
    with sqlite3.connect(root / "scheduler.sqlite3") as connection:
        _insert_scheduler_event(
            connection,
            cycle="2026-09-07",
            sequence=1,
            event_kind="started",
            scheduled_at="2026-09-07T12:30:00.000000+00:00",
            missed_at="2026-09-07T13:00:00.000000+00:00",
            attempt=1,
            recorded_at="2026-09-07T12:45:00.000000+00:00",
        )
    window = build_session_window(MarketSession(date(2026, 8, 28)), 60, 30)
    assert window is not None
    with pytest.raises(SchedulerPersistenceError):
        ledger.claim(
            policy,
            window,
            UtcInstant.from_datetime(datetime(2026, 8, 28, 12, 45, tzinfo=UTC)),
        )


def test_scheduler_composition_bounds_invalid_runtime_configuration(tmp_path: Path) -> None:
    values = runtime_configuration(tmp_path / "unused")
    values["state_root"] = "relative/runtime"

    configured = configure_scheduler(
        values,
        scheduler_policy=_policy(),
        repository_root=Path(__file__).resolve().parents[2],
        advance=RefusingAdvance([]),
        status=NotStartedStatus(),
        clock=FrozenClock(datetime(2026, 8, 28, 12, 45, tzinfo=UTC)),
    )

    assert isinstance(configured, SchedulerConfigurationRefusal)
    assert configured.fields == ("state_root",)


def _insert_scheduler_event(  # noqa: PLR0913 - mirror the complete durable event envelope.
    connection: sqlite3.Connection,
    *,
    cycle: str,
    sequence: int,
    event_kind: str,
    scheduled_at: str,
    missed_at: str,
    attempt: int,
    recorded_at: str,
    event_hash: str | None = None,
) -> None:
    material: dict[str, object] = {
        "cycle": cycle,
        "sequence": sequence,
        "event_kind": event_kind,
        "scheduled_at": scheduled_at,
        "missed_at": missed_at,
        "attempt": attempt,
        "lifecycle_receipt": None,
        "lifecycle_receipt_hash": None,
        "recorded_at": recorded_at,
    }
    connection.execute(
        "INSERT INTO scheduler_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*material.values(), _scheduler_hash(material) if event_hash is None else event_hash),
    )


def _scheduler_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    "policy",
    [
        {},
        {**_policy(), "asset_class": "crypto_spot"},
        {**_policy(), "first_session": "2027-01-04"},
    ],
)
def test_invalid_scheduler_configuration_changes_no_filesystem(
    tmp_path: Path,
    policy: object,
) -> None:
    root = tmp_path / "runtime"
    configured = configure_scheduler(
        runtime_configuration(root),
        scheduler_policy=policy,
        repository_root=Path(__file__).resolve().parents[2],
        advance=RefusingAdvance([]),
        status=NotStartedStatus(),
        clock=FrozenClock(datetime(2026, 8, 28, 12, 45, tzinfo=UTC)),
    )

    assert isinstance(configured, SchedulerConfigurationRefusal)
    assert not root.exists()
