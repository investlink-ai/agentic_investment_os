"""Persist content-addressed evidence below one validated private runtime root."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from contextlib import suppress
from pathlib import Path

from agentic_investment_os.domain.lifecycle import EvidenceCaptureReference, is_sha256
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.evidence.capture import (
    CaptureIntent,
    CaptureOutcome,
    EvidenceArtifact,
    EvidenceCaptureSummary,
    EvidenceFeed,
    EvidencePersistenceError,
    EvidencePolicy,
    EvidenceSourceIdentityConflictError,
    EvidenceStoredRecord,
    InvalidEvidenceError,
    parse_capture_intent,
    parse_capture_outcome,
    validate_capture_outcome_association,
)

__all__ = ("FilesystemEvidenceVault",)

_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_MAXIMUM_METADATA_BYTES = 2_000_000
_MAXIMUM_CONTENT_BYTES = 1_000_000
_VAULT_INVALID = "Evidence Vault state is invalid"
_VAULT_WRITE_FAILED = "Evidence Vault publication failed"
_SOURCE_IDENTITY_CONFLICT = "official source identity has conflicting immutable facts"
_SOURCE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_SOURCE_BINDING_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "feed",
        "source_identity",
        "content_hash",
        "published_at",
    }
)
_OFFICIAL_FEEDS = frozenset(
    {
        EvidenceFeed.SEC_EDGAR,
        EvidenceFeed.ISSUER_INVESTOR_RELATIONS,
        EvidenceFeed.FEDERAL_RESERVE,
        EvidenceFeed.BLS,
        EvidenceFeed.BEA,
    }
)


class FilesystemEvidenceVault:
    """Store immutable content once and append one outcome per effect-local intent."""

    def __init__(
        self,
        root: Path,
        *,
        _create: bool | None = True,
    ) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise EvidencePersistenceError(_VAULT_INVALID)
        self._root = root
        self._contents = root / "contents"
        self._intents = root / "intents"
        self._outcomes = root / "outcomes"
        self._policies = root / "policies"
        self._source_bindings = root / "source-bindings"
        self._temporary = root / "tmp"
        for directory in (
            self._root,
            self._contents,
            self._intents,
            self._outcomes,
            self._policies,
            self._source_bindings,
            self._temporary,
        ):
            if _create:
                _prepare_private_directory(directory)
            elif _create is False:
                _validate_private_directory(directory)

    @classmethod
    def open_existing(cls, root: Path) -> FilesystemEvidenceVault:
        """Open a complete private Vault layout without recreating missing state."""
        return cls(root, _create=False)

    @classmethod
    def reference_validator(
        cls,
        root: Path,
    ) -> FilesystemEvidenceVault:
        """Validate referenced Vault state lazily without creating an absent empty Vault."""
        return cls(root, _create=None)

    def append_policy(
        self,
        policy: EvidencePolicy,
        capture_intents: tuple[CaptureIntent, ...],
    ) -> None:
        """Append the complete canonical policy before its capture intents."""
        policy.__post_init__()
        if (
            type(capture_intents) is not tuple
            or tuple(intent.request for intent in capture_intents) != policy.requests
            or any(
                intent.data_regime != policy.data_regime
                or parse_capture_intent(intent.to_payload()) != intent
                for intent in capture_intents
            )
        ):
            raise EvidencePersistenceError(_VAULT_INVALID)
        policy_path = self._policies / f"{policy.policy_id}.json"
        if not _entry_exists(policy_path) and any(
            _entry_exists(self._intents / f"{intent.intent_id}.json")
            or _entry_exists(self._outcomes / f"{intent.intent_id}.json")
            for intent in capture_intents
        ):
            raise EvidencePersistenceError(_VAULT_INVALID)
        self._append_bytes(
            policy_path,
            _canonical_json(policy.to_payload()),
        )

    def load_policy(self, policy_id: str) -> EvidencePolicy:
        """Reconstruct one content-addressed historical capture policy."""
        if not is_sha256(policy_id):
            raise EvidencePersistenceError(_VAULT_INVALID)
        return _load_policy(self._policies / f"{policy_id}.json")

    def append_intent(self, intent: CaptureIntent) -> None:
        """Append one validated intent before its recorded adapter effect."""
        payload = intent.to_payload()
        if parse_capture_intent(payload) != intent:
            raise EvidencePersistenceError(_VAULT_INVALID)
        self._append_bytes(self._intents / f"{intent.intent_id}.json", _canonical_json(payload))

    def load_outcome(self, intent: CaptureIntent) -> CaptureOutcome | None:
        """Return a prior disposition only after validating its intent association."""
        intent_path = self._intents / f"{intent.intent_id}.json"
        outcome_path = self._outcomes / f"{intent.intent_id}.json"
        if not intent_path.exists():
            if outcome_path.exists():
                raise EvidencePersistenceError(_VAULT_INVALID)
            return None
        if _load_intent(intent_path) != intent:  # pragma: no cover - content hash binds equality.
            raise EvidencePersistenceError(_VAULT_INVALID)
        if not outcome_path.exists():
            return None
        outcome = _load_outcome(outcome_path)
        _validate_association(intent, outcome)
        if outcome.artifact is not None:
            content = _load_content(
                self._contents / outcome.artifact.content_hash,
                outcome.artifact.content_hash,
            )
            record = _validated_stored_record(outcome, content)
            if record is None:  # pragma: no cover - guarded by the artifact branch.
                raise EvidencePersistenceError(_VAULT_INVALID)
            self._validate_source_bindings((record,))
        return outcome

    def append_outcome(
        self,
        intent: CaptureIntent,
        outcome: CaptureOutcome,
        content: bytes | None,
    ) -> None:
        """Publish validated content before append-only observation metadata."""
        intent_path = self._intents / f"{intent.intent_id}.json"
        if _load_intent(intent_path) != intent:
            raise EvidencePersistenceError(_VAULT_INVALID)
        payload = outcome.to_payload()
        if parse_capture_outcome(payload) != outcome:
            raise EvidencePersistenceError(_VAULT_INVALID)
        _validate_association(intent, outcome)
        record = _validated_stored_record(outcome, content)
        if record is not None:
            self._append_source_binding(record.artifact)
            self._append_bytes(self._contents / record.artifact.content_hash, record.content)
        self._append_bytes(
            self._outcomes / f"{intent.intent_id}.json",
            _canonical_json(payload),
        )

    def stored_records(self) -> tuple[EvidenceStoredRecord, ...]:
        """Reconstruct every distinct observation and validate its immutable content."""
        records: dict[str, EvidenceStoredRecord] = {}
        for path in _published_files(self._outcomes):
            outcome = _load_outcome(path)
            intent = _load_intent(self._intents / f"{outcome.intent_id}.json")
            _validate_association(intent, outcome)
            artifact = outcome.artifact
            if artifact is None:
                continue
            content = _load_content(self._contents / artifact.content_hash, artifact.content_hash)
            try:
                record = EvidenceStoredRecord(artifact, content)
            except InvalidEvidenceError as error:  # pragma: no cover - load validates both hashes.
                raise EvidencePersistenceError(_VAULT_INVALID) from error
            # A SHA-bound observation ID makes a distinct record under the same key infeasible;
            # retain the check as explicit collision defense.
            previous = records.get(artifact.observation_id)
            if previous is not None and previous != record:  # pragma: no cover - SHA-256 identity.
                raise EvidencePersistenceError(_VAULT_INVALID)
            records[artifact.observation_id] = record
        ordered = tuple(records[key] for key in sorted(records))
        self._validate_source_bindings(ordered)
        return ordered

    def stored_records_for_artifacts(
        self,
        artifact_ids: tuple[str, ...],
    ) -> tuple[EvidenceStoredRecord, ...]:
        """Reconstruct and validate only the exact artifact set named by a checkpoint."""
        requested = set(artifact_ids)
        if (
            type(artifact_ids) is not tuple
            or len(requested) != len(artifact_ids)
            or any(not is_sha256(artifact_id) for artifact_id in artifact_ids)
        ):
            raise EvidencePersistenceError(_VAULT_INVALID)
        records: dict[str, EvidenceStoredRecord] = {}
        for path in _published_files(self._outcomes):
            outcome = _load_outcome(path)
            artifact = outcome.artifact
            if artifact is None or artifact.artifact_id not in requested:
                continue
            intent = _load_intent(self._intents / f"{outcome.intent_id}.json")
            _validate_association(intent, outcome)
            content = _load_content(self._contents / artifact.content_hash, artifact.content_hash)
            try:
                record = EvidenceStoredRecord(artifact, content)
            except InvalidEvidenceError as error:  # pragma: no cover - load validates both hashes.
                raise EvidencePersistenceError(_VAULT_INVALID) from error
            previous = records.get(artifact.artifact_id)
            if previous is not None and previous != record:  # pragma: no cover - SHA-256 identity.
                raise EvidencePersistenceError(_VAULT_INVALID)
            records[artifact.artifact_id] = record
        ordered = tuple(records[key] for key in sorted(records))
        self._validate_source_bindings(ordered)
        return ordered

    def validate_references(
        self,
        checkpoints: tuple[EvidenceCaptureReference, ...],
    ) -> None:
        """Require every lifecycle reference to resolve through valid intent-first outcomes."""
        if type(checkpoints) is not tuple or any(
            type(checkpoint) is not EvidenceCaptureReference for checkpoint in checkpoints
        ):
            raise EvidencePersistenceError(_VAULT_INVALID)
        try:
            self._root.lstat()
        except FileNotFoundError:
            if checkpoints:
                raise EvidencePersistenceError(_VAULT_INVALID) from None
            return
        except OSError as error:
            raise EvidencePersistenceError(_VAULT_INVALID) from error
        for directory in (
            self._root,
            self._contents,
            self._intents,
            self._outcomes,
            self._policies,
            self._source_bindings,
            self._temporary,
        ):
            _validate_private_directory(directory)
        self.stored_records()
        for reference in checkpoints:
            policy = self.load_policy(reference.checkpoint.policy_id)
            if reference.data_regime != policy.data_regime:
                raise EvidencePersistenceError(_VAULT_INVALID)
            outcomes: list[CaptureOutcome] = []
            for request in policy.requests:
                intent = CaptureIntent.create(
                    run_id=reference.run_id,
                    universe_snapshot_id=reference.universe_snapshot_id,
                    cutoff=reference.cutoff,
                    data_regime=reference.data_regime,
                    request=request,
                )
                intent_path = self._intents / f"{intent.intent_id}.json"
                outcome_path = self._outcomes / f"{intent.intent_id}.json"
                if _load_intent(intent_path) != intent:
                    raise EvidencePersistenceError(_VAULT_INVALID)
                outcome = _load_outcome(outcome_path)
                _validate_association(intent, outcome)
                outcomes.append(outcome)
            summary = EvidenceCaptureSummary.from_policy(policy, tuple(outcomes))
            if (summary.policy_id, summary.artifact_ids, summary.refusal_ids) != (
                reference.checkpoint.policy_id,
                reference.checkpoint.artifact_ids,
                reference.checkpoint.refusal_ids,
            ):
                raise EvidencePersistenceError(_VAULT_INVALID)

    def _append_source_binding(self, artifact: EvidenceArtifact) -> None:
        binding = _source_binding(artifact)
        if binding is None:
            return
        binding_id, content = binding
        path = self._source_bindings / f"{binding_id}.json"
        try:
            self._append_bytes(path, content)
        except EvidencePersistenceError as error:
            try:
                existing = _load_source_binding(path)
            except EvidencePersistenceError as binding_error:
                raise error from binding_error
            published_at = artifact.published_at
            if published_at is None:  # pragma: no cover - official artifacts require publication.
                raise error
            target = (
                artifact.feed,
                artifact.source_identity,
                artifact.content_hash,
                published_at.isoformat(),
            )
            if existing == target or existing[:2] != target[:2]:
                raise error from None
            raise EvidenceSourceIdentityConflictError(_SOURCE_IDENTITY_CONFLICT) from error

    def _validate_source_bindings(
        self,
        records: tuple[EvidenceStoredRecord, ...],
    ) -> None:
        published = {
            path.stem: _load_source_binding(path)
            for path in _published_files(self._source_bindings)
        }
        expected: dict[str, bytes] = {}
        for record in records:
            binding = _source_binding(record.artifact)
            if binding is None:
                continue
            binding_id, content = binding
            previous = expected.get(binding_id)
            if previous is not None and previous != content:
                raise EvidencePersistenceError(_VAULT_INVALID)
            expected[binding_id] = content
            actual = published.get(binding_id)
            if actual is None or _canonical_json(_source_binding_payload(*actual)) != content:
                raise EvidencePersistenceError(_VAULT_INVALID)

    def _append_bytes(  # noqa: PLR0912 - validate publication components independently.
        self,
        destination: Path,
        content: bytes,
    ) -> None:
        if destination.parent not in (  # pragma: no cover - callers use fixed vault children.
            self._contents,
            self._intents,
            self._outcomes,
            self._policies,
            self._source_bindings,
        ):
            raise EvidencePersistenceError(_VAULT_INVALID)
        if destination.parent == self._contents:
            if not is_sha256(destination.name):  # pragma: no cover - artifact validation binds it.
                raise EvidencePersistenceError(_VAULT_INVALID)
            if len(content) > _MAXIMUM_CONTENT_BYTES:  # pragma: no cover - candidate bounds it.
                raise EvidencePersistenceError(_VAULT_INVALID)
        else:
            if not destination.name.endswith(  # pragma: no cover - fixed metadata suffix.
                ".json"
            ):
                raise EvidencePersistenceError(_VAULT_INVALID)
            if not is_sha256(destination.stem):  # pragma: no cover - intent identity binds it.
                raise EvidencePersistenceError(_VAULT_INVALID)
            if len(content) > _MAXIMUM_METADATA_BYTES:
                raise EvidencePersistenceError(_VAULT_INVALID)
        root_descriptor = _open_private_directory(self._root)
        parent_descriptor: int | None = None
        temporary_directory_descriptor: int | None = None
        descriptor: int | None = None
        temporary_name: str | None = None
        try:
            parent_descriptor = _open_private_directory_at(
                root_descriptor,
                destination.parent.name,
            )
            temporary_directory_descriptor = _open_private_directory_at(
                root_descriptor,
                self._temporary.name,
            )
            descriptor, temporary_name = _create_temporary_file(temporary_directory_descriptor)
            os.fchmod(descriptor, _PRIVATE_FILE_MODE)
            _write_all(descriptor, content)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.link(
                temporary_name,
                destination.name,
                src_dir_fd=temporary_directory_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            os.fsync(parent_descriptor)
        except FileExistsError:
            if (
                parent_descriptor is None
                or _read_private_file_at(
                    parent_descriptor,
                    destination.name,
                    maximum_bytes=_MAXIMUM_METADATA_BYTES,
                )
                != content
            ):
                raise EvidencePersistenceError(_VAULT_INVALID) from None
        except OSError as error:
            raise EvidencePersistenceError(_VAULT_WRITE_FAILED) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_name is not None and temporary_directory_descriptor is not None:
                with suppress(OSError):
                    os.unlink(temporary_name, dir_fd=temporary_directory_descriptor)
            if temporary_directory_descriptor is not None:
                os.close(temporary_directory_descriptor)
            if parent_descriptor is not None:
                os.close(parent_descriptor)
            os.close(root_descriptor)


def _validated_stored_record(
    outcome: CaptureOutcome,
    content: bytes | None,
) -> EvidenceStoredRecord | None:
    artifact = outcome.artifact
    if artifact is None:
        if content is not None:
            raise EvidencePersistenceError(_VAULT_INVALID)
        return None
    if content is None:
        raise EvidencePersistenceError(_VAULT_INVALID)
    try:
        return EvidenceStoredRecord(artifact, content)
    except InvalidEvidenceError as error:
        raise EvidencePersistenceError(_VAULT_INVALID) from error


def _validate_association(intent: CaptureIntent, outcome: CaptureOutcome) -> None:
    try:
        validate_capture_outcome_association(intent, outcome)
    except InvalidEvidenceError as error:
        raise EvidencePersistenceError(_VAULT_INVALID) from error


def _source_binding(artifact: EvidenceArtifact) -> tuple[str, bytes] | None:
    if artifact.feed not in _OFFICIAL_FEEDS:
        return None
    published_at = artifact.published_at
    if published_at is None:  # pragma: no cover - official artifact validation requires it.
        raise EvidencePersistenceError(_VAULT_INVALID)
    binding_id = _source_binding_id(artifact.feed, artifact.source_identity)
    payload = _source_binding_payload(
        artifact.feed,
        artifact.source_identity,
        artifact.content_hash,
        published_at.isoformat(),
    )
    return binding_id, _canonical_json(payload)


def _source_binding_id(feed: EvidenceFeed, source_identity: str) -> str:
    subject = _canonical_json(
        {
            "feed": feed.value,
            "source_identity": source_identity,
        }
    )
    return hashlib.sha256(subject).hexdigest()


def _source_binding_payload(
    feed: EvidenceFeed,
    source_identity: str,
    content_hash: str,
    published_at: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_kind": "official_source_binding",
        "feed": feed.value,
        "source_identity": source_identity,
        "content_hash": content_hash,
        "published_at": published_at,
    }


def _load_source_binding(path: Path) -> tuple[EvidenceFeed, str, str, str]:
    value = _load_json(path)
    fields = _exact_source_binding_mapping(value)
    if fields is None:
        raise EvidencePersistenceError(_VAULT_INVALID)
    raw_feed = fields["feed"]
    source_identity = fields["source_identity"]
    content_hash = fields["content_hash"]
    published_at = fields["published_at"]
    if (
        fields["schema_version"] != 1
        or fields["record_kind"] != "official_source_binding"
        or type(raw_feed) is not str
        or type(source_identity) is not str
        or _SOURCE_IDENTITY.fullmatch(source_identity) is None
        or type(content_hash) is not str
        or not is_sha256(content_hash)
        or type(published_at) is not str
    ):
        raise EvidencePersistenceError(_VAULT_INVALID)
    try:
        feed = EvidenceFeed(raw_feed)
        canonical_published_at = UtcInstant.parse(published_at).isoformat()
    except (InvalidUtcInstantError, ValueError) as error:
        raise EvidencePersistenceError(_VAULT_INVALID) from error
    if (
        feed not in _OFFICIAL_FEEDS
        or path.stem != _source_binding_id(feed, source_identity)
        or canonical_published_at != published_at
    ):
        raise EvidencePersistenceError(_VAULT_INVALID)
    return feed, source_identity, content_hash, published_at


def _exact_source_binding_mapping(value: object) -> dict[str, object] | None:
    if type(value) is not dict:
        return None
    result: dict[str, object] = {}
    for key, item in value.items():
        if type(key) is not str:
            return None
        result[key] = item
    if result.keys() != _SOURCE_BINDING_FIELDS:
        return None
    return result


def _prepare_private_directory(directory: Path) -> None:
    try:
        os.mkdir(  # noqa: PTH102 - os.mkdir is structurally non-recursive.
            directory, _PRIVATE_DIRECTORY_MODE
        )
    except FileExistsError:
        pass
    except OSError as error:
        raise EvidencePersistenceError(_VAULT_INVALID) from error
    _validate_private_directory(directory)


def _validate_private_directory(directory: Path) -> None:
    try:
        details = directory.lstat()
    except OSError as error:
        raise EvidencePersistenceError(_VAULT_INVALID) from error
    if (
        not stat.S_ISDIR(details.st_mode)
        or stat.S_IMODE(details.st_mode) != _PRIVATE_DIRECTORY_MODE
    ):
        raise EvidencePersistenceError(_VAULT_INVALID)


def _published_files(directory: Path) -> tuple[Path, ...]:
    try:
        children = tuple(directory.iterdir())
    except OSError as error:
        raise EvidencePersistenceError(_VAULT_INVALID) from error
    for child in children:
        if child.is_symlink():
            raise EvidencePersistenceError(_VAULT_INVALID)
        if not child.is_file():
            raise EvidencePersistenceError(_VAULT_INVALID)
        if not child.name.endswith(".json"):
            raise EvidencePersistenceError(_VAULT_INVALID)
        if not is_sha256(child.stem):
            raise EvidencePersistenceError(_VAULT_INVALID)
    return tuple(sorted(children))


def _entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise EvidencePersistenceError(_VAULT_INVALID) from error
    return True


def _load_intent(path: Path) -> CaptureIntent:
    value = _load_json(path)
    intent = parse_capture_intent(value)
    if intent is None or path.stem != intent.intent_id:
        raise EvidencePersistenceError(_VAULT_INVALID)
    return intent


def _load_outcome(path: Path) -> CaptureOutcome:
    value = _load_json(path)
    outcome = parse_capture_outcome(value)
    if outcome is None or path.stem != outcome.intent_id:
        raise EvidencePersistenceError(_VAULT_INVALID)
    return outcome


def _load_policy(path: Path) -> EvidencePolicy:
    value = _load_json(path)
    policy = EvidencePolicy.parse(value)
    if policy is None or path.stem != policy.policy_id:
        raise EvidencePersistenceError(_VAULT_INVALID)
    return policy


def _load_json(path: Path) -> object:
    content = _read_private_file(path, maximum_bytes=_MAXIMUM_METADATA_BYTES)
    try:
        value: object = json.loads(content)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise EvidencePersistenceError(_VAULT_INVALID) from error
    if _canonical_json(value) != content:
        raise EvidencePersistenceError(_VAULT_INVALID)
    return value


def _load_content(path: Path, expected_hash: str) -> bytes:
    # Validated artifact hashes construct content paths; keep both checks as defense against future
    # callers crossing this filesystem boundary.
    if path.name != expected_hash or not is_sha256(
        expected_hash
    ):  # pragma: no cover - caller supplies a hash-named path.
        raise EvidencePersistenceError(_VAULT_INVALID)
    content = _read_private_file(path, maximum_bytes=_MAXIMUM_CONTENT_BYTES)
    actual_hash = hashlib.sha256(content).hexdigest()
    if actual_hash != expected_hash:
        raise EvidencePersistenceError(_VAULT_INVALID)
    return content


def _read_private_file(path: Path, *, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or stat.S_IMODE(details.st_mode) != _PRIVATE_FILE_MODE
                or details.st_size > maximum_bytes
            ):
                raise EvidencePersistenceError(_VAULT_INVALID)
            content = os.read(descriptor, details.st_size)
            if len(content) != details.st_size:
                raise EvidencePersistenceError(_VAULT_INVALID)
            if os.fstat(descriptor).st_size != details.st_size:
                raise EvidencePersistenceError(_VAULT_INVALID)
            return content
        finally:
            os.close(descriptor)
    except (OSError, ValueError) as error:
        raise EvidencePersistenceError(_VAULT_INVALID) from error


def _read_private_file_at(
    directory_descriptor: int,
    name: str,
    *,
    maximum_bytes: int,
) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or stat.S_IMODE(details.st_mode) != _PRIVATE_FILE_MODE
                or details.st_size > maximum_bytes
            ):
                raise EvidencePersistenceError(_VAULT_INVALID)
            content = os.read(descriptor, details.st_size)
            if len(content) != details.st_size or os.fstat(descriptor).st_size != details.st_size:
                raise EvidencePersistenceError(_VAULT_INVALID)
            return content
        finally:
            os.close(descriptor)
    except (OSError, ValueError) as error:
        raise EvidencePersistenceError(_VAULT_INVALID) from error


def _open_private_directory(path: Path) -> int:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise EvidencePersistenceError(_VAULT_INVALID) from error
    try:
        _validate_private_directory_descriptor(descriptor)
    except EvidencePersistenceError:
        os.close(descriptor)
        raise
    return descriptor


def _open_private_directory_at(parent_descriptor: int, name: str) -> int:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
    except OSError as error:
        raise EvidencePersistenceError(_VAULT_INVALID) from error
    try:
        _validate_private_directory_descriptor(descriptor)
    except EvidencePersistenceError:
        os.close(descriptor)
        raise
    return descriptor


def _validate_private_directory_descriptor(descriptor: int) -> None:
    try:
        details = os.fstat(descriptor)
    except OSError as error:
        raise EvidencePersistenceError(_VAULT_INVALID) from error
    if (
        not stat.S_ISDIR(details.st_mode)
        or stat.S_IMODE(details.st_mode) != _PRIVATE_DIRECTORY_MODE
    ):
        raise EvidencePersistenceError(_VAULT_INVALID)


def _create_temporary_file(directory_descriptor: int) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for _ in range(10):
        name = f"publish-{secrets.token_hex(16)}"
        try:
            return os.open(
                name,
                flags,
                _PRIVATE_FILE_MODE,
                dir_fd=directory_descriptor,
            ), name
        except FileExistsError:
            continue
    raise OSError(_VAULT_WRITE_FAILED)


def _write_all(descriptor: int, content: bytes) -> None:
    written = 0
    while written < len(content):
        count = os.write(descriptor, content[written:])
        if count <= 0:
            raise OSError(_VAULT_WRITE_FAILED)
        written += count


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
