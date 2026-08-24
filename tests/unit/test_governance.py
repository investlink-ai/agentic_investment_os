from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from agentic_investment_os.domain.governance import (
    ACTIVE_CONSTITUTION,
    ApprovalVerification,
    ConstitutionArtifact,
    ConstitutionGovernanceHistory,
    ConstitutionReference,
    ConstitutionUse,
    GovernanceDisposition,
    GovernanceEvent,
    GovernanceEventKind,
    GovernanceInputRefusal,
    GovernanceRefusalReason,
    GovernanceRequest,
    GovernanceRequestIdentity,
    GovernanceStateError,
    OperatorApprovalProof,
    activate_constitution,
    decide_governance,
    parse_governance_event,
    parse_governance_request,
    reconstruct_constitution_governance,
    validate_constitution_uses,
)
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.temporal import UtcInstant

APPROVED_AT = UtcInstant.from_datetime(datetime(2026, 8, 21, 20, 0, tzinfo=UTC))
RECORDED_AT = UtcInstant.from_datetime(datetime(2026, 8, 21, 20, 5, tzinfo=UTC))
PINNED_BEFORE_ACTIVATION = UtcInstant.from_datetime(datetime(2026, 8, 22, 20, 5, tzinfo=UTC))
ACTIVATED_AT = UtcInstant.from_datetime(datetime(2026, 8, 24, 20, 5, tzinfo=UTC))
APPROVED_SESSION = MarketSession(date(2026, 8, 21))
ACTIVATION_SESSION = MarketSession(date(2026, 8, 24))
AMENDMENT_VERSION = 2
SHA256_HEX_LENGTH = 64


def _reseal(envelope: dict[str, object]) -> None:
    material = {key: value for key, value in envelope.items() if key != "content_hash"}
    envelope["content_hash"] = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _artifact(
    *, version: int = 2, final_clause: str = "Never force activity."
) -> ConstitutionArtifact:
    return ConstitutionArtifact.create(
        version=version,
        clauses=(*ACTIVE_CONSTITUTION.clauses[:-1], final_clause),
    )


def _request(
    *,
    artifact: ConstitutionArtifact | None = None,
    identity_value: str = "constitution-amendment-2",
) -> GovernanceRequest:
    chosen = _artifact() if artifact is None else artifact
    identity = GovernanceRequestIdentity(identity_value)
    unsigned = OperatorApprovalProof(
        request_identity=identity,
        constitution_hash=chosen.content_hash,
        activation_session=ACTIVATION_SESSION,
        approved_session=APPROVED_SESSION,
        approved_at=APPROVED_AT,
        key_id="operator-key-1",
        signature="0" * 64,
    )
    proof = replace(
        unsigned,
        signature=hashlib.sha256(unsigned.signing_bytes()).hexdigest(),
    )
    return GovernanceRequest(identity, chosen, ACTIVATION_SESSION, proof)


def _verify(proof: OperatorApprovalProof) -> ApprovalVerification:
    expected = hashlib.sha256(replace(proof, signature="0" * 64).signing_bytes()).hexdigest()
    if proof.key_id != "operator-key-1":
        return ApprovalVerification.UNAUTHORIZED
    if proof.signature != expected:
        return ApprovalVerification.INVALID_SIGNATURE
    return ApprovalVerification.VERIFIED


def _reject(proof: OperatorApprovalProof) -> ApprovalVerification:
    del proof
    return ApprovalVerification.INVALID_SIGNATURE


def test_constitution_artifact_is_immutable_versioned_and_hash_consistent() -> None:
    artifact = _artifact()

    assert artifact.version == AMENDMENT_VERSION
    assert len(artifact.content_hash) == SHA256_HEX_LENGTH
    assert ConstitutionArtifact.parse(artifact.to_payload()) == artifact
    assert ConstitutionArtifact.parse({**artifact.to_payload(), "content_hash": "f" * 64}) is None
    assert ConstitutionArtifact.parse({**artifact.to_payload(), "schema_version": 2}) is None
    with pytest.raises(AttributeError):
        # Frozen artifact immutability is the behavior under test, so assignment is intentional.
        artifact.clauses = ("changed",)  # type: ignore[misc]
    with pytest.raises(ValueError, match="invalid Constitution artifact"):
        ConstitutionArtifact(1, ("clause",), "f" * SHA256_HEX_LENGTH)


