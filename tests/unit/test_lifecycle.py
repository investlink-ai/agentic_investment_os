from __future__ import annotations

import shutil
import subprocess
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

import pytest

from agentic_investment_os.application.lifecycle import Advance
from agentic_investment_os.domain.lifecycle import (
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRecovery,
    AdvanceRequest,
    CheckpointResult,
    CheckpointWrite,
    IdempotencyKey,
    InputRefusal,
    InputRefusalCode,
    InvalidLifecycleStateError,
    LifecyclePhase,
    LifecycleProgress,
    PinnedRunIdentity,
    StartResult,
)
from agentic_investment_os.entrypoints.configuration import (
    ConfigurationRefusal,
    ConfigurationRefusalCode,
    ConfigurationSource,
    RuntimeConfiguration,
    resolve_runtime_configuration,
)

SHA256_HEX_LENGTH = 64


def _initialize_git_repository(repository: Path) -> None:
    git = shutil.which("git")
    if git is None:
        message = "required test executable is unavailable: git"
        raise RuntimeError(message)
    subprocess.run(  # noqa: S603
        (git, "init", "--quiet"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


@dataclass(frozen=True)
class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 21, 22, 0, tzinfo=UTC)


@dataclass
class ConcurrentCompletionLedger:
    completion_point: str
    receipt: AdvanceReceipt

    def _progress(
        self,
        phase: LifecyclePhase | None,
        sequence: int,
    ) -> LifecycleProgress:
        assert self.receipt.pinned_run_identity is not None
        request = AdvanceRequest.parse(
            session="2026-08-21",
            mode="champion",
            idempotency_key="concurrent-request",
        )
        assert isinstance(request, AdvanceRequest)
        return LifecycleProgress(request, self.receipt.pinned_run_identity, phase, sequence)

    def load_by_idempotency_key(
        self, _key: IdempotencyKey
    ) -> LifecycleProgress | AdvanceReceipt | None:
        return None

    def start(
        self,
        request: AdvanceRequest,
        identity: PinnedRunIdentity,
        _recorded_at: datetime,
    ) -> StartResult:
        if self.completion_point == "start":
            return CheckpointResult(
                self._progress(LifecyclePhase.PIN_RUN_INPUTS, 2),
                CheckpointWrite.OBSERVED,
            )
        return CheckpointResult(
            LifecycleProgress(request, identity, None, 0),
            CheckpointWrite.APPENDED,
        )

    def complete_reconciliation(
        self, _key: IdempotencyKey, _recorded_at: datetime
    ) -> CheckpointResult | AdvanceReceipt:
        if self.completion_point == "reconcile_failure":
            return AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
        if self.completion_point == "incomplete_reconcile":
            return CheckpointResult(self._progress(None, 0), CheckpointWrite.OBSERVED)
        if self.completion_point in {"pin_failure", "pin_observed"}:
            return CheckpointResult(
                self._progress(LifecyclePhase.RECONCILE_PRIOR_STATE, 1),
                CheckpointWrite.APPENDED,
            )
        return CheckpointResult(
            self._progress(LifecyclePhase.PIN_RUN_INPUTS, 2),
            CheckpointWrite.OBSERVED,
        )

    def pin_run_inputs(
        self, _key: IdempotencyKey, _recorded_at: datetime
    ) -> CheckpointResult | AdvanceReceipt:
        if self.completion_point == "pin_failure":
            return AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
        if self.completion_point == "pin_observed":
            return CheckpointResult(
                self._progress(LifecyclePhase.PIN_RUN_INPUTS, 2),
                CheckpointWrite.OBSERVED,
            )
        message = "concurrent completion must return before pinning"
        raise AssertionError(message)

    def record_refusal(
        self,
        _key: IdempotencyKey | None,
        _reason_code: AdvanceFailureReason,
        _recorded_at: datetime,
    ) -> AdvanceReceipt:
        message = "valid concurrent completion cannot be refused"
        raise AssertionError(message)


def test_advance_request_validates_the_complete_boundary() -> None:
    request = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key="session-2026-08-21",
    )

    assert isinstance(request, AdvanceRequest)
    assert request.session.isoformat() == "2026-08-21"
    assert request.mode.value == "champion"
    assert request.idempotency_key.value == "session-2026-08-21"
    assert request.stream_id == "b24b0e025ab67f2594db49e7f5e1c7cfe8170645fe5b9defe068e8c715d7a9e5"
    identity = PinnedRunIdentity.create(
        request,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    assert identity.run_id == "e148a27c171f24e3f53a415fd06b16eea872c7dce9e8d0378f41d6031fe2df55"

    invalid_cases = (
        (
            {"session": "21-08-2026", "mode": "champion", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_SESSION,
        ),
        (
            {"session": "2026-08-21", "mode": "research-lab", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_MODE,
        ),
        (
            {"session": "2026-08-21", "mode": "champion", "idempotency_key": "contains space"},
            InputRefusalCode.INVALID_IDEMPOTENCY_KEY,
        ),
        (
            {"session": None, "mode": "champion", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_SESSION,
        ),
        (
            {"session": "20260821", "mode": "champion", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_SESSION,
        ),
    )

    for values, expected_code in invalid_cases:
        refusal = AdvanceRequest.parse(**values)
        assert isinstance(refusal, InputRefusal)
        assert refusal.code is expected_code


def test_advance_receipt_rejects_incomplete_success_and_failure_shapes() -> None:
    identity = PinnedRunIdentity(
        run_id="b" * SHA256_HEX_LENGTH,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )

    with pytest.raises(ValueError, match="advanced receipt requires completed recovery facts"):
        AdvanceReceipt(
            AdvanceDisposition.ADVANCED,
            LifecyclePhase.PIN_RUN_INPUTS,
            identity,
            None,
        )
    with pytest.raises(ValueError, match="advanced receipt requires completed recovery facts"):
        AdvanceReceipt(
            AdvanceDisposition.ADVANCED,
            None,
            identity,
            None,
            AdvanceRecovery.FRESH,
        )
    with pytest.raises(ValueError, match="failed receipt requires one bounded reason"):
        AdvanceReceipt(
            AdvanceDisposition.FAILED_CLOSED,
            None,
            None,
            None,
        )
    with pytest.raises(ValueError, match="failed receipt requires one bounded reason"):
        AdvanceReceipt(
            AdvanceDisposition.FAILED_CLOSED,
            None,
            None,
            AdvanceFailureReason.INVALID_DURABLE_STATE,
            AdvanceRecovery.RESUMED,
        )

    assert (
        AdvanceReceipt.advanced(identity, AdvanceRecovery.FRESH).recovery is AdvanceRecovery.FRESH
    )


def test_runtime_configuration_is_complete_immutable_and_deterministically_hashed(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    first = resolve_runtime_configuration(
        (
            ConfigurationSource("file", {"schema_version": 1}),
            ConfigurationSource("operator", {"schema_version": 1, "state_root": str(state_root)}),
        ),
        repository_root=Path(__file__).resolve().parents[2],
    )
    second = resolve_runtime_configuration(
        (
            ConfigurationSource("operator", {"state_root": str(state_root)}),
            ConfigurationSource("file", {"schema_version": 1}),
        ),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert isinstance(first, RuntimeConfiguration)
    assert isinstance(second, RuntimeConfiguration)
    assert first == second
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == SHA256_HEX_LENGTH
    immutable_field = "schema_version"
    with pytest.raises(FrozenInstanceError):
        setattr(first, immutable_field, 2)

    fixed = resolve_runtime_configuration(
        (ConfigurationSource("fixed", {"schema_version": 1, "state_root": "/runtime/state"}),),
        repository_root=Path(__file__).resolve().parents[2],
    )
    assert isinstance(fixed, RuntimeConfiguration)
    assert fixed.fingerprint == "970663be9297f29b85127d33893ea5dcc439d299ae3d939dc9cd1d0ff05d1aeb"

    unicode_path = resolve_runtime_configuration(
        (ConfigurationSource("unicode", {"schema_version": 1, "state_root": "/runtime/état"}),),
        repository_root=Path(__file__).resolve().parents[2],
    )
    assert isinstance(unicode_path, RuntimeConfiguration)
    assert (
        unicode_path.fingerprint
        == "78cd37a94fb428c1b9ac707fcee5c55e742974cc6fabc64813ec9552e46bfd49"
    )


@pytest.mark.parametrize(
    ("sources", "expected_refusal"),
    [
        (
            (ConfigurationSource("file", {"schema_version": 1}),),
            ConfigurationRefusal(ConfigurationRefusalCode.MISSING_FIELD, ("state_root",)),
        ),
        (
            (
                ConfigurationSource(
                    "file",
                    {"schema_version": 1, "state_root": "/runtime/state", "extra": True},
                ),
            ),
            ConfigurationRefusal(ConfigurationRefusalCode.UNKNOWN_FIELD),
        ),
        (
            (ConfigurationSource("file", {"schema_version": 2, "state_root": "/runtime/state"}),),
            ConfigurationRefusal(ConfigurationRefusalCode.UNSUPPORTED_VERSION, ("schema_version",)),
        ),
        (
            (ConfigurationSource("file", {"schema_version": 1, "state_root": "relative/state"}),),
            ConfigurationRefusal(ConfigurationRefusalCode.INVALID_STATE_ROOT, ("state_root",)),
        ),
        (
            (
                ConfigurationSource("file", {"schema_version": 1, "state_root": "/runtime/one"}),
                ConfigurationSource("operator", {"state_root": "/runtime/two"}),
            ),
            ConfigurationRefusal(ConfigurationRefusalCode.CONFLICTING_FIELD, ("state_root",)),
        ),
    ],
)
def test_runtime_configuration_rejects_invalid_sources(
    sources: tuple[ConfigurationSource, ...],
    expected_refusal: ConfigurationRefusal,
) -> None:
    resolution = resolve_runtime_configuration(
        sources,
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == expected_refusal


def test_rejected_configuration_does_not_disclose_unknown_values(tmp_path: Path) -> None:
    max_bounded_refusal_length = 100
    sentinel = "value-never-persisted"
    hostile_field = f"secret-field-{sentinel}-{'x' * 5_000}"
    resolution = resolve_runtime_configuration(
        (
            ConfigurationSource(
                "hostile",
                {
                    "schema_version": 1,
                    "state_root": str(tmp_path / "state"),
                    hostile_field: sentinel,
                },
            ),
        ),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(ConfigurationRefusalCode.UNKNOWN_FIELD)
    assert sentinel not in repr(resolution)
    assert len(repr(resolution)) < max_bounded_refusal_length


@pytest.mark.parametrize(
    "sources",
    [
        (
            ConfigurationSource("integer", {"schema_version": 1}),
            ConfigurationSource(
                "boolean", {"schema_version": True, "state_root": "/runtime/state"}
            ),
        ),
        (
            ConfigurationSource("boolean", {"schema_version": True}),
            ConfigurationSource("integer", {"schema_version": 1, "state_root": "/runtime/state"}),
        ),
    ],
)
def test_runtime_configuration_treats_equal_values_of_different_types_as_conflicts(
    sources: tuple[ConfigurationSource, ...],
) -> None:
    resolution = resolve_runtime_configuration(
        sources,
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.CONFLICTING_FIELD, ("schema_version",)
    )


def test_runtime_configuration_rejects_noncanonical_values_and_unignored_roots(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _initialize_git_repository(repository)
    unsupported_version = ConfigurationRefusal(
        ConfigurationRefusalCode.UNSUPPORTED_VERSION, ("schema_version",)
    )
    invalid_state_root = ConfigurationRefusal(
        ConfigurationRefusalCode.INVALID_STATE_ROOT, ("state_root",)
    )
    invalid_values = (
        (
            {"schema_version": "1", "state_root": str(tmp_path / "string-version")},
            unsupported_version,
        ),
        (
            {"schema_version": True, "state_root": str(tmp_path / "boolean-version")},
            unsupported_version,
        ),
        ({"schema_version": 1, "state_root": tmp_path / "path-object"}, invalid_state_root),
        (
            {"schema_version": 1, "state_root": str(tmp_path / "safe" / ".." / "state")},
            invalid_state_root,
        ),
        ({"schema_version": 1, "state_root": str(repository)}, invalid_state_root),
        ({"schema_version": 1, "state_root": str(repository / "var")}, invalid_state_root),
    )

    for values, expected_refusal in invalid_values:
        resolution = resolve_runtime_configuration(
            (ConfigurationSource("test", values),),
            repository_root=repository,
        )
        assert resolution == expected_refusal

    (repository / ".gitignore").write_text("/var/\n", encoding="utf-8")
    accepted = resolve_runtime_configuration(
        (
            ConfigurationSource(
                "test",
                {"schema_version": 1, "state_root": str(repository / "var")},
            ),
        ),
        repository_root=repository,
    )

    assert isinstance(accepted, RuntimeConfiguration)


@pytest.mark.parametrize(
    "ignore_rules",
    [
        "/var/\n!/var/\n",
        "/var/*\n!/var/lifecycle.sqlite3\n",
        "/var/lifecycle.sqlite3\n",
    ],
)
def test_runtime_configuration_rejects_unignored_database_paths(
    tmp_path: Path,
    ignore_rules: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _initialize_git_repository(repository)
    (repository / ".gitignore").write_text(ignore_rules, encoding="utf-8")

    resolution = resolve_runtime_configuration(
        (
            ConfigurationSource(
                "test",
                {"schema_version": 1, "state_root": str(repository / "var")},
            ),
        ),
        repository_root=repository,
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.INVALID_STATE_ROOT, ("state_root",)
    )


def test_runtime_configuration_checks_the_database_with_clean_git_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    git = "/path/to/git"
    commands: list[tuple[str, ...]] = []

    def find_git(name: str) -> str:
        assert name == "git"
        return git

    def run_git(
        command: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        **options: object,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == repository
        assert options == {
            "check": False,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        assert env["CONFIGURATION_TEST_VALUE"] == "preserved"
        assert "GIT_DIR" not in env
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("CONFIGURATION_TEST_VALUE", "preserved")
    monkeypatch.setenv("GIT_DIR", "/poisoned/repository")
    monkeypatch.setattr(
        "agentic_investment_os.entrypoints.configuration.shutil.which",
        find_git,
    )
    monkeypatch.setattr(
        "agentic_investment_os.entrypoints.configuration.subprocess.run",
        run_git,
    )

    resolution = resolve_runtime_configuration(
        (
            ConfigurationSource(
                "test",
                {"schema_version": 1, "state_root": str(repository / "var")},
            ),
        ),
        repository_root=repository,
    )

    assert isinstance(resolution, RuntimeConfiguration)
    assert commands == [
        (git, "check-ignore", "--quiet", "--", "var/"),
        (git, "check-ignore", "--quiet", "--", "var/lifecycle.sqlite3"),
    ]


def test_runtime_configuration_rejects_a_tracked_database_path(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _initialize_git_repository(repository)
    (repository / ".gitignore").write_text("/var/\n", encoding="utf-8")
    runtime_root = repository / "var"
    runtime_root.mkdir()
    database = runtime_root / "lifecycle.sqlite3"
    database.write_text("", encoding="utf-8")
    git = shutil.which("git")
    assert git is not None
    subprocess.run(  # noqa: S603
        (git, "add", "--force", "var/lifecycle.sqlite3"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )

    resolution = resolve_runtime_configuration(
        (
            ConfigurationSource(
                "test",
                {"schema_version": 1, "state_root": str(runtime_root)},
            ),
        ),
        repository_root=repository,
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.INVALID_STATE_ROOT, ("state_root",)
    )


def test_runtime_configuration_fails_closed_when_git_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _initialize_git_repository(repository)
    (repository / ".gitignore").write_text("/var/\n", encoding="utf-8")
    source = (
        ConfigurationSource(
            "test",
            {"schema_version": 1, "state_root": str(repository / "var")},
        ),
    )

    git = shutil.which("git")
    assert git is not None
    monkeypatch.setattr(
        "agentic_investment_os.entrypoints.configuration.shutil.which",
        lambda _name: None,
    )
    missing = resolve_runtime_configuration(source, repository_root=repository)

    monkeypatch.setattr(
        "agentic_investment_os.entrypoints.configuration.shutil.which",
        lambda _name: git,
    )

    def fail_to_start(*_args: object, **_kwargs: object) -> Never:
        raise OSError

    monkeypatch.setattr(
        "agentic_investment_os.entrypoints.configuration.subprocess.run",
        fail_to_start,
    )
    inaccessible = resolve_runtime_configuration(source, repository_root=repository)

    expected = ConfigurationRefusal(ConfigurationRefusalCode.INVALID_STATE_ROOT, ("state_root",))
    assert missing == expected
    assert inaccessible == expected


def test_runtime_configuration_rejects_nul_and_intermediate_symlinks(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(target, target_is_directory=True)

    hostile_roots = (f"{tmp_path}/nul\0state", str(linked_parent / "state"))
    for state_root in hostile_roots:
        resolution = resolve_runtime_configuration(
            (ConfigurationSource("hostile", {"schema_version": 1, "state_root": state_root}),),
            repository_root=repository,
        )
        assert resolution == ConfigurationRefusal(
            ConfigurationRefusalCode.INVALID_STATE_ROOT, ("state_root",)
        )


@pytest.mark.parametrize("completion_point", ["start", "reconcile"])
def test_advance_returns_a_concurrent_checkpoint_receipt(completion_point: str) -> None:
    receipt = AdvanceReceipt(
        AdvanceDisposition.ADVANCED,
        completed_phase=LifecyclePhase.PIN_RUN_INPUTS,
        pinned_run_identity=PinnedRunIdentity(
            run_id="b" * SHA256_HEX_LENGTH,
            configuration_version=1,
            configuration_hash="a" * SHA256_HEX_LENGTH,
        ),
        failure_reason=None,
        recovery=AdvanceRecovery.PREVIOUSLY_COMPLETED,
    )
    capability = Advance(
        ledger=ConcurrentCompletionLedger(completion_point, receipt),
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
        clock=FixedClock(),
    )

    observed = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert observed.disposition is receipt.disposition
    assert observed.completed_phase is receipt.completed_phase
    assert observed.pinned_run_identity is receipt.pinned_run_identity
    assert observed.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED


@pytest.mark.parametrize("failure_point", ["reconcile_failure", "pin_failure"])
def test_advance_returns_a_durable_checkpoint_failure(failure_point: str) -> None:
    identity = PinnedRunIdentity(
        run_id="b" * SHA256_HEX_LENGTH,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    capability = Advance(
        ledger=ConcurrentCompletionLedger(
            failure_point,
            AdvanceReceipt.advanced(identity, AdvanceRecovery.PREVIOUSLY_COMPLETED),
        ),
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
        clock=FixedClock(),
    )

    observed = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert observed == AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)


def test_advance_reports_a_checkpoint_completed_during_pinning() -> None:
    identity = PinnedRunIdentity(
        run_id="b" * SHA256_HEX_LENGTH,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    capability = Advance(
        ledger=ConcurrentCompletionLedger(
            "pin_observed",
            AdvanceReceipt.advanced(identity, AdvanceRecovery.PREVIOUSLY_COMPLETED),
        ),
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
        clock=FixedClock(),
    )

    observed = capability(
        session="2026-08-21",
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert observed == AdvanceReceipt.advanced(
        identity,
        AdvanceRecovery.PREVIOUSLY_COMPLETED,
    )


def test_advance_rejects_an_incomplete_checkpoint_result() -> None:
    identity = PinnedRunIdentity(
        run_id="b" * SHA256_HEX_LENGTH,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    capability = Advance(
        ledger=ConcurrentCompletionLedger(
            "incomplete_reconcile",
            AdvanceReceipt.advanced(identity, AdvanceRecovery.PREVIOUSLY_COMPLETED),
        ),
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
        clock=FixedClock(),
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle ledger returned an incomplete checkpoint result",
    ):
        capability(
            session="2026-08-21",
            mode="champion",
            idempotency_key="concurrent-request",
        )
