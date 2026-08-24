from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from agentic_investment_os.domain.governance import (
    ACTIVE_CONSTITUTION,
    ApprovalVerification,
    ConstitutionArtifact,
    OperatorApprovalProof,
)
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.temporal import UtcInstant

APPROVED_SESSION = MarketSession(date(2026, 8, 21))
ACTIVATION_SESSION = MarketSession(date(2026, 8, 24))
APPROVED_AT = UtcInstant.from_datetime(datetime(2026, 8, 21, 20, 0, tzinfo=UTC))


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
    sessions: tuple[MarketSession, ...] = (ACTIVATION_SESSION,)

    def is_eligible(self, session: MarketSession) -> bool:
        return session in self.sessions
