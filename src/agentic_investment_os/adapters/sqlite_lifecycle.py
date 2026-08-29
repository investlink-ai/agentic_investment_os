"""Persist domain-selected lifecycle records in a private SQLite database."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Self, TypeVar, assert_never

from agentic_investment_os.adapters.recorded_universe import parse_persisted_universe_snapshot
from agentic_investment_os.domain.attention import (
    AttentionArtifact,
    AttentionRefusalReason,
    parse_attention_artifact,
)
from agentic_investment_os.domain.governance import (
    ApprovalVerification,
    ConstitutionActivation,
    ConstitutionArtifact,
    ConstitutionGovernanceHistory,
    ConstitutionGovernanceState,
    ConstitutionGovernanceStatus,
    ConstitutionReference,
    ConstitutionUse,
    GovernanceCommand,
    GovernanceEvent,
    GovernanceReceipt,
    GovernanceStateError,
    OperatorApprovalProof,
    OperatorApprovalVerifier,
    activate_constitution,
    constitution_for_use,
    decide_governance,
    parse_governance_event,
    validate_constitution_uses,
)
from agentic_investment_os.domain.identity import (
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
    DurableAdvanceConflict,
    DurableAdvanceRefusal,
    EvidenceCaptureCheckpoint,
    EvidenceCaptureReference,
    IdempotencyKey,
    InputRefusal,
    InvalidLifecycleStateError,
    LifecycleCheckpoint,
    LifecycleCommand,
    LifecycleDecision,
    LifecycleEvent,
    LifecycleEventKind,
    LifecycleHistory,
    LifecyclePersistenceError,
    LifecycleRecord,
    LifecycleStatus,
    NoActionReason,
    PerformAttentionSelection,
    PerformDossierBuild,
    PerformEvidenceCapture,
    PerformMemoryUpdate,
    PerformPortfolioConstruction,
    PerformResearch,
    PinnedRunIdentity,
    PortfolioCheckpoint,
    PortfolioCheckpointReference,
    ProductionResearchReference,
    ResearchCheckpoint,
    ResearchRefusal,
    decide_advance,
    decide_evidence_refusal_replay,
    decide_invalid_history,
    decide_terminal_refusal,
    derive_lifecycle_status,
    is_sha256,
    parse_lifecycle_checkpoint,
    parse_portfolio_checkpoint,
    parse_research_checkpoint,
    parse_research_refusal,
    reconstruct_constitution_uses,
    reconstruct_evidence_checkpoints,
    reconstruct_memory_event_ids,
    reconstruct_portfolio_checkpoints,
    reconstruct_production_research_checkpoints,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.domain.universe import (
    UniverseRefusal,
    UniverseSnapshot,
    is_data_regime,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = (
    "PreparedRuntimeDatabase",
    "RuntimeRootRefusal",
    "SQLiteConstitutionGovernance",
    "SQLiteLifecycleLedger",
    "open_runtime_database",
    "prepare_runtime_database",
)

_DATABASE_NAME = "lifecycle.sqlite3"
_T = TypeVar("_T")
_PRIVATE_DATABASE_FLAGS = os.O_CREAT | os.O_EXCL | os.O_WRONLY
_STREAM_EXISTS_SQL = "SELECT 1 FROM lifecycle_events WHERE stream_id = ? LIMIT 1"
_NEXT_REFUSAL_SEQUENCE_SQL = "SELECT COALESCE(MAX(refusal_id), 0) + 1 FROM advance_refusals"
_ENABLE_FOREIGN_KEYS_SQL = "PRAGMA foreign_keys = ON"
_BUSY_TIMEOUT_SQL = "PRAGMA busy_timeout = 5000"
_BEGIN_IMMEDIATE_SQL = "BEGIN IMMEDIATE"
_DROP_PROJECTION_SQL = (
    "DROP TABLE IF EXISTS lifecycle_status_projection"
    # SQLite keywords and identifiers are case-insensitive, so case-only mutants are equivalent.
)
_DROP_BELIEF_GRAPH_PROJECTION_SQL = "DROP TABLE IF EXISTS belief_graph_projection"
_USER_VERSION_SQL = "PRAGMA user_version"
_USER_SCHEMA_OBJECT_EXISTS_SQL = """
SELECT EXISTS (
    SELECT 1 FROM sqlite_schema
    WHERE name NOT LIKE 'sqlite!_%' ESCAPE '!'
)
"""
_INTEGRITY_CHECK_SQL = "PRAGMA integrity_check"
_INTEGRITY_SAVEPOINT_SQL = "SAVEPOINT validate_without_projection"
_ROLLBACK_INTEGRITY_SAVEPOINT_SQL = "ROLLBACK TO validate_without_projection"
_RELEASE_INTEGRITY_SAVEPOINT_SQL = "RELEASE validate_without_projection"
_CHECKPOINT_FAILED = "SQLite lifecycle checkpoint failed"
_DATABASE_INITIALIZATION_FAILED = "SQLite database initialization failed"
_UNSUPPORTED_DATABASE_VERSION = "unsupported SQLite database version"
_SCHEMA_VERSION_MISMATCH = "SQLite schema does not match database version"
_DATABASE_INTEGRITY_FAILED = "SQLite database integrity check failed"
_INVALID_REQUEST = "invalid request values in lifecycle ledger"
_UNKNOWN_REASON_CODE = "unknown reason_code in lifecycle ledger"
_INVALID_REFUSAL_KEY = "invalid idempotency_key in lifecycle refusal ledger"
_INVALID_CONFLICT_KEY = "invalid idempotency_key in lifecycle conflict ledger"
_RECORDED_AT_NOT_CANONICAL = "recorded_at must use canonical UTC format"
_INVALID_CHECKPOINT_ORDER = "lifecycle stream checkpoint order is invalid"
_INVALID_DATA_REGIME = "invalid data_regime in lifecycle ledger"
_FUTURE_EVIDENCE_CUTOFF = "evidence_cutoff cannot be later than recorded_at"
_INVALID_GOVERNANCE_HISTORY = "invalid Constitution governance history"
_MISSING_APPROVAL_VERIFIER = "operator approval verifier required for governance history"

_CURRENT_SCHEMA = (
    """
CREATE TABLE lifecycle_events (
    stream_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    cycle_identity TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode = 'champion'),
    configuration_version INTEGER NOT NULL CHECK (configuration_version = 1),
    configuration_hash TEXT NOT NULL CHECK (length(configuration_hash) = 64),
    research_policy_hash TEXT NOT NULL CHECK (length(research_policy_hash) = 64),
    constitution_version INTEGER NOT NULL CHECK (constitution_version >= 1),
    constitution_hash TEXT NOT NULL CHECK (length(constitution_hash) = 64),
    run_id TEXT NOT NULL CHECK (length(run_id) = 64),
    data_regime TEXT NOT NULL,
    evidence_cutoff TEXT NOT NULL,
    instrument_snapshot_hash TEXT NOT NULL CHECK (length(instrument_snapshot_hash) = 64),
    position_snapshot_hash TEXT NOT NULL CHECK (length(position_snapshot_hash) = 64),
    eligibility_policy_hash TEXT NOT NULL CHECK (length(eligibility_policy_hash) = 64),
    portfolio_policy_hash TEXT NOT NULL CHECK (length(portfolio_policy_hash) = 64),
    portfolio_input_hash TEXT NOT NULL CHECK (length(portfolio_input_hash) = 64),
    event_kind TEXT NOT NULL CHECK (
        event_kind IN (
            'advance_requested', 'phase_completed', 'run_inputs_pinned',
            'universe_snapshotted', 'evidence_captured', 'attention_selected',
            'dossiers_built', 'research_run', 'memory_updated',
            'portfolio_constructed'
        )
    ),
    completed_phase TEXT,
    universe_snapshot_id TEXT CHECK (
        universe_snapshot_id IS NULL OR length(universe_snapshot_id) = 64
    ),
    universe_snapshot TEXT,
    evidence_policy_id TEXT CHECK (
        evidence_policy_id IS NULL OR length(evidence_policy_id) = 64
    ),
    evidence_artifact_ids TEXT,
    evidence_refusal_ids TEXT,
    attention_artifact_id TEXT CHECK (
        attention_artifact_id IS NULL OR length(attention_artifact_id) = 64
    ),
    attention_artifact TEXT,
    research_checkpoint TEXT,
    portfolio_checkpoint TEXT,
    no_action_reason TEXT CHECK (
        no_action_reason IS NULL OR no_action_reason IN (
            'no_attention', 'no_valid_thesis', 'skeptic_rejected', 'cio_abstained'
        )
    ),
    event_envelope TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    CHECK (
        (event_kind = 'run_inputs_pinned'
            AND universe_snapshot_id IS NOT NULL
            AND universe_snapshot IS NOT NULL
            AND evidence_policy_id IS NULL
            AND evidence_artifact_ids IS NULL
            AND evidence_refusal_ids IS NULL
            AND attention_artifact_id IS NULL
            AND attention_artifact IS NULL
            AND research_checkpoint IS NULL
            AND portfolio_checkpoint IS NULL
            AND no_action_reason IS NULL)
        OR (event_kind = 'universe_snapshotted'
            AND universe_snapshot_id IS NOT NULL
            AND universe_snapshot IS NULL
            AND evidence_policy_id IS NULL
            AND evidence_artifact_ids IS NULL
            AND evidence_refusal_ids IS NULL
            AND attention_artifact_id IS NULL
            AND attention_artifact IS NULL
            AND research_checkpoint IS NULL
            AND portfolio_checkpoint IS NULL
            AND no_action_reason IS NULL)
        OR (event_kind = 'evidence_captured'
            AND universe_snapshot_id IS NULL
            AND universe_snapshot IS NULL
            AND evidence_policy_id IS NOT NULL
            AND evidence_artifact_ids IS NOT NULL
            AND evidence_refusal_ids = '[]'
            AND attention_artifact_id IS NULL
            AND attention_artifact IS NULL
            AND research_checkpoint IS NULL
            AND portfolio_checkpoint IS NULL
            AND no_action_reason IS NULL)
        OR (event_kind = 'attention_selected'
            AND universe_snapshot_id IS NULL
            AND universe_snapshot IS NULL
            AND evidence_policy_id IS NULL
            AND evidence_artifact_ids IS NULL
            AND evidence_refusal_ids IS NULL
            AND attention_artifact_id IS NOT NULL
            AND attention_artifact IS NOT NULL
            AND research_checkpoint IS NULL
            AND portfolio_checkpoint IS NULL
            AND no_action_reason IS NULL)
        OR (event_kind IN ('dossiers_built', 'research_run')
            AND universe_snapshot_id IS NULL
            AND universe_snapshot IS NULL
            AND evidence_policy_id IS NULL
            AND evidence_artifact_ids IS NULL
            AND evidence_refusal_ids IS NULL
            AND attention_artifact_id IS NULL
            AND attention_artifact IS NULL
            AND research_checkpoint IS NOT NULL
            AND portfolio_checkpoint IS NULL
            AND no_action_reason IS NULL)
        OR (event_kind = 'memory_updated'
            AND universe_snapshot_id IS NULL
            AND universe_snapshot IS NULL
            AND evidence_policy_id IS NULL
            AND evidence_artifact_ids IS NULL
            AND evidence_refusal_ids IS NULL
            AND attention_artifact_id IS NULL
            AND attention_artifact IS NULL
            AND research_checkpoint IS NOT NULL
            AND portfolio_checkpoint IS NULL)
        OR (event_kind = 'portfolio_constructed'
            AND universe_snapshot_id IS NULL
            AND universe_snapshot IS NULL
            AND evidence_policy_id IS NULL
            AND evidence_artifact_ids IS NULL
            AND evidence_refusal_ids IS NULL
            AND attention_artifact_id IS NULL
            AND attention_artifact IS NULL
            AND research_checkpoint IS NULL
            AND portfolio_checkpoint IS NOT NULL
            AND no_action_reason IS NULL)
        OR (event_kind NOT IN (
                'run_inputs_pinned', 'universe_snapshotted', 'evidence_captured',
                'attention_selected', 'dossiers_built', 'research_run', 'memory_updated',
                'portfolio_constructed'
            )
            AND universe_snapshot_id IS NULL
            AND universe_snapshot IS NULL
            AND evidence_policy_id IS NULL
            AND evidence_artifact_ids IS NULL
            AND evidence_refusal_ids IS NULL
            AND attention_artifact_id IS NULL
            AND attention_artifact IS NULL
            AND research_checkpoint IS NULL
            AND portfolio_checkpoint IS NULL
            AND no_action_reason IS NULL)
    ),
    PRIMARY KEY (stream_id, sequence),
    UNIQUE (idempotency_key, sequence)
) STRICT
""",
    """
