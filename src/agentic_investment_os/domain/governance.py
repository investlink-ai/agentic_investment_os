"""Govern immutable operator-approved Constitution versions at session boundaries."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, assert_never

from agentic_investment_os.domain.identity import MarketSession, parse_decision_cycle_identity
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = (
    "ACTIVE_CONSTITUTION",
    "ApprovalVerification",
    "ConstitutionActivation",
    "ConstitutionArtifact",
    "ConstitutionGovernanceHistory",
    "ConstitutionGovernanceLedger",
    "ConstitutionGovernanceProjection",
    "ConstitutionGovernanceState",
    "ConstitutionGovernanceStatus",
    "ConstitutionReference",
    "ConstitutionUse",
    "GovernanceCommand",
    "GovernanceDecision",
    "GovernanceDisposition",
    "GovernanceEvent",
    "GovernanceEventKind",
    "GovernanceInputRefusal",
    "GovernanceReceipt",
    "GovernanceRefusalReason",
    "GovernanceRequest",
    "GovernanceRequestIdentity",
    "GovernanceStateError",
    "MarketSessionEligibility",
    "OperatorApprovalProof",
    "OperatorApprovalVerifier",
    "SessionBoundaryRelation",
    "activate_constitution",
    "decide_governance",
    "parse_governance_event",
    "parse_governance_request",
    "reconstruct_constitution_governance",
    "validate_constitution_uses",
)

_SCHEMA_VERSION = 1
_ARTIFACT_TYPE = "investment_constitution"
_AUTHORITY_SCOPE = "constitution_governance"
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SIGNATURE = re.compile(r"[A-Za-z0-9_-]{16,1024}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_CLAUSES = 32
_MAX_CLAUSE_LENGTH = 2_000
_MAX_TOTAL_LENGTH = 32_000
_MAX_HOSTILE_DIGEST_DEPTH = 32
_MAX_EXACT_INTEGER_BITS = 512
_HOSTILE_TEXT_CHUNK_SIZE = 4_096
_MISSED_ACTIVATION = "missed Constitution activation boundary"
_INVALID_HISTORY = "invalid Constitution governance history"
_INVALID_REFERENCE = "invalid Constitution reference"
_INVALID_ARTIFACT = "invalid Constitution artifact"
_INVALID_REQUEST_IDENTITY = "invalid governance request identity"
_INVALID_APPROVAL = "invalid operator approval proof"
_INVALID_REQUEST = "invalid governance request"
_INVALID_REFUSAL_FINGERPRINT = "invalid governance refusal fingerprint"
_INVALID_EVENT = "invalid governance event"
_EMPTY_DECISION = "cannot append an empty governance decision"


class GovernanceStateError(RuntimeError):
    """Report governance history that cannot authorize a Constitution selection."""


class ApprovalVerification(StrEnum):
    """Classify the result of checking an operator approval proof."""

    VERIFIED = "verified"
    INVALID_SIGNATURE = "invalid_signature"
    UNAUTHORIZED = "unauthorized"


class GovernanceDisposition(StrEnum):
    """Describe one bounded public governance outcome."""

    SCHEDULED = "scheduled"
    ACTIVATED = "activated"
    REPLAYED = "replayed"
    REFUSED = "refused"
    CONFLICTED = "conflicted"


class GovernanceRefusalReason(StrEnum):
    """Bound every governance refusal without disclosing approval material."""

    INVALID_REQUEST_IDENTITY = "invalid_request_identity"
    INVALID_ARTIFACT = "invalid_artifact"
    UNSUPPORTED_ARTIFACT_SCHEMA = "unsupported_artifact_schema"
    HASH_MISMATCH = "hash_mismatch"
    INVALID_ACTIVATION_SESSION = "invalid_activation_session"
    UNSIGNED = "unsigned"
    INVALID_APPROVAL = "invalid_approval"
    APPROVAL_MISMATCH = "approval_mismatch"
    NON_FUTURE_ACTIVATION = "non_future_activation"
    INELIGIBLE_SESSION = "ineligible_session"
    FUTURE_APPROVAL = "future_approval"
    INVALID_SIGNATURE = "invalid_signature"
    UNAUTHORIZED_OPERATOR = "unauthorized_operator"
    VERSION_CONFLICT = "version_conflict"
    PENDING_AMENDMENT = "pending_amendment"
    IDENTITY_CONFLICT = "identity_conflict"


class GovernanceEventKind(StrEnum):
    """Name authoritative append-only Constitution governance facts."""

    SCHEDULED = "constitution_scheduled"
    ACTIVATED = "constitution_activated"
    REFUSED = "constitution_refused"
    CONFLICTED = "constitution_conflicted"


class SessionBoundaryRelation(StrEnum):
    """Classify an eligible exchange session against one trusted UTC instant."""

    PAST = "past"
    CURRENT = "current"
    FUTURE = "future"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True, slots=True)
class ConstitutionReference:
    """Identify one immutable Constitution without carrying its model-visible content."""

    version: int
    content_hash: str

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version < 1 or not _is_sha256(self.content_hash):
            raise ValueError(_INVALID_REFERENCE)


@dataclass(frozen=True, slots=True)
class ConstitutionUse:
    """Bind one lifecycle session to the exact Constitution it durably used."""

    session: MarketSession
    constitution: ConstitutionReference

    def __post_init__(self) -> None:
        if (
            type(self.session) is not MarketSession
            or type(self.constitution) is not ConstitutionReference
        ):
            raise ValueError(_INVALID_REFERENCE)


@dataclass(frozen=True, slots=True)
class ConstitutionArtifact:
    """Carry one bounded immutable Constitution artifact and its canonical content hash."""

    version: int
    clauses: tuple[str, ...]
    content_hash: str

    @classmethod
    def create(cls, *, version: int, clauses: tuple[str, ...]) -> ConstitutionArtifact:
        """Construct an internal artifact from already approved typed content."""
        material = _artifact_material(version, clauses)
        if material is None:
            raise ValueError(_INVALID_ARTIFACT)
        return cls(version, clauses, _content_hash(material))

    @classmethod
    def parse(cls, value: object) -> ConstitutionArtifact | None:
        """Validate an untrusted artifact, including its declared content hash."""
        artifact, _ = _parse_artifact(value)
        return artifact

    @property
    def reference(self) -> ConstitutionReference:
        return ConstitutionReference(self.version, self.content_hash)

    def to_payload(self) -> dict[str, object]:
        return {
            **_artifact_material_required(self.version, self.clauses),
            "content_hash": self.content_hash,
        }

    def __post_init__(self) -> None:
        material = _artifact_material(self.version, self.clauses)
        if material is None or self.content_hash != _content_hash(material):
            raise ValueError(_INVALID_ARTIFACT)


@dataclass(frozen=True, slots=True)
class GovernanceRequestIdentity:
    """Carry a stable operator-selected identity for one governance request."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _REQUEST_ID.fullmatch(self.value) is None:
            raise ValueError(_INVALID_REQUEST_IDENTITY)

    @classmethod
    def parse(cls, value: object) -> GovernanceRequestIdentity | None:
        try:
            return cls(value) if type(value) is str else None
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True, slots=True)
class OperatorApprovalProof:
    """Carry public proof material for verification outside model authority."""

    request_identity: GovernanceRequestIdentity
    constitution_hash: str
    activation_session: MarketSession
    approved_session: MarketSession
    approved_at: UtcInstant
    key_id: str
    signature: str

    def __post_init__(self) -> None:
        if (
            type(self.request_identity) is not GovernanceRequestIdentity
            or not _is_sha256(self.constitution_hash)
            or type(self.activation_session) is not MarketSession
            or type(self.approved_session) is not MarketSession
            or type(self.approved_at) is not UtcInstant
            or type(self.key_id) is not str
            or _KEY_ID.fullmatch(self.key_id) is None
            or type(self.signature) is not str
            or _SIGNATURE.fullmatch(self.signature) is None
        ):
            raise ValueError(_INVALID_APPROVAL)

    def signing_bytes(self) -> bytes:
        """Return the canonical non-secret material covered by an operator signature."""
        return _canonical_bytes(
            {
                "schema_version": _SCHEMA_VERSION,
                "authority_scope": _AUTHORITY_SCOPE,
                "request_identity": self.request_identity.value,
                "constitution_hash": self.constitution_hash,
                "activation_session": self.activation_session.to_payload(),
                "approved_session": self.approved_session.to_payload(),
                "approved_at": self.approved_at.isoformat(),
                "key_id": self.key_id,
            }
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "authority_scope": _AUTHORITY_SCOPE,
            "request_identity": self.request_identity.value,
            "constitution_hash": self.constitution_hash,
            "activation_session": self.activation_session.to_payload(),
            "approved_session": self.approved_session.to_payload(),
            "approved_at": self.approved_at.isoformat(),
            "key_id": self.key_id,
            "signature": self.signature,
        }

    @classmethod
    def parse(cls, value: object) -> OperatorApprovalProof | None:
        fields = _exact_mapping(
            value,
            {
                "schema_version",
                "authority_scope",
                "request_identity",
                "constitution_hash",
                "activation_session",
                "approved_session",
                "approved_at",
                "key_id",
                "signature",
            },
        )
        if fields is None or fields["schema_version"] != _SCHEMA_VERSION:
            return None
        if fields["authority_scope"] != _AUTHORITY_SCOPE:
            return None
        identity = GovernanceRequestIdentity.parse(fields["request_identity"])
        activation = _market_session(fields["activation_session"])
        approved_session = _market_session(fields["approved_session"])
        approved_at = _utc_instant(fields["approved_at"])
        if (
            identity is None
            or activation is None
            or approved_session is None
            or approved_at is None
        ):
            return None
        try:
            return cls(
                identity,
                _text(fields["constitution_hash"]),
                activation,
                approved_session,
                approved_at,
                _text(fields["key_id"]),
                _text(fields["signature"]),
            )
        except (TypeError, ValueError):
            return None


