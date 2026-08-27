from __future__ import annotations

import sqlite3
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agentic_investment_os.application.lifecycle import Advance
from agentic_investment_os.domain.attention import (
    AdapterQuotaDisposition,
    AttentionFeature,
    AttentionRefusalReason,
    AttentionState,
    DossierSelectionKind,
)
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.lifecycle import (
    AdvanceAttempt,
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRecovery,
    IdempotencyKey,
    LifecycleCommand,
    LifecycleDecision,
    LifecycleLedger,
    PerformAttentionSelection,
)
from agentic_investment_os.entrypoints.configuration import ConfigurationSource
from agentic_investment_os.entrypoints.lifecycle import configure_advance
from tests._evidence import recorded_evidence
from tests._governance import RecordedSessionEligibility
from tests._production_research import ValidProductionModel, production_recorded_evidence
from tests._universe import recorded_universe, runtime_configuration

if TYPE_CHECKING:
    from agentic_investment_os.domain.attention import AttentionInputs
    from agentic_investment_os.domain.governance import ConstitutionUse
    from agentic_investment_os.domain.temporal import UtcInstant
    from agentic_investment_os.domain.universe import UniverseSnapshot

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_EVENT_COUNT = 9
EXPECTED_SELECTED_SUBJECT_COUNT = 2


@dataclass(frozen=True)
class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 21, 22, tzinfo=UTC)


@dataclass(frozen=True)
class _RefusingAttentionInputs:
    reason: AttentionRefusalReason

    def __call__(  # noqa: PLR0913 - mirror the application boundary exactly.
        self,
        *,
        run_id: str,
        cycle: MarketSession,
        universe_snapshot: UniverseSnapshot,
        cutoff: UtcInstant,
        data_regime: str,
        evidence_policy_id: str,
        evidence_artifact_ids: tuple[str, ...],
    ) -> AttentionInputs | AttentionRefusalReason:
        _ = (
            run_id,
            cycle,
            universe_snapshot,
            cutoff,
            data_regime,
            evidence_policy_id,
            evidence_artifact_ids,
        )
        return self.reason


@dataclass
class _CorruptEvidenceBeforeAttention:
    delegate: LifecycleLedger
    vault_root: Path
    corrupted: bool = False

    def pinned_constitution_use(
        self,
        idempotency_key: IdempotencyKey,
    ) -> ConstitutionUse | None:
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
        if isinstance(decision, PerformAttentionSelection) and not self.corrupted:
            content_path = min((self.vault_root / "contents").iterdir())
            content_path.write_bytes(b"corrupt")
            self.corrupted = True
        return decision


def _configure(
    state_root: Path,
    *,
    configuration: dict[str, object] | None = None,
    evidence: dict[str, object] | None = None,
) -> Advance:
    configured = configure_advance(
        (
            ConfigurationSource(
                "test",
                runtime_configuration(state_root) if configuration is None else configuration,
            ),
        ),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=production_recorded_evidence() if evidence is None else evidence,
        recorded_model=ValidProductionModel(cio_stance="abstain"),
        session_eligibility=RecordedSessionEligibility(),
        clock=_FixedClock(),
    )
    assert isinstance(configured, Advance)
    return configured


def _advance(capability: Advance, cycle: date, key: str) -> AdvanceReceipt:
    return capability(
        cycle=MarketSession(cycle).to_payload(),
        mode="champion",
        idempotency_key=key,
    )


def test_attention_selection_publishes_progression_refresh_and_exploration_idempotently(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)

    monday = _advance(capability, date(2026, 8, 21), "attention-monday")
    tuesday = _advance(capability, date(2026, 8, 22), "attention-tuesday")
    wednesday = _advance(capability, date(2026, 8, 23), "attention-wednesday")
    replay = _advance(capability, date(2026, 8, 23), "attention-wednesday")

    assert monday.attention_artifact is not None
    assert tuesday.attention_artifact is not None
    assert wednesday.attention_artifact is not None
    assert replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED
    assert replay.attention_artifact == wednesday.attention_artifact
    assert replay.evidence_artifact_ids == wednesday.evidence_artifact_ids
    assert tuple(card.next_state for card in monday.attention_artifact.candidate_cards) == (
        AttentionState.WATCH,
        AttentionState.WATCH,
    )
    assert tuple(card.next_state for card in tuesday.attention_artifact.candidate_cards) == (
        AttentionState.CANDIDATE,
        AttentionState.CANDIDATE,
    )
    assert all(
        card.next_state is AttentionState.DOSSIER
        for card in wednesday.attention_artifact.candidate_cards
    )
    assert {
        request.selection_kind for request in wednesday.attention_artifact.dossier_requests
    } == {DossierSelectionKind.PRIORITY, DossierSelectionKind.EXPLORATION}
    assert len(wednesday.attention_artifact.holding_refreshes) == 1
    accounting = wednesday.attention_artifact.resource_accounting
    assert accounting.candidate_card_count == EXPECTED_SELECTED_SUBJECT_COUNT
    assert accounting.new_dossier_count == EXPECTED_SELECTED_SUBJECT_COUNT
    assert accounting.holding_refresh_count == 1
    assert accounting.weekly_exploration_after == 1
    assert accounting.model_tokens == 0
    assert accounting.model_turns == 0
    assert accounting.adapter_quota_disposition is AdapterQuotaDisposition.NOT_CONSULTED
    aapl = next(
        card
        for card in monday.attention_artifact.candidate_cards
        if card.identity.catalog_id == "equity-aapl"
    )
    assert AttentionFeature.NEWS_ARRIVAL not in aapl.missing_features
    assert AttentionFeature.FILING_ARRIVAL in aapl.missing_features
    assert aapl.evidence_artifact_ids
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM lifecycle_events WHERE idempotency_key = ?",
            ("attention-wednesday",),
        ).fetchone() == (EXPECTED_EVENT_COUNT,)


