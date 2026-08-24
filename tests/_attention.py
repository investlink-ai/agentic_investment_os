from __future__ import annotations

from typing import TYPE_CHECKING, cast

from agentic_investment_os.domain.attention import (
    AttentionArtifact,
    AttentionFeature,
    AttentionInputs,
    AttentionPolicy,
    AttentionSubjectInput,
    select_attention,
)
from tests._universe import attention_policy

if TYPE_CHECKING:
    from agentic_investment_os.domain.identity import MarketSession
    from agentic_investment_os.domain.lifecycle import (
        EvidenceCaptureCheckpoint,
        PinnedRunIdentity,
    )
    from agentic_investment_os.domain.temporal import UtcInstant
    from agentic_investment_os.domain.universe import UniverseSnapshot


def typed_attention_policy() -> AttentionPolicy:
    """Return the validated baseline attention policy used by lifecycle fixtures."""
    parsed = AttentionPolicy.parse(attention_policy())
    assert parsed is not None
    return parsed


def attention_inputs(
    identity: PinnedRunIdentity,
    snapshot: UniverseSnapshot,
    evidence: EvidenceCaptureCheckpoint,
) -> AttentionInputs:
    """Build deterministic local attention inputs for a completed evidence fixture."""
    return attention_inputs_for_snapshot(
        run_id=identity.run_id,
        cycle=cast("MarketSession", identity.cycle),
        snapshot=snapshot,
        cutoff=identity.evidence_cutoff,
        data_regime=identity.data_regime,
        evidence=evidence,
    )


def attention_inputs_for_snapshot(  # noqa: PLR0913 - mirror the application boundary.
    *,
    run_id: str,
    cycle: MarketSession,
    snapshot: UniverseSnapshot,
    cutoff: UtcInstant,
    data_regime: str,
    evidence: EvidenceCaptureCheckpoint,
) -> AttentionInputs:
    """Build fixture inputs from the explicit application-boundary facts."""
    subjects = tuple(
        AttentionSubjectInput(
            subject=subject,
            observed_features=(
                (AttentionFeature.CURRENT_HOLDING, AttentionFeature.FRESHNESS)
                if subject.is_position
                else (AttentionFeature.FRESHNESS,)
            ),
            missing_features=tuple(
                feature
                for feature in AttentionFeature
                if feature
                not in (
                    AttentionFeature.FRESHNESS,
                    *((AttentionFeature.CURRENT_HOLDING,) if subject.is_position else ()),
                )
            ),
            evidence_artifact_ids=evidence.artifact_ids,
        )
        for subject in snapshot.subjects
    )
    return AttentionInputs(
        run_id=run_id,
        cycle=cycle,
        universe_snapshot_id=snapshot.snapshot_id,
        cutoff=cutoff,
        data_regime=data_regime,
        evidence_policy_id=evidence.policy_id,
        evidence_artifact_ids=evidence.artifact_ids,
        subjects=subjects,
    )


def attention_artifact(
    identity: PinnedRunIdentity,
    snapshot: UniverseSnapshot,
    evidence: EvidenceCaptureCheckpoint,
    history: tuple[AttentionArtifact, ...] = (),
) -> AttentionArtifact:
    """Select the baseline artifact bound to one lifecycle test checkpoint."""
    return select_attention(
        typed_attention_policy(),
        attention_inputs(identity, snapshot, evidence),
        history,
    )
