from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    REPOSITORY_ROOT / "scripts" / "install-scheduler-launch-agent.sh",
    REPOSITORY_ROOT / "scripts" / "uninstall-scheduler-launch-agent.sh",
)


def test_scheduler_launch_agent_helpers_are_executable_valid_shell() -> None:
    for script in SCRIPTS:
        assert script.stat().st_mode & 0o111
        completed = subprocess.run(  # noqa: S603
            ("/bin/sh", "-n", str(script)),
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


def test_tracked_launch_agent_helper_embeds_no_runtime_authority() -> None:
    installer = SCRIPTS[0].read_text()

    assert "EnvironmentVariables" not in installer
    assert "WorkingDirectory" not in installer
    assert "credential" not in installer.lower()
    assert "account" not in installer.lower()
    assert str(REPOSITORY_ROOT) not in installer


def test_installer_refuses_runner_not_owned_or_safely_writable_by_operator(
    tmp_path: Path,
) -> None:
    runner = tmp_path / "runner"
    runner.write_text("#!/bin/sh\nexit 0\n")
    runner.chmod(0o755)
    commands = tmp_path / "commands"
    commands.mkdir()
    _write_command(commands / "uname", "printf 'Darwin\\n'\n")
    _write_command(commands / "id", "printf '501\\n'\n")
    _write_command(
        commands / "stat",
        """
case "$2" in
    %u) printf '%s\\n' "$TEST_RUNNER_OWNER" ;;
    %Lp) printf '%s\\n' "$TEST_RUNNER_MODE" ;;
    *) exit 2 ;;
esac
""",
    )
    base_environment = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "PATH": f"{commands}:{os.defpath}",
    }

    for owner, mode, expected in (
        ("502", "755", "owned by the installing operator"),
        ("501", "775", "must not be writable"),
    ):
        completed = subprocess.run(  # noqa: S603
            ("/bin/sh", str(SCRIPTS[0]), str(runner)),
            cwd=REPOSITORY_ROOT,
            env={
                **base_environment,
                "TEST_RUNNER_OWNER": owner,
                "TEST_RUNNER_MODE": mode,
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0
        assert expected in completed.stderr


def test_uninstaller_retains_plist_when_loaded_agent_cannot_be_unloaded(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    agent = (
        home / "Library" / "LaunchAgents" / ("ai.investlink.agentic-investment-os.scheduler.plist")
    )
    agent.parent.mkdir(parents=True)
    agent.write_text("trusted launch agent")
    commands = tmp_path / "commands"
    commands.mkdir()
    _write_command(commands / "uname", "printf 'Darwin\\n'\n")
    _write_command(commands / "id", "printf '501\\n'\n")
    _write_command(
        commands / "launchctl",
        """
case "$1" in
    print) printf 'ai.investlink.agentic-investment-os.scheduler\\n' ;;
    bootout) exit 1 ;;
    *) exit 2 ;;
esac
""",
    )

    completed = subprocess.run(  # noqa: S603
        ("/bin/sh", str(SCRIPTS[1])),
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "HOME": str(home), "PATH": f"{commands}:{os.defpath}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "plist was retained" in completed.stderr
    assert agent.read_text() == "trusted launch agent"


def test_uninstaller_removes_an_unloaded_agent_after_inspecting_the_domain(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    agent = (
        home / "Library" / "LaunchAgents" / ("ai.investlink.agentic-investment-os.scheduler.plist")
    )
    agent.parent.mkdir(parents=True)
    agent.write_text("trusted launch agent")
    commands = tmp_path / "commands"
    commands.mkdir()
    _write_command(commands / "uname", "printf 'Darwin\\n'\n")
    _write_command(commands / "id", "printf '501\\n'\n")
    _write_command(commands / "launchctl", "printf 'unrelated-service\\n'\n")

    completed = subprocess.run(  # noqa: S603
        ("/bin/sh", str(SCRIPTS[1])),
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "HOME": str(home), "PATH": f"{commands}:{os.defpath}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not agent.exists()


def _write_command(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}")
    path.chmod(0o755)
