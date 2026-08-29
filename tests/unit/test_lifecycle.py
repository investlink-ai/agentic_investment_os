from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast, override

import pytest

from agentic_investment_os.adapters.recorded_portfolio import RecordedPortfolioSource
from agentic_investment_os.adapters.recorded_universe import RecordedUniverseSource
from agentic_investment_os.application.lifecycle import Advance
from agentic_investment_os.domain.attention import (
    AttentionArtifact,
    AttentionRefusalReason,
)
from agentic_investment_os.domain.governance import (
    ACTIVE_CONSTITUTION,
    ConstitutionArtifact,
    ConstitutionUse,
    GovernanceStateError,
)
from agentic_investment_os.domain.identity import (
    AssetClass,
    CryptoDecisionWindow,
    EquityInstrumentIdentity,
    MarketSession,
)
from agentic_investment_os.domain.lifecycle import (
    AdvanceAttempt,
    AdvanceCommand,
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRecovery,
    AdvanceRequest,
    AppendLifecycleRecord,
    EvidenceCaptureCheckpoint,
    EvidenceCaptureReference,
    IdempotencyKey,
    InputRefusal,
    InputRefusalCode,
    InvalidLifecycleStateError,
    LifecycleCheckpoint,
    LifecycleCommand,
    LifecycleDecision,
    LifecycleEvent,
    LifecycleEventKind,
    LifecycleHistory,
    LifecycleLedger,
    LifecyclePhase,
    MemoryUpdateRefusal,
    MemoryUpdateRefusalReason,
    NoActionReason,
    PerformAttentionSelection,
    PerformDossierBuild,
    PerformEvidenceCapture,
    PerformMemoryUpdate,
    PerformPortfolioConstruction,
    PerformResearch,
    PinnedRunIdentity,
    PortfolioCheckpoint,
    PortfolioCheckpointReference,
    PortfolioCheckpointRefusalReason,
    ProductionResearchReference,
    ResearchCheckpoint,
    ResearchRefusal,
    derive_lifecycle_status,
    parse_advance_receipt,
    parse_lifecycle_checkpoint,
    parse_portfolio_checkpoint,
    parse_research_checkpoint,
    parse_research_refusal,
    reconstruct_constitution_uses,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.domain.universe import (
    UniverseInputIdentity,
    UniverseInputs,
    UniverseRefusal,
    UniverseSnapshot,
)
from agentic_investment_os.evidence.capture import EvidencePersistenceError
from agentic_investment_os.portfolio.construction import (
    PortfolioInputSet,
    PortfolioRefusalReason,
)
from agentic_investment_os.research.production import ProductionResearchRun
from agentic_investment_os.research.resolution import CioStance
from tests._attention import (
    attention_artifact,
    attention_inputs_for_snapshot,
    typed_attention_policy,
)
from tests._evidence import evidence_capture_checkpoint
from tests._governance import BaselineConstitutionRegistry
from tests._portfolio import recorded_portfolio_inputs
from tests._universe import (
    exact_text,
    pinned_run_identity,
    recorded_universe,
    typed_portfolio_inputs,
    typed_portfolio_policy,
    typed_research_policy,
    typed_universe_policy,
    universe_snapshot,
)

SHA256_HEX_LENGTH = 64
REFUSAL_RECORDED_AT = UtcInstant.from_datetime(datetime(2026, 8, 21, 22, tzinfo=UTC))
PINNED_SEQUENCE = 2
RECEIPT_RECORDED_AT = UtcInstant.from_datetime(datetime(2026, 8, 21, 22, 0, tzinfo=UTC))
UNEXPECTED_EVIDENCE_CAPTURE = "test ledger should terminate before evidence capture"
EVIDENCE_VAULT_INVALID = "Evidence Vault state is invalid"
ATTENTION_INPUTS_LOADED_TOO_EARLY = "attention inputs must not load before checkpoint validation"
INVALID_GOVERNANCE_HISTORY = "invalid Constitution governance history"
UNEXPECTED_GOVERNANCE_RESOLUTION = "governance validation must fail before resolution"
DOSSIER_CHECKPOINT = ResearchCheckpoint(("1" * SHA256_HEX_LENGTH,))
RESEARCH_CHECKPOINT = ResearchCheckpoint(("2" * SHA256_HEX_LENGTH,))
MEMORY_CHECKPOINT = ResearchCheckpoint(("3" * SHA256_HEX_LENGTH,))

if TYPE_CHECKING:
    from agentic_investment_os.application.memory import Record
    from agentic_investment_os.domain.attention import AttentionInputs
    from agentic_investment_os.domain.universe import PositionSnapshot
    from agentic_investment_os.evidence.capture import (
        EvidenceCaptureSummary,
        EvidenceStoredRecord,
        EvidenceVault,
    )
    from agentic_investment_os.memory.beliefs import BeliefLedger
    from agentic_investment_os.portfolio.construction import PortfolioResultLedger
    from agentic_investment_os.research.policy import ProductionResearchPolicy
    from agentic_investment_os.research.production import (
        ProductionResearch,
        ProductionResearchResolution,
    )


class _StringSubclass(str):
    __slots__ = ()


class _IntSubclass(int):
    __slots__ = ()


class _NoneSpoof:
    @override
    def __eq__(self, other: object) -> bool:
        return other is None

    @override
    def __hash__(self) -> int:
        return hash(None)


@dataclass(frozen=True, slots=True)
class _FixtureProductionResearch:
    policy: object


@dataclass(frozen=True, slots=True)
class _MissingPortfolioReplay:
    policy: ProductionResearchPolicy

    def replay_run(
        self,
        *,
        run_id: str,
        dossier_checkpoint: ResearchCheckpoint,
        research_checkpoint: ResearchCheckpoint,
        no_action_reason: NoActionReason | None,
    ) -> ProductionResearchRun | None:
        _ = (run_id, dossier_checkpoint, research_checkpoint, no_action_reason)
        return None


@dataclass(frozen=True, slots=True)
class _FixedPortfolioInputSource:
    result: PortfolioInputSet | PortfolioRefusalReason

    def load(
        self,
        position_snapshot: PositionSnapshot,
    ) -> PortfolioInputSet | PortfolioRefusalReason:
        _ = position_snapshot
        return self.result


@dataclass(frozen=True, slots=True)
class _PortfolioCioView:
    subject: EquityInstrumentIdentity
    content_hash: str = "f" * SHA256_HEX_LENGTH
    stance: CioStance = CioStance.LONG
    uncertainty: str = "low"
    authority_scope: str = "production_research"
    non_production: bool = False


@dataclass(frozen=True, slots=True)
class _PortfolioResolutionView:
    request_id: str
    cio: _PortfolioCioView


@dataclass(frozen=True, slots=True)
class _MissingSubjectEvidenceVault:
    def stored_records_for_artifacts(
        self,
        artifact_ids: tuple[str, ...],
    ) -> tuple[EvidenceStoredRecord, ...]:
        _ = artifact_ids
        return ()


def _request(key: str = "concurrent-request") -> AdvanceRequest:
    parsed = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key=key,
    )
    assert isinstance(parsed, AdvanceRequest)
    return parsed


def _advance(ledger: LifecycleLedger) -> Advance:
    return Advance(
        ledger=ledger,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
        universe_source=RecordedUniverseSource(recorded_universe()),
        enabled_asset_classes=(AssetClass.US_EQUITY,),
        universe_policy=typed_universe_policy(),
        evidence_capture=_UnusedEvidenceCapture(),
        attention_policy=typed_attention_policy(),
        attention_inputs=_FixtureAttentionInputs(),
        clock=FixedClock(),
        constitution_registry=BaselineConstitutionRegistry(),
        # These fake-ledger tests terminate before Stage 3 orchestration.
        production_research=cast(
            "ProductionResearch",
            _FixtureProductionResearch(typed_research_policy()),
        ),
        evidence_vault=cast("EvidenceVault", None),
        memory=cast("Record", None),
        memory_refusal_ledger=cast("BeliefLedger", None),
        portfolio_policy=typed_portfolio_policy(),
        portfolio_input_source=RecordedPortfolioSource(
            recorded_portfolio_inputs(typed_portfolio_inputs().position_snapshot)
        ),
        portfolio_ledger=cast("PortfolioResultLedger", None),
    )