@pytest.mark.parametrize("reason", tuple(AttentionRefusalReason))
def test_attention_input_refusals_are_durable_specific_and_idempotent(
    tmp_path: Path,
    reason: AttentionRefusalReason,
) -> None:
    state_root = tmp_path / reason.value
    configured = _configure(state_root)
    capability = replace(configured, attention_inputs=_RefusingAttentionInputs(reason))

    receipt = _advance(capability, date(2026, 8, 21), f"attention-{reason.value}")
    replay = _advance(capability, date(2026, 8, 21), f"attention-{reason.value}")

    assert receipt.disposition is AdvanceDisposition.FAILED_CLOSED
    assert receipt.failure_reason is AdvanceFailureReason.ATTENTION_SELECTION_FAILED
    assert receipt.attention_refusal_reason is reason
    assert receipt.attention_artifact is None
    assert replay == receipt
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT attention_refusal_reason FROM advance_refusals"
        ).fetchall() == [(reason.value,)]


def test_corrupt_vault_content_fails_closed_before_attention_publication_and_replays(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "corrupt-vault"
    configured = _configure(state_root)
    corrupting = _CorruptEvidenceBeforeAttention(
        configured.ledger,
        state_root / "evidence-vault",
    )
    capability = replace(configured, ledger=corrupting)

    receipt = _advance(capability, date(2026, 8, 21), "attention-corrupt-vault")
    replay = _advance(capability, date(2026, 8, 21), "attention-corrupt-vault")

    assert receipt.disposition is AdvanceDisposition.FAILED_CLOSED
    assert receipt.attention_refusal_reason is AttentionRefusalReason.CORRUPT_EVIDENCE
    assert replay == receipt
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM lifecycle_events WHERE event_kind = 'attention_selected'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT attention_refusal_reason FROM advance_refusals"
        ).fetchall() == [(AttentionRefusalReason.CORRUPT_EVIDENCE.value,)]


def test_conflicting_market_captures_fail_closed_before_attention_publication(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "contradictory-market"
    configuration = runtime_configuration(state_root)
    evidence = recorded_evidence()
    policy = configuration["evidence_policy"]
    items = evidence["items"]
    assert isinstance(policy, dict)
    requests = policy["requests"]
    assert isinstance(requests, list)
    assert isinstance(items, list)
    second_request = deepcopy(requests[0])
    second_item = deepcopy(items[0])
    assert isinstance(second_request, dict)
    assert isinstance(second_item, dict)
    second_request["retrieval_identity"] = "market-session-bars-secondary"
    second_item["retrieval_identity"] = "market-session-bars-secondary"
    content = second_item["content"]
    assert isinstance(content, dict)
    bars = content["bars"]
    assert isinstance(bars, list)
    bar = bars[0]
    assert isinstance(bar, dict)
    bar["close"] = "226.00"
    requests.append(second_request)
    items.append(second_item)
    capability = _configure(state_root, configuration=configuration, evidence=evidence)

    receipt = _advance(capability, date(2026, 8, 21), "attention-contradictory-market")
    replay = _advance(capability, date(2026, 8, 21), "attention-contradictory-market")

    assert receipt.disposition is AdvanceDisposition.FAILED_CLOSED
    assert receipt.failure_reason is AdvanceFailureReason.ATTENTION_SELECTION_FAILED
    assert receipt.attention_refusal_reason is AttentionRefusalReason.CONTRADICTORY_EVIDENCE
    assert receipt.attention_artifact is None
    assert replay == receipt
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM lifecycle_events WHERE event_kind = 'attention_selected'"
        ).fetchone() == (0,)
