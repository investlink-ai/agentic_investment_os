from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
START_ISSUE = ROOT / "scripts" / "start-issue.sh"
PRE_COMMIT = ROOT / ".githooks" / "pre-commit"
PRE_PUSH = ROOT / ".githooks" / "pre-push"


def _required_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        message = f"required test executable is unavailable: {name}"
        raise RuntimeError(message)
    return executable


GIT = _required_executable("git")


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command,
        cwd=cwd,
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def _must_run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = _run(command, cwd=cwd, env=env)
    assert completed.returncode == 0, completed.stderr
    return completed


def _git(cwd: Path, *arguments: str) -> str:
    return _must_run([GIT, *arguments], cwd=cwd).stdout.strip()


@dataclass(frozen=True)
class IssueRepository:
    control: Path
    origin: Path
    environment: dict[str, str]
    make_log: Path


@pytest.fixture
def issue_repository(tmp_path: Path) -> IssueRepository:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    control = tmp_path / "control"
    fake_bin = tmp_path / "bin"
    make_log = tmp_path / "make.log"

    _must_run([GIT, "init", "--bare", str(origin)], cwd=tmp_path)
    _must_run([GIT, "init", "-b", "dev", str(seed)], cwd=tmp_path)
    _git(seed, "config", "user.name", "Harness Test")
    _git(seed, "config", "user.email", "harness@example.invalid")
    (seed / ".agents" / "skills").mkdir(parents=True)
    (seed / ".agents" / "skills" / ".gitkeep").write_text("", encoding="utf-8")
    (seed / ".gitignore").write_text("/.agents/worktrees/\n", encoding="utf-8")
    (seed / "README.md").write_text("# Fixture\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "Initial fixture")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-u", "origin", "dev")
    _must_run(
        [GIT, f"--git-dir={origin}", "symbolic-ref", "HEAD", "refs/heads/dev"],
        cwd=tmp_path,
    )
    _must_run([GIT, "clone", str(origin), str(control)], cwd=tmp_path)
    _git(control, "config", "user.name", "Harness Test")
    _git(control, "config", "user.email", "harness@example.invalid")

    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            set -eu
            case "$1:$2" in
                auth:status) exit 0 ;;
                repo:view) printf '%s\\n' 'investlink-ai/fixture' ;;
                issue:view)
                    case " $* " in
                        *" .state "*) printf '%s\\n' "${FAKE_ISSUE_STATE:-OPEN}" ;;
                        *" .title "*) printf '%s\\n' "${FAKE_ISSUE_TITLE:-Guard the worktree}" ;;
                        *) exit 2 ;;
                    esac
                    ;;
                *) exit 2 ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    fake_make = fake_bin / "make"
    fake_make.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            set -eu
            printf '%s\\n' "$*" >> "$FAKE_MAKE_LOG"
            if [ "${FAKE_SETUP_FAIL:-0}" = "1" ]; then
                exit 9
            fi
            """
        ),
        encoding="utf-8",
    )
    fake_make.chmod(0o755)

    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            set -eu
            printf '%s\\n' "$*" >> "$FAKE_UV_LOG"
            """
        ),
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    environment["FAKE_MAKE_LOG"] = str(make_log)
    environment["FAKE_UV_LOG"] = str(tmp_path / "uv.log")
    return IssueRepository(
        control=control,
        origin=origin,
        environment=environment,
        make_log=make_log,
    )


