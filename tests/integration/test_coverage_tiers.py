from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.check_coverage_tiers import classify_modules, load_policy, main

PACKAGE = Path("src/agentic_investment_os")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INVALID_INPUT_EXIT = 2


def test_exact_thresholds_and_module_without_branches_pass(
    coverage_repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(("--root", str(coverage_repository)))

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "coverage tiers passed: critical lines 100.00% branches 100.00%; "
        "safety-supporting lines 90.00% branches 85.00%; "
        "overall lines 85.00% branches 80.00%\n"
    )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("covered_lines", 17, "overall lines 83.33% (required 85.00%)"),
        ("covered_branches", 10, "overall branches 77.50% (required 80.00%)"),
    ],
)
def test_package_below_either_overall_threshold_is_rejected(
    coverage_repository: Path,
    capsys: pytest.CaptureFixture[str],
    field: str,
    value: int,
    expected: str,
) -> None:
    _change_summary(coverage_repository, "ordinary.py", field, value)

    exit_code = main(("--root", str(coverage_repository)))

    assert exit_code == 1
    output = capsys.readouterr().out
    assert expected in output
    assert "src/agentic_investment_os/ordinary.py" in output


@pytest.mark.parametrize(
    "case",
    [
        ("covered_lines", 9, "critical lines 93.33% (required 100.00%)", "uncovered lines 10"),
        (
            "covered_branches",
            3,
            "critical branches 75.00% (required 100.00%)",
            "uncovered branches 4->5",
        ),
    ],
)
def test_critical_gap_is_rejected_despite_high_package_coverage(
    coverage_repository: Path,
    capsys: pytest.CaptureFixture[str],
    case: tuple[str, int, str, str],
) -> None:
    field, value, expected, detail = case
    _change_summary(coverage_repository, "ordinary.py", "covered_lines", 25)
    _change_summary(coverage_repository, "ordinary.py", "covered_branches", 16)
    _change_summary(coverage_repository, "critical.py", field, value)

    exit_code = main(("--root", str(coverage_repository)))

    assert exit_code == 1
    output = capsys.readouterr().out
    assert expected in output
    assert "src/agentic_investment_os/critical.py" in output
    assert detail in output


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("covered_lines", 17, "safety-supporting lines 85.00% (required 90.00%)"),
        ("covered_branches", 16, "safety-supporting branches 80.00% (required 85.00%)"),
    ],
)
def test_safety_supporting_gap_is_rejected(
    coverage_repository: Path,
    capsys: pytest.CaptureFixture[str],
    field: str,
    value: int,
    expected: str,
) -> None:
    _change_summary(coverage_repository, "ordinary.py", "covered_lines", 25)
    _change_summary(coverage_repository, "ordinary.py", "covered_branches", 16)
    _change_summary(coverage_repository, "support.py", field, value)

    exit_code = main(("--root", str(coverage_repository)))

    assert exit_code == 1
    output = capsys.readouterr().out
    assert expected in output
    assert "src/agentic_investment_os/support.py" in output


def test_current_asset_refusals_and_financial_authority_capabilities_are_critical() -> None:
    policy = load_policy(REPOSITORY_ROOT, REPOSITORY_ROOT / "pyproject.toml")
    modules = classify_modules(REPOSITORY_ROOT, policy)

    assert {
        "src/agentic_investment_os/domain/universe.py",
        "src/agentic_investment_os/entrypoints/configuration.py",
        "src/agentic_investment_os/execution/__init__.py",
        "src/agentic_investment_os/portfolio/__init__.py",
    } <= set(modules.critical)


