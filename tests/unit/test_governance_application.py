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
    ApprovalVerification,
    ConstitutionActivation,
    ConstitutionGovernanceHistory,
    ConstitutionGovernanceStatus,
    GovernanceCommand,
    GovernanceDisposition,
    GovernanceReceipt,
    OperatorApprovalProof,
    OperatorApprovalVerifier,
    activate_constitution,
    decide_governance,
    reconstruct_constitution_governance,
)
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.temporal import UtcInstant
from tests._governance import (
    ACTIVATION_SESSION,
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

    def rebuild_constitution_status(
        self, verifier: OperatorApprovalVerifier | None
    ) -> ConstitutionGovernanceStatus:
        verification = HashApprovalVerifier().verify if verifier is None else verifier.verify
        state = reconstruct_constitution_governance(self.history, verification)
        return ConstitutionGovernanceStatus.from_state(state)


@dataclass(frozen=True, slots=True)
class InvalidEligibility:
    def is_eligible(self, session: MarketSession) -> bool:
        del session
        return False


@dataclass(frozen=True, slots=True)
class InvalidVerifier:
    def verify(self, proof: OperatorApprovalProof) -> ApprovalVerification:
        del proof
        return "invalid"  # type: ignore[return-value]


def _govern(
    ledger: MemoryGovernanceLedger,
    *,
    verifier: OperatorApprovalVerifier | None = None,
    eligibility: RecordedSessionEligibility | InvalidEligibility | None = None,
) -> Govern:
    return Govern(
        ledger,
        HashApprovalVerifier() if verifier is None else verifier,
        RecordedSessionEligibility() if eligibility is None else eligibility,
        FixedClock(RECORDED_AT.value),
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

    idle = capability.activate(session=MarketSession(date(2026, 8, 22)).to_payload())
    assert idle.disposition is GovernanceDisposition.REPLAYED
    assert idle.constitution is not None

    _schedule(capability)
    activated = capability.activate(session=ACTIVATION_SESSION.to_payload())
    assert activated.disposition is GovernanceDisposition.ACTIVATED
    replayed = capability.activate(session=ACTIVATION_SESSION.to_payload())
    assert replayed.disposition is GovernanceDisposition.REPLAYED


def test_registry_status_and_invalid_clock_preserve_their_boundaries() -> None:
    ledger = MemoryGovernanceLedger()
    registry = ConstitutionRegistry(ledger)
    assert registry.resolve(ACTIVATION_SESSION, RECORDED_AT).version == 1
    status = ConstitutionStatus(ledger)()
    assert status.active.version == 1

    invalid_clock = Govern(
        ledger,
        HashApprovalVerifier(),
        RecordedSessionEligibility(),
        InvalidClock(),
    )
    with pytest.raises(RuntimeError, match="governance clock must return"):
        _schedule(invalid_clock)
