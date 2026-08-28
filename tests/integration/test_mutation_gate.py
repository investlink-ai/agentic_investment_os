from __future__ import annotations

import logging
import tomllib
from pathlib import Path

import pytest
from scripts import run_mutation

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MUTATION_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/mutation.yml"
EXPECTED_MUTATION_SOURCES = [
    "src/agentic_investment_os/execution/__init__.py",
    "src/agentic_investment_os/memory/admission.py",
    "src/agentic_investment_os/memory/reducer.py",
    "src/agentic_investment_os/portfolio/__init__.py",
    "src/agentic_investment_os/portfolio/construction.py",
]
EXPECTED_AUTHORITY_ROOTS = [
    "src/agentic_investment_os/execution",
    "src/agentic_investment_os/memory",
    "src/agentic_investment_os/portfolio",
]
EXPECTED_NONCRITICAL_MODULES = ["src/agentic_investment_os/memory/beliefs.py"]
EXPECTED_MUTATION_TESTS = [
    "tests/unit/test_beliefs.py",
    "tests/contract/test_belief_event_contract.py",
    "tests/unit/test_portfolio.py",
]
INVALID_CONFIGURATION_EXIT = 2
REJECTED_RESULT_KEYS = (
    "survived",
    "no_tests",
    "skipped",
    "suspicious",
    "timeout",
    "check_was_interrupted_by_user",
    "segfault",
)


def test_repository_scope_is_an_exact_static_authority_allowlist() -> None:
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as stream:
        tool = tomllib.load(stream)["tool"]
    config = tool["mutmut"]
    classification = tool["mutation_gate"]

    assert config["only_mutate"] == EXPECTED_MUTATION_SOURCES
    assert config["pytest_add_cli_args_test_selection"] == EXPECTED_MUTATION_TESTS
    assert config["mutate_only_covered_lines"] is False
    assert "do_not_mutate" not in config
    assert all("*" not in path for path in config["only_mutate"])
    assert classification == {
        "schema_version": 1,
        "authority_roots": EXPECTED_AUTHORITY_ROOTS,
        "noncritical_modules": EXPECTED_NONCRITICAL_MODULES,
    }


