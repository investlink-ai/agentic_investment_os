"""Schedule and resolve operator-approved Constitution amendments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from agentic_investment_os.domain.governance import (
    ApprovalVerification,
    ConstitutionArtifact,
    ConstitutionGovernanceLedger,
    ConstitutionGovernanceProjection,
    ConstitutionGovernanceStatus,
    GovernanceDisposition,
    GovernanceInputRefusal,
    GovernanceReceipt,
    GovernanceRefusalReason,
    GovernanceRequest,
    MarketSessionEligibility,
    OperatorApprovalVerifier,
    parse_governance_request,
)
from agentic_investment_os.domain.identity import MarketSession, parse_decision_cycle_identity
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ("ConstitutionRegistry", "ConstitutionStatus", "Govern", "GovernanceClock")

_CLOCK_INVALID = "governance clock must return a timezone-aware instant representable in UTC"


class GovernanceClock(Protocol):
    """Supply an aware timestamp at the composition boundary."""

    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class Govern:
    """Validate and schedule one operator-approved Constitution amendment."""

    ledger: ConstitutionGovernanceLedger
    approval_verifier: OperatorApprovalVerifier
    session_eligibility: MarketSessionEligibility
    clock: GovernanceClock

    def __call__(
        self,
        *,
        request_identity: object,
        artifact: object,
        activation_session: object,
        approval_proof: object,
    ) -> GovernanceReceipt:
        command = parse_governance_request(
            request_identity=request_identity,
            artifact=artifact,
            activation_session=activation_session,
            approval_proof=approval_proof,
        )
        recorded_at = self._now()
        verification: ApprovalVerification | None = None
        if isinstance(command, GovernanceRequest):
            eligible = self.session_eligibility.is_eligible(command.activation_session)
            if type(eligible) is not bool or not eligible:
                command = GovernanceInputRefusal(
                    GovernanceRefusalReason.INELIGIBLE_SESSION,
                    command.identity,
                    command.fingerprint,
                    command.artifact,
                    command.activation_session,
                )
            else:
                verification = self.approval_verifier.verify(command.approval)
                if type(verification) is not ApprovalVerification:
                    verification = ApprovalVerification.INVALID_SIGNATURE
        return self.ledger.govern(
            command,
            self.approval_verifier,
            verification,
            recorded_at,
        )

    def activate(self, *, session: object) -> GovernanceReceipt:
        """Activate a due amendment only at its exact eligible Market Session boundary."""
        parsed = parse_decision_cycle_identity(session)
        recorded_at = self._now()
        if type(parsed) is not MarketSession:
            return GovernanceReceipt(
                GovernanceDisposition.REFUSED,
                None,
                None,
                None,
                GovernanceRefusalReason.INVALID_ACTIVATION_SESSION,
                recorded_at,
            )
        result = self.ledger.resolve_constitution(
            parsed,
            self.approval_verifier,
            recorded_at,
        )
        if result.receipt is not None:
            return result.receipt
        return GovernanceReceipt(
            GovernanceDisposition.REPLAYED,
            None,
            result.constitution,
            None,
            None,
            recorded_at,
        )

    def _now(self) -> UtcInstant:
        try:
            return UtcInstant.from_datetime(self.clock.now())
        except (AttributeError, InvalidUtcInstantError, TypeError) as error:
            raise RuntimeError(_CLOCK_INVALID) from error


@dataclass(frozen=True, slots=True)
class ConstitutionRegistry:
    """Resolve and, when due, activate the exact Constitution for one Market Session."""

    ledger: ConstitutionGovernanceLedger
    approval_verifier: OperatorApprovalVerifier | None = None

    def resolve(self, session: MarketSession, recorded_at: UtcInstant) -> ConstitutionArtifact:
        return self.ledger.resolve_constitution(
            session,
            self.approval_verifier,
            recorded_at,
        ).artifact


@dataclass(frozen=True, slots=True)
class ConstitutionStatus:
    """Rebuild bounded Constitution governance status without changing authority."""

    projection: ConstitutionGovernanceProjection
    approval_verifier: OperatorApprovalVerifier | None = None

    def __call__(self) -> ConstitutionGovernanceStatus:
        return self.projection.rebuild_constitution_status(self.approval_verifier)
