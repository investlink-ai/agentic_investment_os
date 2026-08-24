from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

from agentic_investment_os.application.governance import (
    ConstitutionRegistry,
    ConstitutionStatus,
    Govern,
)
from agentic_investment_os.domain.governance import (
    ACTIVE_CONSTITUTION,
    ApprovalVerification,
    ConstitutionActivation,
    ConstitutionArtifact,
    ConstitutionGovernanceHistory,
    ConstitutionGovernanceStatus,
    ConstitutionUse,
    GovernanceCommand,
    GovernanceDisposition,
    GovernanceReceipt,
    GovernanceStateError,
    OperatorApprovalProof,
    OperatorApprovalVerifier,
    SessionBoundaryRelation,
    activate_constitution,
    constitution_for_use,
    decide_governance,
    reconstruct_constitution_governance,
    validate_constitution_uses,
)
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.temporal import UtcInstant
from tests._governance import (
    ACTIVATION_SESSION,
    APPROVED_SESSION,
    HashApprovalVerifier,
    RecordedSessionEligibility,
    amended_constitution,
    approval_for,
)

RECORDED_AT = UtcInstant.from_datetime(datetime(2026, 8, 21, 20, 5, tzinfo=UTC))


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


@dataclass(frozen=True, slots=True)
class InvalidClock:
    def now(self) -> datetime:
        # This hostile boundary double deliberately violates the clock protocol.
        return "invalid"  # type: ignore[return-value]


class MemoryGovernanceLedger:
    def __init__(self) -> None:
        self.history = ConstitutionGovernanceHistory()

    def govern(
        self,
        command: GovernanceCommand,
        verifier: OperatorApprovalVerifier,
        verification: ApprovalVerification | None,
        recorded_at: UtcInstant,
    ) -> GovernanceReceipt:
        reconstruct_constitution_governance(self.history, verifier.verify)
        decision = decide_governance(self.history, command, verification, recorded_at)
        if decision.record is not None:
            self.history = self.history.append(decision.record)
        return decision.receipt

    def resolve_constitution(
        self,
        session: MarketSession,
        verifier: OperatorApprovalVerifier | None,
        recorded_at: UtcInstant,
    ) -> ConstitutionActivation:
        if verifier is not None:
            reconstruct_constitution_governance(self.history, verifier.verify)
        activation = activate_constitution(self.history, session, recorded_at)
        if activation.record is not None:
            self.history = self.history.append(activation.record)
        return activation

    def constitution_for(
        self,
        session: MarketSession,
        verifier: OperatorApprovalVerifier | None,
    ) -> ConstitutionArtifact:
        verification = HashApprovalVerifier().verify if verifier is None else verifier.verify
        return reconstruct_constitution_governance(self.history, verification).constitution_for(
            session
        )

    def constitution_for_use(
        self,
        use: ConstitutionUse,
        verifier: OperatorApprovalVerifier | None,
    ) -> ConstitutionArtifact:
        verification = HashApprovalVerifier().verify if verifier is None else verifier.verify
        return constitution_for_use(self.history, verification, use)

    def next_activation_session(
        self,
        verifier: OperatorApprovalVerifier | None,
    ) -> MarketSession | None:
        verification = HashApprovalVerifier().verify if verifier is None else verifier.verify
        state = reconstruct_constitution_governance(self.history, verification)
        return None if not state.pending else state.pending[0].activation_session

    def validate_constitution_uses(
        self,
        uses: tuple[ConstitutionUse, ...],
        verifier: OperatorApprovalVerifier | None,
    ) -> None:
        verification = HashApprovalVerifier().verify if verifier is None else verifier.verify
        validate_constitution_uses(self.history, verification, uses)

    def rebuild_constitution_status(
        self,
        verifier: OperatorApprovalVerifier | None,
        uses: tuple[ConstitutionUse, ...] = (),
    ) -> ConstitutionGovernanceStatus:
        verification = HashApprovalVerifier().verify if verifier is None else verifier.verify
        state = validate_constitution_uses(self.history, verification, uses)
        return ConstitutionGovernanceStatus.from_state(state)


