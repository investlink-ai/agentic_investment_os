from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.lifecycle import AdvanceFailureReason, AdvanceReceipt
from agentic_investment_os.domain.scheduler import (
    PINNED_XNYS_CALENDAR_ID,
    SchedulerPolicy,
    build_session_window,
    receipt_matches_session,
)
from agentic_investment_os.domain.temporal import UtcInstant


def _policy(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "policy_type": "market_session_advance",
        "asset_class": "us_equity",
        "calendar_id": PINNED_XNYS_CALENDAR_ID,
        "first_session": "2026-03-06",
        "advance_minutes_before_open": 60,
        "maximum_lateness_minutes": 30,
        "recovery_delay_seconds": 300,
        "maximum_actions_per_run": 20,
    }
    values.update(overrides)
    return values


def test_scheduler_policy_is_complete_versioned_and_hashed() -> None:
    first = SchedulerPolicy.parse(_policy())
    second = SchedulerPolicy.parse(dict(reversed(tuple(_policy().items()))))

    assert isinstance(first, SchedulerPolicy)
    assert second == first
    assert first.policy_id == second.policy_id
    assert first.to_payload() == _policy()

    with pytest.raises(ValueError, match="scheduler policy"):
        SchedulerPolicy(MarketSession(date(2027, 1, 4)), 60, 30, 300, 20)


def test_scheduler_policy_rejects_unknown_missing_or_unsupported_material() -> None:
    valid = _policy()
    missing = dict(valid)
    missing.pop("calendar_id")

    invalid = (
        {**valid, "unknown": True},
        missing,
        {**valid, "schema_version": True},
        {**valid, "policy_type": "private_stage"},
        {**valid, "asset_class": "crypto_spot"},
        {**valid, "calendar_id": "ambient-calendar"},
        {**valid, "first_session": "2027-01-04"},
        {**valid, "first_session": "2026-09-07"},
        {**valid, "advance_minutes_before_open": 0},
        {**valid, "maximum_lateness_minutes": -1},
        {**valid, "recovery_delay_seconds": 0},
        {**valid, "maximum_actions_per_run": 0},
    )

    assert all(SchedulerPolicy.parse(value) is None for value in invalid)


def test_pinned_calendar_resolves_dst_holidays_and_early_closes() -> None:
    before_dst = build_session_window(MarketSession(date(2026, 3, 6)), 60, 30)
    after_dst = build_session_window(MarketSession(date(2026, 3, 9)), 60, 30)
    early_close = build_session_window(MarketSession(date(2026, 11, 27)), 60, 30)

    assert before_dst is not None
    assert after_dst is not None
    assert early_close is not None
    assert before_dst.opens_at == UtcInstant.from_datetime(datetime(2026, 3, 6, 14, 30, tzinfo=UTC))
    assert after_dst.opens_at == UtcInstant.from_datetime(datetime(2026, 3, 9, 13, 30, tzinfo=UTC))
    assert early_close.closes_at == UtcInstant.from_datetime(
        datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    )
    assert build_session_window(MarketSession(date(2026, 9, 7)), 60, 30) is None
    assert build_session_window(MarketSession(date(2027, 1, 4)), 60, 30) is None


def test_scheduler_receipt_binding_rejects_inexact_boundary_types() -> None:
    class InexactReceipt(AdvanceReceipt):
        pass

    cycle = MarketSession(date(2026, 8, 28))
    receipt = AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)

    assert receipt_matches_session(receipt, cycle)
    assert not receipt_matches_session(object.__new__(InexactReceipt), cycle)
