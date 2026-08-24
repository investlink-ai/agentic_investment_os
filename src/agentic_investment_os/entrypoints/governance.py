"""Compose Constitution governance without model or broker authority."""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from agentic_investment_os.adapters.sqlite_lifecycle import (
    PreparedRuntimeDatabase,
    RuntimeRootRefusal,
    SQLiteConstitutionGovernance,
    prepare_runtime_database,
)
from agentic_investment_os.application.governance import Govern
from agentic_investment_os.entrypoints.configuration import (
    ConfigurationRefusal,
    ConfigurationRefusalCode,
    ConfigurationSource,
    RuntimeConfiguration,
    resolve_runtime_configuration,
)
from agentic_investment_os.entrypoints.lifecycle import SystemClock

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from agentic_investment_os.application.governance import GovernanceClock
    from agentic_investment_os.domain.governance import (
        MarketSessionEligibility,
        OperatorApprovalVerifier,
    )


def configure_govern(
    sources: Sequence[ConfigurationSource],
    *,
    repository_root: Path,
    approval_verifier: OperatorApprovalVerifier,
    session_eligibility: MarketSessionEligibility,
    clock: GovernanceClock | None = None,
) -> Govern | ConfigurationRefusal:
    """Validate configuration and compose operator-only Constitution governance."""
    resolution = resolve_runtime_configuration(sources, repository_root=repository_root)
    if isinstance(resolution, ConfigurationRefusal):
        return resolution
    if not isinstance(resolution, RuntimeConfiguration):
        assert_never(resolution)  # pragma: no cover - closed resolution union is exhausted.
    database = prepare_runtime_database(resolution.state_root)
    if isinstance(database, RuntimeRootRefusal):
        return ConfigurationRefusal(
            code=ConfigurationRefusalCode.INVALID_STATE_ROOT,
            fields=("state_root",),
        )
    if isinstance(database, PreparedRuntimeDatabase):
        return Govern(
            SQLiteConstitutionGovernance(database.path),
            approval_verifier,
            session_eligibility,
            clock if clock is not None else SystemClock(),
        )
    assert_never(database)  # pragma: no cover - closed database union is exhausted.
