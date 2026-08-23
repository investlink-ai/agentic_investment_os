"""Resolve complete runtime and universe configuration without ambient defaults."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, assert_never

from agentic_investment_os.adapters.recorded_universe import is_alpaca_paper_identity
from agentic_investment_os.domain.identity import AssetClass
from agentic_investment_os.domain.universe import EquityUniversePolicy, UniverseRefusal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_FIELDS = frozenset({"schema_version", "state_root", "enabled_asset_classes", "universe_policy"})
_IGNORED_RUNTIME_ROOTS = frozenset({"artifacts", "data", "var"})
_SCHEMA_VERSION_FIELDS = ("schema_version",)
_STATE_ROOT_FIELDS = ("state_root",)
_ENABLED_ASSET_CLASS_FIELDS = ("enabled_asset_classes",)
_UNIVERSE_POLICY_FIELDS = ("universe_policy",)
_LIFECYCLE_DATABASE_NAME = "lifecycle.sqlite3"


class ConfigurationRefusalCode(StrEnum):
    """Classify configuration failures without retaining hostile values."""

    MISSING_FIELD = "missing_field"
    UNKNOWN_FIELD = "unknown_field"
    CONFLICTING_FIELD = "conflicting_field"
    UNSUPPORTED_VERSION = "unsupported_version"
    INVALID_STATE_ROOT = "invalid_state_root"
    INVALID_ENABLED_ASSET_CLASSES = "invalid_enabled_asset_classes"
    INVALID_UNIVERSE_POLICY = "invalid_universe_policy"


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
    """Carry validated immutable runtime and universe-policy values into composition."""

    schema_version: int
    state_root: Path
    enabled_asset_classes: tuple[AssetClass, ...]
    universe_policy: EquityUniversePolicy
    fingerprint: str


ConfigurationResolution = RuntimeConfiguration | ConfigurationRefusal
_UNSUPPORTED_VERSION_REFUSAL = ConfigurationRefusal(
    ConfigurationRefusalCode.UNSUPPORTED_VERSION, _SCHEMA_VERSION_FIELDS
)
_INVALID_STATE_ROOT_REFUSAL = ConfigurationRefusal(
    ConfigurationRefusalCode.INVALID_STATE_ROOT, _STATE_ROOT_FIELDS
)
_INVALID_ENABLED_ASSET_CLASSES_REFUSAL = ConfigurationRefusal(
    ConfigurationRefusalCode.INVALID_ENABLED_ASSET_CLASSES, _ENABLED_ASSET_CLASS_FIELDS
)
_INVALID_UNIVERSE_POLICY_REFUSAL = ConfigurationRefusal(
    ConfigurationRefusalCode.INVALID_UNIVERSE_POLICY, _UNIVERSE_POLICY_FIELDS
)
_UNKNOWN_FIELD_REFUSAL = ConfigurationRefusal(ConfigurationRefusalCode.UNKNOWN_FIELD)


def resolve_runtime_configuration(
    sources: Sequence[ConfigurationSource],
    *,
    repository_root: Path,
) -> ConfigurationResolution:
    """Resolve all sources, refusing ambiguity instead of applying precedence."""
    merged = _merge_sources(sources)
    if isinstance(merged, ConfigurationRefusal):
        return merged
    if isinstance(merged, dict):
        return _validate_configuration(merged, repository_root=repository_root)
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(merged)  # pragma: no cover


def _merge_sources(
    sources: Sequence[ConfigurationSource],
) -> dict[str, object] | ConfigurationRefusal:
    merged: dict[str, object] = {}
    for source in sources:
        if type(source.values) is not dict or any(
            type(field) is not str or field not in _FIELDS for field in source.values
        ):
            return _UNKNOWN_FIELD_REFUSAL
        for field, value in source.values.items():
            if field in merged:
                previous = merged[field]
                if not _same_configuration_value(previous, value):
                    return ConfigurationRefusal(
                        ConfigurationRefusalCode.CONFLICTING_FIELD, (field,)
                    )
            merged[field] = value

    return merged


def _same_configuration_value(left: object, right: object) -> bool:
    """Compare JSON-shaped configuration without numeric type coercion."""
    try:
        return _same_configuration_value_unchecked(left, right)
    except (RecursionError, ValueError):
        return False


def _same_configuration_value_unchecked(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    match left, right:
        case dict() as left_map, dict() as right_map:
            return _same_configuration_map(left_map, right_map)
        case list() as left_list, list() as right_list:
            return _same_configuration_list(left_list, right_list)
        case _:
            return _same_configuration_scalar(left, right)


def _same_configuration_map(left: dict[object, object], right: dict[object, object]) -> bool:
    if type(left) is not dict:
        return False
    if (
        any(type(key) is not str for mapping in (left, right) for key in mapping)
        or left.keys() != right.keys()
    ):
        return False
    return all(_same_configuration_value_unchecked(left[key], right[key]) for key in left)


def _same_configuration_list(left: list[object], right: list[object]) -> bool:
    if type(left) is not list:
        return False
    return all(
        _same_configuration_value_unchecked(left_item, right_item)
        for left_item, right_item in zip(left, right, strict=True)
    )


def _same_configuration_scalar(left: object, right: object) -> bool:
    if left is None:
        return True
    return type(left) in (bool, int, float, str) and left == right


def _validate_configuration(
    merged: Mapping[str, object], *, repository_root: Path
) -> ConfigurationResolution:
    missing = _FIELDS - set(merged)
    if missing:
        return ConfigurationRefusal(ConfigurationRefusalCode.MISSING_FIELD, tuple(sorted(missing)))
    schema_version = merged["schema_version"]
    if type(schema_version) is not int or schema_version != 1:
        return _UNSUPPORTED_VERSION_REFUSAL
    state_root_value = merged["state_root"]
    state_root = _validate_state_root(state_root_value, repository_root=repository_root)
    if isinstance(state_root, ConfigurationRefusal):
        return state_root
    if not isinstance(state_root, Path):
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(state_root)  # pragma: no cover
    enabled_asset_classes = _validate_enabled_asset_classes(merged["enabled_asset_classes"])
    if enabled_asset_classes is None:
        return _INVALID_ENABLED_ASSET_CLASSES_REFUSAL
    universe_policy = EquityUniversePolicy.parse(merged["universe_policy"])
    if isinstance(universe_policy, UniverseRefusal) or (
        isinstance(universe_policy, EquityUniversePolicy)
        and any(
            not is_alpaca_paper_identity(identity) for identity in universe_policy.etf_allowlist
        )
    ):
        return _INVALID_UNIVERSE_POLICY_REFUSAL
    if not isinstance(universe_policy, EquityUniversePolicy):
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(universe_policy)  # pragma: no cover

    canonical = {
        "state_root": str(state_root),
        "schema_version": schema_version,
        "enabled_asset_classes": [item.value for item in enabled_asset_classes],
        "universe_policy": universe_policy.to_payload(),
    }
    fingerprint = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return RuntimeConfiguration(
        schema_version,
        state_root,
        enabled_asset_classes,
        universe_policy,
        fingerprint,
    )


def _validate_enabled_asset_classes(value: object) -> tuple[AssetClass, ...] | None:
    if (
        type(value) is not list
        or len(value) != 1
        or type(value[0]) is not str
        or value[0] != AssetClass.US_EQUITY.value
    ):
        return None
    return (AssetClass.US_EQUITY,)


def _validate_state_root(
    state_root_value: object, *, repository_root: Path
) -> Path | ConfigurationRefusal:
    if type(state_root_value) is not str:
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
