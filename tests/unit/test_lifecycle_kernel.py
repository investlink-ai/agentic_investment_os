from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.lifecycle import (
    AdvanceAttempt,
    AdvanceCommand,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRecovery,
    AdvanceRequest,
    AppendLifecycleRecord,
    AppendTerminalLifecycleRecord,
    DurableAdvanceConflict,
    DurableAdvanceRefusal,
    EvidenceCaptureCheckpoint,
    InputRefusal,
    InputRefusalCode,
    InvalidLifecycleStateError,
    LifecycleCheckpoint,
    LifecycleCommand,
    LifecycleDecision,
    LifecycleEvent,
    LifecycleEventKind,
    LifecycleHistory,
    LifecycleLiveness,
    LifecyclePhase,
    LifecycleProgress,
    PerformAttentionSelection,
    PerformEvidenceCapture,
    decide_evidence_refusal_replay,
    decide_invalid_history,
    decide_terminal_refusal,
    derive_lifecycle_status,
    reconstruct_lifecycle,
)
from agentic_investment_os.domain.lifecycle import (
    decide_advance as _decide_advance,
)
from agentic_investment_os.domain.temporal import UtcInstant
from tests._attention import attention_artifact
from tests._universe import (
    advance_command,
    pinned_run_identity,
    universe_snapshot,
)

if TYPE_CHECKING:
    from agentic_investment_os.domain.attention import AttentionArtifact

SHA256_HEX_LENGTH = 64
EVIDENCE_CAPTURE = EvidenceCaptureCheckpoint(
    "c" * SHA256_HEX_LENGTH,
    ("d" * SHA256_HEX_LENGTH,),
    (),
)
REFUSED_EVIDENCE_CAPTURE = EvidenceCaptureCheckpoint(
    "c" * SHA256_HEX_LENGTH,
    ("d" * SHA256_HEX_LENGTH,),
    ("e" * SHA256_HEX_LENGTH,),
)
INVALID_HISTORY_REFUSAL_SEQUENCE = 5
KERNEL_RECORDED_AT = UtcInstant.from_datetime(datetime(2026, 8, 21, 22, 0, tzinfo=UTC))


def decide_advance(
    history: LifecycleHistory,
    command: LifecycleCommand,
    attempt: AdvanceAttempt,
) -> LifecycleDecision:
    return _decide_advance(history, command, attempt, KERNEL_RECORDED_AT)


def _command(
    *,
    session: str = "2026-08-21",
    key: str = "kernel-key",
    configuration_hash: str = "a" * SHA256_HEX_LENGTH,
) -> AdvanceCommand:
    request = AdvanceRequest.parse(
        session=session,
        mode="champion",
        idempotency_key=key,
    )
    assert isinstance(request, AdvanceRequest)
    return advance_command(
        request,
        configuration_hash=configuration_hash,
    )


def _append_decision(
    history: LifecycleHistory,
    command: AdvanceCommand | InputRefusal,
    attempt: AdvanceAttempt,
) -> tuple[LifecycleHistory, AdvanceAttempt, AdvanceReceipt | None]:
    decision = decide_advance(history, command, attempt)
    if isinstance(decision, PerformEvidenceCapture):
        assert isinstance(command, AdvanceCommand)
        command = replace(command, evidence_capture=EVIDENCE_CAPTURE)
        decision = decide_advance(history, command, attempt)
    if isinstance(decision, PerformAttentionSelection):
        assert isinstance(command, AdvanceCommand)
        command = replace(
            command,
            attention_selection=attention_artifact(
                command.pinned_run_identity,
                command.universe_snapshot,
                EVIDENCE_CAPTURE,
                decision.attention_history,
            ),
        )
        decision = decide_advance(history, command, attempt)
    assert not isinstance(decision, AdvanceReceipt)
    assert not isinstance(decision, PerformEvidenceCapture)
    assert not isinstance(decision, PerformAttentionSelection)
    next_history = history.append(decision.record)
    if isinstance(decision, AppendTerminalLifecycleRecord):
        return next_history, attempt, decision.receipt
    return next_history, decision.attempt, None


def _complete(
    history: LifecycleHistory,
    command: AdvanceCommand,
    attempt: AdvanceAttempt | None = None,
) -> tuple[LifecycleHistory, AdvanceReceipt]:
    current_attempt = AdvanceAttempt() if attempt is None else attempt
    while True:
        decision = decide_advance(history, command, current_attempt)
        if isinstance(decision, AdvanceReceipt):
            return history, decision
        if isinstance(decision, PerformEvidenceCapture):
            command = replace(command, evidence_capture=EVIDENCE_CAPTURE)
            continue
        if isinstance(decision, PerformAttentionSelection):
            command = replace(
                command,
                attention_selection=attention_artifact(
                    command.pinned_run_identity,
                    command.universe_snapshot,
                    EVIDENCE_CAPTURE,
                    decision.attention_history,
                ),
            )
            continue
        history = history.append(decision.record)
        if isinstance(decision, AppendTerminalLifecycleRecord):
            return history, decision.receipt
        current_attempt = decision.attempt