class OperatorApprovalVerifier(Protocol):
    """Verify public approval proof without exposing signing keys to the capability."""

    def verify(self, proof: OperatorApprovalProof) -> ApprovalVerification: ...


class MarketSessionEligibility(Protocol):
    """Classify a session using an exchange calendar and a trusted UTC instant."""

    def relation(
        self, session: MarketSession, recorded_at: UtcInstant
    ) -> SessionBoundaryRelation: ...


class ConstitutionGovernanceLedger(Protocol):
    """Append domain-selected governance records and resolve session-bound versions."""

    def govern(
        self,
        command: GovernanceCommand,
        verifier: OperatorApprovalVerifier,
        verification: ApprovalVerification | None,
        recorded_at: UtcInstant,
    ) -> GovernanceReceipt: ...

    def resolve_constitution(
        self,
        session: MarketSession,
        verifier: OperatorApprovalVerifier | None,
        recorded_at: UtcInstant,
    ) -> ConstitutionActivation: ...

    def constitution_for(
        self,
        session: MarketSession,
        verifier: OperatorApprovalVerifier | None,
    ) -> ConstitutionArtifact: ...

    def next_activation_session(
        self,
        verifier: OperatorApprovalVerifier | None,
    ) -> MarketSession | None: ...

    def validate_constitution_uses(
        self,
        uses: tuple[ConstitutionUse, ...],
        verifier: OperatorApprovalVerifier | None,
    ) -> None: ...


class ConstitutionGovernanceProjection(Protocol):
    """Rebuild bounded governance status from authoritative append-only history."""

    def rebuild_constitution_status(
        self,
        verifier: OperatorApprovalVerifier | None,
        uses: tuple[ConstitutionUse, ...] = (),
    ) -> ConstitutionGovernanceStatus: ...


