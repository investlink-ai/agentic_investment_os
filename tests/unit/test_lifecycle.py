from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_investment_os.application.lifecycle import Advance
from agentic_investment_os.domain.lifecycle import (
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRequest,
    IdempotencyKey,
    InputRefusal,
    InputRefusalCode,
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


@dataclass(frozen=True)
class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 21, 22, 0, tzinfo=UTC)


@dataclass
class ConcurrentCompletionLedger:
    completion_point: str
    receipt: AdvanceReceipt

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
            return self.receipt
        return LifecycleProgress(request, identity, None, 0)

    def complete_reconciliation(
        self, _key: IdempotencyKey, _recorded_at: datetime
    ) -> LifecycleProgress | AdvanceReceipt:
        return self.receipt

    def pin_run_inputs(self, _key: IdempotencyKey, _recorded_at: datetime) -> AdvanceReceipt:
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
            ConfigurationRefusal(ConfigurationRefusalCode.UNKNOWN_FIELD, ("extra",)),
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
    sentinel = "value-never-persisted"
    resolution = resolve_runtime_configuration(
        (
            ConfigurationSource(
                "hostile",
                {
                    "schema_version": 1,
                    "state_root": str(tmp_path / "state"),
                    "broker_secret": sentinel,
                },
            ),
        ),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert isinstance(resolution, ConfigurationRefusal)
    assert sentinel not in repr(resolution)


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

    assert observed is receipt
