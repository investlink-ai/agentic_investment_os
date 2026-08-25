"""Compose an isolated Research Lab Replay without production authority."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from agentic_investment_os.adapters.sqlite_lab import (
    LabRootRefusal,
    SQLiteLabCallLedger,
    prepare_lab_database,
)
from agentic_investment_os.application.replay import Replay, ReplayClock
from agentic_investment_os.entrypoints.configuration import (
    ConfigurationRefusal,
    _validate_state_root,
)
from agentic_investment_os.entrypoints.lifecycle import SystemClock

if TYPE_CHECKING:
    from agentic_investment_os.research.model import EvidenceCollectorModel

__all__ = ("LabCompositionRefusal", "LabCompositionRefusalCode", "configure_replay")

_NAMESPACE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")


class LabCompositionRefusalCode(StrEnum):
    """Classify a Research Lab composition refusal before runtime effects."""

    INVALID_NAMESPACE = "invalid_namespace"
    INVALID_STATE_ROOT = "invalid_state_root"
    PRODUCTION_ROOTS_REQUIRED = "production_roots_required"


@dataclass(frozen=True, slots=True)
class LabCompositionRefusal:
    """Return a content-free Research Lab composition refusal."""

    code: LabCompositionRefusalCode


def configure_replay(  # noqa: PLR0913 - composition names every authority input.
    *,
    namespace: object,
    lab_state_root: object,
    production_state_roots: tuple[Path, ...],
    repository_root: Path,
    model: EvidenceCollectorModel,
    clock: ReplayClock | None = None,
) -> Replay | LabCompositionRefusal:
    """Validate isolation and construct one Lab-local Replay capability."""
    if type(namespace) is not str or _NAMESPACE.fullmatch(namespace) is None:
        return LabCompositionRefusal(LabCompositionRefusalCode.INVALID_NAMESPACE)
    if type(lab_state_root) is not str:
        return LabCompositionRefusal(LabCompositionRefusalCode.INVALID_STATE_ROOT)
    validated_root = _validate_state_root(lab_state_root, repository_root=repository_root)
    if isinstance(validated_root, ConfigurationRefusal):
        return LabCompositionRefusal(LabCompositionRefusalCode.INVALID_STATE_ROOT)
    if not production_state_roots or any(
        not isinstance(root, Path) for root in production_state_roots
    ):
        return LabCompositionRefusal(LabCompositionRefusalCode.PRODUCTION_ROOTS_REQUIRED)
    database = prepare_lab_database(
        validated_root,
        production_state_roots=production_state_roots,
    )
    if isinstance(database, LabRootRefusal):
        return LabCompositionRefusal(LabCompositionRefusalCode.INVALID_STATE_ROOT)
    return Replay(
        namespace,
        SQLiteLabCallLedger(database.path, namespace),
        model,
        clock if clock is not None else SystemClock(),
    )