@dataclass(frozen=True, slots=True)
class GovernanceRequest:
    """Carry one completely validated amendment request into the governance kernel."""

    identity: GovernanceRequestIdentity
    artifact: ConstitutionArtifact
    activation_session: MarketSession
    approval: OperatorApprovalProof

    def __post_init__(self) -> None:
        if (
            type(self.identity) is not GovernanceRequestIdentity
            or type(self.artifact) is not ConstitutionArtifact
            or type(self.activation_session) is not MarketSession
            or type(self.approval) is not OperatorApprovalProof
            or self.approval.request_identity != self.identity
            or self.approval.constitution_hash != self.artifact.content_hash
            or self.approval.activation_session != self.activation_session
            or self.activation_session.trading_date <= self.approval.approved_session.trading_date
        ):
            raise ValueError(_INVALID_REQUEST)

    @property
    def fingerprint(self) -> str:
        return _content_hash(
            {
                "request_identity": self.identity.value,
                "artifact": self.artifact.to_payload(),
                "activation_session": self.activation_session.to_payload(),
                "approval": self.approval.to_payload(),
            }
        )


@dataclass(frozen=True, slots=True)
class GovernanceInputRefusal:
    """Carry a bounded hostile-input refusal into append-only governance history."""

    reason: GovernanceRefusalReason
    request_identity: GovernanceRequestIdentity | None
    request_fingerprint: str
    artifact: ConstitutionArtifact | None = None
    activation_session: MarketSession | None = None

    def __post_init__(self) -> None:
        if (
            not _is_sha256(self.request_fingerprint)
            or (
                self.request_identity is not None
                and type(self.request_identity) is not GovernanceRequestIdentity
            )
            or (self.artifact is not None and type(self.artifact) is not ConstitutionArtifact)
            or (
                self.activation_session is not None
                and type(self.activation_session) is not MarketSession
            )
        ):
            raise ValueError(_INVALID_REFUSAL_FINGERPRINT)


GovernanceCommand = GovernanceRequest | GovernanceInputRefusal


@dataclass(frozen=True, slots=True)
class GovernanceEvent:
    """Preserve one validated append-only Constitution governance fact."""

    sequence: int
    kind: GovernanceEventKind
    request_identity: GovernanceRequestIdentity | None
    request_fingerprint: str
    artifact: ConstitutionArtifact | None
    activation_session: MarketSession | None
    approval: OperatorApprovalProof | None
    reason: GovernanceRefusalReason | None
    recorded_at: UtcInstant

    def __post_init__(self) -> None:
        common_valid = (
            type(self.sequence) is int
            and self.sequence >= 0
            and type(self.kind) is GovernanceEventKind
            and _is_sha256(self.request_fingerprint)
            and type(self.recorded_at) is UtcInstant
        )
        scheduled = (
            self.kind is GovernanceEventKind.SCHEDULED
            and self.request_identity is not None
            and self.artifact is not None
            and self.activation_session is not None
            and self.approval is not None
            and self.reason is None
        )
        activated = (
            self.kind is GovernanceEventKind.ACTIVATED
            and self.request_identity is not None
            and self.artifact is not None
            and self.activation_session is not None
            and self.approval is None
            and self.reason is None
        )
        refused = (
            self.kind is GovernanceEventKind.REFUSED
            and self.approval is None
            and self.reason is not None
        )
        conflicted = (
            self.kind is GovernanceEventKind.CONFLICTED
            and self.request_identity is not None
            and self.approval is None
            and self.reason is GovernanceRefusalReason.IDENTITY_CONFLICT
        )
        if not common_valid or not (scheduled or activated or refused or conflicted):
            raise ValueError(_INVALID_EVENT)

    def to_payload(self) -> dict[str, object]:
        material = {
            "envelope_schema_version": _SCHEMA_VERSION,
            "record_kind": "constitution_governance_event",
            "payload_discriminator": self.kind.value,
            "payload_schema_version": _SCHEMA_VERSION,
            "authority_scope": _AUTHORITY_SCOPE,
            "recorded_at": self.recorded_at.isoformat(),
            "payload": {
                "sequence": self.sequence,
                "request_identity": (
                    None if self.request_identity is None else self.request_identity.value
                ),
                "request_fingerprint": self.request_fingerprint,
                "artifact": None if self.artifact is None else self.artifact.to_payload(),
                "activation_session": (
                    None
                    if self.activation_session is None
                    else self.activation_session.to_payload()
                ),
                "approval": None if self.approval is None else self.approval.to_payload(),
                "reason": None if self.reason is None else self.reason.value,
            },
        }
        return {**material, "content_hash": _content_hash(material)}


def parse_governance_event(  # noqa: PLR0911 - reject each hostile envelope layer directly.
    value: object,
) -> GovernanceEvent | None:
    """Validate one hostile durable governance envelope before reconstruction."""
    root = _exact_mapping(
        value,
        {
            "envelope_schema_version",
            "record_kind",
            "payload_discriminator",
            "payload_schema_version",
            "authority_scope",
            "recorded_at",
            "payload",
            "content_hash",
        },
    )
    if root is None:
        return None
    payload = _exact_mapping(
        root["payload"],
        {
            "sequence",
            "request_identity",
            "request_fingerprint",
            "artifact",
            "activation_session",
            "approval",
            "reason",
        },
    )
    if payload is None:
        return None
    try:
        kind = GovernanceEventKind(_text(root["payload_discriminator"]))
    except (ValueError, TypeError):
        return None
    if not (
        root["envelope_schema_version"] == _SCHEMA_VERSION
        and root["payload_schema_version"] == _SCHEMA_VERSION
        and root["record_kind"] == "constitution_governance_event"
        and root["authority_scope"] == _AUTHORITY_SCOPE
    ):
        return None
    identity_value = payload["request_identity"]
    identity = None if identity_value is None else GovernanceRequestIdentity.parse(identity_value)
    artifact_value = payload["artifact"]
    artifact = None if artifact_value is None else ConstitutionArtifact.parse(artifact_value)
    session_value = payload["activation_session"]
    session = None if session_value is None else _market_session(session_value)
    approval_value = payload["approval"]
    approval = None if approval_value is None else OperatorApprovalProof.parse(approval_value)
    reason_value = payload["reason"]
    try:
        reason = None if reason_value is None else GovernanceRefusalReason(_text(reason_value))
    except (ValueError, TypeError):
        return None
    recorded_at = _utc_instant(root["recorded_at"])
    if (
        (identity_value is not None and identity is None)
        or (artifact_value is not None and artifact is None)
        or (session_value is not None and session is None)
        or (approval_value is not None and approval is None)
        or recorded_at is None
        or type(payload["sequence"]) is not int
    ):
        return None
    try:
        event = GovernanceEvent(
            payload["sequence"],
            kind,
            identity,
            _text(payload["request_fingerprint"]),
            artifact,
            session,
            approval,
            reason,
            recorded_at,
        )
    except (TypeError, ValueError):
        return None
    return event if event.to_payload() == root else None


