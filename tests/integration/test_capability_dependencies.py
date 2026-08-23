from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "capability_dependencies"
PACKAGE = Path("src/agentic_investment_os")
EXPECTED_TIMEZONE_VIOLATIONS = 3
EXPECTED_FILESYSTEM_VIOLATIONS = 8
EXPECTED_RANDOM_VIOLATIONS = 3
CATALOG_FIELDS = 2
CATALOG_ENTRY = re.compile(r'^"(?P<api>[^"]+)"\.msg = "(?P<code>CAP\d{3}) ')
CATALOG_DIAGNOSTIC = re.compile(r"catalog\.py:(?P<line>\d+):\d+: TID251 .*: (?P<code>CAP\d{3})")


def test_current_production_sources_have_explicit_capability_dependencies() -> None:
    result = _run_gate(REPOSITORY_ROOT)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "capability dependency check passed" in result.stdout


def test_catalog_rejects_ambient_effects_and_concrete_authority(tmp_path: Path) -> None:
    source_root = tmp_path / PACKAGE / "research"
    source_root.mkdir(parents=True)
    for fixture in sorted((FIXTURE_ROOT / "denied").glob("*.py.txt")):
        shutil.copyfile(fixture, source_root / fixture.name.removesuffix(".txt"))

    result = _run_gate(tmp_path)

    assert result.returncode == 1
    for code in ("CAP001", "CAP002", "CAP003", "CAP004", "CAP005"):
        assert code in result.stdout
    for code in ("CAP006", "CAP007", "CAP008", "CAP009", "CAP010"):
        assert code in result.stdout
    assert "research/wildcard.py" in result.stdout


def test_every_banned_api_catalog_entry_has_executable_negative_evidence(tmp_path: Path) -> None:
    configured_catalog = tuple(
        (match.group("api"), match.group("code"))
        for line in (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if (match := CATALOG_ENTRY.match(line)) is not None
    )
    expected_catalog = tuple(
        (fields[0], fields[1])
        for line in (FIXTURE_ROOT / "banned_api_catalog.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if len(fields := line.split()) == CATALOG_FIELDS
    )
    assert configured_catalog == expected_catalog

    source_lines: list[str] = []
    expected: dict[int, str] = {}
    for index, (api, code) in enumerate(expected_catalog):
        root, separator, remainder = api.partition(".")
        if separator:
            alias = f"dependency_{index}"
            source_lines.extend((f"import {root} as {alias}", f"{alias}.{remainder}"))
        else:
            source_lines.append(f"import {api}")
        expected[len(source_lines)] = code
    source = tmp_path / PACKAGE / "domain" / "catalog.py"
    source.parent.mkdir(parents=True)
    source.write_text("\n".join(source_lines), encoding="utf-8")

    result = _run_gate(tmp_path)

    observed = {
        int(match.group("line")): match.group("code")
        for match in CATALOG_DIAGNOSTIC.finditer(result.stdout)
    }
    assert expected_catalog
    assert result.returncode == 1
    assert observed == expected


def test_narrow_context_checks_reject_only_missing_deterministic_inputs(tmp_path: Path) -> None:
    denied_source = tmp_path / PACKAGE / "domain" / "denied.py"
    denied_source.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURE_ROOT / "denied" / "context_sensitive.py.txt", denied_source)

    result = _run_gate(tmp_path)

    assert result.returncode == 1
    assert result.stdout.count("CAP002") == EXPECTED_RANDOM_VIOLATIONS
    assert result.stdout.count("CAP004") == EXPECTED_TIMEZONE_VIOLATIONS
    assert result.stdout.count("CAP009") == EXPECTED_FILESYSTEM_VIOLATIONS
    assert "denied.py:9:" in result.stdout
    assert "denied.py:12:" in result.stdout
    assert "denied.py:13:" in result.stdout
    for subject in (
        "builtins.open",
        "datetime.datetime.astimezone",
        "datetime.datetime.fromtimestamp",
        "pathlib.Path.exists",
        "pathlib.Path.read_text",
        "pathlib.Path.write_text",
        "random.Random",
        "random.Random.seed",
    ):
        assert subject in result.stdout


def test_effect_bearing_mixed_module_wildcards_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / PACKAGE / "research" / "wildcard.py"
    source.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURE_ROOT / "denied" / "wildcard.py.txt", source)

    result = _run_gate(tmp_path)

    assert result.returncode == 1
    for subject in (
        "asyncio.*",
        "datetime.*",
        "os.*",
        "pathlib.*",
        "random.*",
        "time.*",
        "uuid.*",
    ):
        assert subject in result.stdout


