"""Publish immutable Champion decisions and signed Balanced packets atomically."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from agentic_investment_os.domain.identity import MarketSession, parse_decision_cycle_identity
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
    DecisionPacketVerifier,
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


@dataclass(frozen=True, slots=True)
class SQLiteDecisionPublicationLedger:
    """Store one complete decision publication per Market Session in one row."""

    database: Path
    verifier: DecisionPacketVerifier
    portfolio_ledger: PortfolioCycleResultLedger

    @classmethod
    def open_existing(
        cls,
        database: Path,
        *,
        verifier: DecisionPacketVerifier,
        portfolio_ledger: PortfolioCycleResultLedger,
    ) -> Self:
        """Open the already startup-validated runtime database."""
        return cls(database, verifier, portfolio_ledger)

    def record_publication(
        self,
        run_id: str,
        result: DecisionPublicationResult,
        recorded_at: UtcInstant,
    ) -> DecisionCheckpoint:
        """Atomically insert or exactly replay a decision and optional complete packet."""
        if (
            not is_sha256(run_id)
            or type(result) is not DecisionPublicationResult
            or type(recorded_at) is not UtcInstant
            or result.decision_record.run_id != run_id
            or result.decision_record.evidence_cutoff.value > recorded_at.value
        ):
            raise InvalidLifecycleStateError(_INVALID_HISTORY)
        cycle_result = self.portfolio_ledger.load_cycle_for_run(run_id)
        if not validate_decision_publication(result, cycle_result):
            raise InvalidLifecycleStateError(_INVALID_HISTORY)
        packet = result.packet
        if packet is not None and (
            parse_decision_packet(packet.to_payload(), verifier=self.verifier) != packet
        ):
            raise InvalidLifecycleStateError(_INVALID_HISTORY)
        decision_json = _canonical_json(result.decision_record.to_payload())
        packet_json = None if packet is None else _canonical_json(packet.to_payload())
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
                if result.packet is not None and (
                    result.packet.issued_at.value > recorded_at.value
                    or result.packet.expires_at.value <= recorded_at.value
                ):
                    raise InvalidLifecycleStateError(_INVALID_HISTORY)
                checkpoint = _checkpoint(result, recorded_at)
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
                        recorded_at.isoformat(),
                    ),
                )
                return checkpoint
        except InvalidLifecycleStateError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise LifecyclePersistenceError(_CHECKPOINT_FAILED) from error

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
        if (
            decision.run_id != run_id
            or decision.cycle != cycle
            or row[2] != decision.decision_record_id
            or decision_json != _canonical_json(decision.to_payload())
            or row[4] != (None if packet is None else packet.packet_id)
            or row[5] != (None if packet is None else packet.expires_at.isoformat())
            or packet_json != (None if packet is None else _canonical_json(packet.to_payload()))
            or row[7] != (None if no_action_reason is None else no_action_reason.value)
            or not validate_decision_publication(
                result,
                self.portfolio_ledger.load_cycle_for_run(run_id),
            )
        ):
            raise InvalidLifecycleStateError(_INVALID_HISTORY)
        return result, DecisionCheckpointReference(run_id, cycle, checkpoint)


def _checkpoint(
    result: DecisionPublicationResult,
    recorded_at: UtcInstant,
) -> DecisionCheckpoint:
    packet = result.packet
    if packet is not None and packet.issued_at.value > recorded_at.value:
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
    if (
        stored.decision_record != candidate.decision_record
        or stored.no_action_reason != candidate.no_action_reason
        or (stored.packet is None) != (candidate.packet is None)
    ):
        return False
    if stored.packet is None or candidate.packet is None:
        return True
    left = stored.packet
    right = candidate.packet
    return (
        left.run_id,
        left.cycle,
        left.account_scope,
        left.decision_record_id,
        left.portfolio_policy_id,
        left.cost_policy_id,
        left.maximum_gross_weight,
        left.maximum_name_weight,
        left.maximum_sector_weight,
        left.maximum_common_cause_weight,
        left.maximum_correlation_cluster_weight,
        left.instructions,
        left.authority_scope,
        left.risk_profile,
        left.asset_class,
        left.quantity_unit,
        left.order_policy,
        left.leverage_allowed,
    ) == (
        right.run_id,
        right.cycle,
        right.account_scope,
        right.decision_record_id,
        right.portfolio_policy_id,
        right.cost_policy_id,
        right.maximum_gross_weight,
        right.maximum_name_weight,
        right.maximum_sector_weight,
        right.maximum_common_cause_weight,
        right.maximum_correlation_cluster_weight,
        right.instructions,
        right.authority_scope,
        right.risk_profile,
        right.asset_class,
        right.quantity_unit,
        right.order_policy,
        right.leverage_allowed,
    )


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
    if type(value) is not str or not value:
        raise InvalidLifecycleStateError(_INVALID_HISTORY)
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
