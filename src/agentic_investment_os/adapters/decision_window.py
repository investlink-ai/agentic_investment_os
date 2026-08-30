"""Resolve the code-owned pre-open DecisionPacket validity window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from agentic_investment_os.domain.scheduler import build_session_window
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.portfolio.publication import (
    DecisionPacketValidityWindow,
    DecisionPublicationRefusalReason,
)

if TYPE_CHECKING:
    from agentic_investment_os.domain.identity import MarketSession

__all__ = ("PreOpenDecisionPacketWindowSource",)

_FREEZE_MINUTES_BEFORE_OPEN = 15
_EXECUTION_DEADLINE_AFTER_OPEN = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class PreOpenDecisionPacketWindowSource:
    """Admit publication only from the approved freeze until the regular open."""

    def window_for(
        self,
        cycle: MarketSession,
        recorded_at: UtcInstant,
    ) -> DecisionPacketValidityWindow | DecisionPublicationRefusalReason:
        session = build_session_window(cycle, _FREEZE_MINUTES_BEFORE_OPEN, 0)
        if (
            session is None
            or recorded_at.value < session.missed_at.value
            or recorded_at.value >= session.opens_at.value
        ):
            return DecisionPublicationRefusalReason.INVALID_VALIDITY_WINDOW
        return DecisionPacketValidityWindow(
            cycle,
            recorded_at,
            UtcInstant(session.opens_at.value + _EXECUTION_DEADLINE_AFTER_OPEN),
        )

    def allows_publication(
        self,
        window: DecisionPacketValidityWindow,
        recorded_at: UtcInstant,
    ) -> bool:
        """Require exact official packet times and visibility before the regular open."""
        if type(window) is not DecisionPacketValidityWindow or type(recorded_at) is not UtcInstant:
            return False
        session = build_session_window(window.cycle, _FREEZE_MINUTES_BEFORE_OPEN, 0)
        return bool(
            session is not None
            and session.missed_at.value <= window.issued_at.value < session.opens_at.value
            and window.expires_at.value == session.opens_at.value + _EXECUTION_DEADLINE_AFTER_OPEN
            and window.issued_at.value <= recorded_at.value < session.opens_at.value
        )