def _first_event(command: AdvanceCommand) -> LifecycleEvent:
    decision = decide_advance(LifecycleHistory(), command, AdvanceAttempt())
    assert isinstance(decision, AppendLifecycleRecord)
    assert isinstance(decision.record, LifecycleEvent)
    return decision.record


def _through_snapshot(command: AdvanceCommand) -> tuple[LifecycleHistory, AdvanceAttempt]:
    history = LifecycleHistory()
    attempt = AdvanceAttempt()
    for _ in range(4):
        decision = decide_advance(history, command, attempt)
        assert isinstance(decision, AppendLifecycleRecord)
        assert isinstance(decision.record, LifecycleEvent)
        history = history.append(decision.record)
        attempt = decision.attempt
    return history, attempt


def test_kernel_assigns_universe_events_and_fresh_recovery() -> None:
    request = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key="kernel-fresh",
    )
    assert isinstance(request, AdvanceRequest)
    identity = pinned_run_identity(
        request,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    command = AdvanceCommand(request, identity, universe_snapshot(identity))
    history = LifecycleHistory()
    attempt = AdvanceAttempt()

    expected = (
        (0, LifecycleEventKind.ADVANCE_REQUESTED),
        (1, LifecycleEventKind.PHASE_COMPLETED),
        (2, LifecycleEventKind.RUN_INPUTS_PINNED),
        (3, LifecycleEventKind.UNIVERSE_SNAPSHOTTED),
        (4, LifecycleEventKind.EVIDENCE_CAPTURED),
        (5, LifecycleEventKind.ATTENTION_SELECTED),
    )
    completed: AppendTerminalLifecycleRecord | None = None
    for sequence, event_kind in expected:
        decision = decide_advance(history, command, attempt)
        if isinstance(decision, PerformEvidenceCapture):
            command = replace(command, evidence_capture=EVIDENCE_CAPTURE)
            decision = decide_advance(history, command, attempt)
        if isinstance(decision, PerformAttentionSelection):
            command = replace(
                command,
                attention_selection=attention_artifact(
                    command.pinned_run_identity,
                    command.universe_snapshot,
                    EVIDENCE_CAPTURE,
                    decision.attention_history,
                ),
            )
            decision = decide_advance(history, command, attempt)
        assert not isinstance(decision, AdvanceReceipt)
        assert not isinstance(decision, PerformEvidenceCapture)
        assert not isinstance(decision, PerformAttentionSelection)
        assert isinstance(decision.record, LifecycleEvent)
        assert decision.record.sequence == sequence
        assert decision.record.event_kind is event_kind
        history = history.append(decision.record)
        if isinstance(decision, AppendTerminalLifecycleRecord):
            completed = decision
        else:
            assert decision.attempt.recovery is AdvanceRecovery.FRESH
            attempt = decision.attempt

    assert completed is not None
    assert completed.receipt.recovery is AdvanceRecovery.FRESH


def test_partial_progress_refuses_attention_access_before_its_checkpoint() -> None:
    command = _command()
    progress = LifecycleProgress(
        command.request,
        command.pinned_run_identity,
        LifecyclePhase.CAPTURE_EVIDENCE,
        4,
        command.universe_snapshot,
        EVIDENCE_CAPTURE,
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream checkpoint order is invalid",
    ):
        progress.require_attention_artifact()


@pytest.mark.parametrize(
    "invalid_selection",
    ["wrong_type", "changed_artifact", "changed_cutoff", "changed_regime", "changed_available"],
)
def test_kernel_refuses_invalid_attention_selection_at_publication(
    invalid_selection: str,
) -> None:
    command = _command()
    history, attempt = _through_snapshot(command)
    command = replace(command, evidence_capture=EVIDENCE_CAPTURE)
    captured = decide_advance(history, command, attempt)
    assert isinstance(captured, AppendLifecycleRecord)
    history = history.append(captured.record)
    attempt = captured.attempt
    artifact = attention_artifact(
        command.pinned_run_identity,
        command.universe_snapshot,
        EVIDENCE_CAPTURE,
    )
    if invalid_selection == "wrong_type":
        selection = cast("AttentionArtifact", "invalid")
    elif invalid_selection == "changed_artifact":
        object.__setattr__(artifact, "run_id", "f" * SHA256_HEX_LENGTH)
        selection = artifact
    elif invalid_selection == "changed_cutoff":
        object.__setattr__(
            artifact,
            "cutoff",
            UtcInstant(artifact.cutoff.value + timedelta(seconds=1)),
        )
        selection = artifact
    elif invalid_selection == "changed_regime":
        object.__setattr__(artifact, "data_regime", "alpaca:iex")
        selection = artifact
    else:
        object.__setattr__(
            artifact,
            "available_at",
            UtcInstant(artifact.available_at.value - timedelta(seconds=1)),
        )
        selection = artifact
    invalid_command = replace(
        command,
        attention_selection=selection,
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream checkpoint order is invalid",
    ):
        decide_advance(history, invalid_command, attempt)


def test_kernel_resumes_partial_history_and_replays_completion() -> None:
    command = _command()
    history, _, _ = _append_decision(LifecycleHistory(), command, AdvanceAttempt())

    completed_history, resumed = _complete(history, command)
    replay = decide_advance(completed_history, command, AdvanceAttempt())

    assert resumed == AdvanceReceipt.advanced(
        command.pinned_run_identity,
        command.universe_snapshot,
        AdvanceRecovery.RESUMED,
        KERNEL_RECORDED_AT,
        EVIDENCE_CAPTURE,
        attention_artifact(
            command.pinned_run_identity,
            command.universe_snapshot,
            EVIDENCE_CAPTURE,
        ),
    )
    assert replay == AdvanceReceipt.advanced(
        command.pinned_run_identity,
        command.universe_snapshot,
        AdvanceRecovery.PREVIOUSLY_COMPLETED,
        KERNEL_RECORDED_AT,
        EVIDENCE_CAPTURE,
        attention_artifact(
            command.pinned_run_identity,
            command.universe_snapshot,
            EVIDENCE_CAPTURE,
        ),
    )


@pytest.mark.parametrize("corruption", ["artifact_before_phase", "missing_artifact"])
def test_reconstruction_requires_attention_only_at_its_checkpoint(corruption: str) -> None:
    command = _command()
    history, _ = _complete(LifecycleHistory(), command)
    artifact = history.events[-1].attention_artifact
    assert artifact is not None
    if corruption == "artifact_before_phase":
        object.__setattr__(history.events[-2], "attention_artifact", artifact)
    else:
        object.__setattr__(history.events[-1], "attention_artifact", None)

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream checkpoint order is invalid",
    ):
        reconstruct_lifecycle(history)


