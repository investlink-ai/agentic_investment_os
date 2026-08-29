"""Run NYSE-relative lifecycle scheduling without acquiring lifecycle authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from agentic_investment_os.domain.lifecycle import AdvanceReceipt, LifecycleStatus
from agentic_investment_os.domain.scheduler import (
    ScheduledRunDisposition,
    ScheduledSessionStatus,
    SchedulerClaimDisposition,
    SchedulerLedger,
    SchedulerPolicy,
    SchedulerReceipt,
    SchedulerStatus,
    SessionWindow,
    calendar_supports,
    next_session_window,
    receipt_matches_session,
    session_windows_through,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant

if TYPE_CHECKING:
    from datetime import datetime

__all__ = (
    "AdvanceCapability",
    "Scheduler",
    "SchedulerCalendarError",
    "SchedulerClock",
    "StatusCapability",
)

_CLOCK_INVALID = "scheduler clock must return a timezone-aware instant representable in UTC"
_ADVANCE_RESULT_INVALID = "Advance returned an invalid scheduler observation"
_STATUS_RESULT_INVALID = "Status returned an invalid scheduler observation"
_CALENDAR_UNAVAILABLE = "scheduler calendar does not cover the current New York year"
_CLOCK_ROLLBACK = "scheduler clock precedes durable scheduler history"


class SchedulerCalendarError(RuntimeError):
    """Refuse operation beyond the exact pinned calendar horizon."""


class SchedulerClock(Protocol):
    """Supply an aware timestamp from the composition seam."""

    def now(self) -> datetime: ...


class AdvanceCapability(Protocol):
    """Advance one complete lifecycle through its existing public interface."""

    def __call__(
        self,
        *,
        cycle: object,
        mode: object,
        idempotency_key: object,
    ) -> AdvanceReceipt: ...


class StatusCapability(Protocol):
    """Rebuild lifecycle liveness through its existing public interface."""

    def __call__(self) -> LifecycleStatus: ...


@dataclass(frozen=True, slots=True)
class Scheduler:
    """Run due advances and report append-only scheduler history."""

    policy: SchedulerPolicy
    ledger: SchedulerLedger
    advance: AdvanceCapability
    lifecycle_status: StatusCapability
    clock: SchedulerClock

    def __call__(self) -> SchedulerReceipt:
        """Reconstruct and run due work under the ledger's live-process lock."""
        with self.ledger.exclusive_run():
            return self._run_due()

    def _run_due(self) -> SchedulerReceipt:
        observed_at = self._now()
        self._require_calendar(observed_at)
        snapshot = self.ledger.snapshot(self.policy)
        self._require_current_clock(observed_at, snapshot.sessions)
        completed = {item.cycle: item for item in snapshot.sessions}
        actions = 0
        due_windows = session_windows_through(self.policy, observed_at)
        due_windows = tuple(
            sorted(
                due_windows,
                key=lambda window: (
                    window.missed_at.value < observed_at.value,
                    window.cycle.trading_date,
                ),
            )
        )
        for window in due_windows:
            existing = completed.get(window.cycle)
            if existing is not None and existing.disposition not in (
                ScheduledRunDisposition.STARTED,
                ScheduledRunDisposition.RESUMED,
            ):
                continue
            if actions >= self.policy.maximum_actions_per_run:
                break
            claim = self.ledger.claim(self.policy, window, observed_at)
            actions += 1
            if claim.disposition in (
                SchedulerClaimDisposition.WAIT,
                SchedulerClaimDisposition.TERMINAL,
                SchedulerClaimDisposition.MISSED,
            ):
                completed[window.cycle] = claim.status
                continue
            lifecycle_receipt = self.advance(
                cycle=window.cycle.to_payload(),
                mode="champion",
                idempotency_key=_advance_identity(
                    self.policy, window.cycle.trading_date.isoformat()
                ),
            )
            if type(lifecycle_receipt) is not AdvanceReceipt:
                raise RuntimeError(_ADVANCE_RESULT_INVALID)
            status = self.lifecycle_status()
            if type(status) is not LifecycleStatus:
                raise RuntimeError(_STATUS_RESULT_INVALID)
            if lifecycle_receipt.disposition.value != "failed_closed" and (
                status.pinned_run_identity is None
                or status.pinned_run_identity.cycle != window.cycle
            ):
                raise RuntimeError(_STATUS_RESULT_INVALID)
            if not receipt_matches_session(lifecycle_receipt, window.cycle):
                raise RuntimeError(_ADVANCE_RESULT_INVALID)
            completed[window.cycle] = self.ledger.record_outcome(
                self.policy,
                claim,
                lifecycle_receipt,
                self._now(),
            )
        snapshot = self.ledger.snapshot(self.policy)
        recorded_at = self._now()
        self._require_current_clock(recorded_at, snapshot.sessions)
        next_window = _next_pending_window(self.policy, recorded_at, snapshot.sessions)
        return SchedulerReceipt(
            self.policy.policy_id,
            recorded_at,
            snapshot.sessions,
            _pending(next_window, recorded_at),
        )

    def status(self) -> SchedulerStatus:
        """Rebuild scheduler status without invoking lifecycle behavior."""
        recorded_at = self._now()
        self._require_calendar(recorded_at)
        snapshot = self.ledger.snapshot(self.policy)
        self._require_current_clock(recorded_at, snapshot.sessions)
        next_window = _next_pending_window(self.policy, recorded_at, snapshot.sessions)
        return SchedulerStatus(
            snapshot.policy_id,
            snapshot.sessions,
            _pending(next_window, recorded_at),
        )

    def _now(self) -> UtcInstant:
        try:
            return UtcInstant.from_datetime(self.clock.now())
        except (AttributeError, InvalidUtcInstantError, TypeError) as error:
            raise RuntimeError(_CLOCK_INVALID) from error

    @staticmethod
    def _require_calendar(recorded_at: UtcInstant) -> None:
        if not calendar_supports(recorded_at):
            raise SchedulerCalendarError(_CALENDAR_UNAVAILABLE)

    @staticmethod
    def _require_current_clock(
        recorded_at: UtcInstant,
        sessions: tuple[ScheduledSessionStatus, ...],
    ) -> None:
        if any(session.recorded_at.value > recorded_at.value for session in sessions):
            raise RuntimeError(_CLOCK_ROLLBACK)


def _advance_identity(policy: SchedulerPolicy, session: str) -> str:
    return f"schedule:{policy.policy_id}:{session}:advance"


def _pending(
    window: SessionWindow | None,
    recorded_at: UtcInstant,
) -> ScheduledSessionStatus | None:
    if window is None:
        return None
    return ScheduledSessionStatus(
        window.cycle,
        window.scheduled_at,
        window.missed_at,
        ScheduledRunDisposition.PENDING,
        0,
        recorded_at,
    )


def _next_pending_window(
    policy: SchedulerPolicy,
    recorded_at: UtcInstant,
    sessions: tuple[ScheduledSessionStatus, ...],
) -> SessionWindow | None:
    observed = {session.cycle for session in sessions}
    for window in session_windows_through(policy, recorded_at):
        if window.cycle not in observed:
            return window
    return next_session_window(policy, recorded_at)
