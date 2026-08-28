from __future__ import annotations

import shutil
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Never, override

import pytest

from agentic_investment_os.entrypoints.configuration import (
    ConfigurationRefusal,
    ConfigurationRefusalCode,
    ConfigurationSource,
    RuntimeConfiguration,
    resolve_runtime_configuration,
)
from tests._evidence import evidence_policy
from tests._universe import attention_policy, portfolio_policy, research_policy, universe_policy

SHA256_HEX_LENGTH = 64


class _PolicyMap(dict[str, object]):
    pass


class _PolicyList(list[object]):
    pass


class _EqualityBomb:
    @override
    def __eq__(self, other: object) -> bool:
        raise RuntimeError

    @override
    def __hash__(self) -> int:
        return id(self)


class _IntegerSubclass(int):
    pass


class _StringSubclass(str):
    __slots__ = ()


def _with_policy(values: dict[str, object]) -> dict[str, object]:
    return {
        **values,
        "enabled_asset_classes": ["us_equity"],
        "universe_policy": universe_policy(),
        "evidence_policy": evidence_policy(),
        "attention_policy": attention_policy(),
        "research_policy": research_policy(),
        "portfolio_policy": portfolio_policy(),
    }


def test_runtime_configuration_accepts_one_complete_balanced_portfolio_policy() -> None:
    resolution = resolve_runtime_configuration(
        (
            ConfigurationSource(
                "test", _with_policy({"schema_version": 1, "state_root": "/runtime/state"})
            ),
        ),
        repository_root=Path("/repository"),
    )

    assert isinstance(resolution, RuntimeConfiguration)
    assert resolution.portfolio_policy.to_payload() == portfolio_policy()


def _policy_with_non_text_key() -> dict[object, object]:
    policy: dict[object, object] = {}
    policy.update(universe_policy())
    policy[1] = "non-text-key"
    return policy


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


