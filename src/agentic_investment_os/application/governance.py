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
    ConstitutionUse,
    GovernanceDisposition,
    GovernanceInputRefusal,
    GovernanceReceipt,
    GovernanceRefusalReason,
    GovernanceRequest,
    GovernanceStateError,
    MarketSessionEligibility,
    OperatorApprovalVerifier,
    SessionBoundaryRelation,
    parse_governance_request,
)
from agentic_investment_os.domain.identity import MarketSession, parse_decision_cycle_identity
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ("ConstitutionRegistry", "ConstitutionStatus", "Govern", "GovernanceClock")

_CLOCK_INVALID = "governance clock must return a timezone-aware instant representable in UTC"
_BOUNDARY_POLICY_INVALID = "Market Session boundary policy returned an invalid relation"
_ACTIVATION_BOUNDARY_INVALID = "Constitution activation requires the exact current Market Session"
_MISSED_ACTIVATION = "missed Constitution activation boundary"
_PINNED_CONSTITUTION_INVALID = "pinned Constitution does not match authoritative governance history"


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
            relation = _session_relation(
                self.session_eligibility,
                command.activation_session,
                recorded_at,
            )
            if relation is SessionBoundaryRelation.INELIGIBLE:
                command = GovernanceInputRefusal(
                    GovernanceRefusalReason.INELIGIBLE_SESSION,
                    command.identity,
                    command.fingerprint,
                    command.artifact,
                    command.activation_session,
                )
            elif relation is not SessionBoundaryRelation.FUTURE:
                command = GovernanceInputRefusal(
                    GovernanceRefusalReason.NON_FUTURE_ACTIVATION,
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
        relation = _session_relation(self.session_eligibility, parsed, recorded_at)
        if relation is not SessionBoundaryRelation.CURRENT:
            reason = (
                GovernanceRefusalReason.INELIGIBLE_SESSION
                if relation is SessionBoundaryRelation.INELIGIBLE
                else GovernanceRefusalReason.INVALID_ACTIVATION_SESSION
            )
            return GovernanceReceipt(
                GovernanceDisposition.REFUSED,
                None,
                None,
                parsed,
                reason,
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
    approval_verifier: OperatorApprovalVerifier | None
    session_eligibility: MarketSessionEligibility

    def activate_due(self, recorded_at: UtcInstant) -> None:
        """Activate the global pending regime or reject a missed or invalid boundary."""
        pending = self.ledger.next_activation_session(self.approval_verifier)
        if pending is None:
            return
        relation = _session_relation(self.session_eligibility, pending, recorded_at)
        if relation is SessionBoundaryRelation.PAST:
            raise GovernanceStateError(_MISSED_ACTIVATION)
        if relation is SessionBoundaryRelation.INELIGIBLE:
            raise GovernanceStateError(_ACTIVATION_BOUNDARY_INVALID)
        if relation is SessionBoundaryRelation.CURRENT:
            self.ledger.resolve_constitution(
                pending,
                self.approval_verifier,
                recorded_at,
            )

    def resolve(
        self,
        session: MarketSession,
        recorded_at: UtcInstant,
        pinned: ConstitutionUse | None,
    ) -> ConstitutionArtifact:
        self.activate_due(recorded_at)
        pending = self.ledger.next_activation_session(self.approval_verifier)
        if pending is not None:
            relation = _session_relation(self.session_eligibility, pending, recorded_at)
            if session == pending and relation is not SessionBoundaryRelation.CURRENT:
                raise GovernanceStateError(_ACTIVATION_BOUNDARY_INVALID)
        if pinned is not None:
            if pinned.session != session:
                raise GovernanceStateError(_PINNED_CONSTITUTION_INVALID)
            try:
                return self.ledger.constitution_for_use(pinned, self.approval_verifier)
            except GovernanceStateError as error:
                raise GovernanceStateError(_PINNED_CONSTITUTION_INVALID) from error
        return self.ledger.resolve_constitution(
            session,
            self.approval_verifier,
            recorded_at,
        ).artifact

    def validate_references(self, uses: tuple[ConstitutionUse, ...]) -> None:
        """Require every existing lifecycle pin to resolve from governance history."""
        self.ledger.validate_constitution_uses(uses, self.approval_verifier)


@dataclass(frozen=True, slots=True)
class ConstitutionStatus:
    """Rebuild bounded Constitution governance status without changing authority."""

    projection: ConstitutionGovernanceProjection
    approval_verifier: OperatorApprovalVerifier | None = None

    def __call__(self, uses: tuple[ConstitutionUse, ...]) -> ConstitutionGovernanceStatus:
        return self.projection.rebuild_constitution_status(self.approval_verifier, uses)


def _session_relation(
    policy: MarketSessionEligibility,
    session: MarketSession,
    recorded_at: UtcInstant,
) -> SessionBoundaryRelation:
    relation = policy.relation(session, recorded_at)
    if type(relation) is not SessionBoundaryRelation:
        raise RuntimeError(_BOUNDARY_POLICY_INVALID)
    return relation
