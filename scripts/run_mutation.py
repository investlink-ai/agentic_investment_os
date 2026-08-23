"""Run the critical authority mutation suite and enforce strict outcome accounting."""

from __future__ import annotations

import argparse
import ast
import json
import logging
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONFIG_NAME = "pyproject.toml"
CACHE_NAME = "mutants"
STATS_NAME = "mutmut-cicd-stats.json"
LOGGER = logging.getLogger(__name__)
INVALID_CONFIGURATION_EXIT = 2

_COUNT_KEYS = (
    "killed",
    "survived",
    "total",
    "no_tests",
    "skipped",
    "suspicious",
    "timeout",
    "check_was_interrupted_by_user",
    "segfault",
)
_REJECTED_KEYS = (
    "survived",
    "no_tests",
    "skipped",
    "suspicious",
    "timeout",
    "check_was_interrupted_by_user",
    "segfault",
)
_CALLABLE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
_MUTATION_GATE_KEYS = frozenset({"schema_version", "authority_roots", "noncritical_modules"})


class MutationConfigurationError(ValueError):
    """Reject mutation configuration that could overclaim critical certification."""


@dataclass(frozen=True, slots=True)
class MutationPolicy:
    source_roots: tuple[PurePosixPath, ...]
    source_files: tuple[PurePosixPath, ...]
    test_files: tuple[PurePosixPath, ...]


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        message = f"{name} must be a mapping"
        raise MutationConfigurationError(message)
    return value


def _required_mapping(
    source: Mapping[str, object],
    key: str,
    name: str,
) -> Mapping[str, object]:
    if key not in source:
        message = f"{name} is missing"
        raise MutationConfigurationError(message)
    return _mapping(source[key], name)


def _string_array(
    config: Mapping[str, object],
    key: str,
    *,
    allow_empty: bool,
    section: str = "tool.mutmut",
) -> tuple[str, ...]:
    value = config.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        message = f"{section}.{key} must be an array of strings"
        raise MutationConfigurationError(message)
    if not allow_empty and not value:
        message = f"{section}.{key} must be a non-empty array"
        raise MutationConfigurationError(message)
    if len(value) != len(set(value)):
        message = f"{section}.{key} must not contain duplicates"
        raise MutationConfigurationError(message)
    return tuple(value)


def _exact_python_file(value: str, name: str) -> PurePosixPath:
    path = PurePosixPath(value)
    invalid = (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or not value.endswith(".py")
        or any(character in value for character in "*?[]")
    )
    if invalid:
        kind = (
            "exact test files"
            if name.endswith("pytest_add_cli_args_test_selection")
            else "exact repository-relative Python files"
        )
        message = f"{name} must contain {kind}"
        raise MutationConfigurationError(message)
    return path


def _source_roots(root: Path, config: Mapping[str, object]) -> tuple[PurePosixPath, ...]:
    values = _string_array(config, "source_paths", allow_empty=False)
    roots: list[PurePosixPath] = []
    for value in values:
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != value
            or any(character in value for character in "*?[]")
        ):
            message = "tool.mutmut.source_paths must contain exact repository-relative directories"
            raise MutationConfigurationError(message)
        _reject_symlink_components(root, path, "tool.mutmut.source_paths")
        if not (root / path).is_dir():
            message = f"mutation source path does not exist: {path.as_posix()}"
            raise MutationConfigurationError(message)
        roots.append(path)
    return tuple(roots)


def _configured_files(
    root: Path,
    config: Mapping[str, object],
    key: str,
    *,
    allow_empty: bool,
    section: str = "tool.mutmut",
) -> tuple[PurePosixPath, ...]:
    values = _string_array(config, key, allow_empty=allow_empty, section=section)
    files = tuple(_exact_python_file(value, f"{section}.{key}") for value in values)
    for path in files:
        _reject_symlink_components(root, path, f"{section}.{key}")
        if not (root / path).is_file():
            message = f"{section}.{key} file does not exist: {path.as_posix()}"
            raise MutationConfigurationError(message)
    return files


def _reject_symlink_components(root: Path, path: PurePosixPath, name: str) -> None:
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            message = f"{name} must not traverse symbolic links: {path.as_posix()}"
            raise MutationConfigurationError(message)


