from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from agentic_investment_os.adapters.decision_window import PreOpenDecisionPacketWindowSource
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.portfolio.publication import (
    DecisionPacketValidityWindow,
    DecisionPublicationRefusalReason,
)


@pytest.mark.parametrize(
    "issued_at",
    [
        datetime(2026, 8, 24, 13, 15, tzinfo=UTC),
        datetime(2026, 8, 24, 13, 29, 59, 999999, tzinfo=UTC),
    ],
)
def test_pre_open_window_expires_at_the_execution_deadline(issued_at: datetime) -> None:
    cycle = MarketSession(date(2026, 8, 24))

    result = PreOpenDecisionPacketWindowSource().window_for(
        cycle,
        UtcInstant.from_datetime(issued_at),
    )

    assert result == DecisionPacketValidityWindow(
        cycle,
        UtcInstant.from_datetime(issued_at),
        UtcInstant.from_datetime(datetime(2026, 8, 24, 14, 0, tzinfo=UTC)),
    )


@pytest.mark.parametrize(
    ("cycle", "issued_at", "expires_at"),
    [
        (
            date(2026, 3, 6),
            datetime(2026, 3, 6, 14, 15, tzinfo=UTC),
            datetime(2026, 3, 6, 15, 0, tzinfo=UTC),
        ),
        (
            date(2026, 3, 9),
            datetime(2026, 3, 9, 13, 15, tzinfo=UTC),
            datetime(2026, 3, 9, 14, 0, tzinfo=UTC),
        ),
    ],
)
def test_pre_open_window_follows_the_pinned_new_york_utc_offset(
    cycle: date,
    issued_at: datetime,
    expires_at: datetime,
) -> None:
    result = PreOpenDecisionPacketWindowSource().window_for(
        MarketSession(cycle),
        UtcInstant.from_datetime(issued_at),
    )

    assert isinstance(result, DecisionPacketValidityWindow)
    assert result.expires_at == UtcInstant.from_datetime(expires_at)


@pytest.mark.parametrize(
    ("cycle", "issued_at"),
    [
        (date(2026, 8, 24), datetime(2026, 8, 24, 13, 14, 59, 999999, tzinfo=UTC)),
        (date(2026, 8, 24), datetime(2026, 8, 24, 13, 30, tzinfo=UTC)),
        (date(2026, 8, 22), datetime(2026, 8, 22, 13, 15, tzinfo=UTC)),
        (date(2026, 7, 3), datetime(2026, 7, 3, 13, 15, tzinfo=UTC)),
        (date(2027, 8, 24), datetime(2027, 8, 24, 13, 15, tzinfo=UTC)),
    ],
)
def test_pre_open_window_refuses_off_window_or_unsupported_sessions(
    cycle: date,
    issued_at: datetime,
) -> None:
    result = PreOpenDecisionPacketWindowSource().window_for(
        MarketSession(cycle),
        UtcInstant.from_datetime(issued_at),
    )

    assert result is DecisionPublicationRefusalReason.INVALID_VALIDITY_WINDOW