def test_runtime_configuration_is_complete_immutable_and_deterministically_hashed(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    first = resolve_runtime_configuration(
        (
            ConfigurationSource(
                "file",
                {
                    "schema_version": 1,
                    "enabled_asset_classes": ["us_equity"],
                    "universe_policy": universe_policy(),
                    "evidence_policy": evidence_policy(),
                    "attention_policy": attention_policy(),
                    "research_policy": research_policy(),
                    "portfolio_policy": portfolio_policy(),
                },
            ),
            ConfigurationSource(
                "operator",
                {
                    "schema_version": 1,
                    "state_root": str(state_root),
                    "enabled_asset_classes": ["us_equity"],
                    "universe_policy": universe_policy(),
                    "evidence_policy": evidence_policy(),
                    "attention_policy": attention_policy(),
                    "research_policy": research_policy(),
                    "portfolio_policy": portfolio_policy(),
                },
            ),
        ),
        repository_root=Path(__file__).resolve().parents[2],
    )
    second = resolve_runtime_configuration(
        (
            ConfigurationSource(
                "operator",
                {
                    "state_root": str(state_root),
                    "enabled_asset_classes": ["us_equity"],
                    "universe_policy": universe_policy(),
                    "evidence_policy": evidence_policy(),
                    "attention_policy": attention_policy(),
                    "research_policy": research_policy(),
                    "portfolio_policy": portfolio_policy(),
                },
            ),
            ConfigurationSource(
                "file",
                {
                    "schema_version": 1,
                    "enabled_asset_classes": ["us_equity"],
                    "universe_policy": universe_policy(),
                    "evidence_policy": evidence_policy(),
                    "attention_policy": attention_policy(),
                    "research_policy": research_policy(),
                    "portfolio_policy": portfolio_policy(),
                },
            ),
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
        (
            ConfigurationSource(
                "fixed", _with_policy({"schema_version": 1, "state_root": "/runtime/state"})
            ),
        ),
        repository_root=Path(__file__).resolve().parents[2],
    )
    assert isinstance(fixed, RuntimeConfiguration)
    assert fixed.fingerprint == "3ba370c125a34bbacad26572c1268c4b7114c8dc246a8f6ee6b4185d206000c3"

    unicode_path = resolve_runtime_configuration(
        (
            ConfigurationSource(
                "unicode", _with_policy({"schema_version": 1, "state_root": "/runtime/état"})
            ),
        ),
        repository_root=Path(__file__).resolve().parents[2],
    )
    assert isinstance(unicode_path, RuntimeConfiguration)
    assert (
        unicode_path.fingerprint
        == "a7336f8e6607f135340a95bdfe0785345eeeabfba967755551786a57645a432f"
    )


@pytest.mark.parametrize(
    ("sources", "expected_refusal"),
    [
        (
            (
                ConfigurationSource(
                    "file",
                    {
                        "schema_version": 1,
                        "enabled_asset_classes": ["us_equity"],
                        "universe_policy": universe_policy(),
                        "evidence_policy": evidence_policy(),
                        "attention_policy": attention_policy(),
                        "research_policy": research_policy(),
                        "portfolio_policy": portfolio_policy(),
                    },
                ),
            ),
            ConfigurationRefusal(ConfigurationRefusalCode.MISSING_FIELD, ("state_root",)),
        ),
        (
            (
                ConfigurationSource(
                    "file",
                    {
                        "schema_version": 1,
                        "state_root": "/runtime/state",
                        "enabled_asset_classes": ["us_equity"],
                        "evidence_policy": evidence_policy(),
                        "attention_policy": attention_policy(),
                        "research_policy": research_policy(),
                        "portfolio_policy": portfolio_policy(),
                    },
                ),
            ),
            ConfigurationRefusal(
                ConfigurationRefusalCode.MISSING_FIELD,
                ("universe_policy",),
            ),
        ),
        (
            (
                ConfigurationSource(
                    "file",
                    _with_policy(
                        {"schema_version": 1, "state_root": "/runtime/state", "extra": True}
                    ),
                ),
            ),
            ConfigurationRefusal(ConfigurationRefusalCode.UNKNOWN_FIELD),
        ),
        (
            (
                ConfigurationSource(
                    "file", _with_policy({"schema_version": 2, "state_root": "/runtime/state"})
                ),
            ),
            ConfigurationRefusal(ConfigurationRefusalCode.UNSUPPORTED_VERSION, ("schema_version",)),
        ),
        (
            (
                ConfigurationSource(
                    "file", _with_policy({"schema_version": 1, "state_root": "relative/state"})
                ),
            ),
            ConfigurationRefusal(ConfigurationRefusalCode.INVALID_STATE_ROOT, ("state_root",)),
        ),
        (
            (
                ConfigurationSource(
                    "file", _with_policy({"schema_version": 1, "state_root": "/runtime/one"})
                ),
                ConfigurationSource("operator", {"state_root": "/runtime/two"}),
            ),
            ConfigurationRefusal(ConfigurationRefusalCode.CONFLICTING_FIELD, ("state_root",)),
        ),
        (
            (
                ConfigurationSource(
                    "file", _with_policy({"schema_version": 1, "state_root": "/runtime/state"})
                ),
                ConfigurationSource(
                    "operator",
                    {
                        "universe_policy": {
                            **universe_policy(),
                            "minimum_price": "10",
                        }
                    },
                ),
            ),
            ConfigurationRefusal(
                ConfigurationRefusalCode.CONFLICTING_FIELD,
                ("universe_policy",),
            ),
        ),
        (
            (
                ConfigurationSource(
                    "file",
                    {
                        "schema_version": 1,
                        "state_root": "/runtime/state",
                        "enabled_asset_classes": ["us_equity"],
                        "universe_policy": {
                            **universe_policy(),
                            "approved_exchanges": ["NASDAQ", "OTC"],
                        },
                        "evidence_policy": evidence_policy(),
                        "attention_policy": attention_policy(),
                        "research_policy": research_policy(),
                        "portfolio_policy": portfolio_policy(),
                    },
                ),
            ),
            ConfigurationRefusal(
                ConfigurationRefusalCode.INVALID_UNIVERSE_POLICY,
                ("universe_policy",),
            ),
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


def test_runtime_configuration_refuses_an_invalid_production_research_policy() -> None:
    configuration = _with_policy({"schema_version": 1, "state_root": "/runtime/state"})
    configuration["research_policy"] = {}

    resolution = resolve_runtime_configuration(
        (ConfigurationSource("invalid-research", configuration),),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.INVALID_RESEARCH_POLICY,
        ("research_policy",),
    )


def test_runtime_configuration_refuses_an_invalid_balanced_portfolio_policy() -> None:
    configuration = _with_policy({"schema_version": 1, "state_root": "/runtime/state"})
    configuration["portfolio_policy"] = {}

    resolution = resolve_runtime_configuration(
        (ConfigurationSource("invalid-portfolio", configuration),),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.INVALID_PORTFOLIO_POLICY,
        ("portfolio_policy",),
    )


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
                    "enabled_asset_classes": ["us_equity"],
                    "universe_policy": universe_policy(),
                    hostile_field: sentinel,
                },
            ),
        ),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(ConfigurationRefusalCode.UNKNOWN_FIELD)
    assert sentinel not in repr(resolution)
    assert len(repr(resolution)) < max_bounded_refusal_length


def test_runtime_configuration_refuses_a_top_level_mapping_subclass() -> None:
    resolution = resolve_runtime_configuration(
        (
            ConfigurationSource(
                "hostile",
                _PolicyMap(
                    {
                        "schema_version": 1,
                        "state_root": "/runtime/state",
                        "enabled_asset_classes": ["us_equity"],
                        "universe_policy": universe_policy(),
                    }
                ),
            ),
        ),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(ConfigurationRefusalCode.UNKNOWN_FIELD)


@pytest.mark.parametrize(
    "sources",
    [
        (
            ConfigurationSource(
                "integer", {"schema_version": 1, "universe_policy": universe_policy()}
            ),
            ConfigurationSource(
                "boolean",
                _with_policy({"schema_version": True, "state_root": "/runtime/state"}),
            ),
        ),
        (
            ConfigurationSource(
                "boolean", {"schema_version": True, "universe_policy": universe_policy()}
            ),
            ConfigurationSource(
                "integer", _with_policy({"schema_version": 1, "state_root": "/runtime/state"})
            ),
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


@pytest.mark.parametrize(
    "conflicting_policy",
    [
        {**universe_policy(), "schema_version": 1.0},
        {**universe_policy(), "minimum_history_days": True},
        {**universe_policy(), "maximum_snapshot_age_seconds": 7200.0},
        {**universe_policy(), "approved_exchanges": ["ARCA", "NASDAQ"]},
        {key: value for key, value in universe_policy().items() if key != "etf_allowlist"},
        _policy_with_non_text_key(),
        _PolicyMap(universe_policy()),
        {
            **universe_policy(),
            "approved_exchanges": _PolicyList(["ARCA", "NASDAQ", "NYSE"]),
        },
    ],
    ids=[
        "nested_integer_float",
        "nested_integer_boolean",
        "nested_duration_float",
        "nested_list",
        "nested_keys",
        "nested_non_text_key",
        "nested_mapping_subclass",
        "nested_list_subclass",
    ],
)
@pytest.mark.parametrize("order", ["canonical_first", "conflict_first"])
def test_runtime_configuration_refuses_nested_policy_conflicts_in_either_source_order(
    conflicting_policy: object,
    order: str,
) -> None:
    canonical = ConfigurationSource(
        "canonical",
        {"schema_version": 1, "universe_policy": universe_policy()},
    )
    conflicting = ConfigurationSource(
        "conflicting",
        {"state_root": "/runtime/state", "universe_policy": conflicting_policy},
    )
    sources = (conflicting, canonical) if order == "conflict_first" else (canonical, conflicting)

    resolution = resolve_runtime_configuration(
        sources,
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.CONFLICTING_FIELD,
        ("universe_policy",),
    )


def test_runtime_configuration_refuses_recursive_duplicate_values() -> None:
    recursive: list[object] = []
    recursive.append(recursive)

    resolution = resolve_runtime_configuration(
        (
            ConfigurationSource("first", {"universe_policy": recursive}),
            ConfigurationSource(
                "second",
                {
                    "schema_version": 1,
                    "state_root": "/runtime/state",
                    "universe_policy": recursive,
                },
            ),
        ),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.CONFLICTING_FIELD,
        ("universe_policy",),
    )


def test_runtime_configuration_refuses_unsupported_values_without_invoking_equality() -> None:
    hostile_policy = {**universe_policy(), "minimum_price": _EqualityBomb()}

    resolution = resolve_runtime_configuration(
        (
            ConfigurationSource("first", {"universe_policy": hostile_policy}),
            ConfigurationSource(
                "second",
                {
                    "schema_version": 1,
                    "state_root": "/runtime/state",
                    "universe_policy": hostile_policy,
                },
            ),
        ),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.CONFLICTING_FIELD,
        ("universe_policy",),
    )


@pytest.mark.parametrize(
    "hostile_policy",
    [
        _PolicyMap(universe_policy()),
        {
            **universe_policy(),
            "approved_exchanges": _PolicyList(["ARCA", "NASDAQ", "NYSE"]),
        },
    ],
    ids=["mapping_subclass", "list_subclass"],
)
def test_runtime_configuration_refuses_duplicate_container_subclasses(
    hostile_policy: object,
) -> None:
    resolution = resolve_runtime_configuration(
        (
            ConfigurationSource("first", {"universe_policy": hostile_policy}),
            ConfigurationSource(
                "second",
                {
                    "schema_version": 1,
                    "state_root": "/runtime/state",
                    "universe_policy": hostile_policy,
                },
            ),
        ),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.CONFLICTING_FIELD,
        ("universe_policy",),
    )


def test_runtime_configuration_validates_duplicate_supported_invalid_scalars() -> None:
    invalid_policy = {**universe_policy(), "minimum_price": None}

    resolution = resolve_runtime_configuration(
        (
            ConfigurationSource("first", {"universe_policy": invalid_policy}),
            ConfigurationSource(
                "second",
                {
                    "schema_version": 1,
                    "state_root": "/runtime/state",
                    "enabled_asset_classes": ["us_equity"],
                    "universe_policy": invalid_policy,
                    "evidence_policy": evidence_policy(),
                    "attention_policy": attention_policy(),
                    "research_policy": research_policy(),
                    "portfolio_policy": portfolio_policy(),
                },
            ),
        ),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.INVALID_UNIVERSE_POLICY,
        ("universe_policy",),
    )


@pytest.mark.parametrize(
    "enabled_asset_classes",
    [[], ["crypto_spot"], ["listed_option"], ["us_equity", "crypto_spot"], ["us_equity"] * 2],
)
def test_runtime_configuration_requires_the_exact_v0_asset_activation_set(
    enabled_asset_classes: list[str],
) -> None:
    values = _with_policy({"schema_version": 1, "state_root": "/runtime/state"})
    values["enabled_asset_classes"] = enabled_asset_classes

    resolution = resolve_runtime_configuration(
        (ConfigurationSource("test", values),),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.INVALID_ENABLED_ASSET_CLASSES,
        ("enabled_asset_classes",),
    )


def test_runtime_configuration_refuses_hostile_asset_activation_without_comparing_it() -> None:
    values = _with_policy({"schema_version": 1, "state_root": "/runtime/state"})
    values["enabled_asset_classes"] = [_EqualityBomb()]

    resolution = resolve_runtime_configuration(
        (ConfigurationSource("test", values),),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.INVALID_ENABLED_ASSET_CLASSES,
        ("enabled_asset_classes",),
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
            _with_policy({"schema_version": "1", "state_root": str(tmp_path / "string-version")}),
            unsupported_version,
        ),
        (
            _with_policy({"schema_version": True, "state_root": str(tmp_path / "boolean-version")}),
            unsupported_version,
        ),
        (
            _with_policy(
                {
                    "schema_version": _IntegerSubclass(1),
                    "state_root": str(tmp_path / "integer-subclass-version"),
                }
            ),
            unsupported_version,
        ),
        (
            _with_policy({"schema_version": 1, "state_root": tmp_path / "path-object"}),
            invalid_state_root,
        ),
        (
            _with_policy(
                {
                    "schema_version": 1,
                    "state_root": _StringSubclass(str(tmp_path / "string-subclass-root")),
                }
            ),
            invalid_state_root,
        ),
        (
            _with_policy(
                {"schema_version": 1, "state_root": str(tmp_path / "safe" / ".." / "state")}
            ),
            invalid_state_root,
        ),
        (_with_policy({"schema_version": 1, "state_root": str(repository)}), invalid_state_root),
        (
            _with_policy({"schema_version": 1, "state_root": str(repository / "var")}),
            invalid_state_root,
        ),
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
                _with_policy({"schema_version": 1, "state_root": str(repository / "var")}),
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
                _with_policy({"schema_version": 1, "state_root": str(repository / "var")}),
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
                _with_policy({"schema_version": 1, "state_root": str(repository / "var")}),
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
                _with_policy({"schema_version": 1, "state_root": str(runtime_root)}),
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
            _with_policy({"schema_version": 1, "state_root": str(repository / "var")}),
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
            (
                ConfigurationSource(
                    "hostile", _with_policy({"schema_version": 1, "state_root": state_root})
                ),
            ),
            repository_root=repository,
        )
        assert resolution == ConfigurationRefusal(
            ConfigurationRefusalCode.INVALID_STATE_ROOT, ("state_root",)
        )


@pytest.mark.parametrize("evidence_value", [None, "different-regime-v1"])
def test_runtime_configuration_refuses_invalid_or_mismatched_evidence_policy(
    evidence_value: str | None,
) -> None:
    configuration = _with_policy({"schema_version": 1, "state_root": "/runtime/state"})
    if evidence_value is None:
        configuration["evidence_policy"] = None
    else:
        configuration["evidence_policy"] = {
            **evidence_policy(),
            "data_regime": evidence_value,
        }

    resolution = resolve_runtime_configuration(
        (ConfigurationSource("evidence", configuration),),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.INVALID_EVIDENCE_POLICY,
        ("evidence_policy",),
    )


def test_runtime_configuration_requires_at_least_one_fail_closed_evidence_retrieval() -> None:
    configuration = _with_policy({"schema_version": 1, "state_root": "/runtime/state"})
    policy = evidence_policy()
    requests = policy["requests"]
    assert isinstance(requests, list)
    for request in requests:
        assert isinstance(request, dict)
        request["required"] = False
    configuration["evidence_policy"] = policy

    resolution = resolve_runtime_configuration(
        (ConfigurationSource("evidence", configuration),),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.INVALID_EVIDENCE_POLICY,
        ("evidence_policy",),
    )


@pytest.mark.parametrize(
    "policy_update",
    [
        {"candidate_card_limit": 21},
        {"new_dossier_limit": 6},
        {"weekly_exploration_budget": 0},
        {"weekly_exploration_budget": 3},
    ],
)
def test_runtime_configuration_refuses_attention_policy_outside_bounded_contract(
    policy_update: dict[str, object],
) -> None:
    configuration = _with_policy({"schema_version": 1, "state_root": "/runtime/state"})
    configuration["attention_policy"] = {**attention_policy(), **policy_update}

    resolution = resolve_runtime_configuration(
        (ConfigurationSource("attention", configuration),),
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert resolution == ConfigurationRefusal(
        ConfigurationRefusalCode.INVALID_ATTENTION_POLICY,
        ("attention_policy",),
    )