def _advanced_receipt(
    identity: PinnedRunIdentity,
    snapshot: UniverseSnapshot,
    recovery: AdvanceRecovery,
    recorded_at: UtcInstant,
    evidence_capture: EvidenceCaptureCheckpoint | None = None,
) -> AdvanceReceipt:
    capture = evidence_capture_checkpoint() if evidence_capture is None else evidence_capture
    return AdvanceReceipt.advanced(
        identity,
        snapshot,
        recovery,
        recorded_at,
        capture,
        attention_artifact(identity, snapshot, capture),
        DOSSIER_CHECKPOINT,
        RESEARCH_CHECKPOINT,
        MEMORY_CHECKPOINT,
        PortfolioCheckpoint(
            "4" * SHA256_HEX_LENGTH,
            "5" * SHA256_HEX_LENGTH,
            identity.portfolio_policy_hash,
            identity.portfolio_input_hash,
            ("6" * SHA256_HEX_LENGTH,),
            recorded_at,
        ),
        None,
    )


def _cycle() -> dict[str, object]:
    return MarketSession(date(2026, 8, 21)).to_payload()


def _reseal_receipt_with_pinned_identity(envelope: dict[str, object]) -> None:
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    pinned = payload["pinned_run_identity"]
    assert isinstance(pinned, dict)
    cycle = pinned["cycle"]
    cycle_text = json.dumps(cycle, sort_keys=True, separators=(",", ":"))
    run_material = (
        pinned["configuration_hash"],
        pinned["configuration_version"],
        pinned["research_policy_hash"],
        pinned["constitution_hash"],
        pinned["constitution_version"],
        "champion",
        cycle_text,
        pinned["data_regime"],
        pinned["evidence_cutoff"],
        pinned["instrument_snapshot_hash"],
        pinned["position_snapshot_hash"],
        pinned["eligibility_policy_hash"],
        pinned["portfolio_policy_hash"],
        pinned["portfolio_input_hash"],
    )
    pinned["run_id"] = hashlib.sha256(json.dumps(run_material).encode()).hexdigest()
    _reseal_receipt(envelope)


def _reseal_receipt(envelope: dict[str, object]) -> None:
    material = {key: item for key, item in envelope.items() if key != "content_hash"}
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    envelope["content_hash"] = hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 21, 22, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _UnusedEvidenceCapture:
    @property
    def policy_id(self) -> str:
        return evidence_capture_checkpoint().policy_id

    def __call__(
        self,
        *,
        run_id: str,
        universe_snapshot_id: str,
        cutoff: UtcInstant,
        data_regime: str,
    ) -> EvidenceCaptureSummary:
        _ = (run_id, universe_snapshot_id, cutoff, data_regime)
        raise AssertionError(UNEXPECTED_EVIDENCE_CAPTURE)

    def validate_checkpoint(
        self,
        *,
        run_id: str,
        universe_snapshot_id: str,
        cutoff: UtcInstant,
        data_regime: str,
        checkpoint: EvidenceCaptureCheckpoint,
    ) -> None:
        _ = (run_id, universe_snapshot_id, cutoff, data_regime, checkpoint)


@dataclass(frozen=True)
class _InvalidCheckpointEvidenceCapture(_UnusedEvidenceCapture):
    @override
    def validate_checkpoint(
        self,
        *,
        run_id: str,
        universe_snapshot_id: str,
        cutoff: UtcInstant,
        data_regime: str,
        checkpoint: EvidenceCaptureCheckpoint,
    ) -> None:
        _ = (run_id, universe_snapshot_id, cutoff, data_regime, checkpoint)
        raise EvidencePersistenceError(EVIDENCE_VAULT_INVALID)


@dataclass(frozen=True)
class _FixtureAttentionInputs:
    def __call__(  # noqa: PLR0913 - mirror the production capability protocol.
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
        return attention_inputs_for_snapshot(
            run_id=run_id,
            cycle=cycle,
            snapshot=universe_snapshot,
            cutoff=cutoff,
            data_regime=data_regime,
            evidence=EvidenceCaptureCheckpoint(
                evidence_policy_id,
                evidence_artifact_ids,
                (),
            ),
        )


@dataclass(frozen=True)
class _FailingAttentionInputs:
    reason: AttentionRefusalReason | None = None

    def __call__(  # noqa: PLR0913 - mirror the production capability protocol.
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
        if self.reason is not None:
            return self.reason
        raise EvidencePersistenceError(EVIDENCE_VAULT_INVALID)


@dataclass(frozen=True)
class _UnexpectedAttentionInputs(_FailingAttentionInputs):
    @override
    def __call__(
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
        raise AssertionError(ATTENTION_INPUTS_LOADED_TOO_EARLY)


@dataclass
class ConcurrentCompletionLedger:
    completion_point: str
    receipt: AdvanceReceipt
    steps: int = 0

    def pinned_constitution_use(self, _idempotency_key: IdempotencyKey) -> ConstitutionUse | None:
        return None

    def constitution_uses(self) -> tuple[ConstitutionUse, ...]:
        return ()

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        recorded_at: UtcInstant,
    ) -> LifecycleDecision:
        assert isinstance(command, AdvanceCommand)
        if self.completion_point == "start" and self.steps == 0:
            return self.receipt
        if self.completion_point == "reconcile_failure" and self.steps == 1:
            return AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
        if self.completion_point == "reconcile" and self.steps == 1:
            return self.receipt
        if self.completion_point == "pin_failure" and self.steps == PINNED_SEQUENCE:
            return AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
        if self.completion_point == "pin_observed" and self.steps == PINNED_SEQUENCE:
            return self.receipt

        event_kind, phase = (
            (LifecycleEventKind.ADVANCE_REQUESTED, None)
            if self.steps == 0
            else (
                LifecycleEventKind.PHASE_COMPLETED,
                LifecyclePhase.RECONCILE_PRIOR_STATE,
            )
        )
        next_attempt = (
            attempt
            if self.completion_point == "incomplete_reconcile" and self.steps == 1
            else AdvanceAttempt(AdvanceRecovery.FRESH, self.steps)
        )
        decision = AppendLifecycleRecord(
            LifecycleEvent(
                command.request.stream_id,
                self.steps,
                command.request,
                command.pinned_run_identity,
                event_kind,
                None if phase is None else LifecycleCheckpoint.equity(phase),
                recorded_at,
            ),
            next_attempt,
        )
        self.steps += 1
        return decision


@dataclass(frozen=True)
class _DecisionOnRefusalLedger:
    decision: LifecycleDecision

    def pinned_constitution_use(self, _idempotency_key: IdempotencyKey) -> ConstitutionUse | None:
        return None

    def constitution_uses(self) -> tuple[ConstitutionUse, ...]:
        return ()

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        recorded_at: UtcInstant,
    ) -> LifecycleDecision:
        _ = (command, attempt, recorded_at)
        return self.decision


@dataclass
class _ContradictoryAttentionHistoryLedger:
    identity: PinnedRunIdentity
    snapshot: UniverseSnapshot
    artifact: AttentionArtifact
    expected_reason: AttentionRefusalReason = AttentionRefusalReason.CONTRADICTORY_EVIDENCE
    calls: int = 0

    def pinned_constitution_use(
        self,
        _idempotency_key: IdempotencyKey,
    ) -> ConstitutionUse | None:
        return None

    def constitution_uses(self) -> tuple[ConstitutionUse, ...]:
        return ()

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        recorded_at: UtcInstant,
    ) -> LifecycleDecision:
        _ = (attempt, recorded_at)
        assert isinstance(command, AdvanceCommand)
        self.calls += 1
        if self.calls == 1:
            return PerformAttentionSelection(
                self.identity,
                self.snapshot,
                evidence_capture_checkpoint(),
                (self.artifact,),
            )
        assert command.attention_selection is self.expected_reason
        return AdvanceReceipt.failed_closed(
            AdvanceFailureReason.ATTENTION_SELECTION_FAILED,
            cycle=command.request.session,
            evidence_capture=evidence_capture_checkpoint(),
            attention_refusal_reason=command.attention_selection,
        )


