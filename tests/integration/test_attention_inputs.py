from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import pytest

from agentic_investment_os.adapters.filesystem_evidence import FilesystemEvidenceVault
from agentic_investment_os.adapters.recorded_evidence import RecordedEvidenceSource
from agentic_investment_os.domain.attention import (
    AttentionFeature,
    AttentionInputs,
    AttentionRefusalReason,
)
from agentic_investment_os.domain.lifecycle import AdvanceRequest
from agentic_investment_os.evidence.attention import BuildAttentionInputs
from agentic_investment_os.evidence.capture import (
    CaptureEvidence,
    CaptureIntent,
    CaptureOutcome,
    EvidencePolicy,
    EvidenceStoredRecord,
)
from tests._evidence import evidence_policy, recorded_evidence
from tests._universe import pinned_run_identity, universe_snapshot

if TYPE_CHECKING:
    from pathlib import Path

    from agentic_investment_os.domain.identity import MarketSession
    from agentic_investment_os.domain.temporal import UtcInstant
    from agentic_investment_os.domain.universe import UniverseSnapshot


@dataclass
class _VaultView:
    delegate: FilesystemEvidenceVault
    records: tuple[EvidenceStoredRecord, ...] | None = None
    omit_outcome: bool = False

    def append_policy(
        self,
        policy: EvidencePolicy,
        capture_intents: tuple[CaptureIntent, ...],
    ) -> None:
        self.delegate.append_policy(policy, capture_intents)

    def load_policy(self, policy_id: str) -> EvidencePolicy:
        return self.delegate.load_policy(policy_id)

    def append_intent(self, intent: CaptureIntent) -> None:
        self.delegate.append_intent(intent)

    def load_outcome(self, intent: CaptureIntent) -> CaptureOutcome | None:
        if self.omit_outcome:
            return None
        return self.delegate.load_outcome(intent)

    def append_outcome(
        self,
        intent: CaptureIntent,
        outcome: CaptureOutcome,
        content: bytes | None,
    ) -> None:
        self.delegate.append_outcome(intent, outcome, content)

    def stored_records(self) -> tuple[EvidenceStoredRecord, ...]:
        return self.delegate.stored_records()

    def stored_records_for_artifacts(
        self,
        artifact_ids: tuple[str, ...],
    ) -> tuple[EvidenceStoredRecord, ...]:
        if self.records is not None:
            return self.records
        return self.delegate.stored_records_for_artifacts(artifact_ids)


@dataclass(frozen=True)
class _CapturedAttention:
    vault: FilesystemEvidenceVault
    builder: BuildAttentionInputs
    run_id: str
    cycle: MarketSession
    snapshot: UniverseSnapshot
    cutoff: UtcInstant
    data_regime: str
    policy_id: str
    artifact_ids: tuple[str, ...]

    def build(
        self,
        *,
        builder: BuildAttentionInputs | None = None,
        snapshot: UniverseSnapshot | None = None,
        data_regime: str | None = None,
        artifact_ids: tuple[str, ...] | None = None,
    ) -> AttentionInputs | AttentionRefusalReason:
        selected_snapshot = self.snapshot if snapshot is None else snapshot
        return (self.builder if builder is None else builder)(
            run_id=self.run_id,
            cycle=self.cycle,
            universe_snapshot=selected_snapshot,
            cutoff=self.cutoff,
            data_regime=self.data_regime if data_regime is None else data_regime,
            evidence_policy_id=self.policy_id,
            evidence_artifact_ids=self.artifact_ids if artifact_ids is None else artifact_ids,
        )


def _capture(tmp_path: Path) -> _CapturedAttention:
    request = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key="attention-inputs",
    )
    assert isinstance(request, AdvanceRequest)
    identity = pinned_run_identity(request)
    snapshot = universe_snapshot(identity)
    policy = EvidencePolicy.parse(evidence_policy())
    assert policy is not None
    evidence = recorded_evidence()
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")
    summary = CaptureEvidence(
        policy=policy,
        source=RecordedEvidenceSource(evidence, None, policy.data_regime),
        vault=vault,
    )(
        run_id=identity.run_id,
        universe_snapshot_id=snapshot.snapshot_id,
        cutoff=identity.evidence_cutoff,
        data_regime=identity.data_regime,
    )
    return _CapturedAttention(
        vault,
        BuildAttentionInputs(vault),
        identity.run_id,
        request.session,
        snapshot,
        identity.evidence_cutoff,
        identity.data_regime,
        policy.policy_id,
        summary.artifact_ids,
    )


