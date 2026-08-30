"""Expose public lifecycle results to the scheduler system journey from a child process."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentic_investment_os.domain.identity import canonical_cycle_bytes
from tests.integration.system._lifecycle_process import _advance, _status

ADVANCE_ARGUMENT_COUNT = 4
STATUS_ARGUMENT_COUNT = 3
INVALID_ARGUMENTS = "expected action, state root, and optional Advance request"
UNKNOWN_ACTION = "unknown scheduler lifecycle action"
STATUS_IDENTITY_MISSING = "scheduler lifecycle Status did not return a pinned identity"


def _emit_advance(state_root: Path, request_json: str) -> None:
    request = json.loads(request_json)
    if type(request) is not dict or set(request) != {"cycle", "mode", "idempotency_key"}:
        raise RuntimeError(INVALID_ARGUMENTS)
    receipt = _advance(state_root)(
        cycle=request["cycle"],
        mode=request["mode"],
        idempotency_key=request["idempotency_key"],
    )
    sys.stdout.write(json.dumps(receipt.to_payload(), sort_keys=True, separators=(",", ":")))


def _emit_status(state_root: Path) -> None:
    status = _status(state_root)()
    identity = status.pinned_run_identity
    if identity is None:
        raise RuntimeError(STATUS_IDENTITY_MISSING)
    sys.stdout.write(
        json.dumps(
            {
                "cycle": json.loads(canonical_cycle_bytes(identity.cycle)),
                "durable_reason": (
                    None if status.durable_reason is None else status.durable_reason.value
                ),
                "liveness": status.liveness.value,
                "run_id": identity.run_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def main() -> None:
    if len(sys.argv) not in (ADVANCE_ARGUMENT_COUNT, STATUS_ARGUMENT_COUNT):
        raise RuntimeError(INVALID_ARGUMENTS)
    action = sys.argv[1]
    state_root = Path(sys.argv[2])
    if action == "advance" and len(sys.argv) == ADVANCE_ARGUMENT_COUNT:
        _emit_advance(state_root, sys.argv[3])
        return
    if action == "status" and len(sys.argv) == STATUS_ARGUMENT_COUNT:
        _emit_status(state_root)
        return
    raise RuntimeError(UNKNOWN_ACTION)


if __name__ == "__main__":
    main()
