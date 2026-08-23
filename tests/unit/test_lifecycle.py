from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from agentic_investment_os.application.lifecycle import Advance
from agentic_investment_os.domain.lifecycle import (
    AdvanceAttempt,
    AdvanceCommand,
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRecovery,
    AdvanceRequest,
    AppendLifecycleRecord,
    InputRefusal,
    InputRefusalCode,
    InvalidLifecycleStateError,
    LifecycleCommand,
    LifecycleDecision,
    LifecycleEvent,
    LifecycleEventKind,
    LifecyclePhase,
    PinnedRunIdentity,
)

if TYPE_CHECKING:
    from agentic_investment_os.domain.temporal import UtcInstant

SHA256_HEX_LENGTH = 64
PINNED_SEQUENCE = 2


@dataclass(frozen=True)
class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 21, 22, 0, tzinfo=UTC)


@dataclass
class ConcurrentCompletionLedger:
    completion_point: str
    receipt: AdvanceReceipt
    steps: int = 0

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        _recorded_at: UtcInstant,
    ) -> LifecycleDecision:
        assert isinstance(command, AdvanceCommand)
        if self.completion_point == "start" and self.steps == 0:
            return self.receipt
        if self.completion_point == "reconcile_failure" and self.steps == 1:
            return AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
        if self.completion_point == "reconcile" and self.steps == 1:
            return self.receipt
        if self.completion_point == "pin_failure" and self.steps == PINNED_SEQUENCE:
            return AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
        if self.completion_point == "pin_observed" and self.steps == PINNED_SEQUENCE:
            return self.receipt

        event_kind, phase = (
            (LifecycleEventKind.ADVANCE_REQUESTED, None)
            if self.steps == 0
            else (
                LifecycleEventKind.PHASE_COMPLETED,
                LifecyclePhase.RECONCILE_PRIOR_STATE,
            )
        )
        next_attempt = (
            attempt
            if self.completion_point == "incomplete_reconcile" and self.steps == 1
            else AdvanceAttempt(AdvanceRecovery.FRESH, self.steps)
        )
        decision = AppendLifecycleRecord(
            LifecycleEvent(
                command.request.stream_id,
                self.steps,
                command.request,
                command.pinned_run_identity,
                event_kind,
                phase,
            ),
            next_attempt,
        )
        self.steps += 1
        return decision