CREATE UNIQUE INDEX one_initial_event_per_stream
ON lifecycle_events(stream_id)
WHERE event_kind = 'advance_requested'
""",
    """
CREATE TABLE advance_refusals (
    refusal_id INTEGER PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    cycle_identity TEXT,
    reason_code TEXT NOT NULL CHECK (
        reason_code IN (
            'invalid_session', 'invalid_mode', 'invalid_idempotency_key',
            'missing_universe_input', 'invalid_universe_input',
            'stale_universe_input', 'contradictory_universe_input',
            'missing_portfolio_input', 'invalid_portfolio_input',
            'stale_portfolio_input', 'contradictory_portfolio_input',
            'session_stream_conflict', 'idempotency_key_conflict',
            'invalid_durable_state', 'evidence_capture_failed',
            'attention_selection_failed', 'research_failed', 'memory_update_failed'
        )
    ),
    evidence_policy_id TEXT CHECK (
        evidence_policy_id IS NULL OR length(evidence_policy_id) = 64
    ),
    evidence_artifact_ids TEXT,
    evidence_refusal_ids TEXT,
    attention_refusal_reason TEXT CHECK (
        attention_refusal_reason IS NULL OR attention_refusal_reason IN (
            'missing_evidence', 'stale_evidence', 'contradictory_evidence',
            'corrupt_evidence'
        )
    ),
    research_refusal_id TEXT CHECK (
        research_refusal_id IS NULL OR length(research_refusal_id) = 64
    ),
    research_refusal TEXT,
    recorded_at TEXT NOT NULL,
    CHECK (
        (reason_code = 'evidence_capture_failed'
            AND evidence_policy_id IS NOT NULL
            AND evidence_artifact_ids IS NOT NULL
            AND evidence_refusal_ids IS NOT NULL
            AND evidence_refusal_ids != '[]'
            AND attention_refusal_reason IS NULL
            AND research_refusal_id IS NULL
            AND research_refusal IS NULL)
        OR (reason_code = 'attention_selection_failed'
            AND evidence_policy_id IS NOT NULL
            AND evidence_artifact_ids IS NOT NULL
            AND evidence_artifact_ids != '[]'
            AND evidence_refusal_ids = '[]'
            AND attention_refusal_reason IS NOT NULL
            AND research_refusal_id IS NULL
            AND research_refusal IS NULL)
        OR (reason_code IN ('research_failed', 'memory_update_failed')
            AND evidence_policy_id IS NULL
            AND evidence_artifact_ids IS NULL
            AND evidence_refusal_ids IS NULL
            AND attention_refusal_reason IS NULL
            AND research_refusal_id IS NOT NULL
            AND research_refusal IS NOT NULL)
        OR (reason_code NOT IN (
                'evidence_capture_failed', 'attention_selection_failed',
                'research_failed', 'memory_update_failed'
            )
            AND evidence_policy_id IS NULL
            AND evidence_artifact_ids IS NULL
            AND evidence_refusal_ids IS NULL
            AND attention_refusal_reason IS NULL
            AND research_refusal_id IS NULL
            AND research_refusal IS NULL)
    )
) STRICT
""",
    """
CREATE UNIQUE INDEX one_unkeyed_refusal_per_reason_and_cycle
ON advance_refusals(reason_code, COALESCE(cycle_identity, ''))
WHERE idempotency_key IS NULL
""",
    """
CREATE TRIGGER lifecycle_events_are_append_only_update
BEFORE UPDATE ON lifecycle_events BEGIN SELECT RAISE(ABORT, 'append-only lifecycle ledger'); END
""",
    """
CREATE TRIGGER lifecycle_events_are_append_only_delete
BEFORE DELETE ON lifecycle_events BEGIN SELECT RAISE(ABORT, 'append-only lifecycle ledger'); END
""",
    """
CREATE TABLE portfolio_constructions (
    run_id TEXT PRIMARY KEY CHECK (length(run_id) = 64),
    result_id TEXT NOT NULL CHECK (length(result_id) = 64),
    house_view_id TEXT CHECK (house_view_id IS NULL OR length(house_view_id) = 64),
    policy_id TEXT NOT NULL CHECK (length(policy_id) = 64),
    input_id TEXT NOT NULL CHECK (length(input_id) = 64),
    target_band_ids TEXT NOT NULL,
    refusal_reason TEXT CHECK (
        refusal_reason IS NULL OR refusal_reason IN (
            'invalid_request', 'incomplete_input', 'stale_input',
            'contradictory_input', 'authority_violation'
        )
    ),
    result_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    CHECK (
        (refusal_reason IS NULL AND house_view_id IS NOT NULL)
        OR (refusal_reason IS NOT NULL AND house_view_id IS NULL AND target_band_ids = '[]')
    )
) STRICT
""",
    """
CREATE TRIGGER portfolio_constructions_are_append_only_update
BEFORE UPDATE ON portfolio_constructions
BEGIN SELECT RAISE(ABORT, 'append-only portfolio construction'); END
""",
    """
CREATE TRIGGER portfolio_constructions_are_append_only_delete
BEFORE DELETE ON portfolio_constructions
BEGIN SELECT RAISE(ABORT, 'append-only portfolio construction'); END
""",
    """
CREATE TABLE portfolio_shadow_accounts (
    run_id TEXT NOT NULL CHECK (length(run_id) = 64),
    account_kind TEXT NOT NULL CHECK (
        account_kind IN ('conservative', 'growth', 'equal_weight')
    ),
    account_id TEXT NOT NULL CHECK (length(account_id) = 64),
    house_view_id TEXT NOT NULL CHECK (length(house_view_id) = 64),
    policy_id TEXT NOT NULL CHECK (length(policy_id) = 64),
    input_id TEXT NOT NULL CHECK (length(input_id) = 64),
    result_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (run_id, account_kind),
    UNIQUE (account_id),
    FOREIGN KEY (run_id) REFERENCES portfolio_constructions(run_id)
) STRICT
""",
    """
CREATE TRIGGER portfolio_shadow_accounts_are_append_only_update
BEFORE UPDATE ON portfolio_shadow_accounts
BEGIN SELECT RAISE(ABORT, 'append-only portfolio shadow account'); END
""",
    """
CREATE TRIGGER portfolio_shadow_accounts_are_append_only_delete
BEFORE DELETE ON portfolio_shadow_accounts
BEGIN SELECT RAISE(ABORT, 'append-only portfolio shadow account'); END
""",
    """
CREATE TRIGGER advance_refusals_are_append_only_update
BEFORE UPDATE ON advance_refusals BEGIN SELECT RAISE(ABORT, 'append-only refusal ledger'); END
    """,
    """
CREATE TRIGGER advance_refusals_are_append_only_delete
BEFORE DELETE ON advance_refusals BEGIN SELECT RAISE(ABORT, 'append-only refusal ledger'); END
""",
    """
CREATE TABLE advance_conflicts (
    idempotency_key TEXT PRIMARY KEY,
    reason_code TEXT NOT NULL CHECK (reason_code = 'idempotency_key_conflict'),
    recorded_at TEXT NOT NULL
) STRICT
""",
    """
CREATE TRIGGER advance_conflicts_are_append_only_update
BEFORE UPDATE ON advance_conflicts BEGIN SELECT RAISE(ABORT, 'append-only conflict ledger'); END
""",
    """
CREATE TRIGGER advance_conflicts_are_append_only_delete
BEFORE DELETE ON advance_conflicts BEGIN SELECT RAISE(ABORT, 'append-only conflict ledger'); END
""",
    """