@pytest.mark.parametrize("capability", ["portfolio", "execution"])
def test_nested_financial_authority_module_inherits_critical_threshold(
    coverage_repository: Path,
    capsys: pytest.CaptureFixture[str],
    capability: str,
) -> None:
    package_root = coverage_repository / PACKAGE
    capability_root = package_root / capability
    capability_root.mkdir()
    (capability_root / "__init__.py").write_text("", encoding="utf-8")
    risk_root = capability_root / "risk"
    risk_root.mkdir()
    (risk_root / "sizing.py").write_text("VALUE = 1\n", encoding="utf-8")
    config_path = coverage_repository / "pyproject.toml"
    config = config_path.read_text(encoding="utf-8").replace(
        'critical_paths = ["critical*.py"]',
        f'critical_paths = ["critical*.py", "{capability}/**/*.py"]',
    )
    config_path.write_text(config, encoding="utf-8")
    _add_module_coverage(coverage_repository, f"{capability}/__init__.py", (0, 0, 0, 0))
    _add_module_coverage(coverage_repository, f"{capability}/risk/sizing.py", (10, 9, 4, 4))
    _change_summary(coverage_repository, "ordinary.py", "covered_lines", 25)
    _change_summary(coverage_repository, "ordinary.py", "covered_branches", 16)

    exit_code = main(("--root", str(coverage_repository)))

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "critical lines 96.00% (required 100.00%)" in output
    assert f"src/agentic_investment_os/{capability}/risk/sizing.py: uncovered lines 10" in output


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        ("", "tool.coverage_tiers is missing"),
        (
            """[tool.coverage_tiers]
schema_version = 1
package_root = "src/agentic_investment_os"
report = ".coverage.json"
overall_lines = 85
overall_branches = 80
critical_lines = 100
critical_branches = 100
safety_supporting_lines = 90
safety_supporting_branches = 85
critical_paths = []
safety_supporting_paths = ["support.py"]
""",
            "critical_paths must be a non-empty array",
        ),
        (
            'critical_paths = ["critical*.py"]',
            "module is assigned to conflicting tiers",
        ),
        (
            'critical_paths = ["missing.py"]',
            "critical_paths pattern matches no implemented production module: missing.py",
        ),
        (
            'critical_paths = ["/critical.py"]',
            "critical_paths must contain normalized package-relative Python patterns",
        ),
    ],
)
def test_invalid_empty_conflicting_or_unmatched_tier_declaration_is_rejected(
    coverage_repository: Path,
    capsys: pytest.CaptureFixture[str],
    replacement: str,
    expected: str,
) -> None:
    config_path = coverage_repository / "pyproject.toml"
    config = config_path.read_text(encoding="utf-8")
    if replacement == "":
        config = "[tool.pytest.ini_options]\n"
    elif replacement.startswith("[tool.coverage_tiers]"):
        config = replacement
    else:
        config = config.replace('critical_paths = ["critical*.py"]', replacement)
        if "conflicting" in expected:
            config = config.replace(
                'safety_supporting_paths = ["support.py"]',
                'safety_supporting_paths = ["critical.py", "support.py"]',
            )
    config_path.write_text(config, encoding="utf-8")

    exit_code = main(("--root", str(coverage_repository)))

    assert exit_code == INVALID_INPUT_EXIT
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize("schema_version", ["true", "1.0"])
def test_non_integer_schema_version_is_rejected(
    coverage_repository: Path,
    capsys: pytest.CaptureFixture[str],
    schema_version: str,
) -> None:
    config_path = coverage_repository / "pyproject.toml"
    config = config_path.read_text(encoding="utf-8").replace(
        "schema_version = 1",
        f"schema_version = {schema_version}",
    )
    config_path.write_text(config, encoding="utf-8")

    exit_code = main(("--root", str(coverage_repository)))

    assert exit_code == INVALID_INPUT_EXIT
    assert "tool.coverage_tiers.schema_version must equal 1" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("invalid-json", "coverage report is not valid JSON"),
        ("missing-files", "coverage report files must be a mapping"),
        ("no-branches", "coverage report must contain branch coverage"),
        (
            "missing-module",
            (
                "configured production module cannot be measured: "
                "src/agentic_investment_os/critical.py"
            ),
        ),
        (
            "incomplete-summary",
            "src/agentic_investment_os/critical.py summary is missing num_branches",
        ),
        (
            "inconsistent-detail",
            "src/agentic_investment_os/critical.py missing_lines does not match summary",
        ),
    ],
)
def test_malformed_or_incomplete_coverage_report_is_rejected(
    coverage_repository: Path,
    capsys: pytest.CaptureFixture[str],
    mutation: str,
    expected: str,
) -> None:
    report_path = coverage_repository / ".coverage.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    critical_path = (PACKAGE / "critical.py").as_posix()
    if mutation == "invalid-json":
        report_path.write_text("{", encoding="utf-8")
    else:
        if mutation == "missing-files":
            del report["files"]
        elif mutation == "no-branches":
            report["meta"]["branch_coverage"] = False
        elif mutation == "missing-module":
            del report["files"][critical_path]
        elif mutation == "incomplete-summary":
            del report["files"][critical_path]["summary"]["num_branches"]
        elif mutation == "inconsistent-detail":
            report["files"][critical_path]["missing_lines"] = [10]
        report_path.write_text(json.dumps(report), encoding="utf-8")

    exit_code = main(("--root", str(coverage_repository)))

    assert exit_code == INVALID_INPUT_EXIT
    assert expected in capsys.readouterr().out


