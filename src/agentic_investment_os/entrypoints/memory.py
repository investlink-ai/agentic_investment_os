"""Compose the uncredentialed Record capability against private runtime state."""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from agentic_investment_os.adapters.filesystem_evidence import FilesystemEvidenceVault
from agentic_investment_os.adapters.sqlite_lifecycle import (
    PreparedRuntimeDatabase,
    RuntimeRootRefusal,
    prepare_runtime_database,
)
from agentic_investment_os.adapters.sqlite_memory import SQLiteBeliefLedger
from agentic_investment_os.application.memory import Record
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

    from agentic_investment_os.application.lifecycle import Clock


def configure_record(
    sources: Sequence[ConfigurationSource],
    *,
    repository_root: Path,
    clock: Clock | None = None,
) -> Record | ConfigurationRefusal:
    """Validate configuration and compose Record without model or execution authority."""
    resolution = resolve_runtime_configuration(sources, repository_root=repository_root)
    if isinstance(resolution, ConfigurationRefusal):
        return resolution
    if not isinstance(resolution, RuntimeConfiguration):
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(resolution)  # pragma: no cover
    database = prepare_runtime_database(resolution.state_root)
    if isinstance(database, RuntimeRootRefusal):
        return ConfigurationRefusal(
            code=ConfigurationRefusalCode.INVALID_STATE_ROOT,
            fields=("state_root",),
        )
    if isinstance(database, PreparedRuntimeDatabase):
        return Record(
            ledger=SQLiteBeliefLedger(database.path),
            evidence_resolver=FilesystemEvidenceVault.reference_validator(
                resolution.state_root / "evidence-vault"
            ),
            clock=clock if clock is not None else SystemClock(),
        )
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(database)  # pragma: no cover