def test_reconstruction_rejects_attention_that_changes_pinned_facts() -> None:
    command = _command()
    history, _ = _complete(LifecycleHistory(), command)
    artifact = history.events[-1].attention_artifact
    assert artifact is not None
    object.__setattr__(artifact, "run_id", "f" * SHA256_HEX_LENGTH)

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream changed pinned request facts",
    ):
        reconstruct_lifecycle(history)


def test_reconstruction_rejects_a_discontinuous_attention_history_chain() -> None:
    first_history, _ = _complete(
        LifecycleHistory(),
        _command(session="2026-08-21", key="first-attention-cycle"),
    )
    second_history, _ = _complete(
        LifecycleHistory(),
        _command(session="2026-08-22", key="second-attention-cycle"),
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream changed pinned request facts",
    ):
        reconstruct_lifecycle(
            LifecycleHistory(events=(*first_history.events, *second_history.events))
        )


@pytest.mark.parametrize(
    "changed_field",
    [
        "run_id",
        "cycle",
        "data_regime",
        "evidence_cutoff",
        "instrument_snapshot_hash",
        "position_snapshot_hash",
        "eligibility_policy_hash",
    ],
)
def test_kernel_rejects_each_snapshot_fact_that_changes_pinned_material(
    changed_field: str,
) -> None:
    command = _command()
    complete_history, _ = _complete(LifecycleHistory(), command)
    original = command.universe_snapshot
    if changed_field == "run_id":
        changed_snapshot = replace(original, run_id="f" * SHA256_HEX_LENGTH)
    elif changed_field == "cycle":
        changed_snapshot = replace(original, cycle=MarketSession(date(2026, 8, 22)))
    elif changed_field == "data_regime":
        changed_snapshot = replace(
            original,
            inputs=replace(original.inputs, data_regime="different-regime-v1"),
        )
    elif changed_field == "evidence_cutoff":
        changed_snapshot = replace(
            original,
            inputs=replace(
                original.inputs,
                evidence_cutoff=UtcInstant.from_datetime(
                    original.inputs.evidence_cutoff.value + timedelta(seconds=1)
                ),
            ),
        )
    elif changed_field == "instrument_snapshot_hash":
        changed_snapshot = replace(
            original,
            inputs=replace(
                original.inputs,
                instrument_snapshot=replace(
                    original.inputs.instrument_snapshot,
                    material_fingerprint="f" * SHA256_HEX_LENGTH,
                ),
            ),
        )
    elif changed_field == "position_snapshot_hash":
        changed_snapshot = replace(
            original,
            inputs=replace(
                original.inputs,
                position_snapshot=replace(
                    original.inputs.position_snapshot,
                    fingerprint="f" * SHA256_HEX_LENGTH,
                ),
            ),
        )
    else:
        assert changed_field == "eligibility_policy_hash"
        changed_snapshot = replace(
            original,
            policy=replace(original.policy, fingerprint="f" * SHA256_HEX_LENGTH),
        )
    changed_pinned_event = replace(
        complete_history.events[2],
        prepared_universe_snapshot=changed_snapshot,
    )
    changed_history = LifecycleHistory(
        events=(
            *complete_history.events[:2],
            changed_pinned_event,
            *complete_history.events[3:],
        ),
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream changed pinned request facts",
    ):
        reconstruct_lifecycle(changed_history)


