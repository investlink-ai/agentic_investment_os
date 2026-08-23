"""Advance and report a Market Session through durable lifecycle capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, assert_never

from agentic_investment_os.domain.identity import (
    AssetClass,
    CryptoDecisionWindow,
    MarketSession,
    parse_decision_cycle_identity,
)
from agentic_investment_os.domain.lifecycle import (
    AdvanceAttempt,
    AdvanceCommand,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRequest,
    AppendLifecycleRecord,
    AppendTerminalLifecycleRecord,
    InputRefusal,
    InputRefusalCode,
    InvalidLifecycleStateError,
    LifecycleCommand,
    LifecycleLedger,
    LifecyclePersistenceError,
    LifecycleStatus,
    LifecycleStatusProjection,
    PinnedRunIdentity,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.domain.universe import (
    EquityUniversePolicy,
    UniverseInputIdentity,
    UniverseInputs,
    UniverseInputSource,
    UniverseRefusal,
    UniverseRefusalCode,
    UniverseSnapshot,
    build_universe_snapshot,
)

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ("Advance", "Clock", "Status")


_INCOMPLETE_CHECKPOINT_RESULT = "lifecycle ledger returned an incomplete checkpoint result"
_CLOCK_INVALID = "lifecycle clock must return a timezone-aware instant representable in UTC"
_UNIVERSE_SOURCE_INVALID = "universe source returned a noncanonical absolute instant"


class Clock(Protocol):
    """Supply an aware timestamp at the composition boundary."""

    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class Advance:
    """Advance or resume one validated Decision Cycle through a universe snapshot."""

    ledger: LifecycleLedger
    configuration_version: int
    configuration_hash: str
    universe_source: UniverseInputSource
    enabled_asset_classes: tuple[AssetClass, ...]
    universe_policy: EquityUniversePolicy
    clock: Clock

    def __call__(
        self,
        *,
        cycle: object,
        mode: object,
        idempotency_key: object,
    ) -> AdvanceReceipt:
        parsed_cycle = parse_decision_cycle_identity(cycle)
        if type(parsed_cycle) is CryptoDecisionWindow:
            return AdvanceReceipt.failed_closed(
                AdvanceFailureReason.UNSUPPORTED_CYCLE,
                cycle=parsed_cycle,
            )
        if type(parsed_cycle) is not MarketSession:
            return AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_SESSION)
        parsed = AdvanceRequest.parse(
            session=parsed_cycle,
            mode=mode,
            idempotency_key=idempotency_key,
        )
        try:
            recorded_at = UtcInstant.from_datetime(self.clock.now())
        except InvalidUtcInstantError as error:
            raise LifecyclePersistenceError(_CLOCK_INVALID) from error
        command = self._prepare_command(parsed, recorded_at)
        attempt = AdvanceAttempt()
        while True:
            decision = self.ledger.advance_step(command, attempt, recorded_at)
            if isinstance(decision, AdvanceReceipt):
                return decision
            if isinstance(decision, AppendTerminalLifecycleRecord):
                return decision.receipt
            if isinstance(decision, AppendLifecycleRecord):
                if decision.attempt.last_sequence is None or (
                    attempt.last_sequence is not None
                    and decision.attempt.last_sequence <= attempt.last_sequence
                ):
                    raise InvalidLifecycleStateError(_INCOMPLETE_CHECKPOINT_RESULT)
                attempt = decision.attempt
                continue
            # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
            assert_never(decision)  # pragma: no cover

    def _prepare_command(
        self,
        parsed: AdvanceRequest | InputRefusal,
        recorded_at: UtcInstant,
    ) -> LifecycleCommand:
        if isinstance(parsed, InputRefusal):
            return parsed
        if isinstance(parsed, AdvanceRequest):
            loaded = self.universe_source.load()
            if isinstance(loaded, UniverseRefusal):
                return InputRefusal(
                    _input_refusal_code(loaded.code),
                    parsed.idempotency_key,
                    parsed.session,
                )
            if isinstance(loaded, UniverseInputs):
                universe_identity = UniverseInputIdentity.from_inputs(
                    loaded,
                    self.universe_policy,
                )
                if isinstance(universe_identity, UniverseRefusal):
                    raise LifecyclePersistenceError(_UNIVERSE_SOURCE_INVALID)
                identity = PinnedRunIdentity.create(
                    parsed,
                    configuration_version=self.configuration_version,
                    configuration_hash=self.configuration_hash,
                    universe_inputs=universe_identity,
                )
                snapshot = build_universe_snapshot(
                    identity.run_id,
                    parsed.session,
                    loaded,
                    self.universe_policy,
                    enabled_asset_classes=self.enabled_asset_classes,
                    recorded_at=recorded_at,
                )
                if isinstance(snapshot, UniverseRefusal):
                    return InputRefusal(
                        _input_refusal_code(snapshot.code),
                        parsed.idempotency_key,
                        parsed.session,
                    )
                if isinstance(snapshot, UniverseSnapshot):
                    return AdvanceCommand(parsed, identity, snapshot)
                # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
                assert_never(snapshot)  # pragma: no cover
            # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
            assert_never(loaded)  # pragma: no cover
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(parsed)  # pragma: no cover


def _input_refusal_code(code: UniverseRefusalCode) -> InputRefusalCode:
    if code is UniverseRefusalCode.MISSING_INPUT:
        return InputRefusalCode.MISSING_UNIVERSE_INPUT
    if code is UniverseRefusalCode.INVALID_INPUT:
        return InputRefusalCode.INVALID_UNIVERSE_INPUT
    if code is UniverseRefusalCode.STALE_INPUT:
        return InputRefusalCode.STALE_UNIVERSE_INPUT
    if code is UniverseRefusalCode.CONTRADICTORY_INPUT:
        return InputRefusalCode.CONTRADICTORY_UNIVERSE_INPUT
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(code)  # pragma: no cover


@dataclass(frozen=True, slots=True)
class Status:
    """Rebuild and return lifecycle status without advancing authoritative history."""

    projection: LifecycleStatusProjection

    def __call__(self) -> LifecycleStatus:
        return self.projection.rebuild_status()
