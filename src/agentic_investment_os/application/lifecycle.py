"""Advance and report a Market Session through durable lifecycle capabilities."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, assert_never

from agentic_investment_os.domain.attention import (
    AttentionArtifact,
    AttentionInputs,
    AttentionPolicy,
    AttentionRefusalReason,
    InvalidAttentionError,
    select_attention,
)
from agentic_investment_os.domain.governance import ACTIVE_CONSTITUTION, ConstitutionArtifact
from agentic_investment_os.domain.identity import (
    AssetClass,
    CryptoDecisionWindow,
    MarketSession,
    parse_decision_cycle_identity,
)
from agentic_investment_os.domain.lifecycle import (
    AdvanceAttempt,
    AdvanceCommand,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRequest,
    AppendLifecycleRecord,
    AppendTerminalLifecycleRecord,
    EvidenceCaptureCheckpoint,
    InputRefusal,
    InputRefusalCode,
    InvalidLifecycleStateError,
    LifecycleCommand,
    LifecycleLedger,
    LifecyclePersistenceError,
    LifecycleStatus,
    LifecycleStatusProjection,
    PerformAttentionSelection,
    PerformEvidenceCapture,
    PinnedRunIdentity,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.domain.universe import (
    EquityUniversePolicy,
    UniverseInputIdentity,
    UniverseInputs,
    UniverseInputSource,
    UniverseRefusal,
    UniverseRefusalCode,
    UniverseSnapshot,
    build_universe_snapshot,
)
from agentic_investment_os.evidence.capture import (
    EvidencePersistenceError,
    InvalidEvidenceError,
)

if TYPE_CHECKING:
    from datetime import datetime

    from agentic_investment_os.application.governance import ConstitutionStatus
    from agentic_investment_os.evidence.capture import (
        EvidenceCaptureCapability,
        EvidenceReferenceValidator,
    )

__all__ = ("Advance", "Clock", "ConstitutionResolver", "Status")


_INCOMPLETE_CHECKPOINT_RESULT = "lifecycle ledger returned an incomplete checkpoint result"
_CLOCK_INVALID = "lifecycle clock must return a timezone-aware instant representable in UTC"
_UNIVERSE_SOURCE_INVALID = "universe source returned a noncanonical absolute instant"


class Clock(Protocol):
    """Supply an aware timestamp at the composition boundary."""

    def now(self) -> datetime: ...


class AttentionInputsCapability(Protocol):
    """Derive the approved local feature set from one captured evidence checkpoint."""

    def __call__(  # noqa: PLR0913 - every pinned evidence fact remains explicit.
        self,
        *,
        run_id: str,
        cycle: MarketSession,
        universe_snapshot: UniverseSnapshot,
        cutoff: UtcInstant,
        data_regime: str,
        evidence_policy_id: str,
        evidence_artifact_ids: tuple[str, ...],
    ) -> AttentionInputs | AttentionRefusalReason: ...


class ConstitutionResolver(Protocol):
    """Resolve the exact immutable Constitution for one Market Session."""

    def resolve(self, session: MarketSession, recorded_at: UtcInstant) -> ConstitutionArtifact: ...


@dataclass(frozen=True, slots=True)
class _ActiveDocumentConstitution:
    def resolve(self, session: MarketSession, recorded_at: UtcInstant) -> ConstitutionArtifact:
        del session, recorded_at
        return ACTIVE_CONSTITUTION


@dataclass(frozen=True, slots=True)
class Advance:
    """Advance or resume one validated Decision Cycle through bounded attention."""

    ledger: LifecycleLedger
    configuration_version: int
    configuration_hash: str
    universe_source: UniverseInputSource
    enabled_asset_classes: tuple[AssetClass, ...]
    universe_policy: EquityUniversePolicy
    evidence_capture: EvidenceCaptureCapability
    attention_policy: AttentionPolicy
    attention_inputs: AttentionInputsCapability
    clock: Clock
    constitution_registry: ConstitutionResolver = _ActiveDocumentConstitution()

    def __call__(  # noqa: PLR0912 - exhaust each typed lifecycle decision.
        self,
        *,
        cycle: object,
        mode: object,
        idempotency_key: object,
    ) -> AdvanceReceipt:
        parsed_cycle = parse_decision_cycle_identity(cycle)
        if type(parsed_cycle) is CryptoDecisionWindow:
            return AdvanceReceipt.failed_closed(
                AdvanceFailureReason.UNSUPPORTED_CYCLE,
                cycle=parsed_cycle,
            )
        if type(parsed_cycle) is not MarketSession:
            return AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_SESSION)
        parsed = AdvanceRequest.parse(
            session=parsed_cycle,
            mode=mode,
            idempotency_key=idempotency_key,
        )
        try:
            recorded_at = UtcInstant.from_datetime(self.clock.now())
        except InvalidUtcInstantError as error:
            raise LifecyclePersistenceError(_CLOCK_INVALID) from error
        command = self._prepare_command(parsed, recorded_at)
        attempt = AdvanceAttempt()
        while True:
            decision = self.ledger.advance_step(command, attempt, recorded_at)
            if isinstance(decision, AdvanceReceipt):
                self._validate_evidence_receipt(command, decision)
                return decision
            if isinstance(decision, AppendTerminalLifecycleRecord):
                self._validate_evidence_receipt(command, decision.receipt)
                return decision.receipt
            if isinstance(decision, AppendLifecycleRecord):
                if decision.attempt.last_sequence is None or (
                    attempt.last_sequence is not None
                    and decision.attempt.last_sequence <= attempt.last_sequence
                ):
                    raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
                attempt = decision.attempt
                continue
            if isinstance(decision, PerformEvidenceCapture):
                if not isinstance(command, AdvanceCommand):
                    raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
                summary = self.evidence_capture(
                    run_id=decision.pinned_run_identity.run_id,
                    universe_snapshot_id=decision.universe_snapshot.snapshot_id,
                    cutoff=decision.pinned_run_identity.evidence_cutoff,
                    data_regime=decision.pinned_run_identity.data_regime,
                )
                command = replace(
                    command,
                    evidence_capture=EvidenceCaptureCheckpoint(
                        summary.policy_id,
                        summary.artifact_ids,
                        summary.refusal_ids,
                    ),
                )
                continue
            if isinstance(decision, PerformAttentionSelection):
                if not isinstance(command, AdvanceCommand):
                    raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
                try:
                    self.evidence_capture.validate_checkpoint(
                        run_id=decision.pinned_run_identity.run_id,
                        universe_snapshot_id=decision.universe_snapshot.snapshot_id,
                        cutoff=decision.pinned_run_identity.evidence_cutoff,
                        data_regime=decision.pinned_run_identity.data_regime,
                        checkpoint=decision.evidence_capture,
                    )
                    attention_inputs = self.attention_inputs(
                        run_id=decision.pinned_run_identity.run_id,
                        cycle=command.request.session,
                        universe_snapshot=decision.universe_snapshot,
                        cutoff=decision.pinned_run_identity.evidence_cutoff,
                        data_regime=decision.pinned_run_identity.data_regime,
                        evidence_policy_id=decision.evidence_capture.policy_id,
                        evidence_artifact_ids=decision.evidence_capture.artifact_ids,
                    )
                except (EvidencePersistenceError, InvalidEvidenceError):
                    attention_inputs = AttentionRefusalReason.CORRUPT_EVIDENCE
                attention_selection: AttentionArtifact | AttentionRefusalReason
                if isinstance(attention_inputs, AttentionInputs):
                    try:
                        attention_selection = select_attention(
                            self.attention_policy,
                            attention_inputs,
                            decision.attention_history,
                            available_at=recorded_at,
                        )
                    except InvalidAttentionError:
                        attention_selection = AttentionRefusalReason.CONTRADICTORY_EVIDENCE
                else:
                    attention_selection = attention_inputs
                command = replace(command, attention_selection=attention_selection)
                continue
            # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
            assert_never(decision)  # pragma: no cover

    def _validate_evidence_receipt(
        self,
        command: LifecycleCommand,
        receipt: AdvanceReceipt,
    ) -> None:
        if not (receipt.evidence_artifact_ids or receipt.evidence_refusal_ids):
            return
        if not isinstance(command, AdvanceCommand):
            raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
        policy_id = receipt.evidence_policy_id
        if policy_id is None:  # pragma: no cover - receipt validation requires it with evidence.
            raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
        try:
            self.evidence_capture.validate_checkpoint(
                run_id=command.pinned_run_identity.run_id,
                universe_snapshot_id=command.universe_snapshot.snapshot_id,
                cutoff=command.pinned_run_identity.evidence_cutoff,
                data_regime=command.pinned_run_identity.data_regime,
                checkpoint=EvidenceCaptureCheckpoint(
                    policy_id,
                    receipt.evidence_artifact_ids,
                    receipt.evidence_refusal_ids,
                ),
            )
        except (EvidencePersistenceError, InvalidEvidenceError):
            if (
                receipt.failure_reason is AdvanceFailureReason.ATTENTION_SELECTION_FAILED
                and receipt.attention_refusal_reason is AttentionRefusalReason.CORRUPT_EVIDENCE
            ):
                return
            raise
        attention_artifact = receipt.attention_artifact
        if attention_artifact is None:
            return
        try:
            attention_inputs = self.attention_inputs(
                run_id=command.pinned_run_identity.run_id,
                cycle=command.request.session,
                universe_snapshot=command.universe_snapshot,
                cutoff=command.pinned_run_identity.evidence_cutoff,
                data_regime=command.pinned_run_identity.data_regime,
                evidence_policy_id=policy_id,
                evidence_artifact_ids=receipt.evidence_artifact_ids,
            )
        except EvidencePersistenceError as error:
            raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT) from error
        if (
            not isinstance(attention_inputs, AttentionInputs)
            or attention_artifact.attention_policy_id != self.attention_policy.policy_id
            or not attention_artifact.matches_inputs(attention_inputs, self.attention_policy)
        ):
            raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)

    def _prepare_command(
        self,
        parsed: AdvanceRequest | InputRefusal,
        recorded_at: UtcInstant,
    ) -> LifecycleCommand:
        if isinstance(parsed, InputRefusal):
            return parsed
        if isinstance(parsed, AdvanceRequest):
            constitution = self.constitution_registry.resolve(parsed.session, recorded_at)
            loaded = self.universe_source.load()
            if isinstance(loaded, UniverseRefusal):
                return InputRefusal(
                    _input_refusal_code(loaded.code),
                    parsed.idempotency_key,
                    parsed.session,
                )
            if isinstance(loaded, UniverseInputs):
                universe_identity = UniverseInputIdentity.from_inputs(
                    loaded,
                    self.universe_policy,
                )
                if isinstance(universe_identity, UniverseRefusal):
                    raise LifecyclePersistenceError(_UNIVERSE_SOURCE_INVALID)
                identity = PinnedRunIdentity.create(
                    parsed,
                    configuration_version=self.configuration_version,
                    configuration_hash=self.configuration_hash,
                    universe_inputs=universe_identity,
                    constitution=constitution.reference,
                )
                snapshot = build_universe_snapshot(
                    identity.run_id,
                    parsed.session,
                    loaded,
                    self.universe_policy,
                    enabled_asset_classes=self.enabled_asset_classes,
                    recorded_at=recorded_at,
                )
                if isinstance(snapshot, UniverseRefusal):
                    return InputRefusal(
                        _input_refusal_code(snapshot.code),
                        parsed.idempotency_key,
                        parsed.session,
                    )
                if isinstance(snapshot, UniverseSnapshot):
                    return AdvanceCommand(parsed, identity, snapshot)
                # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
                assert_never(snapshot)  # pragma: no cover
            # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
            assert_never(loaded)  # pragma: no cover
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(parsed)  # pragma: no cover


def _input_refusal_code(code: UniverseRefusalCode) -> InputRefusalCode:
    if code is UniverseRefusalCode.MISSING_INPUT:
        return InputRefusalCode.MISSING_UNIVERSE_INPUT
    if code is UniverseRefusalCode.INVALID_INPUT:
        return InputRefusalCode.INVALID_UNIVERSE_INPUT
    if code is UniverseRefusalCode.STALE_INPUT:
        return InputRefusalCode.STALE_UNIVERSE_INPUT
    if code is UniverseRefusalCode.CONTRADICTORY_INPUT:
        return InputRefusalCode.CONTRADICTORY_UNIVERSE_INPUT
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(code)  # pragma: no cover


@dataclass(frozen=True, slots=True)
class Status:
    """Rebuild and return lifecycle status without advancing authoritative history."""

    projection: LifecycleStatusProjection
    evidence_validator: EvidenceReferenceValidator
    constitution_status: ConstitutionStatus

    def __call__(self) -> LifecycleStatus:
        status = self.projection.rebuild_status()
        self.evidence_validator.validate_references(self.projection.rebuild_evidence_checkpoints())
        return replace(status, constitution_governance=self.constitution_status())