@pytest.mark.parametrize(
    ("version", "clauses"),
    [
        (0, ("clause",)),
        (1, ()),
        (1, ("",)),
        (1, (" leading",)),
        (1, ("x" * 2_001,)),
        (1, tuple("x" for _ in range(33))),
        (1, tuple("x" * 1_100 for _ in range(30))),
    ],
)
def test_constitution_artifact_rejects_unbounded_or_noncanonical_content(
    version: int, clauses: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="invalid Constitution artifact"):
        ConstitutionArtifact.create(version=version, clauses=clauses)


@pytest.mark.parametrize(
    ("version", "content_hash"),
    [(0, "a" * SHA256_HEX_LENGTH), (1, "invalid")],
)
def test_constitution_reference_rejects_invalid_identity(version: int, content_hash: str) -> None:
    with pytest.raises(ValueError, match="invalid Constitution reference"):
        ConstitutionReference(version, content_hash)


@pytest.mark.parametrize("identity", ["", "has space", "x" * 129, 1])
def test_governance_request_identity_rejects_unstable_values(identity: object) -> None:
    assert GovernanceRequestIdentity.parse(identity) is None


def test_operator_approval_parser_rejects_unknown_versions_authority_and_fields() -> None:
    proof = _request().approval.to_payload()
    invalid_values: list[object] = [None, {**proof, "schema_version": 2}]
    invalid_values.extend(
        [
            {**proof, "authority_scope": "model"},
            {**proof, "request_identity": "has space"},
            {**proof, "activation_session": "invalid"},
            {**proof, "approved_session": "invalid"},
            {**proof, "approved_at": "invalid"},
            {**proof, "constitution_hash": 3},
            {**proof, "key_id": "has space"},
            {**proof, "signature": "short"},
            {**proof, "extra": True},
        ]
    )

    assert all(OperatorApprovalProof.parse(value) is None for value in invalid_values)


def test_governance_request_rejects_unsigned_inconsistent_and_non_future_material() -> None:
    request = _request()

    assert (
        parse_governance_request(
            request_identity=request.identity.value,
            artifact=request.artifact.to_payload(),
            activation_session=request.activation_session.to_payload(),
            approval_proof=request.approval.to_payload(),
        )
        == request
    )
    unsigned = parse_governance_request(
        request_identity=request.identity.value,
        artifact=request.artifact.to_payload(),
        activation_session=request.activation_session.to_payload(),
        approval_proof=None,
    )
    assert isinstance(unsigned, GovernanceInputRefusal)
    assert unsigned.reason.value == "unsigned"
    mismatched = parse_governance_request(
        request_identity=request.identity.value,
        artifact=request.artifact.to_payload(),
        activation_session=request.activation_session.to_payload(),
        approval_proof={
            **request.approval.to_payload(),
            "constitution_hash": "f" * 64,
        },
    )
    assert isinstance(mismatched, GovernanceInputRefusal)
    assert mismatched.reason.value == "approval_mismatch"
    non_future = parse_governance_request(
        request_identity=request.identity.value,
        artifact=request.artifact.to_payload(),
        activation_session=APPROVED_SESSION.to_payload(),
        approval_proof={
            **request.approval.to_payload(),
            "activation_session": APPROVED_SESSION.to_payload(),
        },
    )
    assert isinstance(non_future, GovernanceInputRefusal)
    assert non_future.reason.value == "non_future_activation"
    invalid_identity = parse_governance_request(
        request_identity="has space",
        artifact=request.artifact.to_payload(),
        activation_session=request.activation_session.to_payload(),
        approval_proof=request.approval.to_payload(),
    )
    assert isinstance(invalid_identity, GovernanceInputRefusal)
    assert invalid_identity.reason is GovernanceRefusalReason.INVALID_REQUEST_IDENTITY
    invalid_session = parse_governance_request(
        request_identity=request.identity.value,
        artifact=request.artifact.to_payload(),
        activation_session="invalid",
        approval_proof=request.approval.to_payload(),
    )
    assert isinstance(invalid_session, GovernanceInputRefusal)
    assert invalid_session.reason is GovernanceRefusalReason.INVALID_ACTIVATION_SESSION
    typed_session = parse_governance_request(
        request_identity=request.identity.value,
        artifact=request.artifact.to_payload(),
        activation_session=request.activation_session,
        approval_proof=request.approval.to_payload(),
    )
    assert typed_session == request