@pytest.fixture
def coverage_repository(tmp_path: Path) -> Path:
    package_root = tmp_path / PACKAGE
    package_root.mkdir(parents=True)
    for name in ("critical.py", "critical_no_branches.py", "support.py", "ordinary.py"):
        (package_root / name).write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """[tool.coverage_tiers]
schema_version = 1
package_root = "src/agentic_investment_os"
report = ".coverage.json"
overall_lines = 85
overall_branches = 80
critical_lines = 100
critical_branches = 100
safety_supporting_lines = 90
safety_supporting_branches = 85
critical_paths = ["critical*.py"]
safety_supporting_paths = ["support.py"]
""",
        encoding="utf-8",
    )
    files = {
        (PACKAGE / "critical.py").as_posix(): _file_coverage(10, 10, 4, 4),
        (PACKAGE / "critical_no_branches.py").as_posix(): _file_coverage(5, 5, 0, 0),
        (PACKAGE / "support.py").as_posix(): _file_coverage(20, 18, 20, 17),
        (PACKAGE / "ordinary.py").as_posix(): _file_coverage(25, 18, 16, 11),
    }
    (tmp_path / ".coverage.json").write_text(
        json.dumps(
            {
                "meta": {"format": 3, "branch_coverage": True},
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def _file_coverage(
    statements: int,
    covered_lines: int,
    branches: int,
    covered_branches: int,
) -> dict[str, object]:
    missing_line_count = statements - covered_lines
    missing_branch_count = branches - covered_branches
    return {
        "missing_lines": list(range(covered_lines + 1, statements + 1)),
        "missing_branches": [
            [branch, branch + 1] for branch in range(covered_branches + 1, branches + 1)
        ],
        "summary": {
            "covered_lines": covered_lines,
            "num_statements": statements,
            "missing_lines": missing_line_count,
            "num_branches": branches,
            "covered_branches": covered_branches,
            "missing_branches": missing_branch_count,
        },
    }


def _change_summary(root: Path, module: str, field: str, value: int) -> None:
    report_path = root / ".coverage.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    item = report["files"][(PACKAGE / module).as_posix()]
    summary = item["summary"]
    summary[field] = value
    if field == "covered_lines":
        summary["missing_lines"] = summary["num_statements"] - value
        item["missing_lines"] = list(range(value + 1, summary["num_statements"] + 1))
    else:
        summary["missing_branches"] = summary["num_branches"] - value
        item["missing_branches"] = [
            [branch, branch + 1] for branch in range(value + 1, summary["num_branches"] + 1)
        ]
    report_path.write_text(json.dumps(report), encoding="utf-8")


def _add_module_coverage(
    root: Path,
    module: str,
    counts: tuple[int, int, int, int],
) -> None:
    statements, covered_lines, branches, covered_branches = counts
    report_path = root / ".coverage.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["files"][(PACKAGE / module).as_posix()] = _file_coverage(
        statements,
        covered_lines,
        branches,
        covered_branches,
    )
    report_path.write_text(json.dumps(report), encoding="utf-8")