CREATE TABLE memory_update_refusals (
    refusal_id TEXT PRIMARY KEY CHECK (length(refusal_id) = 64),
    run_id TEXT NOT NULL CHECK (length(run_id) = 64),
    failed_event_id TEXT NOT NULL CHECK (length(failed_event_id) = 64),
    refusal_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (run_id, failed_event_id)
) STRICT
""",
    """
CREATE TRIGGER memory_update_refusals_are_append_only_update
BEFORE UPDATE ON memory_update_refusals
BEGIN SELECT RAISE(ABORT, 'append-only memory refusal'); END
""",
    """
CREATE TRIGGER memory_update_refusals_are_append_only_delete
BEFORE DELETE ON memory_update_refusals
BEGIN SELECT RAISE(ABORT, 'append-only memory refusal'); END
""",
    """
CREATE TABLE belief_events (
    ledger_position INTEGER PRIMARY KEY CHECK (ledger_position > 0),
    event_id TEXT NOT NULL UNIQUE,
    belief_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    projection_identity TEXT NOT NULL CHECK (length(projection_identity) = 64),
    recorded_at TEXT NOT NULL
) STRICT
""",
    """
CREATE INDEX belief_events_by_belief
ON belief_events(belief_id, ledger_position)
""",
    """
CREATE TRIGGER belief_events_are_append_only_update
BEFORE UPDATE ON belief_events BEGIN SELECT RAISE(ABORT, 'append-only belief ledger'); END
""",
    """
CREATE TRIGGER belief_events_are_append_only_delete
BEFORE DELETE ON belief_events BEGIN SELECT RAISE(ABORT, 'append-only belief ledger'); END
""",
    """
CREATE TABLE belief_ledger_commitments (
    ledger_position INTEGER PRIMARY KEY CHECK (ledger_position > 0),
    projection_identity TEXT NOT NULL UNIQUE CHECK (length(projection_identity) = 64),
    commitment_identity TEXT NOT NULL UNIQUE CHECK (length(commitment_identity) = 64)
) STRICT
""",
    """
CREATE TRIGGER belief_ledger_commitments_are_append_only_update
BEFORE UPDATE ON belief_ledger_commitments
BEGIN SELECT RAISE(ABORT, 'append-only belief commitment'); END
""",
    """
CREATE TRIGGER belief_ledger_commitments_are_append_only_delete
BEFORE DELETE ON belief_ledger_commitments
BEGIN SELECT RAISE(ABORT, 'append-only belief commitment'); END
""",
    """
CREATE TABLE belief_ledger_head (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    ledger_position INTEGER NOT NULL CHECK (ledger_position > 0),
    commitment_identity TEXT NOT NULL CHECK (length(commitment_identity) = 64)
) STRICT
""",
    """
CREATE TRIGGER belief_ledger_head_starts_at_first_commitment
BEFORE INSERT ON belief_ledger_head
WHEN NEW.ledger_position != 1
  OR NOT EXISTS (
      SELECT 1 FROM belief_ledger_commitments
      WHERE ledger_position = NEW.ledger_position
        AND commitment_identity = NEW.commitment_identity
  )
BEGIN SELECT RAISE(ABORT, 'invalid belief ledger head'); END
""",
    """
CREATE TRIGGER belief_ledger_head_advances_monotonically
BEFORE UPDATE ON belief_ledger_head
WHEN NEW.singleton != OLD.singleton
  OR NEW.ledger_position != OLD.ledger_position + 1
  OR NOT EXISTS (
      SELECT 1 FROM belief_ledger_commitments
      WHERE ledger_position = NEW.ledger_position
        AND commitment_identity = NEW.commitment_identity
  )
BEGIN SELECT RAISE(ABORT, 'invalid belief ledger head'); END
""",
    """
CREATE TRIGGER belief_ledger_head_cannot_be_deleted
BEFORE DELETE ON belief_ledger_head
BEGIN SELECT RAISE(ABORT, 'durable belief ledger head'); END
""",
    """
CREATE TABLE constitution_governance_events (
    sequence INTEGER PRIMARY KEY CHECK (sequence >= 0),
    event_kind TEXT NOT NULL CHECK (
        event_kind IN (
            'constitution_scheduled', 'constitution_activated',
            'constitution_refused', 'constitution_conflicted'
        )
    ),
    request_identity TEXT,
    request_fingerprint TEXT NOT NULL CHECK (length(request_fingerprint) = 64),
    event_envelope TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    CHECK (
        request_identity IS NOT NULL
        OR event_kind = 'constitution_refused'
    )
) STRICT
""",
    """
CREATE UNIQUE INDEX one_constitution_governance_fact_per_kind_request_and_material
ON constitution_governance_events(
    event_kind, COALESCE(request_identity, ''), request_fingerprint
)
""",
    """
CREATE TRIGGER constitution_governance_events_are_append_only_update
BEFORE UPDATE ON constitution_governance_events
BEGIN SELECT RAISE(ABORT, 'append-only Constitution governance ledger'); END
""",
    """
CREATE TRIGGER constitution_governance_events_are_append_only_delete
BEFORE DELETE ON constitution_governance_events
BEGIN SELECT RAISE(ABORT, 'append-only Constitution governance ledger'); END
""",
    """
CREATE TABLE production_research_call_intents (
    call_id TEXT PRIMARY KEY CHECK (length(call_id) = 64),
    run_id TEXT NOT NULL CHECK (length(run_id) = 64),
    request_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN (
        'evidence_collector', 'thesis_builder', 'independent_skeptic',
        'scenario_forecaster', 'cio'
    )),
    intent_json TEXT NOT NULL,
    intent_hash TEXT NOT NULL CHECK (length(intent_hash) = 64),
    recorded_at TEXT NOT NULL,
    UNIQUE (run_id, request_id, role)
) STRICT
""",
    """
CREATE TABLE production_research_call_observations (
    call_id TEXT PRIMARY KEY REFERENCES production_research_call_intents(call_id),
    observation_json TEXT NOT NULL,
    raw_response BLOB,
    observation_hash TEXT NOT NULL CHECK (length(observation_hash) = 64),
    recorded_at TEXT NOT NULL
) STRICT
""",
    """
CREATE TRIGGER production_research_call_intents_are_append_only_update
BEFORE UPDATE ON production_research_call_intents
BEGIN SELECT RAISE(ABORT, 'append-only production research intent'); END
""",
    """
CREATE TRIGGER production_research_call_intents_are_append_only_delete
BEFORE DELETE ON production_research_call_intents
BEGIN SELECT RAISE(ABORT, 'append-only production research intent'); END
""",
    """
CREATE TRIGGER production_research_call_observations_are_append_only_update
BEFORE UPDATE ON production_research_call_observations
BEGIN SELECT RAISE(ABORT, 'append-only production research observation'); END
""",
    """