def _start_issue(
    repository: IssueRepository,
    *,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return _run(
        [str(START_ISSUE), "42"],
        cwd=repository.control,
        env=environment or repository.environment,
    )


def test_start_issue_creates_untracked_branch_from_fresh_dev(
    issue_repository: IssueRepository,
) -> None:
    completed = _start_issue(issue_repository)

    assert completed.returncode == 0, completed.stderr
    worktree = issue_repository.control / ".agents" / "worktrees" / "42-guard-the-worktree"
    assert worktree.is_dir()
    assert _git(worktree, "branch", "--show-current") == "issue/42-guard-the-worktree"
    assert _git(worktree, "rev-parse", "HEAD") == _git(
        issue_repository.control, "rev-parse", "origin/dev"
    )
    assert (
        _git(
            worktree,
            "for-each-ref",
            "--format=%(upstream)",
            "refs/heads/issue/42-guard-the-worktree",
        )
        == ""
    )
    assert issue_repository.make_log.read_text(encoding="utf-8") == (f"-C {worktree} bootstrap\n")


def test_start_issue_reuses_registered_worktree_after_title_change(
    issue_repository: IssueRepository,
) -> None:
    first = _start_issue(issue_repository)
    assert first.returncode == 0, first.stderr
    changed_environment = issue_repository.environment | {"FAKE_ISSUE_TITLE": "Renamed issue"}

    second = _start_issue(issue_repository, environment=changed_environment)

    assert second.returncode == 0, second.stderr
    assert "Resuming issue #42" in second.stdout
    assert "42-guard-the-worktree" in second.stdout
    worktree = issue_repository.control / ".agents" / "worktrees" / "42-guard-the-worktree"
    assert issue_repository.make_log.read_text(encoding="utf-8") == (
        f"-C {worktree} bootstrap\n-C {worktree} bootstrap\n"
    )


@pytest.mark.parametrize(
    ("prepare", "expected_error"),
    [
        ("dirty", "must be clean"),
        ("wrong-branch", "must be on dev"),
        ("closed", "is not open"),
    ],
)
def test_start_issue_rejects_invalid_control_state(
    issue_repository: IssueRepository,
    prepare: str,
    expected_error: str,
) -> None:
    environment = issue_repository.environment
    if prepare == "dirty":
        (issue_repository.control / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    elif prepare == "wrong-branch":
        _git(issue_repository.control, "switch", "-c", "issue/99-other")
    else:
        environment = environment | {"FAKE_ISSUE_STATE": "CLOSED"}

    completed = _start_issue(issue_repository, environment=environment)

    assert completed.returncode != 0
    assert expected_error in completed.stderr


def test_start_issue_refuses_existing_remote_issue_branch(
    issue_repository: IssueRepository,
) -> None:
    _git(issue_repository.control, "branch", "issue/42-existing", "origin/dev")
    _git(issue_repository.control, "push", "origin", "issue/42-existing")
    _git(issue_repository.control, "branch", "-D", "issue/42-existing")

    completed = _start_issue(issue_repository)

    assert completed.returncode != 0
    assert "remote issue work already exists" in completed.stderr
    assert not (
        issue_repository.control / ".agents" / "worktrees" / "42-guard-the-worktree"
    ).exists()


def test_start_issue_preserves_worktree_when_bootstrap_fails(
    issue_repository: IssueRepository,
) -> None:
    environment = issue_repository.environment | {"FAKE_SETUP_FAIL": "1"}

    completed = _start_issue(issue_repository, environment=environment)

    worktree = issue_repository.control / ".agents" / "worktrees" / "42-guard-the-worktree"
    assert completed.returncode != 0
    assert "worktree preserved" in completed.stderr
    assert worktree.is_dir()
    assert _git(worktree, "branch", "--show-current") == "issue/42-guard-the-worktree"

    resumed = _start_issue(issue_repository)

    assert resumed.returncode == 0, resumed.stderr
    assert "Resuming issue #42" in resumed.stdout


def test_pre_commit_requires_linked_issue_branch(issue_repository: IssueRepository) -> None:
    rejected = _run(
        [str(PRE_COMMIT)],
        cwd=issue_repository.control,
        env=issue_repository.environment,
    )
    assert rejected.returncode != 0
    assert "not directly on dev" in rejected.stderr

    created = _start_issue(issue_repository)
    assert created.returncode == 0, created.stderr
    worktree = issue_repository.control / ".agents" / "worktrees" / "42-guard-the-worktree"

    accepted = _run([str(PRE_COMMIT)], cwd=worktree, env=issue_repository.environment)

    assert accepted.returncode == 0, accepted.stderr
    uv_log = Path(issue_repository.environment["FAKE_UV_LOG"]).read_text(encoding="utf-8")
    assert "run ruff format --check ." in uv_log
    assert "run ruff check ." in uv_log


@pytest.mark.parametrize(
    ("branch", "inside_agents_root", "expected_error"),
    [
        ("topic/no-issue", True, "branch must match"),
        ("issue/7-outside", False, "must be below .agents/worktrees"),
    ],
)
def test_pre_commit_rejects_invalid_linked_context(
    issue_repository: IssueRepository,
    branch: str,
    *,
    inside_agents_root: bool,
    expected_error: str,
) -> None:
    if inside_agents_root:
        worktree = issue_repository.control / ".agents" / "worktrees" / "invalid"
    else:
        worktree = issue_repository.control.parent / "outside-worktree"
    _git(
        issue_repository.control,
        "worktree",
        "add",
        "--no-track",
        "-b",
        branch,
        str(worktree),
        "origin/dev",
    )

    completed = _run([str(PRE_COMMIT)], cwd=worktree, env=issue_repository.environment)

    assert completed.returncode != 0
    assert expected_error in completed.stderr


def test_pre_push_rejects_protected_destination_before_checks(
    issue_repository: IssueRepository,
) -> None:
    completed = _run(
        [str(PRE_PUSH), "origin", str(issue_repository.origin)],
        cwd=issue_repository.control,
        env=issue_repository.environment,
        input_text=(
            "refs/heads/issue/42-guard 1111111111111111111111111111111111111111 "
            "refs/heads/dev 2222222222222222222222222222222222222222\n"
        ),
    )

    assert completed.returncode != 0
    assert "direct pushes to refs/heads/dev are prohibited" in completed.stderr
    assert not issue_repository.make_log.exists()


def test_pre_push_runs_gate_for_issue_destination(issue_repository: IssueRepository) -> None:
    completed = _run(
        [str(PRE_PUSH), "origin", str(issue_repository.origin)],
        cwd=issue_repository.control,
        env=issue_repository.environment,
        input_text=(
            "refs/heads/issue/42-guard 1111111111111111111111111111111111111111 "
            "refs/heads/issue/42-guard 0000000000000000000000000000000000000000\n"
        ),
    )

    assert completed.returncode == 0, completed.stderr
    assert issue_repository.make_log.read_text(encoding="utf-8") == "check\n"