def _authority_roots(
    root: Path,
    config: Mapping[str, object],
) -> tuple[PurePosixPath, ...]:
    section = "tool.mutation_gate"
    values = _string_array(
        config,
        "authority_roots",
        allow_empty=False,
        section=section,
    )
    roots: list[PurePosixPath] = []
    for value in values:
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != value
            or any(character in value for character in "*?[]")
        ):
            message = (
                f"{section}.authority_roots must contain exact repository-relative directories"
            )
            raise MutationConfigurationError(message)
        _reject_symlink_components(root, path, f"{section}.authority_roots")
        if not (root / path).is_dir():
            message = f"critical authority root does not exist: {path.as_posix()}"
            raise MutationConfigurationError(message)
        roots.append(path)
    return tuple(roots)


def _load_authority_classification(
    root: Path,
    tool: Mapping[str, object],
    source_files: tuple[PurePosixPath, ...],
) -> None:
    classification = _required_mapping(tool, "mutation_gate", "tool.mutation_gate")
    if unknown_keys := sorted(set(classification) - _MUTATION_GATE_KEYS):
        message = f"tool.mutation_gate contains unknown fields: {', '.join(unknown_keys)}"
        raise MutationConfigurationError(message)
    schema_version = classification.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        message = "tool.mutation_gate.schema_version must be 1"
        raise MutationConfigurationError(message)
    authority_roots = _authority_roots(root, classification)
    noncritical_files = _configured_files(
        root,
        classification,
        "noncritical_modules",
        allow_empty=True,
        section="tool.mutation_gate",
    )
    if set(source_files) & set(noncritical_files):
        message = "critical and non-critical mutation classifications must not overlap"
        raise MutationConfigurationError(message)
    _validate_noncritical_files(root, authority_roots, noncritical_files)
    _validate_authority_inventory(
        root,
        authority_roots,
        source_files,
        noncritical_files,
    )


def _validate_noncritical_files(
    root: Path,
    authority_roots: tuple[PurePosixPath, ...],
    noncritical_files: tuple[PurePosixPath, ...],
) -> None:
    for noncritical_file in noncritical_files:
        if not any(noncritical_file.is_relative_to(path) for path in authority_roots):
            message = (
                "tool.mutation_gate.noncritical_modules file is outside authority_roots: "
                f"{noncritical_file.as_posix()}"
            )
            raise MutationConfigurationError(message)
        if not _has_mutatable_callable(root / noncritical_file):
            message = (
                "tool.mutation_gate.noncritical_modules file has no callable to classify: "
                f"{noncritical_file.as_posix()}"
            )
            raise MutationConfigurationError(message)


def load_policy(root: Path, config_path: Path) -> MutationPolicy:
    """Load and validate the exact critical source and focused test allowlists."""
    try:
        with config_path.open("rb") as stream:
            raw: object = tomllib.load(stream)
    except FileNotFoundError as error:
        message = f"mutation configuration does not exist: {config_path}"
        raise MutationConfigurationError(message) from error
    except tomllib.TOMLDecodeError as error:
        message = "mutation configuration is not valid TOML"
        raise MutationConfigurationError(message) from error

    document = _mapping(raw, "configuration")
    tool = _required_mapping(document, "tool", "tool")
    config = _required_mapping(tool, "mutmut", "tool.mutmut")
    if "do_not_mutate" in config:
        message = "tool.mutmut.only_mutate must be the single mutation source allowlist"
        raise MutationConfigurationError(message)
    if config.get("mutate_only_covered_lines") is not False:
        message = "tool.mutmut.mutate_only_covered_lines must be false"
        raise MutationConfigurationError(message)

    source_roots = _source_roots(root, config)
    source_files = _configured_files(
        root,
        config,
        "only_mutate",
        allow_empty=False,
    )
    for source_file in source_files:
        if not any(source_file.is_relative_to(source_root) for source_root in source_roots):
            message = (
                f"tool.mutmut.only_mutate file is outside source_paths: {source_file.as_posix()}"
            )
            raise MutationConfigurationError(message)

    test_files = _configured_files(
        root,
        config,
        "pytest_add_cli_args_test_selection",
        allow_empty=True,
    )
    if any(not test_file.is_relative_to(PurePosixPath("tests")) for test_file in test_files):
        message = "tool.mutmut.pytest_add_cli_args_test_selection must select tests"
        raise MutationConfigurationError(message)
    _load_authority_classification(root, tool, source_files)
    callables_exist = any(_has_mutatable_callable(root / path) for path in source_files)
    if callables_exist and not test_files:
        message = "critical authority callables require focused test selection"
        raise MutationConfigurationError(message)
    if not callables_exist and test_files:
        message = "scaffold-only mutation scope must not select unrelated tests"
        raise MutationConfigurationError(message)
    return MutationPolicy(source_roots, source_files, test_files)