CREATE TRIGGER production_research_call_observations_are_append_only_delete
BEFORE DELETE ON production_research_call_observations
BEGIN SELECT RAISE(ABORT, 'append-only production research observation'); END
""",
)


class _DatabaseOpenMode(StrEnum):
    CREATE_IF_MISSING = "rwc"
    EXISTING_ONLY = "rw"


_CURRENT_DATABASE_VERSION = 14
_CURRENT_SCHEMA_SIGNATURE = frozenset(" ".join(statement.split()) for statement in _CURRENT_SCHEMA)

_PROJECTION_SCHEMA = """
CREATE TABLE lifecycle_status_projection (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    active_phase TEXT,
    last_completed_cycle TEXT,
    run_id TEXT,
    configuration_version INTEGER,
    configuration_hash TEXT,
    research_policy_hash TEXT,
    constitution_version INTEGER,
    constitution_hash TEXT,
    data_regime TEXT,
    evidence_cutoff TEXT,
    instrument_snapshot_hash TEXT,
    position_snapshot_hash TEXT,
    eligibility_policy_hash TEXT,
    portfolio_policy_hash TEXT,
    portfolio_input_hash TEXT,
    liveness TEXT NOT NULL,
    durable_reason TEXT,
    universe_snapshot_cycle TEXT,
    universe_snapshot_id TEXT,
    attention_artifact_cycle TEXT,
    attention_artifact_id TEXT,
    portfolio_checkpoint TEXT
) STRICT;
"""

_BELIEF_GRAPH_PROJECTION_SCHEMA = """
CREATE TABLE belief_graph_projection (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    projection_identity TEXT NOT NULL CHECK (length(projection_identity) = 64),
    query_json TEXT NOT NULL,
    graph_json TEXT NOT NULL
) STRICT;
"""


@dataclass(frozen=True, slots=True)
class RuntimeRootRefusal:
    """Signal that private runtime storage cannot be established safely."""


@dataclass(frozen=True, slots=True)
class PreparedRuntimeDatabase:
    """Identify a validated database path and whether this call created it."""

    path: Path
    created: bool


@dataclass(frozen=True, slots=True)
class _RuntimeDatabaseLocation:
    path: Path
    root_created: bool
    database_exists: bool


def prepare_runtime_database(state_root: Path) -> PreparedRuntimeDatabase | RuntimeRootRefusal:
    """Validate private runtime storage and create a missing database."""
    location = _locate_runtime_database(state_root)
    if isinstance(location, RuntimeRootRefusal):
        return location
    if isinstance(location, _RuntimeDatabaseLocation):
        if location.database_exists:
            return PreparedRuntimeDatabase(path=location.path, created=False)
        return _create_runtime_database(location.path)
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(location)  # pragma: no cover


def open_runtime_database(state_root: Path) -> PreparedRuntimeDatabase | RuntimeRootRefusal:
    """Validate runtime storage without replacing a database missing from an existing root."""
    location = _locate_runtime_database(state_root)
    if isinstance(location, RuntimeRootRefusal):
        return location
    if isinstance(location, _RuntimeDatabaseLocation):
        if location.database_exists:
            return PreparedRuntimeDatabase(path=location.path, created=False)
        if location.root_created:
            return _create_runtime_database(location.path)
        return PreparedRuntimeDatabase(path=location.path, created=False)
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(location)  # pragma: no cover


def _locate_runtime_database(
    state_root: Path,
) -> _RuntimeDatabaseLocation | RuntimeRootRefusal:
    try:
        if state_root.is_symlink():
            return _invalid_root()
        root_created = not state_root.exists()
        if not root_created:
            if not state_root.is_dir() or stat.S_IMODE(state_root.stat().st_mode) & 0o077:
                return _invalid_root()
        else:
            state_root.mkdir(mode=0o700)
        database = state_root / _DATABASE_NAME
        if database.is_symlink():
            return _invalid_root()
        database_exists = database.exists()
        if database_exists and (
            not database.is_file() or stat.S_IMODE(database.stat().st_mode) & 0o077
        ):
            return _invalid_root()
        return _RuntimeDatabaseLocation(database, root_created, database_exists)
    except OSError:
        return _invalid_root()


def _create_runtime_database(database: Path) -> PreparedRuntimeDatabase | RuntimeRootRefusal:
    try:
        descriptor = os.open(database, _PRIVATE_DATABASE_FLAGS, 0o600)
        os.close(descriptor)
    except OSError:
        return _invalid_root()
    return PreparedRuntimeDatabase(path=database, created=True)


def _invalid_root() -> RuntimeRootRefusal:
    return RuntimeRootRefusal()


def _prepare_database(database: Path, *, mode: _DatabaseOpenMode) -> None:
    try:
        with closing(_connect_database(database, mode=mode)) as connection:
            _initialize_or_validate_database(connection)
    except sqlite3.Error as error:
        raise LifecyclePersistenceError(_DATABASE_INITIALIZATION_FAILED) from error


def _initialize_or_validate_database(connection: sqlite3.Connection) -> None:
    with connection:
        connection.execute(_BEGIN_IMMEDIATE_SQL)
        recorded_version = _database_version(connection)
        is_fresh = recorded_version == 0 and not _user_schema_has_objects(connection)
        if is_fresh:
            for statement in _CURRENT_SCHEMA:
                connection.execute(statement)
            _set_current_database_version(connection)
        elif recorded_version != _CURRENT_DATABASE_VERSION:
            raise LifecyclePersistenceError(_UNSUPPORTED_DATABASE_VERSION)
        _validate_current_database(connection, _schema_signature(connection))
        if is_fresh:
            connection.commit()
        else:
            connection.rollback()


def _database_version(connection: sqlite3.Connection) -> int:
    value = connection.execute(_USER_VERSION_SQL).fetchall()[0][0]
    return _integer(value, "database_version")


def _set_current_database_version(connection: sqlite3.Connection) -> None:
    connection.execute(f"PRAGMA user_version = {_CURRENT_DATABASE_VERSION}")


def _user_schema_has_objects(connection: sqlite3.Connection) -> bool:
    row: tuple[object, ...] | None = connection.execute(_USER_SCHEMA_OBJECT_EXISTS_SQL).fetchone()
    return row == (1,)


def _schema_signature(connection: sqlite3.Connection) -> frozenset[str]:
    rows = connection.execute(
        """
        SELECT sql FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite!_%' ESCAPE '!'
          AND NOT (
              type IN ('table', 'index', 'trigger')
              AND tbl_name IN ('lifecycle_status_projection', 'belief_graph_projection')
          )
          AND sql IS NOT NULL
        """
    ).fetchall()
    return frozenset(" ".join(str(row[0]).split()) for row in rows)


def _validate_current_database(
    connection: sqlite3.Connection,
    schema: frozenset[str],
) -> None:
    if schema != _CURRENT_SCHEMA_SIGNATURE:
        raise LifecyclePersistenceError(_SCHEMA_VERSION_MISMATCH)
    _validate_database_integrity(connection)


def _validate_database_integrity(connection: sqlite3.Connection) -> None:
    connection.execute(_INTEGRITY_SAVEPOINT_SQL)
    try:
        # The projection is disposable, so exclude its b-trees while retaining a full
        # database check for authoritative objects and global SQLite consistency.
        connection.execute(_DROP_PROJECTION_SQL)
        connection.execute(_DROP_BELIEF_GRAPH_PROJECTION_SQL)
        rows = connection.execute(_INTEGRITY_CHECK_SQL).fetchall()
    finally:
        connection.execute(_ROLLBACK_INTEGRITY_SAVEPOINT_SQL)
        connection.execute(_RELEASE_INTEGRITY_SAVEPOINT_SQL)
    if rows != [("ok",)]:
        raise LifecyclePersistenceError(_DATABASE_INTEGRITY_FAILED)


def _connect_database(database: Path, *, mode: _DatabaseOpenMode) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{database.as_uri()}?mode={mode}", uri=True)
    connection.execute(_ENABLE_FOREIGN_KEYS_SQL)
    connection.execute(_BUSY_TIMEOUT_SQL)
    return connection


class SQLiteLifecycleLedger:
    """Initialize or validate current storage, then append domain-selected records.

    Construction fails with ``LifecyclePersistenceError`` when the physical database
    version, schema, or integrity cannot be trusted. Capability operations validate the
    authoritative rows within their owning request or global reconstruction scope.
    """

    def __init__(self, database: Path) -> None:
        self._database = database
        _prepare_database(database, mode=_DatabaseOpenMode.CREATE_IF_MISSING)

    @classmethod
    def open_existing(cls, database: Path) -> Self:
        """Validate existing current storage without recreating a missing database."""
        instance = cls.__new__(cls)
        instance._database = database
        if database.exists():
            _prepare_database(database, mode=_DatabaseOpenMode.EXISTING_ONLY)
        return instance

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        recorded_at: object,
    ) -> LifecycleDecision:
        """Apply one pure lifecycle decision inside an append transaction."""
        recorded_at_value, timestamp = _canonical_write_timestamp(recorded_at)

        def operation(  # noqa: PLR0911, PLR0912 - preserve one transaction for all decisions.
            connection: sqlite3.Connection,
        ) -> LifecycleDecision:
            key = _command_key(command)
            refusals = _load_refusals(connection, command=command)
            terminal = decide_terminal_refusal(tuple(refusals), command)
            terminal_requires_history = terminal is not None and bool(
                terminal.evidence_artifact_ids
                or terminal.evidence_refusal_ids
                or terminal.research_refusal_id
            )
            if terminal is not None and not terminal_requires_history:
                return terminal
            next_refusal_sequence = _next_refusal_sequence(connection)
            if terminal_requires_history:
                if not isinstance(command, AdvanceCommand):  # pragma: no cover - advance-only.
                    raise LifecyclePersistenceError(_DATABASE_INTEGRITY_FAILED)
                try:
                    target_events = tuple(_load_events(connection, key=key))
                    events = (
                        tuple(
                            _load_events(
                                connection,
                                key=key,
                                include_attention_history=True,
                            )
                        )
                        if any(event.attention_artifact is not None for event in target_events)
                        else target_events
                    )
                    history = LifecycleHistory(
                        events=events,
                        conflicts=tuple(_load_conflicts(connection, key=key)),
                    )
                except InvalidLifecycleStateError:
                    return AdvanceReceipt.failed_closed(
                        AdvanceFailureReason.INVALID_DURABLE_STATE,
                        cycle=command.request.session,
                    )
                return decide_evidence_refusal_replay(history, tuple(refusals), command)
            try:
                history = LifecycleHistory(
                    events=(
                        ()
                        if key is None
                        else tuple(
                            _load_events(
                                connection,
                                key=key,
                                include_attention_history=True,
                            )
                        )
                    ),
                    conflicts=(() if key is None else tuple(_load_conflicts(connection, key=key))),
                    occupied_stream_ids=_occupied_stream_ids(connection, command),
                    next_refusal_sequence=next_refusal_sequence,
                )
            except InvalidLifecycleStateError:
                decision = decide_invalid_history(
                    tuple(refusals),
                    command,
                    recorded_at_value,
                    next_refusal_sequence=next_refusal_sequence,
                )
            else:
                decision = decide_advance(history, command, attempt, recorded_at_value)
            if isinstance(decision, (AppendLifecycleRecord, AppendTerminalLifecycleRecord)):
                _append_record(connection, decision.record, recorded_at_value, timestamp)
                return decision
            if isinstance(decision, AdvanceReceipt):
                return decision
            if isinstance(decision, PerformEvidenceCapture):
                return decision
            if isinstance(decision, PerformAttentionSelection):
                return decision
            if isinstance(decision, PerformDossierBuild):
                return decision
            if isinstance(decision, PerformResearch):
                return decision
            if isinstance(decision, PerformMemoryUpdate):
                return decision
            if isinstance(decision, PerformPortfolioConstruction):
                return decision
            # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
            assert_never(decision)  # pragma: no cover

        return self._write(operation)

    def rebuild_status(self) -> LifecycleStatus:
        """Replace the disposable projection after validating all authoritative history."""

        def operation(connection: sqlite3.Connection) -> LifecycleStatus:
            history = LifecycleHistory(
                events=tuple(_load_events(connection)),
                refusals=tuple(_load_refusals(connection)),
                conflicts=tuple(_load_conflicts(connection)),
            )
            status = derive_lifecycle_status(history)
            _replace_status_projection(connection, status)
            return status

        return self._write(operation)

    def rebuild_portfolio_checkpoints(self) -> tuple[PortfolioCheckpointReference, ...]:
        """Rebuild exact portfolio references from validated lifecycle history."""

        def operation(
            connection: sqlite3.Connection,
        ) -> tuple[PortfolioCheckpointReference, ...]:
            return reconstruct_portfolio_checkpoints(
                LifecycleHistory(events=tuple(_load_events(connection)))
            )

        return self._write(operation)

    def pinned_constitution_use(self, idempotency_key: IdempotencyKey) -> ConstitutionUse | None:
        """Return an existing stream's fully validated historical Constitution use."""

        def operation(connection: sqlite3.Connection) -> ConstitutionUse | None:
            target_events = tuple(_load_events(connection, key=idempotency_key))
            if not target_events:
                return None
            events = (
                tuple(
                    _load_events(
                        connection,
                        key=idempotency_key,
                        include_attention_history=True,
                    )
                )
                if any(event.attention_artifact is not None for event in target_events)
                else target_events
            )
            history = LifecycleHistory(events=events)
            stream_starts = tuple(event for event in events if event.sequence == 0)
            uses = reconstruct_constitution_uses(history)
            if len(stream_starts) != len(uses):
                raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
            matching = tuple(
                use
                for event, use in zip(stream_starts, uses, strict=True)
                if event.request.idempotency_key == idempotency_key
            )
            if len(matching) != 1:
                raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
            return matching[0]

        return self._write(operation)

    def constitution_uses(self) -> tuple[ConstitutionUse, ...]:
        """Read every lifecycle Constitution pin without coupling unrelated streams."""

        def operation(connection: sqlite3.Connection) -> tuple[ConstitutionUse, ...]:
            return _load_constitution_uses(connection)

        return self._write(operation)

    def rebuild_evidence_checkpoints(self) -> tuple[EvidenceCaptureReference, ...]:
        """Reconstruct every validated lifecycle reference into Evidence Vault state."""

        def operation(connection: sqlite3.Connection) -> tuple[EvidenceCaptureReference, ...]:
            history = LifecycleHistory(
                events=tuple(_load_events(connection)),
                refusals=tuple(_load_refusals(connection)),
                conflicts=tuple(_load_conflicts(connection)),
            )
            return reconstruct_evidence_checkpoints(history)

        return self._write(operation)

    def rebuild_production_research_checkpoints(
        self,
    ) -> tuple[ProductionResearchReference, ...]:
        """Reconstruct completed production-call references for Status validation."""

        def operation(
            connection: sqlite3.Connection,
        ) -> tuple[ProductionResearchReference, ...]:
            history = LifecycleHistory(
                events=tuple(_load_events(connection)),
                refusals=tuple(_load_refusals(connection)),
                conflicts=tuple(_load_conflicts(connection)),
            )
            return reconstruct_production_research_checkpoints(history)

        return self._write(operation)

    def rebuild_memory_event_ids(self) -> tuple[str, ...]:
        """Reconstruct every Belief Event reference named by lifecycle memory checkpoints."""

        def operation(connection: sqlite3.Connection) -> tuple[str, ...]:
            history = LifecycleHistory(
                events=tuple(_load_events(connection)),
                refusals=tuple(_load_refusals(connection)),
                conflicts=tuple(_load_conflicts(connection)),
            )
            return reconstruct_memory_event_ids(history)

        return self._write(operation)

    def rebuild_constitution_uses(self) -> tuple[ConstitutionUse, ...]:
        """Reconstruct exact lifecycle-to-Constitution references for Status validation."""

        def operation(connection: sqlite3.Connection) -> tuple[ConstitutionUse, ...]:
            return reconstruct_constitution_uses(
                LifecycleHistory(events=tuple(_load_events(connection)))
            )

        return self._write(operation)

    def _connect(self) -> sqlite3.Connection:
        return _connect_database(self._database, mode=_DatabaseOpenMode.EXISTING_ONLY)

    def _write(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_BEGIN_IMMEDIATE_SQL)
                return operation(connection)
        except sqlite3.Error as error:
            raise LifecyclePersistenceError(_CHECKPOINT_FAILED) from error


