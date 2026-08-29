"""Resolve a short explicit DecisionPacket validity window from the injected clock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.portfolio.publication import DecisionPacketValidityWindow

if TYPE_CHECKING:
    from agentic_investment_os.domain.identity import MarketSession

__all__ = ("ShortLivedDecisionPacketWindowSource",)

_PACKET_LIFETIME = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class ShortLivedDecisionPacketWindowSource:
    """Expire publication authority five minutes after its durable issue instant."""

    def window_for(
        self,
        cycle: MarketSession,
        recorded_at: UtcInstant,
    ) -> DecisionPacketValidityWindow:
        return DecisionPacketValidityWindow(
            cycle,
            recorded_at,
            UtcInstant(recorded_at.value + _PACKET_LIFETIME),
        )
