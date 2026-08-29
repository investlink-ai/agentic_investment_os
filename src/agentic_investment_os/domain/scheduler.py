"""Define deterministic NYSE scheduling values and append-only state ports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo

from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.temporal import UtcInstant

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from agentic_investment_os.domain.lifecycle import AdvanceReceipt

__all__ = (
    "PINNED_XNYS_CALENDAR_ID",
    "ScheduledRunDisposition",
    "ScheduledSessionStatus",
    "SchedulerClaim",
    "SchedulerClaimDisposition",
    "SchedulerLedger",
    "SchedulerPolicy",
    "SchedulerReceipt",
    "SchedulerSnapshot",
    "SchedulerStatus",
    "SessionWindow",
    "build_session_window",
    "calendar_supports",
    "next_session_window",
    "session_windows_through",
)

PINNED_XNYS_CALENDAR_ID = "xnys-regular-2026a"
_PINNED_YEAR = 2026
_NEW_YORK = ZoneInfo("America/New_York")
_OPEN_TIME = time(9, 30)
_REGULAR_CLOSE_TIME = time(16, 0)
_EARLY_CLOSE_TIME = time(13, 0)
_HOLIDAYS = frozenset(
    {
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    }
)
_EARLY_CLOSES = frozenset({date(2026, 11, 27), date(2026, 12, 24)})
_ISO_DATE_LENGTH = 10
_WEEKDAY_COUNT = 5
_HASH_LENGTH = 64
_INVALID_POLICY = "invalid scheduler policy"
_INVALID_STATUS = "invalid scheduled session status"
_POLICY_FIELDS = frozenset(
    {
        "schema_version",
        "policy_type",
        "asset_class",
        "calendar_id",
        "first_session",
        "advance_minutes_before_open",
        "maximum_lateness_minutes",
        "recovery_delay_seconds",
        "maximum_actions_per_run",
    }
)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _is_exact_integer(value: object, *, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _parse_session_text(value: object) -> MarketSession | None:
    if type(value) is not str or len(value) != _ISO_DATE_LENGTH:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if parsed.isoformat() != value:
        return None
    return MarketSession(parsed)


def _is_regular_session(value: date) -> bool:
    return (
        value.year == _PINNED_YEAR and value.weekday() < _WEEKDAY_COUNT and value not in _HOLIDAYS
    )


def calendar_supports(recorded_at: UtcInstant) -> bool:
    """Report whether the pinned calendar covers the instant's New York year."""
    return recorded_at.value.astimezone(_NEW_YORK).year == _PINNED_YEAR


@dataclass(frozen=True, slots=True)
class SchedulerPolicy:
    """Pin one bounded Market Session advance schedule."""

    first_session: MarketSession
    advance_minutes_before_open: int
    maximum_lateness_minutes: int
    recovery_delay_seconds: int
    maximum_actions_per_run: int

    def __post_init__(self) -> None:
        if (
            type(self.first_session) is not MarketSession
            or not _is_regular_session(self.first_session.trading_date)
            or not _is_exact_integer(self.advance_minutes_before_open, minimum=1, maximum=1_440)
            or not _is_exact_integer(self.maximum_lateness_minutes, minimum=0, maximum=1_440)
            or not _is_exact_integer(self.recovery_delay_seconds, minimum=1, maximum=86_400)
            or not _is_exact_integer(self.maximum_actions_per_run, minimum=1, maximum=252)
        ):
            raise ValueError(_INVALID_POLICY)

    @classmethod
    def parse(cls, value: object) -> SchedulerPolicy | None:
        if type(value) is not dict or set(value) != _POLICY_FIELDS:
            return None
        if any(type(key) is not str for key in value):
            return None
        first_session = _parse_session_text(value["first_session"])
        if (
            value["schema_version"] != 1
            or type(value["schema_version"]) is not int
            or value["policy_type"] != "market_session_advance"
            or type(value["policy_type"]) is not str
            or value["asset_class"] != "us_equity"
            or type(value["asset_class"]) is not str
            or value["calendar_id"] != PINNED_XNYS_CALENDAR_ID
            or type(value["calendar_id"]) is not str
            or first_session is None
            or not _is_regular_session(first_session.trading_date)
            or not _is_exact_integer(value["advance_minutes_before_open"], minimum=1, maximum=1_440)
            or not _is_exact_integer(value["maximum_lateness_minutes"], minimum=0, maximum=1_440)
            or not _is_exact_integer(value["recovery_delay_seconds"], minimum=1, maximum=86_400)
            or not _is_exact_integer(value["maximum_actions_per_run"], minimum=1, maximum=252)
        ):
            return None
        return cls(
            first_session,
            value["advance_minutes_before_open"],
            value["maximum_lateness_minutes"],
            value["recovery_delay_seconds"],
            value["maximum_actions_per_run"],
        )

    @property
    def policy_id(self) -> str:
        return _hash(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "policy_type": "market_session_advance",
            "asset_class": "us_equity",
            "calendar_id": PINNED_XNYS_CALENDAR_ID,
            "first_session": self.first_session.trading_date.isoformat(),
            "advance_minutes_before_open": self.advance_minutes_before_open,
            "maximum_lateness_minutes": self.maximum_lateness_minutes,
            "recovery_delay_seconds": self.recovery_delay_seconds,
            "maximum_actions_per_run": self.maximum_actions_per_run,
        }


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """Resolve one NYSE trading date into canonical operation instants."""

    cycle: MarketSession
    opens_at: UtcInstant
    closes_at: UtcInstant
    scheduled_at: UtcInstant
    missed_at: UtcInstant

    def __post_init__(self) -> None:
        if (
            type(self.cycle) is not MarketSession
            or type(self.opens_at) is not UtcInstant
            or type(self.closes_at) is not UtcInstant
            or type(self.scheduled_at) is not UtcInstant
            or type(self.missed_at) is not UtcInstant
            or self.scheduled_at.value >= self.opens_at.value
            or self.opens_at.value >= self.closes_at.value
            or self.missed_at.value < self.scheduled_at.value
        ):
            raise ValueError(_INVALID_STATUS)


