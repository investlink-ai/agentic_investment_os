from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Never

import pytest

from agentic_investment_os.adapters.sqlite_lifecycle import (
    RuntimeRootRefusal,
    SQLiteConstitutionGovernance,
    SQLiteLifecycleLedger,
)
from agentic_investment_os.application.governance import Govern
from agentic_investment_os.application.lifecycle import Advance, Status
from agentic_investment_os.domain.governance import (
    ACTIVE_CONSTITUTION,
    ConstitutionUse,
    GovernanceDisposition,
    GovernanceReceipt,
    GovernanceRefusalReason,
    GovernanceStateError,
)
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.lifecycle import (
    AdvanceAttempt,
    IdempotencyKey,
    LifecycleCommand,
    LifecycleDecision,
    LifecyclePersistenceError,
)
from agentic_investment_os.domain.temporal import UtcInstant
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
UNEXPECTED_UNIVERSE_LOAD = "overdue governance reached downstream universe work"


class SimulatedInterruptionError(RuntimeError):
    """Stop a lifecycle after its first authoritative write."""


@dataclass(frozen=True, slots=True)
class UnexpectedUniverseSource:
    def load(self) -> Never:
        raise AssertionError(UNEXPECTED_UNIVERSE_LOAD)


@dataclass(slots=True)
class InterruptAfterFirstWrite:
    delegate: SQLiteLifecycleLedger
    interrupted: bool = False

    def pinned_constitution_use(self, idempotency_key: IdempotencyKey) -> ConstitutionUse | None:
        return self.delegate.pinned_constitution_use(idempotency_key)

    def constitution_uses(self) -> tuple[ConstitutionUse, ...]:
        return self.delegate.constitution_uses()

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        recorded_at: UtcInstant,
    ) -> LifecycleDecision:
        decision = self.delegate.advance_step(command, attempt, recorded_at)
        if not self.interrupted:
            self.interrupted = True
            raise SimulatedInterruptionError
        return decision


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
    governance_ledger = govern.ledger
    assert isinstance(governance_ledger, SQLiteConstitutionGovernance)
    assert (
        governance_ledger.constitution_for(ACTIVATION_SESSION, HashApprovalVerifier())
        == ACTIVE_CONSTITUTION
    )

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
        session_eligibility=RecordedSessionEligibility(),
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
        session_eligibility=RecordedSessionEligibility(),
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

    ledger = SQLiteConstitutionGovernance.open_existing(state_root / "lifecycle.sqlite3")
    historical = ConstitutionUse(
        ACTIVATION_SESSION,
        ACTIVE_CONSTITUTION.reference,
        UtcInstant.from_datetime(datetime(2026, 8, 21, 20, 0, tzinfo=UTC)),
    )
    with pytest.raises(GovernanceStateError, match="operator approval verifier required"):
        ledger.constitution_for_use(historical, None)


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


@pytest.mark.parametrize(
    "recorded_at",
    [
        datetime(2026, 8, 24, 20, 5, tzinfo=UTC),
        datetime(2026, 8, 25, 20, 5, tzinfo=UTC),
    ],
)
def test_govern_refuses_current_or_past_activation_before_downstream_state(
    tmp_path: Path,
    recorded_at: datetime,
) -> None:
    state_root = tmp_path / recorded_at.date().isoformat()

    receipt = _schedule(_govern(state_root, clock=recorded_at))

    assert receipt.disposition is GovernanceDisposition.REFUSED
    assert receipt.reason is GovernanceRefusalReason.NON_FUTURE_ACTIVATION
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        governance_count = connection.execute(
            "SELECT COUNT(*) FROM constitution_governance_events"
        ).fetchone()
        lifecycle_count = connection.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()
    assert governance_count == (1,)
    assert lifecycle_count == (0,)
    assert not (state_root / "evidence-vault").exists()


