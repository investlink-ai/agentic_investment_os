from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from agentic_investment_os.domain.governance import (
    ACTIVE_CONSTITUTION,
    ApprovalVerification,
    ConstitutionArtifact,
    ConstitutionGovernanceStatus,
    ConstitutionReference,
    ConstitutionUse,
    GovernanceStateError,
    OperatorApprovalProof,
    SessionBoundaryRelation,
)
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.temporal import UtcInstant

APPROVED_SESSION = MarketSession(date(2026, 8, 21))
ACTIVATION_SESSION = MarketSession(date(2026, 8, 24))
APPROVED_AT = UtcInstant.from_datetime(datetime(2026, 8, 21, 20, 0, tzinfo=UTC))
BASELINE_GOVERNANCE_STATUS = ConstitutionGovernanceStatus(
    ACTIVE_CONSTITUTION.reference,
    (),
    (),
    (),
    (),
)
_INVALID_BASELINE_PIN = "test lifecycle pin is not the explicit baseline"
_INVALID_BASELINE_HISTORY = "test lifecycle history is not the explicit baseline"


def amended_constitution(*, final_clause: str = "Never force activity.") -> ConstitutionArtifact:
    return ConstitutionArtifact.create(
        version=2,
        clauses=(*ACTIVE_CONSTITUTION.clauses[:-1], final_clause),
    )


def approval_for(
    artifact: ConstitutionArtifact,
    *,
    request_identity: str = "constitution-amendment-2",
    activation_session: MarketSession = ACTIVATION_SESSION,
    approved_session: MarketSession = APPROVED_SESSION,
) -> OperatorApprovalProof:
    unsigned = OperatorApprovalProof.parse(
        {
            "schema_version": 1,
            "authority_scope": "constitution_governance",
            "request_identity": request_identity,
            "constitution_hash": artifact.content_hash,
            "activation_session": activation_session.to_payload(),
            "approved_session": approved_session.to_payload(),
            "approved_at": APPROVED_AT.isoformat(),
            "key_id": "operator-key-1",
            "signature": "0" * 64,
        }
    )
    assert isinstance(unsigned, OperatorApprovalProof)
    return replace(unsigned, signature=hashlib.sha256(unsigned.signing_bytes()).hexdigest())


@dataclass(frozen=True, slots=True)
class HashApprovalVerifier:
    authorized_key_id: str = "operator-key-1"

    def verify(self, proof: OperatorApprovalProof) -> ApprovalVerification:
        if proof.key_id != self.authorized_key_id:
            return ApprovalVerification.UNAUTHORIZED
        unsigned = replace(proof, signature="0" * 64)
        expected = hashlib.sha256(unsigned.signing_bytes()).hexdigest()
        if proof.signature != expected:
            return ApprovalVerification.INVALID_SIGNATURE
        return ApprovalVerification.VERIFIED


@dataclass(frozen=True, slots=True)
class RecordedSessionEligibility:
    sessions: tuple[MarketSession, ...] = (APPROVED_SESSION, ACTIVATION_SESSION)

    def relation(self, session: MarketSession, recorded_at: UtcInstant) -> SessionBoundaryRelation:
        if session not in self.sessions:
            return SessionBoundaryRelation.INELIGIBLE
        current_date = recorded_at.value.date()
        if session.trading_date < current_date:
            return SessionBoundaryRelation.PAST
        if session.trading_date == current_date:
            return SessionBoundaryRelation.CURRENT
        return SessionBoundaryRelation.FUTURE


@dataclass(frozen=True, slots=True)
class BaselineConstitutionRegistry:
    """Provide an explicit baseline only to tests with no governance history."""

    def resolve(
        self,
        session: MarketSession,
        recorded_at: UtcInstant,
        pinned: ConstitutionReference | None,
    ) -> ConstitutionArtifact:
        del session, recorded_at
        if pinned is not None and pinned != ACTIVE_CONSTITUTION.reference:
            raise GovernanceStateError(_INVALID_BASELINE_PIN)
        return ACTIVE_CONSTITUTION

    def validate_references(self, uses: tuple[ConstitutionUse, ...]) -> None:
        if any(use.constitution != ACTIVE_CONSTITUTION.reference for use in uses):
            raise GovernanceStateError(_INVALID_BASELINE_HISTORY)
