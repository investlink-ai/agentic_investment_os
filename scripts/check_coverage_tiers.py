"""Enforce consequence-tiered line and branch coverage thresholds."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


CONFIG_SECTION = "tool.coverage_tiers"
SUPPORTED_SCHEMA_VERSION = 1
PERCENT_MAXIMUM = 100
BRANCH_PAIR_SIZE = 2
MAX_MISSING_DETAILS = 20


class CoverageConfigurationError(ValueError):
    """Reject an invalid or incomplete repository coverage policy."""


class CoverageReportError(ValueError):
    """Reject malformed or incomplete machine-readable coverage evidence."""


@dataclass(frozen=True, slots=True)
class MetricCounts:
    covered: int
    total: int

    @property
    def percentage(self) -> Decimal:
        if self.total == 0:
            return Decimal(100)
        return Decimal(self.covered * 100) / Decimal(self.total)

    def __add__(self, other: MetricCounts) -> MetricCounts:
        return MetricCounts(self.covered + other.covered, self.total + other.total)


@dataclass(frozen=True, slots=True)
class ModuleCoverage:
    path: str
    lines: MetricCounts
    branches: MetricCounts
    missing_lines: tuple[int, ...]
    missing_branches: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class CoveragePolicy:
    package_root: PurePosixPath
    report: PurePosixPath
    overall_lines: Decimal
    overall_branches: Decimal
    critical_lines: Decimal
    critical_branches: Decimal
    safety_supporting_lines: Decimal
    safety_supporting_branches: Decimal
    critical_paths: tuple[str, ...]
    safety_supporting_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClassifiedModules:
    all_modules: tuple[str, ...]
    critical: tuple[str, ...]
    safety_supporting: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CoverageResult:
    name: str
    modules: tuple[ModuleCoverage, ...]
    lines: MetricCounts
    branches: MetricCounts
    required_lines: Decimal
    required_branches: Decimal


def _mapping(value: object, name: str, error_type: type[ValueError]) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        message = f"{name} must be a mapping"
        raise error_type(message)
    return value


def _required_mapping(
    source: Mapping[str, object],
    key: str,
    name: str,
    error_type: type[ValueError],
) -> Mapping[str, object]:
    if key not in source:
        message = f"{name} is missing"
        raise error_type(message)
    return _mapping(source[key], name, error_type)


def _nonnegative_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        message = f"{name} must be a non-negative integer"
        raise CoverageReportError(message)
    return value


def _threshold(config: Mapping[str, object], key: str) -> Decimal:
    value = config.get(key)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not Decimal(str(value)).is_finite()
    ):
        message = f"{CONFIG_SECTION}.{key} must be a percentage from 0 through 100"
        raise CoverageConfigurationError(message)
    threshold = Decimal(str(value))
    if threshold < 0 or threshold > PERCENT_MAXIMUM:
        message = f"{CONFIG_SECTION}.{key} must be a percentage from 0 through 100"
        raise CoverageConfigurationError(message)
    return threshold


def _relative_path(value: object, name: str, *, python_pattern: bool = False) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        message = f"{name} must be a normalized repository-relative path"
        raise CoverageConfigurationError(message)
    path = PurePosixPath(value)
    invalid = path.is_absolute() or ".." in path.parts or path.as_posix() != value
    if python_pattern:
        invalid = invalid or not value.endswith(".py")
    if invalid:
        if python_pattern:
            message = f"{name} must contain normalized package-relative Python patterns"
        else:
            message = f"{name} must be a normalized repository-relative path"
        raise CoverageConfigurationError(message)
    return path


def _patterns(config: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = config.get(key)
    if not isinstance(value, list) or not value:
        message = f"{key} must be a non-empty array"
        raise CoverageConfigurationError(message)
    patterns: list[str] = []
    for item in value:
        pattern = _relative_path(item, key, python_pattern=True).as_posix()
        if pattern in patterns:
            message = f"{key} contains a duplicate pattern: {pattern}"
            raise CoverageConfigurationError(message)
        patterns.append(pattern)
    return tuple(patterns)


def load_policy(root: Path, config_path: Path) -> CoveragePolicy:
    try:
        with config_path.open("rb") as stream:
            raw: object = tomllib.load(stream)
    except FileNotFoundError as error:
        message = f"coverage configuration does not exist: {config_path}"
        raise CoverageConfigurationError(message) from error
    except tomllib.TOMLDecodeError as error:
        message = "coverage configuration is not valid TOML"
        raise CoverageConfigurationError(message) from error

    document = _mapping(raw, "configuration", CoverageConfigurationError)
    tool = _required_mapping(document, "tool", "tool", CoverageConfigurationError)
    config = _required_mapping(
        tool,
        "coverage_tiers",
        CONFIG_SECTION,
        CoverageConfigurationError,
    )
    schema_version = config.get("schema_version")
    if type(schema_version) is not int or schema_version != SUPPORTED_SCHEMA_VERSION:
        message = f"{CONFIG_SECTION}.schema_version must equal {SUPPORTED_SCHEMA_VERSION}"
        raise CoverageConfigurationError(message)
    package_root = _relative_path(config.get("package_root"), "package_root")
    package_directory = root / package_root
    if not package_directory.is_dir():
        message = f"configured package path does not exist: {package_root.as_posix()}"
        raise CoverageConfigurationError(message)
    report = _relative_path(config.get("report"), "report")
    return CoveragePolicy(
        package_root=package_root,
        report=report,
        overall_lines=_threshold(config, "overall_lines"),
        overall_branches=_threshold(config, "overall_branches"),
        critical_lines=_threshold(config, "critical_lines"),
        critical_branches=_threshold(config, "critical_branches"),
        safety_supporting_lines=_threshold(config, "safety_supporting_lines"),
        safety_supporting_branches=_threshold(config, "safety_supporting_branches"),
        critical_paths=_patterns(config, "critical_paths"),
        safety_supporting_paths=_patterns(config, "safety_supporting_paths"),
    )


def classify_modules(root: Path, policy: CoveragePolicy) -> ClassifiedModules:
    package_directory = root / policy.package_root
    package_modules = tuple(
        sorted(
            path.relative_to(package_directory).as_posix()
            for path in package_directory.rglob("*.py")
            if path.is_file()
        )
    )
    if not package_modules:
        message = (
            f"configured package contains no implemented production modules: {policy.package_root}"
        )
        raise CoverageConfigurationError(message)

    critical = _matched_modules(package_modules, policy.critical_paths, "critical_paths")
    safety_supporting = _matched_modules(
        package_modules,
        policy.safety_supporting_paths,
        "safety_supporting_paths",
    )
    conflicts = sorted(set(critical) & set(safety_supporting))
    if conflicts:
        message = f"module is assigned to conflicting tiers: {conflicts[0]}"
        raise CoverageConfigurationError(message)
    return ClassifiedModules(
        all_modules=tuple((policy.package_root / path).as_posix() for path in package_modules),
        critical=tuple((policy.package_root / path).as_posix() for path in critical),
        safety_supporting=tuple(
            (policy.package_root / path).as_posix() for path in safety_supporting
        ),
    )


def _matched_modules(
    modules: tuple[str, ...],
    patterns: tuple[str, ...],
    name: str,
) -> tuple[str, ...]:
    matched: set[str] = set()
    for pattern in patterns:
        matches = {module for module in modules if _module_matches(module, pattern)}
        if not matches:
            message = f"{name} pattern matches no implemented production module: {pattern}"
            raise CoverageConfigurationError(message)
        matched.update(matches)
    return tuple(sorted(matched))


def _module_matches(module: str, pattern: str) -> bool:
    prefix, recursive, suffix = pattern.partition("/**/")
    if not recursive:
        return PurePosixPath(module).match(pattern)
    if not module.startswith(f"{prefix}/"):
        return False
    relative = module.removeprefix(f"{prefix}/")
    return PurePosixPath(relative).match(suffix)


def load_report(report_path: Path, modules: ClassifiedModules) -> Mapping[str, ModuleCoverage]:
    try:
        raw: object = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        message = f"coverage report does not exist: {report_path}"
        raise CoverageReportError(message) from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        message = "coverage report is not valid JSON"
        raise CoverageReportError(message) from error

    document = _mapping(raw, "coverage report", CoverageReportError)
    meta = _required_mapping(document, "meta", "coverage report meta", CoverageReportError)
    if meta.get("branch_coverage") is not True:
        message = "coverage report must contain branch coverage"
        raise CoverageReportError(message)
    files = _mapping(document.get("files"), "coverage report files", CoverageReportError)
    configured = set(modules.critical) | set(modules.safety_supporting)
    parsed: dict[str, ModuleCoverage] = {}
    for path in modules.all_modules:
        if path not in files:
            qualifier = "configured " if path in configured else ""
            message = f"{qualifier}production module cannot be measured: {path}"
            raise CoverageReportError(message)
        parsed[path] = _module_coverage(path, files[path])
    return parsed


def _module_coverage(path: str, value: object) -> ModuleCoverage:
    item = _mapping(value, path, CoverageReportError)
    summary = _required_mapping(item, "summary", f"{path} summary", CoverageReportError)
    required_fields = (
        "covered_lines",
        "num_statements",
        "missing_lines",
        "covered_branches",
        "num_branches",
        "missing_branches",
    )
    counts: dict[str, int] = {}
    for field in required_fields:
        if field not in summary:
            message = f"{path} summary is missing {field}"
            raise CoverageReportError(message)
        counts[field] = _nonnegative_integer(summary[field], f"{path} summary {field}")

    lines = MetricCounts(counts["covered_lines"], counts["num_statements"])
    branches = MetricCounts(counts["covered_branches"], counts["num_branches"])
    if lines.covered + counts["missing_lines"] != lines.total:
        message = f"{path} line counts are inconsistent"
        raise CoverageReportError(message)
    if branches.covered + counts["missing_branches"] != branches.total:
        message = f"{path} branch counts are inconsistent"
        raise CoverageReportError(message)
    missing_lines = _integer_array(item.get("missing_lines"), f"{path} missing_lines")
    missing_branches = _branch_array(item.get("missing_branches"), f"{path} missing_branches")
    if len(missing_lines) != counts["missing_lines"]:
        message = f"{path} missing_lines does not match summary"
        raise CoverageReportError(message)
    if len(missing_branches) != counts["missing_branches"]:
        message = f"{path} missing_branches does not match summary"
        raise CoverageReportError(message)
    return ModuleCoverage(path, lines, branches, missing_lines, missing_branches)


def _integer_array(value: object, name: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        message = f"{name} must be an array of positive integers"
        raise CoverageReportError(message)
    result: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
            message = f"{name} must be an array of positive integers"
            raise CoverageReportError(message)
        result.append(item)
    if len(result) != len(set(result)):
        message = f"{name} must not contain duplicates"
        raise CoverageReportError(message)
    return tuple(result)


def _branch_array(value: object, name: str) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list):
        message = f"{name} must be an array of integer branch pairs"
        raise CoverageReportError(message)
    result: list[tuple[int, int]] = []
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != BRANCH_PAIR_SIZE
            or not all(isinstance(part, int) and not isinstance(part, bool) for part in item)
        ):
            message = f"{name} must be an array of integer branch pairs"
            raise CoverageReportError(message)
        result.append((item[0], item[1]))
    if len(result) != len(set(result)):
        message = f"{name} must not contain duplicates"
        raise CoverageReportError(message)
    return tuple(result)


def evaluate_coverage(
    policy: CoveragePolicy,
    modules: ClassifiedModules,
    report: Mapping[str, ModuleCoverage],
) -> tuple[CoverageResult, CoverageResult, CoverageResult]:
    return (
        _result(
            "critical",
            modules.critical,
            report,
            policy.critical_lines,
            policy.critical_branches,
        ),
        _result(
            "safety-supporting",
            modules.safety_supporting,
            report,
            policy.safety_supporting_lines,
            policy.safety_supporting_branches,
        ),
        _result(
            "overall",
            modules.all_modules,
            report,
            policy.overall_lines,
            policy.overall_branches,
        ),
    )


def _result(
    name: str,
    paths: tuple[str, ...],
    report: Mapping[str, ModuleCoverage],
    required_lines: Decimal,
    required_branches: Decimal,
) -> CoverageResult:
    selected = tuple(report[path] for path in paths)
    lines = MetricCounts(0, 0)
    branches = MetricCounts(0, 0)
    for module in selected:
        lines += module.lines
        branches += module.branches
    return CoverageResult(
        name,
        selected,
        lines,
        branches,
        required_lines,
        required_branches,
    )


def _format_percentage(value: Decimal) -> str:
    return f"{value:.2f}%"


def report_results(results: tuple[CoverageResult, ...]) -> int:
    failures: list[tuple[CoverageResult, str]] = []
    for result in results:
        if result.lines.percentage < result.required_lines:
            failures.append((result, "lines"))
        if result.branches.percentage < result.required_branches:
            failures.append((result, "branches"))
    if not failures:
        summary = "; ".join(
            f"{result.name} lines {_format_percentage(result.lines.percentage)} "
            f"branches {_format_percentage(result.branches.percentage)}"
            for result in results
        )
        sys.stdout.write(f"coverage tiers passed: {summary}\n")
        return 0

    output = ["coverage tier gate failed:"]
    reported_modules: set[tuple[str, str]] = set()
    for result, metric_name in failures:
        metric = result.lines if metric_name == "lines" else result.branches
        required = result.required_lines if metric_name == "lines" else result.required_branches
        output.append(
            f"- {result.name} {metric_name} {_format_percentage(metric.percentage)} "
            f"(required {_format_percentage(required)})"
        )
        for module in result.modules:
            key = (module.path, metric_name)
            if metric_name == "lines":
                detail = _line_detail(module.missing_lines)
            else:
                detail = _branch_detail(module.missing_branches)
            if detail is None or key in reported_modules:
                continue
            reported_modules.add(key)
            output.append(f"  {module.path}: uncovered {metric_name} {detail}")
    sys.stdout.write(f"{'\n'.join(output)}\n")
    return 1


def _line_detail(values: tuple[int, ...]) -> str | None:
    return _bounded_detail([str(value) for value in values])


def _branch_detail(values: tuple[tuple[int, int], ...]) -> str | None:
    return _bounded_detail([f"{source}->{destination}" for source, destination in values])


def _bounded_detail(rendered: list[str]) -> str | None:
    if not rendered:
        return None
    if len(rendered) > MAX_MISSING_DETAILS:
        rendered = [
            *rendered[:MAX_MISSING_DETAILS],
            f"... ({len(rendered) - MAX_MISSING_DETAILS} more)",
        ]
    return ", ".join(rendered)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the gate, write diagnostics, and return 0, 1, or 2 for pass, gap, or invalid input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args(argv)
    root = arguments.root.resolve()
    config_path = arguments.config
    if not config_path.is_absolute():
        config_path = root / config_path
    try:
        policy = load_policy(root, config_path)
        modules = classify_modules(root, policy)
        report_path = arguments.report or Path(policy.report.as_posix())
        if not report_path.is_absolute():
            report_path = root / report_path
        report = load_report(report_path, modules)
    except CoverageConfigurationError as error:
        sys.stdout.write(f"coverage gate configuration error: {error}\n")
        return 2
    except CoverageReportError as error:
        sys.stdout.write(f"coverage gate report error: {error}\n")
        return 2
    return report_results(evaluate_coverage(policy, modules, report))


if __name__ == "__main__":
    sys.exit(main())