def build_session_window(
    cycle: MarketSession,
    advance_minutes_before_open: int,
    maximum_lateness_minutes: int,
) -> SessionWindow | None:
    """Resolve one supported regular session without consulting host-local time."""
    if (
        type(cycle) is not MarketSession
        or not _is_regular_session(cycle.trading_date)
        or not _is_exact_integer(advance_minutes_before_open, minimum=1, maximum=1_440)
        or not _is_exact_integer(maximum_lateness_minutes, minimum=0, maximum=1_440)
    ):
        return None
    opening = datetime.combine(cycle.trading_date, _OPEN_TIME, _NEW_YORK)
    closing = datetime.combine(
        cycle.trading_date,
        _EARLY_CLOSE_TIME if cycle.trading_date in _EARLY_CLOSES else _REGULAR_CLOSE_TIME,
        _NEW_YORK,
    )
    scheduled = opening - timedelta(minutes=advance_minutes_before_open)
    missed = scheduled + timedelta(minutes=maximum_lateness_minutes)
    return SessionWindow(
        cycle,
        UtcInstant.from_datetime(opening),
        UtcInstant.from_datetime(closing),
        UtcInstant.from_datetime(scheduled),
        UtcInstant.from_datetime(missed),
    )


def session_windows_through(
    policy: SchedulerPolicy,
    recorded_at: UtcInstant,
) -> tuple[SessionWindow, ...]:
    """Return supported due windows from activation through the supplied instant."""
    local_date = recorded_at.value.astimezone(_NEW_YORK).date()
    if local_date.year != _PINNED_YEAR:
        return ()
    current = policy.first_session.trading_date
    windows: list[SessionWindow] = []
    while current <= local_date:
        window = build_session_window(
            MarketSession(current),
            policy.advance_minutes_before_open,
            policy.maximum_lateness_minutes,
        )
        if window is not None and window.scheduled_at.value <= recorded_at.value:
            windows.append(window)
        current += timedelta(days=1)
    return tuple(windows)


def next_session_window(
    policy: SchedulerPolicy,
    recorded_at: UtcInstant,
) -> SessionWindow | None:
    """Return the first supported operation window strictly after the supplied instant."""
    current = max(
        policy.first_session.trading_date,
        recorded_at.value.astimezone(_NEW_YORK).date(),
    )
    limit = date(_PINNED_YEAR, 12, 31)
    while current <= limit:
        window = build_session_window(
            MarketSession(current),
            policy.advance_minutes_before_open,
            policy.maximum_lateness_minutes,
        )
        if window is not None and window.scheduled_at.value > recorded_at.value:
            return window
        current += timedelta(days=1)
    return None


