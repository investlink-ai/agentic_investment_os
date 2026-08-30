"""Publish immutable Champion decisions and signed Balanced packets atomically."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, TypeGuard

from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    MarketSession,
    parse_decision_cycle_identity,
)
from agentic_investment_os.domain.lifecycle import (
    DecisionCheckpoint,
    DecisionCheckpointReference,
    InvalidLifecycleStateError,
    LifecyclePersistenceError,
    NoActionReason,
    is_sha256,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.portfolio.publication import (
    DecisionPacket,
    DecisionPacketValidityWindow,
    DecisionPacketVerifier,
    DecisionPacketWindowSource,
    DecisionPublicationClock,
    DecisionPublicationRefusal,
    DecisionPublicationRefusalReason,
    DecisionPublicationResult,
    PacketNoActionReason,
    parse_champion_decision_record,
    parse_decision_packet,
    validate_decision_publication,
)

if TYPE_CHECKING:
    from pathlib import Path

    from agentic_investment_os.portfolio.shadows import PortfolioCycleResultLedger

__all__ = ("SQLiteDecisionPublicationLedger",)

_CHECKPOINT_FAILED = "SQLite decision publication checkpoint failed"
_INVALID_HISTORY = "durable decision publication is invalid"
_INVALID_CLOCK = "decision publication clock must return a timezone-aware UTC instant"


@dataclass(frozen=True, slots=True)
class SQLiteDecisionPublicationLedger:
    """Store one complete decision publication per Market Session in one row."""

    database: Path
    verifier: DecisionPacketVerifier
    portfolio_ledger: PortfolioCycleResultLedger
    benchmark_identity: EquityInstrumentIdentity
    decision_window_source: DecisionPacketWindowSource
    clock: DecisionPublicationClock

    @classmethod
    def open_existing(  # noqa: PLR0913 - reconstruction names each authority dependency.
        cls,
        database: Path,
        *,
        verifier: DecisionPacketVerifier,
        portfolio_ledger: PortfolioCycleResultLedger,
        benchmark_identity: EquityInstrumentIdentity,
        decision_window_source: DecisionPacketWindowSource,
        clock: DecisionPublicationClock,
    ) -> Self:
        """Open the already startup-validated runtime database."""
        return cls(
            database,
            verifier,
            portfolio_ledger,
            benchmark_identity,
            decision_window_source,
            clock,
        )

    def record_publication(
        self,
        run_id: str,
        result: DecisionPublicationResult,
        recorded_at: UtcInstant,
    ) -> DecisionCheckpoint | DecisionPublicationRefusal:
        """Atomically insert, replay, or timestamp a refused decision publication."""
        if (
            not is_sha256(run_id)
            or type(result) is not DecisionPublicationResult
            or type(recorded_at) is not UtcInstant
            or result.decision_record.run_id != run_id
            or result.decision_record.evidence_cutoff.value > recorded_at.value
        ):
            raise InvalidLifecycleStateError(_INVALID_HISTORY)
        packet = result.packet
        try:
            with closing(sqlite3.connect(self.database, timeout=5.0)) as connection, connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 5000")
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT run_id, cycle_identity, decision_record_id, decision_record_json,
                           packet_id, packet_expires_at, packet_json, no_action_reason,
                           recorded_at
                    FROM decision_publications
                    WHERE run_id = ? OR cycle_identity = ?
                    """,
                    (run_id, _canonical_json(result.decision_record.cycle.to_payload())),
                ).fetchone()
                if existing is not None:
                    stored_result, stored_reference = self._validated_row(existing)
                    if not _same_publication_intent(stored_result, result):
                        raise InvalidLifecycleStateError(_INVALID_HISTORY)
                    return stored_reference.checkpoint
                refusal = self._fresh_publication_refusal(run_id, result, recorded_at)
                if refusal is not None:
                    return DecisionPublicationRefusal(refusal, _clock_instant(self.clock))
                decision_json = _canonical_json(result.decision_record.to_payload())
                packet_json = None if packet is None else _canonical_json(packet.to_payload())
                visibility_at = _clock_instant(self.clock)
                if visibility_at.value < recorded_at.value or not _packet_window_is_valid(
                    packet,
                    visibility_at,
                    self.decision_window_source,
                ):
                    return DecisionPublicationRefusal(
                        DecisionPublicationRefusalReason.INVALID_VALIDITY_WINDOW,
                        visibility_at,
                    )
                checkpoint = _checkpoint(result, visibility_at)
                connection.execute(
                    """
                    INSERT INTO decision_publications (
                        run_id, cycle_identity, decision_record_id, decision_record_json,
                        packet_id, packet_expires_at, packet_json, no_action_reason,
                        recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        _canonical_json(result.decision_record.cycle.to_payload()),
                        result.decision_record.decision_record_id,
                        decision_json,
                        None if packet is None else packet.packet_id,
                        None if packet is None else packet.expires_at.isoformat(),
                        packet_json,
                        (
                            None
                            if result.no_action_reason is None
                            else result.no_action_reason.value
                        ),
                        visibility_at.isoformat(),
                    ),
                )
                return checkpoint
        except InvalidLifecycleStateError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise LifecyclePersistenceError(_CHECKPOINT_FAILED) from error

    def _fresh_publication_refusal(
        self,
        run_id: str,
        result: DecisionPublicationResult,
        recorded_at: UtcInstant,
    ) -> DecisionPublicationRefusalReason | None:
        try:
            cycle_result, portfolio_reference = (
                self.portfolio_ledger.load_cycle_with_reference_for_run(run_id)
            )
        except InvalidLifecycleStateError:
            return DecisionPublicationRefusalReason.INVALID_PORTFOLIO
        if recorded_at.value < portfolio_reference.recorded_at.value:
            return DecisionPublicationRefusalReason.INVALID_VALIDITY_WINDOW
        if not validate_decision_publication(
            result,
            cycle_result,
            benchmark_identity=self.benchmark_identity,
        ):
            return DecisionPublicationRefusalReason.INVALID_PORTFOLIO
        packet = result.packet
        if packet is not None and (
            parse_decision_packet(packet.to_payload(), verifier=self.verifier) != packet
        ):
            return DecisionPublicationRefusalReason.SIGNING_FAILED
        if not _packet_window_is_valid(packet, recorded_at, self.decision_window_source):
            return DecisionPublicationRefusalReason.INVALID_VALIDITY_WINDOW
        return None

    def replay_publication(
        self,
        run_id: str,
        cycle: MarketSession,
    ) -> DecisionCheckpoint | None:
        """Return an exact prior publication after revalidating all authoritative material."""
        if not is_sha256(run_id) or type(cycle) is not MarketSession:
            raise InvalidLifecycleStateError(_INVALID_HISTORY)
        try:
            with closing(sqlite3.connect(self.database, timeout=5.0)) as connection:
                row = connection.execute(
                    """
                    SELECT run_id, cycle_identity, decision_record_id, decision_record_json,
                           packet_id, packet_expires_at, packet_json, no_action_reason,
                           recorded_at
                    FROM decision_publications
                    WHERE run_id = ? OR cycle_identity = ?
                    """,
                    (run_id, _canonical_json(cycle.to_payload())),
                ).fetchone()
                if row is None:
                    return None
                reference = self._validated_row(row)[1]
                if reference.run_id != run_id or reference.cycle != cycle:
                    raise InvalidLifecycleStateError(_INVALID_HISTORY)
                return reference.checkpoint
        except InvalidLifecycleStateError:
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as error:
            raise InvalidLifecycleStateError(_INVALID_HISTORY) from error

    def validate_history(self, references: tuple[DecisionCheckpointReference, ...]) -> None:
        """Reparse and semantically validate every publication and lifecycle reference."""
        try:
            with closing(sqlite3.connect(self.database, timeout=5.0)) as connection:
                rows = connection.execute(
                    """
                    SELECT run_id, cycle_identity, decision_record_id, decision_record_json,
                           packet_id, packet_expires_at, packet_json, no_action_reason,
                           recorded_at
                    FROM decision_publications ORDER BY cycle_identity
                    """
                ).fetchall()
                available = [self._validated_row(row)[1] for row in rows]
                for reference in references:
                    try:
                        available.remove(reference)
                    except ValueError as error:
                        raise InvalidLifecycleStateError(_INVALID_HISTORY) from error
        except InvalidLifecycleStateError:
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as error:
            raise InvalidLifecycleStateError(_INVALID_HISTORY) from error

    def validate_reference(self, reference: DecisionCheckpointReference) -> None:
        """Validate the exact publication named by one lifecycle stream."""
        try:
            with closing(sqlite3.connect(self.database, timeout=5.0)) as connection:
                row = connection.execute(
                    """
                    SELECT run_id, cycle_identity, decision_record_id, decision_record_json,
                           packet_id, packet_expires_at, packet_json, no_action_reason,
                           recorded_at
                    FROM decision_publications WHERE run_id = ?
                    """,
                    (reference.run_id,),
                ).fetchone()
                if row is None or self._validated_row(row)[1] != reference:
                    raise InvalidLifecycleStateError(_INVALID_HISTORY)
        except InvalidLifecycleStateError:
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as error:
            raise InvalidLifecycleStateError(_INVALID_HISTORY) from error

    def _validated_row(
        self,
        row: tuple[object, ...],
    ) -> tuple[DecisionPublicationResult, DecisionCheckpointReference]:
        run_id = _hash(row[0])
        cycle = _cycle(row[1])
        decision_json = _text(row[3])
        decision = parse_champion_decision_record(json.loads(decision_json))
        packet_json = None if row[6] is None else _text(row[6])
        packet = (
            None
            if packet_json is None
            else parse_decision_packet(json.loads(packet_json), verifier=self.verifier)
        )
        no_action_reason = None if row[7] is None else PacketNoActionReason(_text(row[7]))
        recorded_at = _instant(row[8])
        if decision is None:
            raise InvalidLifecycleStateError(_INVALID_HISTORY)
        result = DecisionPublicationResult(decision, packet, no_action_reason)
        checkpoint = _checkpoint(result, recorded_at)
        cycle_result, portfolio_reference = self.portfolio_ledger.load_cycle_with_reference_for_run(
            run_id
        )
        if (
            decision.run_id != run_id
            or decision.cycle != cycle
            or decision.evidence_cutoff.value > recorded_at.value
            or portfolio_reference.recorded_at.value > recorded_at.value
            or row[2] != decision.decision_record_id
            or decision_json != _canonical_json(decision.to_payload())
            or row[4] != (None if packet is None else packet.packet_id)
            or row[5] != (None if packet is None else packet.expires_at.isoformat())
            or packet_json != (None if packet is None else _canonical_json(packet.to_payload()))
            or row[7] != (None if no_action_reason is None else no_action_reason.value)
            or not _packet_window_is_valid(packet, recorded_at, self.decision_window_source)
            or not validate_decision_publication(
                result,
                cycle_result,
                benchmark_identity=self.benchmark_identity,
            )
        ):
            raise InvalidLifecycleStateError(_INVALID_HISTORY)
        return result, DecisionCheckpointReference(run_id, cycle, checkpoint)


def _checkpoint(
    result: DecisionPublicationResult,
    recorded_at: UtcInstant,
) -> DecisionCheckpoint:
    packet = result.packet
    if result.decision_record.evidence_cutoff.value > recorded_at.value or (
        packet is not None and packet.issued_at.value > recorded_at.value
    ):
        raise InvalidLifecycleStateError(_INVALID_HISTORY)
    return DecisionCheckpoint(
        result.decision_record.decision_record_id,
        None if packet is None else packet.packet_id,
        None if packet is None else packet.expires_at,
        recorded_at,
        (
            NoActionReason.NO_AUTHORIZED_ADJUSTMENTS
            if result.no_action_reason is PacketNoActionReason.NO_AUTHORIZED_ADJUSTMENTS
            else None
        ),
    )


def _same_publication_intent(
    stored: DecisionPublicationResult,
    candidate: DecisionPublicationResult,
) -> bool:
    return (
        stored.decision_record,
        stored.no_action_reason,
        _packet_authorization_intent(stored.packet),
    ) == (
        candidate.decision_record,
        candidate.no_action_reason,
        _packet_authorization_intent(candidate.packet),
    )


def _packet_authorization_intent(packet: DecisionPacket | None) -> tuple[object, ...] | None:
    if packet is None:
        return None
    return (
        packet.run_id,
        packet.cycle,
        packet.account_scope,
        packet.decision_record_id,
        packet.portfolio_policy_id,
        packet.cost_policy_id,
        packet.maximum_gross_weight,
        packet.maximum_name_weight,
        packet.maximum_sector_weight,
        packet.maximum_common_cause_weight,
        packet.maximum_correlation_cluster_weight,
        packet.maximum_fraction_of_median_dollar_volume,
        packet.instructions,
        packet.authority_scope,
        packet.risk_profile,
        packet.asset_class,
        packet.quantity_unit,
        packet.order_policy,
        packet.leverage_allowed,
    )


def _packet_window_is_valid(
    packet: DecisionPacket | None,
    recorded_at: UtcInstant,
    decision_window_source: DecisionPacketWindowSource,
) -> bool:
    if packet is None:
        return True
    return decision_window_source.allows_publication(
        DecisionPacketValidityWindow(packet.cycle, packet.issued_at, packet.expires_at),
        recorded_at,
    )


def _clock_instant(clock: DecisionPublicationClock) -> UtcInstant:
    try:
        return UtcInstant.from_datetime(clock.now())
    except InvalidUtcInstantError as error:
        raise LifecyclePersistenceError(_INVALID_CLOCK) from error


def _cycle(value: object) -> MarketSession:
    try:
        decoded: object = json.loads(_text(value))
    except json.JSONDecodeError as error:
        raise InvalidLifecycleStateError(_INVALID_HISTORY) from error
    cycle = parse_decision_cycle_identity(decoded)
    if type(cycle) is not MarketSession or _canonical_json(cycle.to_payload()) != value:
        raise InvalidLifecycleStateError(_INVALID_HISTORY)
    return cycle


def _instant(value: object) -> UtcInstant:
    try:
        return UtcInstant.parse(value)
    except InvalidUtcInstantError as error:
        raise InvalidLifecycleStateError(_INVALID_HISTORY) from error


def _hash(value: object) -> str:
    text = _text(value)
    if not is_sha256(text):
        raise InvalidLifecycleStateError(_INVALID_HISTORY)
    return text


def _text(value: object) -> str:
    if _is_non_empty_exact_text(value):
        return value
    raise InvalidLifecycleStateError(_INVALID_HISTORY)


def _is_non_empty_exact_text(value: object) -> TypeGuard[str]:
    return all((type(value) is str, bool(value)))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
