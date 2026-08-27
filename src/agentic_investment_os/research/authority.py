"""Define the closed authority scopes accepted by research artifact validators."""

from __future__ import annotations

from enum import StrEnum

__all__ = ("ResearchAuthority", "research_authority_is_valid")


class ResearchAuthority(StrEnum):
    """Separate production research from isolated non-production replay."""

    PRODUCTION = "production_research"
    LAB = "research_lab_non_production"

    @property
    def non_production(self) -> bool:
        return self is ResearchAuthority.LAB


def research_authority_is_valid(scope: object, non_production: object) -> bool:
    """Return whether an exact scope and isolation flag form an allowed pair."""
    return any(
        scope == authority.value and non_production is authority.non_production
        for authority in ResearchAuthority
    )
