"""Validate tracked repository-local Markdown links and heading fragments."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import unquote

if TYPE_CHECKING:
    from collections.abc import Sequence


MAX_DIAGNOSTICS = 20
_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}[\t ]+(.+?)[\t ]*$")
_CLOSING_HEADING_MARKS = re.compile(r"[\t ]+#+[\t ]*$")
_EXTERNAL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\n]*)\)")
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_MARKDOWN_LINK_LABEL = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[^>]*>")
_GITHUB_PUNCTUATION = re.compile(r"[^\w\s-]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class MarkdownLinkViolation:
    """Identify one invalid local Markdown reference without echoing source text."""

    path: PurePosixPath
    line: int
    column: int
    message: str


@dataclass(frozen=True, slots=True)
class MarkdownLinkResult:
    """Summarize deterministic local-reference inspection."""

    markdown_file_count: int
    local_reference_count: int
    violations: tuple[MarkdownLinkViolation, ...]


@dataclass(frozen=True, slots=True)
class _LocalReference:
    """Retain the source location and destination of one supported local link."""

    line: int
    column: int
    destination: str


@dataclass(frozen=True, slots=True)
class _ParsedDestination:
    """Hold one normalized repository target and optional heading fragment."""

    target: PurePosixPath
    fragment: str | None


def _mask_inline_code(line: str) -> str:
    masked = list(line)
    cursor = 0
    while cursor < len(line):
        if line[cursor] != "`":
            cursor += 1
            continue
        marker_end = cursor
        while marker_end < len(line) and line[marker_end] == "`":
            marker_end += 1
        marker = line[cursor:marker_end]
        closing = line.find(marker, marker_end)
        if closing == -1:
            cursor = marker_end
            continue
        closing_end = closing + len(marker)
        masked[cursor:closing_end] = " " * (closing_end - cursor)
        cursor = closing_end
    return "".join(masked)


def _blank_except_line_ending(line: str) -> str:
    return "".join(character if character in "\r\n" else " " for character in line)


def _mask_fenced_code(text: str) -> str:
    masked_lines: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        fence = _FENCE.match(content)
        if fence_character is None:
            if fence is None:
                masked_lines.append(line)
                continue
            marker = fence.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
        elif fence is not None:
            marker = fence.group(1)
            trailing = fence.group(2)
            if (
                marker[0] == fence_character
                and len(marker) >= fence_length
                and not trailing.strip()
            ):
                fence_character = None
                fence_length = 0
        masked_lines.append(_blank_except_line_ending(line))
    return "".join(masked_lines)


def _supported_references(text: str) -> tuple[_LocalReference, ...]:
    references: list[_LocalReference] = []
    fence_masked = _mask_fenced_code(text)
    visible_text = "".join(
        _mask_inline_code(line) for line in fence_masked.splitlines(keepends=True)
    )
    for match in _INLINE_LINK.finditer(visible_text):
        destination = match.group(1).strip()
        if destination.startswith("<") and destination.endswith(">"):
            destination = destination[1:-1]
        if _EXTERNAL_SCHEME.match(destination) or destination.startswith("//"):
            continue
        destination_start = match.start(1)
        previous_newline = visible_text.rfind("\n", 0, destination_start)
        references.append(
            _LocalReference(
                line=visible_text.count("\n", 0, destination_start) + 1,
                column=destination_start - previous_newline,
                destination=destination,
            )
        )
    return tuple(references)


def _github_slug(heading: str) -> str:
    without_closing_marks = _CLOSING_HEADING_MARKS.sub("", heading)
    without_links = _MARKDOWN_LINK_LABEL.sub(r"\1", without_closing_marks)
    without_code_marks = without_links.replace("`", "")
    without_html = _HTML_TAG.sub("", without_code_marks)
    without_punctuation = _GITHUB_PUNCTUATION.sub("", without_html.lower())
    return _WHITESPACE.sub("-", without_punctuation.strip())


def _heading_fragments(text: str) -> frozenset[str]:
    fragments: set[str] = set()
    occurrences: dict[str, int] = {}
    for line in _mask_fenced_code(text).splitlines():
        heading = _ATX_HEADING.match(line)
        if heading is None:
            continue
        base = _github_slug(heading.group(1))
        suffix = occurrences.get(base, 0)
        fragment = base if suffix == 0 else f"{base}-{suffix}"
        while fragment in fragments:
            suffix += 1
            fragment = f"{base}-{suffix}"
        occurrences[base] = suffix + 1
        fragments.add(fragment)
    return frozenset(fragments)


def _resolve_target(
    source: PurePosixPath,
    raw_path: str,
) -> PurePosixPath | None:
    parts: list[str] = []
    for part in (*source.parent.parts, *PurePosixPath(raw_path).parts):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return PurePosixPath(*parts)


def _resolved_repository_file(
    root: Path,
    relative_path: PurePosixPath,
) -> tuple[Path | None, str | None]:
    try:
        resolved = (root / relative_path).resolve(strict=True)
    except OSError:
        return None, "missing"
    if not resolved.is_relative_to(root):
        return None, "outside"
    if not resolved.is_file():
        return None, "missing"
    return resolved, None


def _violation(
    source: PurePosixPath,
    reference: _LocalReference,
    message: str,
) -> MarkdownLinkViolation:
    return MarkdownLinkViolation(
        path=source,
        line=reference.line,
        column=reference.column,
        message=message,
    )


def _destination_syntax_error(destination: str) -> str | None:
    message: str | None = None
    raw_path, separator, raw_fragment = destination.partition("#")
    if not destination:
        message = "local reference has an empty destination"
    elif any(character.isspace() for character in destination):
        message = "local reference destinations must not contain whitespace"
    elif _INVALID_PERCENT_ESCAPE.search(destination):
        message = "local reference contains malformed percent encoding"
    elif destination.startswith("/"):
        message = f"target must be repository-relative: {destination}"
    elif separator and not raw_fragment:
        message = "local reference has an empty heading fragment"
    elif "?" in raw_path:
        message = "local reference query parameters are unsupported"
    return message


def _parse_destination(
    source: PurePosixPath,
    reference: _LocalReference,
) -> tuple[_ParsedDestination | None, MarkdownLinkViolation | None]:
    destination = reference.destination
    syntax_error = _destination_syntax_error(destination)
    if syntax_error is not None:
        return None, _violation(source, reference, syntax_error)

    raw_path, separator, raw_fragment = destination.partition("#")
    decoded_path = unquote(raw_path)
    target = source if not decoded_path else _resolve_target(source, decoded_path)
    if target is None:
        return None, _violation(
            source,
            reference,
            f"target escapes the repository root: {destination}",
        )
    fragment = unquote(raw_fragment) if separator else None
    return _ParsedDestination(target, fragment), None


def _target_path_or_violation(
    *,
    root: Path,
    source: PurePosixPath,
    reference: _LocalReference,
    parsed: _ParsedDestination,
    tracked_paths: frozenset[PurePosixPath],
) -> tuple[Path | None, MarkdownLinkViolation | None]:
    message: str | None = None
    resolved_target: Path | None = None
    if parsed.target not in tracked_paths:
        message = f"target is not a tracked repository file: {parsed.target}"
    else:
        resolved_target, resolution = _resolved_repository_file(root, parsed.target)
        if resolution == "outside":
            message = f"target resolves outside the repository: {parsed.target}"
        elif resolution == "missing":
            message = f"tracked target is missing from the worktree: {parsed.target}"
        elif parsed.fragment is not None and parsed.target.suffix.lower() != ".md":
            message = f"heading fragments require a Markdown target: {parsed.target}"
    if message is None:
        return resolved_target, None
    return None, _violation(source, reference, message)


def _validate_reference(
    *,
    root: Path,
    source: PurePosixPath,
    reference: _LocalReference,
    tracked_paths: frozenset[PurePosixPath],
    markdown_content: dict[PurePosixPath, str],
) -> MarkdownLinkViolation | None:
    parsed, destination_violation = _parse_destination(source, reference)
    if parsed is None:
        return destination_violation
    resolved_target, target_violation = _target_path_or_violation(
        root=root,
        source=source,
        reference=reference,
        parsed=parsed,
        tracked_paths=tracked_paths,
    )
    if target_violation is not None:
        return target_violation
    if resolved_target is None:
        message = "validated tracked target must resolve to a repository file"
        raise AssertionError(message)
    if parsed.fragment is None:
        return None

    content = markdown_content.get(parsed.target)
    if content is None:
        try:
            content = resolved_target.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return _violation(
                source,
                reference,
                f"cannot read tracked Markdown target as UTF-8: {parsed.target}",
            )
        markdown_content[parsed.target] = content
    if parsed.fragment not in _heading_fragments(content):
        return _violation(
            source,
            reference,
            f"heading fragment does not exist: {parsed.fragment}",
        )
    return None


def check_markdown_links(
    root: Path,
    tracked_paths: Sequence[PurePosixPath],
) -> MarkdownLinkResult:
    """Check supported links in every tracked Markdown file under ``root``."""
    resolved_root = root.resolve()
    normalized_paths = frozenset(PurePosixPath(path) for path in tracked_paths)
    markdown_paths = tuple(
        sorted(path for path in normalized_paths if path.suffix.lower() == ".md")
    )
    violations: list[MarkdownLinkViolation] = []
    local_reference_count = 0
    markdown_content: dict[PurePosixPath, str] = {}
    for source in markdown_paths:
        resolved_source, resolution = _resolved_repository_file(resolved_root, source)
        if resolution == "outside":
            violations.append(
                MarkdownLinkViolation(
                    source,
                    1,
                    1,
                    f"tracked Markdown source resolves outside repository: {source}",
                )
            )
            continue
        if resolved_source is None:
            violations.append(
                MarkdownLinkViolation(
                    source,
                    1,
                    1,
                    "cannot read tracked Markdown source as UTF-8",
                )
            )
            continue
        try:
            content = resolved_source.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            violations.append(
                MarkdownLinkViolation(
                    source,
                    1,
                    1,
                    "cannot read tracked Markdown source as UTF-8",
                )
            )
            continue
        markdown_content[source] = content
        references = _supported_references(content)
        local_reference_count += len(references)
        for reference in references:
            violation = _validate_reference(
                root=resolved_root,
                source=source,
                reference=reference,
                tracked_paths=normalized_paths,
                markdown_content=markdown_content,
            )
            if violation is not None:
                violations.append(violation)

    return MarkdownLinkResult(
        markdown_file_count=len(markdown_paths),
        local_reference_count=local_reference_count,
        violations=tuple(violations),
    )


def _git_tracked_paths(root: Path) -> tuple[PurePosixPath, ...] | None:
    git_executable = shutil.which("git")
    if git_executable is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 - fixed executable and arguments, no shell
            (git_executable, "-C", str(root), "ls-files", "-z"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    try:
        decoded = completed.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return tuple(PurePosixPath(path) for path in decoded.split("\0") if path)


def main(
    argv: Sequence[str] | None = None,
    *,
    tracked_paths: Sequence[PurePosixPath] | None = None,
) -> int:
    """Report bounded violations and return nonzero when local references fail."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    root = arguments.root.resolve()
    selected_paths = tracked_paths if tracked_paths is not None else _git_tracked_paths(root)
    if selected_paths is None:
        sys.stderr.write(".: cannot enumerate tracked repository files with git\n")
        return 1

    result = check_markdown_links(root, selected_paths)
    for violation in result.violations[:MAX_DIAGNOSTICS]:
        sys.stderr.write(
            f"{violation.path}:{violation.line}:{violation.column}: {violation.message}\n"
        )
    omitted = len(result.violations) - MAX_DIAGNOSTICS
    if omitted > 0:
        sys.stderr.write(f"{omitted} additional Markdown link violations omitted\n")
    if result.violations:
        return 1

    sys.stdout.write(
        f"Markdown links passed: {result.markdown_file_count} tracked Markdown files, "
        f"{result.local_reference_count} local references\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
