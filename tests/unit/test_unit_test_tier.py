from pathlib import Path

from scripts.check_unit_test_tier import UnitTierRule, check_unit_test_source

PROHIBITED_FIXTURE_SOURCE = """
from pathlib import Path


def test_builds_a_runtime_path(tmp_path: Path) -> None:
    assert (tmp_path / "state.json").suffix == ".json"
"""

PROHIBITED_EFFECT_SOURCE = """
import io
import http.server
import os
import sqlite3
import subprocess
import time
from datetime import datetime
from os import system
from pathlib import Path
from urllib.request import urlopen


def test_performs_effects() -> None:
    path = Path("state.json")
    path.write_text("{}", encoding="utf-8")
    Path("current").readlink()
    Path("current").owner()
    os.path.exists("state.json")
    os.access("state.json", os.R_OK)
    os.getcwd()
    os.chown("state.json", 1, 1)
    os.utime("state.json")
    os.path.getsize("state.json")
    io.open("state.json")
    time.sleep(0.01)
    datetime.now()
    system("git status")
    os.execv("/bin/echo", ["echo"])
    os.spawnv(os.P_WAIT, "/bin/echo", ["echo"])
"""

PURE_SOURCE = """
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class FixedClock:
    observed_at: datetime

    def now(self) -> datetime:
        return self.observed_at


class PurePath:
    def exists(self) -> bool:
        return True


class PureNamespace:
    path = PurePath()


def open(value: int) -> int:
    return value


def test_manipulates_values_without_effects() -> None:
    path = Path("reports") / "decision.json"
    clock = FixedClock(datetime(2026, 8, 22, 9, 30, tzinfo=UTC))
    label = "daily report".replace(" ", "-")

    assert path.parts == ("reports", "decision.json")
    assert clock.now().tzinfo is UTC
    assert label == "daily-report"


def test_local_values_shadow_effect_capable_names(os: PureNamespace) -> None:
    assert os.path.exists()
    assert open(1) == 1
"""

CLASS_METHOD_SCOPE_SOURCE = """
from pathlib import Path


class TestPathBehavior:
    Path = object

    def test_reads_the_global_path(self) -> None:
        assert Path("state.json").read_text()
"""

FUNCTION_LOCAL_SHADOW_SOURCE = """
import os


class PureOS:
    def system(self, command: str) -> int:
        return len(command)


def test_local_binding_is_lexical() -> None:
    os.system("unreachable")
    os = PureOS()


def test_comprehension_walrus_is_lexical() -> None:
    os.system("unreachable")
    assert [(os := PureOS()) for _ in (1,)]


def test_match_capture_is_lexical(value: object) -> None:
    os.system("unreachable")
    match value:
        case os:
            pass
"""

NESTED_CLASS_SCOPE_SOURCE = """
from pathlib import Path


class Outer:
    Path = object

    class Inner:
        def test_reads_the_global_path(self) -> None:
            assert Path("state.json").read_text()
"""

LAMBDA_INJECTED_CLOCK_SOURCE = """
import time

read_time = lambda time: time.time()
"""

COMPREHENSION_INJECTED_CLOCK_SOURCE = """
import time


class Clock:
    def time(self) -> float:
        return 42.0


values = [time.time() for time in (Clock(),)]
"""

CLASS_LAMBDA_SCOPE_SOURCE = """
from pathlib import Path


class Holder:
    Path = object
    read = lambda: Path("state.json").read_text()
"""

CLASS_COMPREHENSION_SCOPE_SOURCE = """
from pathlib import Path


class Holder:
    Path = object
    reads = [Path("state.json").read_text() for _ in (1,)]
"""

CLASS_INSIDE_METHOD_SOURCE = """
class Outer:
    def define_reader(self) -> None:
        from pathlib import Path

        class Inner:
            def test_reads_local_path(self) -> None:
                assert Path("state.json").read_text()
"""

WALRUS_COMPREHENSION_SOURCE = """
from os import path


class PurePath:
    def exists(self) -> bool:
        return True


values = [(path := PurePath()) for _ in (1,)]
assert path.exists()
"""

LAMBDA_WALRUS_SCOPE_SOURCE = """
import os


class PureOS:
    pass


funcs = [lambda: (os := PureOS()) for _ in (1,)]
os.system("echo")
"""


def test_unit_tier_check_reports_the_offending_test_and_fixture_rule() -> None:
    violations = check_unit_test_source(
        PROHIBITED_FIXTURE_SOURCE,
        path=Path("tests/unit/test_effect.py"),
    )

    assert len(violations) == 1
    assert violations[0].path == Path("tests/unit/test_effect.py")
    assert violations[0].test_name == "test_builds_a_runtime_path"
    assert violations[0].rule is UnitTierRule.FILESYSTEM_FIXTURE