def test_kernel_rejects_a_pinned_cycle_that_differs_from_its_request() -> None:
    command = _command()
    decision = decide_advance(LifecycleHistory(), command, AdvanceAttempt())
    assert isinstance(decision, AppendLifecycleRecord)
    assert isinstance(decision.record, LifecycleEvent)
    changed_event = replace(
        decision.record,
        pinned_run_identity=replace(
            decision.record.pinned_run_identity,
            cycle=MarketSession(date(2026, 8, 22)),
        ),
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream derived identity is invalid",
    ):
        reconstruct_lifecycle(LifecycleHistory(events=(changed_event,)))


@pytest.mark.parametrize(
    ("changed_field", "expected_message"),
    [
        ("stream_id", "lifecycle stream sequence is not contiguous"),
        ("request", "lifecycle stream changed pinned request facts"),
    ],
)
def test_kernel_rejects_each_changed_event_identity_fact(
    changed_field: str,
    expected_message: str,
) -> None:
    command = _command()
    complete_history, _ = _complete(LifecycleHistory(), command)
    event = complete_history.events[1]
    assert isinstance(event, LifecycleEvent)
    if changed_field == "stream_id":
        changed_event = replace(event, stream_id="f" * SHA256_HEX_LENGTH)
    else:
        assert changed_field == "request"
        changed_event = replace(event, request=_command(session="2026-08-22").request)
    changed_history = LifecycleHistory(
        events=(complete_history.events[0], changed_event, *complete_history.events[2:]),
    )
    with pytest.raises(
        InvalidLifecycleStateError,
        match=expected_message,
    ):
        reconstruct_lifecycle(changed_history)


def test_kernel_reconstruction_retains_the_completed_universe_snapshot() -> None:
    command = _command()
    complete_history, _ = _complete(LifecycleHistory(), command)

    progress = reconstruct_lifecycle(complete_history)

    assert progress[0].universe_snapshot == command.universe_snapshot


def test_reconstructed_progress_rejects_snapshot_access_before_pin_checkpoint() -> None:
    command = _command()
    decision = decide_advance(LifecycleHistory(), command, AdvanceAttempt())
    assert isinstance(decision, AppendLifecycleRecord)
    assert isinstance(decision.record, LifecycleEvent)
    progress = reconstruct_lifecycle(LifecycleHistory(events=(decision.record,)))

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream checkpoint order is invalid",
    ):
        progress[0].require_prepared_universe_snapshot()
    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream checkpoint order is invalid",
    ):
        progress[0].require_evidence_capture()


def test_kernel_rejects_incomplete_evidence_on_a_completed_event() -> None:
    command = _command()
    complete_history, _ = _complete(LifecycleHistory(), command)
    changed_final = replace(
        complete_history.events[-1],
        evidence_capture=REFUSED_EVIDENCE_CAPTURE,
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream checkpoint order is invalid",
    ):
        reconstruct_lifecycle(
            LifecycleHistory(events=(*complete_history.events[:-1], changed_final))
        )


def test_kernel_turns_required_evidence_refusals_into_a_terminal_refusal() -> None:
    command = _command()
    history, attempt = _through_snapshot(command)
    command = replace(command, evidence_capture=REFUSED_EVIDENCE_CAPTURE)

    decision = decide_advance(history, command, attempt)

    assert isinstance(decision, AppendTerminalLifecycleRecord)
    assert isinstance(decision.record, DurableAdvanceRefusal)
    assert decision.record.evidence_capture == REFUSED_EVIDENCE_CAPTURE
    assert decision.receipt == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.EVIDENCE_CAPTURE_FAILED,
        cycle=command.request.session,
        evidence_capture=REFUSED_EVIDENCE_CAPTURE,
    )
    reconstruct_lifecycle(history.append(decision.record))