@dataclass(slots=True)
class _CountingUniverseSource:
    delegate: RecordedUniverseSource
    loads: int = 0

    def load(self) -> UniverseInputs | UniverseRefusal:
        self.loads += 1
        return self.delegate.load()


@dataclass(frozen=True, slots=True)
class _RefusingConstitutionRegistry:
    def activate_due(self, recorded_at: UtcInstant) -> None:
        del recorded_at

    def validate_references(self, uses: tuple[ConstitutionUse, ...]) -> None:
        del uses
        raise GovernanceStateError(INVALID_GOVERNANCE_HISTORY)

    def resolve(
        self,
        session: MarketSession,
        recorded_at: UtcInstant,
        pinned: ConstitutionUse | None,
    ) -> ConstitutionArtifact:
        del session, recorded_at, pinned
        raise AssertionError(UNEXPECTED_GOVERNANCE_RESOLUTION)


def test_advance_validates_governance_before_loading_universe_or_entering_lifecycle() -> None:
    ledger = _DecisionOnRefusalLedger(
        AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
    )
    capability = _advance(ledger)
    source = _CountingUniverseSource(RecordedUniverseSource(recorded_universe()))
    blocked = replace(
        capability,
        universe_source=source,
        constitution_registry=_RefusingConstitutionRegistry(),
    )

    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        blocked(cycle=_cycle(), mode="champion", idempotency_key="invalid-governance")

    assert source.loads == 0