def _validate_authority_inventory(
    root: Path,
    authority_roots: tuple[PurePosixPath, ...],
    source_files: tuple[PurePosixPath, ...],
    noncritical_files: tuple[PurePosixPath, ...],
) -> None:
    selected = set(source_files)
    classified_noncritical = set(noncritical_files)
    for authority_root in authority_roots:
        _reject_symlink_components(root, authority_root, "critical authority root")
        directory = root / authority_root
        if not directory.is_dir():
            message = f"critical authority root does not exist: {authority_root.as_posix()}"
            raise MutationConfigurationError(message)
        for candidate in sorted(directory.rglob("*.py")):
            relative = PurePosixPath(candidate.relative_to(root).as_posix())
            _reject_symlink_components(root, relative, "critical authority source")
            if (
                relative not in selected
                and relative not in classified_noncritical
                and _has_mutatable_callable(candidate)
            ):
                message = (
                    "authority callable lacks an explicit critical or non-critical classification: "
                    f"{relative.as_posix()}"
                )
                raise MutationConfigurationError(message)


def _has_mutatable_callable(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError) as error:
        message = f"cannot inspect mutation source: {path}"
        raise MutationConfigurationError(message) from error
    for node in tree.body:
        if isinstance(node, _CALLABLE_NODES):
            return True
        if isinstance(node, ast.ClassDef) and any(
            isinstance(member, _CALLABLE_NODES) for member in node.body
        ):
            return True
    return False


def _clean_cache(cache_path: Path) -> None:
    if cache_path.is_symlink() or cache_path.is_file():
        cache_path.unlink()
    elif cache_path.is_dir():
        shutil.rmtree(cache_path)


def _run_mutmut(root: Path, *arguments: str) -> int:
    executable = Path(sys.executable).with_name("mutmut")
    if not executable.is_file():
        message = f"mutmut executable not found beside {sys.executable}"
        raise FileNotFoundError(message)
    # The executable resolves from the active uv environment and every argument is repository-owned.
    completed = subprocess.run(  # noqa: S603
        [str(executable), *arguments],
        cwd=root,
        check=False,
    )
    return completed.returncode


def _load_counts(stats_path: Path) -> dict[str, int]:
    if not stats_path.is_file():
        message = f"mutation statistics were not written to {stats_path}"
        raise FileNotFoundError(message)
    try:
        raw: object = json.loads(stats_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        message = "mutation statistics are not valid JSON"
        raise ValueError(message) from error
    data = _mapping(raw, "mutation statistics")
    counts: dict[str, int] = {}
    for key in _COUNT_KEYS:
        value = data.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            message = f"mutation statistic {key!r} must be a non-negative integer"
            raise ValueError(message)
        counts[key] = value
    return counts


def validate_counts(counts: Mapping[str, int]) -> list[str]:
    """Return every strict mutation-accounting failure in stable result-key order."""
    failures = [f"{key}={counts[key]}" for key in _REJECTED_KEYS if counts[key]]
    accounted = sum(counts[key] for key in _COUNT_KEYS if key != "total")
    if accounted != counts["total"]:
        failures.append(f"unaccounted={counts['total'] - accounted}")
    if counts["total"] == 0:
        failures.append("total=0")
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    """Run clean certification or report the explicit no-callable scaffold exemption."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    arguments = parser.parse_args(argv)
    root = arguments.root.resolve()
    try:
        policy = load_policy(root, root / CONFIG_NAME)
    except MutationConfigurationError as error:
        LOGGER.error("Mutation gate configuration error: %s", error)
        return INVALID_CONFIGURATION_EXIT

    cache_path = root / CACHE_NAME
    _clean_cache(cache_path)
    sources = ", ".join(path.as_posix() for path in policy.source_files)
    tests = ", ".join(path.as_posix() for path in policy.test_files) or "none"
    LOGGER.info("Mutation testing scope: sources=%s; tests=%s.", sources, tests)
    if not any(_has_mutatable_callable(root / path) for path in policy.source_files):
        LOGGER.info(
            "Mutation testing: no critical authority callables exist; "
            "scaffold exemption applies; mutants=0."
        )
        return 0

    if (exit_code := _run_mutmut(root, "run")) != 0:
        return exit_code
    if (exit_code := _run_mutmut(root, "export-cicd-stats")) != 0:
        return exit_code

    counts = _load_counts(cache_path / STATS_NAME)
    if failures := validate_counts(counts):
        LOGGER.error("Mutation gate failed: %s", ", ".join(failures))
        _run_mutmut(root, "results")
        return 1

    LOGGER.info(
        "Mutation gate passed: killed=%d, total=%d.",
        counts["killed"],
        counts["total"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