def test_kernel_replays_evidence_refusal_only_for_unchanged_pinned_inputs() -> None:
    command = _command()
    history, _ = _through_snapshot(command)
    refusal = DurableAdvanceRefusal(
        1,
        command.request.idempotency_key,
        AdvanceFailureReason.EVIDENCE_CAPTURE_FAILED,
        command.request.session,
        REFUSED_EVIDENCE_CAPTURE,
    )
    durable = replace(history, refusals=(refusal,))

    assert decide_advance(durable, command, AdvanceAttempt()) == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.EVIDENCE_CAPTURE_FAILED,
        cycle=command.request.session,
        evidence_capture=REFUSED_EVIDENCE_CAPTURE,
    )
    changed = _command(configuration_hash="c" * SHA256_HEX_LENGTH)
    assert decide_advance(durable, changed, AdvanceAttempt()) == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=command.request.session,
    )


def test_scoped_evidence_refusal_replay_fails_closed_on_missing_or_invalid_state() -> None:
    command = _command()
    history, _ = _through_snapshot(command)
    refusal = DurableAdvanceRefusal(
        1,
        command.request.idempotency_key,
        AdvanceFailureReason.EVIDENCE_CAPTURE_FAILED,
        command.request.session,
        REFUSED_EVIDENCE_CAPTURE,
    )
    invalid_event = replace(history.events[0], sequence=1)

    assert decide_evidence_refusal_replay(history, (), command) == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=command.request.session,
    )
    bounded_refusal = DurableAdvanceRefusal(
        1,
        command.request.idempotency_key,
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        command.request.session,
    )
    assert decide_evidence_refusal_replay(
        history,
        (bounded_refusal,),
        command,
    ) == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=command.request.session,
    )
    assert decide_evidence_refusal_replay(
        LifecycleHistory(events=(invalid_event,)),
        (refusal,),
        command,
    ) == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=command.request.session,
    )
    before_snapshot = LifecycleHistory(events=history.events[:-1])
    assert decide_evidence_refusal_replay(
        before_snapshot,
        (refusal,),
        command,
    ) == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=command.request.session,
    )


@pytest.mark.parametrize(
    "invalid_association",
    ["missing_progress", "wrong_phase", "missing_capture", "complete_capture"],
)
def test_kernel_rejects_each_invalid_required_evidence_refusal_association(
    invalid_association: str,
) -> None:
    command = _command()
    history, _ = _through_snapshot(command)
    evidence_capture: EvidenceCaptureCheckpoint | None = REFUSED_EVIDENCE_CAPTURE
    if invalid_association == "missing_progress":
        history = LifecycleHistory()
    elif invalid_association == "wrong_phase":
        history = LifecycleHistory(events=history.events[:-1])
    elif invalid_association == "missing_capture":
        evidence_capture = None
    else:
        assert invalid_association == "complete_capture"
        evidence_capture = EVIDENCE_CAPTURE
    refusal = DurableAdvanceRefusal(
        1,
        command.request.idempotency_key,
        AdvanceFailureReason.EVIDENCE_CAPTURE_FAILED,
        command.request.session,
        evidence_capture,
    )

    with pytest.raises(InvalidLifecycleStateError, match="invalid refusal association"):
        reconstruct_lifecycle(LifecycleHistory(events=history.events, refusals=(refusal,)))


def test_kernel_rejects_evidence_references_on_unassociated_refusals() -> None:
    command = _command()
    invalid_refusals = (
        DurableAdvanceRefusal(
            1,
            None,
            AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
            command.request.session,
            REFUSED_EVIDENCE_CAPTURE,
        ),
        DurableAdvanceRefusal(
            1,
            command.request.idempotency_key,
            AdvanceFailureReason.INVALID_MODE,
            command.request.session,
            REFUSED_EVIDENCE_CAPTURE,
        ),
    )

    for refusal in invalid_refusals:
        with pytest.raises(InvalidLifecycleStateError, match="invalid refusal association"):
            reconstruct_lifecycle(LifecycleHistory(refusals=(refusal,)))


def test_kernel_rejects_a_changed_published_snapshot_reference() -> None:
    command = _command()
    complete_history, _ = _complete(LifecycleHistory(), command)
    changed_final = replace(
        complete_history.events[3],
        published_universe_snapshot_id="f" * SHA256_HEX_LENGTH,
    )
    changed_history = LifecycleHistory(
        events=(*complete_history.events[:3], changed_final, *complete_history.events[4:]),
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream changed pinned request facts",
    ):
        reconstruct_lifecycle(changed_history)


@pytest.mark.parametrize("reference", ["unexpected_prepared", "missing_published"])
def test_kernel_rejects_a_snapshot_reference_at_the_wrong_checkpoint(reference: str) -> None:
    command = _command()
    complete_history, _ = _complete(LifecycleHistory(), command)
    events = list(complete_history.events)
    if reference == "unexpected_prepared":
        events[1] = replace(
            events[1],
            prepared_universe_snapshot=command.universe_snapshot,
        )
    else:
        assert reference == "missing_published"
        events[3] = replace(events[3], published_universe_snapshot_id=None)

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream checkpoint order is invalid",
    ):
        reconstruct_lifecycle(LifecycleHistory(events=tuple(events)))


@pytest.mark.parametrize("changed_fact", ["request", "identity"])
def test_kernel_conflicts_on_each_changed_retry_fact_independently(changed_fact: str) -> None:
    command = _command()
    complete_history, _ = _complete(LifecycleHistory(), command)
    if changed_fact == "request":
        conflicting = replace(command, request=_command(session="2026-08-22").request)
    else:
        assert changed_fact == "identity"
        conflicting = replace(
            command,
            pinned_run_identity=replace(
                command.pinned_run_identity,
                configuration_hash="f" * SHA256_HEX_LENGTH,
            ),
        )

    decision = decide_advance(complete_history, conflicting, AdvanceAttempt())

    assert isinstance(decision, AppendTerminalLifecycleRecord)
    assert decision.receipt == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=conflicting.request.session,
    )


def test_kernel_distinguishes_session_and_idempotency_conflicts() -> None:
    original = _command()
    complete_history, _ = _complete(LifecycleHistory(), original)
    reused_key = _command(session="2026-08-22")

    conflicted_history, _, completed_conflict = _append_decision(
        complete_history,
        reused_key,
        AdvanceAttempt(),
    )
    original_replay = decide_advance(conflicted_history, original, AdvanceAttempt())
    conflict_replay = decide_advance(conflicted_history, reused_key, AdvanceAttempt())

    expected_conflict = AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=reused_key.request.session,
    )
    assert completed_conflict == expected_conflict
    assert conflict_replay == expected_conflict
    assert isinstance(original_replay, AdvanceReceipt)
    assert original_replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED

    second_key = _command(key="second-key")
    refused_history, _, session_conflict = _append_decision(
        complete_history,
        second_key,
        AdvanceAttempt(),
    )
    assert session_conflict == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.SESSION_STREAM_CONFLICT,
        cycle=second_key.request.session,
    )
    assert isinstance(refused_history.refusals[-1], DurableAdvanceRefusal)


def test_kernel_derives_session_occupancy_from_reconstructed_progress() -> None:
    original = _command()
    complete_history, _ = _complete(LifecycleHistory(), original)
    history_without_occupancy_hint = LifecycleHistory(events=complete_history.events)
    same_session = _command(key="same-session-without-hint")

    decision = decide_advance(
        history_without_occupancy_hint,
        same_session,
        AdvanceAttempt(),
    )

    assert isinstance(decision, AppendTerminalLifecycleRecord)
    assert decision.receipt == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.SESSION_STREAM_CONFLICT,
        cycle=same_session.request.session,
    )


def test_kernel_makes_partial_idempotency_conflict_terminal() -> None:
    original = _command()
    partial_history, _, _ = _append_decision(
        LifecycleHistory(),
        original,
        AdvanceAttempt(),
    )
    conflicting = _command(session="2026-08-22")

    refused_history, _, refused = _append_decision(
        partial_history,
        conflicting,
        AdvanceAttempt(),
    )

    expected = AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=conflicting.request.session,
    )
    assert refused == expected
    assert decide_advance(
        refused_history, original, AdvanceAttempt()
    ) == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=original.request.session,
    )


