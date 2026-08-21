"""Resolve the complete Stage 1 runtime configuration without ambient defaults."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_FIELDS = frozenset({"schema_version", "state_root"})
_IGNORED_RUNTIME_ROOTS = frozenset({"artifacts", "data", "var"})
_SCHEMA_VERSION_FIELDS = ("schema_version",)
_STATE_ROOT_FIELDS = ("state_root",)
_LIFECYCLE_DATABASE_NAME = "lifecycle.sqlite3"


class ConfigurationRefusalCode(StrEnum):
    """Classify configuration failures without retaining hostile values."""

    MISSING_FIELD = "missing_field"
    UNKNOWN_FIELD = "unknown_field"
    CONFLICTING_FIELD = "conflicting_field"
    UNSUPPORTED_VERSION = "unsupported_version"
    INVALID_STATE_ROOT = "invalid_state_root"


@dataclass(frozen=True, slots=True)
class ConfigurationRefusal:
    """Return bounded field-level configuration diagnostics safe for logs."""

    code: ConfigurationRefusalCode
    fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfigurationSource:
    """Name one explicit, non-secret set of runtime configuration values."""

    name: str
    values: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    """Carry validated immutable Stage 1 values into composition."""

    schema_version: int
    state_root: Path
    fingerprint: str


ConfigurationResolution = RuntimeConfiguration | ConfigurationRefusal
_UNSUPPORTED_VERSION_REFUSAL = ConfigurationRefusal(
    ConfigurationRefusalCode.UNSUPPORTED_VERSION, _SCHEMA_VERSION_FIELDS
)
_INVALID_STATE_ROOT_REFUSAL = ConfigurationRefusal(
    ConfigurationRefusalCode.INVALID_STATE_ROOT, _STATE_ROOT_FIELDS
)


def resolve_runtime_configuration(
    sources: Sequence[ConfigurationSource],
    *,
    repository_root: Path,
) -> ConfigurationResolution:
    """Resolve all sources, refusing ambiguity instead of applying precedence."""
    merged = _merge_sources(sources)
    if isinstance(merged, ConfigurationRefusal):
        return merged
    return _validate_configuration(merged, repository_root=repository_root)


def _merge_sources(
    sources: Sequence[ConfigurationSource],
) -> dict[str, object] | ConfigurationRefusal:
    merged: dict[str, object] = {}
    for source in sources:
        unknown = set(source.values) - _FIELDS
        if unknown:
            return ConfigurationRefusal(
                ConfigurationRefusalCode.UNKNOWN_FIELD,
                tuple(sorted(str(field) for field in unknown)),
            )
        for field, value in source.values.items():
            if field in merged:
                previous = merged[field]
                if type(previous) is not type(value) or previous != value:
                    return ConfigurationRefusal(
                        ConfigurationRefusalCode.CONFLICTING_FIELD, (field,)
                    )
            merged[field] = value

    return merged


def _validate_configuration(
    merged: Mapping[str, object], *, repository_root: Path
) -> ConfigurationResolution:
    missing = _FIELDS - set(merged)
    if missing:
        return ConfigurationRefusal(ConfigurationRefusalCode.MISSING_FIELD, tuple(sorted(missing)))
    schema_version = merged["schema_version"]
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        return _UNSUPPORTED_VERSION_REFUSAL
    state_root_value = merged["state_root"]
    state_root = _validate_state_root(state_root_value, repository_root=repository_root)
    if isinstance(state_root, ConfigurationRefusal):
        return state_root

    canonical = {
        "state_root": str(state_root),
        "schema_version": schema_version,
    }
    fingerprint = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return RuntimeConfiguration(schema_version, state_root, fingerprint)


def _validate_state_root(
    state_root_value: object, *, repository_root: Path
) -> Path | ConfigurationRefusal:
    if not isinstance(state_root_value, str):
        return _INVALID_STATE_ROOT_REFUSAL
    state_root = Path(state_root_value)
    if not state_root.is_absolute() or ".." in state_root.parts:
        return _INVALID_STATE_ROOT_REFUSAL
    try:
        if _has_symlink_component(state_root):
            return _INVALID_STATE_ROOT_REFUSAL
        state_root = state_root.resolve()
    except (OSError, RuntimeError, ValueError):
        return _INVALID_STATE_ROOT_REFUSAL
    return _validate_repository_location(state_root, repository_root=repository_root)


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


def _validate_repository_location(
    state_root: Path, *, repository_root: Path
) -> Path | ConfigurationRefusal:
    resolved_repository = repository_root.resolve()
    if state_root == resolved_repository:
        return _INVALID_STATE_ROOT_REFUSAL
    if state_root.is_relative_to(resolved_repository):
        relative = state_root.relative_to(resolved_repository)
        if relative.parts[0] not in _IGNORED_RUNTIME_ROOTS:
            return _INVALID_STATE_ROOT_REFUSAL
        if not _git_ignores_runtime_state(state_root, resolved_repository):
            return _INVALID_STATE_ROOT_REFUSAL

    return state_root


def _git_ignores_runtime_state(state_root: Path, repository_root: Path) -> bool:
    git = shutil.which("git")
    if git is None:
        return False
    relative_root = state_root.relative_to(repository_root)
    database = (state_root / _LIFECYCLE_DATABASE_NAME).relative_to(repository_root)
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    return all(
        _git_ignores_path(git, path, repository_root, environment)
        for path in (f"{relative_root.as_posix()}/", database.as_posix())
    )


def _git_ignores_path(
    git: str,
    path: str,
    repository_root: Path,
    environment: dict[str, str],
) -> bool:
    try:
        completed = subprocess.run(  # noqa: S603
            (git, "check-ignore", "--quiet", "--", path),
            cwd=repository_root,
            env=environment,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return completed.returncode == 0