@dataclass(frozen=True, slots=True)
class ConstitutionGovernanceHistory:
    """Carry authoritative governance events in append order."""

    events: tuple[GovernanceEvent, ...] = ()

    def append(self, event: GovernanceEvent | None) -> ConstitutionGovernanceHistory:
        if event is None:
            raise ValueError(_EMPTY_DECISION)
        if event.sequence != len(self.events):
            raise GovernanceStateError(_INVALID_HISTORY)
        return ConstitutionGovernanceHistory((*self.events, event))


@dataclass(frozen=True, slots=True)
class ScheduledConstitution:
    """Expose bounded pending amendment facts without approval material."""

    request_identity: GovernanceRequestIdentity
    constitution: ConstitutionReference
    activation_session: MarketSession


@dataclass(frozen=True, slots=True)
class GovernanceRefusal:
    """Expose one bounded refusal reconstructed from authoritative history."""

    request_identity: GovernanceRequestIdentity | None
    reason: GovernanceRefusalReason
    constitution: ConstitutionReference | None
    activation_session: MarketSession | None


@dataclass(frozen=True, slots=True)
class ConstitutionGovernanceState:
    """Reconstruct active, pending, superseded, and refused governance state."""

    active_artifact: ConstitutionArtifact
    pending: tuple[ScheduledConstitution, ...]
    superseded: tuple[ConstitutionReference, ...]
    refusals: tuple[GovernanceRefusal, ...]
    conflicts: tuple[GovernanceRequestIdentity, ...]
    regimes: tuple[tuple[MarketSession | None, ConstitutionArtifact], ...]
    latest_activation_request: GovernanceRequestIdentity | None = None

    @property
    def active(self) -> ConstitutionReference:
        return self.active_artifact.reference

    def constitution_for(self, session: MarketSession) -> ConstitutionArtifact:
        selected = self.regimes[0][1]
        for effective_session, artifact in self.regimes[1:]:
            if (
                effective_session is not None
                and effective_session.trading_date <= session.trading_date
            ):
                selected = artifact
        return selected


@dataclass(frozen=True, slots=True)
class ConstitutionGovernanceStatus:
    """Expose bounded governance state through public Status."""

    active: ConstitutionReference
    pending: tuple[ScheduledConstitution, ...]
    superseded: tuple[ConstitutionReference, ...]
    refusals: tuple[GovernanceRefusal, ...]
    conflicts: tuple[GovernanceRequestIdentity, ...]

    @classmethod
    def from_state(cls, state: ConstitutionGovernanceState) -> ConstitutionGovernanceStatus:
        return cls(state.active, state.pending, state.superseded, state.refusals, state.conflicts)


@dataclass(frozen=True, slots=True)
class GovernanceReceipt:
    """Report bounded governance facts without disclosing signature material."""

    disposition: GovernanceDisposition
    request_identity: GovernanceRequestIdentity | None
    constitution: ConstitutionReference | None
    effective_session: MarketSession | None
    reason: GovernanceRefusalReason | None
    recorded_at: UtcInstant
    replayed_disposition: GovernanceDisposition | None = None

    def to_payload(self) -> dict[str, object]:
        material = {
            "envelope_schema_version": _SCHEMA_VERSION,
            "record_kind": "constitution_governance_receipt",
            "payload_discriminator": "bounded_constitution_governance_receipt",
            "payload_schema_version": _SCHEMA_VERSION,
            "authority_scope": _AUTHORITY_SCOPE,
            "relevant_at": self.recorded_at.isoformat(),
            "available_at": self.recorded_at.isoformat(),
            "payload": {
                "disposition": self.disposition.value,
                "request_identity": (
                    None if self.request_identity is None else self.request_identity.value
                ),
                "constitution_version": (
                    None if self.constitution is None else self.constitution.version
                ),
                "constitution_hash": (
                    None if self.constitution is None else self.constitution.content_hash
                ),
                "effective_session": (
                    None if self.effective_session is None else self.effective_session.to_payload()
                ),
                "reason": None if self.reason is None else self.reason.value,
                "replayed_disposition": (
                    None if self.replayed_disposition is None else self.replayed_disposition.value
                ),
            },
        }
        return {**material, "content_hash": _content_hash(material)}


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    """Return a pure scheduling decision and any event the adapter must append."""

    record: GovernanceEvent | None
    receipt: GovernanceReceipt


@dataclass(frozen=True, slots=True)
class ConstitutionActivation:
    """Return the Constitution selected for a session and any activation event."""

    record: GovernanceEvent | None
    receipt: GovernanceReceipt | None
    artifact: ConstitutionArtifact

    @property
    def constitution(self) -> ConstitutionReference:
        return self.artifact.reference