def test_invalid_request_fingerprints_are_type_strict_cycle_safe_and_retry_bounded() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    nested: object = "leaf"
    for _ in range(34):
        nested = [nested]
    hostile_values: tuple[object, ...] = (
        True,
        -(1 << 600),
        1.5,
        b"bytes",
        ("tuple",),
        {"set"},
        frozenset({"frozen"}),
        cyclic,
        nested,
        object(),
    )
    fingerprints: set[str] = set()
    for value in hostile_values:
        first = parse_governance_request(
            request_identity=value,
            artifact={"invalid": True},
            activation_session=ACTIVATION_SESSION.to_payload(),
            approval_proof=None,
        )
        second = parse_governance_request(
            request_identity=value,
            artifact={"invalid": True},
            activation_session=ACTIVATION_SESSION.to_payload(),
            approval_proof=None,
        )
        assert isinstance(first, GovernanceInputRefusal)
        assert isinstance(second, GovernanceInputRefusal)
        assert first.request_fingerprint == second.request_fingerprint
        fingerprints.add(first.request_fingerprint)
    assert len(fingerprints) == len(hostile_values)

    first_decision = decide_governance(
        ConstitutionGovernanceHistory(),
        parse_governance_request(
            request_identity=[],
            artifact={"invalid": True},
            activation_session=ACTIVATION_SESSION.to_payload(),
            approval_proof=None,
        ),
        None,
        RECORDED_AT,
    )
    history = ConstitutionGovernanceHistory().append(first_decision.record)
    replay = decide_governance(
        history,
        parse_governance_request(
            request_identity=[],
            artifact={"invalid": True},
            activation_session=ACTIVATION_SESSION.to_payload(),
            approval_proof=None,
        ),
        None,
        RECORDED_AT,
    )
    changed = decide_governance(
        history,
        parse_governance_request(
            request_identity=["changed"],
            artifact={"invalid": True},
            activation_session=ACTIVATION_SESSION.to_payload(),
            approval_proof=None,
        ),
        None,
        RECORDED_AT,
    )
    assert replay.record is None
    assert replay.receipt.disposition is GovernanceDisposition.REPLAYED
    assert changed.record is not None
    assert changed.receipt.disposition is GovernanceDisposition.REFUSED


def test_schedule_retry_conflict_and_exact_boundary_activation_are_deterministic() -> None:
    request = _request()
    empty = ConstitutionGovernanceHistory()

    scheduled = decide_governance(empty, request, _verify(request.approval), RECORDED_AT)
    assert scheduled.receipt.disposition is GovernanceDisposition.SCHEDULED
    history = empty.append(scheduled.record)

    replayed = decide_governance(history, request, _verify(request.approval), RECORDED_AT)
    assert replayed.record is None
    assert replayed.receipt.disposition is GovernanceDisposition.REPLAYED
    assert replayed.receipt.replayed_disposition is GovernanceDisposition.SCHEDULED

    changed = _request(artifact=_artifact(final_clause="Changed material under the same identity."))
    conflicted = decide_governance(history, changed, _verify(changed.approval), RECORDED_AT)
    assert conflicted.receipt.disposition is GovernanceDisposition.CONFLICTED
    conflicted_history = history.append(conflicted.record)
    replayed_conflict = decide_governance(
        conflicted_history, changed, _verify(changed.approval), RECORDED_AT
    )
    assert replayed_conflict.record is None
    assert replayed_conflict.receipt.disposition is GovernanceDisposition.REPLAYED
    assert replayed_conflict.receipt.replayed_disposition is GovernanceDisposition.CONFLICTED

    before = activate_constitution(history, MarketSession(date(2026, 8, 22)), RECORDED_AT)
    assert before.record is None
    assert before.constitution == ACTIVE_CONSTITUTION.reference

    activated = activate_constitution(history, ACTIVATION_SESSION, RECORDED_AT)
    assert activated.record is not None
    assert activated.receipt is not None
    assert activated.receipt.disposition is GovernanceDisposition.ACTIVATED
    activated_history = history.append(activated.record)
    state = reconstruct_constitution_governance(activated_history, _verify)
    assert state.active == request.artifact.reference
    assert state.pending == ()
    assert state.superseded == (ACTIVE_CONSTITUTION.reference,)
    assert state.constitution_for(APPROVED_SESSION) == ACTIVE_CONSTITUTION

    replay = activate_constitution(activated_history, ACTIVATION_SESSION, RECORDED_AT)
    assert replay.record is None
    assert replay.receipt is not None
    assert replay.receipt.disposition is GovernanceDisposition.REPLAYED
    assert replay.constitution == request.artifact.reference