def test_advance_request_validates_the_complete_boundary() -> None:
    request = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key="session-2026-08-21",
    )

    assert isinstance(request, AdvanceRequest)
    assert request.session.isoformat() == "2026-08-21"
    assert request.mode.value == "champion"
    assert request.idempotency_key.value == "session-2026-08-21"
    assert request.stream_id == "b24b0e025ab67f2594db49e7f5e1c7cfe8170645fe5b9defe068e8c715d7a9e5"
    identity = pinned_run_identity(
        request,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    assert identity.run_id == "16e7342cac5d962debc0cf9ff891364d91e77be82c22bb2827a3e0884cf9643f"

    invalid_cases = (
        (
            {"session": "21-08-2026", "mode": "champion", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_SESSION,
        ),
        (
            {"session": "2026-08-21", "mode": "research-lab", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_MODE,
        ),
        (
            {"session": "2026-08-21", "mode": "champion", "idempotency_key": "contains space"},
            InputRefusalCode.INVALID_IDEMPOTENCY_KEY,
        ),
        (
            {"session": None, "mode": "champion", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_SESSION,
        ),
        (
            {"session": "20260821", "mode": "champion", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_SESSION,
        ),
    )

    for values, expected_code in invalid_cases:
        refusal = AdvanceRequest.parse(**values)
        assert isinstance(refusal, InputRefusal)
        assert refusal.code is expected_code


def test_lifecycle_reconstruction_rejects_an_invalid_pinned_constitution_reference() -> None:
    request = _request()
    identity = replace(pinned_run_identity(request), constitution_version=0)
    event = LifecycleEvent(
        request.stream_id,
        0,
        request,
        identity,
        LifecycleEventKind.ADVANCE_REQUESTED,
        None,
        RECEIPT_RECORDED_AT,
    )

    with pytest.raises(InvalidLifecycleStateError, match="derived identity is invalid"):
        derive_lifecycle_status(LifecycleHistory(events=(event,)))


def test_lifecycle_reconstructs_the_exact_constitution_use() -> None:
    request = _request()
    identity = pinned_run_identity(request)
    event = LifecycleEvent(
        request.stream_id,
        0,
        request,
        identity,
        LifecycleEventKind.ADVANCE_REQUESTED,
        None,
        RECEIPT_RECORDED_AT,
    )

    assert reconstruct_constitution_uses(LifecycleHistory(events=(event,))) == (
        ConstitutionUse(request.session, ACTIVE_CONSTITUTION.reference, RECEIPT_RECORDED_AT),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("envelope_schema_version", True),
        ("envelope_schema_version", 1.0),
        ("payload_schema_version", True),
        ("payload_schema_version", 1.0),
        ("record_kind", 3),
        ("payload_discriminator", 3),
        ("payload_discriminator", "crypto_decision_window_phase"),
        ("authority_scope", 3),
        ("content_hash", 3),
        ("content_hash", "f" * SHA256_HEX_LENGTH),
    ],
)
def test_lifecycle_checkpoint_parser_rejects_hostile_or_changed_envelopes(
    field: str,
    value: object,
) -> None:
    checkpoint = LifecycleCheckpoint.equity(LifecyclePhase.SNAPSHOT_UNIVERSE)
    payload = checkpoint.to_payload()
    payload[field] = value

    assert parse_lifecycle_checkpoint(payload) is None


@pytest.mark.parametrize(
    "case",
    [
        "non_mapping",
        "non_string_key",
        "wrong_envelope_fields",
        "non_mapping_payload",
        "wrong_payload_fields",
        "non_string_phase",
    ],
)
def test_lifecycle_checkpoint_parser_rejects_hostile_shapes(case: str) -> None:
    value: object = LifecycleCheckpoint.equity(LifecyclePhase.SNAPSHOT_UNIVERSE).to_payload()
    if case == "non_mapping":
        value = []
    else:
        assert isinstance(value, dict)
        if case == "non_string_key":
            value[1] = "hostile"
        elif case == "wrong_envelope_fields":
            value["extra"] = "hostile"
        elif case == "non_mapping_payload":
            value["payload"] = []
        else:
            phase_payload = value["payload"]
            assert isinstance(phase_payload, dict)
            if case == "wrong_payload_fields":
                phase_payload["extra"] = "hostile"
            else:
                assert case == "non_string_phase"
                phase_payload["phase"] = True

    assert parse_lifecycle_checkpoint(value) is None


def test_lifecycle_checkpoint_rejects_an_untyped_phase_and_unknown_phase_payload() -> None:
    # Deliberately violate the static enum contract to exercise constructor validation.
    with pytest.raises(ValueError, match="invalid lifecycle checkpoint phase"):
        LifecycleCheckpoint(cast("LifecyclePhase", "SnapshotUniverse"))

    payload = LifecycleCheckpoint.equity(LifecyclePhase.SNAPSHOT_UNIVERSE).to_payload()
    phase_payload = payload["payload"]
    assert isinstance(phase_payload, dict)
    phase_payload["phase"] = "UnknownPhase"

    assert parse_lifecycle_checkpoint(payload) is None


def test_lifecycle_event_uses_a_hashed_common_envelope() -> None:
    request = _request()
    identity = pinned_run_identity(request)
    snapshot = universe_snapshot(identity)
    checkpoint = LifecycleCheckpoint.equity(LifecyclePhase.SNAPSHOT_UNIVERSE)
    assert parse_lifecycle_checkpoint(checkpoint.to_payload()) == checkpoint
    event = LifecycleEvent(
        request.stream_id,
        3,
        request,
        identity,
        LifecycleEventKind.UNIVERSE_SNAPSHOTTED,
        checkpoint,
        RECEIPT_RECORDED_AT,
        snapshot,
    )

    envelope = event.to_envelope()

    assert envelope["payload_discriminator"] == "equity_market_session_lifecycle_event"
    assert envelope["authority_scope"] == "investment_operating_system"
    assert envelope["available_at"] == envelope["event_at"]
    assert envelope["content_hash"] != identity.run_id
    event_payload = envelope["payload"]
    assert isinstance(event_payload, dict)
    assert event_payload["completed_phase"] == checkpoint.to_payload()
    assert event_payload["universe_snapshot_id"] == snapshot.snapshot_id
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(
            event,
            recorded_at=cast("UtcInstant", datetime(2026, 8, 21, 22, 0)),  # noqa: DTZ001
        ).to_envelope()


def test_pinned_identity_rejects_an_untyped_evidence_cutoff_with_a_bounded_error() -> None:
    universe_inputs = UniverseInputIdentity(
        data_regime="adjusted-sip-v1",
        evidence_cutoff=cast("UtcInstant", datetime(2026, 8, 21, 20, 0, tzinfo=UTC)),
        instrument_snapshot_hash="1" * 64,
        position_snapshot_hash="2" * 64,
        eligibility_policy_hash="3" * 64,
    )

    with pytest.raises(
        InvalidUtcInstantError,
        match="lifecycle absolute instant must be canonical",
    ):
        PinnedRunIdentity.create(
            _request(),
            configuration_version=1,
            configuration_hash="a" * 64,
            research_policy_hash="b" * 64,
            portfolio_policy_hash="c" * 64,
            portfolio_input_hash="d" * 64,
            universe_inputs=universe_inputs,
            constitution=ACTIVE_CONSTITUTION.reference,
        )


def test_advance_receipt_rejects_incomplete_success_and_failure_shapes() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)

    with pytest.raises(ValueError, match="advanced receipt requires completed recovery facts"):
        AdvanceReceipt(
            AdvanceDisposition.ADVANCED,
            LifecycleCheckpoint.equity(LifecyclePhase.PIN_RUN_INPUTS),
            identity,
            None,
        )
    with pytest.raises(ValueError, match="advanced receipt requires completed recovery facts"):
        AdvanceReceipt(
            AdvanceDisposition.ADVANCED,
            None,
            identity,
            None,
            AdvanceRecovery.FRESH,
            snapshot.snapshot_id,
        )
    with pytest.raises(ValueError, match="failed receipt requires one bounded reason"):
        AdvanceReceipt(
            AdvanceDisposition.FAILED_CLOSED,
            None,
            None,
            None,
        )
    with pytest.raises(ValueError, match="failed receipt requires one bounded reason"):
        AdvanceReceipt(
            AdvanceDisposition.FAILED_CLOSED,
            None,
            None,
            AdvanceFailureReason.INVALID_DURABLE_STATE,
            AdvanceRecovery.RESUMED,
        )
    crypto_cycle = CryptoDecisionWindow(
        UtcInstant.from_datetime(datetime(2026, 8, 22, tzinfo=UTC)),
        UtcInstant.from_datetime(datetime(2026, 8, 23, tzinfo=UTC)),
    )
    with pytest.raises(ValueError, match="failed receipt requires one bounded reason"):
        AdvanceReceipt.failed_closed(AdvanceFailureReason.UNSUPPORTED_CYCLE)
    with pytest.raises(ValueError, match="failed receipt requires one bounded reason"):
        AdvanceReceipt.failed_closed(
            AdvanceFailureReason.INVALID_SESSION,
            cycle=crypto_cycle,
        )
    with pytest.raises(ValueError, match="failed receipt requires one bounded reason"):
        AdvanceReceipt.failed_closed(
            AdvanceFailureReason.UNSUPPORTED_CYCLE,
            cycle=MarketSession(date(2026, 8, 21)),
        )

    assert (
        _advanced_receipt(identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT).recovery
        is AdvanceRecovery.FRESH
    )


def test_evidence_capture_checkpoint_rejects_invalid_or_duplicate_references() -> None:
    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        EvidenceCaptureCheckpoint("invalid", (), ())
    invalid_references = (
        (("invalid",), ()),
        (("a" * SHA256_HEX_LENGTH,) * 2, ()),
        (("b" * SHA256_HEX_LENGTH, "a" * SHA256_HEX_LENGTH), ()),
        ((), ("invalid",)),
        ((), ("a" * SHA256_HEX_LENGTH,) * 2),
        ((), ("b" * SHA256_HEX_LENGTH, "a" * SHA256_HEX_LENGTH)),
    )
    for artifact_ids, refusal_ids in invalid_references:
        with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
            EvidenceCaptureCheckpoint("c" * SHA256_HEX_LENGTH, artifact_ids, refusal_ids)


def test_evidence_capture_reference_rejects_invalid_pinned_identity() -> None:
    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        EvidenceCaptureReference(
            "invalid",
            "b" * SHA256_HEX_LENGTH,
            RECEIPT_RECORDED_AT,
            "alpaca-basic-iex-v1",
            evidence_capture_checkpoint(),
        )


def test_research_checkpoint_and_refusal_reject_invalid_references_or_resources() -> None:
    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        ResearchCheckpoint(("invalid",))
    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        ResearchRefusal("invalid")
    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        ResearchRefusal(
            "f" * SHA256_HEX_LENGTH,
            ResearchCheckpoint(),
            "1" * SHA256_HEX_LENGTH,
        )
    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        ResearchRefusal(
            "f" * SHA256_HEX_LENGTH,
            ResearchCheckpoint(call_ids=("1" * SHA256_HEX_LENGTH,)),
            "invalid",
        )


def test_research_checkpoint_parser_rejects_hostile_shapes_and_negative_resources() -> None:
    valid = ResearchCheckpoint(("1" * SHA256_HEX_LENGTH,)).to_payload()
    invalid_values: tuple[object, ...] = (
        [],
        {**valid, "schema_version": 2},
        {**valid, "input_tokens": "0"},
        {**valid, "turns": -1},
    )

    assert all(parse_research_checkpoint(value) is None for value in invalid_values)


def test_portfolio_checkpoint_rejects_invalid_and_inconsistent_material() -> None:
    valid = PortfolioCheckpoint(
        "1" * SHA256_HEX_LENGTH,
        "2" * SHA256_HEX_LENGTH,
        "3" * SHA256_HEX_LENGTH,
        "4" * SHA256_HEX_LENGTH,
        ("5" * SHA256_HEX_LENGTH,),
        RECEIPT_RECORDED_AT,
    )
    invalid_hash = {**valid.to_payload(), "result_id": "invalid"}
    missing_success_house_view = {**valid.to_payload(), "house_view_id": None}
    boolean_schema = {**valid.to_payload(), "schema_version": True}
    invalid_recorded_at = {**valid.to_payload(), "recorded_at": "invalid"}

    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        PortfolioCheckpoint(
            "1" * SHA256_HEX_LENGTH,
            None,
            "3" * SHA256_HEX_LENGTH,
            "4" * SHA256_HEX_LENGTH,
            (),
            RECEIPT_RECORDED_AT,
        )
    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        PortfolioCheckpointReference("invalid", valid, RECEIPT_RECORDED_AT)

    assert parse_portfolio_checkpoint(invalid_hash) is None
    assert parse_portfolio_checkpoint(missing_success_house_view) is None
    assert parse_portfolio_checkpoint(boolean_schema) is None
    assert parse_portfolio_checkpoint(invalid_recorded_at) is None
    assert (
        parse_portfolio_checkpoint(
            PortfolioCheckpoint(
                "1" * SHA256_HEX_LENGTH,
                None,
                "3" * SHA256_HEX_LENGTH,
                "4" * SHA256_HEX_LENGTH,
                (),
                RECEIPT_RECORDED_AT,
                PortfolioCheckpointRefusalReason.INCOMPLETE_INPUT,
            ).to_payload()
        )
        is not None
    )


def test_research_refusal_parser_rejects_hostile_envelopes_and_checkpoints() -> None:
    valid = ResearchRefusal("f" * SHA256_HEX_LENGTH).to_payload()

    assert parse_research_refusal({**valid, "schema_version": 2}) is None
    assert parse_research_refusal({**valid, "checkpoint": {}}) is None
    assert parse_research_refusal({**valid, "terminal_call_id": "invalid"}) is None
    assert parse_research_refusal({**valid, "terminal_call_id": "1" * SHA256_HEX_LENGTH}) is None


def test_research_refusal_parser_binds_exact_typed_memory_failure() -> None:
    detail = MemoryUpdateRefusal(
        "a" * SHA256_HEX_LENGTH,
        "b" * SHA256_HEX_LENGTH,
        MemoryUpdateRefusalReason.INVALID_AUTHORITATIVE_HISTORY,
        REFUSAL_RECORDED_AT,
    )
    refusal = ResearchRefusal(detail.refusal_id, memory_update_refusal=detail)
    payload = refusal.to_payload()

    assert parse_research_refusal(payload) == refusal
    memory_payload = payload["memory_update_refusal"]
    assert isinstance(memory_payload, dict)
    assert (
        parse_research_refusal(
            {
                **payload,
                "memory_update_refusal": {
                    **memory_payload,
                    "reason": MemoryUpdateRefusalReason.INVALID_EVENT.value,
                },
            }
        )
        is None
    )
    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        ResearchRefusal("f" * SHA256_HEX_LENGTH, memory_update_refusal=detail)
    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        ResearchRefusal(
            detail.refusal_id,
            ResearchCheckpoint(("c" * SHA256_HEX_LENGTH,)),
            memory_update_refusal=detail,
        )
    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        ResearchRefusal(
            detail.refusal_id,
            ResearchCheckpoint(call_ids=("c" * SHA256_HEX_LENGTH,)),
            "c" * SHA256_HEX_LENGTH,
            detail,
        )


def test_memory_update_refusal_rejects_invalid_identity_and_hostile_payloads() -> None:
    failed_event_id = "b" * SHA256_HEX_LENGTH
    detail = MemoryUpdateRefusal(
        "a" * SHA256_HEX_LENGTH,
        failed_event_id,
        MemoryUpdateRefusalReason.INVALID_EVENT,
        REFUSAL_RECORDED_AT,
    )
    valid = detail.to_payload()

    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        MemoryUpdateRefusal(
            "invalid",
            failed_event_id,
            MemoryUpdateRefusalReason.INVALID_EVENT,
            REFUSAL_RECORDED_AT,
        )

    invalid_payloads: tuple[dict[str, object], ...] = (
        {**valid, "schema_version": 2},
        {**valid, "accepted_event_ids": "not-a-list"},
        {**valid, "reason": "not-a-memory-refusal-reason"},
        {**valid, "recorded_at": "2026-08-21T22:00:00+00:00"},
        {**valid, "accepted_event_ids": [failed_event_id]},
        {**valid, "attempted_event_content_hash": "c" * SHA256_HEX_LENGTH},
    )

    for memory_payload in invalid_payloads:
        refusal_payload = {
            **ResearchRefusal(detail.refusal_id).to_payload(),
            "memory_update_refusal": memory_payload,
        }
        assert parse_research_refusal(refusal_payload) is None


def test_production_research_reference_rejects_a_non_research_phase() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)

    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        ProductionResearchReference(
            identity,
            LifecyclePhase.SELECT_ATTENTION,
            attention_artifact(identity, snapshot, evidence_capture_checkpoint()),
            snapshot.inputs.position_snapshot,
            None,
        )


def test_production_research_reference_requires_a_checkpoint_for_refusal_identity() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    artifact = attention_artifact(identity, snapshot, evidence_capture_checkpoint())

    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        ProductionResearchReference(
            identity,
            LifecyclePhase.BUILD_DOSSIERS,
            artifact,
            snapshot.inputs.position_snapshot,
            None,
            "f" * SHA256_HEX_LENGTH,
        )
    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        ProductionResearchReference(
            identity,
            LifecyclePhase.BUILD_DOSSIERS,
            artifact,
            snapshot.inputs.position_snapshot,
            ResearchCheckpoint(),
            "invalid",
        )
    with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
        ProductionResearchReference(
            identity,
            LifecyclePhase.BUILD_DOSSIERS,
            artifact,
            snapshot.inputs.position_snapshot,
            ResearchCheckpoint(call_ids=("1" * SHA256_HEX_LENGTH,)),
            None,
            "1" * SHA256_HEX_LENGTH,
        )


def test_production_research_reference_binds_memory_refusal_to_completed_research() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    artifact = attention_artifact(identity, snapshot, evidence_capture_checkpoint())
    detail = MemoryUpdateRefusal(
        identity.run_id,
        "e" * SHA256_HEX_LENGTH,
        MemoryUpdateRefusalReason.INVALID_EVENT,
        identity.evidence_cutoff,
    )
    memory_refusal = ResearchRefusal(
        detail.refusal_id,
        memory_update_refusal=detail,
    )
    reference = ProductionResearchReference(
        identity,
        LifecyclePhase.RUN_RESEARCH,
        artifact,
        snapshot.inputs.position_snapshot,
        ResearchCheckpoint(),
        memory_recorded_at=identity.evidence_cutoff,
        memory_refusal=memory_refusal,
    )

    assert reference.memory_refusal == memory_refusal
    invalid_replacements = (
        {"phase": LifecyclePhase.BUILD_DOSSIERS},
        {"checkpoint": None},
        {"refusal_id": "a" * SHA256_HEX_LENGTH},
        {"memory_refusal": ResearchRefusal("f" * SHA256_HEX_LENGTH)},
        {"memory_recorded_at": None},
        {
            "memory_checkpoint": ResearchCheckpoint(("b" * SHA256_HEX_LENGTH,)),
            "memory_recorded_at": identity.evidence_cutoff,
        },
    )
    for changes in invalid_replacements:
        with pytest.raises(ValueError, match="lifecycle stream checkpoint order is invalid"):
            replace(reference, **changes)


def test_advance_rejects_missing_attention_owned_subject_evidence() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    evidence = evidence_capture_checkpoint()
    artifact = attention_artifact(identity, snapshot, evidence)
    capability = replace(
        _advance(
            _DecisionOnRefusalLedger(PerformDossierBuild(identity, snapshot, evidence, artifact))
        ),
        evidence_vault=cast("EvidenceVault", _MissingSubjectEvidenceVault()),
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle ledger returned an incomplete checkpoint result",
    ):
        capability(
            cycle=_cycle(),
            mode="champion",
            idempotency_key="concurrent-request",
        )


@pytest.mark.parametrize(
    "decision_kind",
    ["dossier", "research", "memory", "portfolio"],
)
def test_advance_rejects_stage_three_effects_for_an_input_refusal(decision_kind: str) -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    artifact = attention_artifact(identity, snapshot, evidence_capture_checkpoint())
    if decision_kind == "dossier":
        decision: LifecycleDecision = PerformDossierBuild(
            identity,
            snapshot,
            evidence_capture_checkpoint(),
            artifact,
        )
    elif decision_kind == "research":
        decision = PerformResearch(identity, DOSSIER_CHECKPOINT, artifact)
    elif decision_kind == "memory":
        decision = PerformMemoryUpdate(identity, RESEARCH_CHECKPOINT, artifact)
    else:
        assert decision_kind == "portfolio"
        decision = PerformPortfolioConstruction(
            identity,
            DOSSIER_CHECKPOINT,
            RESEARCH_CHECKPOINT,
            MEMORY_CHECKPOINT,
            artifact,
            None,
        )
    capability = _advance(_DecisionOnRefusalLedger(decision))

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle ledger returned an incomplete checkpoint result",
    ):
        capability(cycle=_cycle(), mode="invalid", idempotency_key="invalid-stage-three")


def test_advance_rejects_a_missing_effect_free_research_replay() -> None:
    identity = pinned_run_identity(_request())
    artifact = attention_artifact(
        identity,
        universe_snapshot(identity),
        evidence_capture_checkpoint(),
    )
    decision = PerformPortfolioConstruction(
        identity,
        DOSSIER_CHECKPOINT,
        RESEARCH_CHECKPOINT,
        MEMORY_CHECKPOINT,
        artifact,
        None,
    )
    capability = replace(
        _advance(_DecisionOnRefusalLedger(decision)),
        production_research=cast(
            "ProductionResearch",
            _MissingPortfolioReplay(typed_research_policy()),
        ),
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle ledger returned an incomplete checkpoint result",
    ):
        capability(cycle=_cycle(), mode="champion", idempotency_key="concurrent-request")


@pytest.mark.parametrize(
    ("reason", "expected_code"),
    [
        (PortfolioRefusalReason.STALE_INPUT, InputRefusalCode.STALE_PORTFOLIO_INPUT),
        (
            PortfolioRefusalReason.CONTRADICTORY_INPUT,
            InputRefusalCode.CONTRADICTORY_PORTFOLIO_INPUT,
        ),
        (
            PortfolioRefusalReason.INCOMPLETE_INPUT,
            InputRefusalCode.MISSING_PORTFOLIO_INPUT,
        ),
        (PortfolioRefusalReason.INVALID_REQUEST, InputRefusalCode.INVALID_PORTFOLIO_INPUT),
        (
            PortfolioRefusalReason.AUTHORITY_VIOLATION,
            InputRefusalCode.INVALID_PORTFOLIO_INPUT,
        ),
    ],
)
def test_prepare_command_maps_portfolio_source_refusals(
    reason: PortfolioRefusalReason,
    expected_code: InputRefusalCode,
) -> None:
    request = _request("portfolio-source-refusal")
    capability = replace(
        _advance(
            _DecisionOnRefusalLedger(
                AdvanceReceipt.failed_closed(
                    AdvanceFailureReason.INVALID_DURABLE_STATE,
                    cycle=request.session,
                )
            )
        ),
        portfolio_input_source=_FixedPortfolioInputSource(reason),
    )

    prepared = capability._prepare_command(request, RECEIPT_RECORDED_AT)

    assert prepared == InputRefusal(
        expected_code,
        request.idempotency_key,
        request.session,
    )


@pytest.mark.parametrize(
    ("input_kind", "expected_code"),
    [
        ("contradictory", InputRefusalCode.CONTRADICTORY_PORTFOLIO_INPUT),
        ("stale", InputRefusalCode.STALE_PORTFOLIO_INPUT),
    ],
)
def test_prepare_command_rejects_portfolio_input_time_violations(
    input_kind: str,
    expected_code: InputRefusalCode,
) -> None:
    request = _request(f"portfolio-{input_kind}")
    capability = _advance(
        _DecisionOnRefusalLedger(
            AdvanceReceipt.failed_closed(
                AdvanceFailureReason.INVALID_DURABLE_STATE,
                cycle=request.session,
            )
        )
    )
    loaded = capability.universe_source.load()
    assert isinstance(loaded, UniverseInputs)
    base = typed_portfolio_inputs()
    if input_kind == "contradictory":
        observed_at = base.observed_at
        available_at = UtcInstant(loaded.evidence_cutoff.value + timedelta(seconds=1))
    else:
        assert input_kind == "stale"
        observed_at = UtcInstant(
            loaded.evidence_cutoff.value
            - timedelta(seconds=capability.portfolio_policy.maximum_input_age_seconds + 1)
        )
        available_at = UtcInstant(loaded.evidence_cutoff.value - timedelta(seconds=1))
    inputs = PortfolioInputSet.create(
        position_snapshot=base.position_snapshot,
        cash=base.cash,
        cash_currency=base.cash_currency,
        source_identity=base.source_identity,
        observed_at=observed_at,
        available_at=available_at,
        data_regime=base.data_regime,
        risk_inputs=base.risk_inputs,
    )
    capability = replace(
        capability,
        portfolio_input_source=_FixedPortfolioInputSource(inputs),
    )

    prepared = capability._prepare_command(request, RECEIPT_RECORDED_AT)

    assert prepared == InputRefusal(
        expected_code,
        request.idempotency_key,
        request.session,
    )


@pytest.mark.parametrize("invalid_material", ["attention", "inputs", "subject"])
def test_portfolio_request_rejects_incomplete_internal_material(
    invalid_material: str,
) -> None:
    request = _request(f"portfolio-request-{invalid_material}")
    identity = pinned_run_identity(request)
    snapshot = universe_snapshot(identity)
    capture = evidence_capture_checkpoint()
    artifact = attention_artifact(identity, snapshot, capture)
    command = AdvanceCommand(
        request,
        identity,
        snapshot,
        typed_portfolio_inputs(),
        attention_selection=artifact,
    )
    run = ProductionResearchRun(RESEARCH_CHECKPOINT, (), None, None)
    if invalid_material == "attention":
        command = replace(command, attention_selection=None)
    elif invalid_material == "inputs":
        command = replace(command, portfolio_inputs=object())
    else:
        assert invalid_material == "subject"
        resolution = _PortfolioResolutionView(
            "missing-subject-request",
            _PortfolioCioView(EquityInstrumentIdentity("alpaca-paper", "missing-subject", "NYSE")),
        )
        run = ProductionResearchRun(
            RESEARCH_CHECKPOINT,
            (cast("ProductionResearchResolution", resolution),),
            None,
            None,
        )
    capability = _advance(
        _DecisionOnRefusalLedger(
            AdvanceReceipt.failed_closed(
                AdvanceFailureReason.INVALID_DURABLE_STATE,
                cycle=request.session,
            )
        )
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle ledger returned an incomplete checkpoint result",
    ):
        capability._portfolio_request(command, run, DOSSIER_CHECKPOINT, MEMORY_CHECKPOINT)


def test_advance_receipts_round_trip_through_one_versioned_public_envelope() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    crypto_cycle = CryptoDecisionWindow(
        UtcInstant.from_datetime(datetime(2026, 8, 22, tzinfo=UTC)),
        UtcInstant.from_datetime(datetime(2026, 8, 22, tzinfo=UTC) + timedelta(hours=1)),
    )
    receipts = (
        _advanced_receipt(identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT),
        AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY),
        AdvanceReceipt.failed_closed(
            AdvanceFailureReason.STALE_UNIVERSE_INPUT,
            cycle=MarketSession(date(2026, 8, 21)),
        ),
        AdvanceReceipt.failed_closed(
            AdvanceFailureReason.UNSUPPORTED_CYCLE,
            cycle=crypto_cycle,
        ),
    )

    envelopes = tuple(receipt.to_payload() for receipt in receipts)

    assert tuple(parse_advance_receipt(envelope) for envelope in envelopes) == receipts
    assert all(envelope["envelope_schema_version"] == 1 for envelope in envelopes)
    assert all(envelope["record_kind"] == "lifecycle_receipt" for envelope in envelopes)
    assert all(
        envelope["authority_scope"] == "investment_operating_system" for envelope in envelopes
    )
    assert envelopes[0]["cycle"] == identity.cycle.to_payload()
    assert envelopes[0]["relevant_at"] == RECEIPT_RECORDED_AT.isoformat()
    assert envelopes[0]["available_at"] == RECEIPT_RECORDED_AT.isoformat()
    assert envelopes[0]["data_regime"] == identity.data_regime
    assert envelopes[2]["cycle"] == MarketSession(date(2026, 8, 21)).to_payload()
    assert envelopes[3]["cycle"] == crypto_cycle.to_payload()
    assert envelopes[3]["payload_discriminator"] == "unsupported_cycle_advance_receipt"
    assert receipts[3].cycle == crypto_cycle


