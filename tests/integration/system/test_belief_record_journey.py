from __future__ import annotations

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
PROCESS_MODULE = "tests.integration.system._belief_process"
INTERRUPTED_EXIT_CODE = 75
RECORD_FIELD_COUNT = 6
GRAPH_FIELD_COUNT = 6
AUTHORITY_SENTINEL_NAMES = frozenset(
    {
        "BELIEF_JOURNEY_BROKER_SENTINEL",
        "BELIEF_JOURNEY_MODEL_SENTINEL",
        "BELIEF_JOURNEY_MUTABLE_ACCOUNT_SENTINEL",
    }
)


@dataclass(frozen=True, slots=True)
class RecordObservation:
    disposition: str
    event_id: str
    ledger_position: int
    projection_identity: str
    ambient_authority_absent: bool


@dataclass(frozen=True, slots=True)
class GraphObservation:
    content_hash: str
    belief_nodes: int
    evidence_nodes: int
    omitted_beliefs: int
    ambient_authority_absent: bool


def _child_environment() -> dict[str, str]:
    return {"LC_ALL": "C", "PATH": os.defpath, "PYTHONHASHSEED": "0"}


def _run_process(action: str, state_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        (sys.executable, "-m", PROCESS_MODULE, action, str(state_root)),
        cwd=REPOSITORY_ROOT,
        env=_child_environment(),
        capture_output=True,
        text=True,
        check=False,
    )


def _record_observation(completed: subprocess.CompletedProcess[str]) -> RecordObservation:
    assert completed.returncode == 0, completed.stderr
    fields = completed.stdout.split("\t")
    assert len(fields) == RECORD_FIELD_COUNT
    assert fields[0] == "record"
    return RecordObservation(
        disposition=fields[1],
        event_id=fields[2],
        ledger_position=int(fields[3]),
        projection_identity=fields[4],
        ambient_authority_absent=fields[5] == "true",
    )


def _graph_observation(completed: subprocess.CompletedProcess[str]) -> GraphObservation:
    assert completed.returncode == 0, completed.stderr
    fields = completed.stdout.split("\t")
    assert len(fields) == GRAPH_FIELD_COUNT
    assert fields[0] == "graph"
    return GraphObservation(
        content_hash=fields[1],
        belief_nodes=int(fields[2]),
        evidence_nodes=int(fields[3]),
        omitted_beliefs=int(fields[4]),
        ambient_authority_absent=fields[5] == "true",
    )


def _belief_rows(database: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            """
            SELECT ledger_position, event_id, projection_identity
            FROM belief_events ORDER BY ledger_position
            """
        ).fetchall()


def test_belief_record_journey_recovers_replays_and_rebuilds_across_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "runtime"
    for name in AUTHORITY_SENTINEL_NAMES:
        monkeypatch.setenv(name, "must-not-cross-process-seam")
    assert AUTHORITY_SENTINEL_NAMES.isdisjoint(_child_environment())

    seeded = _run_process("seed", state_root)
    interrupted = _run_process("interrupt-before-append", state_root)

    assert seeded.returncode == 0, seeded.stderr
    assert seeded.stdout == "seeded"
    assert interrupted.returncode == INTERRUPTED_EXIT_CODE
    assert interrupted.stdout == ""
    assert interrupted.stderr == ""
    database = state_root / "lifecycle.sqlite3"
    assert _belief_rows(database) == []

    appended = _record_observation(_run_process("record", state_root))
    history = _belief_rows(database)
    replayed = _record_observation(_run_process("record", state_root))

    assert appended.disposition == "appended"
    assert replayed.disposition == "replayed"
    assert replayed.event_id == appended.event_id
    assert replayed.ledger_position == appended.ledger_position == 1
    assert replayed.projection_identity == appended.projection_identity
    assert appended.ambient_authority_absent is True
    assert replayed.ambient_authority_absent is True
    assert _belief_rows(database) == history

    expected_graph = _graph_observation(_run_process("graph", state_root))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE belief_graph_projection SET graph_json = ? WHERE singleton = 1",
            ('{"manufactured":"projection"}',),
        )
    rebuilt_graph = _graph_observation(_run_process("graph", state_root))

    assert expected_graph == rebuilt_graph
    assert expected_graph.belief_nodes == 1
    assert expected_graph.evidence_nodes == 1
    assert expected_graph.omitted_beliefs == 0
    assert expected_graph.ambient_authority_absent is True
    assert _belief_rows(database) == history