def test_invalid_signature_and_missed_boundary_fail_closed() -> None:
    request = _request()
    invalid = decide_governance(
        ConstitutionGovernanceHistory(),
        request,
        ApprovalVerification.INVALID_SIGNATURE,
        RECORDED_AT,
    )
    assert invalid.receipt.disposition is GovernanceDisposition.REFUSED
    assert invalid.receipt.reason is not None
    assert invalid.receipt.reason.value == "invalid_signature"

    scheduled = decide_governance(
        ConstitutionGovernanceHistory(),
        request,
        ApprovalVerification.VERIFIED,
        RECORDED_AT,
    )
    history = ConstitutionGovernanceHistory().append(scheduled.record)
    with pytest.raises(GovernanceStateError, match="missed Constitution activation boundary"):
        activate_constitution(history, MarketSession(date(2026, 8, 25)), RECORDED_AT)


def test_constitution_uses_must_resolve_exactly_from_governance_history() -> None:
    request = _request()
    scheduled = decide_governance(
        ConstitutionGovernanceHistory(),
        request,
        ApprovalVerification.VERIFIED,
        RECORDED_AT,
    )
    scheduled_history = ConstitutionGovernanceHistory().append(scheduled.record)
    activation = activate_constitution(scheduled_history, ACTIVATION_SESSION, ACTIVATED_AT)
    activated_history = scheduled_history.append(activation.record)
    valid_uses = (
        ConstitutionUse(APPROVED_SESSION, ACTIVE_CONSTITUTION.reference, RECORDED_AT),
        ConstitutionUse(
            ACTIVATION_SESSION,
            ACTIVE_CONSTITUTION.reference,
            PINNED_BEFORE_ACTIVATION,
        ),
        ConstitutionUse(ACTIVATION_SESSION, request.artifact.reference, ACTIVATED_AT),
    )

    assert validate_constitution_uses(activated_history, _verify, valid_uses).active == (
        request.artifact.reference
    )
    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        validate_constitution_uses(
            activated_history,
            _verify,
            (ConstitutionUse(ACTIVATION_SESSION, ACTIVE_CONSTITUTION.reference, ACTIVATED_AT),),
        )
    with pytest.raises(ValueError, match="invalid Constitution reference"):
        # Static typing cannot represent the hostile value needed to exercise this runtime guard.
        ConstitutionUse(
            ACTIVATION_SESSION,
            "invalid",  # type: ignore[arg-type]
            RECORDED_AT,
        )
    with pytest.raises(ValueError, match="invalid Constitution reference"):
        # Static typing cannot represent the hostile value needed to exercise this runtime guard.
        ConstitutionUse(
            ACTIVATION_SESSION,
            ACTIVE_CONSTITUTION.reference,
            "invalid",  # type: ignore[arg-type]
        )


def test_governance_refuses_retroactive_authoritative_event_times() -> None:
    request = _request()
    scheduled = decide_governance(
        ConstitutionGovernanceHistory(),
        request,
        ApprovalVerification.VERIFIED,
        RECORDED_AT,
    )
    history = ConstitutionGovernanceHistory().append(scheduled.record)
    retroactive = UtcInstant.from_datetime(datetime(2026, 8, 21, 20, 4, tzinfo=UTC))

    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        decide_governance(
            history,
            _request(identity_value="retroactive-request"),
            ApprovalVerification.VERIFIED,
            retroactive,
        )
    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        activate_constitution(history, ACTIVATION_SESSION, retroactive)


