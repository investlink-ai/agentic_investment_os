from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PROCESS_MODULE = "tests.integration.system._lifecycle_process"
INTERRUPTED_EXIT_CODE = 75
ADVANCE_FIELD_COUNT = 8
STATUS_FIELD_COUNT = 11
SHA256_HEX_LENGTH = 64
AUTHORITY_SENTINEL_NAMES = frozenset(
    {
        "SYSTEM_JOURNEY_BROKER_SENTINEL",
        "SYSTEM_JOURNEY_MODEL_SENTINEL",
        "SYSTEM_JOURNEY_MUTABLE_ACCOUNT_SENTINEL",
    }
)


@dataclass(frozen=True, slots=True)
class AdvanceObservation:
    disposition: str
    completed_phase: str
    recovery: str
    run_id: str
    configuration_version: int
    configuration_hash: str
    ambient_authority_absent: bool


@dataclass(frozen=True, slots=True)
class StatusObservation:
    active_phase: str
    last_completed_cycle: str
    universe_snapshot_cycle: str
    liveness: str
    durable_reason: str
    run_id: str
    configuration_version: int
    configuration_hash: str
    universe_snapshot_id: str
    ambient_authority_absent: bool


def _child_environment() -> dict[str, str]:
    return {
        "LC_ALL": "C",
        "PATH": os.defpath,
        "PYTHONHASHSEED": "0",
    }


def _run_process(action: str, state_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        (sys.executable, "-m", PROCESS_MODULE, action, str(state_root)),
        cwd=REPOSITORY_ROOT,
        env=_child_environment(),
        capture_output=True,
        text=True,
        check=False,
    )


def _advance_observation(completed: subprocess.CompletedProcess[str]) -> AdvanceObservation:
    assert completed.returncode == 0, completed.stderr
    fields = completed.stdout.split("\t")
    assert len(fields) == ADVANCE_FIELD_COUNT
    assert fields[0] == "advance"
    return AdvanceObservation(
        disposition=fields[1],
        completed_phase=fields[2],
        recovery=fields[3],
        run_id=fields[4],
        configuration_version=int(fields[5]),
        configuration_hash=fields[6],
        ambient_authority_absent=fields[7] == "true",
    )


def _status_observation(completed: subprocess.CompletedProcess[str]) -> StatusObservation:
    assert completed.returncode == 0, completed.stderr
    fields = completed.stdout.split("\t")
    assert len(fields) == STATUS_FIELD_COUNT
    assert fields[0] == "status"
    return StatusObservation(
        active_phase=fields[1],
        last_completed_cycle=fields[2],
        universe_snapshot_cycle=fields[3],
        liveness=fields[4],
        durable_reason=fields[5],
        run_id=fields[6],
        configuration_version=int(fields[7]),
        configuration_hash=fields[8],
        universe_snapshot_id=fields[9],
        ambient_authority_absent=fields[10] == "true",
    )


def _authoritative_history(database: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            """
            SELECT sequence, event_kind, completed_phase, run_id, configuration_hash
            FROM lifecycle_events ORDER BY sequence
            """
        ).fetchall()


def _phase_name(value: object) -> str | None:
    if value is None:
        return None
    assert isinstance(value, str)
    checkpoint = json.loads(value)
    assert isinstance(checkpoint, dict)
    payload = checkpoint["payload"]
    assert isinstance(payload, dict)
    phase = payload["phase"]
    assert isinstance(phase, str)
    return phase


def _non_event_counts(database: Path) -> tuple[int, int]:
    with sqlite3.connect(database) as connection:
        refusals = connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone()
        conflicts = connection.execute("SELECT COUNT(*) FROM advance_conflicts").fetchone()
    assert refusals is not None
    assert conflicts is not None
    return int(refusals[0]), int(conflicts[0])


def _snapshot_id(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT universe_snapshot_id FROM lifecycle_events "
            "WHERE universe_snapshot_id IS NOT NULL"
        ).fetchone()
    assert row is not None
    snapshot_id = row[0]
    assert isinstance(snapshot_id, str)
    return snapshot_id


def test_lifecycle_journey_resumes_replays_and_rebuilds_status_across_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "runtime"
    for name in AUTHORITY_SENTINEL_NAMES:
        monkeypatch.setenv(name, "must-not-cross-process-seam")
    assert AUTHORITY_SENTINEL_NAMES.isdisjoint(_child_environment())

    interrupted = _run_process("interrupt-after-reconcile", state_root)

    assert interrupted.returncode == INTERRUPTED_EXIT_CODE
    assert interrupted.stdout == ""
    assert interrupted.stderr == ""
    database = state_root / "lifecycle.sqlite3"
    partial_history = _authoritative_history(database)
    assert [(row[0], row[1], _phase_name(row[2])) for row in partial_history] == [
        (0, "advance_requested", None),
        (1, "phase_completed", "ReconcilePriorState"),
    ]

    resumed = _advance_observation(_run_process("advance", state_root))

    assert resumed.disposition == "advanced"
    assert resumed.completed_phase == "CaptureEvidence"
    assert resumed.recovery == "resumed"
    assert resumed.configuration_version == 1
    assert len(resumed.run_id) == SHA256_HEX_LENGTH
    assert len(resumed.configuration_hash) == SHA256_HEX_LENGTH
    assert resumed.ambient_authority_absent is True
    completed_history = _authoritative_history(database)
    assert [(row[0], row[1], _phase_name(row[2])) for row in completed_history] == [
        (0, "advance_requested", None),
        (1, "phase_completed", "ReconcilePriorState"),
        (2, "run_inputs_pinned", "PinRunInputs"),
        (3, "universe_snapshotted", "SnapshotUniverse"),
        (4, "evidence_captured", "CaptureEvidence"),
    ]
    assert {str(row[3]) for row in completed_history} == {resumed.run_id}
    assert {str(row[4]) for row in completed_history} == {resumed.configuration_hash}

    expected_status = _status_observation(_run_process("status", state_root))

    assert expected_status == StatusObservation(
        active_phase="",
        last_completed_cycle="",
        universe_snapshot_cycle=(
            '{"asset_class":"us_equity","cycle_type":"market_session",'
            '"payload":{"trading_date":"2026-08-21"},'
            '"payload_schema_version":1,"schema_version":1}'
        ),
        liveness="active",
        durable_reason="",
        run_id=resumed.run_id,
        configuration_version=resumed.configuration_version,
        configuration_hash=resumed.configuration_hash,
        universe_snapshot_id=_snapshot_id(database),
        ambient_authority_absent=True,
    )
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE lifecycle_status_projection SET liveness = 'manufactured'")

    rebuilt_status = _status_observation(_run_process("status", state_root))
    replayed = _advance_observation(_run_process("advance", state_root))

    assert rebuilt_status == expected_status
    assert replayed.run_id == resumed.run_id
    assert replayed.configuration_hash == resumed.configuration_hash
    assert replayed.recovery == "previously_completed"
    assert replayed.ambient_authority_absent is True
    assert _authoritative_history(database) == completed_history
    assert _non_event_counts(database) == (0, 0)
    assert sorted(path.name for path in state_root.iterdir()) == [
        "evidence-vault",
        "lifecycle.sqlite3",
    ]