def test_advance_receipt_parser_rejects_crypto_as_success_even_when_resealed() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    envelope = _advanced_receipt(
        identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
    ).to_payload()
    crypto_cycle = CryptoDecisionWindow(
        UtcInstant.from_datetime(datetime(2026, 8, 22, tzinfo=UTC)),
        UtcInstant.from_datetime(datetime(2026, 8, 23, tzinfo=UTC)),
    ).to_payload()
    envelope["cycle"] = crypto_cycle
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    pinned = payload["pinned_run_identity"]
    assert isinstance(pinned, dict)
    pinned["cycle"] = crypto_cycle
    _reseal_receipt_with_pinned_identity(envelope)

    assert parse_advance_receipt(envelope) is None


def test_advance_receipt_rejects_a_data_regime_not_bound_into_attention() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    envelope = _advanced_receipt(
        identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
    ).to_payload()
    envelope["data_regime"] = "alpaca:iex"
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    pinned = payload["pinned_run_identity"]
    assert isinstance(pinned, dict)
    pinned["data_regime"] = "alpaca:iex"
    _reseal_receipt_with_pinned_identity(envelope)

    parsed = parse_advance_receipt(envelope)

    assert parsed is None


def test_advance_receipt_parser_rejects_resealed_invalid_research_references() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    success = _advanced_receipt(
        identity,
        snapshot,
        AdvanceRecovery.FRESH,
        RECEIPT_RECORDED_AT,
    ).to_payload()
    success_payload = success["payload"]
    assert isinstance(success_payload, dict)
    success_payload["dossier_checkpoint"] = {}
    _reseal_receipt(success)

    refusal = AdvanceReceipt.failed_closed(
        AdvanceFailureReason.RESEARCH_FAILED,
        cycle=_request().session,
        research_refusal=ResearchRefusal("f" * SHA256_HEX_LENGTH),
    ).to_payload()
    refusal_payload = refusal["payload"]
    assert isinstance(refusal_payload, dict)
    refusal_payload["research_refusal_id"] = "invalid"
    _reseal_receipt(refusal)

    assert parse_advance_receipt(success) is None
    assert parse_advance_receipt(refusal) is None