@pytest.mark.parametrize("cache_kind", ["directory", "file", "symlink"])
def test_scaffold_exemption_cleans_cached_state_without_invoking_mutmut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    cache_kind: str,
) -> None:
    root = _mutation_repository(tmp_path)
    cache = root / "mutants"
    if cache_kind == "directory":
        cache.mkdir()
        (cache / "cached-result").write_text("stale", encoding="utf-8")
    elif cache_kind == "file":
        cache.write_text("stale", encoding="utf-8")
    else:
        link_target = root / "outside-cache"
        link_target.mkdir()
        cache.symlink_to(link_target, target_is_directory=True)

    def unexpected_mutmut(root: Path, *arguments: str) -> int:
        pytest.fail(f"mutmut was invoked for scaffold-only scope: {root}, {arguments}")

    monkeypatch.setattr(run_mutation, "_run_mutmut", unexpected_mutmut)
    caplog.set_level(logging.INFO)

    exit_code = run_mutation.main(("--root", str(root)))

    assert exit_code == 0
    assert not cache.exists()
    assert not cache.is_symlink()
    if cache_kind == "symlink":
        assert link_target.is_dir()
    assert "no critical authority callables exist; scaffold exemption applies" in caplog.text
    assert "mutants=0" in caplog.text


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            "wildcard_source",
            "only_mutate must contain exact repository-relative Python files",
        ),
        (
            "callable_without_tests",
            "critical authority callables require focused test selection",
        ),
        (
            "suite_directory",
            "pytest_add_cli_args_test_selection must contain exact test files",
        ),
        (
            "covered_line_filter",
            "mutate_only_covered_lines must be false",
        ),
    ],
)
def test_broad_or_incomplete_mutation_selection_fails_closed(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    mutation: str,
    expected: str,
) -> None:
    root = _mutation_repository(tmp_path)
    config_path = root / "pyproject.toml"
    config = config_path.read_text(encoding="utf-8")
    if mutation == "wildcard_source":
        config = config.replace(
            "src/agentic_investment_os/portfolio/__init__.py",
            "src/agentic_investment_os/portfolio/*.py",
        )
    elif mutation == "callable_without_tests":
        (root / "src/agentic_investment_os/portfolio/__init__.py").write_text(
            "def size_position() -> int:\n    return 1\n",
            encoding="utf-8",
        )
    elif mutation == "suite_directory":
        config = config.replace(
            "pytest_add_cli_args_test_selection = []",
            'pytest_add_cli_args_test_selection = ["tests/unit"]',
        )
    else:
        config = config.replace(
            "mutate_only_covered_lines = false",
            "mutate_only_covered_lines = true",
        )
    config_path.write_text(config, encoding="utf-8")
    caplog.set_level(logging.ERROR)

    exit_code = run_mutation.main(("--root", str(root)))

    assert exit_code == INVALID_CONFIGURATION_EXIT
    assert expected in caplog.text


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("boolean_version", "tool.mutation_gate.schema_version must be 1"),
        ("unknown_field", "tool.mutation_gate contains unknown fields: unexpected"),
    ],
)
def test_mutation_gate_schema_fails_closed(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    mutation: str,
    expected: str,
) -> None:
    root = _mutation_repository(tmp_path)
    config_path = root / "pyproject.toml"
    config = config_path.read_text(encoding="utf-8")
    if mutation == "boolean_version":
        config = config.replace("schema_version = 1", "schema_version = true")
    else:
        config = config.replace(
            "noncritical_modules = []",
            "noncritical_modules = []\nunexpected = true",
        )
    config_path.write_text(config, encoding="utf-8")
    caplog.set_level(logging.ERROR)

    exit_code = run_mutation.main(("--root", str(root)))

    assert exit_code == INVALID_CONFIGURATION_EXIT
    assert expected in caplog.text


@pytest.mark.parametrize("rejected_key", REJECTED_RESULT_KEYS)
def test_every_non_killed_outcome_fails_strict_accounting(rejected_key: str) -> None:
    counts = _counts(killed=1, total=2, **{rejected_key: 1})

    assert run_mutation.validate_counts(counts) == [f"{rejected_key}=1"]


def test_unjustified_skip_and_unaccounted_result_fail() -> None:
    assert run_mutation.validate_counts(_counts(killed=1, skipped=1, total=2)) == ["skipped=1"]
    assert run_mutation.validate_counts(_counts(killed=1, total=2)) == ["unaccounted=1"]
    assert run_mutation.validate_counts(_counts()) == ["total=0"]


