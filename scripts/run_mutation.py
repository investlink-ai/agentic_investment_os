"""Run the targeted mutation suite and enforce its safety outcome policy."""

from __future__ import annotations

import ast
import fnmatch
import json
import logging
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "pyproject.toml"
CACHE_PATH = ROOT / "mutants"
STATS_PATH = CACHE_PATH / "mutmut-cicd-stats.json"
LOGGER = logging.getLogger(__name__)

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
    "suspicious",
    "timeout",
    "check_was_interrupted_by_user",
    "segfault",
)
_CALLABLE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        message = f"{name} must be a mapping with string keys"
        raise ValueError(message)
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _string_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        message = f"{name} must be an array of strings"
        raise ValueError(message)
    return [item for item in value if isinstance(item, str)]


def _mutation_config() -> tuple[list[Path], list[str], list[str]]:
    with CONFIG_PATH.open("rb") as stream:
        document = _mapping(tomllib.load(stream), "pyproject.toml")
    tool = _mapping(document.get("tool"), "tool")
    config = _mapping(tool.get("mutmut"), "tool.mutmut")
    source_paths = [
        ROOT / path for path in _string_list(config.get("source_paths"), "source_paths")
    ]
    only_mutate = _string_list(config.get("only_mutate", []), "only_mutate")
    do_not_mutate = _string_list(config.get("do_not_mutate", []), "do_not_mutate")
    return source_paths, only_mutate, do_not_mutate


def _matches(path: Path, patterns: list[str]) -> bool:
    relative_path = path.relative_to(ROOT).as_posix()
    return any(fnmatch.fnmatch(relative_path, pattern) for pattern in patterns)


def _is_selected(path: Path, only_mutate: list[str], do_not_mutate: list[str]) -> bool:
    included = not only_mutate or _matches(path, only_mutate)
    return included and not _matches(path, do_not_mutate)


def _has_mutatable_callable(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, _CALLABLE_NODES):
            return True
        if isinstance(node, ast.ClassDef) and any(
            isinstance(member, _CALLABLE_NODES) for member in node.body
        ):
            return True
    return False


def _target_callables_exist() -> bool:
    source_paths, only_mutate, do_not_mutate = _mutation_config()
    for source_path in source_paths:
        if not source_path.exists():
            message = f"mutation source path does not exist: {source_path.relative_to(ROOT)}"
            raise FileNotFoundError(message)
        candidates = [source_path] if source_path.is_file() else source_path.rglob("*.py")
        for path in candidates:
            if _is_selected(path, only_mutate, do_not_mutate) and _has_mutatable_callable(path):
                return True
    return False


def _run_mutmut(*arguments: str) -> int:
    executable = Path(sys.executable).with_name("mutmut")
    if not executable.is_file():
        message = f"mutmut executable not found beside {sys.executable}"
        raise FileNotFoundError(message)
    # The executable resolves from the active uv environment and every argument is repository-owned.
    completed = subprocess.run(  # noqa: S603
        [str(executable), *arguments],
        cwd=ROOT,
        check=False,
    )
    return completed.returncode


def _load_counts() -> dict[str, int]:
    if not STATS_PATH.is_file():
        message = f"mutation statistics were not written to {STATS_PATH.relative_to(ROOT)}"
        raise FileNotFoundError(message)
    raw = json.loads(STATS_PATH.read_text(encoding="utf-8"))
    data = _mapping(raw, "mutation statistics")
    counts: dict[str, int] = {}
    for key in _COUNT_KEYS:
        value = data.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            message = f"mutation statistic {key!r} must be a non-negative integer"
            raise ValueError(message)
        counts[key] = value
    return counts


def _validate_counts(counts: dict[str, int]) -> list[str]:
    failures = [f"{key}={counts[key]}" for key in _REJECTED_KEYS if counts[key]]
    accounted = sum(counts[key] for key in _COUNT_KEYS if key != "total")
    if accounted != counts["total"]:
        failures.append(f"unaccounted={counts['total'] - accounted}")
    if counts["total"] == 0:
        failures.append("total=0")
    return failures


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if CACHE_PATH.is_dir():
        shutil.rmtree(CACHE_PATH)
    if not _target_callables_exist():
        LOGGER.info("Mutation testing: no scoped callables exist; scaffold exemption applies.")
        return 0

    if (exit_code := _run_mutmut("run")) != 0:
        return exit_code
    if (exit_code := _run_mutmut("export-cicd-stats")) != 0:
        return exit_code

    counts = _load_counts()
    if failures := _validate_counts(counts):
        LOGGER.error("Mutation gate failed: %s", ", ".join(failures))
        _run_mutmut("results")
        return 1

    LOGGER.info(
        "Mutation gate passed: killed=%d, skipped=%d, total=%d.",
        counts["killed"],
        counts["skipped"],
        counts["total"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