@dataclass(frozen=True, slots=True)
class InvalidEligibility:
    def relation(self, session: MarketSession, recorded_at: UtcInstant) -> SessionBoundaryRelation:
        del session, recorded_at
        return SessionBoundaryRelation.INELIGIBLE


@dataclass(frozen=True, slots=True)
class InvalidBoundaryPolicy:
    def relation(self, session: MarketSession, recorded_at: UtcInstant) -> SessionBoundaryRelation:
        del session, recorded_at
        # This hostile boundary double deliberately violates the policy protocol.
        return "invalid"  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class InvalidVerifier:
    def verify(self, proof: OperatorApprovalProof) -> ApprovalVerification:
        del proof
        # This hostile boundary double deliberately violates the verifier protocol.
        return "invalid"  # type: ignore[return-value]


def _govern(
    ledger: MemoryGovernanceLedger,
    *,
    verifier: OperatorApprovalVerifier | None = None,
    eligibility: RecordedSessionEligibility
    | InvalidEligibility
    | InvalidBoundaryPolicy
    | None = None,
    recorded_at: UtcInstant = RECORDED_AT,
) -> Govern:
    return Govern(
        ledger,
        HashApprovalVerifier() if verifier is None else verifier,
        RecordedSessionEligibility() if eligibility is None else eligibility,
        FixedClock(recorded_at.value),
    )


def _schedule(capability: Govern) -> GovernanceReceipt:
    artifact = amended_constitution()
    return capability(
        request_identity="constitution-amendment-2",
        artifact=artifact.to_payload(),
        activation_session=ACTIVATION_SESSION.to_payload(),
        approval_proof=approval_for(artifact).to_payload(),
    )


def test_govern_refuses_ineligible_and_invalid_verifier_results() -> None:
    malformed = _govern(MemoryGovernanceLedger())(
        request_identity="malformed-request",
        artifact={"invalid": True},
        activation_session=ACTIVATION_SESSION.to_payload(),
        approval_proof=None,
    )
    assert malformed.disposition is GovernanceDisposition.REFUSED

    ineligible = _schedule(_govern(MemoryGovernanceLedger(), eligibility=InvalidEligibility()))
    assert ineligible.disposition is GovernanceDisposition.REFUSED
    assert ineligible.reason is not None
    assert ineligible.reason.value == "ineligible_session"

    invalid_signature = _schedule(_govern(MemoryGovernanceLedger(), verifier=InvalidVerifier()))
    assert invalid_signature.disposition is GovernanceDisposition.REFUSED
    assert invalid_signature.reason is not None
    assert invalid_signature.reason.value == "invalid_signature"


def test_govern_activate_reports_invalid_due_idle_and_replayed_boundaries() -> None:
    ledger = MemoryGovernanceLedger()
    capability = _govern(ledger)

    invalid = capability.activate(session="invalid")
    assert invalid.disposition is GovernanceDisposition.REFUSED

    idle = capability.activate(session=MarketSession(date(2026, 8, 21)).to_payload())
    assert idle.disposition is GovernanceDisposition.REPLAYED
    assert idle.constitution is not None

    _schedule(capability)
    early = capability.activate(session=ACTIVATION_SESSION.to_payload())
    assert early.disposition is GovernanceDisposition.REFUSED
    ineligible = capability.activate(session=MarketSession(date(2026, 8, 25)).to_payload())
    assert ineligible.disposition is GovernanceDisposition.REFUSED
    assert ineligible.reason is not None
    assert ineligible.reason.value == "ineligible_session"

    activation_time = UtcInstant.from_datetime(datetime(2026, 8, 24, 20, 5, tzinfo=UTC))
    activation_capability = _govern(ledger, recorded_at=activation_time)
    activated = activation_capability.activate(session=ACTIVATION_SESSION.to_payload())
    assert activated.disposition is GovernanceDisposition.ACTIVATED
    replayed = activation_capability.activate(session=ACTIVATION_SESSION.to_payload())
    assert replayed.disposition is GovernanceDisposition.REPLAYED


