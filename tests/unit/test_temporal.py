from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import override

import pytest

from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant


class _NoncanonicalDatetime(datetime):
    @override
    def isoformat(self, *_args: object, **_kwargs: object) -> str:
        return "2026-08-21T18:00:00.000000-04:00"


def test_utc_instant_normalizes_equivalent_aware_datetimes() -> None:
    eastern = datetime(
        2026,
        8,
        21,
        18,
        0,
        0,
        123456,
        tzinfo=timezone(-timedelta(hours=4)),
    )
    utc = datetime(2026, 8, 21, 22, 0, 0, 123456, tzinfo=UTC)

    assert UtcInstant.from_datetime(eastern) == UtcInstant.from_datetime(utc)
    assert UtcInstant.from_datetime(eastern).isoformat() == ("2026-08-21T22:00:00.123456+00:00")


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-21T22:00:00+00:00",
        "2026-08-21T22:00:00.000000Z",
        "2026-08-21T18:00:00.000000-04:00",
        "2026-08-21T22:00:00.0000000+00:00",
        "2026-08-21T22:00:00",
        "0001-01-01T00:00:00.000000+14:00",
        "not-a-timestamp",
        3,
    ],
)
def test_utc_instant_rejects_noncanonical_durable_text(value: object) -> None:
    with pytest.raises(InvalidUtcInstantError, match="canonical UTC"):
        UtcInstant.parse(value)


def test_utc_instant_round_trips_canonical_text() -> None:
    text = "2026-08-21T22:00:00.000000+00:00"

    assert UtcInstant.parse(text).isoformat() == text


def test_utc_instant_rejects_naive_datetime() -> None:
    with pytest.raises(InvalidUtcInstantError, match="timezone-aware"):
        UtcInstant.from_datetime(datetime(2026, 8, 21, 22, 0))  # noqa: DTZ001


@pytest.mark.parametrize(
    "value",
    [
        datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=14))),
        datetime.max.replace(tzinfo=timezone(-timedelta(hours=14))),
    ],
    ids=("below-minimum", "above-maximum"),
)
def test_utc_instant_rejects_an_aware_datetime_outside_the_utc_range(
    value: datetime,
) -> None:
    with pytest.raises(InvalidUtcInstantError, match="normalized to UTC"):
        UtcInstant.from_datetime(value)


def test_utc_instant_cannot_be_constructed_with_a_non_utc_datetime() -> None:
    eastern = datetime(
        2026,
        8,
        21,
        18,
        0,
        tzinfo=timezone(-timedelta(hours=4)),
    )

    with pytest.raises(InvalidUtcInstantError, match="normalized to UTC"):
        UtcInstant(eastern)


def test_utc_instant_rejects_a_datetime_subclass_with_hostile_serialization() -> None:
    value = _NoncanonicalDatetime(2026, 8, 21, 22, 0, tzinfo=UTC)

    with pytest.raises(InvalidUtcInstantError, match="normalized to UTC"):
        UtcInstant(value)
    with pytest.raises(InvalidUtcInstantError, match="normalized to UTC"):
        UtcInstant.from_datetime(value)