class SQLiteConstitutionGovernance:
    """Persist and reconstruct operator-approved Constitution governance facts."""

    def __init__(self, database: Path) -> None:
        self._database = database
        _prepare_database(database, mode=_DatabaseOpenMode.CREATE_IF_MISSING)

    @classmethod
    def open_existing(cls, database: Path) -> Self:
        """Validate existing storage without recreating a missing database."""
        instance = cls.__new__(cls)
        instance._database = database
        if database.exists():
            _prepare_database(database, mode=_DatabaseOpenMode.EXISTING_ONLY)
        return instance

    def govern(
        self,
        command: GovernanceCommand,
        verifier: OperatorApprovalVerifier,
        verification: ApprovalVerification | None,
        recorded_at: UtcInstant,
    ) -> GovernanceReceipt:
        """Select and append one governance fact inside an atomic transaction."""
        _, timestamp = _canonical_write_timestamp(recorded_at)

        def operation(connection: sqlite3.Connection) -> GovernanceReceipt:
            history = _load_governance_history(connection)
            uses = _load_constitution_uses(connection)
            _validate_governance_history(history, verifier, uses)
            decision = decide_governance(history, command, verification, recorded_at)
            if decision.record is not None:
                _append_governance_event(connection, decision.record, timestamp)
            return decision.receipt

        return self._write(operation)

    def resolve_constitution(
        self,
        session: MarketSession,
        verifier: OperatorApprovalVerifier | None,
        recorded_at: UtcInstant,
    ) -> ConstitutionActivation:
        """Resolve a session Constitution and append a due activation atomically."""
        _, timestamp = _canonical_write_timestamp(recorded_at)

        def operation(connection: sqlite3.Connection) -> ConstitutionActivation:
            history = _load_governance_history(connection)
            uses = _load_constitution_uses(connection)
            _validate_governance_history(history, verifier, uses)
            activation = activate_constitution(history, session, recorded_at)
            if activation.record is not None:
                _validate_governance_history(
                    history.append(activation.record),
                    verifier,
                    uses,
                )
                _append_governance_event(connection, activation.record, timestamp)
            return activation

        return self._write(operation)

    def constitution_for(
        self,
        session: MarketSession,
        verifier: OperatorApprovalVerifier | None,
    ) -> ConstitutionArtifact:
        """Resolve one historical session without activating pending governance."""

        def operation(connection: sqlite3.Connection) -> ConstitutionArtifact:
            history = _load_governance_history(connection)
            state = _validate_governance_history(history, verifier)
            return state.constitution_for(session)

        return self._write(operation)

    def constitution_for_use(
        self,
        use: ConstitutionUse,
        verifier: OperatorApprovalVerifier | None,
    ) -> ConstitutionArtifact:
        """Resolve one lifecycle pin from the governance prefix visible at its pin time."""

        def operation(connection: sqlite3.Connection) -> ConstitutionArtifact:
            history = _load_governance_history(connection)
            if history.events and verifier is None:
                raise GovernanceStateError(_MISSING_APPROVAL_VERIFIER)
            verification = _approval_unavailable if verifier is None else verifier.verify
            return constitution_for_use(history, verification, use)

        return self._write(operation)

    def next_activation_session(
        self,
        verifier: OperatorApprovalVerifier | None,
    ) -> MarketSession | None:
        """Return the sole pending activation boundary after validating history."""

        def operation(connection: sqlite3.Connection) -> MarketSession | None:
            history = _load_governance_history(connection)
            state = _validate_governance_history(history, verifier)
            return None if not state.pending else state.pending[0].activation_session

        return self._write(operation)

    def validate_constitution_uses(
        self,
        uses: tuple[ConstitutionUse, ...],
        verifier: OperatorApprovalVerifier | None,
    ) -> None:
        """Require lifecycle pins to resolve from authoritative governance history."""

        def operation(connection: sqlite3.Connection) -> None:
            history = _load_governance_history(connection)
            _validate_governance_history(history, verifier, uses)

        self._write(operation)

    def rebuild_constitution_status(
        self,
        verifier: OperatorApprovalVerifier | None,
        uses: tuple[ConstitutionUse, ...] = (),
    ) -> ConstitutionGovernanceStatus:
        """Reconstruct bounded status after reverifying durable approvals."""

        def operation(connection: sqlite3.Connection) -> ConstitutionGovernanceStatus:
            history = _load_governance_history(connection)
            state = _validate_governance_history(history, verifier, uses)
            return ConstitutionGovernanceStatus.from_state(state)

        return self._write(operation)

    def _connect(self) -> sqlite3.Connection:
        return _connect_database(self._database, mode=_DatabaseOpenMode.EXISTING_ONLY)

    def _write(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_BEGIN_IMMEDIATE_SQL)
                return operation(connection)
        except sqlite3.Error as error:
            raise LifecyclePersistenceError(_CHECKPOINT_FAILED) from error


def _load_governance_history(connection: sqlite3.Connection) -> ConstitutionGovernanceHistory:
    rows = connection.execute(
        """
        SELECT sequence, event_kind, request_identity, request_fingerprint,
               event_envelope, recorded_at
        FROM constitution_governance_events ORDER BY sequence
        """
    ).fetchall()
    events: list[GovernanceEvent] = []
    for row in rows:
        envelope = _text(row[4], "event_envelope")
        try:
            decoded: object = json.loads(envelope)
        except (RecursionError, ValueError) as error:
            raise GovernanceStateError(_INVALID_GOVERNANCE_HISTORY) from error
        event = parse_governance_event(decoded)
        if event is None or _canonical_json(event.to_payload()) != envelope:
            raise GovernanceStateError(_INVALID_GOVERNANCE_HISTORY)
        request_identity = None if event.request_identity is None else event.request_identity.value
        if (
            event.sequence != _integer(row[0], "sequence")
            or event.kind.value != _text(row[1], "event_kind")
            or request_identity != row[2]
            or event.request_fingerprint != _hash(row[3], "request_fingerprint")
            or event.recorded_at != _canonical_timestamp(row[5], "recorded_at")
        ):
            raise GovernanceStateError(_INVALID_GOVERNANCE_HISTORY)
        events.append(event)
    return ConstitutionGovernanceHistory(tuple(events))


def _validate_governance_history(
    history: ConstitutionGovernanceHistory,
    verifier: OperatorApprovalVerifier | None,
    uses: tuple[ConstitutionUse, ...] = (),
) -> ConstitutionGovernanceState:
    if history.events and verifier is None:
        raise GovernanceStateError(_MISSING_APPROVAL_VERIFIER)
    verification = _approval_unavailable if verifier is None else verifier.verify
    return validate_constitution_uses(history, verification, uses)


def _approval_unavailable(_proof: OperatorApprovalProof) -> ApprovalVerification:
    return (
        ApprovalVerification.INVALID_SIGNATURE
    )  # pragma: no cover - empty history never verifies.