def test_typed_values_and_explicit_deterministic_inputs_are_allowed(tmp_path: Path) -> None:
    source = tmp_path / PACKAGE / "new_capability" / "service.py"
    source.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURE_ROOT / "allowed" / "deterministic_seams.py.txt", source)

    result = _run_gate(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 protected source file" in result.stdout


def test_new_capabilities_are_protected_while_effect_owners_are_exempt(tmp_path: Path) -> None:
    denied = (FIXTURE_ROOT / "denied" / "ambient_values.py.txt").read_text(encoding="utf-8")
    for capability in ("new_capability", "adapters", "entrypoints"):
        source = tmp_path / PACKAGE / capability / "service.py"
        source.parent.mkdir(parents=True)
        source.write_text(denied, encoding="utf-8")

    result = _run_gate(tmp_path)

    assert result.returncode == 1
    assert "new_capability/service.py" in result.stdout
    assert "adapters/service.py" not in result.stdout
    assert "entrypoints/service.py" not in result.stdout


def test_inline_lint_suppression_cannot_bypass_the_gate(tmp_path: Path) -> None:
    source = tmp_path / PACKAGE / "domain" / "clock.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from datetime import datetime\n\ndatetime.now()  # noqa: TID251\n",
        encoding="utf-8",
    )

    result = _run_gate(tmp_path)

    assert result.returncode == 1
    assert "CAP001" in result.stdout


def test_diagnostics_are_repo_relative_and_do_not_echo_source(tmp_path: Path) -> None:
    source = tmp_path / PACKAGE / "domain" / "secret.py"
    source.parent.mkdir(parents=True)
    source.write_text('open("TOP_SECRET_VALUE")\n', encoding="utf-8")

    result = _run_gate(tmp_path)

    assert result.returncode == 1
    assert str(tmp_path) not in result.stdout
    assert "TOP_SECRET_VALUE" not in result.stdout
    assert "src/agentic_investment_os/domain/secret.py:1:" in result.stdout


def test_invalid_python_fails_closed_with_a_fixed_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / PACKAGE / "domain" / "invalid.py"
    source.parent.mkdir(parents=True)
    source.write_text("def unfinished(\n", encoding="utf-8")

    result = _run_gate(tmp_path)

    assert result.returncode == 1
    assert "CAP000 source: invalid Python source" in result.stdout
    assert "src/agentic_investment_os/domain/invalid.py:1:" in result.stdout


def test_non_utf8_python_fails_closed_with_a_fixed_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / PACKAGE / "domain" / "invalid_encoding.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\xff")

    result = _run_gate(tmp_path)

    assert result.returncode == 1
    assert result.stderr == ""
    assert "CAP000 source: invalid Python source" in result.stdout
    assert "src/agentic_investment_os/domain/invalid_encoding.py:1:" in result.stdout


def test_missing_lint_engine_fails_closed_with_a_fixed_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / PACKAGE / "domain" / "safe.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    fake_ruff = tmp_path / "ruff" / "__main__.py"
    fake_ruff.parent.mkdir()
    (fake_ruff.parent / "__init__.py").write_text("", encoding="utf-8")
    fake_ruff.write_text(
        'import sys\nsys.stderr.write("No module named ruff\\n")\nraise SystemExit(1)\n',
        encoding="utf-8",
    )

    result = _run_gate(tmp_path)

    assert result.returncode == 1
    assert result.stderr == ""
    assert "CAP000 capability lint unavailable" in result.stdout
    assert "No module named ruff" not in result.stdout


def _run_gate(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - executable and module are repository-controlled.
        [
            sys.executable,
            "-m",
            "scripts.check_capability_dependencies",
            "--root",
            str(root),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
