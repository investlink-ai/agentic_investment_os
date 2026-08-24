from __future__ import annotations

from pathlib import Path

import pytest
from scripts.check_skill_catalog import (
    MAX_CATALOG_DESCRIPTION_CHARS,
    MAX_SKILL_DESCRIPTION_CHARS,
    main,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _write_skill(root: Path, name: str, description: str | None) -> None:
    skill = root / ".agents" / "skills" / name
    skill.mkdir(parents=True)
    description_line = "" if description is None else f"description: {description}\n"
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\n{description_line}---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_repository_skill_catalog_passes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected_count = len(list((REPOSITORY_ROOT / ".agents" / "skills").glob("*/SKILL.md")))

    exit_code = main(("--root", str(REPOSITORY_ROOT)))

    assert exit_code == 0
    assert capsys.readouterr().out.startswith(
        f"skill catalog passed: {expected_count} descriptions, "
    )


def test_exact_individual_and_aggregate_limits_pass(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skill_count = MAX_CATALOG_DESCRIPTION_CHARS // MAX_SKILL_DESCRIPTION_CHARS
    assert skill_count * MAX_SKILL_DESCRIPTION_CHARS == MAX_CATALOG_DESCRIPTION_CHARS
    for index in range(skill_count):
        _write_skill(tmp_path, f"skill-{index}", "x" * MAX_SKILL_DESCRIPTION_CHARS)

    exit_code = main(("--root", str(tmp_path)))

    assert exit_code == 0
    assert capsys.readouterr().out == (
        f"skill catalog passed: {skill_count} descriptions, "
        f"{MAX_CATALOG_DESCRIPTION_CHARS}/{MAX_CATALOG_DESCRIPTION_CHARS} characters, "
        f"max {MAX_SKILL_DESCRIPTION_CHARS}/{MAX_SKILL_DESCRIPTION_CHARS}\n"
    )


@pytest.mark.parametrize(
    ("description_line", "expected"),
    [
        (None, "description is missing"),
        ("", "description must be a non-empty single-line string scalar"),
        ("# comment only", "description must be a non-empty single-line string scalar"),
        ("|", "description must be a non-empty single-line string scalar"),
        ("> # folded text", "description must be a non-empty single-line string scalar"),
        ("[unterminated", "description must be a non-empty single-line string scalar"),
        ("- item", "description must be a non-empty single-line string scalar"),
        ("? item", "description must be a non-empty single-line string scalar"),
        ("@invalid", "description must be a non-empty single-line string scalar"),
        ("null", "description must be a non-empty single-line string scalar"),
        ("[]", "description must be a non-empty single-line string scalar"),
    ],
)
def test_missing_or_malformed_description_is_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    description_line: str | None,
    expected: str,
) -> None:
    _write_skill(tmp_path, "broken-skill", description_line)

    exit_code = main(("--root", str(tmp_path)))

    assert exit_code == 1
    assert capsys.readouterr().err == (f".agents/skills/broken-skill/SKILL.md: {expected}\n")


@pytest.mark.parametrize("separator", ["", "\n"])
def test_multiline_description_continuation_is_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    separator: str,
) -> None:
    _write_skill(tmp_path, "multiline-skill", "short")
    path = tmp_path / ".agents" / "skills" / "multiline-skill" / "SKILL.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "description: short\n",
            f"description: short\n{separator}  {'x' * 400}\n",
        ),
        encoding="utf-8",
    )

    exit_code = main(("--root", str(tmp_path)))

    assert exit_code == 1
    assert capsys.readouterr().err == (
        ".agents/skills/multiline-skill/SKILL.md: "
        "description must be a non-empty single-line string scalar\n"
    )


def test_quoted_description_is_measured_without_yaml_quotes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_skill(
        tmp_path,
        "quoted-skill",
        f"'{'x' * MAX_SKILL_DESCRIPTION_CHARS}' # catalog comment",
    )

    exit_code = main(("--root", str(tmp_path)))

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "skill catalog passed: 1 descriptions, 320/3200 characters, max 320/320\n"
    )


def test_digit_leading_plain_description_remains_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_skill(tmp_path, "numbered-skill", "12 skills route reviews")

    exit_code = main(("--root", str(tmp_path)))

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "skill catalog passed: 1 descriptions, 23/3200 characters, max 23/320\n"
    )


def test_duplicate_description_is_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_skill(tmp_path, "duplicate-skill", "first")
    path = tmp_path / ".agents" / "skills" / "duplicate-skill" / "SKILL.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "description: first\n",
            "description: first\ndescription: second\n",
        ),
        encoding="utf-8",
    )

    exit_code = main(("--root", str(tmp_path)))

    assert exit_code == 1
    assert capsys.readouterr().err == (
        ".agents/skills/duplicate-skill/SKILL.md: description must appear exactly once\n"
    )


def test_individual_description_over_budget_names_the_skill(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_skill(tmp_path, "verbose-skill", "x" * (MAX_SKILL_DESCRIPTION_CHARS + 1))

    exit_code = main(("--root", str(tmp_path)))

    assert exit_code == 1
    assert capsys.readouterr().err == (
        ".agents/skills/verbose-skill/SKILL.md: description has 321 characters; maximum is 320\n"
    )


def test_aggregate_description_over_budget_lists_every_contributor(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for index in range(11):
        _write_skill(tmp_path, f"skill-{index:02}", "x" * 300)

    exit_code = main(("--root", str(tmp_path)))

    assert exit_code == 1
    error = capsys.readouterr().err
    assert error.startswith(".agents/skills: descriptions total 3300 characters; maximum is 3200\n")
    for index in range(11):
        assert f"  skill-{index:02}: 300 characters\n" in error