def test_registry_status_and_invalid_clock_preserve_their_boundaries() -> None:
    ledger = MemoryGovernanceLedger()
    registry = ConstitutionRegistry(ledger, HashApprovalVerifier(), RecordedSessionEligibility())
    assert registry.resolve(ACTIVATION_SESSION, RECORDED_AT, None) == ACTIVE_CONSTITUTION
    assert (
        registry.resolve(
            ACTIVATION_SESSION,
            RECORDED_AT,
            ConstitutionUse(ACTIVATION_SESSION, ACTIVE_CONSTITUTION.reference, RECORDED_AT),
        )
        == ACTIVE_CONSTITUTION
    )
    with pytest.raises(GovernanceStateError, match="pinned Constitution does not match"):
        registry.resolve(
            APPROVED_SESSION,
            RECORDED_AT,
            ConstitutionUse(ACTIVATION_SESSION, ACTIVE_CONSTITUTION.reference, RECORDED_AT),
        )
    status = ConstitutionStatus(ledger)(())
    assert status.active.version == 1

    _schedule(_govern(ledger))
    ineligible_registry = ConstitutionRegistry(
        ledger,
        HashApprovalVerifier(),
        InvalidEligibility(),
    )
    with pytest.raises(GovernanceStateError, match="exact current Market Session"):
        ineligible_registry.activate_due(RECORDED_AT)
    with pytest.raises(GovernanceStateError, match="exact current Market Session"):
        registry.resolve(ACTIVATION_SESSION, RECORDED_AT, None)
    late = UtcInstant.from_datetime(datetime(2026, 8, 25, 20, 5, tzinfo=UTC))
    with pytest.raises(GovernanceStateError, match="missed Constitution activation boundary"):
        registry.resolve(ACTIVATION_SESSION, late, None)
    with pytest.raises(GovernanceStateError, match="pinned Constitution does not match"):
        registry.resolve(
            APPROVED_SESSION,
            RECORDED_AT,
            ConstitutionUse(
                APPROVED_SESSION,
                ConstitutionArtifact.create(version=1, clauses=("different",)).reference,
                RECORDED_AT,
            ),
        )

    invalid_clock = Govern(
        ledger,
        HashApprovalVerifier(),
        RecordedSessionEligibility(),
        InvalidClock(),
    )
    with pytest.raises(RuntimeError, match="governance clock must return"):
        _schedule(invalid_clock)

    with pytest.raises(RuntimeError, match="boundary policy returned an invalid relation"):
        _schedule(_govern(ledger, eligibility=InvalidBoundaryPolicy()))


def test_registry_activates_globally_while_preserving_a_same_session_historical_pin() -> None:
    ledger = MemoryGovernanceLedger()
    registry = ConstitutionRegistry(ledger, HashApprovalVerifier(), RecordedSessionEligibility())
    pinned_at = UtcInstant.from_datetime(datetime(2026, 8, 21, 19, 55, tzinfo=UTC))
    historical = ConstitutionUse(
        ACTIVATION_SESSION,
        ACTIVE_CONSTITUTION.reference,
        pinned_at,
    )
    _schedule(_govern(ledger))
    activation_time = UtcInstant.from_datetime(datetime(2026, 8, 24, 20, 5, tzinfo=UTC))

    selected = registry.resolve(ACTIVATION_SESSION, activation_time, historical)

    state = reconstruct_constitution_governance(ledger.history, HashApprovalVerifier().verify)
    assert selected == ACTIVE_CONSTITUTION
    assert state.active == amended_constitution().reference
    assert state.pending == ()
    validate_constitution_uses(ledger.history, HashApprovalVerifier().verify, (historical,))


def test_govern_refuses_current_activation_even_with_an_older_valid_approval() -> None:
    ledger = MemoryGovernanceLedger()
    current = UtcInstant.from_datetime(datetime(2026, 8, 24, 20, 5, tzinfo=UTC))

    receipt = _schedule(_govern(ledger, recorded_at=current))

    assert receipt.disposition is GovernanceDisposition.REFUSED
    assert receipt.reason is not None
    assert receipt.reason.value == "non_future_activation"
    assert ledger.history.events[0].kind.value == "constitution_refused"