class ScheduledRunDisposition(StrEnum):
    """Report one durable scheduler outcome without implying lifecycle completion."""

    PENDING = "pending"
    STARTED = "started"
    RESUMED = "resumed"
    COMPLETED = "completed"
    MISSED = "missed"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class ScheduledSessionStatus:
    """Expose one bounded session scheduling result."""

    cycle: MarketSession
    scheduled_at: UtcInstant
    missed_at: UtcInstant
    disposition: ScheduledRunDisposition
    attempts: int
    recorded_at: UtcInstant
    lifecycle_receipt_hash: str | None = None

    def __post_init__(self) -> None:
        no_receipt = self.lifecycle_receipt_hash is None
        terminal_with_receipt = self.disposition in (
            ScheduledRunDisposition.COMPLETED,
            ScheduledRunDisposition.REFUSED,
        )
        if (
            type(self.cycle) is not MarketSession
            or type(self.scheduled_at) is not UtcInstant
            or type(self.missed_at) is not UtcInstant
            or type(self.disposition) is not ScheduledRunDisposition
            or type(self.attempts) is not int
            or type(self.recorded_at) is not UtcInstant
            or self.missed_at.value < self.scheduled_at.value
            or (
                self.disposition
                in (ScheduledRunDisposition.PENDING, ScheduledRunDisposition.MISSED)
                and self.attempts != 0
            )
            or (
                self.disposition
                in (
                    ScheduledRunDisposition.STARTED,
                    ScheduledRunDisposition.RESUMED,
                    ScheduledRunDisposition.COMPLETED,
                    ScheduledRunDisposition.REFUSED,
                )
                and self.attempts < 1
            )
            or terminal_with_receipt == no_receipt
            or (
                self.lifecycle_receipt_hash is not None
                and (
                    len(self.lifecycle_receipt_hash) != _HASH_LENGTH
                    or any(
                        character not in "0123456789abcdef"
                        for character in self.lifecycle_receipt_hash
                    )
                )
            )
        ):
            raise ValueError(_INVALID_STATUS)


class SchedulerClaimDisposition(StrEnum):
    """Tell orchestration whether it owns the next lifecycle invocation."""

    START = "start"
    RESUME = "resume"
    WAIT = "wait"
    TERMINAL = "terminal"
    MISSED = "missed"


@dataclass(frozen=True, slots=True)
class SchedulerClaim:
    window: SessionWindow
    disposition: SchedulerClaimDisposition
    attempt: int
    status: ScheduledSessionStatus


@dataclass(frozen=True, slots=True)
class SchedulerSnapshot:
    policy_id: str
    sessions: tuple[ScheduledSessionStatus, ...]


@dataclass(frozen=True, slots=True)
class SchedulerReceipt:
    """Report durable scheduler outcomes observed by one invocation."""

    policy_id: str
    recorded_at: UtcInstant
    sessions: tuple[ScheduledSessionStatus, ...]
    pending: ScheduledSessionStatus | None

    @property
    def next_scheduled_at(self) -> UtcInstant | None:
        """Return the next pending instant for concise operator rendering."""
        return None if self.pending is None else self.pending.scheduled_at


@dataclass(frozen=True, slots=True)
class SchedulerStatus:
    """Report bounded scheduler history independently of lifecycle liveness."""

    policy_id: str
    sessions: tuple[ScheduledSessionStatus, ...]
    pending: ScheduledSessionStatus | None

    @property
    def next_scheduled_at(self) -> UtcInstant | None:
        """Return the next pending instant for concise operator rendering."""
        return None if self.pending is None else self.pending.scheduled_at


class SchedulerLedger(Protocol):
    """Persist scheduler intent before lifecycle invocation and its observation afterward."""

    def exclusive_run(self) -> AbstractContextManager[None]:
        """Prevent overlapping live scheduler invocations for this ledger."""
        ...

    def claim(
        self,
        policy: SchedulerPolicy,
        window: SessionWindow,
        recorded_at: UtcInstant,
    ) -> SchedulerClaim: ...

    def record_outcome(
        self,
        policy: SchedulerPolicy,
        claim: SchedulerClaim,
        receipt: AdvanceReceipt,
        recorded_at: UtcInstant,
    ) -> ScheduledSessionStatus: ...

    def snapshot(self, policy: SchedulerPolicy) -> SchedulerSnapshot: ...