def test_invalid_governance_retry_is_bounded_and_changed_raw_material_is_distinct(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    govern = _govern(state_root)
    arguments: dict[str, object] = {
        "request_identity": [],
        "artifact": {"invalid": True},
        "activation_session": ACTIVATION_SESSION.to_payload(),
        "approval_proof": None,
    }

    first = govern(**arguments)
    replay = govern(**arguments)
    changed = govern(**{**arguments, "request_identity": ["changed"]})

    assert first.disposition is GovernanceDisposition.REFUSED
    assert replay.disposition is GovernanceDisposition.REPLAYED
    assert changed.disposition is GovernanceDisposition.REFUSED
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        count = connection.execute("SELECT COUNT(*) FROM constitution_governance_events").fetchone()
    assert count == (2,)


def test_interrupted_run_keeps_its_pin_after_a_later_constitution_activates(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    initial = configure_advance(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(datetime(2026, 8, 22, 20, 5, tzinfo=UTC)),
        approval_verifier=HashApprovalVerifier(),
    )
    assert isinstance(initial, Advance)
    ledger = initial.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    interrupted = Advance(
        ledger=InterruptAfterFirstWrite(ledger),
        configuration_version=initial.configuration_version,
        configuration_hash=initial.configuration_hash,
        universe_source=initial.universe_source,
        enabled_asset_classes=initial.enabled_asset_classes,
        universe_policy=initial.universe_policy,
        evidence_capture=initial.evidence_capture,
        attention_policy=initial.attention_policy,
        attention_inputs=initial.attention_inputs,
        clock=initial.clock,
        constitution_registry=initial.constitution_registry,
    )
    with pytest.raises(SimulatedInterruptionError):
        interrupted(
            cycle=ACTIVATION_SESSION.to_payload(),
            mode="champion",
            idempotency_key="interrupted-before-activation",
        )

    scheduled = _schedule(
        _govern(
            state_root,
            clock=datetime(2026, 8, 22, 20, 6, tzinfo=UTC),
        )
    )
    assert scheduled.disposition is GovernanceDisposition.SCHEDULED
    retry = configure_advance(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(datetime(2026, 8, 24, 20, 5, tzinfo=UTC)),
        approval_verifier=HashApprovalVerifier(),
    )
    assert isinstance(retry, Advance)

    receipt = retry(
        cycle=ACTIVATION_SESSION.to_payload(),
        mode="champion",
        idempotency_key="interrupted-before-activation",
    )

    assert receipt.pinned_run_identity is not None
    assert receipt.pinned_run_identity.constitution_version == ACTIVE_CONSTITUTION.version
    assert receipt.pinned_run_identity.constitution_hash == ACTIVE_CONSTITUTION.content_hash
    status = configure_status(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        approval_verifier=HashApprovalVerifier(),
    )
    assert isinstance(status, Status)
    rebuilt = status()
    assert rebuilt.constitution_governance is not None
    assert rebuilt.constitution_governance.active == amended_constitution().reference
    assert rebuilt.pinned_run_identity is not None
    assert rebuilt.pinned_run_identity.constitution_version == ACTIVE_CONSTITUTION.version
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        requested = connection.execute(
            "SELECT COUNT(*) FROM lifecycle_events "
            "WHERE idempotency_key = 'interrupted-before-activation' "
            "AND event_kind = 'advance_requested'"
        ).fetchone()
        activated = connection.execute(
            "SELECT COUNT(*) FROM constitution_governance_events "
            "WHERE event_kind = 'constitution_activated'"
        ).fetchone()
    assert requested == (1,)
    assert activated == (1,)


def test_overdue_governance_blocks_a_historical_pin_before_downstream_work(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    initial = configure_advance(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(datetime(2026, 8, 22, 20, 5, tzinfo=UTC)),
        approval_verifier=HashApprovalVerifier(),
    )
    assert isinstance(initial, Advance)
    ledger = initial.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    interrupted = Advance(
        ledger=InterruptAfterFirstWrite(ledger),
        configuration_version=initial.configuration_version,
        configuration_hash=initial.configuration_hash,
        universe_source=initial.universe_source,
        enabled_asset_classes=initial.enabled_asset_classes,
        universe_policy=initial.universe_policy,
        evidence_capture=initial.evidence_capture,
        attention_policy=initial.attention_policy,
        attention_inputs=initial.attention_inputs,
        clock=initial.clock,
        constitution_registry=initial.constitution_registry,
    )
    with pytest.raises(SimulatedInterruptionError):
        interrupted(
            cycle=ACTIVATION_SESSION.to_payload(),
            mode="champion",
            idempotency_key="overdue-historical-pin",
        )
    _schedule(
        _govern(
            state_root,
            clock=datetime(2026, 8, 22, 20, 6, tzinfo=UTC),
        )
    )
    configured_retry = configure_advance(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(datetime(2026, 8, 25, 20, 5, tzinfo=UTC)),
        approval_verifier=HashApprovalVerifier(),
    )
    assert isinstance(configured_retry, Advance)
    retry = Advance(
        ledger=configured_retry.ledger,
        configuration_version=configured_retry.configuration_version,
        configuration_hash=configured_retry.configuration_hash,
        universe_source=UnexpectedUniverseSource(),
        enabled_asset_classes=configured_retry.enabled_asset_classes,
        universe_policy=configured_retry.universe_policy,
        evidence_capture=configured_retry.evidence_capture,
        attention_policy=configured_retry.attention_policy,
        attention_inputs=configured_retry.attention_inputs,
        clock=configured_retry.clock,
        constitution_registry=configured_retry.constitution_registry,
    )

    with pytest.raises(GovernanceStateError, match="missed Constitution activation boundary"):
        retry(
            cycle=ACTIVATION_SESSION.to_payload(),
            mode="champion",
            idempotency_key="overdue-historical-pin",
        )

    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM lifecycle_events WHERE idempotency_key = 'overdue-historical-pin'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM constitution_governance_events "
            "WHERE event_kind = 'constitution_activated'"
        ).fetchone() == (0,)


def test_lifecycle_pins_fail_closed_if_governance_history_is_removed(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    _schedule(_govern(state_root))
    advance = configure_advance(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(datetime(2026, 8, 24, 20, 5, tzinfo=UTC)),
        approval_verifier=HashApprovalVerifier(),
    )
    assert isinstance(advance, Advance)
    advance(
        cycle=ACTIVATION_SESSION.to_payload(),
        mode="champion",
        idempotency_key="governed-lifecycle-use",
    )
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
            "AND name = 'constitution_governance_events_are_append_only_delete'"
        ).fetchone()
        assert trigger_sql is not None
        assert isinstance(trigger_sql[0], str)
        before_count = connection.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()
        connection.execute("DROP TRIGGER constitution_governance_events_are_append_only_delete")
        connection.execute("DELETE FROM constitution_governance_events")
        connection.execute(trigger_sql[0])
    evidence_before = tuple(sorted(path.relative_to(state_root) for path in state_root.rglob("*")))

    status = configure_status(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        approval_verifier=HashApprovalVerifier(),
    )
    assert isinstance(status, Status)
    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        status()
    retry = configure_advance(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(datetime(2026, 8, 25, 20, 5, tzinfo=UTC)),
        approval_verifier=HashApprovalVerifier(),
    )
    assert isinstance(retry, Advance)
    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        retry(
            cycle=MarketSession(date(2026, 8, 25)).to_payload(),
            mode="champion",
            idempotency_key="blocked-by-missing-governance",
        )
    with sqlite3.connect(database) as connection:
        after_count = connection.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()
    evidence_after = tuple(sorted(path.relative_to(state_root) for path in state_root.rglob("*")))
    assert after_count == before_count
    assert evidence_after == evidence_before


def test_status_reopens_and_rebuilds_projection_from_governed_history(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    _schedule(_govern(state_root))
    initial = configure_status(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        approval_verifier=HashApprovalVerifier(),
    )
    assert isinstance(initial, Status)
    initial()
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM lifecycle_status_projection")

    reopened = configure_status(
        _sources(state_root),
        repository_root=REPOSITORY_ROOT,
        approval_verifier=HashApprovalVerifier(),
    )

    assert isinstance(reopened, Status)
    governance = reopened().constitution_governance
    assert governance is not None
    assert governance.active == ACTIVE_CONSTITUTION.reference
    assert governance.pending[0].constitution == amended_constitution().reference


def test_document_owned_baseline_constitution_matches_the_runtime_artifact() -> None:
    document = (REPOSITORY_ROOT / "docs" / "investment-domain.md").read_text(encoding="utf-8")
    section = document.split("## Investment Constitution", 1)[1].split("## Decision flow", 1)[0]
    clauses = tuple(
        match.group(1) for match in re.finditer(r"^\d+\. (.+)$", section, flags=re.MULTILINE)
    )

    assert clauses == ACTIVE_CONSTITUTION.clauses