def _append_governance_event(
    connection: sqlite3.Connection,
    event: GovernanceEvent,
    timestamp: str,
) -> None:
    connection.execute(
        """
        INSERT INTO constitution_governance_events (
            sequence, event_kind, request_identity, request_fingerprint,
            event_envelope, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            event.sequence,
            event.kind.value,
            None if event.request_identity is None else event.request_identity.value,
            event.request_fingerprint,
            _canonical_json(event.to_payload()),
            timestamp,
        ),
    )


def _load_events(
    connection: sqlite3.Connection,
    *,
    key: IdempotencyKey | None = None,
    include_attention_history: bool = False,
) -> list[LifecycleEvent]:
    if key is None:
        rows = connection.execute(
            """
            SELECT stream_id, sequence, idempotency_key, cycle_identity, mode,
                   configuration_version, configuration_hash,
                   research_policy_hash,
                   constitution_version, constitution_hash, run_id,
                   data_regime, evidence_cutoff, instrument_snapshot_hash,
                   position_snapshot_hash, eligibility_policy_hash,
                   portfolio_policy_hash, portfolio_input_hash,
                   event_kind, completed_phase, universe_snapshot_id,
                   universe_snapshot, evidence_policy_id,
                   evidence_artifact_ids, evidence_refusal_ids,
                   attention_artifact_id, attention_artifact,
                   research_checkpoint, portfolio_checkpoint, no_action_reason,
                   event_envelope, recorded_at
            FROM lifecycle_events ORDER BY stream_id, sequence
            """
        ).fetchall()
    elif include_attention_history:
        rows = connection.execute(
            """
            SELECT stream_id, sequence, idempotency_key, cycle_identity, mode,
                   configuration_version, configuration_hash,
                   research_policy_hash,
                   constitution_version, constitution_hash, run_id,
                   data_regime, evidence_cutoff, instrument_snapshot_hash,
                   position_snapshot_hash, eligibility_policy_hash,
                   portfolio_policy_hash, portfolio_input_hash,
                   event_kind, completed_phase, universe_snapshot_id,
                   universe_snapshot, evidence_policy_id,
                   evidence_artifact_ids, evidence_refusal_ids,
                   attention_artifact_id, attention_artifact,
                   research_checkpoint, portfolio_checkpoint, no_action_reason,
                   event_envelope, recorded_at
            FROM lifecycle_events
            WHERE idempotency_key = ? OR stream_id IN (
                SELECT stream_id FROM lifecycle_events
                WHERE event_kind = 'attention_selected'
            )
            ORDER BY stream_id, sequence
            """,
            (key.value,),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT stream_id, sequence, idempotency_key, cycle_identity, mode,
                   configuration_version, configuration_hash,
                   research_policy_hash,
                   constitution_version, constitution_hash, run_id,
                   data_regime, evidence_cutoff, instrument_snapshot_hash,
                   position_snapshot_hash, eligibility_policy_hash,
                   portfolio_policy_hash, portfolio_input_hash,
                   event_kind, completed_phase, universe_snapshot_id,
                   universe_snapshot, evidence_policy_id,
                   evidence_artifact_ids, evidence_refusal_ids,
                   attention_artifact_id, attention_artifact,
                   research_checkpoint, portfolio_checkpoint, no_action_reason,
                   event_envelope, recorded_at
            FROM lifecycle_events WHERE idempotency_key = ? ORDER BY sequence
            """,
            (key.value,),
        ).fetchall()
    return [_load_event(row) for row in rows]


def _load_constitution_uses(
    connection: sqlite3.Connection,
) -> tuple[ConstitutionUse, ...]:
    """Validate only governance-owned columns so unrelated stream damage stays isolated."""
    rows = connection.execute(
        """
        SELECT DISTINCT cycle_identity, constitution_version, constitution_hash, recorded_at
        FROM lifecycle_events
        WHERE sequence = 0
        ORDER BY cycle_identity, constitution_version, constitution_hash, recorded_at
        """
    ).fetchall()
    uses: list[ConstitutionUse] = []
    for row in rows:
        try:
            reference = ConstitutionReference(
                _integer(row[1], "constitution_version"),
                _hash(row[2], "constitution_hash"),
            )
        except ValueError as error:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER) from error
        uses.append(
            ConstitutionUse(
                _market_session(row[0]),
                reference,
                _canonical_timestamp(row[3], "recorded_at"),
            )
        )
    return tuple(uses)


def _load_event(row: tuple[object, ...]) -> LifecycleEvent:
    cycle = _market_session(row[3])
    request = AdvanceRequest.parse(session=cycle, mode=row[4], idempotency_key=row[2])
    if isinstance(request, InputRefusal):
        raise InvalidLifecycleStateError(_INVALID_REQUEST)
    if not isinstance(request, AdvanceRequest):
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(request)  # pragma: no cover
    stream_id = _text(row[0], "stream_id")
    sequence = _integer(row[1], "sequence")
    version = _integer(row[5], "configuration_version")
    configuration_hash = _hash(row[6], "configuration_hash")
    research_policy_hash = _hash(row[7], "research_policy_hash")
    constitution_version = _integer(row[8], "constitution_version")
    constitution_hash = _hash(row[9], "constitution_hash")
    run_id = _hash(row[10], "run_id")
    data_regime = _data_regime(row[11])
    evidence_cutoff = _canonical_timestamp(row[12], "evidence_cutoff")
    instrument_snapshot_hash = _hash(row[13], "instrument_snapshot_hash")
    position_snapshot_hash = _hash(row[14], "position_snapshot_hash")
    eligibility_policy_hash = _hash(row[15], "eligibility_policy_hash")
    portfolio_policy_hash = _hash(row[16], "portfolio_policy_hash")
    portfolio_input_hash = _hash(row[17], "portfolio_input_hash")
    recorded_at = _canonical_timestamp(row[31], "recorded_at")
    if evidence_cutoff.value > recorded_at.value:
        raise InvalidLifecycleStateError(_FUTURE_EVIDENCE_CUTOFF)
    try:
        event_kind = LifecycleEventKind(_text(row[18], "event_kind"))
    except ValueError as error:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER) from error
    completed_phase = _optional_checkpoint(row[19])
    prepared_snapshot, published_snapshot_id = _load_universe_reference(
        row[20],
        row[21],
        event_kind=event_kind,
        run_id=run_id,
        recorded_at=recorded_at,
    )
    evidence_capture = _load_evidence_capture(row[22], row[23], row[24], event_kind=event_kind)
    attention_artifact = _load_attention_artifact(
        row[25],
        row[26],
        event_kind=event_kind,
        recorded_at=recorded_at,
    )
    research_checkpoint, no_action_reason = _load_research_checkpoint(
        row[27],
        row[29],
        event_kind=event_kind,
    )
    portfolio_checkpoint = _load_portfolio_checkpoint(row[28], event_kind=event_kind)
    event = LifecycleEvent(
        stream_id=stream_id,
        sequence=sequence,
        request=request,
        pinned_run_identity=PinnedRunIdentity(
            run_id,
            cycle,
            version,
            configuration_hash,
            research_policy_hash,
            constitution_version,
            constitution_hash,
            data_regime,
            evidence_cutoff,
            instrument_snapshot_hash,
            position_snapshot_hash,
            eligibility_policy_hash,
            portfolio_policy_hash,
            portfolio_input_hash,
        ),
        event_kind=event_kind,
        completed_phase=completed_phase,
        recorded_at=recorded_at,
        prepared_universe_snapshot=prepared_snapshot,
        published_universe_snapshot_id=published_snapshot_id,
        evidence_capture=evidence_capture,
        attention_artifact=attention_artifact,
        research_checkpoint=research_checkpoint,
        portfolio_checkpoint=portfolio_checkpoint,
        no_action_reason=no_action_reason,
    )
    envelope = _text(row[30], "event_envelope")
    if _canonical_json(event.to_envelope()) != envelope:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
    return event


def _load_universe_reference(
    snapshot_id_value: object,
    snapshot_json_value: object,
    *,
    event_kind: LifecycleEventKind,
    run_id: str,
    recorded_at: UtcInstant,
) -> tuple[UniverseSnapshot | None, str | None]:
    if event_kind is LifecycleEventKind.RUN_INPUTS_PINNED:
        snapshot_id = _hash(snapshot_id_value, "universe_snapshot_id")
        snapshot_json = _text(snapshot_json_value, "universe_snapshot")
    elif event_kind is LifecycleEventKind.UNIVERSE_SNAPSHOTTED:
        if snapshot_json_value is not None:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
        return None, _hash(snapshot_id_value, "universe_snapshot_id")
    elif snapshot_id_value is None and snapshot_json_value is None:
        return None, None
    else:
        if snapshot_id_value is not None:
            _hash(snapshot_id_value, "universe_snapshot_id")
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
    parsed = parse_persisted_universe_snapshot(
        snapshot_json,
        expected_run_id=run_id,
        expected_snapshot_id=snapshot_id,
        recorded_at=recorded_at,
    )
    if isinstance(parsed, UniverseRefusal):
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
    if isinstance(parsed, UniverseSnapshot):
        return parsed, None
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(parsed)  # pragma: no cover


def _load_evidence_capture(
    policy_id_value: object,
    artifact_ids_value: object,
    refusal_ids_value: object,
    *,
    event_kind: LifecycleEventKind,
) -> EvidenceCaptureCheckpoint | None:
    if event_kind is not LifecycleEventKind.EVIDENCE_CAPTURED:
        if policy_id_value is not None:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
        if artifact_ids_value is not None:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
        if refusal_ids_value is not None:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
        return None
    checkpoint = _parse_evidence_capture(
        policy_id_value,
        artifact_ids_value,
        refusal_ids_value,
    )
    if not checkpoint.is_complete:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
    return checkpoint


def _parse_evidence_capture(
    policy_id_value: object,
    artifact_ids_value: object,
    refusal_ids_value: object,
) -> EvidenceCaptureCheckpoint:
    policy_id = _hash(policy_id_value, "evidence_policy_id")
    artifact_ids = _hash_tuple_json(artifact_ids_value, "evidence_artifact_ids")
    refusal_ids = _hash_tuple_json(refusal_ids_value, "evidence_refusal_ids")
    try:
        return EvidenceCaptureCheckpoint(policy_id, artifact_ids, refusal_ids)
    except ValueError as error:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER) from error