def test_advance_request_validates_the_complete_boundary() -> None:
    request = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key="session-2026-08-21",
    )

    assert isinstance(request, AdvanceRequest)
    assert request.session.isoformat() == "2026-08-21"
    assert request.mode.value == "champion"
    assert request.idempotency_key.value == "session-2026-08-21"
    assert request.stream_id == "b24b0e025ab67f2594db49e7f5e1c7cfe8170645fe5b9defe068e8c715d7a9e5"
    identity = PinnedRunIdentity.create(
        request,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    assert identity.run_id == "e148a27c171f24e3f53a415fd06b16eea872c7dce9e8d0378f41d6031fe2df55"

    invalid_cases = (
        (
            {"session": "21-08-2026", "mode": "champion", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_SESSION,
        ),
        (
            {"session": "2026-08-21", "mode": "research-lab", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_MODE,
        ),
        (
            {"session": "2026-08-21", "mode": "champion", "idempotency_key": "contains space"},
            InputRefusalCode.INVALID_IDEMPOTENCY_KEY,
        ),
        (
            {"session": None, "mode": "champion", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_SESSION,
        ),
        (
            {"session": "20260821", "mode": "champion", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_SESSION,
        ),
    )

    for values, expected_code in invalid_cases:
        refusal = AdvanceRequest.parse(**values)
        assert isinstance(refusal, InputRefusal)
        assert refusal.code is expected_code


def test_advance_receipt_rejects_incomplete_success_and_failure_shapes() -> None:
    identity = PinnedRunIdentity(
        run_id="b" * SHA256_HEX_LENGTH,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )

    with pytest.raises(ValueError, match="advanced receipt requires completed recovery facts"):
        AdvanceReceipt(
            AdvanceDisposition.ADVANCED,
            LifecyclePhase.PIN_RUN_INPUTS,
            identity,
            None,
        )
    with pytest.raises(ValueError, match="advanced receipt requires completed recovery facts"):
        AdvanceReceipt(
            AdvanceDisposition.ADVANCED,
            None,
            identity,
            None,
            AdvanceRecovery.FRESH,
        )
    with pytest.raises(ValueError, match="failed receipt requires one bounded reason"):
        AdvanceReceipt(
            AdvanceDisposition.FAILED_CLOSED,
            None,
            None,
            None,
        )
    with pytest.raises(ValueError, match="failed receipt requires one bounded reason"):
        AdvanceReceipt(
            AdvanceDisposition.FAILED_CLOSED,
            None,
            None,
            AdvanceFailureReason.INVALID_DURABLE_STATE,
            AdvanceRecovery.RESUMED,
        )

    assert (
        AdvanceReceipt.advanced(identity, AdvanceRecovery.FRESH).recovery is AdvanceRecovery.FRESH
    )


@pytest.mark.parametrize("completion_point", ["start", "reconcile"])
def test_advance_returns_a_concurrent_checkpoint_receipt(completion_point: str) -> None:
    receipt = AdvanceReceipt(
        AdvanceDisposition.ADVANCED,
        completed_phase=LifecyclePhase.PIN_RUN_INPUTS,
        pinned_run_identity=PinnedRunIdentity(
            run_id="b" * SHA256_HEX_LENGTH,
            configuration_version=1,
            configuration_hash="a" * SHA256_HEX_LENGTH,
        ),
        failure_reason=None,
        recovery=AdvanceRecovery.PREVIOUSLY_COMPLETED,
    )
    capability = Advance(
        ledger=ConcurrentCompletionLedger(completion_point, receipt),
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
        clock=FixedClock(),
    )

    observed = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert observed.disposition is receipt.disposition
    assert observed.completed_phase is receipt.completed_phase
    assert observed.pinned_run_identity is receipt.pinned_run_identity
    assert observed.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED


@pytest.mark.parametrize("failure_point", ["reconcile_failure", "pin_failure"])
def test_advance_returns_a_durable_checkpoint_failure(failure_point: str) -> None:
    identity = PinnedRunIdentity(
        run_id="b" * SHA256_HEX_LENGTH,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    capability = Advance(
        ledger=ConcurrentCompletionLedger(
            failure_point,
            AdvanceReceipt.advanced(identity, AdvanceRecovery.PREVIOUSLY_COMPLETED),
        ),
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
        clock=FixedClock(),
    )

    observed = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert observed == AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)


def test_advance_reports_a_checkpoint_completed_during_pinning() -> None:
    identity = PinnedRunIdentity(
        run_id="b" * SHA256_HEX_LENGTH,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    capability = Advance(
        ledger=ConcurrentCompletionLedger(
            "pin_observed",
            AdvanceReceipt.advanced(identity, AdvanceRecovery.PREVIOUSLY_COMPLETED),
        ),
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
        clock=FixedClock(),
    )

    observed = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert observed == AdvanceReceipt.advanced(
        identity,
        AdvanceRecovery.PREVIOUSLY_COMPLETED,
    )


def test_advance_rejects_an_incomplete_checkpoint_result() -> None:
    identity = PinnedRunIdentity(
        run_id="b" * SHA256_HEX_LENGTH,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    capability = Advance(
        ledger=ConcurrentCompletionLedger(
            "incomplete_reconcile",
            AdvanceReceipt.advanced(identity, AdvanceRecovery.PREVIOUSLY_COMPLETED),
        ),
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
        clock=FixedClock(),
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle ledger returned an incomplete checkpoint result",
    ):
        capability(
            session="2026-08-21",
            mode="champion",
            idempotency_key="concurrent-request",
        )