@pytest.mark.parametrize(
    ("refusal", "reason"),
    [
        (
            InputRefusal(InputRefusalCode.INVALID_SESSION, _command().request.idempotency_key),
            AdvanceFailureReason.INVALID_SESSION,
        ),
        (
            InputRefusal(InputRefusalCode.INVALID_MODE, _command().request.idempotency_key),
            AdvanceFailureReason.INVALID_MODE,
        ),
        (
            InputRefusal(InputRefusalCode.INVALID_IDEMPOTENCY_KEY, None),
            AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
        ),
        (
            InputRefusal(
                InputRefusalCode.MISSING_UNIVERSE_INPUT,
                _command().request.idempotency_key,
            ),
            AdvanceFailureReason.MISSING_UNIVERSE_INPUT,
        ),
        (
            InputRefusal(
                InputRefusalCode.INVALID_UNIVERSE_INPUT,
                _command().request.idempotency_key,
            ),
            AdvanceFailureReason.INVALID_UNIVERSE_INPUT,
        ),
        (
            InputRefusal(
                InputRefusalCode.STALE_UNIVERSE_INPUT,
                _command().request.idempotency_key,
            ),
            AdvanceFailureReason.STALE_UNIVERSE_INPUT,
        ),
        (
            InputRefusal(
                InputRefusalCode.CONTRADICTORY_UNIVERSE_INPUT,
                _command().request.idempotency_key,
            ),
            AdvanceFailureReason.CONTRADICTORY_UNIVERSE_INPUT,
        ),
    ],
)
def test_kernel_bounds_and_replays_input_refusals(
    refusal: InputRefusal,
    reason: AdvanceFailureReason,
) -> None:
    refused_history, _, receipt = _append_decision(
        LifecycleHistory(),
        refusal,
        AdvanceAttempt(),
    )

    assert receipt == AdvanceReceipt.failed_closed(reason)
    assert isinstance(refused_history.refusals[-1].sequence, int)
    assert refused_history.refusals[-1].sequence == 1
    assert decide_advance(refused_history, refusal, AdvanceAttempt()) == receipt


def test_terminal_refusal_selection_distinguishes_keyed_and_unkeyed_history() -> None:
    valid_command = _command()
    valid_cycle = valid_command.request.session
    keyed_command = InputRefusal(
        InputRefusalCode.INVALID_SESSION,
        valid_command.request.idempotency_key,
        valid_cycle,
    )
    missing_command = InputRefusal(
        InputRefusalCode.INVALID_MODE,
        _command(key="missing-key").request.idempotency_key,
    )
    unkeyed_command = InputRefusal(
        InputRefusalCode.INVALID_IDEMPOTENCY_KEY,
        None,
        valid_cycle,
    )
    missing_unkeyed_command = InputRefusal(
        InputRefusalCode.INVALID_IDEMPOTENCY_KEY,
        None,
        _command(session="2026-08-22").request.session,
    )
    refusals = (
        DurableAdvanceRefusal(
            1,
            None,
            AdvanceFailureReason.INVALID_MODE,
        ),
        DurableAdvanceRefusal(
            2,
            keyed_command.idempotency_key,
            AdvanceFailureReason.INVALID_SESSION,
            valid_cycle,
        ),
        DurableAdvanceRefusal(
            3,
            None,
            AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
            valid_cycle,
        ),
    )

    assert decide_terminal_refusal(refusals, keyed_command) == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_SESSION,
        cycle=valid_cycle,
    )
    assert decide_terminal_refusal(refusals, valid_command) == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=valid_cycle,
    )
    assert decide_terminal_refusal(refusals, missing_command) is None
    assert decide_terminal_refusal(refusals, unkeyed_command) == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
        cycle=valid_cycle,
    )
    assert decide_terminal_refusal(refusals, missing_unkeyed_command) is None


def test_kernel_fails_closed_on_invalid_typed_history() -> None:
    command = _command()
    invalid_event = LifecycleEvent(
        command.request.stream_id,
        1,
        command.request,
        command.pinned_run_identity,
        LifecycleEventKind.PHASE_COMPLETED,
        LifecycleCheckpoint.equity(LifecyclePhase.RECONCILE_PRIOR_STATE),
    )
    invalid_history = LifecycleHistory(
        events=(invalid_event,),
        next_refusal_sequence=INVALID_HISTORY_REFUSAL_SEQUENCE,
    )

    decision = decide_advance(invalid_history, command, AdvanceAttempt())

    assert isinstance(decision, AppendTerminalLifecycleRecord)
    assert isinstance(decision.record, DurableAdvanceRefusal)
    assert decision.record.sequence == INVALID_HISTORY_REFUSAL_SEQUENCE
    assert decision.receipt == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=command.request.session,
    )
    with pytest.raises(InvalidLifecycleStateError, match="sequence is not contiguous"):
        reconstruct_lifecycle(invalid_history)


