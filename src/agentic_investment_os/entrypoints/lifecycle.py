"""Compose uncredentialed Stage 1 lifecycle capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agentic_investment_os.adapters.sqlite_lifecycle import (
    RuntimeRootRefusal,
    SQLiteLifecycleLedger,
    open_runtime_database,
    prepare_runtime_database,
)
from agentic_investment_os.application.lifecycle import Advance, Status
from agentic_investment_os.entrypoints.configuration import (
    ConfigurationRefusal,
    ConfigurationRefusalCode,
    ConfigurationSource,
    resolve_runtime_configuration,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from agentic_investment_os.application.lifecycle import Clock


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Provide UTC wall-clock time only from the composition boundary."""

    def now(self) -> datetime:
        return datetime.now(UTC)


def configure_advance(
    sources: Sequence[ConfigurationSource],
    *,
    repository_root: Path,
    clock: Clock | None = None,
) -> Advance | ConfigurationRefusal:
    """Validate configuration and compose Advance without credentials or network access."""
    resolution = resolve_runtime_configuration(sources, repository_root=repository_root)
    if isinstance(resolution, ConfigurationRefusal):
        return resolution
    database = prepare_runtime_database(resolution.state_root)
    if isinstance(database, RuntimeRootRefusal):
        return ConfigurationRefusal(
            code=ConfigurationRefusalCode.INVALID_STATE_ROOT,
            fields=("state_root",),
        )
    return Advance(
        ledger=SQLiteLifecycleLedger(database.path),
        configuration_version=resolution.schema_version,
        configuration_hash=resolution.fingerprint,
        clock=clock if clock is not None else SystemClock(),
    )


def configure_status(
    sources: Sequence[ConfigurationSource],
    *,
    repository_root: Path,
) -> Status | ConfigurationRefusal:
    """Validate configuration and compose rebuildable lifecycle status."""
    resolution = resolve_runtime_configuration(sources, repository_root=repository_root)
    if isinstance(resolution, ConfigurationRefusal):
        return resolution
    database = open_runtime_database(resolution.state_root)
    if isinstance(database, RuntimeRootRefusal):
        return ConfigurationRefusal(
            code=ConfigurationRefusalCode.INVALID_STATE_ROOT,
            fields=("state_root",),
        )
    return Status(SQLiteLifecycleLedger(database.path, initialize_schema=database.created))