def parse_governance_request(  # noqa: PLR0911 - retain precise bounded refusal reasons.
    *,
    request_identity: object,
    artifact: object,
    activation_session: object,
    approval_proof: object,
) -> GovernanceCommand:
    """Validate hostile Govern arguments once and return typed material or a bounded refusal."""
    identity = GovernanceRequestIdentity.parse(request_identity)
    parsed_artifact, artifact_reason = _parse_artifact(artifact)
    session = _market_session(activation_session)
    if identity is None:
        return GovernanceInputRefusal(
            GovernanceRefusalReason.INVALID_REQUEST_IDENTITY,
            None,
            _input_fingerprint(
                GovernanceRefusalReason.INVALID_REQUEST_IDENTITY,
                request_identity,
                artifact,
                activation_session,
                approval_proof,
            ),
            parsed_artifact,
            session,
        )
    if parsed_artifact is None:
        return GovernanceInputRefusal(
            artifact_reason,
            identity,
            _input_fingerprint(
                artifact_reason,
                request_identity,
                artifact,
                activation_session,
                approval_proof,
            ),
            activation_session=session,
        )
    if session is None:
        return GovernanceInputRefusal(
            GovernanceRefusalReason.INVALID_ACTIVATION_SESSION,
            identity,
            _input_fingerprint(
                GovernanceRefusalReason.INVALID_ACTIVATION_SESSION,
                request_identity,
                artifact,
                activation_session,
                approval_proof,
            ),
            parsed_artifact,
        )
    if approval_proof is None:
        return GovernanceInputRefusal(
            GovernanceRefusalReason.UNSIGNED,
            identity,
            _input_fingerprint(
                GovernanceRefusalReason.UNSIGNED,
                request_identity,
                artifact,
                activation_session,
                approval_proof,
            ),
            parsed_artifact,
            session,
        )
    proof = OperatorApprovalProof.parse(approval_proof)
    if proof is None:
        return GovernanceInputRefusal(
            GovernanceRefusalReason.INVALID_APPROVAL,
            identity,
            _input_fingerprint(
                GovernanceRefusalReason.INVALID_APPROVAL,
                request_identity,
                artifact,
                activation_session,
                approval_proof,
            ),
            parsed_artifact,
            session,
        )
    if (
        proof.request_identity != identity
        or proof.constitution_hash != parsed_artifact.content_hash
        or proof.activation_session != session
    ):
        return GovernanceInputRefusal(
            GovernanceRefusalReason.APPROVAL_MISMATCH,
            identity,
            _input_fingerprint(
                GovernanceRefusalReason.APPROVAL_MISMATCH,
                request_identity,
                artifact,
                activation_session,
                approval_proof,
            ),
            parsed_artifact,
            session,
        )
    if session.trading_date <= proof.approved_session.trading_date:
        return GovernanceInputRefusal(
            GovernanceRefusalReason.NON_FUTURE_ACTIVATION,
            identity,
            _input_fingerprint(
                GovernanceRefusalReason.NON_FUTURE_ACTIVATION,
                request_identity,
                artifact,
                activation_session,
                approval_proof,
            ),
            parsed_artifact,
            session,
        )
    return GovernanceRequest(identity, parsed_artifact, session, proof)


def decide_governance(
    history: ConstitutionGovernanceHistory,
    command: GovernanceCommand,
    verification: ApprovalVerification | None,
    recorded_at: UtcInstant,
) -> GovernanceDecision:
    """Select one scheduling event after the caller has verified existing history."""
    state = _reconstruct_constitution_governance(history, None)
    identity = _command_identity(command)
    fingerprint = _command_fingerprint(command)
    prior = tuple(event for event in history.events if event.request_identity == identity)
    if prior:
        matching = tuple(event for event in prior if event.request_fingerprint == fingerprint)
        if matching:
            return GovernanceDecision(None, _replay_receipt(matching))
        if identity is not None:
            event = _conflict_event(history, command, recorded_at)
            return GovernanceDecision(event, _receipt_for_event(event))
    if isinstance(command, GovernanceInputRefusal):
        event = _refusal_event(history, command, command.reason, recorded_at)
        return GovernanceDecision(event, _receipt_for_event(event))
    if not isinstance(command, GovernanceRequest):
        assert_never(command)  # pragma: no cover - closed command union is exhausted above.
    refusal_reason = _verification_refusal(verification)
    if refusal_reason is None and command.approval.approved_at.value > recorded_at.value:
        refusal_reason = GovernanceRefusalReason.FUTURE_APPROVAL
    if refusal_reason is None and state.pending:
        refusal_reason = GovernanceRefusalReason.PENDING_AMENDMENT
    if refusal_reason is None and command.artifact.version != state.active.version + 1:
        refusal_reason = GovernanceRefusalReason.VERSION_CONFLICT
    if refusal_reason is not None:
        event = _refusal_event(history, command, refusal_reason, recorded_at)
        return GovernanceDecision(event, _receipt_for_event(event))
    event = GovernanceEvent(
        len(history.events),
        GovernanceEventKind.SCHEDULED,
        command.identity,
        command.fingerprint,
        command.artifact,
        command.activation_session,
        command.approval,
        None,
        recorded_at,
    )
    return GovernanceDecision(event, _receipt_for_event(event))


def activate_constitution(
    history: ConstitutionGovernanceHistory,
    session: MarketSession,
    recorded_at: UtcInstant,
) -> ConstitutionActivation:
    """Select the exact as-of Constitution and activate only at its approved boundary."""
    state = _reconstruct_constitution_governance(history, None)
    pending = state.pending
    if pending:
        scheduled = pending[0]
        if session.trading_date > scheduled.activation_session.trading_date:
            raise GovernanceStateError(_MISSED_ACTIVATION)
        if session == scheduled.activation_session:
            source = next(
                event
                for event in reversed(history.events)
                if event.kind is GovernanceEventKind.SCHEDULED
                and event.request_identity == scheduled.request_identity
            )
            event = GovernanceEvent(
                len(history.events),
                GovernanceEventKind.ACTIVATED,
                source.request_identity,
                source.request_fingerprint,
                source.artifact,
                source.activation_session,
                None,
                None,
                recorded_at,
            )
            return ConstitutionActivation(
                event, _receipt_for_event(event), _required_artifact(event)
            )
    artifact = state.constitution_for(session)
    activation = _activation_for_session(history, session)
    receipt = None if activation is None else _replayed_activation_receipt(activation, recorded_at)
    return ConstitutionActivation(None, receipt, artifact)


