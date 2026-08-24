from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from agentic_investment_os.adapters.sqlite_lifecycle import (
    RuntimeRootRefusal,
    SQLiteConstitutionGovernance,
)
from agentic_investment_os.application.governance import Govern
from agentic_investment_os.application.lifecycle import Advance, Status
from agentic_investment_os.domain.governance import (
    ACTIVE_CONSTITUTION,
    GovernanceDisposition,
    GovernanceReceipt,
    GovernanceRefusalReason,
    GovernanceStateError,
)
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.lifecycle import LifecyclePersistenceError
from agentic_investment_os.entrypoints.configuration import (
    ConfigurationRefusal,
    ConfigurationRefusalCode,
    ConfigurationSource,
)
from agentic_investment_os.entrypoints.governance import configure_govern
from agentic_investment_os.entrypoints.lifecycle import configure_advance, configure_status
from tests._evidence import recorded_evidence
from tests._governance import (
    ACTIVATION_SESSION,
    HashApprovalVerifier,
    RecordedSessionEligibility,
    amended_constitution,
    approval_for,
)
from tests._universe import recorded_universe, runtime_configuration

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
AMENDMENT_VERSION = 2


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


def _sources(state_root: Path) -> tuple[ConfigurationSource, ...]:
    return (ConfigurationSource("test", runtime_configuration(state_root)),)


def _govern(state_root: Path, *, clock: datetime | None = None) -> Govern:
    capability = configure_govern(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        approval_verifier=HashApprovalVerifier(),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(clock or datetime(2026, 8, 21, 20, 5, tzinfo=UTC)),
    )
    assert isinstance(capability, Govern)
    return capability


def _schedule(govern: Govern) -> GovernanceReceipt:
    artifact = amended_constitution()
    return govern(
        request_identity="constitution-amendment-2",
        artifact=artifact.to_payload(),
        activation_session=ACTIVATION_SESSION.to_payload(),
        approval_proof=approval_for(artifact).to_payload(),
    )


