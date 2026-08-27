from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import subprocess
import sys
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Literal, Never, cast, override

import pytest
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from agentic_investment_os.adapters.sqlite_lifecycle import (
    RuntimeRootRefusal,
    SQLiteLifecycleLedger,
    prepare_runtime_database,
)
from agentic_investment_os.application.lifecycle import Advance
from agentic_investment_os.domain.attention import (
    AttentionArtifact,
    AttentionRefusalReason,
    DossierRequest,
    DossierSelectionKind,
    HoldingRefreshDisposition,
)
from agentic_investment_os.domain.governance import (
    ACTIVE_CONSTITUTION,
    ConstitutionUse,
)
from agentic_investment_os.domain.identity import (
    CryptoDecisionWindow,
    EquityInstrumentIdentity,
    MarketSession,
)
from agentic_investment_os.domain.lifecycle import (
    AdvanceAttempt,
    AdvanceCommand,
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRecovery,
    AdvanceRequest,
    AppendLifecycleRecord,
    AppendTerminalLifecycleRecord,
    DurableAdvanceConflict,
    DurableAdvanceRefusal,
    EvidenceCaptureCheckpoint,
    IdempotencyKey,
    InputRefusal,
    InputRefusalCode,
    InvalidLifecycleStateError,
    LifecycleCommand,
    LifecycleDecision,
    LifecycleEvent,
    LifecycleEventKind,
    LifecyclePersistenceError,
    LifecyclePhase,
    LifecycleStatus,
    NoActionReason,
    PerformAttentionSelection,
    PerformDossierBuild,
    PerformEvidenceCapture,
    PerformMemoryUpdate,
    PerformResearch,
    PinnedRunIdentity,
    ResearchCheckpoint,
    ResearchRefusal,
)
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.domain.universe import UniverseInputs
from agentic_investment_os.entrypoints.configuration import (
    ConfigurationRefusal,
    ConfigurationRefusalCode,
    ConfigurationSource,
)
from agentic_investment_os.entrypoints.lifecycle import SystemClock, configure_advance
from agentic_investment_os.evidence.capture import EvidencePersistenceError
from agentic_investment_os.memory.beliefs import (
    BeliefGraphRefusal,
    BeliefGraphRefusalCode,
)
from tests._attention import attention_artifact
from tests._evidence import (
    evidence_capture_checkpoint,
    materialized_evidence_capture_checkpoint,
)
from tests._governance import RecordedSessionEligibility
from tests._production_research import (
    ValidProductionModel,
    production_recorded_evidence,
    production_recorded_official_evidence,
)
from tests._universe import (
    advance_command,
    pinned_run_identity,
    recorded_universe,
    runtime_configuration,
    typed_research_policy,
    universe_policy,
    universe_snapshot,
)

if TYPE_CHECKING:
    from agentic_investment_os.application.memory import Record
    from agentic_investment_os.evidence.capture import EvidenceVault

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SHA256_HEX_LENGTH = 64
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PINNED_EVENT_COUNT = 9
ATTENTION_REFUSAL_EVENT_COUNT = 5
ATTENTION_PUBLISHED_EVENT_COUNT = 6
RECEIPT_FIELD_COUNT = 4
BASE_EVIDENCE_ARTIFACT_COUNT = 2
COMPLETE_EVIDENCE_ARTIFACT_COUNT = 7
RECORDED_AT = datetime(2026, 8, 21, 22, 0, tzinfo=UTC)
DOSSIER_CHECKPOINT = ResearchCheckpoint(("1" * SHA256_HEX_LENGTH,))
RESEARCH_CHECKPOINT = ResearchCheckpoint(("2" * SHA256_HEX_LENGTH,))
MEMORY_CHECKPOINT = ResearchCheckpoint(("3" * SHA256_HEX_LENGTH,))
SQLiteValue = str | bytes | int | float | None
NONCANONICAL_CHECKPOINT_SQL = (
    "UPDATE lifecycle_events SET completed_phase = ' ' || completed_phase WHERE sequence = 1"
)
VALID_EVIDENCE_IDS_JSON = json.dumps(["a" * SHA256_HEX_LENGTH], separators=(",", ":"))
DUPLICATE_EVIDENCE_IDS_JSON = json.dumps(
    ["a" * SHA256_HEX_LENGTH, "a" * SHA256_HEX_LENGTH],
    separators=(",", ":"),
)
EVIDENCE_ON_WRONG_EVENT_SQL = (
    f"UPDATE lifecycle_events SET evidence_artifact_ids = '{VALID_EVIDENCE_IDS_JSON}', "  # noqa: S608
    "evidence_refusal_ids = '[]' WHERE sequence = 0"
)
EVIDENCE_ARTIFACT_ON_WRONG_EVENT_SQL = (
    f"UPDATE lifecycle_events SET evidence_artifact_ids = '{VALID_EVIDENCE_IDS_JSON}' "  # noqa: S608
    "WHERE sequence = 0"
)
EVIDENCE_REFUSAL_ON_WRONG_EVENT_SQL = (
    f"UPDATE lifecycle_events SET evidence_refusal_ids = '{VALID_EVIDENCE_IDS_JSON}' "  # noqa: S608
    "WHERE sequence = 0"
)
EVIDENCE_POLICY_ON_WRONG_EVENT_SQL = (
    f"UPDATE lifecycle_events SET evidence_policy_id = '{'a' * SHA256_HEX_LENGTH}' "  # noqa: S608
    "WHERE sequence = 0"
)
MISSING_CAPTURED_EVIDENCE_SQL = (
    "UPDATE lifecycle_events SET evidence_artifact_ids = '[]' "
    "WHERE event_kind = 'evidence_captured'"
)
REFUSAL_ON_CAPTURED_EVIDENCE_SQL = (
    f"UPDATE lifecycle_events SET evidence_refusal_ids = '{VALID_EVIDENCE_IDS_JSON}' "  # noqa: S608
    "WHERE event_kind = 'evidence_captured'"
)
DUPLICATE_EVIDENCE_IDENTIFIERS_SQL = (
    f"UPDATE lifecycle_events SET evidence_artifact_ids = '{DUPLICATE_EVIDENCE_IDS_JSON}' "  # noqa: S608
    "WHERE event_kind = 'evidence_captured'"
)
MALFORMED_EVIDENCE_IDENTIFIERS_SQL = (
    "UPDATE lifecycle_events SET evidence_artifact_ids = '{' WHERE event_kind = 'evidence_captured'"
)
NON_LIST_EVIDENCE_IDENTIFIERS_SQL = (
    "UPDATE lifecycle_events SET evidence_artifact_ids = '{}' "
    "WHERE event_kind = 'evidence_captured'"
)
INVALID_EVIDENCE_IDENTIFIER_SQL = (
    "UPDATE lifecycle_events SET evidence_artifact_ids = '[\"invalid\"]' "
    "WHERE event_kind = 'evidence_captured'"
)
NONCANONICAL_EVIDENCE_IDENTIFIERS_SQL = (
    f"UPDATE lifecycle_events SET evidence_artifact_ids = '[ \"{'a' * SHA256_HEX_LENGTH}\"]' "  # noqa: S608
    "WHERE event_kind = 'evidence_captured'"
)
MALFORMED_RESEARCH_CHECKPOINT_SQL = (
    "UPDATE lifecycle_events SET research_checkpoint = '{' WHERE event_kind = 'dossiers_built'"
)
INVALID_RESEARCH_CHECKPOINT_SQL = (
    "UPDATE lifecycle_events SET research_checkpoint = '{}' WHERE event_kind = 'dossiers_built'"
)
NO_ACTION_ON_WRONG_RESEARCH_EVENT_SQL = (
    "UPDATE lifecycle_events SET no_action_reason = 'cio_abstained' "
    "WHERE event_kind = 'dossiers_built'"
)
UNKNOWN_NO_ACTION_REASON_SQL = (
    "UPDATE lifecycle_events SET no_action_reason = 'unknown' WHERE event_kind = 'memory_updated'"
)
SIMULATED_INVALID_PRODUCTION_EVIDENCE = "simulated invalid production evidence"


def _cycle_payload(value: str) -> object:
    try:
        trading_date = date.fromisoformat(value)
    except ValueError:
        return value
    return MarketSession(trading_date).to_payload()


