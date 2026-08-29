import os
import shlex
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]
STATE_MACHINE_NODE = (
    "tests/integration/test_advance_lifecycle.py::TestLifecycleStateMachine::runTest"
)
PYTEST_TIMING_ARGUMENTS = ("--durations=20", "--durations-min=1.0")
FAKE_GATE_COMMAND = """\
import os
import sys
import time
from pathlib import Path

mode, *arguments = sys.argv[1:]
if mode == "pytest":
    if any(argument.startswith("--deselect=") for argument in arguments):
        leg = "coverage"
    elif any("::TestLifecycleStateMachine::runTest" in argument for argument in arguments):
        leg = "state-machine"
    else:
        raise SystemExit(2)
elif mode == "tiers":
    leg = "coverage-tiers"
else:
    raise SystemExit(2)

event_log = Path(os.environ["GATE_EVENT_LOG"])


def record(phase: str) -> None:
    event = f"{leg}\\t{phase}\\t{time.monotonic_ns()}\\t{chr(31).join(arguments)}\\n"
    descriptor = os.open(event_log, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, event.encode())
    finally:
        os.close(descriptor)


record("start")
if mode == "pytest":
    time.sleep(0.2)
if os.environ.get("FAIL_GATE_LEG") == leg:
    raise SystemExit(1)
record("end")
"""


class GateEvent(NamedTuple):
    leg: str
    phase: str
    timestamp_ns: int
    arguments: tuple[str, ...]


def _write_fake_gate_command(tmp_path: Path) -> Path:
    command = tmp_path / "fake-gate-command"
    command.write_text(f"#!{sys.executable}\n{FAKE_GATE_COMMAND}", encoding="utf-8")
    command.chmod(0o700)
    return command


def _run_test_gate(
    tmp_path: Path,
    *,
    failing_leg: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[GateEvent]]:
    make_executable = shutil.which("make")
    assert make_executable is not None

    command = _write_fake_gate_command(tmp_path)
    event_log = tmp_path / "events.log"
    environment = os.environ.copy()
    environment["GATE_EVENT_LOG"] = str(event_log)
    environment.pop("FAIL_GATE_LEG", None)
    if failing_leg is not None:
        environment["FAIL_GATE_LEG"] = failing_leg

    completed = subprocess.run(  # noqa: S603 - Executable and arguments are test-owned.
        (
            make_executable,
            "--no-print-directory",
            f"PYTEST_COMMAND={shlex.quote(str(command))} pytest",
            f"COVERAGE_TIER_COMMAND={shlex.quote(str(command))} tiers",
            "test",
        ),
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )
    events = []
    for line in event_log.read_text(encoding="utf-8").splitlines():
        leg, phase, timestamp_ns, argument_text = line.split("\t", maxsplit=3)
        arguments = tuple(argument_text.split(chr(31))) if argument_text else ()
        events.append(GateEvent(leg, phase, int(timestamp_ns), arguments))
    return completed, events


def test_test_gate_runs_exact_partition_in_parallel(tmp_path: Path) -> None:
    completed, events = _run_test_gate(tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert Counter((event.leg, event.phase) for event in events) == Counter(
        {
            ("coverage", "start"): 1,
            ("coverage", "end"): 1,
            ("state-machine", "start"): 1,
            ("state-machine", "end"): 1,
            ("coverage-tiers", "start"): 1,
            ("coverage-tiers", "end"): 1,
        }
    )
    starts = {event.leg: event for event in events if event.phase == "start"}
    ends = {event.leg: event for event in events if event.phase == "end"}
    assert starts.keys() == {"coverage", "state-machine", "coverage-tiers"}
    assert ends.keys() == starts.keys()
    assert starts["coverage"].timestamp_ns < ends["state-machine"].timestamp_ns
    assert starts["state-machine"].timestamp_ns < ends["coverage"].timestamp_ns
    assert starts["coverage-tiers"].timestamp_ns > ends["coverage"].timestamp_ns
    assert starts["coverage"].arguments == (
        *PYTEST_TIMING_ARGUMENTS,
        f"--deselect={STATE_MACHINE_NODE}",
    )
    assert starts["state-machine"].arguments == (
        *PYTEST_TIMING_ARGUMENTS,
        "-o",
        "addopts=--strict-config --strict-markers -ra",
        "-p",
        "no:cacheprovider",
        STATE_MACHINE_NODE,
    )


@pytest.mark.parametrize("failing_leg", ["coverage", "state-machine"])
def test_test_gate_fails_when_either_parallel_leg_fails(
    tmp_path: Path,
    failing_leg: str,
) -> None:
    completed, events = _run_test_gate(tmp_path, failing_leg=failing_leg)

    assert completed.returncode != 0
    assert {event.leg for event in events if event.phase == "start"} >= {
        "coverage",
        "state-machine",
    }