def test_advance_receipt_rejects_a_completion_time_before_its_evidence_cutoff() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)

    with pytest.raises(ValueError, match="advanced receipt requires completed recovery facts"):
        _advanced_receipt(
            identity,
            snapshot,
            AdvanceRecovery.FRESH,
            UtcInstant.from_datetime(identity.evidence_cutoff.value - timedelta(seconds=1)),
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("envelope_schema_version",), True),
        (("payload_discriminator",), "unknown_receipt"),
        (("relevant_at",), "2026-08-21T22:00:00"),
        (("content_hash",), "f" * SHA256_HEX_LENGTH),
        (("material_fingerprints", "instrument_snapshot"), True),
        (("payload", "disposition"), "unknown"),
        (("payload", "universe_snapshot_id"), "not-a-hash"),
        (("payload", "evidence_policy_id"), "not-a-hash"),
        (("payload", "evidence_artifact_ids"), True),
        (("payload", "evidence_artifact_ids"), ["invalid"]),
        (
            ("payload", "evidence_artifact_ids"),
            ["b" * SHA256_HEX_LENGTH, "a" * SHA256_HEX_LENGTH],
        ),
        (("payload", "evidence_refusal_ids"), True),
        (("payload", "evidence_refusal_ids"), ["invalid"]),
        (("payload", "attention_artifact", "content_hash"), "f" * SHA256_HEX_LENGTH),
        (
            ("payload", "evidence_refusal_ids"),
            ["b" * SHA256_HEX_LENGTH, "a" * SHA256_HEX_LENGTH],
        ),
    ],
)
def test_advance_receipt_parser_rejects_hostile_or_changed_envelopes(
    path: tuple[str, ...],
    value: object,
) -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    envelope = deepcopy(
        _advanced_receipt(
            identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
        ).to_payload()
    )
    target = envelope
    for field in path[:-1]:
        nested = target[field]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value

    assert parse_advance_receipt(envelope) is None