def reconstruct_constitution_governance(
    history: ConstitutionGovernanceHistory,
    verifier: Callable[[OperatorApprovalProof], ApprovalVerification],
) -> ConstitutionGovernanceState:
    """Rebuild governance state and reverify every durable operator approval proof."""
    return _reconstruct_constitution_governance(history, verifier)


def validate_constitution_uses(
    history: ConstitutionGovernanceHistory,
    verifier: Callable[[OperatorApprovalProof], ApprovalVerification],
    uses: tuple[ConstitutionUse, ...],
) -> ConstitutionGovernanceState:
    """Rebuild governance and require every lifecycle pin to resolve exactly."""
    state = _reconstruct_constitution_governance(history, verifier)
    if any(state.constitution_for(use.session).reference != use.constitution for use in uses):
        raise GovernanceStateError(_INVALID_HISTORY)
    return state


def _reconstruct_constitution_governance(
    history: ConstitutionGovernanceHistory,
    verifier: Callable[[OperatorApprovalProof], ApprovalVerification] | None,
) -> ConstitutionGovernanceState:
    active = ACTIVE_CONSTITUTION
    pending_event: GovernanceEvent | None = None
    superseded: list[ConstitutionReference] = []
    refusals: list[GovernanceRefusal] = []
    conflicts: list[GovernanceRequestIdentity] = []
    regimes: list[tuple[MarketSession | None, ConstitutionArtifact]] = [(None, active)]
    latest_activation_request: GovernanceRequestIdentity | None = None
    for sequence, event in enumerate(history.events):
        if event.sequence != sequence:
            raise GovernanceStateError(_INVALID_HISTORY)
        if event.kind is GovernanceEventKind.SCHEDULED:
            artifact = _required_artifact(event)
            approval = event.approval
            if (
                pending_event is not None
                or approval is None
                or artifact.version != active.version + 1
                or approval.request_identity != event.request_identity
                or approval.constitution_hash != artifact.content_hash
                or approval.activation_session != event.activation_session
                or event.activation_session is None
                or event.activation_session.trading_date <= approval.approved_session.trading_date
                or approval.approved_at.value > event.recorded_at.value
                or event.request_fingerprint
                != GovernanceRequest(
                    _required_identity(event),
                    artifact,
                    event.activation_session,
                    approval,
                ).fingerprint
                or (
                    verifier is not None
                    and _verify(verifier, approval) is not ApprovalVerification.VERIFIED
                )
            ):
                raise GovernanceStateError(_INVALID_HISTORY)
            pending_event = event
            continue
        if event.kind is GovernanceEventKind.ACTIVATED:
            if (
                pending_event is None
                or event.request_identity != pending_event.request_identity
                or event.request_fingerprint != pending_event.request_fingerprint
                or event.artifact != pending_event.artifact
                or event.activation_session != pending_event.activation_session
                or event.recorded_at.value < pending_event.recorded_at.value
            ):
                raise GovernanceStateError(_INVALID_HISTORY)
            artifact = _required_artifact(event)
            superseded.append(active.reference)
            active = artifact
            regimes.append((_required_session(event), artifact))
            latest_activation_request = event.request_identity
            pending_event = None
            continue
        if event.kind is GovernanceEventKind.REFUSED:
            if event.reason is None:  # pragma: no cover - refused event construction requires it.
                raise GovernanceStateError(_INVALID_HISTORY)
            refusals.append(
                GovernanceRefusal(
                    event.request_identity,
                    event.reason,
                    None if event.artifact is None else event.artifact.reference,
                    event.activation_session,
                )
            )
            continue
        if event.kind is GovernanceEventKind.CONFLICTED:
            if (
                event.request_identity is None
            ):  # pragma: no cover - conflict construction requires it.
                raise GovernanceStateError(_INVALID_HISTORY)
            conflicts.append(event.request_identity)
            continue
        assert_never(event.kind)  # pragma: no cover - closed event union is exhausted above.
    pending = (
        ()
        if pending_event is None
        else (
            ScheduledConstitution(
                _required_identity(pending_event),
                _required_artifact(pending_event).reference,
                _required_session(pending_event),
            ),
        )
    )
    return ConstitutionGovernanceState(
        active,
        pending,
        tuple(superseded),
        tuple(refusals),
        tuple(conflicts),
        tuple(regimes),
        latest_activation_request,
    )


def _verify(
    verifier: Callable[[OperatorApprovalProof], ApprovalVerification],
    proof: OperatorApprovalProof,
) -> ApprovalVerification:
    result = verifier(proof)
    return (
        result if type(result) is ApprovalVerification else ApprovalVerification.INVALID_SIGNATURE
    )


def _receipt_for_event(event: GovernanceEvent) -> GovernanceReceipt:
    if event.kind is GovernanceEventKind.SCHEDULED:
        disposition = GovernanceDisposition.SCHEDULED
    elif event.kind is GovernanceEventKind.ACTIVATED:
        disposition = GovernanceDisposition.ACTIVATED
    elif event.kind is GovernanceEventKind.REFUSED:
        disposition = GovernanceDisposition.REFUSED
    elif event.kind is GovernanceEventKind.CONFLICTED:
        disposition = GovernanceDisposition.CONFLICTED
    else:
        assert_never(event.kind)  # pragma: no cover - closed event union is exhausted above.
    return GovernanceReceipt(
        disposition,
        event.request_identity,
        None if event.artifact is None else event.artifact.reference,
        event.activation_session,
        event.reason,
        event.recorded_at,
    )


def _replay_receipt(prior: tuple[GovernanceEvent, ...]) -> GovernanceReceipt:
    activation = next(
        (event for event in reversed(prior) if event.kind is GovernanceEventKind.ACTIVATED),
        None,
    )
    source = prior[0] if activation is None else activation
    if source.kind is GovernanceEventKind.SCHEDULED:
        replayed = GovernanceDisposition.SCHEDULED
    elif source.kind is GovernanceEventKind.ACTIVATED:
        replayed = GovernanceDisposition.ACTIVATED
    elif source.kind is GovernanceEventKind.REFUSED:
        replayed = GovernanceDisposition.REFUSED
    elif source.kind is GovernanceEventKind.CONFLICTED:
        replayed = GovernanceDisposition.CONFLICTED
    else:
        assert_never(source.kind)  # pragma: no cover - closed event union is exhausted above.
    return GovernanceReceipt(
        GovernanceDisposition.REPLAYED,
        source.request_identity,
        None if source.artifact is None else source.artifact.reference,
        source.activation_session,
        source.reason,
        source.recorded_at,
        replayed,
    )


