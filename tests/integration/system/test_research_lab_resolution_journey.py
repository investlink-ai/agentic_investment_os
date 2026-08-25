from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PROCESS_MODULE = "tests.integration.system._lab_resolution_process"
AUTHORITY_SENTINEL_NAMES = frozenset(
    {
        "LAB_JOURNEY_BROKER_SENTINEL",
        "LAB_JOURNEY_CHAMPION_SENTINEL",
        "LAB_JOURNEY_CREDENTIAL_SENTINEL",
        "LAB_JOURNEY_PACKET_SENTINEL",
    }
)


def _run(action: str, lab_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        (sys.executable, "-m", PROCESS_MODULE, action, str(lab_root)),
        cwd=REPOSITORY_ROOT,
        env={"LC_ALL": "C", "PATH": os.defpath, "PYTHONHASHSEED": "0"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_research_lab_resolution_replays_across_processes_without_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lab_root = tmp_path / "lab"
    for name in AUTHORITY_SENTINEL_NAMES:
        monkeypatch.setenv(name, "must-not-cross-process-seam")

    resolved = _run("resolve", lab_root)
    replayed = _run("replay", lab_root)

    assert resolved.returncode == 0, resolved.stderr
    assert replayed.returncode == 0, replayed.stderr
    assert resolved.stdout.split("\t") == ["completed", "hold", "4", "4", "true"]
    assert replayed.stdout.split("\t") == ["replayed", "hold", "4", "0", "true"]
    assert not (tmp_path / "production").exists()
    with sqlite3.connect(lab_root / "research-lab.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM lab_call_intents").fetchone() == (4,)
        assert connection.execute("SELECT COUNT(*) FROM lab_call_observations").fetchone() == (4,)
