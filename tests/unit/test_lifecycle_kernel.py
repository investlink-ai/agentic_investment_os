from __future__ import annotations

import pytest

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
    InputRefusal,
    InputRefusalCode,
    InvalidLifecycleStateError,
    LifecycleEvent,
    LifecycleEventKind,
    LifecycleHistory,
    LifecyclePhase,
    PinnedRunIdentity,
    decide_advance,
    decide_invalid_history,
    decide_terminal_refusal,
    derive_lifecycle_status,
    reconstruct_lifecycle,
)

SHA256_HEX_LENGTH = 64
INVALID_HISTORY_REFUSAL_SEQUENCE = 5


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
    return AdvanceCommand(
        request,
        PinnedRunIdentity.create(
            request,
            configuration_version=1,
            configuration_hash=configuration_hash,
        ),
    )


def _append_decision(
    history: LifecycleHistory,
    command: AdvanceCommand | InputRefusal,
    attempt: AdvanceAttempt,
) -> tuple[LifecycleHistory, AdvanceAttempt, AdvanceReceipt | None]:
    decision = decide_advance(history, command, attempt)
    assert not isinstance(decision, AdvanceReceipt)
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
        history = history.append(decision.record)
        if isinstance(decision, AppendTerminalLifecycleRecord):
            return history, decision.receipt
        current_attempt = decision.attempt


def test_kernel_assigns_stage_one_events_and_fresh_recovery() -> None:
    request = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key="kernel-fresh",
    )
    assert isinstance(request, AdvanceRequest)
    identity = PinnedRunIdentity.create(
        request,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    command = AdvanceCommand(request, identity)
    history = LifecycleHistory()
    attempt = AdvanceAttempt()

    expected = (
        (0, LifecycleEventKind.ADVANCE_REQUESTED),
        (1, LifecycleEventKind.PHASE_COMPLETED),
        (2, LifecycleEventKind.RUN_INPUTS_PINNED),
    )
    completed: AppendTerminalLifecycleRecord | None = None
    for sequence, event_kind in expected:
        decision = decide_advance(history, command, attempt)
        assert not isinstance(decision, AdvanceReceipt)
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


def test_kernel_resumes_partial_history_and_replays_completion() -> None:
    command = _command()
    history, _, _ = _append_decision(LifecycleHistory(), command, AdvanceAttempt())

    completed_history, resumed = _complete(history, command)
    replay = decide_advance(completed_history, command, AdvanceAttempt())

    assert resumed == AdvanceReceipt.advanced(
        command.pinned_run_identity,
        AdvanceRecovery.RESUMED,
    )
    assert replay == AdvanceReceipt.advanced(
        command.pinned_run_identity,
        AdvanceRecovery.PREVIOUSLY_COMPLETED,
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

    expected_conflict = AdvanceReceipt.failed_closed(AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT)
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
        AdvanceFailureReason.SESSION_STREAM_CONFLICT
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
        AdvanceFailureReason.SESSION_STREAM_CONFLICT
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

    expected = AdvanceReceipt.failed_closed(AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT)
    assert refused == expected
    assert decide_advance(refused_history, original, AdvanceAttempt()) == expected


@pytest.mark.parametrize(
    ("refusal", "reason"),
    [
        (
            InputRefusal(InputRefusalCode.INVALID_SESSION, _command().request.idempotency_key),
            AdvanceFailureReason.INVALID_SESSION,
        ),
        (
            InputRefusal(InputRefusalCode.INVALID_IDEMPOTENCY_KEY, None),
            AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
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
    keyed_command = InputRefusal(
        InputRefusalCode.INVALID_SESSION,
        valid_command.request.idempotency_key,
    )
    missing_command = InputRefusal(
        InputRefusalCode.INVALID_MODE,
        _command(key="missing-key").request.idempotency_key,
    )
    unkeyed_command = InputRefusal(InputRefusalCode.INVALID_IDEMPOTENCY_KEY, None)
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
        ),
        DurableAdvanceRefusal(
            3,
            None,
            AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
        ),
    )

    assert decide_terminal_refusal(refusals, keyed_command) == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_SESSION
    )
    assert decide_terminal_refusal(refusals, valid_command) == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_SESSION
    )
    assert decide_terminal_refusal(refusals, missing_command) is None
    assert decide_terminal_refusal(refusals, unkeyed_command) == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY
    )


def test_kernel_fails_closed_on_invalid_typed_history() -> None:
    command = _command()
    invalid_event = LifecycleEvent(
        command.request.stream_id,
        1,
        command.request,
        command.pinned_run_identity,
        LifecycleEventKind.PHASE_COMPLETED,
        LifecyclePhase.RECONCILE_PRIOR_STATE,
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
        AdvanceFailureReason.INVALID_DURABLE_STATE
    )
    with pytest.raises(InvalidLifecycleStateError, match="sequence is not contiguous"):
        reconstruct_lifecycle(invalid_history)


def test_kernel_replays_a_terminal_refusal_when_boundary_history_is_invalid() -> None:
    command = _command()
    refusal = DurableAdvanceRefusal(
        1,
        command.request.idempotency_key,
        AdvanceFailureReason.INVALID_DURABLE_STATE,
    )

    decision = decide_invalid_history((refusal,), command)

    assert decision == AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)


def test_invalid_history_preserves_keyless_input_refusal_semantics() -> None:
    command = InputRefusal(InputRefusalCode.INVALID_IDEMPOTENCY_KEY, None)

    decision = decide_invalid_history((), command, next_refusal_sequence=4)

    assert isinstance(decision, AppendTerminalLifecycleRecord)
    assert decision.record == DurableAdvanceRefusal(
        4,
        None,
        AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
    )
    assert decision.receipt == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY
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
        AdvanceFailureReason.INVALID_DURABLE_STATE
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
        None,
    )

    for expected_phase in expected_phases:
        history, _, _ = _append_decision(history, command, AdvanceAttempt())
        assert derive_lifecycle_status(history).active_phase is expected_phase

    conflict = DurableAdvanceConflict(
        command.request.idempotency_key,
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
    )
    status = derive_lifecycle_status(history.append(conflict))
    assert status.durable_reason is AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT
