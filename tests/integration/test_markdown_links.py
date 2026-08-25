from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from scripts.check_markdown_links import MAX_DIAGNOSTICS, check_markdown_links, main

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_LINK_LINE = 3


def _write_markdown(root: Path, relative_path: str, content: str) -> PurePosixPath:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return PurePosixPath(relative_path)


def test_repository_markdown_links_pass(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(("--root", str(REPOSITORY_ROOT)))

    assert exit_code == 0
    output = capsys.readouterr().out
    assert output.startswith("Markdown links passed: ")
    assert " tracked Markdown files, " in output
    assert output.endswith(" local references\n")


def test_same_file_cross_file_and_parent_relative_fragments_pass(tmp_path: Path) -> None:
    tracked_paths = (
        _write_markdown(
            tmp_path,
            "README.md",
            "# Project\n\n[Local](#current-state)\n\n## Current state\n",
        ),
        _write_markdown(
            tmp_path,
            "docs/architecture.md",
            "# Architecture\n\n[Contract](contracts/lifecycle.md#public-api)\n",
        ),
        _write_markdown(
            tmp_path,
            "docs/contracts/lifecycle.md",
            "# Lifecycle\n\n## Public API\n\n[Architecture](../architecture.md#architecture)\n",
        ),
    )

    result = check_markdown_links(tmp_path, tracked_paths)

    assert result.markdown_file_count == len(tracked_paths)
    assert result.local_reference_count == len(tracked_paths)
    assert result.violations == ()


@pytest.mark.parametrize(
    ("fragment", "expected_message"),
    [
        ("foo-2", None),
        ("foo-3", "heading fragment does not exist: foo-3"),
    ],
)
def test_duplicate_github_heading_slugs_are_checked(
    tmp_path: Path,
    fragment: str,
    expected_message: str | None,
) -> None:
    tracked_paths = (
        _write_markdown(
            tmp_path,
            "README.md",
            f"# Project\n\n[Collision](guide.md#{fragment})\n",
        ),
        _write_markdown(
            tmp_path,
            "guide.md",
            "# Guide\n\n## Foo\n\n## Foo-1\n\n## Foo\n",
        ),
    )

    result = check_markdown_links(tmp_path, tracked_paths)

    messages = tuple(violation.message for violation in result.violations)
    assert messages == (() if expected_message is None else (expected_message,))


def test_internal_markdown_target_symlink_is_supported(tmp_path: Path) -> None:
    source = _write_markdown(
        tmp_path,
        "README.md",
        "# Project\n\n[Contract](guide.md#contract)\n",
    )
    target = _write_markdown(tmp_path, "docs/guide.md", "# Guide\n\n## Contract\n")
    (tmp_path / "guide.md").symlink_to("docs/guide.md")

    result = check_markdown_links(
        tmp_path,
        (source, target, PurePosixPath("guide.md")),
    )

    assert result.violations == ()


def test_escaping_markdown_source_symlink_fails(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    _write_markdown(tmp_path, "outside.md", "# Outside\n")
    outside = tmp_path / "outside.md"
    (root / "README.md").symlink_to(outside)

    result = check_markdown_links(root, (PurePosixPath("README.md"),))

    assert result.violations[0].message == (
        "tracked Markdown source resolves outside repository: README.md"
    )


def test_escaping_markdown_target_symlink_fails(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    source = _write_markdown(root, "README.md", "# Project\n\n[Outside](guide.md)\n")
    _write_markdown(tmp_path, "outside.md", "# Outside\n")
    outside = tmp_path / "outside.md"
    (root / "guide.md").symlink_to(outside)

    result = check_markdown_links(
        root,
        (source, PurePosixPath("guide.md")),
    )

    assert result.violations[0].message == ("target resolves outside the repository: guide.md")


def test_cyclic_markdown_source_symlink_has_bounded_diagnostic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "README.md").symlink_to("README.md")

    exit_code = main(
        ("--root", str(tmp_path)),
        tracked_paths=(PurePosixPath("README.md"),),
    )

    assert exit_code == 1
    diagnostic = capsys.readouterr().err
    assert diagnostic == (
        "README.md:1:1: tracked Markdown source cannot be resolved within repository: README.md\n"
    )
    assert str(tmp_path) not in diagnostic


def test_cyclic_markdown_target_symlink_has_bounded_diagnostic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _write_markdown(tmp_path, "README.md", "# Project\n\n[Cycle](guide.md)\n")
    (tmp_path / "guide.md").symlink_to("guide.md")

    exit_code = main(
        ("--root", str(tmp_path)),
        tracked_paths=(source, PurePosixPath("guide.md")),
    )

    assert exit_code == 1
    diagnostic = capsys.readouterr().err
    assert diagnostic == (
        "README.md:3:9: target cannot be resolved within repository: guide.md\n"
        "guide.md:1:1: tracked Markdown source cannot be resolved within repository: guide.md\n"
    )
    assert str(tmp_path) not in diagnostic


@pytest.mark.parametrize(
    ("fragment", "expected_message"),
    [
        ("current-state", None),
        ("stale-state", "heading fragment does not exist: stale-state"),
    ],
)
def test_multiline_link_labels_are_checked(
    tmp_path: Path,
    fragment: str,
    expected_message: str | None,
) -> None:
    tracked_paths = (
        _write_markdown(
            tmp_path,
            "README.md",
            f"# Project\n\n[Current\nstate](guide.md#{fragment})\n",
        ),
        _write_markdown(tmp_path, "guide.md", "# Guide\n\n## Current state\n"),
    )

    result = check_markdown_links(tmp_path, tracked_paths)

    assert result.local_reference_count == 1
    messages = tuple(violation.message for violation in result.violations)
    assert messages == (() if expected_message is None else (expected_message,))


def test_external_links_and_code_examples_are_not_checked(tmp_path: Path) -> None:
    tracked_paths = (
        _write_markdown(
            tmp_path,
            "README.md",
            """# Project

[Web](https://example.com/missing#anchor) and [mail](mailto:operator@example.com).

`[inline](missing.md)`

```markdown
[fenced](also-missing.md)
```
""",
        ),
    )

    result = check_markdown_links(tmp_path, tracked_paths)

    assert result.local_reference_count == 0
    assert result.violations == ()


def test_fence_with_info_text_does_not_close_an_existing_fence(tmp_path: Path) -> None:
    tracked_paths = (
        _write_markdown(
            tmp_path,
            "README.md",
            """# Project

```text
```python
[example](missing.md)
```
""",
        ),
    )

    result = check_markdown_links(tmp_path, tracked_paths)

    assert result.local_reference_count == 0
    assert result.violations == ()


@pytest.mark.parametrize(
    ("destination", "expected"),
    [
        ("missing.md", "target is not a tracked repository file: docs/missing.md"),
        ("guide.md#missing", "heading fragment does not exist: missing"),
        ("#missing", "heading fragment does not exist: missing"),
        ("guide.md#", "local reference has an empty heading fragment"),
        ("guide.md#bad%2", "local reference contains malformed percent encoding"),
        ("../../outside.md", "target escapes the repository root: ../../outside.md"),
        ("/absolute.md", "target must be repository-relative: /absolute.md"),
    ],
)
def test_missing_or_malformed_local_references_fail(
    tmp_path: Path,
    destination: str,
    expected: str,
) -> None:
    tracked_paths = (
        _write_markdown(
            tmp_path,
            "docs/source.md",
            f"# Source\n\n[Broken]({destination})\n",
        ),
        _write_markdown(tmp_path, "docs/guide.md", "# Guide\n"),
    )

    result = check_markdown_links(tmp_path, tracked_paths)

    assert len(result.violations) == 1
    violation = result.violations[0]
    assert violation.path == PurePosixPath("docs/source.md")
    assert violation.line == SOURCE_LINK_LINE
    assert violation.message == expected


def test_untracked_existing_target_fails(tmp_path: Path) -> None:
    source = _write_markdown(tmp_path, "README.md", "# Project\n\n[Hidden](hidden.md)\n")
    _write_markdown(tmp_path, "hidden.md", "# Hidden\n")

    result = check_markdown_links(tmp_path, (source,))

    assert result.violations[0].message == ("target is not a tracked repository file: hidden.md")


def test_non_markdown_tracked_target_is_checked_without_fragment_parsing(
    tmp_path: Path,
) -> None:
    source = _write_markdown(tmp_path, "README.md", "# Project\n\n[CI](.github/ci.yml)\n")
    target = tmp_path / ".github" / "ci.yml"
    target.parent.mkdir(parents=True)
    target.write_text("name: CI\n", encoding="utf-8")

    result = check_markdown_links(
        tmp_path,
        (source, PurePosixPath(".github/ci.yml")),
    )

    assert result.violations == ()


def test_diagnostics_are_bounded_and_repository_relative(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _write_markdown(
        tmp_path,
        "docs/source.md",
        "# Source\n\n" + "\n".join(f"[Missing {index}](missing-{index}.md)" for index in range(25)),
    )

    exit_code = main(
        ("--root", str(tmp_path)),
        tracked_paths=(source,),
    )

    assert exit_code == 1
    error_lines = capsys.readouterr().err.splitlines()
    assert len(error_lines) == MAX_DIAGNOSTICS + 1
    assert error_lines[0].startswith(f"docs/source.md:{SOURCE_LINK_LINE}:")
    assert str(tmp_path) not in "\n".join(error_lines)
    assert error_lines[-1] == "5 additional Markdown link violations omitted"
