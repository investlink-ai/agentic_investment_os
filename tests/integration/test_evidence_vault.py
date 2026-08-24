from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agentic_investment_os.adapters.filesystem_evidence import FilesystemEvidenceVault
from agentic_investment_os.adapters.recorded_evidence import (
    RecordedAlpacaEvidenceSource,
    RecordedEvidenceSource,
    RecordedOfficialEvidenceSource,
)
from agentic_investment_os.domain.lifecycle import (
    EvidenceCaptureCheckpoint,
    EvidenceCaptureReference,
)
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.evidence.capture import (
    CaptureEvidence,
    CaptureIntent,
    CaptureOutcome,
    EvidenceArtifact,
    EvidenceCandidate,
    EvidenceCaptureStatus,
    EvidenceCaptureSummary,
    EvidenceFeed,
    EvidenceKind,
    EvidenceLookup,
    EvidencePersistenceError,
    EvidencePolicy,
    EvidenceQuery,
    EvidenceRefusalReason,
    EvidenceRetrieval,
    EvidenceSourceResult,
    EvidenceStoredRecord,
)
from tests._evidence import (
    alpaca_evidence_policy,
    evidence_item,
    evidence_policy,
    official_evidence_item,
    recorded_evidence,
    recorded_official_evidence,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

RUN_ID = "a" * 64
SECOND_RUN_ID = "b" * 64
EARLIER_RUN_ID = "9" * 64
SNAPSHOT_ID = "c" * 64
EXPECTED_CONTENT_COUNT = 2
EXPECTED_OUTCOME_COUNT = 4
EXPECTED_RECORD_COUNT = 3
EXPECTED_RETRIEVAL_COUNT = 2
OPTIONAL_OFFICIAL_RETRIEVAL_COUNT = 5
COMPLETE_RETRIEVAL_COUNT = 7
SECOND_FSTAT_CALL = 2
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
INJECTED_DIRECTORY_FAILURE = "injected directory failure"
INJECTED_PUBLICATION_FAILURE = "injected publication failure"


def _instant(hour: int, minute: int = 0) -> UtcInstant:
    return UtcInstant.from_datetime(datetime(2026, 8, 21, hour, minute, tzinfo=UTC))


def _policy() -> EvidencePolicy:
    policy = EvidencePolicy.parse(alpaca_evidence_policy())
    assert isinstance(policy, EvidencePolicy)
    return policy


def _complete_policy() -> EvidencePolicy:
    policy = EvidencePolicy.parse(evidence_policy())
    assert isinstance(policy, EvidencePolicy)
    return policy


@dataclass
class _CountingSource:
    delegate: RecordedAlpacaEvidenceSource
    calls: list[str] = field(default_factory=list)

    def retrieve(self, request: EvidenceRetrieval) -> EvidenceSourceResult:
        self.calls.append(request.retrieval_identity)
        return self.delegate.retrieve(request)


@dataclass
class _InterruptingSource:
    calls: int = 0

    def retrieve(self, request: EvidenceRetrieval) -> EvidenceSourceResult:
        self.calls += 1
        raise SystemExit(request.retrieval_identity)


def _capture(
    vault: FilesystemEvidenceVault,
    payload: object,
    *,
    run_id: str = RUN_ID,
    cutoff: UtcInstant | None = None,
) -> EvidenceCaptureSummary:
    return CaptureEvidence(
        _policy(),
        RecordedAlpacaEvidenceSource(payload),
        vault,
    )(
        run_id=run_id,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=_instant(20) if cutoff is None else cutoff,
        data_regime="alpaca-basic-iex-v1",
    )


def _intent_and_outcome() -> tuple[CaptureIntent, CaptureOutcome, bytes]:
    request = _policy().requests[0]
    intent = CaptureIntent.create(
        run_id=RUN_ID,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=_instant(20),
        data_regime="alpaca-basic-iex-v1",
        request=request,
    )
    candidate = RecordedAlpacaEvidenceSource(recorded_evidence()).retrieve(request)
    assert isinstance(candidate, EvidenceCandidate)
    artifact = EvidenceArtifact.from_candidate(candidate)
    return (
        intent,
        CaptureOutcome(intent.intent_id, EvidenceCaptureStatus.CAPTURED, None, artifact),
        candidate.content,
    )


def _association_violation_artifact(field: str) -> tuple[EvidenceArtifact, bytes]:
    request = _policy().requests[0]
    candidate = RecordedAlpacaEvidenceSource(recorded_evidence()).retrieve(request)
    assert isinstance(candidate, EvidenceCandidate)
    if field == "kind":
        changed = EvidenceCandidate.create(
            retrieval_identity=candidate.retrieval_identity,
            source_identity="news-1",
            kind=EvidenceKind.NEWS,
            source_event_at=None,
            published_at=_instant(19),
            first_observed_at=_instant(19, 5),
            data_regime=candidate.data_regime,
            feed=EvidenceFeed.ALPACA_NEWS,
            entity_mappings=candidate.entity_mappings,
            content=candidate.content,
        )
    elif field == "after_cutoff":
        changed = EvidenceCandidate.create(
            retrieval_identity=candidate.retrieval_identity,
            source_identity=candidate.source_identity,
            kind=candidate.kind,
            source_event_at=_instant(21),
            published_at=None,
            first_observed_at=_instant(21),
            data_regime=candidate.data_regime,
            feed=candidate.feed,
            entity_mappings=candidate.entity_mappings,
            content=candidate.content,
        )
    elif field == "stale_at_cutoff":
        changed = EvidenceCandidate.create(
            retrieval_identity=candidate.retrieval_identity,
            source_identity=candidate.source_identity,
            kind=candidate.kind,
            source_event_at=_instant(17),
            published_at=None,
            first_observed_at=_instant(17),
            data_regime=candidate.data_regime,
            feed=candidate.feed,
            entity_mappings=candidate.entity_mappings,
            content=candidate.content,
        )
    elif field == "retrieval_identity":
        changed = replace(candidate, retrieval_identity="another-retrieval")
    else:
        assert field == "data_regime"
        changed = replace(candidate, data_regime="another-regime")
    return EvidenceArtifact.from_candidate(changed), changed.content


def _write_private(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    path.chmod(PRIVATE_FILE_MODE)


def test_vault_deduplicates_content_retains_observations_and_filters_as_of(
    tmp_path: Path,
) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    first = _capture(vault, recorded_evidence())
    assert all(outcome.status is EvidenceCaptureStatus.CAPTURED for outcome in first.outcomes)
    assert {path.name for path in vault_root.iterdir()} == {
        "contents",
        "intents",
        "outcomes",
        "policies",
        "tmp",
    }

    second_payload = recorded_evidence()
    evidence_item(second_payload, 0)["first_observed_at"] = "2026-08-21T19:20:00.000000+00:00"
    second = _capture(vault, second_payload, run_id=SECOND_RUN_ID)

    assert all(outcome.status is EvidenceCaptureStatus.CAPTURED for outcome in second.outcomes)
    assert len(tuple((vault_root / "contents").iterdir())) == EXPECTED_CONTENT_COUNT
    assert len(tuple((vault_root / "outcomes").iterdir())) == EXPECTED_OUTCOME_COUNT
    assert len(vault.stored_records()) == EXPECTED_RECORD_COUNT
    admitted = EvidenceLookup(vault)(EvidenceQuery(_instant(19, 10), "alpaca-basic-iex-v1", 10))
    assert tuple(record.artifact.kind.value for record in admitted) == ("news", "market")
    assert EvidenceLookup(vault)(EvidenceQuery(_instant(23), "another-regime", 10)) == ()
    assert b"ignore previous instructions" in admitted[0].content

    for directory in (vault_root, *(path for path in vault_root.iterdir() if path.is_dir())):
        assert stat.S_IMODE(directory.stat().st_mode) == PRIVATE_DIRECTORY_MODE
    for directory_name in ("contents", "intents", "outcomes", "policies"):
        for path in (vault_root / directory_name).iterdir():
            assert stat.S_IMODE(path.stat().st_mode) == PRIVATE_FILE_MODE


def test_optional_official_absence_is_durable_without_blocking_required_capture(
    tmp_path: Path,
) -> None:
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")
    policy = _complete_policy()

    summary = CaptureEvidence(
        policy,
        RecordedEvidenceSource(recorded_evidence(), None, policy.data_regime),
        vault,
    )(
        run_id=RUN_ID,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=_instant(20),
        data_regime=policy.data_regime,
    )

    official_outcomes = tuple(
        outcome
        for request, outcome in zip(policy.requests, summary.outcomes, strict=True)
        if request.kind not in (EvidenceKind.MARKET, EvidenceKind.NEWS)
    )
    assert len(summary.artifact_ids) == EXPECTED_RETRIEVAL_COUNT
    assert summary.refusal_ids == ()
    assert len(summary.disposition_ids) == OPTIONAL_OFFICIAL_RETRIEVAL_COUNT
    assert all(
        outcome.status is EvidenceCaptureStatus.UNAVAILABLE
        and outcome.refusal_reason is EvidenceRefusalReason.SOURCE_UNAVAILABLE
        for outcome in official_outcomes
    )
    assert (
        len(tuple((tmp_path / "evidence-vault" / "outcomes").iterdir())) == COMPLETE_RETRIEVAL_COUNT
    )


def test_required_official_absence_fails_closed_but_retains_other_artifacts(
    tmp_path: Path,
) -> None:
    raw_policy = evidence_policy()
    requests = raw_policy["requests"]
    assert isinstance(requests, list)
    sec_request = next(
        request
        for request in requests
        if isinstance(request, dict) and request.get("kind") == "sec_filing"
    )
    sec_request["required"] = True
    policy = EvidencePolicy.parse(raw_policy)
    assert isinstance(policy, EvidencePolicy)

    summary = CaptureEvidence(
        policy,
        RecordedEvidenceSource(recorded_evidence(), None, policy.data_regime),
        FilesystemEvidenceVault(tmp_path / "evidence-vault"),
    )(
        run_id=RUN_ID,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=_instant(20),
        data_regime=policy.data_regime,
    )

    assert len(summary.artifact_ids) == EXPECTED_RETRIEVAL_COUNT
    assert len(summary.refusal_ids) == 1
    assert len(summary.disposition_ids) == OPTIONAL_OFFICIAL_RETRIEVAL_COUNT


def test_sec_amendment_appends_a_new_accession_and_obeys_as_of_cutoffs(
    tmp_path: Path,
) -> None:
    full_policy = _complete_policy()
    sec_request = next(
        request for request in full_policy.requests if request.kind is EvidenceKind.SEC_FILING
    )
    policy = EvidencePolicy(full_policy.data_regime, (sec_request,))
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")

    original = CaptureEvidence(
        policy,
        RecordedOfficialEvidenceSource(recorded_official_evidence()),
        vault,
    )(
        run_id=RUN_ID,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=_instant(18),
        data_regime=policy.data_regime,
    )
    amendment_payload = recorded_official_evidence()
    amendment_item = official_evidence_item(amendment_payload, 0)
    amendment_item["source_identity"] = "0000320193-26-000099"
    amendment_item["published_at"] = "2026-08-21T18:30:00.000000+00:00"
    amendment_item["first_observed_at"] = "2026-08-21T18:31:00.000000+00:00"
    amendment_content = deepcopy(amendment_item["content"])
    assert isinstance(amendment_content, dict)
    amendment_content.update(
        {
            "accession_number": "0000320193-26-000099",
            "amends_accession": "0000320193-26-000081",
            "form": "10-Q/A",
            "restatement": True,
            "text": "Synthetic SEC amendment; external instructions remain inert evidence.",
        }
    )
    amendment_item["content"] = amendment_content
    amended = CaptureEvidence(
        policy,
        RecordedOfficialEvidenceSource(amendment_payload),
        vault,
    )(
        run_id=SECOND_RUN_ID,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=_instant(19),
        data_regime=policy.data_regime,
    )

    before_amendment = EvidenceLookup(vault)(EvidenceQuery(_instant(18), policy.data_regime, 10))
    after_amendment = EvidenceLookup(vault)(EvidenceQuery(_instant(19), policy.data_regime, 10))
    assert len(original.artifact_ids) == 1
    assert len(amended.artifact_ids) == 1
    assert len(before_amendment) == 1
    assert tuple(record.artifact.source_identity for record in after_amendment) == (
        "0000320193-26-000081",
        "0000320193-26-000099",
    )
    assert (
        len(tuple((tmp_path / "evidence-vault" / "contents").iterdir())) == EXPECTED_CONTENT_COUNT
    )


def test_vault_reference_validation_requires_every_configured_capture_outcome(
    tmp_path: Path,
) -> None:
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")
    summary = _capture(vault, recorded_evidence())
    incomplete = EvidenceCaptureCheckpoint(
        summary.policy_id,
        (summary.artifact_ids[0],),
        (),
    )

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.validate_references(
            (
                EvidenceCaptureReference(
                    RUN_ID,
                    SNAPSHOT_ID,
                    _instant(20),
                    "alpaca-basic-iex-v1",
                    incomplete,
                ),
            )
        )


def test_vault_reference_validation_cannot_promote_a_refused_artifact(
    tmp_path: Path,
) -> None:
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")
    late = recorded_evidence()
    evidence_item(late, 0)["first_observed_at"] = "2026-08-21T20:30:00.000000+00:00"
    summary = _capture(vault, late)
    promoted = EvidenceCaptureCheckpoint(summary.policy_id, summary.artifact_ids, ())

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.validate_references(
            (
                EvidenceCaptureReference(
                    RUN_ID,
                    SNAPSHOT_ID,
                    _instant(20),
                    "alpaca-basic-iex-v1",
                    promoted,
                ),
            )
        )


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_vault_reference_validation_requires_a_valid_pinned_policy_snapshot(
    tmp_path: Path,
    damage: str,
) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    summary = _capture(vault, recorded_evidence())
    policy_path = vault_root / "policies" / f"{summary.policy_id}.json"
    if damage == "missing":
        policy_path.unlink()
    else:
        assert damage == "corrupt"
        policy_path.write_bytes(b"{}")

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.validate_references(
            (
                EvidenceCaptureReference(
                    RUN_ID,
                    SNAPSHOT_ID,
                    _instant(20),
                    "alpaca-basic-iex-v1",
                    EvidenceCaptureCheckpoint(
                        summary.policy_id,
                        summary.artifact_ids,
                        summary.refusal_ids,
                    ),
                ),
            )
        )


def test_completed_retry_reuses_durable_outcomes_without_another_source_effect(
    tmp_path: Path,
) -> None:
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")
    source = _CountingSource(RecordedAlpacaEvidenceSource(recorded_evidence()))
    capture = CaptureEvidence(_policy(), source, vault)
    first = capture(
        run_id=RUN_ID,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=_instant(20),
        data_regime="alpaca-basic-iex-v1",
    )
    replayed = capture(
        run_id=RUN_ID,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=_instant(20),
        data_regime="alpaca-basic-iex-v1",
    )

    assert replayed == first
    assert first.policy_id == _policy().policy_id
    assert source.calls == ["market-session-bars", "news-session-latest"]


def test_retry_after_interruption_resumes_from_the_durable_intent(
    tmp_path: Path,
) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    interrupted_source = _InterruptingSource()
    capture = CaptureEvidence(_policy(), interrupted_source, vault)

    with pytest.raises(SystemExit, match="market-session-bars"):
        capture(
            run_id=RUN_ID,
            universe_snapshot_id=SNAPSHOT_ID,
            cutoff=_instant(20),
            data_regime="alpaca-basic-iex-v1",
        )

    assert interrupted_source.calls == 1
    assert len(tuple((vault_root / "intents").iterdir())) == 1
    assert tuple((vault_root / "outcomes").iterdir()) == ()

    resumed = _capture(vault, recorded_evidence())

    assert len(resumed.outcomes) == EXPECTED_RETRIEVAL_COUNT
    assert len(tuple((vault_root / "intents").iterdir())) == EXPECTED_RETRIEVAL_COUNT
    assert len(tuple((vault_root / "outcomes").iterdir())) == EXPECTED_RETRIEVAL_COUNT


def test_retry_after_interruption_rejects_a_deleted_policy_snapshot(
    tmp_path: Path,
) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    capture = CaptureEvidence(_policy(), _InterruptingSource(), vault)

    with pytest.raises(SystemExit, match="market-session-bars"):
        capture(
            run_id=RUN_ID,
            universe_snapshot_id=SNAPSHOT_ID,
            cutoff=_instant(20),
            data_regime="alpaca-basic-iex-v1",
        )
    (vault_root / "policies" / f"{_policy().policy_id}.json").unlink()

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        _capture(vault, recorded_evidence())


def test_late_and_stale_artifacts_remain_recorded_but_are_explicit_refusals(
    tmp_path: Path,
) -> None:
    late_payload = recorded_evidence()
    evidence_item(late_payload, 0)["first_observed_at"] = "2026-08-21T20:01:00.000000+00:00"
    late = _capture(
        FilesystemEvidenceVault(tmp_path / "late"),
        late_payload,
    )
    stale = _capture(
        FilesystemEvidenceVault(tmp_path / "stale"),
        recorded_evidence(),
        cutoff=_instant(23),
    )

    assert late.outcomes[0].status is EvidenceCaptureStatus.REFUSED
    assert late.outcomes[0].artifact is not None
    assert all(outcome.status is EvidenceCaptureStatus.STALE for outcome in stale.outcomes)
    assert all(outcome.artifact is not None for outcome in stale.outcomes)


@pytest.mark.parametrize("damage", ["metadata", "missing_content"])
def test_reopen_fails_closed_on_corrupt_metadata_or_missing_content(
    tmp_path: Path,
    damage: str,
) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    _capture(vault, recorded_evidence())
    if damage == "metadata":
        outcome = next((vault_root / "outcomes").iterdir())
        outcome.write_text(json.dumps({"corrupt": True}))
    else:
        content = next((vault_root / "contents").iterdir())
        content.unlink()

    reopened = FilesystemEvidenceVault(vault_root)

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        reopened.stored_records()


def test_vault_rejects_invalid_roots_and_public_permissions(tmp_path: Path) -> None:
    public_root = tmp_path / "public"
    public_root.mkdir(mode=0o755)
    public_root.chmod(0o755)
    file_root = tmp_path / "file"
    file_root.write_text("not a directory")

    invalid_roots = (
        Path("relative"),
        tmp_path / "missing-parent" / "vault",
        public_root,
        file_root,
    )
    for root in invalid_roots:
        with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
            FilesystemEvidenceVault(root)


@pytest.mark.parametrize("symlink_location", ["root", "child"])
def test_vault_rejects_symlinked_private_directories(
    tmp_path: Path,
    symlink_location: str,
) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    target.chmod(PRIVATE_DIRECTORY_MODE)
    root = tmp_path / "evidence-vault"
    if symlink_location == "root":
        root.symlink_to(target, target_is_directory=True)
    else:
        root.mkdir(mode=PRIVATE_DIRECTORY_MODE)
        root.chmod(PRIVATE_DIRECTORY_MODE)
        (root / "contents").symlink_to(target, target_is_directory=True)

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        FilesystemEvidenceVault(root)


def test_vault_translates_a_private_directory_inspection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evidence-vault"
    root.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    root.chmod(PRIVATE_DIRECTORY_MODE)

    def fail_lstat(_path: Path) -> os.stat_result:
        raise OSError(INJECTED_DIRECTORY_FAILURE)

    monkeypatch.setattr(Path, "lstat", fail_lstat)

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        FilesystemEvidenceVault(root)


def test_vault_rejects_invalid_intent_and_outcome_associations(tmp_path: Path) -> None:
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")
    intent, outcome, content = _intent_and_outcome()
    forged_intent = replace(intent, intent_id="f" * 64)

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.append_intent(forged_intent)
    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.append_outcome(intent, outcome, content)

    vault.append_intent(intent)
    wrong_outcome = replace(outcome, intent_id="f" * 64)
    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.append_outcome(intent, wrong_outcome, content)
    artifact = outcome.artifact
    assert artifact is not None
    forged_artifact = replace(artifact, artifact_id="f" * 64)
    forged_outcome = replace(outcome, artifact=forged_artifact)
    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.append_outcome(intent, forged_outcome, content)
    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.append_outcome(intent, outcome, None)
    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.append_outcome(intent, outcome, b"changed")
    vault.append_outcome(intent, outcome, content)
    conflicting = CaptureOutcome(
        intent.intent_id,
        EvidenceCaptureStatus.REFUSED,
        EvidenceRefusalReason.SOURCE_REFUSED,
        None,
    )
    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.append_outcome(intent, conflicting, None)
    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.append_outcome(intent, conflicting, b"unexpected")


def test_vault_revalidates_normalized_content_before_publication(tmp_path: Path) -> None:
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")
    intent, outcome, _ = _intent_and_outcome()
    artifact = outcome.artifact
    assert artifact is not None
    malformed_content = b'{"bars":[]}'
    source_candidate = RecordedAlpacaEvidenceSource(recorded_evidence()).retrieve(intent.request)
    assert isinstance(source_candidate, EvidenceCandidate)
    malformed_candidate = replace(
        source_candidate,
        content=malformed_content,
    )
    malformed_artifact = EvidenceArtifact.from_candidate(malformed_candidate)
    malformed_outcome = replace(outcome, artifact=malformed_artifact)
    vault.append_intent(intent)

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.append_outcome(intent, malformed_outcome, malformed_content)


@pytest.mark.parametrize(
    "field",
    ["retrieval_identity", "kind", "data_regime", "after_cutoff", "stale_at_cutoff"],
)
def test_vault_rejects_resealed_outcomes_that_contradict_their_intent(
    tmp_path: Path,
    field: str,
) -> None:
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")
    intent, _, _ = _intent_and_outcome()
    artifact, content = _association_violation_artifact(field)
    outcome = CaptureOutcome(
        intent.intent_id,
        EvidenceCaptureStatus.CAPTURED,
        None,
        artifact,
    )
    vault.append_intent(intent)

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.append_outcome(intent, outcome, content)


def test_vault_revalidates_intent_association_when_loading_an_outcome(tmp_path: Path) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    intent, _, _ = _intent_and_outcome()
    artifact, content = _association_violation_artifact("data_regime")
    outcome = CaptureOutcome(intent.intent_id, EvidenceCaptureStatus.CAPTURED, None, artifact)
    vault.append_intent(intent)
    _write_private(
        vault_root / "outcomes" / f"{intent.intent_id}.json",
        json.dumps(outcome.to_payload(), sort_keys=True, separators=(",", ":")).encode(),
    )
    _write_private(vault_root / "contents" / artifact.content_hash, content)

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.load_outcome(intent)


def test_vault_rejects_rehashed_noncanonical_content_before_publication(tmp_path: Path) -> None:
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")
    intent, _, _ = _intent_and_outcome()
    source_candidate = RecordedAlpacaEvidenceSource(recorded_evidence()).retrieve(intent.request)
    assert isinstance(source_candidate, EvidenceCandidate)
    noncanonical_content = b'{"z":1, "a":2}'
    candidate = replace(source_candidate, content=noncanonical_content)
    artifact = EvidenceArtifact.from_candidate(candidate)
    outcome = CaptureOutcome(intent.intent_id, EvidenceCaptureStatus.CAPTURED, None, artifact)
    vault.append_intent(intent)

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.append_outcome(intent, outcome, noncanonical_content)


def test_vault_loads_nonartifact_outcomes_and_rejects_orphans(tmp_path: Path) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    intent, _, _ = _intent_and_outcome()
    refused = CaptureOutcome(
        intent.intent_id,
        EvidenceCaptureStatus.REFUSED,
        EvidenceRefusalReason.SOURCE_REFUSED,
        None,
    )
    assert vault.load_outcome(intent) is None
    vault.append_intent(intent)
    assert vault.load_outcome(intent) is None
    vault.append_outcome(intent, refused, None)
    assert vault.load_outcome(intent) == refused
    assert vault.stored_records() == ()

    orphan_root = tmp_path / "orphan"
    orphan = FilesystemEvidenceVault(orphan_root)
    _write_private(
        orphan_root / "outcomes" / f"{intent.intent_id}.json",
        json.dumps(refused.to_payload(), sort_keys=True, separators=(",", ":")).encode(),
    )
    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        orphan.load_outcome(intent)

    captured_intent, captured, content = _intent_and_outcome()
    captured_artifact = captured.artifact
    assert captured_artifact is not None
    _write_private(
        orphan_root / "outcomes" / f"{captured_intent.intent_id}.json",
        json.dumps(captured.to_payload(), sort_keys=True, separators=(",", ":")).encode(),
    )
    _write_private(orphan_root / "contents" / captured_artifact.content_hash, content)
    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        orphan.stored_records()


def test_vault_scans_past_an_earlier_nonartifact_outcome(tmp_path: Path) -> None:
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")
    intent, outcome, content = _intent_and_outcome()
    earlier_intent = CaptureIntent.create(
        run_id=EARLIER_RUN_ID,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=_instant(20),
        data_regime="alpaca-basic-iex-v1",
        request=_policy().requests[0],
    )
    assert earlier_intent.intent_id < intent.intent_id
    refusal = CaptureOutcome(
        earlier_intent.intent_id,
        EvidenceCaptureStatus.REFUSED,
        EvidenceRefusalReason.SOURCE_REFUSED,
        None,
    )
    vault.append_intent(earlier_intent)
    vault.append_outcome(earlier_intent, refusal, None)
    vault.append_intent(intent)
    vault.append_outcome(intent, outcome, content)

    artifact = outcome.artifact
    assert artifact is not None
    assert vault.stored_records() == (EvidenceStoredRecord(artifact, content),)


def test_vault_accepts_news_content_at_the_normalized_size_limit(tmp_path: Path) -> None:
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")
    request = next(item for item in _policy().requests if item.kind is EvidenceKind.NEWS)
    intent = CaptureIntent.create(
        run_id=RUN_ID,
        universe_snapshot_id=SNAPSHOT_ID,
        cutoff=_instant(20),
        data_regime="alpaca-basic-iex-v1",
        request=request,
    )
    source_candidate = RecordedAlpacaEvidenceSource(recorded_evidence()).retrieve(intent.request)
    assert isinstance(source_candidate, EvidenceCandidate)
    content = json.dumps(
        {
            "headline": "Bounded headline",
            "id": "news-at-limit",
            "summary": "x" * 100_000,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    candidate = replace(
        source_candidate,
        source_identity="news-at-limit",
        content=content,
    )
    artifact = EvidenceArtifact.from_candidate(candidate)
    outcome = CaptureOutcome(intent.intent_id, EvidenceCaptureStatus.CAPTURED, None, artifact)
    vault.append_intent(intent)
    vault.append_outcome(intent, outcome, content)

    assert vault.stored_records() == (EvidenceStoredRecord(artifact, content),)


def test_vault_handles_concurrent_identical_appends(
    tmp_path: Path,
) -> None:
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")
    intent, _, _ = _intent_and_outcome()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(vault.append_intent, intent) for _ in range(2))
        for future in futures:
            future.result()

    assert vault.load_outcome(intent) is None
    assert tuple((tmp_path / "evidence-vault" / "tmp").iterdir()) == ()


@pytest.mark.parametrize("failure_point", ["temporary_create", "fchmod", "link"])
def test_vault_translates_publication_failures_and_removes_temporary_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    intent, _, _ = _intent_and_outcome()

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError(INJECTED_PUBLICATION_FAILURE)

    target = (
        "agentic_investment_os.adapters.filesystem_evidence._create_temporary_file"
        if failure_point == "temporary_create"
        else f"agentic_investment_os.adapters.filesystem_evidence.os.{failure_point}"
    )
    monkeypatch.setattr(target, fail)
    with pytest.raises(EvidencePersistenceError, match="Evidence Vault publication failed"):
        vault.append_intent(intent)
    assert tuple((vault_root / "tmp").iterdir()) == ()


def test_vault_publication_leaves_no_temporary_files_or_open_file_descriptors(
    tmp_path: Path,
) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    intent, _, _ = _intent_and_outcome()
    descriptors_before = len(tuple(Path("/dev/fd").iterdir()))

    vault.append_intent(intent)

    assert tuple((vault_root / "tmp").iterdir()) == ()
    assert len(tuple(Path("/dev/fd").iterdir())) == descriptors_before
    assert tuple((vault_root / "intents").iterdir()) == (
        vault_root / "intents" / f"{intent.intent_id}.json",
    )


@pytest.mark.parametrize("replaced_directory", ["tmp", "intents"])
def test_vault_publication_refuses_a_directory_replaced_after_validation(
    tmp_path: Path,
    replaced_directory: str,
) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    intent, _, _ = _intent_and_outcome()
    original = vault_root / replaced_directory
    original.rename(vault_root / f"original-{replaced_directory}")
    redirected = tmp_path / f"redirected-{replaced_directory}"
    redirected.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    original.symlink_to(redirected, target_is_directory=True)

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.append_intent(intent)

    assert tuple(redirected.iterdir()) == ()


def test_vault_preserves_publication_failure_when_temporary_cleanup_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")
    intent, _, _ = _intent_and_outcome()

    def fail_link(*_args: object, **_kwargs: object) -> None:
        raise OSError(INJECTED_PUBLICATION_FAILURE)

    def fail_cleanup(*_args: object, **_kwargs: object) -> None:
        raise OSError(INJECTED_PUBLICATION_FAILURE)

    monkeypatch.setattr("agentic_investment_os.adapters.filesystem_evidence.os.link", fail_link)
    monkeypatch.setattr(
        "agentic_investment_os.adapters.filesystem_evidence.os.unlink", fail_cleanup
    )

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault publication failed"):
        vault.append_intent(intent)


@pytest.mark.parametrize(
    "invalid_kind",
    ["missing_suffix", "invalid_stem", "directory", "symlink"],
)
def test_vault_rejects_each_invalid_published_file_shape(
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    _ = vault
    outcomes = vault_root / "outcomes"
    valid_stem = "a" * 64
    if invalid_kind == "missing_suffix":
        _write_private(outcomes / valid_stem, b"{}")
    elif invalid_kind == "invalid_stem":
        _write_private(outcomes / "invalid.json", b"{}")
    elif invalid_kind == "directory":
        (outcomes / f"{valid_stem}.json").mkdir()
    else:
        target = vault_root / "target"
        _write_private(target, b"{}")
        (outcomes / f"{valid_stem}.json").symlink_to(target)

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.stored_records()


def test_vault_rejects_directory_read_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    original_iterdir = Path.iterdir

    def fail_outcomes(path: Path) -> Iterator[Path]:
        if path == vault_root / "outcomes":
            raise OSError(INJECTED_DIRECTORY_FAILURE)
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_outcomes)
    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.stored_records()


def test_vault_does_not_follow_a_symlinked_metadata_file(tmp_path: Path) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    intent, _, _ = _intent_and_outcome()
    vault.append_intent(intent)
    intent_path = vault_root / "intents" / f"{intent.intent_id}.json"
    target = vault_root / "intent-target"
    intent_path.rename(target)
    intent_path.symlink_to(target)

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.load_outcome(intent)


@pytest.mark.parametrize("read_race", ["short_read", "changed_size"])
def test_vault_rejects_metadata_that_changes_while_being_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    read_race: str,
) -> None:
    vault = FilesystemEvidenceVault(tmp_path / "evidence-vault")
    intent, _, _ = _intent_and_outcome()
    vault.append_intent(intent)
    if read_race == "short_read":
        monkeypatch.setattr(
            "agentic_investment_os.adapters.filesystem_evidence.os.read",
            lambda _descriptor, _size: b"",
        )
    else:
        assert read_race == "changed_size"
        original_fstat = os.fstat
        calls = 0

        def changed_fstat(descriptor: int) -> os.stat_result:
            nonlocal calls
            calls += 1
            details = original_fstat(descriptor)
            if calls == SECOND_FSTAT_CALL:
                values = list(details)
                values[6] += 1
                return os.stat_result(values)
            return details

        monkeypatch.setattr(
            "agentic_investment_os.adapters.filesystem_evidence.os.fstat",
            changed_fstat,
        )

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.load_outcome(intent)


@pytest.mark.parametrize("damage", ["malformed_json", "noncanonical_json", "public_mode"])
def test_vault_rejects_hostile_metadata_files(tmp_path: Path, damage: str) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    intent, outcome, content = _intent_and_outcome()
    vault.append_intent(intent)
    vault.append_outcome(intent, outcome, content)
    outcome_path = vault_root / "outcomes" / f"{intent.intent_id}.json"
    if damage == "malformed_json":
        outcome_path.write_bytes(b"{")
    elif damage == "noncanonical_json":
        outcome_path.write_text(json.dumps(outcome.to_payload()))
    else:
        outcome_path.chmod(0o644)

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.stored_records()


def test_vault_rejects_changed_content_bytes(tmp_path: Path) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    intent, outcome, content = _intent_and_outcome()
    vault.append_intent(intent)
    vault.append_outcome(intent, outcome, content)
    artifact = outcome.artifact
    assert artifact is not None
    content_path = vault_root / "contents" / artifact.content_hash
    _write_private(content_path, b"changed")

    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.stored_records()


@pytest.mark.parametrize("record_kind", ["intent", "outcome"])
def test_vault_rejects_metadata_published_under_the_wrong_identity(
    tmp_path: Path,
    record_kind: str,
) -> None:
    vault_root = tmp_path / "evidence-vault"
    vault = FilesystemEvidenceVault(vault_root)
    intent, outcome, content = _intent_and_outcome()
    vault.append_intent(intent)
    if record_kind == "intent":
        path = vault_root / "intents" / f"{intent.intent_id}.json"
        _write_private(path, b"{}")
        with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
            vault.load_outcome(intent)
        return

    vault.append_outcome(intent, outcome, content)
    path = vault_root / "outcomes" / f"{intent.intent_id}.json"
    changed_path = vault_root / "outcomes" / f"{'f' * 64}.json"
    path.rename(changed_path)
    with pytest.raises(EvidencePersistenceError, match="Evidence Vault state is invalid"):
        vault.stored_records()
