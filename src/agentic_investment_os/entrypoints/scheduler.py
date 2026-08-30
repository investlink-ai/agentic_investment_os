"""Compose the uncredentialed Market Session scheduler capability."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, assert_never

from agentic_investment_os.adapters.sqlite_scheduler import (
    SchedulerPersistenceError,
    SchedulerPolicyConflictError,
    SQLiteSchedulerLedger,
)
from agentic_investment_os.application.scheduler import (
    AdvanceCapability,
    Scheduler,
    SchedulerClock,
    StatusCapability,
)
from agentic_investment_os.domain.scheduler import SchedulerPolicy
from agentic_investment_os.entrypoints.configuration import (
    ConfigurationRefusal,
    ConfigurationSource,
    RuntimeConfiguration,
    resolve_runtime_configuration,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = (
    "SchedulerConfigurationRefusal",
    "SchedulerConfigurationRefusalCode",
    "configure_scheduler",
)


class SchedulerConfigurationRefusalCode(StrEnum):
    """Classify bounded scheduler composition failures."""

    INVALID_RUNTIME_CONFIGURATION = "invalid_runtime_configuration"
    INVALID_SCHEDULER_POLICY = "invalid_scheduler_policy"
    CONFLICTING_SCHEDULER_POLICY = "conflicting_scheduler_policy"
    INVALID_SCHEDULER_STORAGE = "invalid_scheduler_storage"


@dataclass(frozen=True, slots=True)
class SchedulerConfigurationRefusal:
    """Return field-level scheduler diagnostics without retaining hostile values."""

    code: SchedulerConfigurationRefusalCode
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SystemSchedulerClock:
    """Provide UTC wall-clock time only from the composition boundary."""

    def now(self) -> datetime:
        return datetime.now(UTC)


def configure_scheduler(  # noqa: PLR0913 - composition names every capability boundary.
    runtime_values: Mapping[str, object],
    *,
    scheduler_policy: object,
    repository_root: Path,
    advance: AdvanceCapability,
    status: StatusCapability,
    clock: SchedulerClock | None = None,
) -> Scheduler | SchedulerConfigurationRefusal:
    """Validate all scheduler inputs before opening its private durable ledger."""
    policy = SchedulerPolicy.parse(scheduler_policy)
    if policy is None:
        return SchedulerConfigurationRefusal(
            SchedulerConfigurationRefusalCode.INVALID_SCHEDULER_POLICY,
            ("scheduler_policy",),
        )
    resolution = resolve_runtime_configuration(
        (ConfigurationSource("scheduler-runtime", runtime_values),),
        repository_root=repository_root,
    )
    if isinstance(resolution, ConfigurationRefusal):
        return SchedulerConfigurationRefusal(
            SchedulerConfigurationRefusalCode.INVALID_RUNTIME_CONFIGURATION,
            resolution.fields,
        )
    if not isinstance(resolution, RuntimeConfiguration):
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(resolution)  # pragma: no cover
    try:
        ledger = SQLiteSchedulerLedger(resolution.state_root, policy)
    except SchedulerPolicyConflictError:
        return SchedulerConfigurationRefusal(
            SchedulerConfigurationRefusalCode.CONFLICTING_SCHEDULER_POLICY,
            ("scheduler_policy",),
        )
    except SchedulerPersistenceError:
        return SchedulerConfigurationRefusal(
            SchedulerConfigurationRefusalCode.INVALID_SCHEDULER_STORAGE,
            ("state_root",),
        )
    return Scheduler(
        policy,
        ledger,
        advance,
        status,
        SystemSchedulerClock() if clock is None else clock,
    )