def test_unit_tier_check_rejects_clear_effectful_imports_and_calls() -> None:
    violations = check_unit_test_source(
        PROHIBITED_EFFECT_SOURCE,
        path=Path("tests/unit/test_effect.py"),
    )

    assert [
        (violation.test_name, violation.rule, violation.subject) for violation in violations
    ] == [
        ("<module>", UnitTierRule.NETWORK_IMPORT, "http.server"),
        ("<module>", UnitTierRule.DATABASE_IMPORT, "sqlite3"),
        ("<module>", UnitTierRule.SUBPROCESS_IMPORT, "subprocess"),
        ("<module>", UnitTierRule.NETWORK_IMPORT, "urllib.request.urlopen"),
        ("test_performs_effects", UnitTierRule.FILESYSTEM_CALL, "path.write_text"),
        ("test_performs_effects", UnitTierRule.FILESYSTEM_CALL, "Path.readlink"),
        ("test_performs_effects", UnitTierRule.FILESYSTEM_CALL, "Path.owner"),
        ("test_performs_effects", UnitTierRule.FILESYSTEM_CALL, "os.path.exists"),
        ("test_performs_effects", UnitTierRule.FILESYSTEM_CALL, "os.access"),
        ("test_performs_effects", UnitTierRule.FILESYSTEM_CALL, "os.getcwd"),
        ("test_performs_effects", UnitTierRule.FILESYSTEM_CALL, "os.chown"),
        ("test_performs_effects", UnitTierRule.FILESYSTEM_CALL, "os.utime"),
        ("test_performs_effects", UnitTierRule.FILESYSTEM_CALL, "os.path.getsize"),
        ("test_performs_effects", UnitTierRule.FILESYSTEM_CALL, "io.open"),
        ("test_performs_effects", UnitTierRule.AMBIENT_CLOCK_CALL, "time.sleep"),
        ("test_performs_effects", UnitTierRule.AMBIENT_CLOCK_CALL, "datetime.now"),
        ("test_performs_effects", UnitTierRule.SUBPROCESS_CALL, "os.system"),
        ("test_performs_effects", UnitTierRule.SUBPROCESS_CALL, "os.execv"),
        ("test_performs_effects", UnitTierRule.SUBPROCESS_CALL, "os.spawnv"),
    ]

    class_scope_violations = check_unit_test_source(
        CLASS_METHOD_SCOPE_SOURCE,
        path=Path("tests/unit/test_class_method_scope.py"),
    )
    assert [
        (violation.test_name, violation.rule, violation.subject)
        for violation in class_scope_violations
    ] == [
        ("test_reads_the_global_path", UnitTierRule.FILESYSTEM_CALL, "Path.read_text"),
    ]
    class_lambda_violations = check_unit_test_source(
        CLASS_LAMBDA_SCOPE_SOURCE,
        path=Path("tests/unit/test_class_lambda_scope.py"),
    )
    assert [
        (violation.test_name, violation.rule, violation.subject)
        for violation in class_lambda_violations
    ] == [
        ("Holder", UnitTierRule.FILESYSTEM_CALL, "Path.read_text"),
    ]
    class_comprehension_violations = check_unit_test_source(
        CLASS_COMPREHENSION_SCOPE_SOURCE,
        path=Path("tests/unit/test_class_comprehension_scope.py"),
    )
    assert [
        (violation.test_name, violation.rule, violation.subject)
        for violation in class_comprehension_violations
    ] == [
        ("Holder", UnitTierRule.FILESYSTEM_CALL, "Path.read_text"),
    ]
    class_inside_method_violations = check_unit_test_source(
        CLASS_INSIDE_METHOD_SOURCE,
        path=Path("tests/unit/test_class_inside_method.py"),
    )
    assert [
        (violation.test_name, violation.rule, violation.subject)
        for violation in class_inside_method_violations
    ] == [
        ("test_reads_local_path", UnitTierRule.FILESYSTEM_CALL, "Path.read_text"),
    ]
    nested_class_violations = check_unit_test_source(
        NESTED_CLASS_SCOPE_SOURCE,
        path=Path("tests/unit/test_nested_class_scope.py"),
    )
    assert [
        (violation.test_name, violation.rule, violation.subject)
        for violation in nested_class_violations
    ] == [
        ("test_reads_the_global_path", UnitTierRule.FILESYSTEM_CALL, "Path.read_text"),
    ]
    lambda_walrus_violations = check_unit_test_source(
        LAMBDA_WALRUS_SCOPE_SOURCE,
        path=Path("tests/unit/test_lambda_walrus_scope.py"),
    )
    assert [
        (violation.test_name, violation.rule, violation.subject)
        for violation in lambda_walrus_violations
    ] == [
        ("<module>", UnitTierRule.SUBPROCESS_CALL, "os.system"),
    ]


def test_unit_tier_check_allows_pure_values_and_injected_clocks() -> None:
    assert check_unit_test_source(PURE_SOURCE, path=Path("tests/unit/test_pure_values.py")) == ()
    assert (
        check_unit_test_source(
            FUNCTION_LOCAL_SHADOW_SOURCE,
            path=Path("tests/unit/test_function_local_shadow.py"),
        )
        == ()
    )
    assert (
        check_unit_test_source(
            WALRUS_COMPREHENSION_SOURCE,
            path=Path("tests/unit/test_walrus_comprehension.py"),
        )
        == ()
    )
    assert (
        check_unit_test_source(
            COMPREHENSION_INJECTED_CLOCK_SOURCE,
            path=Path("tests/unit/test_comprehension_injected_clock.py"),
        )
        == ()
    )
    assert (
        check_unit_test_source(
            LAMBDA_INJECTED_CLOCK_SOURCE,
            path=Path("tests/unit/test_lambda_injected_clock.py"),
        )
        == ()
    )
