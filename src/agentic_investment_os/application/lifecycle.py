"""Advance and report a Market Session through durable lifecycle capabilities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, assert_never

from agentic_investment_os.domain.attention import (
    AttentionArtifact,
    AttentionInputs,
    AttentionPolicy,
    AttentionRefusalReason,
    HoldingRefreshDisposition,
    InvalidAttentionError,
    select_attention,
)
from agentic_investment_os.domain.identity import (
    AssetClass,
    CryptoDecisionWindow,
    EquityInstrumentIdentity,
    MarketSession,
    canonical_instrument_bytes,
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
    DecisionCheckpointReference,
    EvidenceCaptureCheckpoint,
    InputRefusal,
    InputRefusalCode,
    InvalidLifecycleStateError,
    LifecycleCommand,
    LifecycleLedger,
    LifecyclePersistenceError,
    LifecycleStatus,
    LifecycleStatusProjection,
    MemoryUpdateRefusal,
    MemoryUpdateRefusalReason,
    PerformAttentionSelection,
    PerformDecisionPublication,
    PerformDossierBuild,
    PerformEvidenceCapture,
    PerformMemoryUpdate,
    PerformPortfolioConstruction,
    PerformResearch,
    PinnedRunIdentity,
    PortfolioCheckpointReference,
    ResearchCheckpoint,
    ResearchRefusal,
)
from agentic_investment_os.domain.lifecycle import (
    DecisionPublicationRefusalReason as LifecycleDecisionPublicationRefusalReason,
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
    EvidenceFeed,
    EvidenceKind,
    EvidencePersistenceError,
    InvalidEvidenceError,
    is_official_macro_release,
)
from agentic_investment_os.memory.admission import (
    BeliefClaimKind,
    BeliefEvent,
    BeliefEvidenceReference,
    BeliefEvidenceRelationship,
    BeliefStatus,
)
from agentic_investment_os.memory.beliefs import (
    BeliefGraphRefusal,
    BeliefPersistenceError,
    RecordDisposition,
)
from agentic_investment_os.portfolio.construction import (
    BalancedPortfolioPolicy,
    HouseViewResolution,
    MaterialEventEvidence,
    PortfolioConstructionRequest,
    PortfolioInputSet,
    PortfolioInputSource,
    PortfolioRefusalReason,
    PortfolioStance,
)
from agentic_investment_os.portfolio.publication import (
    DecisionPacketAccountScope,
    DecisionPacketSigner,
    DecisionPacketWindowSource,
    DecisionPublicationHistoryValidator,
    DecisionPublicationLedger,
    DecisionPublicationRefusalReason,
    DecisionPublicationResult,
    construct_decision_publication,
)
from agentic_investment_os.portfolio.shadows import (
    PortfolioCycleHistoryValidator,
    PortfolioCycleResultLedger,
    construct_portfolio_cycle,
)
from agentic_investment_os.research.authority import ResearchAuthority
from agentic_investment_os.research.production import (
    ProductionBeliefGraph,
    ProductionResearch,
    ProductionResearchContext,
    ProductionResearchEvidence,
    ProductionResearchRun,
    ProductionResearchSubject,
)
from agentic_investment_os.research.resolution import CioStance

if TYPE_CHECKING:
    from datetime import datetime

    from agentic_investment_os.application.governance import ConstitutionStatus
    from agentic_investment_os.application.memory import Record
    from agentic_investment_os.domain.governance import (
        ConstitutionArtifact,
        ConstitutionUse,
    )
    from agentic_investment_os.evidence.capture import (
        EvidenceCaptureCapability,
        EvidenceReferenceValidator,
        EvidenceVault,
    )
    from agentic_investment_os.memory.beliefs import BeliefHistoryValidator, BeliefLedger
    from agentic_investment_os.research.production import (
        ProductionResearchHistoryValidator,
    )

__all__ = ("Advance", "Clock", "ConstitutionResolver", "Status")


_INCOMPLETE_CHECKPOINT_RESULT = "lifecycle ledger returned an incomplete checkpoint result"
_CLOCK_INVALID = "lifecycle clock must return a timezone-aware instant representable in UTC"
_UNIVERSE_SOURCE_INVALID = "universe source returned a noncanonical absolute instant"
_MEMORY_REFUSAL_CONFLICT = "memory refusal observation conflicts with the accepted event prefix"


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

    def activate_due(self, recorded_at: UtcInstant) -> None: ...

    def resolve(
        self,
        session: MarketSession,
        recorded_at: UtcInstant,
        pinned: ConstitutionUse | None,
    ) -> ConstitutionArtifact: ...

    def validate_references(self, uses: tuple[ConstitutionUse, ...]) -> None: ...


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
    constitution_registry: ConstitutionResolver
    production_research: ProductionResearch
    evidence_vault: EvidenceVault
    memory: Record
    memory_refusal_ledger: BeliefLedger
    portfolio_policy: BalancedPortfolioPolicy
    portfolio_input_source: PortfolioInputSource
    portfolio_ledger: PortfolioCycleResultLedger
    decision_ledger: DecisionPublicationLedger
    decision_signer: DecisionPacketSigner
    decision_window_source: DecisionPacketWindowSource
    decision_account_scope: DecisionPacketAccountScope
    benchmark_identity: EquityInstrumentIdentity

    def __call__(  # noqa: PLR0912, PLR0915 - exhaust each typed lifecycle decision.
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
                self._validate_portfolio_receipt(decision)
                self._validate_decision_receipt(decision)
                return decision
            if isinstance(decision, AppendTerminalLifecycleRecord):
                self._validate_evidence_receipt(command, decision.receipt)
                self._validate_portfolio_receipt(decision.receipt)
                self._validate_decision_receipt(decision.receipt)
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
            if isinstance(decision, PerformDossierBuild):
                if not isinstance(command, AdvanceCommand):
                    raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
                command = replace(
                    command,
                    evidence_capture=decision.evidence_capture,
                    attention_selection=decision.attention_artifact,
                )
                context = self._research_context(command)
                build = self.production_research.build_dossiers(context, recorded_at)
                command = replace(
                    command,
                    dossier_build=(
                        build.refusal if build.refusal is not None else build.checkpoint
                    ),
                )
                continue
            if isinstance(decision, PerformResearch):
                if not isinstance(command, AdvanceCommand):
                    raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
                command = replace(command, attention_selection=decision.attention_artifact)
                context = self._research_context(command)
                build = self.production_research.build_dossiers(context, recorded_at)
                if build.checkpoint != decision.dossier_checkpoint:
                    raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
                run = self.production_research.run_research(context, build, recorded_at)
                command = replace(
                    command,
                    research_run=(run.refusal if run.refusal is not None else run.checkpoint),
                    no_action_reason=run.no_action_reason,
                )
                continue
            if isinstance(decision, PerformMemoryUpdate):
                if not isinstance(command, AdvanceCommand):
                    raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
                command = replace(command, attention_selection=decision.attention_artifact)
                context = self._research_context(command)
                build = self.production_research.build_dossiers(context, recorded_at)
                run = self.production_research.run_research(context, build, recorded_at)
                if run.checkpoint != decision.research_checkpoint:
                    raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
                memory_result = self._update_memory(context, run, recorded_at)
                command = replace(
                    command,
                    memory_update=memory_result,
                    no_action_reason=run.no_action_reason,
                )
                continue
            if isinstance(decision, PerformPortfolioConstruction):
                if not isinstance(command, AdvanceCommand):
                    raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
                command = replace(command, attention_selection=decision.attention_artifact)
                replayed_run = self.production_research.replay_run(
                    run_id=decision.pinned_run_identity.run_id,
                    dossier_checkpoint=decision.dossier_checkpoint,
                    research_checkpoint=decision.research_checkpoint,
                    no_action_reason=decision.no_action_reason,
                )
                if replayed_run is None:
                    raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
                result = construct_portfolio_cycle(
                    self._portfolio_request(
                        command,
                        replayed_run,
                        decision.dossier_checkpoint,
                        decision.memory_checkpoint,
                    )
                )
                command = replace(
                    command,
                    portfolio_construction=self.portfolio_ledger.record_cycle(
                        command.pinned_run_identity.run_id,
                        result,
                        recorded_at,
                    ),
                    no_action_reason=decision.no_action_reason,
                )
                continue
            if isinstance(decision, PerformDecisionPublication):
                if not isinstance(command, AdvanceCommand):
                    raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
                replayed_publication = self.decision_ledger.replay_publication(
                    decision.pinned_run_identity.run_id,
                    command.request.session,
                )
                if replayed_publication is not None:
                    command = replace(command, decision_publication=replayed_publication)
                    continue
                cycle_result = self.portfolio_ledger.load_cycle(
                    PortfolioCheckpointReference(
                        decision.pinned_run_identity.run_id,
                        decision.portfolio_checkpoint,
                        decision.portfolio_checkpoint.recorded_at,
                    )
                )
                replayed_run = self.production_research.replay_run(
                    run_id=decision.pinned_run_identity.run_id,
                    dossier_checkpoint=decision.dossier_checkpoint,
                    research_checkpoint=decision.research_checkpoint,
                    no_action_reason=decision.no_action_reason,
                )
                if replayed_run is None:
                    raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
                forecast_ids = tuple(
                    sorted(
                        resolution.forecast.content_hash
                        for resolution in replayed_run.resolutions
                        if resolution.forecast is not None
                    )
                )
                validity_window = self.decision_window_source.window_for(
                    command.request.session,
                    recorded_at,
                )
                publication: DecisionPublicationResult | DecisionPublicationRefusalReason
                if isinstance(validity_window, DecisionPublicationRefusalReason):
                    publication = validity_window
                else:
                    publication = construct_decision_publication(
                        cycle_result,
                        forecast_ids=forecast_ids,
                        model_fingerprint=_content_hash(
                            [
                                {
                                    "role": contract.role.value,
                                    "model_configuration": (
                                        contract.model_configuration.to_payload()
                                    ),
                                }
                                for contract in self.production_research.policy.role_contracts
                            ]
                        ),
                        benchmark_identity=self.benchmark_identity,
                        account_scope=self.decision_account_scope,
                        validity_window=validity_window,
                        signer=self.decision_signer,
                    )
                if isinstance(publication, DecisionPublicationRefusalReason):
                    command = replace(
                        command,
                        decision_publication=LifecycleDecisionPublicationRefusalReason(
                            publication.value
                        ),
                    )
                    continue
                command = replace(
                    command,
                    decision_publication=self.decision_ledger.record_publication(
                        decision.pinned_run_identity.run_id,
                        publication,
                        recorded_at,
                    ),
                )
                continue
            # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
            assert_never(decision)  # pragma: no cover

    def _research_context(self, command: AdvanceCommand) -> ProductionResearchContext:
        attention = command.attention_selection
        constitution = command.constitution
        if not isinstance(attention, AttentionArtifact) or constitution is None:
            raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
        try:
            records = self.evidence_vault.stored_records_for_artifacts(
                attention.evidence_artifact_ids
            )
        except (EvidencePersistenceError, InvalidEvidenceError) as error:
            raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT) from error
        records_by_id = {record.artifact.artifact_id: record for record in records}
        cards = {card.card_id: card for card in attention.candidate_cards}
        requested: list[tuple[str, EquityInstrumentIdentity, tuple[str, ...]]] = []
        for request in attention.dossier_requests:
            card = cards.get(request.candidate_card_id)
            if card is None or type(request.identity) is not EquityInstrumentIdentity:
                raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
            requested.append((request.request_id, request.identity, card.evidence_artifact_ids))
        for refresh in attention.holding_refreshes:
            if refresh.disposition is not HoldingRefreshDisposition.REQUIRED:
                continue
            if type(refresh.identity) is not EquityInstrumentIdentity:
                raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
            requested.append((refresh.refresh_id, refresh.identity, refresh.evidence_artifact_ids))
        subjects: list[ProductionResearchSubject] = []
        graphs: list[tuple[str, ProductionBeliefGraph]] = []
        for request_id, identity, evidence_ids in sorted(requested):
            try:
                subject_records = tuple(records_by_id[artifact_id] for artifact_id in evidence_ids)
            except KeyError as error:
                raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT) from error
            subject = ProductionResearchSubject(
                request_id,
                identity,
                tuple(
                    ProductionResearchEvidence(
                        record.artifact.artifact_id,
                        record.artifact.content_hash,
                        record.artifact.available_at,
                        json.dumps(
                            record.artifact.to_payload(),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        record.content,
                        record.artifact.feed
                        in (
                            EvidenceFeed.SEC_EDGAR,
                            EvidenceFeed.ISSUER_INVESTOR_RELATIONS,
                            EvidenceFeed.FEDERAL_RESERVE,
                            EvidenceFeed.BLS,
                            EvidenceFeed.BEA,
                        ),
                    )
                    for record in subject_records
                ),
            )
            graph = self.memory.graph(
                {
                    "schema_version": 1,
                    "record_kind": "belief_graph_query",
                    "cutoff": command.pinned_run_identity.evidence_cutoff.isoformat(),
                    "subjects": [identity.to_payload()],
                    "maximum_belief_events": (
                        self.production_research.policy.maximum_belief_events
                    ),
                    "maximum_evidence_artifacts": (
                        self.production_research.policy.maximum_evidence_artifacts
                    ),
                }
            )
            if isinstance(graph, BeliefGraphRefusal):
                raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
            subjects.append(subject)
            graphs.append(
                (
                    request_id,
                    ProductionBeliefGraph(
                        json.dumps(
                            graph.to_payload(),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        graph.content_hash,
                        tuple(
                            (node.event.belief_id, node.event.event_id)
                            for node in graph.belief_nodes
                        ),
                    ),
                )
            )
        return ProductionResearchContext(
            command.pinned_run_identity.run_id,
            command.pinned_run_identity.evidence_cutoff,
            command.pinned_run_identity.data_regime,
            constitution,
            command.universe_snapshot.inputs.position_snapshot,
            attention,
            tuple(subjects),
            tuple(graphs),
        )

    def _update_memory(
        self,
        context: ProductionResearchContext,
        run: ProductionResearchRun,
        recorded_at: UtcInstant,
    ) -> ResearchCheckpoint | ResearchRefusal:
        # PerformMemoryUpdate carries a completed ResearchCheckpoint, so the equality gate above
        # rejects a refused rerun before memory admission.
        if run.refusal is not None:  # pragma: no cover - checkpoint contract proves unreachable.
            return run.refusal
        active = tuple(
            resolution
            for resolution in run.resolutions
            if resolution.cio is not None and resolution.cio.stance is not CioStance.ABSTAIN
        )
        if not active:
            return ResearchCheckpoint()
        event_ids: list[str] = []
        for resolution in active:
            cio = resolution.cio
            if cio is None:  # pragma: no cover - active narrows this condition.
                raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
            belief_id = _content_hash(
                {
                    "subject": cio.subject.to_payload(),
                    "claim_kind": BeliefClaimKind.EXPECTATION.value,
                }
            )
            assertions = {
                assertion.assertion_id: assertion
                for assertion in (
                    *resolution.dossier.facts,
                    *resolution.dossier.interpretations,
                )
            }
            supporting_ids = tuple(
                sorted(
                    {
                        citation
                        for assertion_id in resolution.thesis.variant_view.supporting_assertion_ids
                        for citation in assertions[assertion_id].citation_artifact_ids
                    }
                )
            )
            contradicting_ids = resolution.thesis.variant_view.contradicting_artifact_ids
            records = {
                item.artifact_id: item
                for item in next(
                    subject.evidence
                    for subject in context.subjects
                    if subject.request_id == resolution.request_id
                )
            }
            evidence = tuple(
                sorted(
                    (
                        *(
                            BeliefEvidenceReference(
                                artifact_id,
                                records[artifact_id].content_hash,
                                BeliefEvidenceRelationship.SUPPORTING,
                            )
                            for artifact_id in supporting_ids
                        ),
                        *(
                            BeliefEvidenceReference(
                                artifact_id,
                                records[artifact_id].content_hash,
                                BeliefEvidenceRelationship.CONTRADICTING,
                            )
                            for artifact_id in contradicting_ids
                        ),
                    ),
                    key=lambda reference: (
                        reference.artifact_id,
                        reference.relationship.value,
                    ),
                )
            )
            event_id = _content_hash(
                {
                    "cio_resolution_id": cio.content_hash,
                    "belief_id": belief_id,
                }
            )
            prior_refusal = self.memory_refusal_ledger.load_memory_update_refusal(
                context.run_id,
                event_id,
            )
            if prior_refusal is not None:
                if prior_refusal.accepted_event_ids != tuple(sorted(event_ids)):
                    raise BeliefPersistenceError(_MEMORY_REFUSAL_CONFLICT)
                return _research_refusal(prior_refusal)
            current_graph = self.memory.graph(
                {
                    "schema_version": 1,
                    "record_kind": "belief_graph_query",
                    "cutoff": recorded_at.isoformat(),
                    "subjects": [cio.subject.to_payload()],
                    "maximum_belief_events": (
                        self.production_research.policy.maximum_belief_events
                    ),
                    "maximum_evidence_artifacts": (
                        self.production_research.policy.maximum_evidence_artifacts
                    ),
                }
            )
            if (
                isinstance(current_graph, BeliefGraphRefusal)
                or current_graph.omitted_belief_events > 0
            ):
                return self._record_memory_refusal(
                    MemoryUpdateRefusal(
                        context.run_id,
                        event_id,
                        MemoryUpdateRefusalReason.CURRENT_BELIEF_HISTORY_UNAVAILABLE,
                        recorded_at,
                        tuple(sorted(event_ids)),
                    )
                )
            current_events = tuple(
                node for node in current_graph.belief_nodes if node.event.belief_id == belief_id
            )
            redelivered_event = next(
                (node.event for node in current_events if node.event.event_id == event_id),
                None,
            )
            transition_from = (
                redelivered_event.transition_from_event_id
                if redelivered_event is not None
                else (
                    None
                    if not current_events
                    else max(
                        current_events,
                        key=lambda node: node.ledger_position,
                    ).event.event_id
                )
            )
            event = BeliefEvent.create(
                event_id=event_id,
                belief_id=belief_id,
                subject=cio.subject,
                claim_kind=BeliefClaimKind.EXPECTATION,
                claim=resolution.thesis.variant_view.text,
                valid_at=context.cutoff,
                transaction_at=(
                    recorded_at if redelivered_event is None else redelivered_event.transaction_at
                ),
                evidence_cutoff=context.cutoff,
                confidence={"low": "0.75", "medium": "0.5", "high": "0.25"}[cio.uncertainty],
                evidence=evidence,
                falsifiers=tuple(sorted(claim.text for claim in resolution.thesis.invalidators)),
                status=BeliefStatus.ACTIVE,
                transition_from_event_id=transition_from,
                supersedes_event_id=None,
            )
            receipt = self.memory(event.to_payload())
            if receipt.disposition not in (
                RecordDisposition.APPENDED,
                RecordDisposition.REPLAYED,
            ):
                reason = (
                    "unknown_record_refusal" if receipt.refusal is None else receipt.refusal.value
                )
                refusal_reason = MemoryUpdateRefusalReason(reason)
                return self._record_memory_refusal(
                    MemoryUpdateRefusal(
                        context.run_id,
                        event_id,
                        refusal_reason,
                        recorded_at,
                        tuple(sorted(event_ids)),
                        (
                            event.content_hash
                            if refusal_reason is MemoryUpdateRefusalReason.EVENT_IDENTITY_CONFLICT
                            else None
                        ),
                    )
                )
            event_ids.append(event_id)
        return ResearchCheckpoint(tuple(sorted(event_ids)))

    def _record_memory_refusal(
        self,
        refusal: MemoryUpdateRefusal,
    ) -> ResearchRefusal:
        stored = self.memory_refusal_ledger.record_memory_update_refusal(refusal)
        return _research_refusal(stored)

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

    def _validate_portfolio_receipt(self, receipt: AdvanceReceipt) -> None:
        checkpoint = receipt.portfolio_checkpoint
        identity = receipt.pinned_run_identity
        if checkpoint is None:
            return
        if identity is None:
            raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
        self.portfolio_ledger.validate_reference(
            PortfolioCheckpointReference(
                identity.run_id,
                checkpoint,
                checkpoint.recorded_at,
            )
        )

    def _validate_decision_receipt(self, receipt: AdvanceReceipt) -> None:
        checkpoint = receipt.decision_checkpoint
        identity = receipt.pinned_run_identity
        if checkpoint is None:
            return
        if identity is None or type(identity.cycle) is not MarketSession:
            raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
        self.decision_ledger.validate_reference(
            DecisionCheckpointReference(identity.run_id, identity.cycle, checkpoint)
        )

    def _prepare_command(  # noqa: PLR0911 - map each hostile input refusal explicitly.
        self,
        parsed: AdvanceRequest | InputRefusal,
        recorded_at: UtcInstant,
    ) -> LifecycleCommand:
        if isinstance(parsed, InputRefusal):
            return parsed
        if isinstance(parsed, AdvanceRequest):
            try:
                uses = self.ledger.constitution_uses()
                pinned = self.ledger.pinned_constitution_use(parsed.idempotency_key)
            except InvalidLifecycleStateError:
                return InputRefusal(
                    InputRefusalCode.INVALID_DURABLE_STATE,
                    parsed.idempotency_key,
                    parsed.session,
                )
            self.constitution_registry.validate_references(uses)
            if pinned is not None and pinned.session != parsed.session:
                self.constitution_registry.activate_due(recorded_at)
                return InputRefusal(
                    InputRefusalCode.IDEMPOTENCY_KEY_CONFLICT,
                    parsed.idempotency_key,
                    parsed.session,
                )
            constitution = self.constitution_registry.resolve(
                parsed.session,
                recorded_at,
                pinned,
            )
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
                portfolio_inputs = self.portfolio_input_source.load(loaded.position_snapshot)
                if isinstance(portfolio_inputs, PortfolioRefusalReason):
                    return InputRefusal(
                        _portfolio_input_refusal_code(portfolio_inputs),
                        parsed.idempotency_key,
                        parsed.session,
                    )
                if portfolio_inputs.available_at.value > loaded.evidence_cutoff.value:
                    return InputRefusal(
                        InputRefusalCode.CONTRADICTORY_PORTFOLIO_INPUT,
                        parsed.idempotency_key,
                        parsed.session,
                    )
                portfolio_age = loaded.evidence_cutoff.value - portfolio_inputs.observed_at.value
                if portfolio_age.total_seconds() > self.portfolio_policy.maximum_input_age_seconds:
                    return InputRefusal(
                        InputRefusalCode.STALE_PORTFOLIO_INPUT,
                        parsed.idempotency_key,
                        parsed.session,
                    )
                identity = PinnedRunIdentity.create(
                    parsed,
                    configuration_version=self.configuration_version,
                    configuration_hash=self.configuration_hash,
                    research_policy_hash=self.production_research.policy.fingerprint,
                    portfolio_policy_hash=self.portfolio_policy.policy_id,
                    portfolio_input_hash=portfolio_inputs.input_id,
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
                    return AdvanceCommand(
                        parsed,
                        identity,
                        snapshot,
                        portfolio_inputs,
                        constitution=constitution,
                    )
                # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
                assert_never(snapshot)  # pragma: no cover
            # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
            assert_never(loaded)  # pragma: no cover
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(parsed)  # pragma: no cover

    def _portfolio_request(
        self,
        command: AdvanceCommand,
        run: ProductionResearchRun,
        dossier_checkpoint: ResearchCheckpoint,
        memory_checkpoint: ResearchCheckpoint,
    ) -> PortfolioConstructionRequest:
        subjects = {
            canonical_instrument_bytes(subject.identity): subject
            for subject in command.universe_snapshot.attention_subjects
        }
        resolutions: list[HouseViewResolution] = []
        for resolution in run.resolutions:
            cio = resolution.cio
            if cio is None:
                continue
            subject = subjects.get(canonical_instrument_bytes(cio.subject))
            if subject is None:
                raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
            resolutions.append(
                HouseViewResolution(
                    identity=cio.subject,
                    request_id=resolution.request_id,
                    resolution_id=cio.content_hash,
                    stance=PortfolioStance(cio.stance.value),
                    uncertainty=cio.uncertainty,
                    production_authority=(
                        cio.authority_scope == ResearchAuthority.PRODUCTION.value
                        and cio.non_production is False
                    ),
                    eligible_for_new_entry=subject.eligible_for_new_entry,
                    is_position=subject.is_position,
                    evidence_artifact_ids=tuple(
                        sorted(
                            {
                                artifact_id
                                for assertion in (
                                    *resolution.dossier.facts,
                                    *resolution.dossier.interpretations,
                                )
                                if assertion.assertion_id in cio.rationale.assertion_ids
                                for artifact_id in assertion.citation_artifact_ids
                            }
                        )
                    ),
                )
            )
        identity = command.pinned_run_identity
        attention = command.attention_selection
        if not isinstance(attention, AttentionArtifact):
            raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
        expected_request_ids = tuple(
            sorted(
                {
                    *(item.request_id for item in attention.dossier_requests),
                    *(
                        item.refresh_id
                        for item in attention.holding_refreshes
                        if item.disposition is HoldingRefreshDisposition.REQUIRED
                    ),
                }
            )
        )
        if not isinstance(command.portfolio_inputs, PortfolioInputSet):
            raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
        try:
            evidence_records = self.evidence_vault.stored_records_for_artifacts(
                attention.evidence_artifact_ids
            )
        except (EvidencePersistenceError, InvalidEvidenceError) as error:
            raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT) from error
        material_event_evidence: list[MaterialEventEvidence] = []
        for record in evidence_records:
            artifact = record.artifact
            if artifact.kind not in (EvidenceKind.ISSUER_RELEASE, EvidenceKind.OFFICIAL_MACRO):
                continue
            if artifact.kind is EvidenceKind.OFFICIAL_MACRO and not is_official_macro_release(
                record
            ):
                continue
            released_at = artifact.source_event_at or artifact.published_at
            if released_at is None:
                raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
            material_event_evidence.append(
                MaterialEventEvidence(
                    artifact.artifact_id,
                    artifact.kind.value,
                    artifact.source_identity,
                    released_at,
                    artifact.available_at,
                    tuple(
                        sorted(
                            (mapping.identity for mapping in artifact.entity_mappings),
                            key=canonical_instrument_bytes,
                        )
                    ),
                )
            )
        return PortfolioConstructionRequest(
            run_id=identity.run_id,
            cycle=command.request.session,
            evidence_cutoff=identity.evidence_cutoff,
            data_regime=identity.data_regime,
            configuration_hash=identity.configuration_hash,
            constitution_version=identity.constitution_version,
            constitution_hash=identity.constitution_hash,
            research_policy_hash=identity.research_policy_hash,
            research_artifact_ids=tuple(
                sorted(
                    {
                        *dossier_checkpoint.artifact_ids,
                        *(() if run.checkpoint is None else run.checkpoint.artifact_ids),
                    }
                )
            ),
            memory_event_ids=memory_checkpoint.artifact_ids,
            universe_snapshot_id=command.universe_snapshot.snapshot_id,
            expected_research_request_ids=expected_request_ids,
            resolutions=tuple(resolutions),
            inputs=command.portfolio_inputs,
            policy=self.portfolio_policy,
            material_event_evidence=tuple(
                sorted(material_event_evidence, key=lambda item: item.artifact_id)
            ),
        )


def _research_refusal(memory_refusal: MemoryUpdateRefusal) -> ResearchRefusal:
    return ResearchRefusal(
        memory_refusal.refusal_id,
        ResearchCheckpoint(memory_refusal.accepted_event_ids),
        memory_update_refusal=memory_refusal,
    )


def _content_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


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


def _portfolio_input_refusal_code(
    reason: PortfolioRefusalReason,
) -> InputRefusalCode:
    if reason is PortfolioRefusalReason.STALE_INPUT:
        return InputRefusalCode.STALE_PORTFOLIO_INPUT
    if reason is PortfolioRefusalReason.CONTRADICTORY_INPUT:
        return InputRefusalCode.CONTRADICTORY_PORTFOLIO_INPUT
    if reason is PortfolioRefusalReason.INCOMPLETE_INPUT:
        return InputRefusalCode.MISSING_PORTFOLIO_INPUT
    return InputRefusalCode.INVALID_PORTFOLIO_INPUT


@dataclass(frozen=True, slots=True)
class Status:
    """Rebuild and return lifecycle status without advancing authoritative history."""

    projection: LifecycleStatusProjection
    evidence_validator: EvidenceReferenceValidator
    constitution_status: ConstitutionStatus
    research_history_validator: ProductionResearchHistoryValidator
    memory_history_validator: BeliefHistoryValidator
    portfolio_history_validator: PortfolioCycleHistoryValidator
    decision_history_validator: DecisionPublicationHistoryValidator

    def __call__(self) -> LifecycleStatus:
        status = self.projection.rebuild_status()
        self.evidence_validator.validate_references(self.projection.rebuild_evidence_checkpoints())
        self.research_history_validator.validate_history(
            self.projection.rebuild_production_research_checkpoints()
        )
        self.memory_history_validator.validate_history(self.projection.rebuild_memory_event_ids())
        self.portfolio_history_validator.validate_history(
            self.projection.rebuild_portfolio_checkpoints()
        )
        self.decision_history_validator.validate_history(
            self.projection.rebuild_decision_checkpoints()
        )
        uses = self.projection.rebuild_constitution_uses()
        return replace(status, constitution_governance=self.constitution_status(uses))