def test_governance_decisions_bound_refusals_pending_versions_and_future_approval() -> None:
    request = _request()
    invalid_input = GovernanceInputRefusal(
        GovernanceRefusalReason.INVALID_ARTIFACT,
        None,
        "f" * SHA256_HEX_LENGTH,
    )
    refused_input = decide_governance(
        ConstitutionGovernanceHistory(), invalid_input, None, RECORDED_AT
    )
    assert refused_input.receipt.reason is GovernanceRefusalReason.INVALID_ARTIFACT

    unauthorized = decide_governance(
        ConstitutionGovernanceHistory(), request, ApprovalVerification.UNAUTHORIZED, RECORDED_AT
    )
    assert unauthorized.receipt.reason is GovernanceRefusalReason.UNAUTHORIZED_OPERATOR
    unsigned = decide_governance(ConstitutionGovernanceHistory(), request, None, RECORDED_AT)
    assert unsigned.receipt.reason is GovernanceRefusalReason.UNSIGNED

    future_request = replace(
        request,
        approval=replace(
            request.approval,
            approved_at=UtcInstant.from_datetime(datetime(2026, 8, 21, 21, 0, tzinfo=UTC)),
        ),
    )
    future = decide_governance(
        ConstitutionGovernanceHistory(),
        future_request,
        ApprovalVerification.VERIFIED,
        RECORDED_AT,
    )
    assert future.receipt.reason is GovernanceRefusalReason.FUTURE_APPROVAL

    scheduled = decide_governance(
        ConstitutionGovernanceHistory(), request, ApprovalVerification.VERIFIED, RECORDED_AT
    )
    pending_history = ConstitutionGovernanceHistory().append(scheduled.record)
    second = _request(artifact=_artifact(version=3), identity_value="constitution-amendment-3")
    pending = decide_governance(pending_history, second, ApprovalVerification.VERIFIED, RECORDED_AT)
    assert pending.receipt.reason is GovernanceRefusalReason.PENDING_AMENDMENT

    wrong_version = _request(
        artifact=_artifact(version=4), identity_value="constitution-amendment-4"
    )
    version = decide_governance(
        ConstitutionGovernanceHistory(),
        wrong_version,
        ApprovalVerification.VERIFIED,
        RECORDED_AT,
    )
    assert version.receipt.reason is GovernanceRefusalReason.VERSION_CONFLICT

    refused_history = ConstitutionGovernanceHistory().append(unauthorized.record)
    refused_state = reconstruct_constitution_governance(refused_history, _verify)
    assert refused_state.refusals[0].constitution == request.artifact.reference
    assert refused_state.refusals[0].activation_session == request.activation_session
    replayed = decide_governance(
        refused_history, request, ApprovalVerification.VERIFIED, RECORDED_AT
    )
    assert replayed.receipt.disposition is GovernanceDisposition.REPLAYED
    assert replayed.receipt.replayed_disposition is GovernanceDisposition.REFUSED


def test_governance_event_round_trip_rejects_every_hostile_envelope_layer() -> None:
    request = _request()
    scheduled = decide_governance(
        ConstitutionGovernanceHistory(), request, ApprovalVerification.VERIFIED, RECORDED_AT
    )
    assert scheduled.record is not None
    envelope = scheduled.record.to_payload()
    assert parse_governance_event(envelope) == scheduled.record

    mutations: list[tuple[str, object]] = [
        ("root_extra", True),
        ("payload_extra", True),
        ("payload_discriminator", "unknown"),
        ("envelope_schema_version", 2),
        ("record_kind", "unknown"),
        ("authority_scope", "model"),
        ("recorded_at", "invalid"),
        ("sequence", "0"),
        ("request_identity", "has space"),
        ("request_fingerprint", "invalid"),
        ("artifact", {"invalid": True}),
        ("activation_session", "invalid"),
        ("approval", {"invalid": True}),
        ("reason", "unknown"),
    ]
    for field, value in mutations:
        hostile = deepcopy(envelope)
        if field == "root_extra":
            hostile["extra"] = value
        elif field == "payload_extra":
            payload = hostile["payload"]
            assert isinstance(payload, dict)
            payload["extra"] = value
        elif field in {
            "sequence",
            "request_identity",
            "request_fingerprint",
            "artifact",
            "activation_session",
            "approval",
            "reason",
        }:
            payload = hostile["payload"]
            assert isinstance(payload, dict)
            payload[field] = value
        else:
            hostile[field] = value
        _reseal(hostile)
        assert parse_governance_event(hostile) is None

    unsealed = deepcopy(envelope)
    unsealed["recorded_at"] = "2026-08-21T20:06:00.000000+00:00"
    assert parse_governance_event(unsealed) is None