def test_kernel_rejects_duplicate_idempotency_keys_across_valid_streams() -> None:
    first = _first_event(_command())
    second = _first_event(_command(session="2026-08-22"))

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream changed pinned request facts",
    ):
        reconstruct_lifecycle(LifecycleHistory(events=(first, second)))


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("unsupported_configuration_version", "unsupported configuration_version"),
        ("invalid_stream_identity", "derived identity is invalid"),
        ("unsupported_later_phase", "unsupported later phases"),
        ("invalid_checkpoint", "checkpoint order is invalid"),
    ],
)
def test_kernel_rejects_invalid_typed_stream_contracts(case: str, message: str) -> None:
    command = _command()
    complete_history, _ = _complete(LifecycleHistory(), command)
    events = complete_history.events
    changed: tuple[LifecycleEvent, ...]
    if case == "unsupported_configuration_version":
        first = replace(
            events[0],
            pinned_run_identity=replace(events[0].pinned_run_identity, configuration_version=2),
        )
        changed = (first,)
    elif case == "invalid_stream_identity":
        changed = (replace(events[0], stream_id="f" * SHA256_HEX_LENGTH),)
    elif case == "unsupported_later_phase":
        changed = (*events, events[-1])
    else:
        assert case == "invalid_checkpoint"
        changed = (
            replace(
                events[0],
                event_kind=LifecycleEventKind.PHASE_COMPLETED,
                completed_phase=LifecycleCheckpoint.equity(LifecyclePhase.RECONCILE_PRIOR_STATE),
            ),
        )

    with pytest.raises(InvalidLifecycleStateError, match=message):
        reconstruct_lifecycle(LifecycleHistory(events=changed))


def test_kernel_replays_a_terminal_refusal_when_boundary_history_is_invalid() -> None:
    command = _command()
    refusal = DurableAdvanceRefusal(
        1,
        command.request.idempotency_key,
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        command.request.session,
    )

    decision = decide_invalid_history((refusal,), command)

    assert decision == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=command.request.session,
    )


def test_invalid_history_preserves_keyless_input_refusal_semantics() -> None:
    cycle = MarketSession(date(2026, 8, 21))
    command = InputRefusal(InputRefusalCode.INVALID_IDEMPOTENCY_KEY, None, cycle)

    decision = decide_invalid_history((), command, next_refusal_sequence=4)

    assert isinstance(decision, AppendTerminalLifecycleRecord)
    assert decision.record == DurableAdvanceRefusal(
        4,
        None,
        AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
        cycle,
    )
    assert decision.receipt == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
        cycle=cycle,
    )
    assert decide_terminal_refusal((decision.record,), command) == decision.receipt


def test_kernel_fails_closed_when_an_active_attempt_loses_its_stream() -> None:
    command = _command()

    decision = decide_advance(
        LifecycleHistory(),
        command,
        AdvanceAttempt(AdvanceRecovery.FRESH, 0),
    )

    assert isinstance(decision, AppendTerminalLifecycleRecord)
    assert isinstance(decision.record, DurableAdvanceRefusal)
    assert decision.record.idempotency_key == command.request.idempotency_key
    assert decision.receipt == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=command.request.session,
    )


def test_kernel_marks_progress_observed_without_a_sequence_as_resumed() -> None:
    command = _command()
    history, _, _ = _append_decision(LifecycleHistory(), command, AdvanceAttempt())

    decision = decide_advance(
        history,
        command,
        AdvanceAttempt(AdvanceRecovery.FRESH, None),
    )

    assert isinstance(decision, AppendLifecycleRecord)
    assert decision.attempt.recovery is AdvanceRecovery.RESUMED


def test_status_uses_the_kernel_transition_sequence() -> None:
    command = _command()
    history = LifecycleHistory()
    expected_phases = (
        LifecyclePhase.RECONCILE_PRIOR_STATE,
        LifecyclePhase.PIN_RUN_INPUTS,
        LifecyclePhase.SNAPSHOT_UNIVERSE,
        LifecyclePhase.CAPTURE_EVIDENCE,
        LifecyclePhase.SELECT_ATTENTION,
        None,
    )

    for expected_phase in expected_phases:
        history, _, _ = _append_decision(history, command, AdvanceAttempt())
        active_phase = derive_lifecycle_status(history).active_phase
        assert (None if active_phase is None else active_phase.phase) is expected_phase

    conflict = DurableAdvanceConflict(
        command.request.idempotency_key,
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
    )
    status = derive_lifecycle_status(history.append(conflict))
    assert status.durable_reason is AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT


def test_status_preserves_the_latest_universe_cycle_after_a_newer_stream_refusal() -> None:
    completed_command = _command(session="2026-08-21", key="completed-key")
    history, completed_receipt = _complete(LifecycleHistory(), completed_command)
    assert completed_receipt.universe_snapshot_id is not None
    active_command = _command(session="2026-08-22", key="refused-key")
    active_event = _first_event(active_command)
    refusal = DurableAdvanceRefusal(
        1,
        active_command.request.idempotency_key,
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
    )

    status = derive_lifecycle_status(
        LifecycleHistory(events=(*history.events, active_event), refusals=(refusal,))
    )

    assert status.liveness is LifecycleLiveness.FAILED_CLOSED
    assert status.last_completed_cycle is None
    assert status.universe_snapshot_cycle == completed_command.request.session
    assert status.universe_snapshot_id == completed_receipt.universe_snapshot_id