def test_advance_receipt_parser_rejects_resealed_unsorted_evidence_references() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    envelope = deepcopy(
        _advanced_receipt(
            identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
        ).to_payload()
    )
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    payload["evidence_artifact_ids"] = [
        "b" * SHA256_HEX_LENGTH,
        "a" * SHA256_HEX_LENGTH,
    ]
    _reseal_receipt_with_pinned_identity(envelope)

    assert parse_advance_receipt(envelope) is None


@pytest.mark.parametrize(
    "case",
    [
        "non_mapping",
        "non_string_root_key",
        "wrong_root_fields",
        "missing_root_field",
        "non_mapping_payload",
        "wrong_payload_fields",
        "missing_payload_field",
        "non_mapping_fingerprints",
        "non_string_fingerprint_key",
        "partial_fingerprints",
        "invalid_cycle",
        "invalid_checkpoint",
        "invalid_pinned_identity_fields",
        "invalid_pinned_identity_fact",
        "invalid_pinned_identity_type",
        "invalid_pinned_cycle",
        "non_string_disposition",
        "invalid_failure_reason",
        "non_string_failure_reason",
        "invalid_recovery",
        "non_string_recovery",
        "non_string_relevant_at",
        "equivalent_relevant_at_subclass",
        "malformed_available_at",
        "noncanonical_available_at",
        "equivalent_available_at_subclass",
        "non_string_data_regime",
        "invalid_data_regime",
        "equivalent_data_regime_subclass",
        "inconsistent_receipt_shape",
        "null_spoofed_failure_reason",
    ],
)
def test_advance_receipt_parser_rejects_hostile_shapes(  # noqa: PLR0912, PLR0915 - mutate every hostile envelope seam explicitly.
    case: str,
) -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    value: object = deepcopy(
        _advanced_receipt(
            identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
        ).to_payload()
    )
    if case == "non_mapping":
        value = []
    else:
        assert isinstance(value, dict)
        if case == "non_string_root_key":
            value[1] = "hostile"
        elif case == "wrong_root_fields":
            value["extra"] = "hostile"
        elif case == "missing_root_field":
            value.pop("content_hash")
        elif case == "non_mapping_payload":
            value["payload"] = []
        elif case == "non_mapping_fingerprints":
            value["material_fingerprints"] = []
        elif case == "non_string_relevant_at":
            value["relevant_at"] = True
        elif case == "equivalent_relevant_at_subclass":
            value["relevant_at"] = _StringSubclass(exact_text(value["relevant_at"]))
        elif case == "malformed_available_at":
            value["available_at"] = "not-a-time"
        elif case == "noncanonical_available_at":
            value["available_at"] = exact_text(value["available_at"]).replace("+00:00", "Z")
        elif case == "equivalent_available_at_subclass":
            value["available_at"] = _StringSubclass(exact_text(value["available_at"]))
        elif case == "non_string_data_regime":
            value["data_regime"] = True
        elif case == "invalid_data_regime":
            value["data_regime"] = "Invalid Regime"
        elif case == "equivalent_data_regime_subclass":
            value["data_regime"] = _StringSubclass(exact_text(value["data_regime"]))
        elif case == "invalid_cycle":
            value["cycle"] = {}
        else:
            receipt_payload = value["payload"]
            fingerprints = value["material_fingerprints"]
            assert isinstance(receipt_payload, dict)
            assert isinstance(fingerprints, dict)
            if case == "wrong_payload_fields":
                receipt_payload["extra"] = "hostile"
            elif case == "missing_payload_field":
                receipt_payload.pop("disposition")
            elif case == "non_string_fingerprint_key":
                fingerprints[1] = "a" * SHA256_HEX_LENGTH
            elif case == "partial_fingerprints":
                fingerprints.pop("configuration")
                pinned = receipt_payload["pinned_run_identity"]
                assert isinstance(pinned, dict)
                pinned["data_regime"] = True
            elif case == "invalid_checkpoint":
                receipt_payload["completed_checkpoint"] = {}
            elif case == "invalid_pinned_identity_fields":
                receipt_payload["pinned_run_identity"] = {}
            elif case == "invalid_pinned_identity_fact":
                pinned = receipt_payload["pinned_run_identity"]
                assert isinstance(pinned, dict)
                pinned["run_id"] = "f" * SHA256_HEX_LENGTH
            elif case == "invalid_pinned_identity_type":
                pinned = receipt_payload["pinned_run_identity"]
                assert isinstance(pinned, dict)
                pinned["configuration_version"] = True
            elif case == "invalid_pinned_cycle":
                pinned = receipt_payload["pinned_run_identity"]
                assert isinstance(pinned, dict)
                pinned["cycle"] = {}
            elif case == "non_string_disposition":
                receipt_payload["disposition"] = True
            elif case == "invalid_failure_reason":
                receipt_payload["failure_reason"] = "unknown"
            elif case == "non_string_failure_reason":
                receipt_payload["failure_reason"] = True
            elif case == "invalid_recovery":
                receipt_payload["recovery"] = "unknown"
            elif case == "non_string_recovery":
                receipt_payload["recovery"] = True
            elif case == "null_spoofed_failure_reason":
                receipt_payload["failure_reason"] = _NoneSpoof()
            else:
                assert case == "inconsistent_receipt_shape"
                receipt_payload["failure_reason"] = AdvanceFailureReason.INVALID_SESSION.value

    assert parse_advance_receipt(value) is None


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "configuration_hash",
        "research_policy_hash",
        "data_regime",
        "evidence_cutoff",
        "instrument_snapshot_hash",
        "position_snapshot_hash",
        "eligibility_policy_hash",
    ],
)
def test_advance_receipt_parser_rejects_equivalent_pinned_text_subclasses(field: str) -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    envelope = deepcopy(
        _advanced_receipt(
            identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
        ).to_payload()
    )
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    pinned = payload["pinned_run_identity"]
    assert isinstance(pinned, dict)
    pinned[field] = _StringSubclass(exact_text(pinned[field]))

    assert parse_advance_receipt(envelope) is None