def test_unlisted_authority_callable_fails_scaffold_exemption(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = _mutation_repository(tmp_path)
    (root / "src/agentic_investment_os/portfolio/sizing.py").write_text(
        "def size_position() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    caplog.set_level(logging.ERROR)

    exit_code = run_mutation.main(("--root", str(root)))

    assert exit_code == INVALID_CONFIGURATION_EXIT
    assert (
        "authority callable lacks an explicit critical or non-critical classification"
        in caplog.text
    )


def test_explicit_noncritical_classification_does_not_expand_mutation_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = _mutation_repository(tmp_path)
    diagnostics = root / "src/agentic_investment_os/portfolio/diagnostics.py"
    diagnostics.write_text("def render_report() -> str:\n    return 'report'\n", encoding="utf-8")
    config_path = root / "pyproject.toml"
    config = config_path.read_text(encoding="utf-8").replace(
        "noncritical_modules = []",
        'noncritical_modules = ["src/agentic_investment_os/portfolio/diagnostics.py"]',
    )
    config_path.write_text(config, encoding="utf-8")

    def unexpected_mutmut(root: Path, *arguments: str) -> int:
        pytest.fail(f"mutmut was invoked for non-critical behavior: {root}, {arguments}")

    monkeypatch.setattr(run_mutation, "_run_mutmut", unexpected_mutmut)
    caplog.set_level(logging.INFO)

    exit_code = run_mutation.main(("--root", str(root)))

    assert exit_code == 0
    assert "no critical authority callables exist; scaffold exemption applies" in caplog.text


@pytest.mark.parametrize("configured_kind", ["source", "test"])
def test_configured_symlink_fails_closed(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    configured_kind: str,
) -> None:
    root = _mutation_repository(tmp_path)
    external = tmp_path / f"external-{configured_kind}.py"
    external.write_text("def external() -> int:\n    return 1\n", encoding="utf-8")
    config_path = root / "pyproject.toml"
    config = config_path.read_text(encoding="utf-8")
    if configured_kind == "source":
        configured = root / "src/agentic_investment_os/portfolio/__init__.py"
        configured.unlink()
        configured.symlink_to(external)
    else:
        source = root / "src/agentic_investment_os/portfolio/__init__.py"
        source.write_text("def size_position() -> int:\n    return 1\n", encoding="utf-8")
        test = root / "tests/unit/test_portfolio.py"
        test.symlink_to(external)
        config = config.replace(
            "pytest_add_cli_args_test_selection = []",
            'pytest_add_cli_args_test_selection = ["tests/unit/test_portfolio.py"]',
        )
        config_path.write_text(config, encoding="utf-8")
    caplog.set_level(logging.ERROR)

    exit_code = run_mutation.main(("--root", str(root)))

    assert exit_code == INVALID_CONFIGURATION_EXIT
    assert "must not traverse symbolic links" in caplog.text


def test_workflow_runs_only_for_manual_dispatch_or_labeled_pull_requests() -> None:
    workflow = MUTATION_WORKFLOW.read_text(encoding="utf-8")
    trigger = _top_level_block(workflow, "on:", "concurrency:")
    job = _top_level_block(workflow, "jobs:", None)

    assert "schedule:" not in trigger
    assert "paths:" not in trigger
    assert "types: [opened, labeled, synchronize, reopened]" in trigger
    assert "workflow_dispatch:" in trigger
    assert "github.event_name == 'workflow_dispatch'" in job
    assert "contains(github.event.pull_request.labels.*.name, 'mutation:critical')" in job


def test_obsolete_mutation_suppressions_are_absent() -> None:
    source_root = REPOSITORY_ROOT / "src/agentic_investment_os"

    annotated = [
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in source_root.rglob("*.py")
        if "# pragma: no mutate" in path.read_text(encoding="utf-8")
    ]

    assert annotated == []


def _mutation_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    for capability in ("execution", "portfolio"):
        package = root / f"src/agentic_investment_os/{capability}"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests/unit").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        """[tool.mutmut]
source_paths = ["src"]
only_mutate = [
  "src/agentic_investment_os/execution/__init__.py",
  "src/agentic_investment_os/portfolio/__init__.py",
]
pytest_add_cli_args = ["-o", "addopts=--strict-config --strict-markers -ra"]
pytest_add_cli_args_test_selection = []
mutate_only_covered_lines = false

[tool.mutation_gate]
schema_version = 1
authority_roots = [
  "src/agentic_investment_os/execution",
  "src/agentic_investment_os/portfolio",
]
noncritical_modules = []
""",
        encoding="utf-8",
    )
    return root


def _counts(**overrides: int) -> dict[str, int]:
    counts = dict.fromkeys(("killed", "total", "skipped", *REJECTED_RESULT_KEYS), 0)
    counts.update(overrides)
    return counts


def _top_level_block(document: str, start: str, end: str | None) -> str:
    start_index = document.index(start)
    end_index = len(document) if end is None else document.index(end, start_index)
    return document[start_index:end_index]
