"""Persist belief events and replace disposable graph projections in SQLite."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING, Self, TypeVar, assert_never

from agentic_investment_os.adapters.sqlite_lifecycle import (
    _BEGIN_IMMEDIATE_SQL,
    _BELIEF_GRAPH_PROJECTION_SCHEMA,
    _DROP_BELIEF_GRAPH_PROJECTION_SQL,
    _canonical_json,
    _connect_database,
    _DatabaseOpenMode,
    _prepare_database,
)
from agentic_investment_os.domain.lifecycle import is_sha256
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.memory.admission import (
    BeliefEvent,
    BeliefEvidenceReference,
    RecordRefusalCode,
    canonical_belief_event_payload,
    parse_belief_event,
    validate_belief_event,
    validate_belief_evidence,
)
from agentic_investment_os.memory.beliefs import (
    AppendBeliefRecord,
    BeliefEvidenceResolver,
    BeliefGraph,
    BeliefGraphQuery,
    BeliefGraphRefusal,
    BeliefGraphRefusalCode,
    BeliefHistory,
    BeliefLedgerEntry,
    BeliefPersistenceError,
    RecordDisposition,
    RecordReceipt,
    belief_graph_evidence_references,
    project_belief_graph,
)
from agentic_investment_os.memory.reducer import (
    belief_commitment_identity,
    decide_belief_record,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = ("SQLiteBeliefLedger",)

_T = TypeVar("_T")
_BELIEF_CHECKPOINT_FAILED = "SQLite belief checkpoint failed"
_BELIEF_RECORDED_AT_NOT_CANONICAL = "belief recorded_at must use canonical UTC format"
_INVALID_BELIEF_HISTORY = "invalid authoritative belief history"
_BELIEF_LEDGER_HEAD_COLUMNS = 3


class _InvalidBeliefHistoryError(ValueError):
    pass


class SQLiteBeliefLedger:
    """Atomically append validated belief events to the shared current database."""

    def __init__(self, database: Path) -> None:
        self._database = database
        _prepare_database(database, mode=_DatabaseOpenMode.CREATE_IF_MISSING)

    @classmethod
    def open_existing(cls, database: Path) -> Self:
        """Validate current belief storage without recreating a missing database."""
        instance = cls.__new__(cls)
        instance._database = database
        if database.exists():
            _prepare_database(database, mode=_DatabaseOpenMode.EXISTING_ONLY)
        return instance

    def record(
        self,
        event: BeliefEvent,
        evidence_resolver: BeliefEvidenceResolver,
        recorded_at: UtcInstant,
    ) -> RecordReceipt:
        """Return one durable append/replay receipt or a bounded refusal."""
        if type(recorded_at) is not UtcInstant:
            raise BeliefPersistenceError(_BELIEF_RECORDED_AT_NOT_CANONICAL)
        try:
            timestamp = recorded_at.isoformat()
        except InvalidUtcInstantError as error:
            raise BeliefPersistenceError(_BELIEF_RECORDED_AT_NOT_CANONICAL) from error
        if not validate_belief_event(event):
            return RecordReceipt.refused(RecordRefusalCode.INVALID_EVENT)

        def operation(connection: sqlite3.Connection) -> RecordReceipt:
            try:
                history = _load_belief_history(connection)
            except ValueError:
                return RecordReceipt.refused(RecordRefusalCode.INVALID_AUTHORITATIVE_HISTORY)
            decision = decide_belief_record(history, event, recorded_at)
            if (
                isinstance(decision, RecordReceipt)
                and decision.disposition is RecordDisposition.REFUSED
            ):
                return decision
            events = tuple(entry.event for entry in history.entries)
            if isinstance(decision, AppendBeliefRecord):
                events = (*events, event)
            references = _belief_evidence_references(events)
            artifacts = evidence_resolver.resolve_belief_evidence(references)
            if isinstance(artifacts, RecordRefusalCode):
                return RecordReceipt.refused(artifacts)
            refusal = validate_belief_evidence(events, artifacts)
            if refusal is not None:
                return RecordReceipt.refused(refusal)
            if isinstance(decision, AppendBeliefRecord):
                _append_belief_entry(connection, decision.entry, timestamp)
                return decision.receipt
            if isinstance(decision, RecordReceipt):
                return decision
            # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
            assert_never(decision)  # pragma: no cover

        return self._write(operation)

    def rebuild_graph(
        self,
        query: BeliefGraphQuery,
        evidence_resolver: BeliefEvidenceResolver,
    ) -> BeliefGraph | BeliefGraphRefusal:
        """Replace the disposable graph only after authoritative reconstruction."""

        def operation(connection: sqlite3.Connection) -> BeliefGraph | BeliefGraphRefusal:
            try:
                history = _load_belief_history(connection)
            except ValueError:
                return BeliefGraphRefusal(BeliefGraphRefusalCode.INVALID_AUTHORITATIVE_HISTORY)
            references = belief_graph_evidence_references(history, query)
            artifacts = evidence_resolver.resolve_belief_evidence(references)
            if isinstance(artifacts, RecordRefusalCode):
                return BeliefGraphRefusal(BeliefGraphRefusalCode.INVALID_EVIDENCE)
            try:
                graph = project_belief_graph(history, query, artifacts)
            except ValueError:
                return BeliefGraphRefusal(BeliefGraphRefusalCode.INVALID_EVIDENCE)
            _replace_belief_graph_projection(connection, graph)
            return graph

        return self._write(operation)

    def validate_history(
        self,
        event_ids: tuple[str, ...],
        evidence_resolver: BeliefEvidenceResolver,
    ) -> None:
        """Revalidate authoritative events, evidence, and lifecycle references."""

        def operation(connection: sqlite3.Connection) -> None:
            try:
                history = _load_belief_history(connection)
            except ValueError as error:
                raise BeliefPersistenceError(_INVALID_BELIEF_HISTORY) from error
            events = tuple(entry.event for entry in history.entries)
            if not events:
                if event_ids:
                    raise BeliefPersistenceError(_INVALID_BELIEF_HISTORY)
                return
            artifacts = evidence_resolver.resolve_belief_evidence(
                _belief_evidence_references(events)
            )
            if isinstance(artifacts, RecordRefusalCode):
                raise BeliefPersistenceError(_INVALID_BELIEF_HISTORY)
            if validate_belief_evidence(events, artifacts) is not None:
                raise BeliefPersistenceError(_INVALID_BELIEF_HISTORY)
            stored_ids = frozenset(event.event_id for event in events)
            if any(event_id not in stored_ids for event_id in event_ids):
                raise BeliefPersistenceError(_INVALID_BELIEF_HISTORY)

        self._write(operation)

    def _connect(self) -> sqlite3.Connection:
        return _connect_database(self._database, mode=_DatabaseOpenMode.EXISTING_ONLY)

    def _write(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_BEGIN_IMMEDIATE_SQL)
                return operation(connection)
        except sqlite3.Error as error:
            raise BeliefPersistenceError(_BELIEF_CHECKPOINT_FAILED) from error


def _load_belief_history(connection: sqlite3.Connection) -> BeliefHistory:
    rows = connection.execute(
        """
        SELECT ledger_position, event_id, belief_id, event_json,
               projection_identity, recorded_at
        FROM belief_events ORDER BY ledger_position
        """
    ).fetchall()
    commitment_rows = connection.execute(
        """
        SELECT ledger_position, projection_identity, commitment_identity
        FROM belief_ledger_commitments ORDER BY ledger_position
        """
    ).fetchall()
    head_rows = connection.execute(
        """
        SELECT singleton, ledger_position, commitment_identity
        FROM belief_ledger_head
        """
    ).fetchall()
    if len(rows) != len(commitment_rows):
        raise _InvalidBeliefHistoryError(_INVALID_BELIEF_HISTORY)
    entries: list[BeliefLedgerEntry] = []
    previous_commitment: str | None = None
    for row, commitment_row in zip(rows, commitment_rows, strict=True):
        ledger_position = _integer(row[0])
        event_id = _text(row[1])
        belief_id = _text(row[2])
        encoded = _text(row[3])
        try:
            decoded: object = json.loads(encoded)
        except (ValueError, RecursionError) as error:
            raise _InvalidBeliefHistoryError(_INVALID_BELIEF_HISTORY) from error
        event = parse_belief_event(decoded)
        if (
            event is None
            or not validate_belief_event(event)
            or _canonical_json(canonical_belief_event_payload(event)) != encoded
            or event.event_id != event_id
            or event.belief_id != belief_id
        ):
            raise _InvalidBeliefHistoryError(_INVALID_BELIEF_HISTORY)
        projection_identity = _hash(row[4])
        recorded_at = _canonical_timestamp(row[5])
        committed_position = _integer(commitment_row[0])
        committed_projection = _hash(commitment_row[1])
        commitment_identity = _hash(commitment_row[2])
        if event.transaction_at.value > recorded_at.value:
            raise _InvalidBeliefHistoryError(_INVALID_BELIEF_HISTORY)
        if (
            committed_position != ledger_position
            or committed_projection != projection_identity
            or commitment_identity
            != belief_commitment_identity(
                previous_commitment,
                ledger_position,
                projection_identity,
            )
        ):
            raise _InvalidBeliefHistoryError(_INVALID_BELIEF_HISTORY)
        previous_commitment = commitment_identity
        entries.append(BeliefLedgerEntry(ledger_position, event, recorded_at, projection_identity))
    _validate_belief_ledger_head(head_rows, len(entries), previous_commitment)
    return BeliefHistory(tuple(entries))


def _belief_evidence_references(
    events: tuple[BeliefEvent, ...],
) -> tuple[BeliefEvidenceReference, ...]:
    references = {
        reference.artifact_id: reference for event in events for reference in event.evidence
    }
    return tuple(references[key] for key in sorted(references))


def _append_belief_entry(
    connection: sqlite3.Connection,
    entry: BeliefLedgerEntry,
    recorded_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO belief_events (
            ledger_position, event_id, belief_id, event_json,
            projection_identity, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            entry.ledger_position,
            entry.event.event_id,
            entry.event.belief_id,
            _canonical_json(canonical_belief_event_payload(entry.event)),
            entry.projection_identity,
            recorded_at,
        ),
    )
    previous_row = connection.execute(
        """
        SELECT commitment_identity FROM belief_ledger_commitments
        WHERE ledger_position = ?
        """,
        (entry.ledger_position - 1,),
    ).fetchone()
    previous_commitment = None if previous_row is None else _hash(previous_row[0])
    commitment_identity = belief_commitment_identity(
        previous_commitment,
        entry.ledger_position,
        entry.projection_identity,
    )
    connection.execute(
        """
        INSERT INTO belief_ledger_commitments (
            ledger_position, projection_identity, commitment_identity
        ) VALUES (?, ?, ?)
        """,
        (
            entry.ledger_position,
            entry.projection_identity,
            commitment_identity,
        ),
    )
    if entry.ledger_position == 1:
        connection.execute(
            """
            INSERT INTO belief_ledger_head (
                singleton, ledger_position, commitment_identity
            ) VALUES (1, ?, ?)
            """,
            (entry.ledger_position, commitment_identity),
        )
        return
    updated = connection.execute(
        """
        UPDATE belief_ledger_head
        SET ledger_position = ?, commitment_identity = ?
        WHERE singleton = 1
          AND ledger_position = ?
          AND commitment_identity = ?
        """,
        (
            entry.ledger_position,
            commitment_identity,
            entry.ledger_position - 1,
            previous_commitment,
        ),
    )
    if updated.rowcount != 1:
        raise sqlite3.IntegrityError(_INVALID_BELIEF_HISTORY)


def _replace_belief_graph_projection(
    connection: sqlite3.Connection,
    graph: BeliefGraph,
) -> None:
    connection.execute(_DROP_BELIEF_GRAPH_PROJECTION_SQL)
    connection.execute(_BELIEF_GRAPH_PROJECTION_SCHEMA)
    connection.execute(
        """
        INSERT INTO belief_graph_projection (
            singleton, projection_identity, query_json, graph_json
        ) VALUES (1, ?, ?, ?)
        """,
        (
            graph.content_hash,
            _canonical_json(graph.query.to_payload()),
            _canonical_json(graph.to_payload()),
        ),
    )


def _text(value: object) -> str:
    if type(value) is not str or not value:
        raise _InvalidBeliefHistoryError(_INVALID_BELIEF_HISTORY)
    return value


def _validate_belief_ledger_head(
    rows: list[tuple[object, ...]],
    ledger_position: int,
    commitment_identity: str | None,
) -> None:
    if ledger_position == 0:
        if rows:
            raise _InvalidBeliefHistoryError(_INVALID_BELIEF_HISTORY)
        return
    if len(rows) != 1:
        raise _InvalidBeliefHistoryError(_INVALID_BELIEF_HISTORY)
    row = rows[0]
    if (
        len(row) != _BELIEF_LEDGER_HEAD_COLUMNS
        or _integer(row[0]) != 1
        or _integer(row[1]) != ledger_position
        or _hash(row[2]) != commitment_identity
    ):
        raise _InvalidBeliefHistoryError(_INVALID_BELIEF_HISTORY)


def _integer(value: object) -> int:
    if type(value) is not int:
        raise _InvalidBeliefHistoryError(_INVALID_BELIEF_HISTORY)
    return value


def _hash(value: object) -> str:
    if not is_sha256(value):
        raise _InvalidBeliefHistoryError(_INVALID_BELIEF_HISTORY)
    return value


def _canonical_timestamp(value: object) -> UtcInstant:
    try:
        return UtcInstant.parse(value)
    except InvalidUtcInstantError as error:
        raise _InvalidBeliefHistoryError(_INVALID_BELIEF_HISTORY) from error
