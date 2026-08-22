"""Compose uncredentialed Stage 1 lifecycle capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, assert_never

from agentic_investment_os.adapters.sqlite_lifecycle import (
    PreparedRuntimeDatabase,
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
    RuntimeConfiguration,
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
    """Validate configuration and compose Advance without credentials or network access.

    Opening storage initializes or validates the current schema before returning and raises
    ``LifecyclePersistenceError`` when durable state fails startup validation.
    """
    resolution = resolve_runtime_configuration(sources, repository_root=repository_root)
    if isinstance(resolution, ConfigurationRefusal):
        return resolution
    if not isinstance(resolution, RuntimeConfiguration):
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(resolution)  # pragma: no cover  # pragma: no mutate
    database = prepare_runtime_database(resolution.state_root)
    if isinstance(database, RuntimeRootRefusal):
        return ConfigurationRefusal(
            code=ConfigurationRefusalCode.INVALID_STATE_ROOT,
            fields=("state_root",),
        )
    if isinstance(database, PreparedRuntimeDatabase):
        return Advance(
            ledger=SQLiteLifecycleLedger(database.path),
            configuration_version=resolution.schema_version,
            configuration_hash=resolution.fingerprint,
            clock=clock if clock is not None else SystemClock(),
        )
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(database)  # pragma: no cover  # pragma: no mutate


def configure_status(
    sources: Sequence[ConfigurationSource],
    *,
    repository_root: Path,
) -> Status | ConfigurationRefusal:
    """Validate configuration and compose rebuildable lifecycle status.

    Existing storage is validated against the current schema before returning and raises
    ``LifecyclePersistenceError`` when durable state fails startup validation; missing authoritative
    storage is never recreated.
    """
    resolution = resolve_runtime_configuration(sources, repository_root=repository_root)
    if isinstance(resolution, ConfigurationRefusal):
        return resolution
    if not isinstance(resolution, RuntimeConfiguration):
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(resolution)  # pragma: no cover  # pragma: no mutate
    database = open_runtime_database(resolution.state_root)
    if isinstance(database, RuntimeRootRefusal):
        return ConfigurationRefusal(
            code=ConfigurationRefusalCode.INVALID_STATE_ROOT,
            fields=("state_root",),
        )
    if isinstance(database, PreparedRuntimeDatabase):
        return Status(SQLiteLifecycleLedger.open_existing(database.path))
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(database)  # pragma: no cover  # pragma: no mutate
