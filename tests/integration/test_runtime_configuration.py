from __future__ import annotations

import shutil
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Never

import pytest

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