def _replayed_activation_receipt(
    activation: GovernanceEvent, recorded_at: UtcInstant
) -> GovernanceReceipt:
    del recorded_at
    return GovernanceReceipt(
        GovernanceDisposition.REPLAYED,
        activation.request_identity,
        _required_artifact(activation).reference,
        activation.activation_session,
        None,
        activation.recorded_at,
        GovernanceDisposition.ACTIVATED,
    )


def _activation_for_session(
    history: ConstitutionGovernanceHistory, session: MarketSession
) -> GovernanceEvent | None:
    return next(
        (
            event
            for event in reversed(history.events)
            if event.kind is GovernanceEventKind.ACTIVATED and event.activation_session == session
        ),
        None,
    )


def _conflict_event(
    history: ConstitutionGovernanceHistory,
    command: GovernanceCommand,
    recorded_at: UtcInstant,
) -> GovernanceEvent:
    return GovernanceEvent(
        len(history.events),
        GovernanceEventKind.CONFLICTED,
        _command_identity(command),
        _command_fingerprint(command),
        _command_artifact(command),
        _command_session(command),
        None,
        GovernanceRefusalReason.IDENTITY_CONFLICT,
        recorded_at,
    )


def _refusal_event(
    history: ConstitutionGovernanceHistory,
    command: GovernanceCommand,
    reason: GovernanceRefusalReason,
    recorded_at: UtcInstant,
) -> GovernanceEvent:
    return GovernanceEvent(
        len(history.events),
        GovernanceEventKind.REFUSED,
        _command_identity(command),
        _command_fingerprint(command),
        _command_artifact(command),
        _command_session(command),
        None,
        reason,
        recorded_at,
    )


def _verification_refusal(
    verification: ApprovalVerification | None,
) -> GovernanceRefusalReason | None:
    if verification is ApprovalVerification.VERIFIED:
        return None
    if verification is ApprovalVerification.INVALID_SIGNATURE:
        return GovernanceRefusalReason.INVALID_SIGNATURE
    if verification is ApprovalVerification.UNAUTHORIZED:
        return GovernanceRefusalReason.UNAUTHORIZED_OPERATOR
    if verification is None:
        return GovernanceRefusalReason.UNSIGNED
    assert_never(verification)  # pragma: no cover - closed verification union is exhausted above.


def _command_identity(command: GovernanceCommand) -> GovernanceRequestIdentity | None:
    if isinstance(command, GovernanceRequest):
        return command.identity
    if isinstance(command, GovernanceInputRefusal):
        return command.request_identity
    assert_never(command)  # pragma: no cover - closed command union is exhausted above.


def _command_fingerprint(command: GovernanceCommand) -> str:
    if isinstance(command, GovernanceRequest):
        return command.fingerprint
    if isinstance(command, GovernanceInputRefusal):
        return command.request_fingerprint
    assert_never(command)  # pragma: no cover - closed command union is exhausted above.


def _command_artifact(command: GovernanceCommand) -> ConstitutionArtifact | None:
    if isinstance(command, GovernanceRequest):
        return command.artifact
    return command.artifact


def _command_session(command: GovernanceCommand) -> MarketSession | None:
    if isinstance(command, GovernanceRequest):
        return command.activation_session
    return command.activation_session


def _parse_artifact(  # noqa: PLR0911 - preserve precise artifact refusal classification.
    value: object,
) -> tuple[ConstitutionArtifact | None, GovernanceRefusalReason]:
    fields = _exact_mapping(
        value,
        {"schema_version", "artifact_type", "version", "clauses", "content_hash"},
    )
    if fields is None:
        return None, GovernanceRefusalReason.INVALID_ARTIFACT
    if fields["schema_version"] != _SCHEMA_VERSION:
        return None, GovernanceRefusalReason.UNSUPPORTED_ARTIFACT_SCHEMA
    clauses_value = fields["clauses"]
    if type(clauses_value) is not list or any(type(clause) is not str for clause in clauses_value):
        return None, GovernanceRefusalReason.INVALID_ARTIFACT
    clauses = tuple(clauses_value)
    version = fields["version"]
    material = _artifact_material(version, clauses)
    if fields["artifact_type"] != _ARTIFACT_TYPE or material is None:
        return None, GovernanceRefusalReason.INVALID_ARTIFACT
    declared_hash = fields["content_hash"]
    if type(declared_hash) is not str or declared_hash != _content_hash(material):
        return None, GovernanceRefusalReason.HASH_MISMATCH
    if type(version) is not int:  # pragma: no cover - non-integers make material invalid above.
        return None, GovernanceRefusalReason.INVALID_ARTIFACT
    return ConstitutionArtifact(version, clauses, declared_hash), (
        GovernanceRefusalReason.INVALID_ARTIFACT
    )


def _artifact_material(version: object, clauses: object) -> dict[str, object] | None:
    if (
        type(version) is not int
        or version < 1
        or type(clauses) is not tuple
        or not clauses
        or len(clauses) > _MAX_CLAUSES
        or any(
            type(clause) is not str
            or not clause.strip()
            or clause != clause.strip()
            or len(clause) > _MAX_CLAUSE_LENGTH
            for clause in clauses
        )
        or len("".join(clauses)) > _MAX_TOTAL_LENGTH
    ):
        return None
    return _artifact_material_required(version, clauses)


def _artifact_material_required(version: int, clauses: tuple[str, ...]) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "artifact_type": _ARTIFACT_TYPE,
        "version": version,
        "clauses": list(clauses),
    }