def test_builder_preserves_optional_absence_and_excluded_attention_subjects(
    tmp_path: Path,
) -> None:
    captured = _capture(tmp_path)

    result = captured.build()

    assert isinstance(result, AttentionInputs)
    aapl = next(
        item for item in result.subjects if item.subject.identity.catalog_id == "equity-aapl"
    )
    assert AttentionFeature.NEWS_ARRIVAL in aapl.observed_features
    assert AttentionFeature.FILING_ARRIVAL in aapl.missing_features
    assert AttentionFeature.PRICE_CHANGE in aapl.missing_features
    assert any(
        not item.subject.eligible_for_new_entry and not item.subject.is_position
        for item in result.subjects
    )


@pytest.mark.parametrize(
    ("market_closes", "expected_feature"),
    [
        (("225.50", "225.50"), None),
        (("225.50", "226.00"), AttentionFeature.PRICE_CHANGE),
    ],
)
def test_builder_derives_price_change_only_from_two_valid_observations(
    tmp_path: Path,
    market_closes: tuple[str, ...],
    expected_feature: AttentionFeature | None,
) -> None:
    captured = _capture(tmp_path)
    records = list(captured.vault.stored_records_for_artifacts(captured.artifact_ids))
    market = next(record for record in records if record.artifact.kind.value == "market")
    content: object = json.loads(market.content)
    assert isinstance(content, dict)
    bars = content["bars"]
    assert isinstance(bars, list)
    first = bars[0]
    assert isinstance(first, dict)
    bars.clear()
    for index, close in enumerate(market_closes):
        bar = deepcopy(first)
        bar["close"] = close
        bar["timestamp"] = f"2026-08-21T19:{index:02d}:00.000000+00:00"
        bars.append(bar)
    object.__setattr__(
        market,
        "content",
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode(),
    )

    result = captured.build(
        builder=BuildAttentionInputs(_VaultView(captured.vault, records=tuple(records)))
    )

    assert isinstance(result, AttentionInputs)
    aapl = next(
        item for item in result.subjects if item.subject.identity.catalog_id == "equity-aapl"
    )
    assert (AttentionFeature.PRICE_CHANGE in aapl.observed_features) is (
        expected_feature is AttentionFeature.PRICE_CHANGE
    )
    assert (AttentionFeature.PRICE_CHANGE in aapl.missing_features) is False


def test_builder_fails_closed_on_checkpoint_and_snapshot_mismatches(tmp_path: Path) -> None:
    captured = _capture(tmp_path)
    assert captured.build(data_regime="alpaca:iex") is AttentionRefusalReason.CONTRADICTORY_EVIDENCE
    assert (
        captured.build(artifact_ids=captured.artifact_ids[:-1])
        is AttentionRefusalReason.CONTRADICTORY_EVIDENCE
    )
    assert (
        captured.build(builder=BuildAttentionInputs(_VaultView(captured.vault, omit_outcome=True)))
        is AttentionRefusalReason.CORRUPT_EVIDENCE
    )
    assert (
        captured.build(builder=BuildAttentionInputs(_VaultView(captured.vault, records=())))
        is AttentionRefusalReason.MISSING_EVIDENCE
    )
    assert (
        captured.build(snapshot=replace(captured.snapshot, run_id="f" * 64))
        is AttentionRefusalReason.CONTRADICTORY_EVIDENCE
    )


@pytest.mark.parametrize(
    "content",
    [
        b"not-json",
        b"[]",
        b'{"bars":{}}',
        b'{"bars":[1]}',
        (
            b'{"bars":[{"asset_id":"equity-aapl","close":"bad",'
            b'"timestamp":"2026-08-21T19:00:00.000000+00:00"}]}'
        ),
        (
            b'{"bars":[{"asset_id":"equity-aapl","close":"NaN",'
            b'"timestamp":"2026-08-21T19:00:00.000000+00:00"}]}'
        ),
    ],
)
def test_builder_rejects_corrupt_market_content_from_its_typed_vault_boundary(
    tmp_path: Path,
    content: bytes,
) -> None:
    captured = _capture(tmp_path)
    records = list(captured.vault.stored_records_for_artifacts(captured.artifact_ids))
    market = next(record for record in records if record.artifact.kind.value == "market")
    object.__setattr__(market, "content", content)

    result = captured.build(
        builder=BuildAttentionInputs(_VaultView(captured.vault, records=tuple(records)))
    )

    assert result is AttentionRefusalReason.CORRUPT_EVIDENCE
