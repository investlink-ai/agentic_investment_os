"""Define the canonical absolute-instant contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Self

__all__ = ("InvalidUtcInstantError", "UtcInstant")

_CANONICAL_UTC_ERROR = "absolute instant must use canonical UTC text"
_TIMEZONE_AWARE_ERROR = "absolute instant must be timezone-aware"
_NORMALIZED_UTC_ERROR = "absolute instant must be normalized to UTC"


class InvalidUtcInstantError(ValueError):
    """Report that a value cannot represent a canonical absolute instant."""


@dataclass(frozen=True, slots=True)
class UtcInstant:
    """Represent one absolute instant normalized to UTC at microsecond precision."""

    value: datetime

    def __post_init__(self) -> None:
        _require_normalized_utc_datetime(self.value)

    @classmethod
    def from_datetime(cls, value: datetime) -> Self:
        """Normalize a timezone-aware datetime to a UTC instant."""
        if type(value) is not datetime:
            raise InvalidUtcInstantError(_NORMALIZED_UTC_ERROR)
        if value.utcoffset() is None:
            raise InvalidUtcInstantError(_TIMEZONE_AWARE_ERROR)
        try:
            normalized = value.astimezone(UTC)
        except (OverflowError, ValueError) as error:
            raise InvalidUtcInstantError(_NORMALIZED_UTC_ERROR) from error
        return cls(normalized)

    @classmethod
    def parse(cls, value: object) -> Self:
        """Parse exact canonical durable text without normalizing stored history."""
        if not isinstance(value, str):
            raise InvalidUtcInstantError(_CANONICAL_UTC_ERROR)
        try:
            instant = cls.from_datetime(datetime.fromisoformat(value))
        except ValueError as error:
            raise InvalidUtcInstantError(_CANONICAL_UTC_ERROR) from error
        if instant.isoformat() != value:
            raise InvalidUtcInstantError(_CANONICAL_UTC_ERROR)
        return instant

    def isoformat(self) -> str:
        """Serialize fixed-width UTC text suitable for durable identity and ordering."""
        value = _require_normalized_utc_datetime(self.value)
        return datetime.isoformat(value, timespec="microseconds")


def _require_normalized_utc_datetime(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is not UTC:
        raise InvalidUtcInstantError(_NORMALIZED_UTC_ERROR)
    return value
