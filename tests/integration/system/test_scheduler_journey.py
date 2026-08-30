from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PROCESS_MODULE = "tests.integration.system._scheduler_process"
SHA256_HEX_LENGTH = 64
COMPLETED_LIFECYCLE_EVENT_COUNT = 11


def _run(state_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        (sys.executable, "-m", PROCESS_MODULE, str(state_root)),
        cwd=REPOSITORY_ROOT,
        env={"LC_ALL": "C", "PATH": os.defpath, "PYTHONHASHSEED": "0"},
        capture_output=True,
        text=True,
        check=False,
    )


def _scheduler_rows(database: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT * FROM scheduler_events ORDER BY cycle, sequence"
        ).fetchall()


def _lifecycle_rows(database: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT * FROM lifecycle_events ORDER BY stream_id, sequence"
        ).fetchall()


def test_scheduler_invokes_and_replays_public_lifecycle_across_processes(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"

    first = _run(state_root)
    assert first.returncode == 0, first.stderr
    fields = first.stdout.split("\t")
    assert len(fields[0]) == SHA256_HEX_LENGTH
    assert fields[1] == "completed"
    assert fields[2] == "2026-08-24T13:15:00.000000+00:00"
    assert fields[3] == "2026-08-25T13:15:00.000000+00:00"
    assert fields[4] == "true"

    scheduler_database = state_root / "scheduler.sqlite3"
    lifecycle_database = state_root / "lifecycle.sqlite3"
    scheduler_rows = _scheduler_rows(scheduler_database)
    lifecycle_rows = _lifecycle_rows(lifecycle_database)
    assert [row[2] for row in scheduler_rows] == ["started", "completed"]
    assert len(lifecycle_rows) == COMPLETED_LIFECYCLE_EVENT_COUNT

    replay = _run(state_root)
    assert replay.returncode == 0, replay.stderr
    assert replay.stdout == first.stdout
    assert _scheduler_rows(scheduler_database) == scheduler_rows
    assert _lifecycle_rows(lifecycle_database) == lifecycle_rows
    assert sorted(path.name for path in state_root.iterdir()) == [
        "evidence-vault",
        "lifecycle.sqlite3",
        "scheduler.lock",
        "scheduler.sqlite3",
    ]