def test_history_reconstruction_rejects_invalid_sequence_signature_and_transitions() -> None:
    request = _request()
    scheduled = decide_governance(
        ConstitutionGovernanceHistory(), request, ApprovalVerification.VERIFIED, RECORDED_AT
    )
    assert scheduled.record is not None
    event = scheduled.record

    with pytest.raises(ValueError, match="empty governance decision"):
        ConstitutionGovernanceHistory().append(None)
    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        ConstitutionGovernanceHistory().append(replace(event, sequence=1))
    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        reconstruct_constitution_governance(
            ConstitutionGovernanceHistory((replace(event, sequence=1),)), _verify
        )

    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        reconstruct_constitution_governance(ConstitutionGovernanceHistory((event,)), _reject)

    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        reconstruct_constitution_governance(
            ConstitutionGovernanceHistory(
                (replace(event, request_fingerprint="e" * SHA256_HEX_LENGTH),)
            ),
            _verify,
        )

    approval = event.approval
    assert approval is not None
    future_approval = replace(
        approval,
        approved_at=UtcInstant.from_datetime(datetime(2026, 8, 21, 21, 0, tzinfo=UTC)),
    )
    future_approval = replace(
        future_approval,
        signature=hashlib.sha256(future_approval.signing_bytes()).hexdigest(),
    )
    future_request = GovernanceRequest(
        request.identity,
        request.artifact,
        request.activation_session,
        future_approval,
    )
    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        reconstruct_constitution_governance(
            ConstitutionGovernanceHistory(
                (
                    replace(
                        event,
                        request_fingerprint=future_request.fingerprint,
                        approval=future_approval,
                    ),
                )
            ),
            _verify,
        )

    second_pending = replace(
        event,
        sequence=1,
        request_identity=GovernanceRequestIdentity("second-request"),
        request_fingerprint="e" * SHA256_HEX_LENGTH,
    )
    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        reconstruct_constitution_governance(
            ConstitutionGovernanceHistory((event, second_pending)), _verify
        )

    wrong_activation = GovernanceEvent(
        1,
        GovernanceEventKind.ACTIVATED,
        GovernanceRequestIdentity("wrong-request"),
        event.request_fingerprint,
        event.artifact,
        event.activation_session,
        None,
        None,
        RECORDED_AT,
    )
    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        reconstruct_constitution_governance(
            ConstitutionGovernanceHistory((event, wrong_activation)), _verify
        )

    activation = activate_constitution(
        ConstitutionGovernanceHistory((event,)), ACTIVATION_SESSION, RECORDED_AT
    )
    assert activation.record is not None
    early_activation = replace(
        activation.record,
        recorded_at=UtcInstant.from_datetime(datetime(2026, 8, 21, 19, 0, tzinfo=UTC)),
    )
    with pytest.raises(GovernanceStateError, match="invalid Constitution governance history"):
        reconstruct_constitution_governance(
            ConstitutionGovernanceHistory((event, early_activation)), _verify
        )


def test_event_and_request_constructors_reject_impossible_typed_states() -> None:
    request = _request()
    with pytest.raises(ValueError, match="invalid governance request"):
        GovernanceRequest(
            GovernanceRequestIdentity("different-request"),
            request.artifact,
            request.activation_session,
            request.approval,
        )
    with pytest.raises(ValueError, match="invalid governance refusal fingerprint"):
        GovernanceInputRefusal(GovernanceRefusalReason.INVALID_ARTIFACT, None, "invalid")
    with pytest.raises(ValueError, match="invalid governance event"):
        GovernanceEvent(
            0,
            GovernanceEventKind.SCHEDULED,
            request.identity,
            request.fingerprint,
            request.artifact,
            request.activation_session,
            None,
            None,
            RECORDED_AT,
        )