def test_advance_receipt_parser_rejects_an_equivalent_configuration_version_subclass() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    envelope = deepcopy(
        _advanced_receipt(
            identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
        ).to_payload()
    )
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    pinned = payload["pinned_run_identity"]
    assert isinstance(pinned, dict)
    pinned["configuration_version"] = _IntSubclass(1)

    assert parse_advance_receipt(envelope) is None


def test_advance_receipt_parser_rejects_a_self_consistent_naive_cutoff() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    envelope = deepcopy(
        _advanced_receipt(
            identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
        ).to_payload()
    )
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    pinned = payload["pinned_run_identity"]
    assert isinstance(pinned, dict)
    naive_cutoff = identity.evidence_cutoff.value.replace(tzinfo=None).isoformat()
    pinned["evidence_cutoff"] = naive_cutoff
    envelope["relevant_at"] = naive_cutoff
    envelope["available_at"] = naive_cutoff
    _reseal_receipt_with_pinned_identity(envelope)

    assert parse_advance_receipt(envelope) is None


@pytest.mark.parametrize("completion_point", ["start", "reconcile"])
def test_advance_returns_a_concurrent_checkpoint_receipt(completion_point: str) -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    receipt = _advanced_receipt(
        identity,
        snapshot,
        AdvanceRecovery.PREVIOUSLY_COMPLETED,
        RECEIPT_RECORDED_AT,
    )
    capability = _advance(ConcurrentCompletionLedger(completion_point, receipt))

    observed = capability(
        cycle=_cycle(),
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert observed.disposition is receipt.disposition
    assert observed.completed_phase is receipt.completed_phase
    assert observed.pinned_run_identity is receipt.pinned_run_identity
    assert observed.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED


@pytest.mark.parametrize("failure_point", ["reconcile_failure", "pin_failure"])
def test_advance_returns_a_durable_checkpoint_failure(failure_point: str) -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    capability = _advance(
        ConcurrentCompletionLedger(
            failure_point,
            _advanced_receipt(
                identity,
                snapshot,
                AdvanceRecovery.PREVIOUSLY_COMPLETED,
                RECEIPT_RECORDED_AT,
            ),
        )
    )

    observed = capability(
        cycle=_cycle(),
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert observed == AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)


def test_advance_reports_a_checkpoint_completed_during_pinning() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    capability = _advance(
        ConcurrentCompletionLedger(
            "pin_observed",
            _advanced_receipt(
                identity,
                snapshot,
                AdvanceRecovery.PREVIOUSLY_COMPLETED,
                RECEIPT_RECORDED_AT,
            ),
        )
    )

    observed = capability(
        cycle=_cycle(),
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert observed == _advanced_receipt(
        identity,
        snapshot,
        AdvanceRecovery.PREVIOUSLY_COMPLETED,
        RECEIPT_RECORDED_AT,
    )


def test_advance_rejects_an_incomplete_checkpoint_result() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    capability = _advance(
        ConcurrentCompletionLedger(
            "incomplete_reconcile",
            _advanced_receipt(
                identity,
                snapshot,
                AdvanceRecovery.PREVIOUSLY_COMPLETED,
                RECEIPT_RECORDED_AT,
            ),
        )
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle ledger returned an incomplete checkpoint result",
    ):
        capability(
            cycle=_cycle(),
            mode="champion",
            idempotency_key="concurrent-request",
        )


def test_advance_rejects_evidence_effect_for_an_input_refusal() -> None:
    identity = pinned_run_identity(_request())
    capability = _advance(
        _DecisionOnRefusalLedger(
            PerformEvidenceCapture(identity, universe_snapshot(identity)),
        )
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle ledger returned an incomplete checkpoint result",
    ):
        capability(
            cycle=_cycle(),
            mode="invalid",
            idempotency_key="concurrent-request",
        )


def test_advance_rejects_evidence_receipt_for_an_input_refusal() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    capability = _advance(
        _DecisionOnRefusalLedger(
            _advanced_receipt(
                identity,
                snapshot,
                AdvanceRecovery.PREVIOUSLY_COMPLETED,
                RECEIPT_RECORDED_AT,
            )
        )
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle ledger returned an incomplete checkpoint result",
    ):
        capability(
            cycle=_cycle(),
            mode="invalid",
            idempotency_key="concurrent-request",
        )


def test_advance_rejects_attention_effect_for_an_input_refusal() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    capture = evidence_capture_checkpoint()
    capability = _advance(
        _DecisionOnRefusalLedger(
            PerformAttentionSelection(
                identity,
                snapshot,
                capture,
                (),
            )
        )
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle ledger returned an incomplete checkpoint result",
    ):
        capability(
            cycle=_cycle(),
            mode="invalid",
            idempotency_key="concurrent-request",
        )


def test_advance_translates_contradictory_attention_history_to_a_typed_refusal() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    capture = evidence_capture_checkpoint()
    ledger = _ContradictoryAttentionHistoryLedger(
        identity,
        snapshot,
        attention_artifact(identity, snapshot, capture),
    )
    capability = _advance(ledger)

    receipt = capability(
        cycle=_cycle(),
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert receipt.failure_reason is AdvanceFailureReason.ATTENTION_SELECTION_FAILED
    assert receipt.attention_refusal_reason is AttentionRefusalReason.CONTRADICTORY_EVIDENCE


def test_advance_translates_corrupt_attention_inputs_to_a_typed_refusal() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    capture = evidence_capture_checkpoint()
    ledger = _ContradictoryAttentionHistoryLedger(
        identity,
        snapshot,
        attention_artifact(identity, snapshot, capture),
        AttentionRefusalReason.CORRUPT_EVIDENCE,
    )
    capability = replace(_advance(ledger), attention_inputs=_FailingAttentionInputs())

    receipt = capability(
        cycle=_cycle(),
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert receipt.attention_refusal_reason is AttentionRefusalReason.CORRUPT_EVIDENCE


def test_advance_validates_the_complete_checkpoint_before_loading_attention_inputs() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    capture = evidence_capture_checkpoint()
    ledger = _ContradictoryAttentionHistoryLedger(
        identity,
        snapshot,
        attention_artifact(identity, snapshot, capture),
        AttentionRefusalReason.CORRUPT_EVIDENCE,
    )
    capability = replace(
        _advance(ledger),
        evidence_capture=_InvalidCheckpointEvidenceCapture(),
        attention_inputs=_UnexpectedAttentionInputs(),
    )

    receipt = capability(
        cycle=_cycle(),
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert receipt.failure_reason is AdvanceFailureReason.ATTENTION_SELECTION_FAILED
    assert receipt.attention_refusal_reason is AttentionRefusalReason.CORRUPT_EVIDENCE


@pytest.mark.parametrize("invalid_inputs", ["corrupt", "refused"])
def test_advance_revalidates_completed_attention_inputs(
    invalid_inputs: str,
) -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    receipt = _advanced_receipt(
        identity,
        snapshot,
        AdvanceRecovery.PREVIOUSLY_COMPLETED,
        RECEIPT_RECORDED_AT,
    )
    capability = _advance(_DecisionOnRefusalLedger(receipt))
    capability = replace(
        capability,
        attention_inputs=(
            _FailingAttentionInputs()
            if invalid_inputs == "corrupt"
            else _FailingAttentionInputs(AttentionRefusalReason.MISSING_EVIDENCE)
        ),
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle ledger returned an incomplete checkpoint result",
    ):
        capability(
            cycle=_cycle(),
            mode="champion",
            idempotency_key="concurrent-request",
        )
