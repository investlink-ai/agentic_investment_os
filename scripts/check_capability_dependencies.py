"""Reject concrete effects from deterministic production capabilities."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from collections.abc import Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_RELATIVE = Path("src/agentic_investment_os")
EXEMPT_CAPABILITIES = frozenset({"adapters", "entrypoints"})
MAX_REPORTED_VIOLATIONS = 50

_PATH_CONSTRUCTORS = frozenset(
    {"pathlib.Path", "pathlib.PosixPath", "pathlib.PurePath", "pathlib.WindowsPath"}
)
_PATH_EFFECT_METHODS = frozenset(
    {
        "absolute",
        "chmod",
        "cwd",
        "exists",
        "expanduser",
        "glob",
        "group",
        "hardlink_to",
        "home",
        "is_block_device",
        "is_char_device",
        "is_dir",
        "is_fifo",
        "is_file",
        "is_junction",
        "is_mount",
        "is_socket",
        "is_symlink",
        "iterdir",
        "lchmod",
        "link_to",
        "lstat",
        "mkdir",
        "open",
        "owner",
        "read_bytes",
        "readlink",
        "read_text",
        "rename",
        "replace",
        "resolve",
        "rglob",
        "rmdir",
        "samefile",
        "stat",
        "symlink_to",
        "touch",
        "unlink",
        "walk",
        "write_bytes",
        "write_text",
    }
)
_PATH_RETURNING_METHODS = frozenset(
    {"absolute", "expanduser", "joinpath", "relative_to", "with_name", "with_stem", "with_suffix"}
)
_RANDOM_CONSTRUCTORS = frozenset({"random.Random"})
_DATETIME_CONSTRUCTORS = frozenset(
    {
        "datetime.datetime",
        "datetime.datetime.combine",
        "datetime.datetime.fromisoformat",
        "datetime.datetime.fromtimestamp",
        "datetime.datetime.strptime",
    }
)
_WILDCARD_EFFECTS = {
    "asyncio": (
        ("CAP005", "network access; import an explicit port-facing symbol"),
        ("CAP010", "process control; import an explicit port-facing symbol"),
    ),
    "builtins": (("CAP009", "filesystem access; import explicit value-only builtins"),),
    "concurrent.futures": (("CAP010", "process control; import an explicit symbol"),),
    "datetime": (
        ("CAP001", "ambient clock; import explicit value-only datetime symbols"),
        ("CAP004", "local-time state; import explicit timezone-aware symbols"),
    ),
    "io": (("CAP009", "filesystem access; import an explicit value-only symbol"),),
    "os": (
        ("CAP002", "ambient randomness; import an explicit value-only symbol"),
        ("CAP004", "environment access; import an explicit value-only symbol"),
        ("CAP009", "filesystem access; import an explicit value-only symbol"),
        ("CAP010", "process control; import an explicit value-only symbol"),
    ),
    "os.path": (("CAP009", "filesystem access; import an explicit value-only symbol"),),
    "pathlib": (("CAP009", "filesystem access; import an explicit value-only symbol"),),
    "random": (("CAP002", "ambient randomness; import Random explicitly and pass a seed"),),
    "time": (
        ("CAP001", "ambient clock; import an explicit value-only symbol"),
        ("CAP004", "local-time state; import an explicit timezone-aware symbol"),
    ),
    "uuid": (("CAP003", "nondeterministic identifiers; import an explicit value-only symbol"),),
}


class _ValueKind(StrEnum):
    DATETIME = "datetime"
    PATH = "path"
    RANDOM = "random"


@dataclass(frozen=True, slots=True)
class CapabilityViolation:
    """Locate one fixed capability-boundary diagnostic."""

    path: Path
    line: int
    column: int
    code: str
    subject: str
    problem: str


class _CapabilityVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self._path = path
        self._bindings: dict[str, str] = {"open": "builtins.open"}
        self._attribute_bindings: dict[tuple[str, ...], str] = {}
        self._kinds: dict[str, _ValueKind] = {}
        self._attribute_kinds: dict[tuple[str, ...], _ValueKind] = {}
        self.violations: list[CapabilityViolation] = []

    @override
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".", maxsplit=1)[0]
            self._bindings[bound] = alias.name if alias.asname else bound

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None or node.level != 0:
            return
        for alias in node.names:
            if alias.name == "*":
                for code, problem in _WILDCARD_EFFECTS.get(node.module, ()):
                    self._append(node, code, f"{node.module}.*", problem)
                continue
            self._bindings[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    @override
    def visit_Assign(self, node: ast.Assign) -> None:
        qualified_name = self._qualified_name(node.value)
        kind = self._expression_kind(node.value)
        self.visit(node.value)
        for target in node.targets:
            self._bind_target(target, qualified_name=qualified_name, kind=kind)

    @override
    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        qualified_name = self._qualified_name(node.value) if node.value is not None else None
        kind = self._annotation_kind(node.annotation)
        if kind is None and node.value is not None:
            kind = self._expression_kind(node.value)
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self._bind_target(node.target, qualified_name=qualified_name, kind=kind)

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        saved_bindings = self._bindings.copy()
        saved_attribute_bindings = self._attribute_bindings.copy()
        saved_kinds = self._kinds.copy()
        saved_attribute_kinds = self._attribute_kinds.copy()
        for statement in node.body:
            self.visit(statement)
        self._bindings = saved_bindings
        self._attribute_bindings = saved_attribute_bindings
        self._kinds = saved_kinds
        self._attribute_kinds = saved_attribute_kinds
        self._clear_name(node.name)

    @override
    def visit_Call(self, node: ast.Call) -> None:
        qualified_name = self._qualified_name(node.func)
        if qualified_name == "builtins.open":
            self._append(
                node, "CAP009", "builtins.open", "filesystem access; inject a filesystem port"
            )
        elif qualified_name in _RANDOM_CONSTRUCTORS and not _has_non_null_argument(
            node, position=0, keywords=frozenset({"x"})
        ):
            self._append(
                node,
                "CAP002",
                "random.Random",
                "unseeded random generator; pass an explicit seed",
            )
        elif qualified_name == "datetime.datetime.fromtimestamp" and not _has_non_null_argument(
            node, position=1, keywords=frozenset({"tz"})
        ):
            self._append(
                node,
                "CAP004",
                "datetime.datetime.fromtimestamp",
                "local-time conversion; pass an explicit timezone",
            )
        elif self._is_unseeded_random_method(node):
            self._append(
                node,
                "CAP002",
                "random.Random.seed",
                "random reseed from ambient state; pass an explicit seed",
            )
        elif self._is_timezone_free_astimezone(node, qualified_name):
            self._append(
                node,
                "CAP004",
                "datetime.datetime.astimezone",
                "local-time conversion; pass an explicit timezone",
            )
        elif self._is_path_effect(node, qualified_name):
            method = node.func.attr if isinstance(node.func, ast.Attribute) else "effect"
            self._append(
                node,
                "CAP009",
                f"pathlib.Path.{method}",
                "filesystem access; inject a filesystem port",
            )
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)

        saved_bindings = self._bindings.copy()
        saved_attribute_bindings = self._attribute_bindings.copy()
        saved_kinds = self._kinds.copy()
        saved_attribute_kinds = self._attribute_kinds.copy()
        for argument in _function_arguments(node.args):
            self._clear_name(argument.arg)
            if argument.annotation is not None:
                self.visit(argument.annotation)
                kind = self._annotation_kind(argument.annotation)
                if kind is not None:
                    self._kinds[argument.arg] = kind
        for statement in node.body:
            self.visit(statement)
        self._bindings = saved_bindings
        self._attribute_bindings = saved_attribute_bindings
        self._kinds = saved_kinds
        self._attribute_kinds = saved_attribute_kinds
        self._clear_name(node.name)

    def _bind_target(
        self,
        target: ast.expr,
        *,
        qualified_name: str | None,
        kind: _ValueKind | None,
    ) -> None:
        if isinstance(target, ast.Attribute):
            key = _attribute_key(target)
            if key is None:
                return
            self._attribute_bindings.pop(key, None)
            self._attribute_kinds.pop(key, None)
            if qualified_name is not None:
                self._attribute_bindings[key] = qualified_name
            if kind is not None:
                self._attribute_kinds[key] = kind
            return
        if not isinstance(target, ast.Name):
            self.visit(target)
            return
        self._clear_name(target.id)
        if qualified_name is not None:
            self._bindings[target.id] = qualified_name
        if kind is not None:
            self._kinds[target.id] = kind

    def _qualified_name(self, node: ast.AST | None) -> str | None:
        if isinstance(node, ast.Name):
            return self._bindings.get(node.id)
        if isinstance(node, ast.Attribute):
            key = _attribute_key(node)
            if key is not None and key in self._attribute_bindings:
                return self._attribute_bindings[key]
            parent = self._qualified_name(node.value)
            return f"{parent}.{node.attr}" if parent is not None else None
        return None

    def _annotation_kind(self, node: ast.AST) -> _ValueKind | None:
        qualified_name = self._qualified_name(node)
        if qualified_name in _PATH_CONSTRUCTORS:
            return _ValueKind.PATH
        if qualified_name == "datetime.datetime":
            return _ValueKind.DATETIME
        if qualified_name in _RANDOM_CONSTRUCTORS:
            return _ValueKind.RANDOM
        return None

    def _expression_kind(self, node: ast.AST) -> _ValueKind | None:
        if isinstance(node, ast.Name):
            return self._kinds.get(node.id)
        if isinstance(node, ast.Attribute):
            key = _attribute_key(node)
            return self._attribute_kinds.get(key) if key is not None else None
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and self._expression_kind(node.left) is _ValueKind.PATH
        ):
            return _ValueKind.PATH
        if not isinstance(node, ast.Call):
            return None
        qualified_name = self._qualified_name(node.func)
        kind: _ValueKind | None = None
        if qualified_name in _PATH_CONSTRUCTORS:
            kind = _ValueKind.PATH
        elif qualified_name in _RANDOM_CONSTRUCTORS:
            kind = _ValueKind.RANDOM
        elif qualified_name in _DATETIME_CONSTRUCTORS:
            kind = _ValueKind.DATETIME
        elif isinstance(node.func, ast.Attribute):
            receiver_kind = self._expression_kind(node.func.value)
            if receiver_kind is _ValueKind.PATH and node.func.attr in _PATH_RETURNING_METHODS:
                kind = _ValueKind.PATH
        return kind

    def _is_unseeded_random_method(self, node: ast.Call) -> bool:
        return (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "seed"
            and self._expression_kind(node.func.value) is _ValueKind.RANDOM
            and not _has_non_null_argument(node, position=0, keywords=frozenset({"a"}))
        )

    def _is_timezone_free_astimezone(self, node: ast.Call, qualified_name: str | None) -> bool:
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "astimezone":
            return False
        if qualified_name == "datetime.datetime.astimezone":
            position = 1
        elif self._expression_kind(node.func.value) is _ValueKind.DATETIME:
            position = 0
        else:
            return False
        return not _has_non_null_argument(node, position=position, keywords=frozenset({"tz"}))

    def _is_path_effect(self, node: ast.Call, qualified_name: str | None) -> bool:
        if not isinstance(node.func, ast.Attribute):
            return False
        if qualified_name is not None and any(
            qualified_name == f"{constructor}.{method}"
            for constructor in _PATH_CONSTRUCTORS
            for method in _PATH_EFFECT_METHODS
        ):
            return True
        return (
            node.func.attr in _PATH_EFFECT_METHODS
            and self._expression_kind(node.func.value) is _ValueKind.PATH
        )

    def _clear_name(self, name: str) -> None:
        self._bindings.pop(name, None)
        self._kinds.pop(name, None)
        self._attribute_bindings = {
            key: value for key, value in self._attribute_bindings.items() if key[0] != name
        }
        self._attribute_kinds = {
            key: value for key, value in self._attribute_kinds.items() if key[0] != name
        }

    def _append(self, node: ast.stmt | ast.expr, code: str, subject: str, problem: str) -> None:
        self.violations.append(
            CapabilityViolation(
                path=self._path,
                line=node.lineno,
                column=node.col_offset + 1,
                code=code,
                subject=subject,
                problem=problem,
            )
        )


def protected_sources(root: Path) -> tuple[Path, ...]:
    """Return production sources whose capabilities cannot own concrete effects."""
    package_root = root / PACKAGE_RELATIVE
    if not package_root.is_dir():
        return ()
    return tuple(
        source
        for source in sorted(package_root.rglob("*.py"))
        if source.relative_to(package_root).parts[0] not in EXEMPT_CAPABILITIES
    )


def inspect_capability_dependencies(
    root: Path, sources: Sequence[Path]
) -> tuple[CapabilityViolation, ...]:
    """Inspect context-sensitive deterministic seams that Ruff cannot express."""
    violations: list[CapabilityViolation] = []
    for source in sources:
        relative_source = source.relative_to(root)
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(relative_source))
        except (OSError, SyntaxError, UnicodeError) as error:
            line = (
                error.lineno if isinstance(error, SyntaxError) and error.lineno is not None else 1
            )
            violations.append(
                CapabilityViolation(
                    path=relative_source,
                    line=line,
                    column=1,
                    code="CAP000",
                    subject="source",
                    problem="invalid Python source; repair it before capability analysis",
                )
            )
            continue
        visitor = _CapabilityVisitor(relative_source)
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return tuple(
        sorted(violations, key=lambda item: (item.path, item.line, item.column, item.code))
    )


def _run_ruff(root: Path, sources: Sequence[Path]) -> subprocess.CompletedProcess[str]:
    relative_sources = tuple(str(source.relative_to(root)) for source in sources)
    return subprocess.run(  # noqa: S603 - executable and module are repository-controlled.
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--ignore-noqa",
            "--select",
            "TID251",
            "--output-format",
            "concise",
            "--config",
            str(REPOSITORY_ROOT / "pyproject.toml"),
            *relative_sources,
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def _has_non_null_argument(node: ast.Call, *, position: int, keywords: frozenset[str]) -> bool:
    if len(node.args) > position and not _is_none(node.args[position]):
        return True
    return any(keyword.arg in keywords and not _is_none(keyword.value) for keyword in node.keywords)


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _attribute_key(node: ast.Attribute) -> tuple[str, ...] | None:
    parts = [node.attr]
    value = node.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if not isinstance(value, ast.Name):
        return None
    parts.append(value.id)
    return tuple(reversed(parts))


def _function_arguments(arguments: ast.arguments) -> tuple[ast.arg, ...]:
    positional = (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    variadic = tuple(
        argument for argument in (arguments.vararg, arguments.kwarg) if argument is not None
    )
    return (*positional, *variadic)


def _format_violations(violations: Sequence[CapabilityViolation]) -> str:
    reported = violations[:MAX_REPORTED_VIOLATIONS]
    lines = [
        f"{violation.path}:{violation.line}:{violation.column}: {violation.code} "
        f"{violation.subject}: {violation.problem} "
        "(docs/architecture.md#capability-effect-boundaries)"
        for violation in reported
    ]
    remaining = len(violations) - len(reported)
    if remaining:
        lines.append(f"... {remaining} additional violation(s) omitted")
    return "\n".join(lines)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reject concrete effects from deterministic production capabilities."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.root.resolve()
    sources = protected_sources(root)
    if not (root / PACKAGE_RELATIVE).is_dir():
        sys.stdout.write(
            f"{PACKAGE_RELATIVE}:1:1: CAP000 protected package is missing "
            "(docs/architecture.md#capability-effect-boundaries)\n"
        )
        return 1

    contextual_violations = inspect_capability_dependencies(root, sources)
    if any(violation.code == "CAP000" for violation in contextual_violations):
        sys.stdout.write(f"{_format_violations(contextual_violations)}\n")
        return 1

    ruff_result = _run_ruff(root, sources)
    if ruff_result.returncode not in (0, 1) or (
        ruff_result.returncode == 1 and not ruff_result.stdout.strip()
    ):
        sys.stdout.write(
            "pyproject.toml:1:1: CAP000 capability lint unavailable; run `uv sync` and retry "
            "(docs/architecture.md#capability-effect-boundaries)\n"
        )
        return 1
    if ruff_result.stdout:
        sys.stdout.write(f"{ruff_result.stdout.rstrip()}\n")
    if contextual_violations:
        sys.stdout.write(f"{_format_violations(contextual_violations)}\n")
    if ruff_result.returncode == 1 or contextual_violations:
        return 1

    noun = "file" if len(sources) == 1 else "files"
    sys.stdout.write(
        f"capability dependency check passed ({len(sources)} protected source {noun})\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
