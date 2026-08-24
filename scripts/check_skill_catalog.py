"""Validate project skill descriptions against repository context limits."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


MAX_SKILL_DESCRIPTION_CHARS = 320
MAX_CATALOG_DESCRIPTION_CHARS = 3_200
_FRONTMATTER_BOUNDARY = "---"
_YAML_NON_STRING_SCALARS = frozenset(
    {
        "~",
        "false",
        "no",
        "null",
        "off",
        "on",
        "true",
        "yes",
    }
)
_YAML_STRUCTURAL_PREFIXES = ("!", "&", "*", "[", "{", "|", ">", "@", "`", "%")
_YAML_INDICATOR_SCALAR = re.compile(r"^[-?:](?:\s|$)")
_YAML_NUMERIC_SCALAR = re.compile(
    r"^[+-]?(?:"
    r"(?:[0-9][0-9_]*)(?:\.[0-9_]*)?(?:e[+-]?[0-9_]+)?"
    r"|0x[0-9a-f_]+|0o[0-7_]+|0b[01_]+"
    r"|\.[0-9_]+(?:e[+-]?[0-9_]+)?|\.inf|\.nan"
    r")$",
    re.IGNORECASE,
)
_YAML_TIMESTAMP_SCALAR = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}(?:$|[Tt ])")


@dataclass(frozen=True, slots=True)
class SkillDescription:
    """Record one valid project skill description and its catalog cost."""

    name: str
    path: PurePosixPath
    text: str


@dataclass(frozen=True, slots=True)
class SkillCatalogViolation:
    """Identify one invalid project skill description contract."""

    path: PurePosixPath
    message: str


@dataclass(frozen=True, slots=True)
class SkillCatalogResult:
    """Return valid descriptions alongside every deterministic violation."""

    descriptions: tuple[SkillDescription, ...]
    violations: tuple[SkillCatalogViolation, ...]


def _relative(path: Path, root: Path) -> PurePosixPath:
    return PurePosixPath(path.relative_to(root).as_posix())


def _quoted_token(value: str) -> str | None:
    quote = value[0]
    index = 1
    while index < len(value):
        if quote == "'" and value[index : index + 2] == "''":
            index += 2
            continue
        if quote == '"' and value[index] == "\\":
            index += 2
            continue
        if value[index] == quote:
            suffix = value[index + 1 :].strip()
            return value[: index + 1] if not suffix or suffix.startswith("#") else None
        index += 1
    return None


def _decode_quoted_description(value: str) -> str | None:
    token = _quoted_token(value)
    if token is None:
        return None
    if token[0] == '"':
        try:
            decoded: object = json.loads(token)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, str) else None
    inner = token[1:-1]
    decoded = inner.replace("''", "'")
    if decoded.replace("'", "''") != inner:
        return None
    return decoded


def _decode_description(value: str) -> str | None:
    value = value.strip()
    if not value or value.startswith("#"):
        return None
    if value[0] in {'"', "'"}:
        return _decode_quoted_description(value)

    value = value.split(" #", maxsplit=1)[0].rstrip()
    if (
        not value
        or value.lower() in _YAML_NON_STRING_SCALARS
        or value.startswith(_YAML_STRUCTURAL_PREFIXES)
        or _YAML_INDICATOR_SCALAR.match(value)
        or _YAML_NUMERIC_SCALAR.match(value)
        or _YAML_TIMESTAMP_SCALAR.match(value)
        or ": " in value
    ):
        return None
    return value


def _parse_description(
    lines: Sequence[str],
    *,
    name: str,
    relative_path: PurePosixPath,
) -> tuple[SkillDescription | None, tuple[SkillCatalogViolation, ...]]:
    if not lines or lines[0] != _FRONTMATTER_BOUNDARY:
        violation = SkillCatalogViolation(relative_path, "YAML frontmatter is missing")
        return None, (violation,)
    try:
        end = lines.index(_FRONTMATTER_BOUNDARY, 1)
    except ValueError:
        violation = SkillCatalogViolation(relative_path, "YAML frontmatter is not closed")
        return None, (violation,)

    description_lines = [
        (index, line)
        for index, line in enumerate(lines[1:end], start=1)
        if line == "description:" or line.startswith("description: ")
    ]
    if not description_lines:
        violation = SkillCatalogViolation(relative_path, "description is missing")
        return None, (violation,)
    if len(description_lines) != 1:
        violation = SkillCatalogViolation(relative_path, "description must appear exactly once")
        return None, (violation,)

    description_index, description_line = description_lines[0]
    has_continuation = False
    for subsequent_line in lines[description_index + 1 : end]:
        if not subsequent_line.strip() or subsequent_line.lstrip().startswith("#"):
            continue
        has_continuation = subsequent_line.startswith((" ", "\t"))
        break
    description = _decode_description(description_line.removeprefix("description:"))
    if description is None or not description or "\n" in description or has_continuation:
        violation = SkillCatalogViolation(
            relative_path,
            "description must be a non-empty single-line string scalar",
        )
        return None, (violation,)

    entry = SkillDescription(
        name=name,
        path=relative_path,
        text=description,
    )
    violations: tuple[SkillCatalogViolation, ...] = ()
    if len(description) > MAX_SKILL_DESCRIPTION_CHARS:
        violation = SkillCatalogViolation(
            relative_path,
            (
                f"description has {len(description)} characters; "
                f"maximum is {MAX_SKILL_DESCRIPTION_CHARS}"
            ),
        )
        violations = (violation,)
    return entry, violations


def _read_description(
    skill_path: Path,
    *,
    root: Path,
) -> tuple[SkillDescription | None, tuple[SkillCatalogViolation, ...]]:
    relative_path = _relative(skill_path, root)
    try:
        lines = skill_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        violation = SkillCatalogViolation(relative_path, f"cannot read UTF-8 content: {error}")
        return None, (violation,)
    return _parse_description(
        lines,
        name=skill_path.parent.name,
        relative_path=relative_path,
    )


def check_skill_catalog(root: Path) -> SkillCatalogResult:
    """Inspect every project skill description in stable path order."""
    skills_root = root / ".agents" / "skills"
    catalog_path = PurePosixPath(".agents/skills")
    if not skills_root.is_dir():
        violation = SkillCatalogViolation(catalog_path, "skill catalog directory is missing")
        return SkillCatalogResult((), (violation,))

    skill_directories = sorted(path for path in skills_root.iterdir() if path.is_dir())
    if not skill_directories:
        violation = SkillCatalogViolation(catalog_path, "skill catalog contains no skills")
        return SkillCatalogResult((), (violation,))

    descriptions: list[SkillDescription] = []
    violations: list[SkillCatalogViolation] = []
    for skill_directory in skill_directories:
        skill_path = skill_directory / "SKILL.md"
        if not skill_path.is_file():
            violations.append(
                SkillCatalogViolation(
                    _relative(skill_path, root),
                    "skill entrypoint is missing",
                )
            )
            continue
        description, entry_violations = _read_description(skill_path, root=root)
        if description is not None:
            descriptions.append(description)
        violations.extend(entry_violations)

    total = sum(len(description.text) for description in descriptions)
    if total > MAX_CATALOG_DESCRIPTION_CHARS:
        contributors = "\n".join(
            f"  {description.name}: {len(description.text)} characters"
            for description in descriptions
        )
        violations.append(
            SkillCatalogViolation(
                catalog_path,
                (
                    f"descriptions total {total} characters; "
                    f"maximum is {MAX_CATALOG_DESCRIPTION_CHARS}\n{contributors}"
                ),
            )
        )

    return SkillCatalogResult(tuple(descriptions), tuple(violations))


def main(argv: Sequence[str] | None = None) -> int:
    """Report catalog violations and return a nonzero status when any exist."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    result = check_skill_catalog(arguments.root)
    for violation in result.violations:
        sys.stderr.write(f"{violation.path}: {violation.message}\n")
    if result.violations:
        return 1

    total = sum(len(description.text) for description in result.descriptions)
    longest = max(len(description.text) for description in result.descriptions)
    sys.stdout.write(
        f"skill catalog passed: {len(result.descriptions)} descriptions, "
        f"{total}/{MAX_CATALOG_DESCRIPTION_CHARS} characters, "
        f"max {longest}/{MAX_SKILL_DESCRIPTION_CHARS}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
