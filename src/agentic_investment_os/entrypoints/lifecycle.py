"""Compose uncredentialed lifecycle capabilities through evidence capture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, assert_never

from agentic_investment_os.adapters.filesystem_evidence import FilesystemEvidenceVault
from agentic_investment_os.adapters.recorded_evidence import RecordedEvidenceSource
from agentic_investment_os.adapters.recorded_universe import RecordedUniverseSource
from agentic_investment_os.adapters.sqlite_lifecycle import (
    PreparedRuntimeDatabase,
    RuntimeRootRefusal,
    SQLiteConstitutionGovernance,
    SQLiteLifecycleLedger,
    open_runtime_database,
    prepare_runtime_database,
)
from agentic_investment_os.adapters.sqlite_memory import SQLiteBeliefLedger
from agentic_investment_os.adapters.sqlite_production_research import (
    SQLiteProductionCallLedger,
)
from agentic_investment_os.application.governance import ConstitutionRegistry, ConstitutionStatus
from agentic_investment_os.application.lifecycle import Advance, Status
from agentic_investment_os.application.memory import Record
from agentic_investment_os.entrypoints.configuration import (
    ConfigurationRefusal,
    ConfigurationRefusalCode,
    ConfigurationSource,
    RuntimeConfiguration,
    resolve_runtime_configuration,
)
from agentic_investment_os.evidence.attention import BuildAttentionInputs
from agentic_investment_os.evidence.capture import CaptureEvidence
from agentic_investment_os.research.production import ProductionResearch

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from agentic_investment_os.application.lifecycle import Clock
    from agentic_investment_os.domain.governance import (
        MarketSessionEligibility,
        OperatorApprovalVerifier,
    )
    from agentic_investment_os.research.model import ResearchRoleModel


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Provide UTC wall-clock time only from the composition boundary."""

    def now(self) -> datetime:
        return datetime.now(UTC)


def configure_advance(  # noqa: PLR0913 - composition names each untrusted boundary explicitly.
    sources: Sequence[ConfigurationSource],
    *,
    repository_root: Path,
    recorded_universe: object,
    recorded_evidence: object,
    session_eligibility: MarketSessionEligibility,
    recorded_model: ResearchRoleModel,
    recorded_official_evidence: object = None,
    clock: Clock | None = None,
    approval_verifier: OperatorApprovalVerifier | None = None,
) -> Advance | ConfigurationRefusal:
    """Validate configuration and compose Advance without credentials or network access.

    Opening storage initializes or validates the current schema before returning and raises
    ``LifecyclePersistenceError`` when durable state fails startup validation.
    """
    resolution = resolve_runtime_configuration(sources, repository_root=repository_root)
    if isinstance(resolution, ConfigurationRefusal):
        return resolution
    if not isinstance(resolution, RuntimeConfiguration):
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(resolution)  # pragma: no cover
    database = prepare_runtime_database(resolution.state_root)
    if isinstance(database, RuntimeRootRefusal):
        return ConfigurationRefusal(
            code=ConfigurationRefusalCode.INVALID_STATE_ROOT,
            fields=("state_root",),
        )
    if isinstance(database, PreparedRuntimeDatabase):
        evidence_vault = FilesystemEvidenceVault(resolution.state_root / "evidence-vault")
        trusted_clock = clock if clock is not None else SystemClock()
        belief_ledger = SQLiteBeliefLedger(database.path)
        return Advance(
            ledger=SQLiteLifecycleLedger(database.path),
            configuration_version=resolution.schema_version,
            configuration_hash=resolution.fingerprint,
            universe_source=RecordedUniverseSource(recorded_universe),
            enabled_asset_classes=resolution.enabled_asset_classes,
            universe_policy=resolution.universe_policy,
            evidence_capture=CaptureEvidence(
                policy=resolution.evidence_policy,
                source=RecordedEvidenceSource(
                    recorded_evidence,
                    recorded_official_evidence,
                    resolution.evidence_policy.data_regime,
                ),
                vault=evidence_vault,
            ),
            attention_policy=resolution.attention_policy,
            attention_inputs=BuildAttentionInputs(evidence_vault),
            clock=trusted_clock,
            constitution_registry=ConstitutionRegistry(
                SQLiteConstitutionGovernance(database.path),
                approval_verifier,
                session_eligibility,
            ),
            production_research=ProductionResearch(
                resolution.research_policy,
                SQLiteProductionCallLedger(
                    database.path,
                    resolution.research_policy,
                    belief_ledger,
                    evidence_vault,
                ),
                recorded_model,
            ),
            evidence_vault=evidence_vault,
            memory=Record(
                belief_ledger,
                evidence_vault,
                trusted_clock,
            ),
            memory_refusal_ledger=belief_ledger,
        )
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(database)  # pragma: no cover


def configure_status(
    sources: Sequence[ConfigurationSource],
    *,
    repository_root: Path,
    approval_verifier: OperatorApprovalVerifier | None = None,
) -> Status | ConfigurationRefusal:
    """Validate configuration and compose rebuildable lifecycle status.

    Existing storage is validated against the current schema before returning and raises
    ``LifecyclePersistenceError`` when durable state fails startup validation; missing authoritative
    storage is never recreated.
    """
    resolution = resolve_runtime_configuration(sources, repository_root=repository_root)
    if isinstance(resolution, ConfigurationRefusal):
        return resolution
    if not isinstance(resolution, RuntimeConfiguration):
        # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
        assert_never(resolution)  # pragma: no cover
    database = open_runtime_database(resolution.state_root)
    if isinstance(database, RuntimeRootRefusal):
        return ConfigurationRefusal(
            code=ConfigurationRefusalCode.INVALID_STATE_ROOT,
            fields=("state_root",),
        )
    if isinstance(database, PreparedRuntimeDatabase):
        evidence_vault = FilesystemEvidenceVault.reference_validator(
            resolution.state_root / "evidence-vault"
        )
        belief_ledger = SQLiteBeliefLedger.open_existing(database.path)
        return Status(
            SQLiteLifecycleLedger.open_existing(database.path),
            evidence_vault,
            ConstitutionStatus(
                SQLiteConstitutionGovernance.open_existing(database.path),
                approval_verifier,
            ),
            SQLiteProductionCallLedger(
                database.path,
                resolution.research_policy,
                belief_ledger,
                evidence_vault,
            ),
            Record(
                belief_ledger,
                evidence_vault,
                SystemClock(),
            ),
        )
    # Strict mypy proves this line unreachable; removing it is runtime-equivalent.
    assert_never(database)  # pragma: no cover
