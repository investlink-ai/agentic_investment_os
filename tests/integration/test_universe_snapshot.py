from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from agentic_investment_os.application.lifecycle import Advance, Status
from agentic_investment_os.domain.identity import CryptoDecisionWindow, MarketSession
from agentic_investment_os.domain.lifecycle import (
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceRecovery,
    LifecyclePersistenceError,
    LifecyclePhase,
    LifecycleStatus,
)
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.entrypoints.configuration import ConfigurationSource
from agentic_investment_os.entrypoints.lifecycle import configure_advance, configure_status
from tests._evidence import recorded_evidence
from tests._universe import (
    mutable_mapping,
    mutable_mapping_list,
    recorded_universe,
    reseal_recorded_snapshot,
    runtime_configuration,
)

SHA256_HEX_LENGTH = 64


def _cycle(value: str) -> dict[str, object]:
    return MarketSession(date.fromisoformat(value)).to_payload()


@dataclass(frozen=True, slots=True)
class FixedClock:
    instant: datetime = datetime(2026, 8, 21, 20, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.instant


def _configure(
    state_root: Path,
    payload: object,
    *,
    clock: FixedClock | None = None,
) -> Advance:
    configured = configure_advance(
        (ConfigurationSource("test", runtime_configuration(state_root)),),
        repository_root=Path.cwd(),
        recorded_universe=payload,
        recorded_evidence=recorded_evidence(),
        clock=FixedClock() if clock is None else clock,
    )
    assert isinstance(configured, Advance)
    return configured


def _snapshot(payload: dict[str, object], name: str) -> dict[str, object]:
    return mutable_mapping(payload[name])


def _items(payload: dict[str, object], name: str) -> list[dict[str, object]]:
    snapshot_payload = mutable_mapping(_snapshot(payload, name)["payload"])
    return mutable_mapping_list(snapshot_payload["items"])


def test_advance_durably_publishes_a_reconstructable_universe_snapshot(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    advance = _configure(state_root, recorded_universe())

    first = advance(cycle=_cycle("2026-08-22"), mode="champion", idempotency_key="universe-1")
    replay = _configure(state_root, recorded_universe())(
        cycle=_cycle("2026-08-22"), mode="champion", idempotency_key="universe-1"
    )

    assert first.disposition is AdvanceDisposition.ADVANCED
    assert first.completed_phase is not None
    assert first.completed_phase.phase is LifecyclePhase.SELECT_ATTENTION
    assert first.recovery is AdvanceRecovery.FRESH
    assert first.universe_snapshot_id is not None
    assert first.pinned_run_identity is not None
    assert first.pinned_run_identity.data_regime == "alpaca-basic-iex-v1"
    assert len(first.pinned_run_identity.instrument_snapshot_hash) == SHA256_HEX_LENGTH
    assert len(first.pinned_run_identity.position_snapshot_hash) == SHA256_HEX_LENGTH
    assert len(first.pinned_run_identity.eligibility_policy_hash) == SHA256_HEX_LENGTH
    assert replay == first.__class__(
        disposition=first.disposition,
        completed_phase=first.completed_phase,
        pinned_run_identity=first.pinned_run_identity,
        universe_snapshot_id=first.universe_snapshot_id,
        failure_reason=None,
        recovery=AdvanceRecovery.PREVIOUSLY_COMPLETED,
        recorded_at=UtcInstant.from_datetime(FixedClock().instant),
        evidence_policy_id=first.evidence_policy_id,
        evidence_artifact_ids=first.evidence_artifact_ids,
        evidence_refusal_ids=(),
        attention_artifact=first.attention_artifact,
    )

    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        events = connection.execute(
            "SELECT event_kind, completed_phase, universe_snapshot_id, universe_snapshot "
            "FROM lifecycle_events ORDER BY sequence"
        ).fetchall()
    assert [
        (row[0], None if row[1] is None else json.loads(row[1])["payload"]["phase"])
        for row in events
    ] == [
        ("advance_requested", None),
        ("phase_completed", "ReconcilePriorState"),
        ("run_inputs_pinned", "PinRunInputs"),
        ("universe_snapshotted", "SnapshotUniverse"),
        ("evidence_captured", "CaptureEvidence"),
        ("attention_selected", "SelectAttention"),
    ]
    assert all(row[2] is None and row[3] is None for row in events[:2])
    assert events[2][2] == first.universe_snapshot_id
    assert events[2][3] is not None
    assert events[3][2] == first.universe_snapshot_id
    assert events[3][3] is None
    assert events[-1][2] is None
    assert events[-1][3] is None
    snapshot = json.loads(events[2][3])
    material = snapshot["payload"]
    assert [
        (
            item["aliases"][0]["value"],
            item["is_position"],
            item["eligible_for_new_entry"],
            item["position_disposition"],
        )
        for item in material["subjects"]
    ] == [
        ("AAPL", False, True, "not_applicable"),
        ("HOLD", True, False, "refresh_required"),
        ("SPY", False, True, "not_applicable"),
    ]


def test_cutoff_equal_to_record_time_remains_valid_after_reopen(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    cutoff = datetime(2026, 8, 21, 20, 0, tzinfo=UTC)

    first = _configure(state_root, recorded_universe(), clock=FixedClock(cutoff))(
        cycle=_cycle("2026-08-22"),
        mode="champion",
        idempotency_key="cutoff-at-record-time",
    )
    replay = _configure(state_root, recorded_universe(), clock=FixedClock(cutoff))(
        cycle=_cycle("2026-08-22"),
        mode="champion",
        idempotency_key="cutoff-at-record-time",
    )

    assert first.disposition is AdvanceDisposition.ADVANCED
    assert replay.universe_snapshot_id == first.universe_snapshot_id
    assert replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED


def test_alias_only_change_replays_the_existing_authoritative_snapshot(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    first_payload = recorded_universe()
    changed_alias_payload = recorded_universe()
    first_item = _items(changed_alias_payload, "instruments")[0]
    aliases = mutable_mapping_list(first_item["aliases"])
    aliases[0]["value"] = "AAPL.NEW"
    reseal_recorded_snapshot(changed_alias_payload, "instruments")

    first = _configure(state_root, first_payload)(
        cycle=_cycle("2026-08-22"),
        mode="champion",
        idempotency_key="alias-provenance-replay",
    )
    replay = _configure(state_root, changed_alias_payload)(
        cycle=_cycle("2026-08-22"),
        mode="champion",
        idempotency_key="alias-provenance-replay",
    )

    assert first.disposition is AdvanceDisposition.ADVANCED
    assert replay.disposition is AdvanceDisposition.ADVANCED
    assert replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED
    assert replay.pinned_run_identity == first.pinned_run_identity
    assert replay.universe_snapshot_id == first.universe_snapshot_id


def test_alias_change_after_pinning_publishes_the_first_normalized_provenance(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    first_payload = recorded_universe()
    changed_alias_payload = recorded_universe()
    aliases = mutable_mapping_list(_items(changed_alias_payload, "instruments")[0]["aliases"])
    aliases[0]["value"] = "AAPL.NEW"
    reseal_recorded_snapshot(changed_alias_payload, "instruments")
    first = _configure(state_root, first_payload)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER interrupt_after_pin
            BEFORE INSERT ON lifecycle_events
            WHEN NEW.event_kind = 'universe_snapshotted'
            BEGIN SELECT RAISE(ABORT, 'injected interruption'); END
            """
        )

    with pytest.raises(LifecyclePersistenceError, match="SQLite lifecycle checkpoint failed"):
        first(
            cycle=_cycle("2026-08-22"),
            mode="champion",
            idempotency_key="alias-after-pin",
        )
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER interrupt_after_pin")

    resumed = _configure(state_root, changed_alias_payload)(
        cycle=_cycle("2026-08-22"),
        mode="champion",
        idempotency_key="alias-after-pin",
    )

    assert resumed.disposition is AdvanceDisposition.ADVANCED
    assert resumed.recovery is AdvanceRecovery.RESUMED
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT universe_snapshot FROM lifecycle_events WHERE event_kind = 'run_inputs_pinned'"
        ).fetchone()
    assert row is not None
    snapshot = json.loads(row[0])
    subjects = snapshot["payload"]["subjects"]
    assert subjects[0]["aliases"][0]["value"] == "AAPL"


def test_stale_universe_input_fails_closed_without_publishing_a_snapshot(tmp_path: Path) -> None:
    payload = recorded_universe()
    _snapshot(payload, "instruments")["observed_at"] = "2026-08-21T17:00:00+00:00"
    _snapshot(payload, "instruments")["available_at"] = "2026-08-21T17:00:00+00:00"
    reseal_recorded_snapshot(payload, "instruments")
    state_root = tmp_path / "runtime"

    refused = _configure(state_root, payload)(
        cycle=_cycle("2026-08-22"), mode="champion", idempotency_key="stale-universe"
    )

    assert refused.disposition is AdvanceDisposition.FAILED_CLOSED
    assert refused.failure_reason is AdvanceFailureReason.STALE_UNIVERSE_INPUT
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone() == (1,)


def test_disabled_crypto_cycle_fails_before_loading_or_snapshotting_equity_inputs(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    starts_at = datetime(2026, 8, 21, 20, 0, tzinfo=UTC)
    cycle = CryptoDecisionWindow(
        UtcInstant.from_datetime(starts_at),
        UtcInstant.from_datetime(starts_at + timedelta(hours=1)),
    )

    refused = _configure(state_root, recorded_universe())(
        cycle=cycle.to_payload(),
        mode="champion",
        idempotency_key="disabled-crypto-cycle",
    )

    assert refused.disposition is AdvanceDisposition.FAILED_CLOSED
    assert refused.failure_reason is AdvanceFailureReason.UNSUPPORTED_CYCLE
    assert refused.cycle == cycle
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM advance_conflicts").fetchone() == (0,)
    status = configure_status(
        (ConfigurationSource("test", runtime_configuration(state_root)),),
        repository_root=Path.cwd(),
    )
    assert isinstance(status, Status)
    assert status() == LifecycleStatus.not_started()


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    [
        ("missing", AdvanceFailureReason.MISSING_UNIVERSE_INPUT),
        ("partial", AdvanceFailureReason.MISSING_UNIVERSE_INPUT),
        ("malformed", AdvanceFailureReason.INVALID_UNIVERSE_INPUT),
        ("float_version", AdvanceFailureReason.INVALID_UNIVERSE_INPUT),
        ("unmapped_holding", AdvanceFailureReason.CONTRADICTORY_UNIVERSE_INPUT),
        ("later_observation", AdvanceFailureReason.CONTRADICTORY_UNIVERSE_INPUT),
        ("future_cutoff", AdvanceFailureReason.CONTRADICTORY_UNIVERSE_INPUT),
    ],
)
def test_required_universe_input_failures_are_durable_before_snapshot_completion(
    tmp_path: Path,
    case: str,
    expected_reason: AdvanceFailureReason,
) -> None:
    payload: object
    if case == "missing":
        payload = None
    elif case == "malformed":
        payload = {"schema_version": 1}
    else:
        mutable = deepcopy(recorded_universe())
        if case == "float_version":
            mutable["schema_version"] = 1.0
        elif case == "partial":
            mutable_mapping(_snapshot(mutable, "positions")["payload"])["complete"] = False
        elif case == "unmapped_holding":
            position = deepcopy(_items(mutable, "positions")[0])
            identity = mutable_mapping(position["identity"])
            identity["catalog_id"] = "equity-missing"
            _items(mutable, "positions").append(position)
            reseal_recorded_snapshot(mutable, "positions")
        elif case == "future_cutoff":
            mutable["evidence_cutoff"] = "2026-08-21T20:02:00+00:00"
            _snapshot(mutable, "instruments")["observed_at"] = mutable["evidence_cutoff"]
            _snapshot(mutable, "instruments")["available_at"] = mutable["evidence_cutoff"]
            _snapshot(mutable, "positions")["observed_at"] = mutable["evidence_cutoff"]
            _snapshot(mutable, "positions")["available_at"] = mutable["evidence_cutoff"]
            reseal_recorded_snapshot(mutable, "instruments")
            reseal_recorded_snapshot(mutable, "positions")
        else:
            assert case == "later_observation"
            _snapshot(mutable, "instruments")["observed_at"] = "2026-08-21T20:01:00+00:00"
            _snapshot(mutable, "instruments")["available_at"] = "2026-08-21T20:01:00+00:00"
            reseal_recorded_snapshot(mutable, "instruments")
        payload = mutable
    state_root = tmp_path / case

    refused = _configure(state_root, payload)(
        cycle=_cycle("2026-08-22"),
        mode="champion",
        idempotency_key=f"refuse-{case}",
    )

    assert refused.disposition is AdvanceDisposition.FAILED_CLOSED
    assert refused.failure_reason is expected_reason
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone() == (0,)
        assert connection.execute(
            "SELECT idempotency_key, reason_code FROM advance_refusals"
        ).fetchall() == [(f"refuse-{case}", expected_reason.value)]


def test_retry_with_changed_recorded_snapshot_fails_without_changing_first_snapshot(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    original_payload = recorded_universe()
    first = _configure(state_root, original_payload)(
        cycle=_cycle("2026-08-22"),
        mode="champion",
        idempotency_key="changed-universe-retry",
    )
    changed_payload = deepcopy(original_payload)
    instrument_payload = mutable_mapping(_items(changed_payload, "instruments")[0]["payload"])
    instrument_payload["price"] = "226"

    conflict = _configure(state_root, changed_payload)(
        cycle=_cycle("2026-08-22"),
        mode="champion",
        idempotency_key="changed-universe-retry",
    )
    replay = _configure(state_root, original_payload)(
        cycle=_cycle("2026-08-22"),
        mode="champion",
        idempotency_key="changed-universe-retry",
    )

    assert conflict.disposition is AdvanceDisposition.FAILED_CLOSED
    assert conflict.failure_reason is AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
    assert replay.universe_snapshot_id == first.universe_snapshot_id
    assert replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone() == (6,)
        assert connection.execute("SELECT COUNT(*) FROM advance_conflicts").fetchone() == (1,)
