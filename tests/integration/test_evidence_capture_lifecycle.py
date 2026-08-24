from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from agentic_investment_os.adapters.sqlite_lifecycle import SQLiteLifecycleLedger
from agentic_investment_os.application.lifecycle import Advance, Status
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.lifecycle import (
    AdvanceDisposition,
    AdvanceFailureReason,
    InvalidLifecycleStateError,
    LifecyclePhase,
    parse_advance_receipt,
)
from agentic_investment_os.entrypoints.configuration import ConfigurationSource
from agentic_investment_os.entrypoints.lifecycle import configure_advance, configure_status
from agentic_investment_os.evidence.capture import EvidencePersistenceError, InvalidEvidenceError
from tests._evidence import evidence_item, recorded_evidence
from tests._universe import (
    mutable_mapping,
    mutable_mapping_list,
    recorded_universe,
    runtime_configuration,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_EVIDENCE_ARTIFACTS = 2
EXPECTED_HISTORICAL_POLICIES = 2
MAXIMUM_CANDIDATE_CARDS = 20
MAXIMUM_NEW_DOSSIERS = 5


@dataclass(frozen=True, slots=True)
class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 21, 22, 0, tzinfo=UTC)


def test_advance_captures_market_and_news_after_the_pinned_universe(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    configured = configure_advance(
        (ConfigurationSource("test", runtime_configuration(state_root)),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        clock=_FixedClock(),
    )
    assert isinstance(configured, Advance)

    receipt = configured(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="issue-20",
    )

    assert receipt.disposition is AdvanceDisposition.ADVANCED
    assert receipt.completed_phase is not None
    assert receipt.completed_phase.phase is LifecyclePhase.SELECT_ATTENTION
    assert len(receipt.evidence_artifact_ids) == REQUIRED_EVIDENCE_ARTIFACTS
    assert receipt.evidence_refusal_ids == ()
    assert receipt.attention_artifact is not None
    assert (
        receipt.attention_artifact.resource_accounting.candidate_card_count
        <= MAXIMUM_CANDIDATE_CARDS
    )
    assert receipt.attention_artifact.resource_accounting.new_dossier_count <= MAXIMUM_NEW_DOSSIERS
    assert receipt.attention_artifact.resource_accounting.model_tokens == 0
    assert receipt.attention_artifact.resource_accounting.model_turns == 0
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        events = connection.execute(
            "SELECT event_kind FROM lifecycle_events ORDER BY sequence"
        ).fetchall()
    assert events[-1] == ("attention_selected",)
    assert sorted(path.name for path in state_root.iterdir()) == [
        "evidence-vault",
        "lifecycle.sqlite3",
    ]


@pytest.mark.parametrize("consumer", ["advance", "status"])
def test_completed_lifecycle_replay_revalidates_referenced_vault_content(
    tmp_path: Path,
    consumer: str,
) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    configured = configure_advance(
        sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        clock=_FixedClock(),
    )
    assert isinstance(configured, Advance)
    configured(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="issue-20-vault-revalidation",
    )
    next((state_root / "evidence-vault" / "contents").iterdir()).unlink()

    if consumer == "advance":
        reopened = configure_advance(
            sources,
            repository_root=REPOSITORY_ROOT,
            recorded_universe=recorded_universe(),
            recorded_evidence=recorded_evidence(),
            clock=_FixedClock(),
        )
        assert isinstance(reopened, Advance)
        with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
            reopened(
                cycle=MarketSession(date(2026, 8, 21)).to_payload(),
                mode="champion",
                idempotency_key="issue-20-vault-revalidation",
            )
    else:
        assert consumer == "status"
        status = configure_status(sources, repository_root=REPOSITORY_ROOT)
        assert isinstance(status, Status)
        with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
            status()


def test_status_requires_each_artifact_to_resolve_to_its_pinned_capture_intent(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    first = configure_advance(
        sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        clock=_FixedClock(),
    )
    assert isinstance(first, Advance)
    first_receipt = first(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="first-observation-run",
    )
    assert first_receipt.pinned_run_identity is not None

    later_evidence = recorded_evidence()
    evidence_item(later_evidence, 0)["first_observed_at"] = "2026-08-21T19:20:00.000000+00:00"
    second = configure_advance(
        sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=later_evidence,
        clock=_FixedClock(),
    )
    assert isinstance(second, Advance)
    second(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="second-observation-run",
    )

    intents = state_root / "evidence-vault" / "intents"
    outcomes = state_root / "evidence-vault" / "outcomes"
    first_market_intent: str | None = None
    for path in intents.iterdir():
        payload = json.loads(path.read_bytes())
        request = payload["request"]
        if (
            payload["run_id"] == first_receipt.pinned_run_identity.run_id
            and request["kind"] == "market"
        ):
            first_market_intent = payload["intent_id"]
            break
    assert first_market_intent is not None
    (outcomes / f"{first_market_intent}.json").unlink()

    status = configure_status(sources, repository_root=REPOSITORY_ROOT)
    assert isinstance(status, Status)
    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        status()


def test_status_reconstructs_each_run_with_its_pinned_historical_evidence_policy(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    first_configuration = runtime_configuration(state_root)
    first_sources = (ConfigurationSource("first", first_configuration),)
    first = configure_advance(
        first_sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        clock=_FixedClock(),
    )
    assert isinstance(first, Advance)
    first_receipt = first(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="historical-policy-first",
    )
    assert first_receipt.disposition is AdvanceDisposition.ADVANCED

    changed_configuration = deepcopy(first_configuration)
    policy = mutable_mapping(changed_configuration["evidence_policy"])
    requests = mutable_mapping_list(policy["requests"])
    requests[0]["maximum_age_seconds"] = 7100
    changed_sources = (ConfigurationSource("changed", changed_configuration),)
    second = configure_advance(
        changed_sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        clock=_FixedClock(),
    )
    assert isinstance(second, Advance)
    second_receipt = second(
        cycle=MarketSession(date(2026, 8, 22)).to_payload(),
        mode="champion",
        idempotency_key="historical-policy-second",
    )
    assert second_receipt.disposition is AdvanceDisposition.ADVANCED

    status = configure_status(changed_sources, repository_root=REPOSITORY_ROOT)
    assert isinstance(status, Status)
    rebuilt = status()

    assert rebuilt.pinned_run_identity is not None
    assert rebuilt.pinned_run_identity.cycle == MarketSession(date(2026, 8, 22))
    assert (
        len(tuple((state_root / "evidence-vault" / "policies").iterdir()))
        == EXPECTED_HISTORICAL_POLICIES
    )


def test_required_evidence_refusal_is_durable_and_replays_without_recapture(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    unavailable = recorded_evidence()
    market = evidence_item(unavailable, 0)
    market.update(
        {
            "status": "unavailable",
            "source_event_at": None,
            "published_at": None,
            "entity_mappings": [],
            "entity_catalog_ids": [],
            "content": None,
        }
    )
    configured = configure_advance(
        (ConfigurationSource("test", runtime_configuration(state_root)),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=unavailable,
        clock=_FixedClock(),
    )
    assert isinstance(configured, Advance)

    receipt = configured(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="issue-20-refusal",
    )

    assert receipt.disposition is AdvanceDisposition.FAILED_CLOSED
    assert receipt.failure_reason is AdvanceFailureReason.EVIDENCE_CAPTURE_FAILED
    assert len(receipt.evidence_artifact_ids) == 1
    assert len(receipt.evidence_refusal_ids) == 1
    assert parse_advance_receipt(receipt.to_payload()) == receipt
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone() == (4,)
        refusal = connection.execute(
            "SELECT evidence_artifact_ids, evidence_refusal_ids FROM advance_refusals"
        ).fetchone()
    assert refusal is not None
    assert refusal[0] != "[]"
    assert refusal[1] != "[]"
    outcome_count = len(tuple((state_root / "evidence-vault" / "outcomes").iterdir()))

    replay_capability = configure_advance(
        (ConfigurationSource("test", runtime_configuration(state_root)),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        clock=_FixedClock(),
    )
    assert isinstance(replay_capability, Advance)
    replay = replay_capability(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="issue-20-refusal",
    )

    assert replay == receipt
    assert len(tuple((state_root / "evidence-vault" / "outcomes").iterdir())) == outcome_count


def test_evidence_refusal_retry_with_changed_pinned_inputs_returns_a_typed_conflict(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    unavailable = recorded_evidence()
    market = evidence_item(unavailable, 0)
    market.update(
        {
            "status": "unavailable",
            "source_event_at": None,
            "published_at": None,
            "entity_mappings": [],
            "entity_catalog_ids": [],
            "content": None,
        }
    )
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    first = configure_advance(
        sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=unavailable,
        clock=_FixedClock(),
    )
    assert isinstance(first, Advance)
    refused = first(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="changed-evidence-refusal",
    )
    assert refused.failure_reason is AdvanceFailureReason.EVIDENCE_CAPTURE_FAILED

    changed_universe = deepcopy(recorded_universe())
    changed_universe["evidence_cutoff"] = "2026-08-21T20:30:00.000000+00:00"
    retry = configure_advance(
        sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=changed_universe,
        recorded_evidence=recorded_evidence(),
        clock=_FixedClock(),
    )
    assert isinstance(retry, Advance)

    conflict = retry(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="changed-evidence-refusal",
    )

    assert conflict.disposition is AdvanceDisposition.FAILED_CLOSED
    assert conflict.failure_reason is AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
    assert conflict.evidence_artifact_ids == ()
    assert conflict.evidence_refusal_ids == ()


def test_evidence_refusal_retry_rejects_a_changed_durable_policy_reference(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    unavailable = recorded_evidence()
    market = evidence_item(unavailable, 0)
    market.update(
        {
            "status": "unavailable",
            "source_event_at": None,
            "published_at": None,
            "entity_mappings": [],
            "entity_catalog_ids": [],
            "content": None,
        }
    )
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    configured = configure_advance(
        sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=unavailable,
        clock=_FixedClock(),
    )
    assert isinstance(configured, Advance)
    receipt = configured(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="changed-durable-policy",
    )
    assert receipt.evidence_policy_id is not None

    retry = configure_advance(
        sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        clock=_FixedClock(),
    )
    assert isinstance(retry, Advance)
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        connection.execute("DROP TRIGGER advance_refusals_are_append_only_update")
        connection.execute(
            f"UPDATE advance_refusals SET evidence_policy_id = '{'f' * 64}' "  # noqa: S608
            "WHERE idempotency_key = 'changed-durable-policy'"
        )

    with pytest.raises(InvalidEvidenceError, match="invalid evidence capture"):
        retry(
            cycle=MarketSession(date(2026, 8, 21)).to_payload(),
            mode="champion",
            idempotency_key="changed-durable-policy",
        )


def test_changed_evidence_refusal_retry_ignores_unrelated_corrupt_history(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    healthy = configure_advance(
        sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        clock=_FixedClock(),
    )
    assert isinstance(healthy, Advance)
    healthy(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="unrelated-history",
    )

    unavailable = recorded_evidence()
    market = evidence_item(unavailable, 0)
    market.update(
        {
            "status": "unavailable",
            "source_event_at": None,
            "published_at": None,
            "entity_mappings": [],
            "entity_catalog_ids": [],
            "content": None,
        }
    )
    refused_capability = configure_advance(
        sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=unavailable,
        clock=_FixedClock(),
    )
    assert isinstance(refused_capability, Advance)
    refused_capability(
        cycle=MarketSession(date(2026, 8, 22)).to_payload(),
        mode="champion",
        idempotency_key="changed-refusal-with-unrelated-history",
    )
    changed_universe = deepcopy(recorded_universe())
    changed_universe["evidence_cutoff"] = "2026-08-21T20:30:00.000000+00:00"
    retry = configure_advance(
        sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=changed_universe,
        recorded_evidence=recorded_evidence(),
        clock=_FixedClock(),
    )
    assert isinstance(retry, Advance)
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        connection.execute("DROP TRIGGER lifecycle_events_are_append_only_update")
        connection.execute(
            "UPDATE lifecycle_events SET event_envelope = '{}' "
            "WHERE idempotency_key = 'unrelated-history' AND sequence = 1"
        )

    conflict = retry(
        cycle=MarketSession(date(2026, 8, 22)).to_payload(),
        mode="champion",
        idempotency_key="changed-refusal-with-unrelated-history",
    )

    assert conflict.failure_reason is AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT


def test_evidence_refusal_retry_fails_closed_when_its_own_history_is_corrupt(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    sources = (ConfigurationSource("test", runtime_configuration(state_root)),)
    unavailable = recorded_evidence()
    market = evidence_item(unavailable, 0)
    market.update(
        {
            "status": "unavailable",
            "source_event_at": None,
            "published_at": None,
            "entity_mappings": [],
            "entity_catalog_ids": [],
            "content": None,
        }
    )
    refused_capability = configure_advance(
        sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=unavailable,
        clock=_FixedClock(),
    )
    assert isinstance(refused_capability, Advance)
    refused_capability(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="refusal-with-corrupt-own-history",
    )
    retry = configure_advance(
        sources,
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=recorded_evidence(),
        clock=_FixedClock(),
    )
    assert isinstance(retry, Advance)
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        connection.execute("DROP TRIGGER lifecycle_events_are_append_only_update")
        connection.execute(
            "UPDATE lifecycle_events SET event_envelope = '{}' "
            "WHERE idempotency_key = 'refusal-with-corrupt-own-history' AND sequence = 1"
        )

    receipt = retry(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="refusal-with-corrupt-own-history",
    )

    assert receipt.failure_reason is AdvanceFailureReason.INVALID_DURABLE_STATE
    assert receipt.evidence_artifact_ids == ()
    assert receipt.evidence_refusal_ids == ()


def test_evidence_failure_row_cannot_claim_a_complete_capture(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    unavailable = recorded_evidence()
    market = evidence_item(unavailable, 0)
    market.update(
        {
            "status": "unavailable",
            "source_event_at": None,
            "published_at": None,
            "entity_mappings": [],
            "entity_catalog_ids": [],
            "content": None,
        }
    )
    configured = configure_advance(
        (ConfigurationSource("test", runtime_configuration(state_root)),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=unavailable,
        clock=_FixedClock(),
    )
    assert isinstance(configured, Advance)
    configured(
        cycle=MarketSession(date(2026, 8, 21)).to_payload(),
        mode="champion",
        idempotency_key="issue-20-corrupt-refusal",
    )
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT refusal_id, idempotency_key, cycle_identity, reason_code, "
            "evidence_policy_id, evidence_artifact_ids, evidence_refusal_ids, "
            "attention_refusal_reason, recorded_at "
            "FROM advance_refusals"
        ).fetchone()
        assert row is not None
        connection.execute("DROP TABLE advance_refusals")
        connection.execute(
            "CREATE TABLE advance_refusals "
            "(refusal_id, idempotency_key, cycle_identity, reason_code, "
            "evidence_policy_id, evidence_artifact_ids, evidence_refusal_ids, "
            "attention_refusal_reason, recorded_at)"
        )
        connection.execute(
            "INSERT INTO advance_refusals VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?)",
            (row[0], row[1], row[2], row[3], row[4], row[5], row[7], row[8]),
        )

    ledger = configured.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    with pytest.raises(
        InvalidLifecycleStateError,
        match="invalid idempotency_key in lifecycle refusal ledger",
    ):
        ledger.rebuild_status()