def test_govern_advance_and_status_schedule_activate_pin_and_replay_exactly_once(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    govern = _govern(state_root)

    scheduled = _schedule(govern)
    assert scheduled.disposition is GovernanceDisposition.SCHEDULED
    assert scheduled.constitution == amended_constitution().reference
    assert "signature" not in json.dumps(scheduled.to_payload())

    pending_status = configure_status(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        approval_verifier=HashApprovalVerifier(),
    )
    assert isinstance(pending_status, Status)
    pending = pending_status().constitution_governance
    assert pending is not None
    assert pending.active == ACTIVE_CONSTITUTION.reference
    assert len(pending.pending) == 1

    advance = configure_advance(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        clock=FixedClock(datetime(2026, 8, 24, 22, 0, tzinfo=UTC)),
        approval_verifier=HashApprovalVerifier(),
    )
    assert isinstance(advance, Advance)
    receipt = advance(
        cycle=ACTIVATION_SESSION.to_payload(),
        mode="champion",
        idempotency_key="advance-2026-08-24",
    )
    assert receipt.pinned_run_identity is not None
    assert receipt.pinned_run_identity.constitution_version == AMENDMENT_VERSION
    assert receipt.pinned_run_identity.constitution_hash == amended_constitution().content_hash

    active = pending_status().constitution_governance
    assert active is not None
    assert active.active == amended_constitution().reference
    assert active.pending == ()
    assert active.superseded == (ACTIVE_CONSTITUTION.reference,)

    replayed = _schedule(govern)
    assert replayed.disposition is GovernanceDisposition.REPLAYED
    assert replayed.replayed_disposition is GovernanceDisposition.ACTIVATED

    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        counts = connection.execute(
            "SELECT event_kind, COUNT(*) FROM constitution_governance_events GROUP BY event_kind"
        ).fetchall()
    assert counts == [("constitution_activated", 1), ("constitution_scheduled", 1)]


def test_changed_identity_ineligible_session_and_missed_boundary_fail_closed(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    govern = _govern(state_root)
    _schedule(govern)

    changed = amended_constitution(final_clause="Changed under a reused identity.")
    conflict = govern(
        request_identity="constitution-amendment-2",
        artifact=changed.to_payload(),
        activation_session=ACTIVATION_SESSION.to_payload(),
        approval_proof=approval_for(changed).to_payload(),
    )
    assert conflict.disposition is GovernanceDisposition.CONFLICTED
    assert conflict.reason is GovernanceRefusalReason.IDENTITY_CONFLICT
    replayed_conflict = govern(
        request_identity="constitution-amendment-2",
        artifact=changed.to_payload(),
        activation_session=ACTIVATION_SESSION.to_payload(),
        approval_proof=approval_for(changed).to_payload(),
    )
    assert replayed_conflict.disposition is GovernanceDisposition.REPLAYED
    assert replayed_conflict.replayed_disposition is GovernanceDisposition.CONFLICTED
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        conflict_count = connection.execute(
            "SELECT COUNT(*) FROM constitution_governance_events "
            "WHERE event_kind = 'constitution_conflicted'"
        ).fetchone()
    assert conflict_count == (1,)

    ineligible_session = MarketSession(date(2026, 8, 25))
    ineligible_artifact = amended_constitution(final_clause="A separately identified request.")
    ineligible = govern(
        request_identity="constitution-amendment-ineligible",
        artifact=ineligible_artifact.to_payload(),
        activation_session=ineligible_session.to_payload(),
        approval_proof=approval_for(
            ineligible_artifact,
            request_identity="constitution-amendment-ineligible",
            activation_session=ineligible_session,
        ).to_payload(),
    )
    assert ineligible.disposition is GovernanceDisposition.REFUSED
    assert ineligible.reason is GovernanceRefusalReason.INELIGIBLE_SESSION
    assert ineligible.constitution == ineligible_artifact.reference

    advance = configure_advance(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        clock=FixedClock(datetime(2026, 8, 25, 22, 0, tzinfo=UTC)),
        approval_verifier=HashApprovalVerifier(),
    )
    assert isinstance(advance, Advance)
    with pytest.raises(GovernanceStateError, match="missed Constitution activation boundary"):
        advance(
            cycle=MarketSession(date(2026, 8, 25)).to_payload(),
            mode="champion",
            idempotency_key="advance-after-missed-boundary",
        )


def test_status_reverifies_durable_approval_and_append_only_triggers_refuse_mutation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    govern = _govern(state_root)
    _schedule(govern)
    status = configure_status(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        approval_verifier=HashApprovalVerifier(),
    )
    assert isinstance(status, Status)
    database = state_root / "lifecycle.sqlite3"

    with (
        sqlite3.connect(database) as connection,
        pytest.raises(sqlite3.IntegrityError, match="append-only Constitution governance ledger"),
    ):
        connection.execute("DELETE FROM constitution_governance_events")

    with sqlite3.connect(database) as connection:
        envelope_text = connection.execute(
            "SELECT event_envelope FROM constitution_governance_events WHERE sequence = 0"
        ).fetchone()[0]
        envelope = json.loads(envelope_text)
        envelope["payload"]["approval"]["signature"] = "f" * 64
        material = {key: value for key, value in envelope.items() if key != "content_hash"}
        envelope["content_hash"] = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        connection.execute("DROP TRIGGER constitution_governance_events_are_append_only_update")
        connection.execute(
            "UPDATE constitution_governance_events SET event_envelope = ? WHERE sequence = 0",
            (json.dumps(envelope, sort_keys=True, separators=(",", ":")),),
        )

    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        status()


def test_governed_history_requires_the_operator_approval_verifier_on_reopen(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    _schedule(_govern(state_root))
    status = configure_status(_sources(state_root), repository_root=REPOSITORY_ROOT)
    assert isinstance(status, Status)

    with pytest.raises(GovernanceStateError, match="operator approval verifier required"):
        status()


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("event_envelope", "{", "invalid Constitution governance history"),
        ("event_envelope", "{}", "invalid Constitution governance history"),
        ("request_identity", "different-request", "invalid Constitution governance history"),
    ],
)
def test_governance_reader_rejects_malformed_envelopes_and_column_mismatches(
    tmp_path: Path,
    column: str,
    value: str,
    message: str,
) -> None:
    state_root = tmp_path / "runtime"
    _schedule(_govern(state_root))
    database = state_root / "lifecycle.sqlite3"
    ledger = SQLiteConstitutionGovernance.open_existing(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER constitution_governance_events_are_append_only_update")
        connection.execute(
            f"UPDATE constitution_governance_events SET {column} = ? WHERE sequence = 0",  # noqa: S608
            (value,),
        )

    with pytest.raises(GovernanceStateError, match=message):
        ledger.rebuild_constitution_status(HashApprovalVerifier())


def test_governance_sql_failure_is_translated_before_authority_is_returned(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    govern = _govern(state_root)
    ledger = govern.ledger
    assert isinstance(ledger, SQLiteConstitutionGovernance)
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        connection.execute("DROP TABLE constitution_governance_events")

    with pytest.raises(LifecyclePersistenceError, match="SQLite lifecycle checkpoint failed"):
        ledger.rebuild_constitution_status(HashApprovalVerifier())


def test_govern_composition_refuses_invalid_configuration_and_runtime_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid_configuration = runtime_configuration(tmp_path / "invalid")
    invalid_configuration["schema_version"] = 2
    refused = configure_govern(
        (ConfigurationSource("test", invalid_configuration),),
        repository_root=REPOSITORY_ROOT,
        approval_verifier=HashApprovalVerifier(),
        session_eligibility=RecordedSessionEligibility(),
    )
    assert refused == ConfigurationRefusal(
        ConfigurationRefusalCode.UNSUPPORTED_VERSION,
        ("schema_version",),
    )

    def refuse_runtime_root(state_root: Path) -> RuntimeRootRefusal:
        del state_root
        return RuntimeRootRefusal()

    monkeypatch.setattr(
        "agentic_investment_os.entrypoints.governance.prepare_runtime_database",
        refuse_runtime_root,
    )
    invalid_root = configure_govern(
        _sources(tmp_path / "runtime"),
        repository_root=REPOSITORY_ROOT,
        approval_verifier=HashApprovalVerifier(),
        session_eligibility=RecordedSessionEligibility(),
    )
    assert invalid_root == ConfigurationRefusal(
        ConfigurationRefusalCode.INVALID_STATE_ROOT,
        ("state_root",),
    )