def _input_fingerprint(
    reason: GovernanceRefusalReason,
    request_identity: object,
    artifact: object,
    activation_session: object,
    approval: object,
) -> str:
    return _content_hash(
        {
            "reason": reason.value,
            "request_identity_digest": _hostile_value_digest(request_identity),
            "artifact_digest": _hostile_value_digest(artifact),
            "activation_session_digest": _hostile_value_digest(activation_session),
            "approval_digest": _hostile_value_digest(approval),
        }
    )


def _hostile_value_digest(value: object) -> str:
    return _digest_hostile_node(value, frozenset(), 0).hex()


def _digest_hostile_node(value: object, ancestors: frozenset[int], depth: int) -> bytes:
    digest = hashlib.sha256()
    value_type = type(value)
    digest.update(value_type.__module__.encode())
    digest.update(b":")
    digest.update(value_type.__qualname__.encode())
    digest.update(b":")
    if depth >= _MAX_HOSTILE_DIGEST_DEPTH:
        digest.update(b"depth-limit")
        return digest.digest()
    primitive = _primitive_hostile_digest(value)
    if primitive is not None:
        digest.update(primitive)
        return digest.digest()
    identity = id(value)
    if identity in ancestors:
        digest.update(b"cycle")
        return digest.digest()
    _update_container_digest(digest, value, ancestors | {identity}, depth)
    return digest.digest()


def _primitive_hostile_digest(value: object) -> bytes | None:
    digest = hashlib.sha256()
    value_type = type(value)
    handled = True
    if value is None:
        digest.update(b"none")
    elif isinstance(value, bool) and value_type is bool:
        digest.update(b"true" if value else b"false")
    elif isinstance(value, int) and value_type is int:
        _update_integer_digest(digest, value)
    elif isinstance(value, float) and value_type is float:
        digest.update(value.hex().encode())
    elif isinstance(value, str) and value_type is str:
        digest.update(str(len(value)).encode())
        for offset in range(0, len(value), _HOSTILE_TEXT_CHUNK_SIZE):
            digest.update(value[offset : offset + _HOSTILE_TEXT_CHUNK_SIZE].encode())
    elif isinstance(value, bytes) and value_type is bytes:
        digest.update(str(len(value)).encode())
        digest.update(value)
    else:
        handled = False
    return digest.digest() if handled else None


def _update_integer_digest(digest: hashlib._Hash, value: int) -> None:
    magnitude = abs(value)
    bit_length = magnitude.bit_length()
    digest.update(b"-" if value < 0 else b"+")
    digest.update(str(bit_length).encode())
    if bit_length <= _MAX_EXACT_INTEGER_BITS:
        digest.update(magnitude.to_bytes(max(1, (bit_length + 7) // 8), "big"))
        return
    digest.update((magnitude >> (bit_length - 256)).to_bytes(32, "big"))
    digest.update((magnitude & ((1 << 256) - 1)).to_bytes(32, "big"))


def _update_container_digest(
    digest: hashlib._Hash,
    value: object,
    ancestors: frozenset[int],
    depth: int,
) -> None:
    value_type = type(value)
    if isinstance(value, dict) and value_type is dict:
        entries = sorted(
            (
                _digest_hostile_node(key, ancestors, depth + 1),
                _digest_hostile_node(item, ancestors, depth + 1),
            )
            for key, item in value.items()
        )
        digest.update(str(len(entries)).encode())
        for key_digest, item_digest in entries:
            digest.update(key_digest)
            digest.update(item_digest)
    elif isinstance(value, (list, tuple)) and value_type in {list, tuple}:
        digest.update(str(len(value)).encode())
        for item in value:
            digest.update(_digest_hostile_node(item, ancestors, depth + 1))
    elif isinstance(value, (set, frozenset)) and value_type in {set, frozenset}:
        items = sorted(_digest_hostile_node(item, ancestors, depth + 1) for item in value)
        digest.update(str(len(items)).encode())
        for item_digest in items:
            digest.update(item_digest)


def _required_artifact(event: GovernanceEvent) -> ConstitutionArtifact:
    if event.artifact is None:  # pragma: no cover - owning event variants require an artifact.
        raise GovernanceStateError(_INVALID_HISTORY)
    return event.artifact


def _required_session(event: GovernanceEvent) -> MarketSession:
    if event.activation_session is None:  # pragma: no cover - owning event variants require it.
        raise GovernanceStateError(_INVALID_HISTORY)
    return event.activation_session


def _required_identity(event: GovernanceEvent) -> GovernanceRequestIdentity:
    if event.request_identity is None:  # pragma: no cover - scheduled events require an identity.
        raise GovernanceStateError(_INVALID_HISTORY)
    return event.request_identity


def _market_session(value: object) -> MarketSession | None:
    if type(value) is MarketSession:
        return value
    parsed = parse_decision_cycle_identity(value)
    return parsed if type(parsed) is MarketSession else None


def _utc_instant(value: object) -> UtcInstant | None:
    try:
        return UtcInstant.parse(value)
    except InvalidUtcInstantError:
        return None


def _text(value: object) -> str:
    if type(value) is not str:
        raise TypeError
    return value


def _exact_mapping(value: object, fields: set[str]) -> dict[str, object] | None:
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != fields
    ):
        return None
    return value


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _content_hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


ACTIVE_CONSTITUTION = ConstitutionArtifact.create(
    version=1,
    clauses=(
        "Invest against expectations, not merely good or bad company news.",
        "State the market's apparent expectation and the variant view.",
        "Use scenarios rather than one precise forecast.",
        "Explain the causal chain from evidence to business or market impact.",
        "Name the catalyst, expected horizon, and observable resolution.",
        "State invalidators before taking risk.",
        "Invert the thesis and examine permanent-loss paths.",
        "Stay within the evidence-backed circle of competence; otherwise abstain.",
        "Prefer asymmetric payoff with survivable downside.",
        "Respect liquidity, concentration, common-cause exposure, and correlation.",
        "Update beliefs when evidence changes while preserving the prior record.",
        "Separate process quality from outcome luck and never force activity.",
    ),
)
