from __future__ import annotations

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