def _load_attention_artifact(
    artifact_id_value: object,
    artifact_value: object,
    *,
    event_kind: LifecycleEventKind,
    recorded_at: UtcInstant,
) -> AttentionArtifact | None:
    if event_kind is not LifecycleEventKind.ATTENTION_SELECTED:
        if artifact_id_value is not None or artifact_value is not None:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
        return None
    artifact_id = _hash(artifact_id_value, "attention_artifact_id")
    encoded = _text(artifact_value, "attention_artifact")
    try:
        decoded: object = json.loads(encoded)
    except (ValueError, RecursionError) as error:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER) from error
    artifact = parse_attention_artifact(decoded)
    if (
        artifact is None
        or artifact.artifact_id != artifact_id
        or artifact.available_at != recorded_at
        or _canonical_json(artifact.to_payload()) != encoded
    ):
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
    return artifact


def _load_research_checkpoint(
    checkpoint_value: object,
    no_action_value: object,
    *,
    event_kind: LifecycleEventKind,
) -> tuple[ResearchCheckpoint | None, NoActionReason | None]:
    if event_kind not in (
        LifecycleEventKind.DOSSIERS_BUILT,
        LifecycleEventKind.RESEARCH_RUN,
        LifecycleEventKind.MEMORY_UPDATED,
    ):
        if checkpoint_value is not None or no_action_value is not None:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
        return None, None
    encoded = _text(checkpoint_value, "research_checkpoint")
    try:
        decoded: object = json.loads(encoded)
    except (ValueError, RecursionError) as error:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER) from error
    checkpoint = parse_research_checkpoint(decoded)
    if checkpoint is None or _canonical_json(checkpoint.to_payload()) != encoded:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
    if no_action_value is None:
        return checkpoint, None
    if event_kind is not LifecycleEventKind.MEMORY_UPDATED:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
    try:
        reason = NoActionReason(_text(no_action_value, "no_action_reason"))
    except ValueError as error:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER) from error
    return checkpoint, reason


def _load_portfolio_checkpoint(
    checkpoint_value: object,
    *,
    event_kind: LifecycleEventKind,
) -> PortfolioCheckpoint | None:
    if event_kind is not LifecycleEventKind.PORTFOLIO_CONSTRUCTED:
        if checkpoint_value is not None:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
        return None
    encoded = _text(checkpoint_value, "portfolio_checkpoint")
    try:
        decoded: object = json.loads(encoded)
    except (ValueError, RecursionError) as error:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER) from error
    checkpoint = parse_portfolio_checkpoint(decoded)
    if checkpoint is None or _canonical_json(checkpoint.to_payload()) != encoded:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
    return checkpoint


def _hash_tuple_json(value: object, field: str) -> tuple[str, ...]:
    encoded = _text(value, field)
    try:
        decoded: object = json.loads(encoded)
    except (ValueError, RecursionError) as error:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER) from error
    if type(decoded) is not list:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
    if any(not is_sha256(item) for item in decoded):
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
    result = tuple(decoded)
    if _canonical_json(list(result)) != encoded:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
    return result


def _load_refusals(
    connection: sqlite3.Connection,
    *,
    command: LifecycleCommand | None = None,
) -> list[DurableAdvanceRefusal]:
    if command is None:
        rows = connection.execute(
            """
            SELECT refusal_id, idempotency_key, cycle_identity, reason_code,
                   evidence_policy_id, evidence_artifact_ids, evidence_refusal_ids,
                   attention_refusal_reason, research_refusal_id, research_refusal, recorded_at
            FROM advance_refusals ORDER BY refusal_id
            """
        ).fetchall()
    elif isinstance(command, InputRefusal):
        refusal_key = command.idempotency_key
        if refusal_key is None:
            cycle = command.cycle
            rows = connection.execute(
                """
                SELECT refusal_id, idempotency_key, cycle_identity, reason_code,
                       evidence_policy_id, evidence_artifact_ids, evidence_refusal_ids,
                       attention_refusal_reason, research_refusal_id, research_refusal, recorded_at
                FROM advance_refusals
                WHERE idempotency_key IS NULL AND reason_code = ?
                    AND cycle_identity IS ?
                ORDER BY refusal_id
                """,
                (
                    AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY.value,
                    None if cycle is None else _canonical_json(cycle.to_payload()),
                ),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT refusal_id, idempotency_key, cycle_identity, reason_code,
                       evidence_policy_id, evidence_artifact_ids, evidence_refusal_ids,
                       attention_refusal_reason, research_refusal_id, research_refusal, recorded_at
                FROM advance_refusals WHERE idempotency_key = ? ORDER BY refusal_id
                """,
                (refusal_key.value,),
            ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT refusal_id, idempotency_key, cycle_identity, reason_code,
                   evidence_policy_id, evidence_artifact_ids, evidence_refusal_ids,
                   attention_refusal_reason, research_refusal_id, research_refusal, recorded_at
            FROM advance_refusals WHERE idempotency_key = ? ORDER BY refusal_id
            """,
            (command.request.idempotency_key.value,),
        ).fetchall()
    refusals: list[DurableAdvanceRefusal] = []
    for row in rows:
        sequence = _integer(row[0], "refusal_id")
        key = _optional_refusal_key(row[1])
        cycle = None if row[2] is None else _market_session(row[2])
        reason = _failure_reason(row[3])
        evidence_capture = _load_refusal_evidence_capture(
            row[4],
            row[5],
            row[6],
            reason=reason,
        )
        attention_refusal_reason = _load_attention_refusal_reason(row[7], reason=reason)
        research_refusal = _load_research_refusal(row[8], row[9], reason=reason)
        recorded_at = _canonical_timestamp(row[10], "recorded_at")
        refusals.append(
            DurableAdvanceRefusal(
                sequence,
                key,
                reason,
                recorded_at,
                cycle,
                evidence_capture,
                attention_refusal_reason,
                research_refusal,
            )
        )
    return refusals


def _load_refusal_evidence_capture(
    policy_id_value: object,
    artifact_ids_value: object,
    refusal_ids_value: object,
    *,
    reason: AdvanceFailureReason,
) -> EvidenceCaptureCheckpoint | None:
    if reason not in (
        AdvanceFailureReason.EVIDENCE_CAPTURE_FAILED,
        AdvanceFailureReason.ATTENTION_SELECTION_FAILED,
    ):
        if policy_id_value is not None:
            raise InvalidLifecycleStateError(_INVALID_REFUSAL_KEY)
        if artifact_ids_value is not None:
            raise InvalidLifecycleStateError(_INVALID_REFUSAL_KEY)
        if refusal_ids_value is not None:
            raise InvalidLifecycleStateError(_INVALID_REFUSAL_KEY)
        return None
    checkpoint = _parse_evidence_capture(
        policy_id_value,
        artifact_ids_value,
        refusal_ids_value,
    )
    if (reason is AdvanceFailureReason.EVIDENCE_CAPTURE_FAILED and checkpoint.is_complete) or (
        reason is AdvanceFailureReason.ATTENTION_SELECTION_FAILED and not checkpoint.is_complete
    ):
        raise InvalidLifecycleStateError(_INVALID_REFUSAL_KEY)
    return checkpoint


def _load_attention_refusal_reason(
    value: object,
    *,
    reason: AdvanceFailureReason,
) -> AttentionRefusalReason | None:
    if reason is not AdvanceFailureReason.ATTENTION_SELECTION_FAILED:
        if value is not None:
            raise InvalidLifecycleStateError(_INVALID_REFUSAL_KEY)
        return None
    text = _text(value, "attention_refusal_reason")
    try:
        return AttentionRefusalReason(text)
    except ValueError as error:
        raise InvalidLifecycleStateError(_INVALID_REFUSAL_KEY) from error


def _load_research_refusal(
    refusal_id_value: object,
    refusal_value: object,
    *,
    reason: AdvanceFailureReason,
) -> ResearchRefusal | None:
    if reason not in (
        AdvanceFailureReason.RESEARCH_FAILED,
        AdvanceFailureReason.MEMORY_UPDATE_FAILED,
    ):
        if refusal_id_value is not None or refusal_value is not None:
            raise InvalidLifecycleStateError(_INVALID_REFUSAL_KEY)
        return None
    encoded = _text(refusal_value, "research_refusal")
    try:
        decoded: object = json.loads(encoded)
    except (ValueError, RecursionError) as error:
        raise InvalidLifecycleStateError(_INVALID_REFUSAL_KEY) from error
    refusal = parse_research_refusal(decoded)
    if (
        refusal is None
        or refusal.refusal_id != _hash(refusal_id_value, "research_refusal_id")
        or _canonical_json(refusal.to_payload()) != encoded
    ):
        raise InvalidLifecycleStateError(_INVALID_REFUSAL_KEY)
    return refusal