CORRUPTIONS = {
    "invalid_request": (
        ("UPDATE lifecycle_events SET mode = 'invalid'",),
        "invalid request values in lifecycle ledger",
    ),
    "invalid_cycle_json": (
        ("UPDATE lifecycle_events SET cycle_identity = '{'",),
        "invalid request values in lifecycle ledger",
    ),
    "non_text_cycle_identity": (
        ("UPDATE lifecycle_events SET cycle_identity = 3",),
        "invalid cycle_identity in lifecycle ledger",
    ),
    "non_equity_cycle": (
        (
            (
                "UPDATE lifecycle_events SET cycle_identity = "
                '\'{"asset_class":"crypto_spot","cycle_type":"crypto_decision_window",'
                '"payload":{"ends_at":"2026-08-23T00:00:00+00:00",'
                '"starts_at":"2026-08-22T00:00:00+00:00"},'
                '"payload_schema_version":1,"schema_version":1}\''
            ),
        ),
        "invalid request values in lifecycle ledger",
    ),
    "noncanonical_cycle": (
        ("UPDATE lifecycle_events SET cycle_identity = ' ' || cycle_identity",),
        "invalid request values in lifecycle ledger",
    ),
    "too_many_events": (
        (
            """
            INSERT INTO lifecycle_events
            SELECT stream_id, 3, idempotency_key, cycle_identity, mode,
                   configuration_version, configuration_hash,
                   research_policy_hash,
                   constitution_version, constitution_hash, run_id,
                   data_regime, evidence_cutoff, instrument_snapshot_hash,
                   position_snapshot_hash, eligibility_policy_hash,
                   event_kind, completed_phase, universe_snapshot_id,
                   universe_snapshot, evidence_policy_id,
                   evidence_artifact_ids, evidence_refusal_ids,
                   attention_artifact_id, attention_artifact,
                   research_checkpoint, no_action_reason,
                   event_envelope, recorded_at
            FROM lifecycle_events WHERE sequence = 2
            """,
        ),
        "lifecycle stream checkpoint order is invalid",
    ),
    "sequence_gap": (
        (
            "DELETE FROM lifecycle_events WHERE sequence = 2",
            "UPDATE lifecycle_events SET sequence = 2 WHERE sequence = 1",
        ),
        "lifecycle stream checkpoint order is invalid",
    ),
    "changed_invariant": (
        (
            (
                "UPDATE lifecycle_events SET cycle_identity = "
                '\'{"asset_class":"us_equity","cycle_type":"market_session",'
                '"payload":{"trading_date":"2026-08-22"},'
                '"payload_schema_version":1,"schema_version":1}\' WHERE sequence = 1'
            ),
        ),
        "lifecycle stream checkpoint order is invalid",
    ),
    "wrong_checkpoint": (
        ("UPDATE lifecycle_events SET event_kind = 'advance_requested' WHERE sequence = 1",),
        "lifecycle stream checkpoint order is invalid",
    ),
    "research_checkpoint_on_wrong_event": (
        ("UPDATE lifecycle_events SET research_checkpoint = '{}' WHERE sequence = 0",),
        "lifecycle stream checkpoint order is invalid",
    ),
    "malformed_research_checkpoint": (
        (MALFORMED_RESEARCH_CHECKPOINT_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "invalid_research_checkpoint": (
        (INVALID_RESEARCH_CHECKPOINT_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "no_action_on_wrong_research_event": (
        (NO_ACTION_ON_WRONG_RESEARCH_EVENT_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "unknown_no_action_reason": (
        (UNKNOWN_NO_ACTION_REASON_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "changed_event_envelope": (
        ("UPDATE lifecycle_events SET event_envelope = '{}' WHERE sequence = 1",),
        "lifecycle stream checkpoint order is invalid",
    ),
    "noncanonical_checkpoint_envelope": (
        (NONCANONICAL_CHECKPOINT_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "non_text_phase": (
        ("UPDATE lifecycle_events SET completed_phase = 3 WHERE sequence = 1",),
        "invalid completed_phase in lifecycle ledger",
    ),
    "non_integer_version": (
        ("UPDATE lifecycle_events SET configuration_version = 'invalid'",),
        "invalid configuration_version in lifecycle ledger",
    ),
    "invalid_constitution_version": (
        ("UPDATE lifecycle_events SET constitution_version = 0",),
        "lifecycle stream checkpoint order is invalid",
    ),
    "non_integer_sequence": (
        ("UPDATE lifecycle_events SET sequence = 'invalid'",),
        "invalid sequence in lifecycle ledger",
    ),
    "non_text_hash": (
        ("UPDATE lifecycle_events SET configuration_hash = 3",),
        "invalid configuration_hash in lifecycle ledger",
    ),
    "non_text_research_policy_hash": (
        ("UPDATE lifecycle_events SET research_policy_hash = 3",),
        "invalid research_policy_hash in lifecycle ledger",
    ),
    "non_text_instrument_snapshot_hash": (
        ("UPDATE lifecycle_events SET instrument_snapshot_hash = 3",),
        "invalid instrument_snapshot_hash in lifecycle ledger",
    ),
    "non_text_position_snapshot_hash": (
        ("UPDATE lifecycle_events SET position_snapshot_hash = 3",),
        "invalid position_snapshot_hash in lifecycle ledger",
    ),
    "non_text_eligibility_policy_hash": (
        ("UPDATE lifecycle_events SET eligibility_policy_hash = 3",),
        "invalid eligibility_policy_hash in lifecycle ledger",
    ),
    "invalid_hash": (
        ("UPDATE lifecycle_events SET configuration_hash = 'invalid'",),
        "invalid configuration_hash in lifecycle ledger",
    ),
    "invalid_run_id": (
        ("UPDATE lifecycle_events SET run_id = 'invalid'",),
        "invalid run_id in lifecycle ledger",
    ),
    "forged_run_id": (
        (
            """UPDATE lifecycle_events SET run_id =
            'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'""",
        ),
        "lifecycle stream checkpoint order is invalid",
    ),
    "forged_stream_id": (
        (
            """UPDATE lifecycle_events SET stream_id =
            'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'""",
        ),
        "lifecycle stream checkpoint order is invalid",
    ),
    "unsupported_version": (
        ("UPDATE lifecycle_events SET configuration_version = 2",),
        "lifecycle stream checkpoint order is invalid",
    ),
    "empty_stream": (
        ("UPDATE lifecycle_events SET stream_id = ''",),
        "invalid stream_id in lifecycle ledger",
    ),
    "non_text_stream": (
        ("UPDATE lifecycle_events SET stream_id = 3",),
        "invalid stream_id in lifecycle ledger",
    ),
    "non_text_event_kind": (
        ("UPDATE lifecycle_events SET event_kind = 3 WHERE sequence = 1",),
        "invalid event_kind in lifecycle ledger",
    ),
    "unknown_event_kind": (
        ("UPDATE lifecycle_events SET event_kind = 'unknown' WHERE sequence = 1",),
        "lifecycle stream checkpoint order is invalid",
    ),
    "unknown_phase": (
        ("UPDATE lifecycle_events SET completed_phase = 'Unknown' WHERE sequence = 1",),
        "lifecycle stream checkpoint order is invalid",
    ),
    "non_text_timestamp": (
        ("UPDATE lifecycle_events SET recorded_at = 3",),
        "invalid recorded_at in lifecycle ledger",
    ),
    "invalid_timestamp": (
        ("UPDATE lifecycle_events SET recorded_at = 'invalid'",),
        "recorded_at must use canonical UTC format",
    ),
    "naive_timestamp": (
        ("UPDATE lifecycle_events SET recorded_at = '2026-08-21T22:00:00'",),
        "recorded_at must use canonical UTC format",
    ),
    "noncanonical_offset_timestamp": (
        ("UPDATE lifecycle_events SET recorded_at = '2026-08-21T18:00:00.000000-04:00'",),
        "recorded_at must use canonical UTC format",
    ),
    "out_of_range_timestamp": (
        ("UPDATE lifecycle_events SET recorded_at = '0001-01-01T00:00:00.000000+14:00'",),
        "recorded_at must use canonical UTC format",
    ),
    "non_text_evidence_cutoff": (
        ("UPDATE lifecycle_events SET evidence_cutoff = 3",),
        "invalid evidence_cutoff in lifecycle ledger",
    ),
    "invalid_evidence_cutoff": (
        ("UPDATE lifecycle_events SET evidence_cutoff = 'invalid'",),
        "evidence_cutoff must use canonical UTC format",
    ),
    "naive_evidence_cutoff": (
        ("UPDATE lifecycle_events SET evidence_cutoff = '2026-08-21T20:00:00'",),
        "evidence_cutoff must use canonical UTC format",
    ),
    "noncanonical_offset_evidence_cutoff": (
        ("UPDATE lifecycle_events SET evidence_cutoff = '2026-08-21T16:00:00.000000-04:00'",),
        "evidence_cutoff must use canonical UTC format",
    ),
    "out_of_range_evidence_cutoff": (
        ("UPDATE lifecycle_events SET evidence_cutoff = '0001-01-01T00:00:00.000000+14:00'",),
        "evidence_cutoff must use canonical UTC format",
    ),
    "future_evidence_cutoff": (
        ("UPDATE lifecycle_events SET evidence_cutoff = '2030-08-21T20:00:00.000000+00:00'",),
        "evidence_cutoff cannot be later than recorded_at",
    ),
    "missing_snapshot_identity": (
        (
            (
                "UPDATE lifecycle_events SET universe_snapshot_id = NULL "
                "WHERE event_kind = 'universe_snapshotted'"
            ),
        ),
        "invalid universe_snapshot_id in lifecycle ledger",
    ),
    "snapshot_payload_on_published_event": (
        (
            (
                "UPDATE lifecycle_events SET universe_snapshot = '{}' "
                "WHERE event_kind = 'universe_snapshotted'"
            ),
        ),
        "lifecycle stream checkpoint order is invalid",
    ),
    "missing_prepared_snapshot_identity": (
        (
            (
                "UPDATE lifecycle_events SET universe_snapshot_id = NULL "
                "WHERE event_kind = 'run_inputs_pinned'"
            ),
        ),
        "invalid universe_snapshot_id in lifecycle ledger",
    ),
    "snapshot_reference_on_wrong_event": (
        (
            (
                "UPDATE lifecycle_events SET universe_snapshot_id = "
                "'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff' "
                "WHERE event_kind = 'advance_requested'"
            ),
        ),
        "lifecycle stream checkpoint order is invalid",
    ),
    "non_text_snapshot_reference_on_wrong_event": (
        (
            (
                "UPDATE lifecycle_events SET universe_snapshot_id = 3 "
                "WHERE event_kind = 'advance_requested'"
            ),
        ),
        "invalid universe_snapshot_id in lifecycle ledger",
    ),
    "non_text_snapshot_payload_on_wrong_event": (
        (
            (
                "UPDATE lifecycle_events SET universe_snapshot = 3 "
                "WHERE event_kind = 'advance_requested'"
            ),
        ),
        "lifecycle stream checkpoint order is invalid",
    ),
    "invalid_universe_snapshot": (
        (
            (
                "UPDATE lifecycle_events SET universe_snapshot = '{}' "
                "WHERE event_kind = 'run_inputs_pinned'"
            ),
        ),
        "lifecycle stream checkpoint order is invalid",
    ),
    "non_text_universe_snapshot_id": (
        (
            (
                "UPDATE lifecycle_events SET universe_snapshot_id = 3 "
                "WHERE event_kind = 'universe_snapshotted'"
            ),
        ),
        "invalid universe_snapshot_id in lifecycle ledger",
    ),
    "non_text_universe_snapshot": (
        (
            (
                "UPDATE lifecycle_events SET universe_snapshot = 3 "
                "WHERE event_kind = 'run_inputs_pinned'"
            ),
        ),
        "invalid universe_snapshot in lifecycle ledger",
    ),
    "missing_universe_snapshot": (
        (
            (
                "UPDATE lifecycle_events SET universe_snapshot = NULL "
                "WHERE event_kind = 'run_inputs_pinned'"
            ),
        ),
        "invalid universe_snapshot in lifecycle ledger",
    ),
    "invalid_data_regime": (
        ("UPDATE lifecycle_events SET data_regime = 'INVALID'",),
        "invalid data_regime in lifecycle ledger",
    ),
    "non_text_event_envelope": (
        ("UPDATE lifecycle_events SET event_envelope = 3 WHERE sequence = 1",),
        "invalid event_envelope in lifecycle ledger",
    ),
    "evidence_on_wrong_event": (
        (EVIDENCE_ON_WRONG_EVENT_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "evidence_artifact_on_wrong_event": (
        (EVIDENCE_ARTIFACT_ON_WRONG_EVENT_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "evidence_refusal_on_wrong_event": (
        (EVIDENCE_REFUSAL_ON_WRONG_EVENT_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "evidence_policy_on_wrong_event": (
        (EVIDENCE_POLICY_ON_WRONG_EVENT_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "non_text_evidence_policy_id": (
        (
            (
                "UPDATE lifecycle_events SET evidence_policy_id = 3 "
                "WHERE event_kind = 'evidence_captured'"
            ),
        ),
        "invalid evidence_policy_id in lifecycle ledger",
    ),
    "missing_evidence_policy_id": (
        (
            (
                "UPDATE lifecycle_events SET evidence_policy_id = NULL "
                "WHERE event_kind = 'evidence_captured'"
            ),
        ),
        "invalid evidence_policy_id in lifecycle ledger",
    ),
    "non_text_evidence_artifact_ids": (
        (
            (
                "UPDATE lifecycle_events SET evidence_artifact_ids = 3 "
                "WHERE event_kind = 'evidence_captured'"
            ),
        ),
        "invalid evidence_artifact_ids in lifecycle ledger",
    ),
    "non_text_evidence_refusal_ids": (
        (
            (
                "UPDATE lifecycle_events SET evidence_refusal_ids = 3 "
                "WHERE event_kind = 'evidence_captured'"
            ),
        ),
        "invalid evidence_refusal_ids in lifecycle ledger",
    ),
    "missing_captured_evidence": (
        (MISSING_CAPTURED_EVIDENCE_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "refusal_on_captured_evidence": (
        (REFUSAL_ON_CAPTURED_EVIDENCE_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "duplicate_evidence_identifiers": (
        (DUPLICATE_EVIDENCE_IDENTIFIERS_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "malformed_evidence_identifiers": (
        (MALFORMED_EVIDENCE_IDENTIFIERS_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "non_list_evidence_identifiers": (
        (NON_LIST_EVIDENCE_IDENTIFIERS_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "invalid_evidence_identifier": (
        (INVALID_EVIDENCE_IDENTIFIER_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "noncanonical_evidence_identifiers": (
        (NONCANONICAL_EVIDENCE_IDENTIFIERS_SQL,),
        "lifecycle stream checkpoint order is invalid",
    ),
    "attention_artifact_on_wrong_event": (
        (
            (
                "UPDATE lifecycle_events SET attention_artifact_id = "
                "'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', "
                "attention_artifact = '{}' WHERE sequence = 0"
            ),
        ),
        "lifecycle stream checkpoint order is invalid",
    ),
    "malformed_attention_artifact": (
        (
            (
                "UPDATE lifecycle_events SET attention_artifact = '{' "
                "WHERE event_kind = 'attention_selected'"
            ),
        ),
        "lifecycle stream checkpoint order is invalid",
    ),
    "invalid_attention_artifact": (
        (
            (
                "UPDATE lifecycle_events SET attention_artifact = '{}' "
                "WHERE event_kind = 'attention_selected'"
            ),
        ),
        "lifecycle stream checkpoint order is invalid",
    ),
}


class _EqualityBombString(str):
    __slots__ = ()

    @override
    def __eq__(self, other: object) -> bool:
        raise RuntimeError

    @override
    def __hash__(self) -> int:
        return str.__hash__(self)


ROLLBACK_TRIGGERS = {
    "advance_requested": """
        CREATE TRIGGER fail_advance_requested_before_insert
        BEFORE INSERT ON lifecycle_events
        WHEN NEW.event_kind = 'advance_requested'
        BEGIN SELECT RAISE(ABORT, 'injected advance request failure'); END
    """,
    "phase_completed": """
        CREATE TRIGGER fail_phase_completed_before_insert
        BEFORE INSERT ON lifecycle_events
        WHEN NEW.event_kind = 'phase_completed'
        BEGIN SELECT RAISE(ABORT, 'injected reconciliation failure'); END
    """,
    "run_inputs_pinned": """
        CREATE TRIGGER fail_run_inputs_pinned_before_insert
        BEFORE INSERT ON lifecycle_events
        WHEN NEW.event_kind = 'run_inputs_pinned'
        BEGIN SELECT RAISE(ABORT, 'injected pin failure'); END
    """,
    "universe_snapshotted": """
        CREATE TRIGGER fail_universe_snapshotted_before_insert
        BEFORE INSERT ON lifecycle_events
        WHEN NEW.event_kind = 'universe_snapshotted'
        BEGIN SELECT RAISE(ABORT, 'injected universe snapshot failure'); END
    """,
    "evidence_captured": """
        CREATE TRIGGER fail_evidence_captured_before_insert
        BEFORE INSERT ON lifecycle_events
        WHEN NEW.event_kind = 'evidence_captured'
        BEGIN SELECT RAISE(ABORT, 'injected evidence capture failure'); END
    """,
    "attention_selected": """
        CREATE TRIGGER fail_attention_selected_before_insert
        BEFORE INSERT ON lifecycle_events
        WHEN NEW.event_kind = 'attention_selected'
        BEGIN SELECT RAISE(ABORT, 'injected attention selection failure'); END
    """,
    "dossiers_built": """
        CREATE TRIGGER fail_dossiers_built_before_insert
        BEFORE INSERT ON lifecycle_events
        WHEN NEW.event_kind = 'dossiers_built'
        BEGIN SELECT RAISE(ABORT, 'injected Dossier checkpoint failure'); END
    """,
    "research_run": """
        CREATE TRIGGER fail_research_run_before_insert
        BEFORE INSERT ON lifecycle_events
        WHEN NEW.event_kind = 'research_run'
        BEGIN SELECT RAISE(ABORT, 'injected research checkpoint failure'); END
    """,
    "memory_updated": """
        CREATE TRIGGER fail_memory_updated_before_insert
        BEFORE INSERT ON lifecycle_events
        WHEN NEW.event_kind = 'memory_updated'
        BEGIN SELECT RAISE(ABORT, 'injected memory checkpoint failure'); END
    """,
    "advance_refusal": """
        CREATE TRIGGER fail_advance_refusal_before_insert
        BEFORE INSERT ON advance_refusals
        BEGIN SELECT RAISE(ABORT, 'injected refusal failure'); END
    """,
    "advance_conflict": """
        CREATE TRIGGER fail_advance_conflict_before_insert
        BEFORE INSERT ON advance_conflicts
        BEGIN SELECT RAISE(ABORT, 'injected conflict failure'); END
    """,
}


@dataclass(frozen=True)
class FixedClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


@dataclass(frozen=True)
class FixedUniverseSource:
    inputs: UniverseInputs

    def load(self) -> UniverseInputs:
        return self.inputs


class NoncanonicalDatetime(datetime):
    @override
    def isoformat(self, *_args: object, **_kwargs: object) -> str:
        return "2026-08-21T18:00:00.000000-04:00"


def _forged_utc_instant() -> UtcInstant:
    forged = object.__new__(UtcInstant)
    object.__setattr__(
        forged,
        "value",
        NoncanonicalDatetime(2026, 8, 21, 22, 0, tzinfo=UTC),
    )
    return forged


class SimulatedInterruptionError(RuntimeError):
    """Stop one Advance call at an exact ledger write boundary."""


class MissingAggregateRowCursor(sqlite3.Cursor):
    """Simulate a hostile driver violating SQLite aggregate-row guarantees."""

    @override
    def fetchone(self) -> tuple[object, ...] | None:
        return None


class MissingAggregateRowConnection(sqlite3.Connection):
    """Return no row only for the refusal-sequence aggregate query."""

    @override
    def execute(
        self,
        sql: str,
        # The adapter passes positional SQLite scalars; typeshed's private parameter alias cannot be
        # named in this hostile-driver test override.
        parameters: tuple[SQLiteValue, ...] = (),  # type: ignore[override]
        /,
    ) -> sqlite3.Cursor:
        if "MAX(refusal_id)" in sql:
            cursor = self.cursor(factory=MissingAggregateRowCursor)
            cursor.execute(sql, parameters)
            return cursor
        return super().execute(sql, parameters)


class MissingAggregateRowLedger(SQLiteLifecycleLedger):
    """Open the test database through the hostile aggregate-row connection."""

    def __init__(self, database: Path) -> None:
        self.database = database
        super().__init__(database)

    @override
    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(
            f"{self.database.as_uri()}?mode=rw",
            uri=True,
            factory=MissingAggregateRowConnection,
        )


class InvalidAggregateRowCursor(sqlite3.Cursor):
    """Simulate a hostile driver returning a non-integer aggregate."""

    @override
    def fetchone(self) -> tuple[object, ...] | None:
        return ("invalid",)


class InvalidAggregateRowConnection(sqlite3.Connection):
    """Return a non-integer row only for the refusal-sequence aggregate query."""

    @override
    def execute(
        self,
        sql: str,
        # The adapter passes positional SQLite scalars; typeshed's private parameter alias cannot be
        # named in this hostile-driver test override.
        parameters: tuple[SQLiteValue, ...] = (),  # type: ignore[override]
        /,
    ) -> sqlite3.Cursor:
        if "MAX(refusal_id)" in sql:
            cursor = self.cursor(factory=InvalidAggregateRowCursor)
            cursor.execute(sql, parameters)
            return cursor
        return super().execute(sql, parameters)


class InvalidAggregateRowLedger(SQLiteLifecycleLedger):
    """Open the test database through the invalid aggregate-row connection."""

    def __init__(self, database: Path) -> None:
        self.database = database
        super().__init__(database)

    @override
    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(
            f"{self.database.as_uri()}?mode=rw",
            uri=True,
            factory=InvalidAggregateRowConnection,
        )


@dataclass(frozen=True)
class InterruptingLedger:
    delegate: SQLiteLifecycleLedger
    operation: str
    timing: str

    def pinned_constitution_use(self, idempotency_key: IdempotencyKey) -> ConstitutionUse | None:
        return self.delegate.pinned_constitution_use(idempotency_key)

    def constitution_uses(self) -> tuple[ConstitutionUse, ...]:
        return self.delegate.constitution_uses()

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        recorded_at: UtcInstant,
    ) -> LifecycleDecision:
        expected_operation = _attempt_operation(command, attempt)
        if self.operation == expected_operation and self.timing == "before":
            raise SimulatedInterruptionError
        result = self.delegate.advance_step(command, attempt, recorded_at)
        actual_operation = _decision_operation(result, expected_operation)
        if self.operation == actual_operation and self.timing == "after":
            raise SimulatedInterruptionError
        return result


@dataclass
class OneDecisionLedger:
    """Return one injected decision, then stop the public orchestration loop."""

    decision: LifecycleDecision
    calls: int = 0

    def pinned_constitution_use(
        self,
        _idempotency_key: IdempotencyKey,
    ) -> ConstitutionUse | None:
        return None

    def constitution_uses(self) -> tuple[ConstitutionUse, ...]:
        return ()

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        recorded_at: UtcInstant,
    ) -> LifecycleDecision:
        _ = (command, attempt, recorded_at)
        self.calls += 1
        if self.calls == 1:
            return self.decision
        return AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)


@dataclass(frozen=True, slots=True)
class _FailingProductionEvidenceVault:
    def stored_records_for_artifacts(self, _artifact_ids: tuple[str, ...]) -> Never:
        raise EvidencePersistenceError(SIMULATED_INVALID_PRODUCTION_EVIDENCE)


@dataclass(frozen=True, slots=True)
class _RefusingProductionMemory:
    def graph(self, _query: object) -> BeliefGraphRefusal:
        return BeliefGraphRefusal(BeliefGraphRefusalCode.INVALID_AUTHORITATIVE_HISTORY)


@dataclass
class RacingStartLedger:
    delegate: SQLiteLifecycleLedger
    winner_phase: LifecyclePhase | None
    raced: bool = False

    def pinned_constitution_use(self, idempotency_key: IdempotencyKey) -> ConstitutionUse | None:
        return self.delegate.pinned_constitution_use(idempotency_key)

    def constitution_uses(self) -> tuple[ConstitutionUse, ...]:
        return self.delegate.constitution_uses()

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        recorded_at: UtcInstant,
    ) -> LifecycleDecision:
        if not self.raced:
            winner = self.delegate.advance_step(command, AdvanceAttempt(), recorded_at)
            assert isinstance(winner, AppendLifecycleRecord)
            if self.winner_phase is LifecyclePhase.RECONCILE_PRIOR_STATE:
                reconciled = self.delegate.advance_step(command, winner.attempt, recorded_at)
                assert isinstance(reconciled, AppendLifecycleRecord)
            self.raced = True
        return self.delegate.advance_step(command, attempt, recorded_at)


def _attempt_operation(command: LifecycleCommand, attempt: AdvanceAttempt) -> str:
    if isinstance(command, InputRefusal):
        if command.code is InputRefusalCode.IDEMPOTENCY_KEY_CONFLICT:
            return "start"
        return "refuse"
    return {
        None: "start",
        0: "reconcile",
        1: "pin",
        2: "snapshot",
        3: "evidence",
        4: "attention",
        5: "dossier",
        6: "research",
        7: "memory",
    }.get(attempt.last_sequence, "memory")


def _decision_operation(decision: LifecycleDecision, fallback: str) -> str:
    if isinstance(decision, PerformEvidenceCapture):
        operation = "capture_effect"
    elif isinstance(decision, PerformAttentionSelection):
        operation = "attention_effect"
    elif isinstance(decision, PerformDossierBuild):
        operation = "dossier_effect"
    elif isinstance(decision, PerformResearch):
        operation = "research_effect"
    elif isinstance(decision, PerformMemoryUpdate):
        operation = "memory_effect"
    elif not isinstance(decision, (AppendLifecycleRecord, AppendTerminalLifecycleRecord)):
        operation = fallback
    elif isinstance(decision.record, DurableAdvanceRefusal):
        operation = "refuse"
    elif isinstance(decision.record, DurableAdvanceConflict):
        operation = "start"
    else:
        assert isinstance(decision.record, LifecycleEvent)
        operation = {
            0: "start",
            1: "reconcile",
            2: "pin",
            3: "snapshot",
            4: "evidence",
            5: "attention",
            6: "dossier",
            7: "research",
            8: "memory",
        }[decision.record.sequence]
    return operation


def _reference_mapping(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise AssertionError
    result: dict[str, object] = {}
    for key, item in value.items():
        if type(key) is not str:
            raise AssertionError
        result[key] = item
    return result


def _reference_items(value: object) -> list[dict[str, object]]:
    if type(value) is not list:
        raise AssertionError
    return [_reference_mapping(item) for item in value]


def _reference_strings(value: object) -> list[str]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise AssertionError
    return sorted(item for item in value if type(item) is str)


def _reference_decimal(value: object) -> str:
    if type(value) is not str:
        raise AssertionError
    return format(Decimal(value).normalize(), "f")


def _reference_identity_key(item: dict[str, object]) -> str:
    return json.dumps(item["identity"], sort_keys=True, separators=(",", ":"))


def _reference_policy_payload() -> dict[str, object]:
    payload = universe_policy()
    payload["approved_exchanges"] = _reference_strings(payload["approved_exchanges"])
    allowlist = payload["etf_allowlist"]
    if type(allowlist) is not list:
        raise AssertionError
    payload["etf_allowlist"] = sorted(
        allowlist,
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
    )
    payload["minimum_price"] = _reference_decimal(payload["minimum_price"])
    payload["minimum_median_dollar_volume"] = _reference_decimal(
        payload["minimum_median_dollar_volume"]
    )
    return payload


def _reference_input_payload() -> dict[str, object]:
    payload = recorded_universe()
    instruments = _reference_mapping(payload["instruments"])
    positions = _reference_mapping(payload["positions"])
    instrument_payload = _reference_mapping(instruments["payload"])
    position_payload = _reference_mapping(positions["payload"])
    normalized_instruments: list[dict[str, object]] = []
    for item in _reference_items(instrument_payload["items"]):
        observation = _reference_mapping(item["payload"])
        if item["asset_class"] == "us_equity":
            observation["price"] = _reference_decimal(observation["price"])
            observation["median_dollar_volume"] = _reference_decimal(
                observation["median_dollar_volume"]
            )
        item["payload"] = observation
        normalized_instruments.append(item)
    normalized_positions: list[dict[str, object]] = []
    for item in _reference_items(position_payload["items"]):
        observation = _reference_mapping(item["payload"])
        observation["quantity"] = _reference_decimal(observation["quantity"])
        valuation = _reference_mapping(observation["valuation"])
        valuation["amount"] = _reference_decimal(valuation["amount"])
        observation["valuation"] = valuation
        item["payload"] = observation
        normalized_positions.append(item)
    instrument_material = {
        "envelope_schema_version": 1,
        "payload_schema_version": 1,
        "record_kind": "instrument_snapshot",
        "payload_discriminator": "recorded_instrument_snapshot",
        "observed_at": instruments["observed_at"],
        "available_at": instruments["available_at"],
        "data_regime": instruments["data_regime"],
        "authority_scope": "market_data_observation",
        "source_fingerprint": instruments["source_fingerprint"],
        "payload": {
            "complete": True,
            "items": sorted(normalized_instruments, key=_reference_identity_key),
        },
    }
    position_material = {
        "envelope_schema_version": 1,
        "payload_schema_version": 1,
        "record_kind": "position_snapshot",
        "payload_discriminator": "recorded_position_snapshot",
        "observed_at": positions["observed_at"],
        "available_at": positions["available_at"],
        "data_regime": positions["data_regime"],
        "authority_scope": "portfolio_observation",
        "source_fingerprint": positions["source_fingerprint"],
        "payload": {
            "complete": True,
            "items": sorted(normalized_positions, key=_reference_identity_key),
        },
    }
    return {
        "schema_version": payload["schema_version"],
        "record_kind": "universe_inputs",
        "data_regime": payload["data_regime"],
        "evidence_cutoff": payload["evidence_cutoff"],
        "instruments": {
            **instrument_material,
            "content_hash": _reference_fingerprint(instrument_material),
        },
        "positions": {
            **position_material,
            "content_hash": _reference_fingerprint(position_material),
        },
    }


def _reference_instrument_material_fingerprint(snapshot: dict[str, object]) -> str:
    payload = _reference_mapping(snapshot["payload"])
    items = _reference_items(payload["items"])
    return _reference_fingerprint(
        {
            "fingerprint_kind": "instrument_snapshot_material",
            "fingerprint_schema_version": 1,
            "observed_at": snapshot["observed_at"],
            "available_at": snapshot["available_at"],
            "data_regime": snapshot["data_regime"],
            "source_fingerprint": snapshot["source_fingerprint"],
            "items": [
                {key: value for key, value in item.items() if key != "aliases"} for item in items
            ],
        }
    )


def _reference_subject_payload() -> list[dict[str, object]]:
    inputs = _reference_input_payload()
    instruments = _reference_mapping(inputs["instruments"])
    instrument_payload = _reference_mapping(instruments["payload"])
    items = _reference_items(instrument_payload["items"])

    def subject(symbol: str, *, is_position: bool, eligible: bool) -> dict[str, object]:
        item = next(
            candidate
            for candidate in items
            if _reference_mapping(_reference_items(candidate["aliases"])[0])["value"] == symbol
        )
        return {
            "identity": item["identity"],
            "aliases": item["aliases"],
            "is_position": is_position,
            "eligible_for_new_entry": eligible,
            "position_disposition": ("refresh_required" if is_position else "not_applicable"),
            "exclusion_reasons": [] if eligible else ["inactive", "not_tradable"],
        }

    return [
        subject("AAPL", is_position=False, eligible=True),
        subject("HOLD", is_position=True, eligible=False),
        subject("SPY", is_position=False, eligible=True),
    ]


def _reference_fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class _ReferenceStream:
    session: str
    events: int
    pinned_run_identity: PinnedRunIdentity
    universe_snapshot_id: str


@dataclass
class _LifecycleReferenceModel:
    configuration_version: int
    configuration_hash: str
    streams: dict[str, _ReferenceStream] = field(default_factory=dict)
    sessions: dict[str, str] = field(default_factory=dict)
    refusals: dict[str, tuple[AdvanceFailureReason, MarketSession]] = field(default_factory=dict)
    research_refusal_ids: dict[str, str] = field(default_factory=dict)
    conflicts: set[str] = field(default_factory=set)
    unkeyed_refusal_cycles: set[MarketSession] = field(default_factory=set)

    def advance(
        self,
        *,
        session: str,
        mode: str,
        key: str,
    ) -> AdvanceReceipt:
        if session == "invalid":
            return self._failed(AdvanceFailureReason.INVALID_SESSION)
        cycle = MarketSession(date.fromisoformat(session))
        if " " in key:
            self.unkeyed_refusal_cycles.add(cycle)
            return self._failed(AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY, cycle)
        invalid_reason = AdvanceFailureReason.INVALID_MODE if mode != "champion" else None
        replay = self._replay_refusal(key, cycle, invalid_reason)
        if replay is not None:
            return replay
        stream = self.streams.get(key)
        if invalid_reason is not None:
            return self._advance_invalid(session, key, stream, invalid_reason)
        return self._advance_valid(session, key, stream)

    def _replay_refusal(
        self,
        key: str,
        cycle: MarketSession,
        current_reason: AdvanceFailureReason | None,
    ) -> AdvanceReceipt | None:
        existing = self.refusals.get(key)
        if existing is None:
            return None
        reason, refused_cycle = existing
        if refused_cycle != cycle or (
            current_reason is None and reason is AdvanceFailureReason.INVALID_MODE
        ):
            return self._failed(AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT, cycle)
        if current_reason is not None and reason is not current_reason:
            return self._failed(AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT, cycle)
        if reason is AdvanceFailureReason.ATTENTION_SELECTION_FAILED:
            stream = self.streams.get(key)
            if stream is None:  # pragma: no cover - reference-model invariant.
                raise AssertionError
            return self._attention_failed(stream)
        if reason is AdvanceFailureReason.RESEARCH_FAILED:
            refusal_id = self.research_refusal_ids.get(key)
            if refusal_id is None:  # pragma: no cover - reference-model invariant.
                raise AssertionError
            return AdvanceReceipt.failed_closed(
                reason,
                cycle=cycle,
                research_refusal=ResearchRefusal(refusal_id),
            )
        return self._failed(reason, cycle)

    def _advance_valid(
        self,
        session: str,
        key: str,
        stream: _ReferenceStream | None,
    ) -> AdvanceReceipt:
        if stream is not None:
            if stream.session != session:
                return self._record_idempotency_conflict(key, stream, session)
            if stream.events < ATTENTION_PUBLISHED_EVENT_COUNT and any(
                candidate.events >= ATTENTION_PUBLISHED_EVENT_COUNT and candidate.session > session
                for candidate in self.streams.values()
            ):
                cycle = MarketSession(date.fromisoformat(session))
                stream.events = ATTENTION_REFUSAL_EVENT_COUNT
                self.refusals[key] = AdvanceFailureReason.ATTENTION_SELECTION_FAILED, cycle
                return self._attention_failed(stream)
            recovery = (
                AdvanceRecovery.PREVIOUSLY_COMPLETED
                if stream.events == PINNED_EVENT_COUNT
                else AdvanceRecovery.RESUMED
            )
            stream.events = PINNED_EVENT_COUNT
            return self._advanced(stream, recovery)
        if session in self.sessions:
            cycle = MarketSession(date.fromisoformat(session))
            self.refusals[key] = AdvanceFailureReason.SESSION_STREAM_CONFLICT, cycle
            return self._failed(AdvanceFailureReason.SESSION_STREAM_CONFLICT, cycle)
        stream = self._stream(session, PINNED_EVENT_COUNT)
        self.streams[key] = stream
        self.sessions[session] = key
        return self._advanced(stream, AdvanceRecovery.FRESH)

    def _advance_invalid(
        self,
        session: str,
        key: str,
        stream: _ReferenceStream | None,
        reason: AdvanceFailureReason,
    ) -> AdvanceReceipt:
        if stream is not None:
            return self._record_idempotency_conflict(key, stream, session)
        cycle = MarketSession(date.fromisoformat(session))
        self.refusals[key] = reason, cycle
        return self._failed(reason, cycle)

    def interrupt(self, *, session: str, key: str, committed_events: int) -> None:
        self.streams[key] = self._stream(session, committed_events)
        self.sessions[session] = key

    def counts(self) -> tuple[int, int, int]:
        return (
            sum(stream.events for stream in self.streams.values()),
            len(self.refusals) + len(self.unkeyed_refusal_cycles),
            len(self.conflicts),
        )

    def _record_idempotency_conflict(
        self,
        key: str,
        stream: _ReferenceStream,
        session: str,
    ) -> AdvanceReceipt:
        cycle = MarketSession(date.fromisoformat(session))
        if stream.events == PINNED_EVENT_COUNT:
            self.conflicts.add(key)
        else:
            self.refusals[key] = AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT, cycle
        return self._failed(AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT, cycle)

    @staticmethod
    def _failed(
        reason: AdvanceFailureReason,
        cycle: MarketSession | None = None,
    ) -> AdvanceReceipt:
        return AdvanceReceipt.failed_closed(reason, cycle=cycle)

    @staticmethod
    def _attention_failed(stream: _ReferenceStream) -> AdvanceReceipt:
        identity = stream.pinned_run_identity
        checkpoint = materialized_evidence_capture_checkpoint(
            run_id=identity.run_id,
            universe_snapshot_id=stream.universe_snapshot_id,
            cutoff=identity.evidence_cutoff,
            data_regime=identity.data_regime,
        )
        return AdvanceReceipt.failed_closed(
            AdvanceFailureReason.ATTENTION_SELECTION_FAILED,
            cycle=identity.cycle,
            evidence_capture=checkpoint,
            attention_refusal_reason=AttentionRefusalReason.CONTRADICTORY_EVIDENCE,
        )

    @staticmethod
    def _advanced(stream: _ReferenceStream, recovery: AdvanceRecovery) -> AdvanceReceipt:
        identity = stream.pinned_run_identity
        checkpoint = materialized_evidence_capture_checkpoint(
            run_id=identity.run_id,
            universe_snapshot_id=stream.universe_snapshot_id,
            cutoff=identity.evidence_cutoff,
            data_regime=identity.data_regime,
        )
        snapshot = universe_snapshot(identity)
        assert snapshot.snapshot_id == stream.universe_snapshot_id
        return AdvanceReceipt.advanced(
            identity,
            snapshot,
            recovery,
            UtcInstant.from_datetime(RECORDED_AT),
            checkpoint,
            attention_artifact(identity, snapshot, checkpoint),
            DOSSIER_CHECKPOINT,
            RESEARCH_CHECKPOINT,
            MEMORY_CHECKPOINT,
            None,
        )

    def _stream(self, session: str, events: int) -> _ReferenceStream:
        identity = self._identity(session)
        return _ReferenceStream(
            session=session,
            events=events,
            pinned_run_identity=identity,
            universe_snapshot_id=self._snapshot_id(identity),
        )

    def _identity(self, session: str) -> PinnedRunIdentity:
        inputs = _reference_input_payload()
        policy = _reference_policy_payload()
        data_regime = inputs["data_regime"]
        cutoff_text = inputs["evidence_cutoff"]
        if type(data_regime) is not str or type(cutoff_text) is not str:
            raise AssertionError
        instrument_snapshot = _reference_mapping(inputs["instruments"])
        position_snapshot = _reference_mapping(inputs["positions"])
        instrument_snapshot_hash = _reference_instrument_material_fingerprint(instrument_snapshot)
        position_snapshot_hash = position_snapshot["content_hash"]
        if type(instrument_snapshot_hash) is not str or type(position_snapshot_hash) is not str:
            raise AssertionError
        eligibility_policy_hash = _reference_fingerprint(policy)
        research_policy_hash = typed_research_policy().fingerprint
        cycle = MarketSession(date.fromisoformat(session))
        cycle_text = json.dumps(cycle.to_payload(), sort_keys=True, separators=(",", ":"))
        encoded = json.dumps(
            (
                self.configuration_hash,
                self.configuration_version,
                research_policy_hash,
                ACTIVE_CONSTITUTION.content_hash,
                ACTIVE_CONSTITUTION.version,
                "champion",
                cycle_text,
                data_regime,
                cutoff_text,
                instrument_snapshot_hash,
                position_snapshot_hash,
                eligibility_policy_hash,
            )
        ).encode()
        return PinnedRunIdentity(
            run_id=hashlib.sha256(encoded).hexdigest(),
            cycle=cycle,
            configuration_version=self.configuration_version,
            configuration_hash=self.configuration_hash,
            research_policy_hash=research_policy_hash,
            constitution_version=ACTIVE_CONSTITUTION.version,
            constitution_hash=ACTIVE_CONSTITUTION.content_hash,
            data_regime=data_regime,
            evidence_cutoff=UtcInstant.parse(cutoff_text),
            instrument_snapshot_hash=instrument_snapshot_hash,
            position_snapshot_hash=position_snapshot_hash,
            eligibility_policy_hash=eligibility_policy_hash,
        )

    @staticmethod
    def _snapshot_id(identity: PinnedRunIdentity) -> str:
        subjects = _reference_subject_payload()
        material = {
            "identity_kind": "eligible_universe_snapshot",
            "identity_schema_version": 1,
            "run_id": identity.run_id,
            "cycle": identity.cycle.to_payload(),
            "data_regime": identity.data_regime,
            "evidence_cutoff": identity.evidence_cutoff.isoformat(),
            "material_fingerprints": {
                "eligibility_policy": identity.eligibility_policy_hash,
                "instrument_snapshot": identity.instrument_snapshot_hash,
                "position_snapshot": identity.position_snapshot_hash,
            },
            "subjects": [
                {key: value for key, value in subject.items() if key != "aliases"}
                for subject in subjects
            ],
        }
        return _reference_fingerprint(material)


def _configure(
    state_root: Path,
    *,
    clock: FixedClock | None = None,
    cio_stance: Literal["hold", "abstain"] = "hold",
) -> Advance:
    configured = configure_advance(
        (
            ConfigurationSource(
                "test",
                runtime_configuration(state_root),
            ),
        ),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=production_recorded_evidence(),
        recorded_official_evidence=production_recorded_official_evidence(),
        recorded_model=ValidProductionModel(cio_stance=cio_stance),
        session_eligibility=RecordedSessionEligibility(),
        clock=(FixedClock(datetime(2026, 8, 21, 22, 0, tzinfo=UTC)) if clock is None else clock),
    )
    assert isinstance(configured, Advance)
    return configured


def _receipt_evidence_checkpoint(receipt: AdvanceReceipt) -> EvidenceCaptureCheckpoint:
    assert receipt.evidence_policy_id is not None
    return EvidenceCaptureCheckpoint(
        receipt.evidence_policy_id,
        receipt.evidence_artifact_ids,
        receipt.evidence_refusal_ids,
    )


@pytest.mark.parametrize("phase", ["research", "memory"])
def test_advance_rejects_a_rebuilt_production_checkpoint_mismatch(
    tmp_path: Path,
    phase: str,
) -> None:
    capability = _configure(tmp_path / "runtime")
    receipt = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="checkpoint-mismatch",
    )
    identity = receipt.pinned_run_identity
    artifact = receipt.attention_artifact
    assert identity is not None
    assert artifact is not None
    mismatched = ResearchCheckpoint(("f" * SHA256_HEX_LENGTH,))
    if phase == "research":
        decision: LifecycleDecision = PerformResearch(identity, mismatched, artifact)
    else:
        assert phase == "memory"
        decision = PerformMemoryUpdate(identity, mismatched, artifact)
    invalid = replace(capability, ledger=OneDecisionLedger(decision))

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle ledger returned an incomplete checkpoint result",
    ):
        invalid(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="checkpoint-mismatch",
        )


@pytest.mark.parametrize(
    "corruption",
    [
        "attention_type",
        "vault",
        "dossier_card",
        "holding_identity",
        "holding_mismatch",
        "belief_graph",
    ],
)
def test_advance_rejects_invalid_production_context_material(
    tmp_path: Path,
    corruption: str,
) -> None:
    capability = _configure(tmp_path / "runtime")
    receipt = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="invalid-production-context",
    )
    identity = receipt.pinned_run_identity
    artifact = deepcopy(receipt.attention_artifact)
    assert identity is not None
    assert artifact is not None
    evidence = _receipt_evidence_checkpoint(receipt)
    selected: AttentionArtifact = artifact
    if corruption == "attention_type":
        selected = cast("AttentionArtifact", "invalid")
    elif corruption == "dossier_card":
        assert artifact.holding_refreshes
        object.__setattr__(
            artifact,
            "dossier_requests",
            (
                DossierRequest(
                    "e" * SHA256_HEX_LENGTH,
                    "f" * SHA256_HEX_LENGTH,
                    artifact.holding_refreshes[0].identity,
                    DossierSelectionKind.PRIORITY,
                ),
            ),
        )
    elif corruption == "holding_identity":
        assert artifact.holding_refreshes
        object.__setattr__(
            artifact.holding_refreshes[0],
            "identity",
            cast("EquityInstrumentIdentity", "invalid"),
        )
    elif corruption == "holding_mismatch":
        assert artifact.holding_refreshes
        object.__setattr__(
            artifact.holding_refreshes[0],
            "disposition",
            HoldingRefreshDisposition.PORTFOLIO_MISMATCH,
        )
    decision = PerformDossierBuild(
        identity,
        universe_snapshot(identity),
        evidence,
        selected,
    )
    invalid = replace(capability, ledger=OneDecisionLedger(decision))
    if corruption == "vault":
        invalid = replace(
            invalid,
            evidence_vault=cast("EvidenceVault", _FailingProductionEvidenceVault()),
        )
    elif corruption == "belief_graph":
        invalid = replace(
            invalid,
            memory=cast("Record", _RefusingProductionMemory()),
        )

    if corruption == "holding_mismatch":
        observed = invalid(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="invalid-production-context",
        )
        assert observed.failure_reason is AdvanceFailureReason.INVALID_DURABLE_STATE
    else:
        with pytest.raises(
            InvalidLifecycleStateError,
            match="lifecycle ledger returned an incomplete checkpoint result",
        ):
            invalid(
                cycle=_cycle_payload("2026-08-21"),
                mode="champion",
                idempotency_key="invalid-production-context",
            )


def _events(database: Path) -> list[tuple[str, str | None]]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT event_kind, completed_phase FROM lifecycle_events ORDER BY sequence"
        ).fetchall()
    return [
        (
            str(kind),
            None if phase is None else str(json.loads(str(phase))["payload"]["phase"]),
        )
        for kind, phase in rows
    ]


def _authoritative_counts(database: Path) -> tuple[int, int, int]:
    with sqlite3.connect(database) as connection:
        event_count = connection.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()
        refusal_count = connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone()
        conflict_count = connection.execute("SELECT COUNT(*) FROM advance_conflicts").fetchone()
    assert event_count is not None
    assert refusal_count is not None
    assert conflict_count is not None
    return int(event_count[0]), int(refusal_count[0]), int(conflict_count[0])


def _advance_in_fresh_process(
    state_root: Path,
    idempotency_key: str,
    *,
    session: str = "2026-08-21",
    mode: str = "champion",
) -> tuple[str, str, str, str]:
    completed = subprocess.run(  # noqa: S603
        (
            sys.executable,
            "-m",
            "tests.integration._advance_process",
            str(state_root),
            session,
            mode,
            idempotency_key,
        ),
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    fields = completed.stdout.split("\t")
    assert len(fields) == RECEIPT_FIELD_COUNT
    return fields[0], fields[1], fields[2], fields[3]


def test_advance_pins_one_stream_and_reconstructs_its_receipt_after_reopen(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    first_capability = _configure(state_root)

    first = first_capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="advance-2026-08-21",
    )
    reopened = _configure(state_root)
    replay = reopened(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="advance-2026-08-21",
    )

    assert first.recovery is AdvanceRecovery.FRESH
    assert replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED
    assert first.disposition is replay.disposition
    assert first.completed_phase == replay.completed_phase
    assert first.pinned_run_identity == replay.pinned_run_identity
    assert first.disposition is AdvanceDisposition.ADVANCED
    assert first.completed_phase is not None
    assert first.completed_phase.phase is LifecyclePhase.UPDATE_MEMORY
    assert first.pinned_run_identity is not None
    assert len(first.pinned_run_identity.run_id) == SHA256_HEX_LENGTH
    assert len(first.pinned_run_identity.configuration_hash) == SHA256_HEX_LENGTH
    assert first.failure_reason is None
    assert _events(state_root / "lifecycle.sqlite3") == [
        ("advance_requested", None),
        ("phase_completed", "ReconcilePriorState"),
        ("run_inputs_pinned", "PinRunInputs"),
        ("universe_snapshotted", "SnapshotUniverse"),
        ("evidence_captured", "CaptureEvidence"),
        ("attention_selected", "SelectAttention"),
        ("dossiers_built", "BuildDossiers"),
        ("research_run", "RunResearch"),
        ("memory_updated", "UpdateMemory"),
    ]
    assert stat.S_IMODE(state_root.stat().st_mode) == PRIVATE_DIRECTORY_MODE
    assert stat.S_IMODE((state_root / "lifecycle.sqlite3").stat().st_mode) == PRIVATE_FILE_MODE


def test_advance_captures_all_recorded_official_sources_without_network_access(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    configured = configure_advance(
        (ConfigurationSource("test", runtime_configuration(state_root)),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=production_recorded_evidence(),
        recorded_model=ValidProductionModel(),
        session_eligibility=RecordedSessionEligibility(),
        recorded_official_evidence=production_recorded_official_evidence(),
        clock=FixedClock(datetime(2026, 8, 21, 22, 0, tzinfo=UTC)),
    )
    assert isinstance(configured, Advance)

    receipt = configured(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="advance-with-official-evidence",
    )

    assert receipt.disposition is AdvanceDisposition.ADVANCED
    assert len(receipt.evidence_artifact_ids) == COMPLETE_EVIDENCE_ARTIFACT_COUNT
    assert receipt.evidence_refusal_ids == ()
    assert (
        len(tuple((state_root / "evidence-vault" / "contents").iterdir()))
        == COMPLETE_EVIDENCE_ARTIFACT_COUNT
    )


def test_optional_official_artifact_after_cutoff_is_not_published_as_admitted_evidence(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    official = production_recorded_official_evidence()
    items = official["items"]
    assert isinstance(items, list)
    sec = items[0]
    assert isinstance(sec, dict)
    sec["published_at"] = "2026-08-21T20:30:00.000000+00:00"
    sec["first_observed_at"] = "2026-08-21T20:31:00.000000+00:00"
    configured = configure_advance(
        (ConfigurationSource("test", runtime_configuration(state_root)),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=production_recorded_evidence(),
        recorded_model=ValidProductionModel(),
        session_eligibility=RecordedSessionEligibility(),
        recorded_official_evidence=official,
        clock=FixedClock(datetime(2026, 8, 21, 22, 0, tzinfo=UTC)),
    )
    assert isinstance(configured, Advance)

    receipt = configured(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="optional-future-official-evidence",
    )

    assert receipt.disposition is AdvanceDisposition.ADVANCED
    assert len(receipt.evidence_artifact_ids) == COMPLETE_EVIDENCE_ARTIFACT_COUNT - 1
    assert receipt.evidence_refusal_ids == ()
    assert (
        len(tuple((state_root / "evidence-vault" / "contents").iterdir()))
        == COMPLETE_EVIDENCE_ARTIFACT_COUNT
    )


def test_advance_fails_closed_when_a_required_official_source_is_absent(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    configuration = runtime_configuration(state_root)
    policy = configuration["evidence_policy"]
    assert isinstance(policy, dict)
    requests = policy["requests"]
    assert isinstance(requests, list)
    sec_request = next(
        request
        for request in requests
        if isinstance(request, dict) and request.get("kind") == "sec_filing"
    )
    sec_request["required"] = True
    configured = configure_advance(
        (ConfigurationSource("test", configuration),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=production_recorded_evidence(),
        recorded_model=ValidProductionModel(),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(datetime(2026, 8, 21, 22, 0, tzinfo=UTC)),
    )
    assert isinstance(configured, Advance)

    receipt = configured(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="required-official-evidence",
    )

    assert receipt.disposition is AdvanceDisposition.FAILED_CLOSED
    assert receipt.failure_reason is AdvanceFailureReason.EVIDENCE_CAPTURE_FAILED
    assert len(receipt.evidence_artifact_ids) == BASE_EVIDENCE_ARTIFACT_COUNT
    assert len(receipt.evidence_refusal_ids) == 1
    assert (
        len(tuple((state_root / "evidence-vault" / "outcomes").iterdir()))
        == COMPLETE_EVIDENCE_ARTIFACT_COUNT
    )


def test_disabled_crypto_cycle_is_refused_before_authoritative_state_changes(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    cycle = CryptoDecisionWindow(
        UtcInstant.from_datetime(datetime(2026, 8, 22, tzinfo=UTC)),
        UtcInstant.from_datetime(datetime(2026, 8, 22, 1, tzinfo=UTC)),
    )

    receipt = capability(
        cycle=cycle.to_payload(),
        mode="champion",
        idempotency_key="disabled-crypto-cycle",
    )

    assert receipt.disposition is AdvanceDisposition.FAILED_CLOSED
    assert receipt.failure_reason is AdvanceFailureReason.UNSUPPORTED_CYCLE
    assert receipt.cycle == cycle
    assert _authoritative_counts(state_root / "lifecycle.sqlite3") == (0, 0, 0)
    assert isinstance(capability.ledger, SQLiteLifecycleLedger)
    assert capability.ledger.rebuild_status() == LifecycleStatus.not_started()


@pytest.mark.parametrize(
    ("operation", "timing", "committed_events", "expected_recovery"),
    [
        ("start", "before", 0, AdvanceRecovery.FRESH),
        ("start", "after", 1, AdvanceRecovery.RESUMED),
        ("reconcile", "before", 1, AdvanceRecovery.RESUMED),
        ("reconcile", "after", 2, AdvanceRecovery.RESUMED),
        ("pin", "before", 2, AdvanceRecovery.RESUMED),
        ("pin", "after", 3, AdvanceRecovery.RESUMED),
        ("snapshot", "before", 3, AdvanceRecovery.RESUMED),
        ("snapshot", "after", 4, AdvanceRecovery.RESUMED),
        ("evidence", "before", 4, AdvanceRecovery.RESUMED),
        ("evidence", "after", 5, AdvanceRecovery.RESUMED),
        ("attention", "before", 5, AdvanceRecovery.RESUMED),
        ("attention", "after", 6, AdvanceRecovery.RESUMED),
        ("dossier", "before", 6, AdvanceRecovery.RESUMED),
        ("dossier", "after", 7, AdvanceRecovery.RESUMED),
        ("research", "before", 7, AdvanceRecovery.RESUMED),
        ("research", "after", 8, AdvanceRecovery.RESUMED),
        ("memory", "before", 8, AdvanceRecovery.RESUMED),
        ("memory", "after", 9, AdvanceRecovery.PREVIOUSLY_COMPLETED),
    ],
)
def test_fresh_process_resumes_at_every_universe_snapshot_write_boundary(
    tmp_path: Path,
    operation: str,
    timing: str,
    committed_events: int,
    expected_recovery: AdvanceRecovery,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    interrupted = Advance(
        ledger=InterruptingLedger(ledger, operation, timing),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        universe_source=capability.universe_source,
        enabled_asset_classes=capability.enabled_asset_classes,
        universe_policy=capability.universe_policy,
        evidence_capture=capability.evidence_capture,
        attention_policy=capability.attention_policy,
        attention_inputs=capability.attention_inputs,
        clock=capability.clock,
        constitution_registry=(capability.constitution_registry),
        production_research=capability.production_research,
        evidence_vault=capability.evidence_vault,
        memory=capability.memory,
        memory_refusal_ledger=capability.memory_refusal_ledger,
    )

    with pytest.raises(SimulatedInterruptionError):
        interrupted(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="write-boundary-recovery",
        )

    database = state_root / "lifecycle.sqlite3"
    assert len(_events(database)) == committed_events
    disposition, phase, recovery, run_id = _advance_in_fresh_process(
        state_root, "write-boundary-recovery"
    )

    assert disposition == AdvanceDisposition.ADVANCED.value
    assert phase == LifecyclePhase.UPDATE_MEMORY.value
    assert recovery == expected_recovery.value
    assert len(run_id) == SHA256_HEX_LENGTH
    assert len(_events(database)) == PINNED_EVENT_COUNT


def test_attention_retry_keeps_selection_identity_when_publication_time_changes(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "partial"
    capability = _configure(state_root, cio_stance="abstain")
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    interrupted = replace(
        capability,
        ledger=InterruptingLedger(ledger, "attention", "before"),
    )
    cycle = _cycle_payload("2026-08-21")

    with pytest.raises(SimulatedInterruptionError):
        interrupted(
            cycle=cycle,
            mode="champion",
            idempotency_key="changing-clock-retry",
        )

    first_root = copytree(state_root, tmp_path / "first-publication")
    later_root = copytree(state_root, tmp_path / "later-publication")
    first_time = RECORDED_AT
    later_time = RECORDED_AT + timedelta(seconds=1)
    first_capability = replace(
        _configure(
            first_root,
            clock=FixedClock(first_time),
            cio_stance="abstain",
        ),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
    )
    later_capability = replace(
        _configure(
            later_root,
            clock=FixedClock(later_time),
            cio_stance="abstain",
        ),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
    )
    first = first_capability(
        cycle=cycle,
        mode="champion",
        idempotency_key="changing-clock-retry",
    )
    later = later_capability(
        cycle=cycle,
        mode="champion",
        idempotency_key="changing-clock-retry",
    )

    assert first.recovery is AdvanceRecovery.RESUMED
    assert later.recovery is AdvanceRecovery.RESUMED
    assert first.attention_artifact is not None
    assert later.attention_artifact is not None
    assert later.attention_artifact.artifact_id == first.attention_artifact.artifact_id
    assert later.attention_artifact.content_hash != first.attention_artifact.content_hash
    assert later.attention_artifact.available_at == UtcInstant.from_datetime(later_time)
    assert first.attention_artifact.available_at == UtcInstant.from_datetime(first_time)
    assert later.attention_artifact.candidate_cards == first.attention_artifact.candidate_cards
    assert later.attention_artifact.dossier_requests == first.attention_artifact.dossier_requests
    assert (
        later.attention_artifact.resource_accounting == first.attention_artifact.resource_accounting
    )

    after_first = first_capability(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="after-changing-clock-retry",
    )
    after_later = later_capability(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="after-changing-clock-retry",
    )

    assert after_first.attention_artifact is not None
    assert after_later.attention_artifact is not None
    assert after_later.attention_artifact.history_fingerprint == (
        after_first.attention_artifact.history_fingerprint
    )
    assert after_later.attention_artifact.artifact_id == after_first.attention_artifact.artifact_id
    assert after_later.attention_artifact.candidate_cards == (
        after_first.attention_artifact.candidate_cards
    )
    assert after_later.attention_artifact.resource_accounting == (
        after_first.attention_artifact.resource_accounting
    )


@pytest.mark.parametrize(
    "refusal_case",
    [
        ("2026-08-21", "invalid", "keyed-refusal", AdvanceFailureReason.INVALID_MODE),
        (
            "2026-08-21",
            "champion",
            "not valid",
            AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
        ),
    ],
)
@pytest.mark.parametrize(("timing", "committed_refusals"), [("before", 0), ("after", 1)])
def test_fresh_process_recovers_at_each_refusal_write_boundary(
    tmp_path: Path,
    refusal_case: tuple[str, str, str, AdvanceFailureReason],
    timing: str,
    committed_refusals: int,
) -> None:
    session, mode, idempotency_key, reason = refusal_case
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    interrupted = Advance(
        ledger=InterruptingLedger(ledger, "refuse", timing),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        universe_source=capability.universe_source,
        enabled_asset_classes=capability.enabled_asset_classes,
        universe_policy=capability.universe_policy,
        evidence_capture=capability.evidence_capture,
        attention_policy=capability.attention_policy,
        attention_inputs=capability.attention_inputs,
        clock=capability.clock,
        constitution_registry=(capability.constitution_registry),
        production_research=capability.production_research,
        evidence_vault=capability.evidence_vault,
        memory=capability.memory,
        memory_refusal_ledger=capability.memory_refusal_ledger,
    )

    with pytest.raises(SimulatedInterruptionError):
        interrupted(
            cycle=_cycle_payload(session),
            mode=mode,
            idempotency_key=idempotency_key,
        )

    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone() == (
            committed_refusals,
        )
    disposition, phase, recovery, failure_reason = _advance_in_fresh_process(
        state_root,
        idempotency_key,
        session=session,
        mode=mode,
    )

    assert disposition == AdvanceDisposition.FAILED_CLOSED.value
    assert phase == ""
    assert recovery == ""
    assert failure_reason == reason.value
    assert _events(database) == []
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT reason_code FROM advance_refusals").fetchall() == [
            (reason.value,)
        ]


@pytest.mark.parametrize(("timing", "committed_conflicts"), [("before", 0), ("after", 1)])
def test_fresh_process_recovers_at_each_completed_conflict_write_boundary(
    tmp_path: Path,
    timing: str,
    committed_conflicts: int,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="interrupted-conflict",
    )
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    interrupted = Advance(
        ledger=InterruptingLedger(ledger, "start", timing),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        universe_source=capability.universe_source,
        enabled_asset_classes=capability.enabled_asset_classes,
        universe_policy=capability.universe_policy,
        evidence_capture=capability.evidence_capture,
        attention_policy=capability.attention_policy,
        attention_inputs=capability.attention_inputs,
        clock=capability.clock,
        constitution_registry=(capability.constitution_registry),
        production_research=capability.production_research,
        evidence_vault=capability.evidence_vault,
        memory=capability.memory,
        memory_refusal_ledger=capability.memory_refusal_ledger,
    )

    with pytest.raises(SimulatedInterruptionError):
        interrupted(
            cycle=_cycle_payload("2026-08-22"),
            mode="champion",
            idempotency_key="interrupted-conflict",
        )

    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM advance_conflicts").fetchone() == (
            committed_conflicts,
        )
    disposition, phase, recovery, failure_reason = _advance_in_fresh_process(
        state_root,
        "interrupted-conflict",
        session="2026-08-22",
    )

    assert disposition == AdvanceDisposition.FAILED_CLOSED.value
    assert phase == ""
    assert recovery == ""
    assert failure_reason == AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT.value
    assert len(_events(database)) == PINNED_EVENT_COUNT
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT reason_code FROM advance_conflicts").fetchall() == [
            (AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT.value,)
        ]


@pytest.mark.parametrize(
    "winner_phase",
    [None, LifecyclePhase.RECONCILE_PRIOR_STATE],
)
def test_duplicate_delivery_reports_progress_committed_by_the_winner_as_resumed(
    tmp_path: Path,
    winner_phase: LifecyclePhase | None,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    raced = Advance(
        ledger=RacingStartLedger(ledger, winner_phase),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        universe_source=capability.universe_source,
        enabled_asset_classes=capability.enabled_asset_classes,
        universe_policy=capability.universe_policy,
        evidence_capture=capability.evidence_capture,
        attention_policy=capability.attention_policy,
        attention_inputs=capability.attention_inputs,
        clock=capability.clock,
        constitution_registry=(capability.constitution_registry),
        production_research=capability.production_research,
        evidence_vault=capability.evidence_vault,
        memory=capability.memory,
        memory_refusal_ledger=capability.memory_refusal_ledger,
    )

    receipt = raced(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="concurrent-duplicate",
    )

    assert receipt.disposition is AdvanceDisposition.ADVANCED
    assert receipt.recovery is AdvanceRecovery.RESUMED
    assert len(_events(state_root / "lifecycle.sqlite3")) == PINNED_EVENT_COUNT


def test_replay_validates_research_refusal_with_preceding_attention_history(
    tmp_path: Path,
) -> None:
    capability = _configure(tmp_path / "runtime", cio_stance="abstain")
    for day in (2, 3):
        receipt = capability(
            cycle=_cycle_payload(f"2026-09-0{day}"),
            mode="champion",
            idempotency_key=f"attention-history-{day}",
        )
        assert receipt.disposition is AdvanceDisposition.NO_ACTION

    failed = capability(
        cycle=_cycle_payload("2026-09-04"),
        mode="champion",
        idempotency_key="attention-history-refusal",
    )
    replayed = capability(
        cycle=_cycle_payload("2026-09-04"),
        mode="champion",
        idempotency_key="attention-history-refusal",
    )

    assert failed.failure_reason is AdvanceFailureReason.RESEARCH_FAILED
    assert replayed == failed


class LifecycleStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self._temporary_directory = TemporaryDirectory(
            prefix="agentic-investment-os-lifecycle-state-machine-"
        )
        self.state_root = Path(self._temporary_directory.name).resolve() / "runtime"
        self.capability = _configure(self.state_root, cio_stance="abstain")
        self.reference = _LifecycleReferenceModel(
            self.capability.configuration_version,
            self.capability.configuration_hash,
        )
        self.next_identity = 0

    @override
    def teardown(self) -> None:
        self._temporary_directory.cleanup()

    def _new_request(self) -> tuple[str, str]:
        self.next_identity += 1
        session = date(2026, 9, 1) + timedelta(days=self.next_identity)
        return session.isoformat(), f"generated-{self.next_identity}"

    def _record_research_refusal(
        self,
        *,
        session: str,
        key: str,
        observed: AdvanceReceipt,
    ) -> AdvanceReceipt:
        assert observed.failure_reason is AdvanceFailureReason.RESEARCH_FAILED
        assert observed.research_refusal_id is not None
        cycle = MarketSession(date.fromisoformat(session))
        if key not in self.reference.streams:
            self.reference.interrupt(
                session=session,
                key=key,
                committed_events=ATTENTION_PUBLISHED_EVENT_COUNT,
            )
        self.reference.streams[key].events = ATTENTION_PUBLISHED_EVENT_COUNT
        self.reference.refusals[key] = AdvanceFailureReason.RESEARCH_FAILED, cycle
        self.reference.research_refusal_ids[key] = observed.research_refusal_id
        return AdvanceReceipt.failed_closed(
            AdvanceFailureReason.RESEARCH_FAILED,
            cycle=cycle,
            research_refusal=ResearchRefusal(observed.research_refusal_id),
        )

    def _advance(self, *, session: str, mode: str, key: str) -> None:
        expected = self.reference.advance(session=session, mode=mode, key=key)
        observed = self.capability(cycle=_cycle_payload(session), mode=mode, idempotency_key=key)
        if observed.disposition in (
            AdvanceDisposition.ADVANCED,
            AdvanceDisposition.NO_ACTION,
        ):
            assert observed.attention_artifact is not None
            expected = replace(
                expected,
                evidence_policy_id=observed.evidence_policy_id,
                evidence_artifact_ids=observed.evidence_artifact_ids,
                evidence_refusal_ids=observed.evidence_refusal_ids,
                attention_artifact=observed.attention_artifact,
                dossier_checkpoint=observed.dossier_checkpoint,
                research_checkpoint=observed.research_checkpoint,
                memory_checkpoint=observed.memory_checkpoint,
                disposition=observed.disposition,
                no_action_reason=observed.no_action_reason,
            )
        elif observed.failure_reason is AdvanceFailureReason.ATTENTION_SELECTION_FAILED:
            expected = replace(
                expected,
                evidence_policy_id=observed.evidence_policy_id,
                evidence_artifact_ids=observed.evidence_artifact_ids,
                evidence_refusal_ids=observed.evidence_refusal_ids,
                attention_refusal_reason=observed.attention_refusal_reason,
            )
        elif observed.failure_reason is AdvanceFailureReason.RESEARCH_FAILED:
            expected = self._record_research_refusal(
                session=session,
                key=key,
                observed=observed,
            )
        assert observed == expected

    def _stream_key(self, selection: int) -> str:
        streams = sorted(self.reference.streams)
        return streams[selection % len(streams)]

    @rule()
    def advance_fresh(self) -> None:
        session, key = self._new_request()
        self._advance(session=session, mode="champion", key=key)

    @precondition(lambda self: bool(self.reference.streams))
    @rule(selection=st.integers(min_value=0, max_value=255))
    def replay(self, selection: int) -> None:
        key = self._stream_key(selection)
        self._advance(
            session=self.reference.streams[key].session,
            mode="champion",
            key=key,
        )

    @precondition(lambda self: bool(self.reference.streams))
    @rule(selection=st.integers(min_value=0, max_value=255))
    def reuse_idempotency_key_for_another_session(self, selection: int) -> None:
        key = self._stream_key(selection)
        session, _ = self._new_request()
        self._advance(session=session, mode="champion", key=key)

    @precondition(lambda self: bool(self.reference.streams))
    @rule(selection=st.integers(min_value=0, max_value=255))
    def reuse_session_for_another_idempotency_key(self, selection: int) -> None:
        existing = self.reference.streams[self._stream_key(selection)]
        _, key = self._new_request()
        self._advance(session=existing.session, mode="champion", key=key)

    @rule(invalid_field=st.sampled_from(("session", "mode", "idempotency_key")))
    def submit_invalid_input(
        self,
        invalid_field: Literal["session", "mode", "idempotency_key"],
    ) -> None:
        session, key = self._new_request()
        mode = "champion"
        if invalid_field == "session":
            session = "invalid"
        elif invalid_field == "mode":
            mode = "invalid"
        else:
            key = f"not valid {self.next_identity}"
        self._advance(session=session, mode=mode, key=key)

    @precondition(lambda self: bool(self.reference.refusals))
    @rule(selection=st.integers(min_value=0, max_value=255))
    def replay_durable_refusal(self, selection: int) -> None:
        keys = sorted(self.reference.refusals)
        key = keys[selection % len(keys)]
        self._advance(session="2026-08-21", mode="champion", key=key)

    @rule(
        interrupted_write=st.sampled_from(
            (
                ("start", 1),
                ("reconcile", 2),
                ("pin", 3),
                ("snapshot", 4),
                ("evidence", 5),
                ("attention", 6),
                ("dossier", 7),
                ("research", 8),
                ("memory", PINNED_EVENT_COUNT),
            )
        )
    )
    def interrupt_fresh_advance(self, interrupted_write: tuple[str, int]) -> None:
        session, key = self._new_request()
        operation, committed_events = interrupted_write
        ledger = self.capability.ledger
        assert isinstance(ledger, SQLiteLifecycleLedger)
        interrupted = Advance(
            ledger=InterruptingLedger(ledger, operation, "after"),
            configuration_version=self.capability.configuration_version,
            configuration_hash=self.capability.configuration_hash,
            universe_source=self.capability.universe_source,
            enabled_asset_classes=self.capability.enabled_asset_classes,
            universe_policy=self.capability.universe_policy,
            evidence_capture=self.capability.evidence_capture,
            attention_policy=self.capability.attention_policy,
            attention_inputs=self.capability.attention_inputs,
            clock=self.capability.clock,
            constitution_registry=self.capability.constitution_registry,
            production_research=self.capability.production_research,
            evidence_vault=self.capability.evidence_vault,
            memory=self.capability.memory,
            memory_refusal_ledger=self.capability.memory_refusal_ledger,
        )
        try:
            observed = interrupted(
                cycle=_cycle_payload(session),
                mode="champion",
                idempotency_key=key,
            )
        except SimulatedInterruptionError:
            self.reference.interrupt(
                session=session,
                key=key,
                committed_events=committed_events,
            )
        else:
            assert operation in ("dossier", "research", "memory")
            assert observed == self._record_research_refusal(
                session=session,
                key=key,
                observed=observed,
            )

    @rule()
    def reopen_database(self) -> None:
        self.capability = _configure(self.state_root, cio_stance="abstain")

    @invariant()
    def authoritative_counts_match_reference_model(self) -> None:
        assert (
            _authoritative_counts(self.state_root / "lifecycle.sqlite3") == self.reference.counts()
        )


TestLifecycleStateMachine = LifecycleStateMachine.TestCase


def test_state_machine_models_interrupted_future_attention_as_published() -> None:
    state = LifecycleStateMachine()
    try:
        state.interrupt_fresh_advance(("start", 1))
        state.interrupt_fresh_advance(("attention", ATTENTION_PUBLISHED_EVENT_COUNT))
        state.replay(0)
        state.authoritative_counts_match_reference_model()
    finally:
        state.teardown()


def test_state_machine_models_research_refusal_before_requested_interruption() -> None:
    state = LifecycleStateMachine()
    try:
        state.interrupt_fresh_advance(("attention", ATTENTION_PUBLISHED_EVENT_COUNT))
        state.interrupt_fresh_advance(("attention", ATTENTION_PUBLISHED_EVENT_COUNT))
        state.interrupt_fresh_advance(("dossier", 7))
        state.authoritative_counts_match_reference_model()
    finally:
        state.teardown()


def test_cio_abstention_resumes_after_research_checkpoint(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root, cio_stance="abstain")
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    interrupted = replace(
        capability,
        ledger=InterruptingLedger(ledger, "research", "after"),
    )

    with pytest.raises(SimulatedInterruptionError):
        interrupted(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="abstain-after-research",
        )

    receipt = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="abstain-after-research",
    )

    assert receipt.disposition is AdvanceDisposition.NO_ACTION
    assert receipt.recovery is AdvanceRecovery.RESUMED
    assert receipt.no_action_reason is NoActionReason.CIO_ABSTAINED
    assert len(_events(state_root / "lifecycle.sqlite3")) == PINNED_EVENT_COUNT


def test_resuming_an_older_partial_cycle_after_a_later_selection_fails_closed(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    interrupted = Advance(
        ledger=InterruptingLedger(ledger, "start", "after"),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        universe_source=capability.universe_source,
        enabled_asset_classes=capability.enabled_asset_classes,
        universe_policy=capability.universe_policy,
        evidence_capture=capability.evidence_capture,
        attention_policy=capability.attention_policy,
        attention_inputs=capability.attention_inputs,
        clock=capability.clock,
        constitution_registry=(capability.constitution_registry),
        production_research=capability.production_research,
        evidence_vault=capability.evidence_vault,
        memory=capability.memory,
        memory_refusal_ledger=capability.memory_refusal_ledger,
    )

    with pytest.raises(SimulatedInterruptionError):
        interrupted(
            cycle=_cycle_payload("2026-09-02"),
            mode="champion",
            idempotency_key="older-partial",
        )
    later = capability(
        cycle=_cycle_payload("2026-09-03"),
        mode="champion",
        idempotency_key="later-complete",
    )
    refused = capability(
        cycle=_cycle_payload("2026-09-02"),
        mode="champion",
        idempotency_key="older-partial",
    )
    replay = _configure(state_root)(
        cycle=_cycle_payload("2026-09-02"),
        mode="champion",
        idempotency_key="older-partial",
    )

    assert later.disposition is AdvanceDisposition.ADVANCED
    assert refused.disposition is AdvanceDisposition.FAILED_CLOSED
    assert refused.failure_reason is AdvanceFailureReason.ATTENTION_SELECTION_FAILED
    assert refused.attention_refusal_reason is AttentionRefusalReason.CONTRADICTORY_EVIDENCE
    assert refused.evidence_artifact_ids
    assert replay == refused
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM lifecycle_events WHERE idempotency_key = ?",
            ("older-partial",),
        ).fetchone() == (5,)
        assert connection.execute(
            """SELECT COUNT(*) FROM lifecycle_events
            WHERE idempotency_key = ? AND event_kind = 'attention_selected'""",
            ("older-partial",),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM advance_refusals WHERE idempotency_key = ?",
            ("older-partial",),
        ).fetchone() == (1,)


def test_resuming_after_published_attention_preserves_that_older_selection(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    interrupted = replace(
        capability,
        ledger=InterruptingLedger(ledger, "attention", "after"),
    )

    with pytest.raises(SimulatedInterruptionError):
        interrupted(
            cycle=_cycle_payload("2026-09-02"),
            mode="champion",
            idempotency_key="older-attention-published",
        )
    later = capability(
        cycle=_cycle_payload("2026-09-03"),
        mode="champion",
        idempotency_key="later-after-older-attention",
    )
    resumed = _configure(state_root)(
        cycle=_cycle_payload("2026-09-02"),
        mode="champion",
        idempotency_key="older-attention-published",
    )

    assert later.disposition is AdvanceDisposition.ADVANCED
    assert resumed.disposition is AdvanceDisposition.ADVANCED
    assert resumed.recovery is AdvanceRecovery.RESUMED
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM lifecycle_events WHERE idempotency_key = ?",
            ("older-attention-published",),
        ).fetchone() == (PINNED_EVENT_COUNT,)
        assert connection.execute(
            "SELECT COUNT(*) FROM advance_refusals WHERE idempotency_key = ?",
            ("older-attention-published",),
        ).fetchone() == (0,)


def test_conflicting_valid_idempotency_reuse_fails_without_shadowing_completed_work(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    completed = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="conflicting-valid-reuse",
    )

    conflict = capability(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="conflicting-valid-reuse",
    )
    conflict_replay = _configure(state_root)(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="conflicting-valid-reuse",
    )
    original_replay = _configure(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="conflicting-valid-reuse",
    )

    expected_conflict = AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=MarketSession(date(2026, 8, 22)),
    )
    assert conflict == expected_conflict
    assert conflict_replay == expected_conflict
    assert original_replay.disposition is completed.disposition
    assert original_replay.pinned_run_identity == completed.pinned_run_identity
    assert original_replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED
    assert len(_events(state_root / "lifecycle.sqlite3")) == PINNED_EVENT_COUNT
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT reason_code FROM advance_conflicts WHERE idempotency_key = ?",
            ("conflicting-valid-reuse",),
        ).fetchall() == [("idempotency_key_conflict",)]


def test_advance_rejects_a_second_initial_stream_for_the_same_session(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    original = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="first-key",
    )

    conflict = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="second-key",
    )
    original_replay = _configure(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="first-key",
    )
    conflict_replay = _configure(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="second-key",
    )

    assert conflict.disposition is AdvanceDisposition.FAILED_CLOSED
    assert conflict.completed_phase is None
    assert conflict.pinned_run_identity is None
    assert conflict.failure_reason == "session_stream_conflict"
    assert conflict_replay == conflict
    assert original_replay.disposition is original.disposition
    assert original_replay.pinned_run_identity == original.pinned_run_identity
    assert original_replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED
    assert (
        sum(kind == "advance_requested" for kind, _ in _events(state_root / "lifecycle.sqlite3"))
        == 1
    )


def test_malformed_cycle_refuses_before_state_and_valid_cycle_refusals_are_durable(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)

    refused = capability(
        cycle=_cycle_payload("not-a-session"),
        mode="champion",
        idempotency_key="invalid-request",
    )
    replay = _configure(state_root)(
        cycle=_cycle_payload("not-a-session"),
        mode="champion",
        idempotency_key="invalid-request",
    )

    assert refused == replay
    assert refused == AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_SESSION)
    assert _events(state_root / "lifecycle.sqlite3") == []
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    assert ledger.rebuild_status() == LifecycleStatus.not_started()
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM advance_conflicts").fetchone() == (0,)

    invalid_key = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="not valid",
    )
    invalid_key_replay = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="not valid",
    )
    another_invalid_key = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="also not valid",
    )
    later_invalid_key = capability(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="not valid",
    )
    assert invalid_key.failure_reason == "invalid_idempotency_key"
    assert invalid_key.cycle == MarketSession(date(2026, 8, 21))
    assert invalid_key_replay == invalid_key
    assert another_invalid_key == invalid_key
    assert later_invalid_key == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
        cycle=MarketSession(date(2026, 8, 22)),
    )
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM advance_refusals
            WHERE idempotency_key IS NULL AND reason_code = 'invalid_idempotency_key'
            """
        ).fetchone() == (2,)
    assert ledger.rebuild_status().durable_reason is AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY


def test_durable_refusal_replay_requires_the_exact_cycle_and_normalized_reason(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    original = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="invalid",
        idempotency_key="refusal-cycle-binding",
    )

    exact_replay = _configure(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="invalid",
        idempotency_key="refusal-cycle-binding",
    )
    changed_cycle = capability(
        cycle=_cycle_payload("2026-08-22"),
        mode="invalid",
        idempotency_key="refusal-cycle-binding",
    )
    corrected_mode = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="refusal-cycle-binding",
    )

    assert exact_replay == original
    assert changed_cycle == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=MarketSession(date(2026, 8, 22)),
    )
    assert corrected_mode == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=MarketSession(date(2026, 8, 21)),
    )
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM advance_conflicts").fetchone() == (0,)


@pytest.mark.parametrize(
    ("conflicting_session", "conflicting_mode", "expected_reason", "expected_conflicts"),
    [
        ("invalid", "champion", AdvanceFailureReason.INVALID_SESSION, 0),
        ("2026-08-21", "research-lab", AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT, 1),
    ],
)
def test_invalid_idempotency_reuse_fails_without_shadowing_completed_work(
    tmp_path: Path,
    conflicting_session: str,
    conflicting_mode: str,
    expected_reason: AdvanceFailureReason,
    expected_conflicts: int,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    completed = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="completed-key",
    )

    malformed_reuse = capability(
        cycle=_cycle_payload(conflicting_session),
        mode=conflicting_mode,
        idempotency_key="completed-key",
    )
    valid_replay = _configure(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="completed-key",
    )

    assert malformed_reuse == AdvanceReceipt.failed_closed(
        expected_reason,
        cycle=(
            MarketSession(date.fromisoformat(conflicting_session))
            if conflicting_session != "invalid"
            else None
        ),
    )
    assert valid_replay.disposition is completed.disposition
    assert valid_replay.pinned_run_identity == completed.pinned_run_identity
    assert valid_replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED
    with sqlite3.connect(state_root / "lifecycle.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM advance_refusals WHERE idempotency_key = 'completed-key'"
        ).fetchone() == (0,)
        assert (
            connection.execute(
                "SELECT reason_code FROM advance_conflicts WHERE idempotency_key = 'completed-key'"
            ).fetchall()
            == [("idempotency_key_conflict",)] * expected_conflicts
        )


def test_conflicting_pinned_identity_fails_without_rewriting_completed_work(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    completed = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="pinned-identity-conflict",
    )
    database = state_root / "lifecycle.sqlite3"
    conflicting_capability = Advance(
        ledger=SQLiteLifecycleLedger(database),
        configuration_version=capability.configuration_version,
        configuration_hash="f" * SHA256_HEX_LENGTH,
        universe_source=capability.universe_source,
        enabled_asset_classes=capability.enabled_asset_classes,
        universe_policy=capability.universe_policy,
        evidence_capture=capability.evidence_capture,
        attention_policy=capability.attention_policy,
        attention_inputs=capability.attention_inputs,
        clock=capability.clock,
        constitution_registry=(capability.constitution_registry),
        production_research=capability.production_research,
        evidence_vault=capability.evidence_vault,
        memory=capability.memory,
        memory_refusal_ledger=capability.memory_refusal_ledger,
    )

    conflict = conflicting_capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="pinned-identity-conflict",
    )
    original_replay = _configure(state_root)(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="pinned-identity-conflict",
    )

    assert conflict == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=MarketSession(date(2026, 8, 21)),
    )
    assert original_replay.disposition is completed.disposition
    assert original_replay.pinned_run_identity == completed.pinned_run_identity
    assert original_replay.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED
    assert len(_events(database)) == PINNED_EVENT_COUNT


def test_terminal_conflict_refusal_prevents_partial_stream_resumption(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    interrupted = Advance(
        ledger=InterruptingLedger(ledger, "start", "after"),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        universe_source=capability.universe_source,
        enabled_asset_classes=capability.enabled_asset_classes,
        universe_policy=capability.universe_policy,
        evidence_capture=capability.evidence_capture,
        attention_policy=capability.attention_policy,
        attention_inputs=capability.attention_inputs,
        clock=capability.clock,
        constitution_registry=(capability.constitution_registry),
        production_research=capability.production_research,
        evidence_vault=capability.evidence_vault,
        memory=capability.memory,
        memory_refusal_ledger=capability.memory_refusal_ledger,
    )
    with pytest.raises(SimulatedInterruptionError):
        interrupted(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="partial-conflict",
        )

    refused = capability(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="partial-conflict",
    )
    replay = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="partial-conflict",
    )

    assert refused == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=MarketSession(date(2026, 8, 22)),
    )
    assert replay == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=MarketSession(date(2026, 8, 21)),
    )
    assert _events(state_root / "lifecycle.sqlite3") == [("advance_requested", None)]


@pytest.mark.parametrize(
    ("event_kind", "committed_events", "expected_recovery"),
    [
        ("advance_requested", 0, AdvanceRecovery.FRESH),
        ("phase_completed", 1, AdvanceRecovery.RESUMED),
        ("run_inputs_pinned", 2, AdvanceRecovery.RESUMED),
        ("universe_snapshotted", 3, AdvanceRecovery.RESUMED),
        ("evidence_captured", 4, AdvanceRecovery.RESUMED),
        ("attention_selected", 5, AdvanceRecovery.RESUMED),
        ("dossiers_built", 6, AdvanceRecovery.RESUMED),
        ("research_run", 7, AdvanceRecovery.RESUMED),
        ("memory_updated", 8, AdvanceRecovery.RESUMED),
    ],
)
def test_advance_resumes_after_each_checkpoint_transaction_rolls_back(
    tmp_path: Path,
    event_kind: str,
    committed_events: int,
    expected_recovery: AdvanceRecovery,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(ROLLBACK_TRIGGERS[event_kind])

    with pytest.raises(LifecyclePersistenceError, match="SQLite lifecycle checkpoint failed"):
        capability(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="rollback-key",
        )

    assert len(_events(database)) == committed_events
    with sqlite3.connect(database) as connection:
        trigger_name = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'fail_%'"
        ).fetchone()
        assert trigger_name is not None
        known_drop_statements = {
            "fail_advance_requested_before_insert": (
                "DROP TRIGGER fail_advance_requested_before_insert"
            ),
            "fail_phase_completed_before_insert": "DROP TRIGGER fail_phase_completed_before_insert",
            "fail_run_inputs_pinned_before_insert": (
                "DROP TRIGGER fail_run_inputs_pinned_before_insert"
            ),
            "fail_universe_snapshotted_before_insert": (
                "DROP TRIGGER fail_universe_snapshotted_before_insert"
            ),
            "fail_evidence_captured_before_insert": (
                "DROP TRIGGER fail_evidence_captured_before_insert"
            ),
            "fail_attention_selected_before_insert": (
                "DROP TRIGGER fail_attention_selected_before_insert"
            ),
            "fail_dossiers_built_before_insert": ("DROP TRIGGER fail_dossiers_built_before_insert"),
            "fail_research_run_before_insert": "DROP TRIGGER fail_research_run_before_insert",
            "fail_memory_updated_before_insert": ("DROP TRIGGER fail_memory_updated_before_insert"),
        }
        connection.execute(known_drop_statements[str(trigger_name[0])])

    disposition, phase, recovery, run_id = _advance_in_fresh_process(state_root, "rollback-key")

    assert disposition == AdvanceDisposition.ADVANCED.value
    assert phase == LifecyclePhase.UPDATE_MEMORY.value
    assert recovery == expected_recovery.value
    assert len(run_id) == SHA256_HEX_LENGTH
    assert len(_events(database)) == PINNED_EVENT_COUNT


@pytest.mark.parametrize(
    ("session", "mode", "idempotency_key", "reason"),
    [
        ("2026-08-21", "invalid", "rolled-back-refusal", AdvanceFailureReason.INVALID_MODE),
        (
            "2026-08-21",
            "champion",
            "not valid",
            AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
        ),
    ],
)
def test_refusal_write_rollback_is_not_observed_as_terminal(
    tmp_path: Path,
    session: str,
    mode: str,
    idempotency_key: str,
    reason: AdvanceFailureReason,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(ROLLBACK_TRIGGERS["advance_refusal"])

    with pytest.raises(LifecyclePersistenceError, match="SQLite lifecycle checkpoint failed"):
        capability(
            cycle=_cycle_payload(session),
            mode=mode,
            idempotency_key=idempotency_key,
        )

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone() == (0,)
        connection.execute("DROP TRIGGER fail_advance_refusal_before_insert")
    disposition, phase, recovery, failure_reason = _advance_in_fresh_process(
        state_root,
        idempotency_key,
        session=session,
        mode=mode,
    )

    assert disposition == AdvanceDisposition.FAILED_CLOSED.value
    assert phase == ""
    assert recovery == ""
    assert failure_reason == reason.value
    assert _events(database) == []
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT reason_code FROM advance_refusals").fetchall() == [
            (reason.value,)
        ]


def test_completed_conflict_write_rollback_is_not_observed_as_terminal(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="rolled-back-conflict",
    )
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(ROLLBACK_TRIGGERS["advance_conflict"])

    with pytest.raises(LifecyclePersistenceError, match="SQLite lifecycle checkpoint failed"):
        capability(
            cycle=_cycle_payload("2026-08-22"),
            mode="champion",
            idempotency_key="rolled-back-conflict",
        )

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM advance_conflicts").fetchone() == (0,)
        connection.execute("DROP TRIGGER fail_advance_conflict_before_insert")
    disposition, phase, recovery, failure_reason = _advance_in_fresh_process(
        state_root,
        "rolled-back-conflict",
        session="2026-08-22",
    )

    assert disposition == AdvanceDisposition.FAILED_CLOSED.value
    assert phase == ""
    assert recovery == ""
    assert failure_reason == AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT.value
    assert len(_events(database)) == PINNED_EVENT_COUNT
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT reason_code FROM advance_conflicts").fetchall() == [
            (AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT.value,)
        ]


def test_runtime_state_root_cannot_point_into_source_directories(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    source = repository / "src"
    source.mkdir(parents=True)
    (repository / ".gitignore").write_text("/var/\n", encoding="utf-8")

    configured = configure_advance(
        (
            ConfigurationSource(
                "test",
                runtime_configuration(source / "runtime"),
            ),
        ),
        repository_root=repository,
        recorded_universe=recorded_universe(),
        recorded_evidence=production_recorded_evidence(),
        recorded_model=ValidProductionModel(),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(datetime(2026, 8, 21, 22, 0, tzinfo=UTC)),
    )

    assert configured == ConfigurationRefusal(
        code=ConfigurationRefusalCode.INVALID_STATE_ROOT,
        fields=("state_root",),
    )
    assert not (source / "runtime").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maximum_snapshot_age_seconds", 10**100),
        ("schema_version", 1.0),
        ("data_regime", _EqualityBombString("alpaca-basic-iex-v1")),
        (
            "etf_allowlist",
            [EquityInstrumentIdentity("alpaca", "equity-spy", "ARCA").to_payload()],
        ),
    ],
    ids=[
        "unrepresentable_duration",
        "non_integer_schema_lookalike",
        "hostile_string_subclass",
        "environment_free_catalog_namespace",
    ],
)
def test_malformed_universe_policy_is_refused_before_state_creation(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    state_root = tmp_path / "runtime"
    configuration = runtime_configuration(state_root)
    policy = configuration["universe_policy"]
    assert isinstance(policy, dict)
    policy[field] = value

    configured = configure_advance(
        (ConfigurationSource("test", configuration),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=production_recorded_evidence(),
        recorded_model=ValidProductionModel(),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(datetime(2026, 8, 21, 22, 0, tzinfo=UTC)),
    )

    assert configured == ConfigurationRefusal(
        code=ConfigurationRefusalCode.INVALID_UNIVERSE_POLICY,
        fields=("universe_policy",),
    )
    assert not state_root.exists()


def test_hostile_asset_activation_is_refused_before_state_creation(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    configuration = runtime_configuration(state_root)
    configuration["enabled_asset_classes"] = [_EqualityBombString("us_equity")]

    configured = configure_advance(
        (ConfigurationSource("test", configuration),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=production_recorded_evidence(),
        recorded_model=ValidProductionModel(),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(datetime(2026, 8, 21, 22, 0, tzinfo=UTC)),
    )

    assert configured == ConfigurationRefusal(
        code=ConfigurationRefusalCode.INVALID_ENABLED_ASSET_CLASSES,
        fields=("enabled_asset_classes",),
    )
    assert not state_root.exists()


def test_hostile_top_level_configuration_key_is_refused_before_state_creation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    configuration = runtime_configuration(state_root)
    schema_version = configuration.pop("schema_version")
    configuration[_EqualityBombString("schema_version")] = schema_version

    configured = configure_advance(
        (ConfigurationSource("test", configuration),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=production_recorded_evidence(),
        recorded_model=ValidProductionModel(),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(datetime(2026, 8, 21, 22, 0, tzinfo=UTC)),
    )

    assert configured == ConfigurationRefusal(ConfigurationRefusalCode.UNKNOWN_FIELD)
    assert not state_root.exists()


def test_runtime_state_storage_refuses_links_public_modes_and_unsafe_shapes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target, target_is_directory=True)
    assert isinstance(prepare_runtime_database(root_link), RuntimeRootRefusal)

    root_file = tmp_path / "root-file"
    root_file.write_text("not a directory", encoding="utf-8")

    public_root = tmp_path / "public-root"
    public_root.mkdir(mode=0o755)

    database_link_root = tmp_path / "database-link-root"
    database_link_root.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    database_target = tmp_path / "database-target"
    database_target.write_text("", encoding="utf-8")
    (database_link_root / "lifecycle.sqlite3").symlink_to(database_target)

    database_directory_root = tmp_path / "database-directory-root"
    database_directory_root.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    (database_directory_root / "lifecycle.sqlite3").mkdir()

    public_database_root = tmp_path / "public-database-root"
    public_database_root.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    public_database = public_database_root / "lifecycle.sqlite3"
    public_database.write_text("", encoding="utf-8")
    public_database.chmod(0o644)

    unsafe_roots = (
        root_link,
        root_file,
        public_root,
        database_link_root,
        database_directory_root,
        public_database_root,
        tmp_path / "missing-parent" / "runtime",
    )
    for state_root in unsafe_roots:
        configured = configure_advance(
            (
                ConfigurationSource(
                    "test",
                    runtime_configuration(state_root),
                ),
            ),
            repository_root=REPOSITORY_ROOT,
            recorded_universe=recorded_universe(),
            recorded_evidence=production_recorded_evidence(),
            recorded_model=ValidProductionModel(),
            session_eligibility=RecordedSessionEligibility(),
            clock=FixedClock(datetime(2026, 8, 21, 22, 0, tzinfo=UTC)),
        )
        assert configured == ConfigurationRefusal(
            code=ConfigurationRefusalCode.INVALID_STATE_ROOT,
            fields=("state_root",),
        ), state_root


def test_default_clock_is_aware() -> None:
    instant = SystemClock().now()

    assert instant.tzinfo is UTC


@pytest.mark.parametrize(
    "instant",
    [
        datetime(2026, 8, 21, 22, 0, tzinfo=UTC),
        datetime(2026, 8, 21, 18, 0, tzinfo=timezone(-timedelta(hours=4))),
    ],
    ids=("utc", "non-utc-offset"),
)
def test_equivalent_clock_offsets_persist_identically_after_reopen(
    tmp_path: Path,
    instant: datetime,
) -> None:
    state_root = tmp_path / "runtime"
    configured = configure_advance(
        (ConfigurationSource("test", runtime_configuration(state_root)),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=production_recorded_evidence(),
        recorded_model=ValidProductionModel(),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(instant),
    )
    assert isinstance(configured, Advance)

    configured(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="normalized-clock",
    )

    database = state_root / "lifecycle.sqlite3"
    reopened = SQLiteLifecycleLedger.open_existing(database)
    reopened.rebuild_status()
    with sqlite3.connect(database) as connection:
        timestamps = connection.execute(
            "SELECT DISTINCT recorded_at FROM lifecycle_events"
        ).fetchall()
    assert timestamps == [("2026-08-21T22:00:00.000000+00:00",)]


def test_lifecycle_ledger_applies_domain_steps_idempotently(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    parsed = AdvanceRequest.parse(
        session="2026-08-24",
        mode="champion",
        idempotency_key="adapter-idempotency",
    )
    assert isinstance(parsed, AdvanceRequest)
    now = UtcInstant.from_datetime(datetime(2026, 8, 22, 22, 0, tzinfo=UTC))

    command = advance_command(parsed)
    started = ledger.advance_step(command, AdvanceAttempt(), now)
    assert isinstance(started, AppendLifecycleRecord)
    assert isinstance(started.record, LifecycleEvent)
    assert started.record.sequence == 0

    conflicting = AdvanceRequest.parse(
        session="2026-08-25",
        mode="champion",
        idempotency_key="adapter-idempotency",
    )
    assert isinstance(conflicting, AdvanceRequest)
    conflict_command = advance_command(conflicting)
    partial_conflict = ledger.advance_step(conflict_command, AdvanceAttempt(), now)
    assert isinstance(partial_conflict, AppendTerminalLifecycleRecord)
    assert isinstance(partial_conflict.record, DurableAdvanceRefusal)
    assert partial_conflict.receipt == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=conflicting.session,
    )
    replay = ledger.advance_step(conflict_command, AdvanceAttempt(), now)
    assert replay == partial_conflict.receipt
    assert ledger.advance_step(command, AdvanceAttempt(), now) == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=parsed.session,
    )
    assert not hasattr(ledger, "start")
    assert not hasattr(ledger, "complete_reconciliation")
    assert not hasattr(ledger, "pin_run_inputs")
    assert not hasattr(ledger, "record_refusal")


def test_lifecycle_ledger_rejects_a_kernel_record_with_a_different_write_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _configure(tmp_path / "runtime")
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    request = AdvanceRequest.parse(
        session="2026-08-24",
        mode="champion",
        idempotency_key="mismatched-kernel-record-time",
    )
    assert isinstance(request, AdvanceRequest)
    command = advance_command(request)
    write_time = UtcInstant.from_datetime(datetime(2026, 8, 22, 22, 0, tzinfo=UTC))
    mismatched_time = UtcInstant.from_datetime(datetime(2026, 8, 22, 22, 1, tzinfo=UTC))
    record = LifecycleEvent(
        request.stream_id,
        0,
        request,
        command.pinned_run_identity,
        LifecycleEventKind.ADVANCE_REQUESTED,
        None,
        mismatched_time,
    )
    decision = AppendLifecycleRecord(
        record,
        AdvanceAttempt(AdvanceRecovery.FRESH, record.sequence),
    )
    monkeypatch.setattr(
        "agentic_investment_os.adapters.sqlite_lifecycle.decide_advance",
        lambda _history, _candidate, _attempt, _recorded_at: decision,
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream checkpoint order is invalid",
    ):
        ledger.advance_step(command, AdvanceAttempt(), write_time)


def test_pinned_constitution_reader_rejects_an_ambiguous_kernel_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _configure(tmp_path / "runtime")
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    request = AdvanceRequest.parse(
        session="2026-08-24",
        mode="champion",
        idempotency_key="ambiguous-constitution-pin",
    )
    assert isinstance(request, AdvanceRequest)
    now = UtcInstant.from_datetime(datetime(2026, 8, 22, 22, 0, tzinfo=UTC))
    command = advance_command(request)
    started = ledger.advance_step(command, AdvanceAttempt(), now)
    assert isinstance(started, AppendLifecycleRecord)
    pinned = ConstitutionUse(request.session, ACTIVE_CONSTITUTION.reference, now)
    monkeypatch.setattr(
        "agentic_investment_os.adapters.sqlite_lifecycle.reconstruct_constitution_uses",
        lambda _history: (pinned, pinned),
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream checkpoint order is invalid",
    ):
        ledger.pinned_constitution_use(request.idempotency_key)


def test_pinned_constitution_reader_rejects_a_substituted_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _configure(tmp_path / "runtime")
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    request = AdvanceRequest.parse(
        session="2026-08-24",
        mode="champion",
        idempotency_key="expected-constitution-pin",
    )
    substituted = AdvanceRequest.parse(
        session="2026-08-25",
        mode="champion",
        idempotency_key="substituted-constitution-pin",
    )
    assert isinstance(request, AdvanceRequest)
    assert isinstance(substituted, AdvanceRequest)
    now = UtcInstant.from_datetime(datetime(2026, 8, 22, 22, 0, tzinfo=UTC))
    substituted_event = LifecycleEvent(
        substituted.stream_id,
        0,
        substituted,
        advance_command(substituted).pinned_run_identity,
        LifecycleEventKind.ADVANCE_REQUESTED,
        None,
        now,
    )
    monkeypatch.setattr(
        "agentic_investment_os.adapters.sqlite_lifecycle._load_events",
        lambda _connection, **_filters: [substituted_event],
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle stream checkpoint order is invalid",
    ):
        ledger.pinned_constitution_use(request.idempotency_key)


def test_lifecycle_ledger_appends_generic_checkpoint_records(tmp_path: Path) -> None:
    capability = _configure(tmp_path / "runtime")
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    request = AdvanceRequest.parse(
        session="2026-08-26",
        mode="champion",
        idempotency_key="adapter-idempotency-resumable",
    )
    assert isinstance(request, AdvanceRequest)
    identity = pinned_run_identity(request, configuration_hash="c" * SHA256_HEX_LENGTH)
    now = UtcInstant.from_datetime(datetime(2026, 8, 22, 22, 0, tzinfo=UTC))

    snapshot = universe_snapshot(identity)
    capture = evidence_capture_checkpoint()
    command = AdvanceCommand(request, identity, snapshot)
    attempt = AdvanceAttempt()
    terminal: AppendTerminalLifecycleRecord | None = None
    for expected_sequence in range(PINNED_EVENT_COUNT):
        decision = ledger.advance_step(command, attempt, now)
        if isinstance(decision, PerformEvidenceCapture):
            command = replace(command, evidence_capture=capture)
            decision = ledger.advance_step(command, attempt, now)
        if isinstance(decision, PerformAttentionSelection):
            command = replace(
                command,
                attention_selection=attention_artifact(
                    identity,
                    snapshot,
                    capture,
                    decision.attention_history,
                    available_at=now,
                ),
            )
            decision = ledger.advance_step(command, attempt, now)
        if isinstance(decision, PerformDossierBuild):
            command = replace(command, dossier_build=DOSSIER_CHECKPOINT)
            decision = ledger.advance_step(command, attempt, now)
        if isinstance(decision, PerformResearch):
            command = replace(command, research_run=RESEARCH_CHECKPOINT)
            decision = ledger.advance_step(command, attempt, now)
        if isinstance(decision, PerformMemoryUpdate):
            command = replace(command, memory_update=MEMORY_CHECKPOINT)
            decision = ledger.advance_step(command, attempt, now)
        assert not isinstance(decision, AdvanceReceipt)
        assert not isinstance(decision, PerformEvidenceCapture)
        assert not isinstance(decision, PerformAttentionSelection)
        assert not isinstance(decision, PerformDossierBuild)
        assert not isinstance(decision, PerformResearch)
        assert not isinstance(decision, PerformMemoryUpdate)
        assert isinstance(decision.record, LifecycleEvent)
        assert decision.record.sequence == expected_sequence
        if isinstance(decision, AppendTerminalLifecycleRecord):
            terminal = decision
        else:
            attempt = decision.attempt
    assert terminal is not None
    assert terminal.receipt == AdvanceReceipt.advanced(
        identity,
        snapshot,
        AdvanceRecovery.FRESH,
        now,
        capture,
        attention_artifact(identity, snapshot, capture, available_at=now),
        DOSSIER_CHECKPOINT,
        RESEARCH_CHECKPOINT,
        MEMORY_CHECKPOINT,
        None,
    )
    assert ledger.advance_step(command, AdvanceAttempt(), now) == AdvanceReceipt.advanced(
        identity,
        snapshot,
        AdvanceRecovery.PREVIOUSLY_COMPLETED,
        now,
        capture,
        attention_artifact(identity, snapshot, capture, available_at=now),
        DOSSIER_CHECKPOINT,
        RESEARCH_CHECKPOINT,
        MEMORY_CHECKPOINT,
        None,
    )

    conflicting_identity = pinned_run_identity(
        request,
        configuration_hash="d" * SHA256_HEX_LENGTH,
    )
    concurrent_conflict = ledger.advance_step(
        AdvanceCommand(request, conflicting_identity, universe_snapshot(conflicting_identity)),
        AdvanceAttempt(),
        now,
    )
    assert isinstance(concurrent_conflict, AppendTerminalLifecycleRecord)
    assert isinstance(concurrent_conflict.record, DurableAdvanceConflict)
    assert concurrent_conflict.receipt == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=request.session,
    )
    assert (
        ledger.advance_step(
            AdvanceCommand(request, conflicting_identity, universe_snapshot(conflicting_identity)),
            AdvanceAttempt(),
            now,
        )
        == concurrent_conflict.receipt
    )


def test_lifecycle_ledger_rejects_an_untyped_datetime_before_append(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    request = AdvanceRequest.parse(
        session="2026-08-26",
        mode="champion",
        idempotency_key="adapter-invalid-instant",
    )
    assert isinstance(request, AdvanceRequest)

    with pytest.raises(
        LifecyclePersistenceError,
        match="recorded_at must use canonical UTC format",
    ):
        ledger.advance_step(
            advance_command(request),
            AdvanceAttempt(),
            datetime(2026, 8, 22, 22, 0),  # noqa: DTZ001
        )

    assert _authoritative_counts(state_root / "lifecycle.sqlite3") == (0, 0, 0)


def test_lifecycle_ledger_rejects_a_forged_utc_instant_before_append(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    request = AdvanceRequest.parse(
        session="2026-08-26",
        mode="champion",
        idempotency_key="adapter-forged-instant",
    )
    assert isinstance(request, AdvanceRequest)
    forged = _forged_utc_instant()

    with pytest.raises(
        LifecyclePersistenceError,
        match="recorded_at must use canonical UTC format",
    ):
        ledger.advance_step(advance_command(request), AdvanceAttempt(), forged)

    assert _authoritative_counts(state_root / "lifecycle.sqlite3") == (0, 0, 0)


def test_universe_source_rejects_a_forged_utc_instant_before_append(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    loaded = capability.universe_source.load()
    assert isinstance(loaded, UniverseInputs)
    hostile = Advance(
        ledger=capability.ledger,
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        universe_source=FixedUniverseSource(replace(loaded, evidence_cutoff=_forged_utc_instant())),
        enabled_asset_classes=capability.enabled_asset_classes,
        universe_policy=capability.universe_policy,
        evidence_capture=capability.evidence_capture,
        attention_policy=capability.attention_policy,
        attention_inputs=capability.attention_inputs,
        clock=capability.clock,
        constitution_registry=(capability.constitution_registry),
        production_research=capability.production_research,
        evidence_vault=capability.evidence_vault,
        memory=capability.memory,
        memory_refusal_ledger=capability.memory_refusal_ledger,
    )

    with pytest.raises(
        LifecyclePersistenceError,
        match="universe source returned a noncanonical absolute instant",
    ):
        hostile(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="forged-universe-instant",
        )

    assert _authoritative_counts(state_root / "lifecycle.sqlite3") == (0, 0, 0)


def test_naive_clock_cannot_create_a_checkpoint(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    configured = configure_advance(
        (ConfigurationSource("test", runtime_configuration(state_root)),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=production_recorded_evidence(),
        recorded_model=ValidProductionModel(),
        session_eligibility=RecordedSessionEligibility(),
        # Intentionally hostile clock value proves the boundary refuses naive time.
        clock=FixedClock(datetime(2026, 8, 21, 22, 0)),  # noqa: DTZ001
    )
    assert isinstance(configured, Advance)

    with pytest.raises(
        LifecyclePersistenceError,
        match="lifecycle clock must return a timezone-aware instant representable in UTC",
    ):
        configured(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="naive-clock",
        )

    assert _authoritative_counts(state_root / "lifecycle.sqlite3") == (0, 0, 0)


def test_out_of_range_aware_clock_cannot_create_a_checkpoint(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    configured = configure_advance(
        (ConfigurationSource("test", runtime_configuration(state_root)),),
        repository_root=REPOSITORY_ROOT,
        recorded_universe=recorded_universe(),
        recorded_evidence=production_recorded_evidence(),
        recorded_model=ValidProductionModel(),
        session_eligibility=RecordedSessionEligibility(),
        clock=FixedClock(datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=14)))),
    )
    assert isinstance(configured, Advance)

    with pytest.raises(
        LifecyclePersistenceError,
        match="lifecycle clock must return a timezone-aware instant representable in UTC",
    ):
        configured(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="out-of-range-clock",
        )

    assert _authoritative_counts(state_root / "lifecycle.sqlite3") == (0, 0, 0)


def test_append_only_tables_reject_rewrites(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="append-only",
    )
    capability(
        cycle=_cycle_payload("2026-08-23"),
        mode="invalid",
        idempotency_key="append-only-refusal",
    )
    capability(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="append-only",
    )
    database = state_root / "lifecycle.sqlite3"

    statements = (
        "UPDATE lifecycle_events SET mode = 'champion'",
        "DELETE FROM lifecycle_events",
        "UPDATE advance_refusals SET reason_code = 'changed'",
        "DELETE FROM advance_refusals",
        "UPDATE advance_conflicts SET reason_code = 'changed'",
        "DELETE FROM advance_conflicts",
    )
    for statement in statements:
        with (
            sqlite3.connect(database) as connection,
            pytest.raises(sqlite3.IntegrityError, match="append-only"),
        ):
            connection.execute(statement)


def test_sqlite_status_read_failures_are_translated(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE lifecycle_events")

    with pytest.raises(LifecyclePersistenceError, match="checkpoint failed"):
        ledger.rebuild_status()


def test_operational_read_failure_does_not_create_a_terminal_refusal(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE lifecycle_events")

    with pytest.raises(LifecyclePersistenceError, match="checkpoint failed"):
        capability(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="operational-read-failure",
        )

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone() == (0,)


def test_missing_refusal_sequence_row_fails_closed(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    hostile = Advance(
        ledger=MissingAggregateRowLedger(state_root / "lifecycle.sqlite3"),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        universe_source=capability.universe_source,
        enabled_asset_classes=capability.enabled_asset_classes,
        universe_policy=capability.universe_policy,
        evidence_capture=capability.evidence_capture,
        attention_policy=capability.attention_policy,
        attention_inputs=capability.attention_inputs,
        clock=capability.clock,
        constitution_registry=(capability.constitution_registry),
        production_research=capability.production_research,
        evidence_vault=capability.evidence_vault,
        memory=capability.memory,
        memory_refusal_ledger=capability.memory_refusal_ledger,
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="invalid idempotency_key in lifecycle refusal ledger",
    ):
        hostile(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="missing-aggregate-row",
        )


def test_invalid_refusal_sequence_row_has_a_bounded_diagnostic(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    hostile = Advance(
        ledger=InvalidAggregateRowLedger(state_root / "lifecycle.sqlite3"),
        configuration_version=capability.configuration_version,
        configuration_hash=capability.configuration_hash,
        universe_source=capability.universe_source,
        enabled_asset_classes=capability.enabled_asset_classes,
        universe_policy=capability.universe_policy,
        evidence_capture=capability.evidence_capture,
        attention_policy=capability.attention_policy,
        attention_inputs=capability.attention_inputs,
        clock=capability.clock,
        constitution_registry=(capability.constitution_registry),
        production_research=capability.production_research,
        evidence_vault=capability.evidence_vault,
        memory=capability.memory,
        memory_refusal_ledger=capability.memory_refusal_ledger,
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="invalid refusal_id in lifecycle ledger",
    ):
        hostile(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="invalid-aggregate-row",
        )


def test_terminal_refusal_replays_without_reading_unrelated_missing_history(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    refused = capability(
        cycle=_cycle_payload("invalid"),
        mode="champion",
        idempotency_key="terminal-before-missing-history",
    )
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE lifecycle_events")

    replay = capability(
        cycle=_cycle_payload("invalid"),
        mode="champion",
        idempotency_key="terminal-before-missing-history",
    )

    assert replay == refused


@pytest.mark.parametrize(
    ("reason_code", "recorded_at", "expected_message"),
    [
        (
            3,
            "2026-08-21T22:00:00.000000+00:00",
            "invalid reason_code in lifecycle ledger",
        ),
        (
            "x" * 5_000,
            "2026-08-21T22:00:00.000000+00:00",
            "unknown reason_code in lifecycle ledger",
        ),
        ("invalid_session", 3, "invalid recorded_at in lifecycle ledger"),
        (
            "invalid_session",
            "invalid",
            "recorded_at must use canonical UTC format",
        ),
        (
            "invalid_session",
            "2026-08-21T22:00:00",
            "recorded_at must use canonical UTC format",
        ),
        (
            "invalid_session",
            "2026-08-21T18:00:00.000000-04:00",
            "recorded_at must use canonical UTC format",
        ),
    ],
)
def test_corrupt_refusal_rows_fail_closed_with_a_bounded_diagnostic(
    tmp_path: Path,
    reason_code: object,
    recorded_at: object,
    expected_message: str,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="invalid",
        idempotency_key="corrupt-refusal",
    )
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT refusal_id, idempotency_key, cycle_identity, reason_code, "
            "evidence_policy_id, evidence_artifact_ids, evidence_refusal_ids, "
            "attention_refusal_reason, research_refusal_id, research_refusal, recorded_at "
            "FROM advance_refusals"
        ).fetchone()
        connection.execute("DROP TABLE advance_refusals")
        connection.execute(
            "CREATE TABLE advance_refusals "
            "(refusal_id, idempotency_key, cycle_identity, reason_code, "
            "evidence_policy_id, evidence_artifact_ids, evidence_refusal_ids, "
            "attention_refusal_reason, research_refusal_id, research_refusal, recorded_at)"
        )
        assert row is not None
        connection.execute(
            "INSERT INTO advance_refusals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row[0],
                row[1],
                row[2],
                reason_code,
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
                row[9],
                recorded_at,
            ),
        )

    with pytest.raises(LifecyclePersistenceError, match=expected_message):
        capability(
            cycle=_cycle_payload("2026-08-21"),
            mode="invalid",
            idempotency_key="corrupt-refusal",
        )


@pytest.mark.parametrize("corruption", ["reason_on_other_refusal", "unknown_reason"])
def test_corrupt_attention_refusal_reason_fails_closed(
    tmp_path: Path,
    corruption: str,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    cycle = json.dumps(
        MarketSession(date(2026, 8, 21)).to_payload(),
        sort_keys=True,
        separators=(",", ":"),
    )
    capture = evidence_capture_checkpoint()
    row: tuple[object, ...]
    if corruption == "reason_on_other_refusal":
        row = (
            1,
            "corrupt-attention-refusal",
            cycle,
            AdvanceFailureReason.INVALID_MODE.value,
            None,
            None,
            None,
            AttentionRefusalReason.MISSING_EVIDENCE.value,
            None,
            None,
            UtcInstant.from_datetime(RECORDED_AT).isoformat(),
        )
    else:
        row = (
            1,
            "corrupt-attention-refusal",
            cycle,
            AdvanceFailureReason.ATTENTION_SELECTION_FAILED.value,
            capture.policy_id,
            json.dumps(list(capture.artifact_ids), separators=(",", ":")),
            "[]",
            "unknown",
            None,
            None,
            UtcInstant.from_datetime(RECORDED_AT).isoformat(),
        )
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE advance_refusals")
        connection.execute(
            "CREATE TABLE advance_refusals "
            "(refusal_id, idempotency_key, cycle_identity, reason_code, "
            "evidence_policy_id, evidence_artifact_ids, evidence_refusal_ids, "
            "attention_refusal_reason, research_refusal_id, research_refusal, recorded_at)"
        )
        connection.execute(
            "INSERT INTO advance_refusals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    with pytest.raises(
        LifecyclePersistenceError,
        match="invalid idempotency_key in lifecycle refusal ledger",
    ):
        capability(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="corrupt-attention-refusal",
        )


def test_research_refusal_identity_on_an_unrelated_refusal_fails_closed(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    cycle = json.dumps(
        MarketSession(date(2026, 8, 21)).to_payload(),
        sort_keys=True,
        separators=(",", ":"),
    )
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE advance_refusals")
        connection.execute(
            "CREATE TABLE advance_refusals "
            "(refusal_id, idempotency_key, cycle_identity, reason_code, "
            "evidence_policy_id, evidence_artifact_ids, evidence_refusal_ids, "
            "attention_refusal_reason, research_refusal_id, research_refusal, recorded_at)"
        )
        connection.execute(
            "INSERT INTO advance_refusals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "corrupt-research-refusal",
                cycle,
                AdvanceFailureReason.INVALID_MODE.value,
                None,
                None,
                None,
                None,
                "f" * SHA256_HEX_LENGTH,
                None,
                UtcInstant.from_datetime(RECORDED_AT).isoformat(),
            ),
        )

    with pytest.raises(
        LifecyclePersistenceError,
        match="invalid idempotency_key in lifecycle refusal ledger",
    ):
        capability(
            cycle=_cycle_payload("2026-08-21"),
            mode="champion",
            idempotency_key="corrupt-research-refusal",
        )


@pytest.mark.parametrize(
    ("reason_code", "recorded_at", "expected_message"),
    [
        (
            "invalid_session",
            "2026-08-21T22:00:00.000000+00:00",
            "invalid conflict association",
        ),
        (
            "idempotency_key_conflict",
            "invalid",
            "recorded_at must use canonical UTC format",
        ),
    ],
)
def test_corrupt_completed_conflict_rows_append_a_durable_invalid_state_refusal(
    tmp_path: Path,
    reason_code: str,
    recorded_at: str,
    expected_message: str,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="corrupt-conflict",
    )
    capability(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="corrupt-conflict",
    )
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE advance_conflicts")
        connection.execute(
            "CREATE TABLE advance_conflicts (idempotency_key, reason_code, recorded_at)"
        )
        connection.execute(
            "INSERT INTO advance_conflicts VALUES (?, ?, ?)",
            ("corrupt-conflict", reason_code, recorded_at),
        )

    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    with pytest.raises(LifecyclePersistenceError, match=expected_message):
        ledger.rebuild_status()

    original_replay = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="corrupt-conflict",
    )
    conflicting_replay = capability(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="corrupt-conflict",
    )

    expected = AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=MarketSession(date(2026, 8, 21)),
    )
    assert original_replay == expected
    assert conflicting_replay == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.IDEMPOTENCY_KEY_CONFLICT,
        cycle=MarketSession(date(2026, 8, 22)),
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT reason_code FROM advance_refusals WHERE idempotency_key = ?",
            ("corrupt-conflict",),
        ).fetchall() == [("invalid_durable_state",)]


def test_orphan_completed_conflict_refuses_before_starting_a_stream(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO advance_conflicts VALUES (?, ?, ?)",
            (
                "orphan-conflict",
                "idempotency_key_conflict",
                "2026-08-21T22:00:00.000000+00:00",
            ),
        )

    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    with pytest.raises(
        LifecyclePersistenceError,
        match="invalid conflict association",
    ):
        ledger.rebuild_status()

    refused = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="orphan-conflict",
    )

    assert refused == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=MarketSession(date(2026, 8, 21)),
    )
    assert _events(database) == []
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT reason_code FROM advance_refusals WHERE idempotency_key = ?",
            ("orphan-conflict",),
        ).fetchall() == [("invalid_durable_state",)]


@pytest.mark.parametrize("operation", ["reconcile", "pin", "refuse"])
def test_corrupt_checkpoint_history_uses_the_call_timestamp_for_its_refusal(
    tmp_path: Path,
    operation: str,
) -> None:
    capability = _configure(tmp_path / "runtime")
    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    request = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key=f"corrupt-before-{operation}",
    )
    assert isinstance(request, AdvanceRequest)
    identity = pinned_run_identity(request)
    started_at = datetime(2026, 8, 21, 22, 0, tzinfo=UTC)
    refused_at = datetime(2026, 8, 22, 23, 30, tzinfo=UTC)
    command = AdvanceCommand(request, identity, universe_snapshot(identity))
    started = ledger.advance_step(
        command,
        AdvanceAttempt(),
        UtcInstant.from_datetime(started_at),
    )
    assert isinstance(started, AppendLifecycleRecord)
    if operation == "pin":
        reconciled = ledger.advance_step(
            command,
            started.attempt,
            UtcInstant.from_datetime(started_at),
        )
        assert isinstance(reconciled, AppendLifecycleRecord)
    database = tmp_path / "runtime" / "lifecycle.sqlite3"
    _replace_with_corrupt_events(database, (("UPDATE lifecycle_events SET mode = 'invalid'",)))

    refused = Advance(
        ledger=ledger,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
        universe_source=capability.universe_source,
        enabled_asset_classes=capability.enabled_asset_classes,
        universe_policy=capability.universe_policy,
        evidence_capture=capability.evidence_capture,
        attention_policy=capability.attention_policy,
        attention_inputs=capability.attention_inputs,
        clock=FixedClock(refused_at),
        constitution_registry=(capability.constitution_registry),
        production_research=capability.production_research,
        evidence_vault=capability.evidence_vault,
        memory=capability.memory,
        memory_refusal_ledger=capability.memory_refusal_ledger,
    )
    receipt = refused(
        cycle=_cycle_payload(request.session.isoformat()),
        mode="research-lab" if operation == "refuse" else request.mode.value,
        idempotency_key=request.idempotency_key.value,
    )

    assert receipt == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=request.session,
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT idempotency_key, reason_code, recorded_at FROM advance_refusals"
        ).fetchall() == [
            (
                request.idempotency_key.value,
                AdvanceFailureReason.INVALID_DURABLE_STATE.value,
                UtcInstant.from_datetime(refused_at).isoformat(),
            )
        ]


@pytest.mark.parametrize("corruption", CORRUPTIONS)
def test_advance_fails_closed_on_corrupt_durable_rows(
    tmp_path: Path,
    corruption: str,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="corrupt-stream",
    )
    database = state_root / "lifecycle.sqlite3"
    statements, expected_message = CORRUPTIONS[corruption]
    _replace_with_corrupt_events(database, statements)

    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    with pytest.raises(LifecyclePersistenceError, match=expected_message):
        ledger.rebuild_status()

    refused = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="corrupt-stream",
    )
    replay = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="corrupt-stream",
    )

    assert refused == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=MarketSession(date(2026, 8, 21)),
    )
    assert replay == refused
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT reason_code FROM advance_refusals WHERE idempotency_key = ?",
            ("corrupt-stream",),
        ).fetchall() == [("invalid_durable_state",)]


@pytest.mark.parametrize(
    "snapshot_json",
    ["9" * 5_000, "[" * 2_000 + "]" * 2_000],
    ids=["oversized_integer", "excessive_nesting"],
)
def test_extreme_snapshot_json_fails_closed_for_status_and_advance(
    tmp_path: Path,
    snapshot_json: str,
) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="extreme-snapshot-json",
    )
    database = state_root / "lifecycle.sqlite3"
    _replace_with_corrupt_events(database, (), universe_snapshot=snapshot_json)

    ledger = capability.ledger
    assert isinstance(ledger, SQLiteLifecycleLedger)
    with pytest.raises(
        LifecyclePersistenceError,
        match="lifecycle stream checkpoint order is invalid",
    ):
        ledger.rebuild_status()

    refused = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="extreme-snapshot-json",
    )

    assert refused == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=MarketSession(date(2026, 8, 21)),
    )


def test_corrupt_attention_history_fails_a_fresh_selection_closed(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="unrelated-corrupt-stream",
    )
    capability(
        cycle=_cycle_payload("2026-08-23"),
        mode="invalid",
        idempotency_key="unrelated-corrupt-refusal",
    )
    database = state_root / "lifecycle.sqlite3"
    _replace_with_corrupt_events(database, ("UPDATE lifecycle_events SET mode = 'invalid'",))
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT refusal_id, idempotency_key, cycle_identity, reason_code, "
            "evidence_policy_id, evidence_artifact_ids, evidence_refusal_ids, "
            "attention_refusal_reason, research_refusal_id, research_refusal, recorded_at "
            "FROM advance_refusals"
        ).fetchone()
        connection.execute("DROP TABLE advance_refusals")
        connection.execute(
            "CREATE TABLE advance_refusals "
            "(refusal_id, idempotency_key, cycle_identity, reason_code, "
            "evidence_policy_id, evidence_artifact_ids, evidence_refusal_ids, "
            "attention_refusal_reason, research_refusal_id, research_refusal, recorded_at)"
        )
        assert row is not None
        connection.execute(
            "INSERT INTO advance_refusals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row[0],
                row[1],
                row[2],
                "unknown",
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
                row[9],
                row[10],
            ),
        )

    receipt = capability(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="fresh-beside-corruption",
    )

    assert receipt.disposition is AdvanceDisposition.FAILED_CLOSED
    assert receipt.failure_reason is AdvanceFailureReason.INVALID_DURABLE_STATE
    assert receipt.recovery is None
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM lifecycle_events WHERE idempotency_key = ?",
            ("fresh-beside-corruption",),
        ).fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM advance_refusals").fetchone() == (2,)


def test_restart_does_not_globally_validate_unrelated_history(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO advance_conflicts (idempotency_key, reason_code, recorded_at)
            VALUES ('unrelated-orphan', 'idempotency_key_conflict', ?)
            """,
            (UtcInstant.from_datetime(datetime(2026, 8, 21, 10, tzinfo=UTC)).isoformat(),),
        )

    restarted = _configure(state_root)
    receipt = restarted(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="fresh-after-restart",
    )

    assert receipt.disposition is AdvanceDisposition.ADVANCED
    assert receipt.recovery is AdvanceRecovery.FRESH


def test_invalid_key_replays_without_reading_corrupt_unrelated_history(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="corrupt-before-invalid-key",
    )
    database = state_root / "lifecycle.sqlite3"
    _replace_with_corrupt_events(database, ("UPDATE lifecycle_events SET mode = 'invalid'",))

    refused = capability(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="not valid",
    )
    replay = capability(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="not valid",
    )

    expected = AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
        cycle=MarketSession(date(2026, 8, 22)),
    )
    assert refused == expected
    assert replay == expected
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT idempotency_key, reason_code FROM advance_refusals"
        ).fetchall() == [(None, AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY.value)]


def test_invalid_key_refuses_when_unrelated_history_tables_are_missing(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    database = state_root / "lifecycle.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE lifecycle_events")
        connection.execute("DROP TABLE advance_conflicts")

    refused = capability(
        cycle=_cycle_payload("2026-08-22"),
        mode="champion",
        idempotency_key="not valid",
    )

    assert refused == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY,
        cycle=MarketSession(date(2026, 8, 22)),
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT idempotency_key, reason_code FROM advance_refusals"
        ).fetchall() == [(None, AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY.value)]


def test_corrupt_request_appends_after_unrelated_refusal_sequence(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime"
    capability = _configure(state_root)
    capability(
        cycle=_cycle_payload("2026-08-23"),
        mode="invalid",
        idempotency_key="earlier-unrelated-refusal",
    )
    capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="corrupt-after-refusal",
    )
    database = state_root / "lifecycle.sqlite3"
    _replace_with_corrupt_events(database, ("UPDATE lifecycle_events SET mode = 'invalid'",))

    refused = capability(
        cycle=_cycle_payload("2026-08-21"),
        mode="champion",
        idempotency_key="corrupt-after-refusal",
    )

    assert refused == AdvanceReceipt.failed_closed(
        AdvanceFailureReason.INVALID_DURABLE_STATE,
        cycle=MarketSession(date(2026, 8, 21)),
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT refusal_id, idempotency_key, reason_code FROM advance_refusals "
            "ORDER BY refusal_id"
        ).fetchall() == [
            (1, "earlier-unrelated-refusal", AdvanceFailureReason.INVALID_MODE.value),
            (2, "corrupt-after-refusal", AdvanceFailureReason.INVALID_DURABLE_STATE.value),
        ]


def _replace_with_corrupt_events(
    database: Path,
    statements: tuple[str, ...],
    *,
    universe_snapshot: str | None = None,
) -> None:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT stream_id, sequence, idempotency_key, cycle_identity, mode,
                   configuration_version, configuration_hash,
                   research_policy_hash,
                   constitution_version, constitution_hash, run_id,
                   data_regime, evidence_cutoff, instrument_snapshot_hash,
                   position_snapshot_hash, eligibility_policy_hash,
                   event_kind, completed_phase, universe_snapshot_id,
                   universe_snapshot, evidence_policy_id,
                   evidence_artifact_ids, evidence_refusal_ids,
                   attention_artifact_id, attention_artifact,
                   research_checkpoint, no_action_reason,
                   event_envelope, recorded_at
            FROM lifecycle_events ORDER BY sequence
            """
        ).fetchall()
        connection.execute("DROP TABLE lifecycle_events")
        connection.execute(
            """
            CREATE TABLE lifecycle_events (
                stream_id, sequence, idempotency_key, cycle_identity, mode,
                configuration_version, configuration_hash,
                research_policy_hash,
                constitution_version, constitution_hash, run_id,
                data_regime, evidence_cutoff, instrument_snapshot_hash,
                position_snapshot_hash, eligibility_policy_hash,
                event_kind, completed_phase, universe_snapshot_id,
                universe_snapshot, evidence_policy_id,
                evidence_artifact_ids, evidence_refusal_ids,
                attention_artifact_id, attention_artifact,
                research_checkpoint, no_action_reason,
                event_envelope, recorded_at
            )
            """
        )
        connection.executemany(
            "INSERT INTO lifecycle_events VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        for statement in statements:
            connection.execute(statement)
        if universe_snapshot is not None:
            connection.execute(
                "UPDATE lifecycle_events SET universe_snapshot = ? "
                "WHERE event_kind = 'run_inputs_pinned'",
                (universe_snapshot,),
            )
