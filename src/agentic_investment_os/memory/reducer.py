"""Select deterministic append-only belief-history decisions."""

from __future__ import annotations

import hashlib
import json

from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.memory.admission import (
    BeliefEvent,
    BeliefStatus,
    RecordRefusalCode,
    validate_belief_event,
)
from agentic_investment_os.memory.beliefs import (
    AppendBeliefRecord,
    BeliefHistory,
    BeliefLedgerEntry,
    BeliefRecordDecision,
    RecordDisposition,
    RecordReceipt,
)

__all__ = (
    "belief_commitment_identity",
    "belief_projection_identity",
    "decide_belief_record",
    "validate_belief_history_integrity",
    "validate_belief_transition_history",
)

_ALLOWED_TRANSITIONS = {
    BeliefStatus.ACTIVE: frozenset(BeliefStatus),
    BeliefStatus.WEAKENED: frozenset(BeliefStatus),
    BeliefStatus.CONTRADICTED: frozenset(BeliefStatus),
    BeliefStatus.DORMANT: frozenset(BeliefStatus),
    BeliefStatus.EXPIRED: frozenset(
        {BeliefStatus.ACTIVE, BeliefStatus.SUPERSEDED, BeliefStatus.ARCHIVED}
    ),
    BeliefStatus.REFUTED: frozenset({BeliefStatus.SUPERSEDED, BeliefStatus.ARCHIVED}),
    BeliefStatus.SUPERSEDED: frozenset({BeliefStatus.ARCHIVED}),
    BeliefStatus.ARCHIVED: frozenset(),
}
_PROJECTION_SCHEMA_VERSION = 1
_COMMITMENT_SCHEMA_VERSION = 1


def decide_belief_record(
    history: BeliefHistory,
    event: BeliefEvent,
    recorded_at: UtcInstant,
) -> BeliefRecordDecision:
    """Select one idempotent append, replay, conflict, or transition refusal."""
    history.__post_init__()
    if not validate_belief_event(event) or type(recorded_at) is not UtcInstant:
        return RecordReceipt.refused(RecordRefusalCode.INVALID_EVENT)
    for entry in history.entries:
        if entry.event.event_id != event.event_id:
            continue
        if entry.event != event:
            return RecordReceipt.refused(RecordRefusalCode.EVENT_IDENTITY_CONFLICT)
        return RecordReceipt(
            RecordDisposition.REPLAYED,
            event.event_id,
            entry.ledger_position,
            entry.projection_identity,
            None,
        )
    if event.transaction_at.value > recorded_at.value:
        return RecordReceipt.refused(RecordRefusalCode.INVALID_EVENT)
    if not _transition_is_valid(history.entries, event):
        return RecordReceipt.refused(RecordRefusalCode.INVALID_TRANSITION)
    position = len(history.entries) + 1
    previous_projection = None if not history.entries else history.entries[-1].projection_identity
    entry = BeliefLedgerEntry(
        position,
        event,
        recorded_at,
        belief_projection_identity(previous_projection, event, recorded_at, position),
    )
    return AppendBeliefRecord(
        entry,
        RecordReceipt(
            RecordDisposition.APPENDED,
            event.event_id,
            position,
            entry.projection_identity,
            None,
        ),
    )


def validate_belief_transition_history(entries: tuple[BeliefLedgerEntry, ...]) -> bool:
    """Return whether every event follows its belief stream's current head and status rules."""
    validated: list[BeliefLedgerEntry] = []
    for entry in entries:
        if not _transition_is_valid(tuple(validated), entry.event):
            return False
        validated.append(entry)
    return True


def validate_belief_history_integrity(entries: tuple[BeliefLedgerEntry, ...]) -> bool:
    """Return whether positions, identities, time, and transitions form one intact ledger."""
    if type(entries) is not tuple:
        return False
    previous_projection: str | None = None
    event_ids: set[str] = set()
    for expected_position, entry in enumerate(entries, start=1):
        if type(entry) is not BeliefLedgerEntry:
            return False
        if (
            entry.ledger_position != expected_position
            or entry.event.event_id in event_ids
            or entry.projection_identity
            != belief_projection_identity(
                previous_projection,
                entry.event,
                entry.recorded_at,
                expected_position,
            )
        ):
            return False
        event_ids.add(entry.event.event_id)
        previous_projection = entry.projection_identity
    return validate_belief_transition_history(entries)


def belief_projection_identity(
    previous: str | None,
    event: BeliefEvent,
    recorded_at: UtcInstant,
    ledger_position: int,
) -> str:
    """Derive the append-chain identity returned in a durable Record receipt."""
    material = {
        "schema_version": _PROJECTION_SCHEMA_VERSION,
        "previous_projection_identity": previous,
        "ledger_position": ledger_position,
        "recorded_at": recorded_at.isoformat(),
        "event_content_hash": event.content_hash,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def belief_commitment_identity(
    previous: str | None,
    ledger_position: int,
    projection_identity: str,
) -> str:
    """Commit independently to the durable ledger length and projection chain."""
    material = {
        "schema_version": _COMMITMENT_SCHEMA_VERSION,
        "previous_commitment_identity": previous,
        "ledger_position": ledger_position,
        "projection_identity": projection_identity,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _transition_is_valid(
    entries: tuple[BeliefLedgerEntry, ...],
    event: BeliefEvent,
) -> bool:
    belief_entries = tuple(entry for entry in entries if entry.event.belief_id == event.belief_id)
    if not belief_entries:
        return (
            event.status is BeliefStatus.ACTIVE
            and event.transition_from_event_id is None
            and event.supersedes_event_id is None
        )
    previous = belief_entries[-1].event
    if (
        event.transition_from_event_id != previous.event_id
        or event.subject != previous.subject
        or event.claim_kind is not previous.claim_kind
        or event.transaction_at.value < previous.transaction_at.value
        or event.status not in _ALLOWED_TRANSITIONS[previous.status]
    ):
        return False
    if event.supersedes_event_id is None:
        return True
    return any(entry.event.event_id == event.supersedes_event_id for entry in belief_entries)