def _load_conflicts(
    connection: sqlite3.Connection,
    *,
    key: IdempotencyKey | None = None,
) -> list[DurableAdvanceConflict]:
    if key is None:
        rows = connection.execute(
            """
            SELECT idempotency_key, reason_code, recorded_at
            FROM advance_conflicts ORDER BY idempotency_key
            """
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT idempotency_key, reason_code, recorded_at
            FROM advance_conflicts WHERE idempotency_key = ? ORDER BY idempotency_key
            """,
            (key.value,),
        ).fetchall()
    conflicts: list[DurableAdvanceConflict] = []
    for row in rows:
        key = _conflict_key(row[0])
        reason = _failure_reason(row[1])
        _canonical_timestamp(row[2], "recorded_at")
        conflicts.append(DurableAdvanceConflict(key, reason))
    return conflicts


def _command_key(command: LifecycleCommand) -> IdempotencyKey | None:
    if isinstance(command, InputRefusal):
        return command.idempotency_key
    if isinstance(command, AdvanceCommand):
        return command.request.idempotency_key
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(command)  # pragma: no cover


def _occupied_stream_ids(
    connection: sqlite3.Connection,
    command: LifecycleCommand,
) -> frozenset[str]:
    if isinstance(command, InputRefusal):
        return frozenset()
    if isinstance(command, AdvanceCommand):
        stream_id = command.request.stream_id
        row = connection.execute(_STREAM_EXISTS_SQL, (stream_id,)).fetchone()
        return frozenset() if row is None else frozenset((stream_id,))
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(command)  # pragma: no cover


def _next_refusal_sequence(connection: sqlite3.Connection) -> int:
    row = connection.execute(_NEXT_REFUSAL_SEQUENCE_SQL).fetchone()
    if row is None:
        raise InvalidLifecycleStateError(_INVALID_REFUSAL_KEY)
    return _integer(row[0], "refusal_id")


def _append_record(
    connection: sqlite3.Connection,
    record: LifecycleRecord,
    recorded_at: UtcInstant,
    timestamp: str,
) -> None:
    if isinstance(record, LifecycleEvent):
        if record.recorded_at != recorded_at:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
        request = record.request
        identity = record.pinned_run_identity
        connection.execute(
            """
            INSERT INTO lifecycle_events (
                stream_id, sequence, idempotency_key, cycle_identity, mode,
                configuration_version, configuration_hash,
                research_policy_hash,
                constitution_version, constitution_hash, run_id,
                data_regime, evidence_cutoff, instrument_snapshot_hash,
                position_snapshot_hash, eligibility_policy_hash,
                portfolio_policy_hash, portfolio_input_hash,
                event_kind, completed_phase, universe_snapshot_id,
                universe_snapshot, evidence_policy_id,
                evidence_artifact_ids, evidence_refusal_ids,
                attention_artifact_id, attention_artifact,
                research_checkpoint, portfolio_checkpoint, no_action_reason,
                event_envelope, recorded_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                record.stream_id,
                record.sequence,
                request.idempotency_key.value,
                _canonical_json(request.session.to_payload()),
                request.mode.value,
                identity.configuration_version,
                identity.configuration_hash,
                identity.research_policy_hash,
                identity.constitution_version,
                identity.constitution_hash,
                identity.run_id,
                identity.data_regime,
                identity.evidence_cutoff.isoformat(),
                identity.instrument_snapshot_hash,
                identity.position_snapshot_hash,
                identity.eligibility_policy_hash,
                identity.portfolio_policy_hash,
                identity.portfolio_input_hash,
                record.event_kind.value,
                (
                    None
                    if record.completed_phase is None
                    else _canonical_json(record.completed_phase.to_payload())
                ),
                (record.universe_snapshot_id),
                (
                    None
                    if record.prepared_universe_snapshot is None
                    else record.prepared_universe_snapshot.to_json()
                ),
                None if record.evidence_capture is None else record.evidence_capture.policy_id,
                (
                    None
                    if record.evidence_capture is None
                    else _canonical_json(list(record.evidence_capture.artifact_ids))
                ),
                (
                    None
                    if record.evidence_capture is None
                    else _canonical_json(list(record.evidence_capture.refusal_ids))
                ),
                (
                    None
                    if record.attention_artifact is None
                    else record.attention_artifact.artifact_id
                ),
                (
                    None
                    if record.attention_artifact is None
                    else _canonical_json(record.attention_artifact.to_payload())
                ),
                (
                    None
                    if record.research_checkpoint is None
                    else _canonical_json(record.research_checkpoint.to_payload())
                ),
                (
                    None
                    if record.portfolio_checkpoint is None
                    else _canonical_json(record.portfolio_checkpoint.to_payload())
                ),
                (None if record.no_action_reason is None else record.no_action_reason.value),
                _canonical_json(record.to_envelope()),
                timestamp,
            ),
        )
        return
    if isinstance(record, DurableAdvanceRefusal):
        if record.recorded_at != recorded_at:
            raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
        connection.execute(
            """
            INSERT INTO advance_refusals (
                refusal_id, idempotency_key, cycle_identity, reason_code,
                evidence_policy_id, evidence_artifact_ids, evidence_refusal_ids,
                attention_refusal_reason, research_refusal_id, research_refusal, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.sequence,
                None if record.idempotency_key is None else record.idempotency_key.value,
                (None if record.cycle is None else _canonical_json(record.cycle.to_payload())),
                record.reason.value,
                None if record.evidence_capture is None else record.evidence_capture.policy_id,
                (
                    None
                    if record.evidence_capture is None
                    else _canonical_json(list(record.evidence_capture.artifact_ids))
                ),
                (
                    None
                    if record.evidence_capture is None
                    else _canonical_json(list(record.evidence_capture.refusal_ids))
                ),
                (
                    None
                    if record.attention_refusal_reason is None
                    else record.attention_refusal_reason.value
                ),
                (None if record.research_refusal is None else record.research_refusal.refusal_id),
                (
                    None
                    if record.research_refusal is None
                    else _canonical_json(record.research_refusal.to_payload())
                ),
                timestamp,
            ),
        )
        return
    if isinstance(record, DurableAdvanceConflict):
        connection.execute(
            """
            INSERT INTO advance_conflicts (idempotency_key, reason_code, recorded_at)
            VALUES (?, ?, ?)
            """,
            (record.idempotency_key.value, record.reason.value, timestamp),
        )
        return
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(record)  # pragma: no cover


def _replace_status_projection(
    connection: sqlite3.Connection,
    status: LifecycleStatus,
) -> None:
    connection.execute(_DROP_PROJECTION_SQL)
    connection.execute(_PROJECTION_SCHEMA)
    identity = status.pinned_run_identity
    connection.execute(
        """
        INSERT INTO lifecycle_status_projection (
            singleton, active_phase, last_completed_cycle, run_id,
            configuration_version, configuration_hash,
            research_policy_hash,
            constitution_version, constitution_hash, data_regime, evidence_cutoff,
            instrument_snapshot_hash, position_snapshot_hash, eligibility_policy_hash,
            portfolio_policy_hash, portfolio_input_hash,
            liveness, durable_reason, universe_snapshot_cycle, universe_snapshot_id,
            attention_artifact_cycle, attention_artifact_id, portfolio_checkpoint
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                None
                if status.active_phase is None
                else _canonical_json(status.active_phase.to_payload())
            ),
            (
                None
                if status.last_completed_cycle is None
                else _canonical_json(status.last_completed_cycle.to_payload())
            ),
            None if identity is None else identity.run_id,
            None if identity is None else identity.configuration_version,
            None if identity is None else identity.configuration_hash,
            None if identity is None else identity.research_policy_hash,
            None if identity is None else identity.constitution_version,
            None if identity is None else identity.constitution_hash,
            None if identity is None else identity.data_regime,
            (None if identity is None else identity.evidence_cutoff.isoformat()),
            None if identity is None else identity.instrument_snapshot_hash,
            None if identity is None else identity.position_snapshot_hash,
            None if identity is None else identity.eligibility_policy_hash,
            None if identity is None else identity.portfolio_policy_hash,
            None if identity is None else identity.portfolio_input_hash,
            status.liveness.value,
            None if status.durable_reason is None else status.durable_reason.value,
            (
                None
                if status.universe_snapshot_cycle is None
                else _canonical_json(status.universe_snapshot_cycle.to_payload())
            ),
            status.universe_snapshot_id,
            (
                None
                if status.attention_artifact_cycle is None
                else _canonical_json(status.attention_artifact_cycle.to_payload())
            ),
            status.attention_artifact_id,
            (
                None
                if status.portfolio_checkpoint is None
                else _canonical_json(status.portfolio_checkpoint.to_payload())
            ),
        ),
    )


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value:
        message = f"invalid {field} in lifecycle ledger"
        raise InvalidLifecycleStateError(message)
    return value


def _optional_checkpoint(value: object) -> LifecycleCheckpoint | None:
    encoded = _optional_string(value, "completed_phase")
    if encoded is None:
        return None
    try:
        decoded: object = json.loads(encoded)
    except (ValueError, RecursionError) as error:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER) from error
    checkpoint = parse_lifecycle_checkpoint(decoded)
    if checkpoint is None or _canonical_json(checkpoint.to_payload()) != encoded:
        raise InvalidLifecycleStateError(_INVALID_CHECKPOINT_ORDER)
    return checkpoint


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _market_session(value: object) -> MarketSession:
    encoded = _text(value, "cycle_identity")
    try:
        decoded: object = json.loads(encoded)
    except (ValueError, RecursionError) as error:
        raise InvalidLifecycleStateError(_INVALID_REQUEST) from error
    cycle = parse_decision_cycle_identity(decoded)
    if type(cycle) is not MarketSession or _canonical_json(cycle.to_payload()) != encoded:
        raise InvalidLifecycleStateError(_INVALID_REQUEST)
    return cycle


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _integer(value: object, field: str) -> int:
    message = f"invalid {field} in lifecycle ledger"
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidLifecycleStateError(message)
    return value


def _hash(value: object, field: str) -> str:
    if not is_sha256(value):
        message = f"invalid {field} in lifecycle ledger"
        raise InvalidLifecycleStateError(message)
    return value


def _data_regime(value: object) -> str:
    if not is_data_regime(value):
        raise InvalidLifecycleStateError(_INVALID_DATA_REGIME)
    return value


def _failure_reason(value: object) -> AdvanceFailureReason:
    text = _text(value, "reason_code")
    try:
        return AdvanceFailureReason(text)
    except ValueError as error:
        raise InvalidLifecycleStateError(_UNKNOWN_REASON_CODE) from error


def _optional_refusal_key(value: object) -> IdempotencyKey | None:
    if value is None:
        return None
    key = IdempotencyKey.parse(value)
    if key is None:
        raise InvalidLifecycleStateError(_INVALID_REFUSAL_KEY)
    return key


def _conflict_key(value: object) -> IdempotencyKey:
    key = IdempotencyKey.parse(value)
    if key is None:
        raise InvalidLifecycleStateError(_INVALID_CONFLICT_KEY)
    return key


def _canonical_timestamp(value: object, field: str) -> UtcInstant:
    text = _text(value, field)
    try:
        return UtcInstant.parse(text)
    except InvalidUtcInstantError as error:
        message = f"{field} must use canonical UTC format"
        raise InvalidLifecycleStateError(message) from error


def _canonical_write_timestamp(value: object) -> tuple[UtcInstant, str]:
    if type(value) is not UtcInstant:
        raise LifecyclePersistenceError(_RECORDED_AT_NOT_CANONICAL)
    try:
        return value, value.isoformat()
    except InvalidUtcInstantError as error:
        raise LifecyclePersistenceError(_RECORDED_AT_NOT_CANONICAL) from error
