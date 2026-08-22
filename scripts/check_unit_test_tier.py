"""Reject clear effectful behavior from the pure unit-test tier."""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from collections.abc import Sequence


class UnitTierRule(StrEnum):
    """Identify one mechanically enforceable unit-tier boundary."""

    AMBIENT_CLOCK_CALL = "ambient-clock-call"
    DATABASE_IMPORT = "database-import"
    FILESYSTEM_CALL = "filesystem-call"
    FILESYSTEM_FIXTURE = "filesystem-fixture"
    FILESYSTEM_IMPORT = "filesystem-import"
    NETWORK_IMPORT = "network-import"
    SUBPROCESS_CALL = "subprocess-call"
    SUBPROCESS_IMPORT = "subprocess-import"


@dataclass(frozen=True, slots=True)
class UnitTierViolation:
    """Locate one unit-tier violation for a repository diagnostic."""

    path: Path
    line: int
    test_name: str
    rule: UnitTierRule
    subject: str


_FILESYSTEM_FIXTURES = frozenset({"tmp_path", "tmp_path_factory", "tmpdir", "tmpdir_factory"})
_FILESYSTEM_IMPORTS = frozenset({"fileinput", "glob", "mmap", "shutil", "tempfile"})
_SUBPROCESS_IMPORTS = frozenset({"subprocess"})
_DATABASE_IMPORTS = frozenset(
    {"asyncpg", "duckdb", "mysql", "psycopg", "pymysql", "sqlalchemy", "sqlite3"}
)
_NETWORK_IMPORTS = frozenset(
    {
        "aiohttp",
        "ftplib",
        "http.client",
        "http.server",
        "httpx",
        "requests",
        "smtplib",
        "socket",
        "urllib.request",
        "urllib3",
        "websockets",
        "xmlrpc.client",
        "xmlrpc.server",
    }
)
_FILESYSTEM_CALLS = frozenset(
    {
        "absolute",
        "chmod",
        "cwd",
        "expanduser",
        "exists",
        "glob",
        "group",
        "hardlink_to",
        "home",
        "is_dir",
        "is_block_device",
        "is_char_device",
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
_FILESYSTEM_FUNCTION_CALLS = frozenset(
    {
        "builtins.open",
        "io.open",
        "os.access",
        "os.chdir",
        "os.chflags",
        "os.chmod",
        "os.chown",
        "os.chroot",
        "os.close",
        "os.closerange",
        "os.dup",
        "os.dup2",
        "os.fchdir",
        "os.fchmod",
        "os.fchown",
        "os.fdatasync",
        "os.fdopen",
        "os.fpathconf",
        "os.fstat",
        "os.fstatvfs",
        "os.fsync",
        "os.ftruncate",
        "os.fwalk",
        "os.getcwd",
        "os.getcwdb",
        "os.getxattr",
        "os.lchmod",
        "os.lchflags",
        "os.lchown",
        "os.link",
        "os.listdir",
        "os.listxattr",
        "os.lockf",
        "os.lseek",
        "os.lstat",
        "os.makedirs",
        "os.mkdir",
        "os.mkfifo",
        "os.mknod",
        "os.open",
        "os.openpty",
        "os.pathconf",
        "os.pipe",
        "os.pread",
        "os.preadv",
        "os.pwrite",
        "os.pwritev",
        "os.read",
        "os.readlink",
        "os.readv",
        "os.remove",
        "os.removedirs",
        "os.removexattr",
        "os.rename",
        "os.renames",
        "os.replace",
        "os.rmdir",
        "os.scandir",
        "os.sendfile",
        "os.stat",
        "os.statvfs",
        "os.setxattr",
        "os.symlink",
        "os.sync",
        "os.truncate",
        "os.unlink",
        "os.utime",
        "os.walk",
        "os.write",
        "os.writev",
        "os.path.exists",
        "os.path.getatime",
        "os.path.getctime",
        "os.path.getmtime",
        "os.path.getsize",
        "os.path.isdir",
        "os.path.isfile",
        "os.path.isjunction",
        "os.path.islink",
        "os.path.ismount",
        "os.path.lexists",
        "os.path.realpath",
        "os.path.samefile",
        "os.path.sameopenfile",
    }
)
_PATH_CONSTRUCTORS = frozenset({"pathlib.Path", "pathlib.PosixPath", "pathlib.WindowsPath"})
_PATH_RETURNING_METHODS = frozenset(
    {"absolute", "expanduser", "joinpath", "relative_to", "resolve", "with_name", "with_suffix"}
)
_AMBIENT_CLOCK_CALLS = frozenset(
    {
        "datetime.date.today",
        "datetime.datetime.now",
        "datetime.datetime.today",
        "datetime.datetime.utcnow",
        "time.monotonic",
        "time.monotonic_ns",
        "time.perf_counter",
        "time.perf_counter_ns",
        "time.process_time",
        "time.process_time_ns",
        "time.sleep",
        "time.time",
        "time.time_ns",
    }
)
_SUBPROCESS_CALLS = frozenset(
    {
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "os.abort",
        "os.execl",
        "os.execle",
        "os.execlp",
        "os.execlpe",
        "os.execv",
        "os.execve",
        "os.execvp",
        "os.execvpe",
        "os.fork",
        "os.forkpty",
        "os.kill",
        "os.killpg",
        "os.login_tty",
        "os.popen",
        "os.posix_spawn",
        "os.posix_spawnp",
        "os.spawnl",
        "os.spawnle",
        "os.spawnlp",
        "os.spawnlpe",
        "os.spawnv",
        "os.spawnve",
        "os.spawnvp",
        "os.spawnvpe",
        "os.system",
        "os.wait",
        "os.wait3",
        "os.wait4",
        "os.waitpid",
    }
)


class _FunctionBindingVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    @override
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    @override
    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    @override
    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocal_names.update(node.names)

    @override
    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(
            alias.asname or alias.name.split(".", maxsplit=1)[0] for alias in node.names
        )

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(alias.asname or alias.name for alias in node.names)

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    @override
    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    @override
    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    @override
    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    @override
    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))

    @override
    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    @override
    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name is not None:
            self.names.add(node.name)
        self.generic_visit(node)

    @override
    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name is not None:
            self.names.add(node.name)

    @override
    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest is not None:
            self.names.add(node.rest)
        self.generic_visit(node)

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        results: tuple[ast.expr, ...],
    ) -> None:
        for generator in generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        for result in results:
            self.visit(result)

    @override
    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None:
            self.names.add(node.name)
        self.generic_visit(node)


class _UnitTestVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self._path = path
        self._scope = "<module>"
        self._imports: dict[str, str] = {"open": "builtins.open"}
        self._path_names: set[str] = set()
        self._class_lexical_bindings: list[tuple[dict[str, str], set[str]]] = []
        self._comprehension_bindings: list[dict[str, bool]] = []
        self.violations: list[UnitTierViolation] = []

    @override
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
            self._imports[bound_name] = alias.name if alias.asname else bound_name
            self._check_import(alias.name, node.lineno)

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None:
            return
        for alias in node.names:
            qualified_name = f"{node.module}.{alias.name}"
            self._imports[alias.asname or alias.name] = qualified_name
            self._check_import(qualified_name, node.lineno)

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        previous_imports = self._imports.copy()
        previous_path_names = self._path_names.copy()
        previous_scope = self._scope
        self._scope = node.name
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        lexical_bindings = (
            self._class_lexical_bindings[-1]
            if self._class_lexical_bindings
            else (previous_imports, previous_path_names)
        )
        self._class_lexical_bindings.append(lexical_bindings)
        for statement in node.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._visit_function(
                    statement,
                    body_imports=lexical_bindings[0],
                    body_path_names=lexical_bindings[1],
                )
            else:
                self.visit(statement)
        self._class_lexical_bindings.pop()
        self._scope = previous_scope
        self._imports = previous_imports
        self._path_names = previous_path_names
        self._imports.pop(node.name, None)
        self._path_names.discard(node.name)

    @override
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self._imports.pop(node.id, None)
            self._path_names.discard(node.id)

    @override
    def visit_Lambda(self, node: ast.Lambda) -> None:
        definition_imports = self._imports.copy()
        definition_path_names = self._path_names.copy()
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        if self._class_lexical_bindings:
            self._imports = self._class_lexical_bindings[-1][0].copy()
            self._path_names = self._class_lexical_bindings[-1][1].copy()
        for argument in _function_arguments(node.args):
            self._imports.pop(argument.arg, None)
            self._path_names.discard(argument.arg)
        binding_visitor = _FunctionBindingVisitor()
        binding_visitor.visit(node.body)
        for name in binding_visitor.names:
            self._imports.pop(name, None)
            self._path_names.discard(name)
        enclosing_comprehension_bindings = self._comprehension_bindings
        self._comprehension_bindings = []
        self.visit(node.body)
        self._comprehension_bindings = enclosing_comprehension_bindings
        self._imports = definition_imports
        self._path_names = definition_path_names

    @override
    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    @override
    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    @override
    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))

    @override
    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    @override
    def visit_Assign(self, node: ast.Assign) -> None:
        is_path = self._is_path_expression(node.value)
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)
        self._record_path_names(node.targets, is_path=is_path)

    @override
    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        targets = (node.target,)
        is_path = self._is_path_annotation(node.annotation) or (
            node.value is not None and self._is_path_expression(node.value)
        )
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self.visit(node.target)
        self._record_path_names(targets, is_path=is_path)

    @override
    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        is_path = self._is_path_expression(node.value)
        self.visit(node.value)
        for name in _binding_names(node.target):
            self._imports.pop(name, None)
            self._path_names.discard(name)
            if is_path:
                self._path_names.add(name)
            if self._comprehension_bindings:
                self._comprehension_bindings[-1][name] = is_path

    @override
    def visit_Call(self, node: ast.Call) -> None:
        qualified_name = self._qualified_name(node.func)
        if qualified_name in _SUBPROCESS_CALLS and self._is_import_bound(node.func):
            self._append_call_violation(
                node,
                UnitTierRule.SUBPROCESS_CALL,
                qualified_name,
            )
        elif self._is_filesystem_call(node, qualified_name):
            self._append_call_violation(
                node,
                UnitTierRule.FILESYSTEM_CALL,
                self._display_name(qualified_name),
            )
        elif qualified_name in _AMBIENT_CLOCK_CALLS and self._is_import_bound(node.func):
            self._append_call_violation(
                node,
                UnitTierRule.AMBIENT_CLOCK_CALL,
                self._display_name(qualified_name),
            )
        self.generic_visit(node)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        body_imports: dict[str, str] | None = None,
        body_path_names: set[str] | None = None,
    ) -> None:
        definition_imports = self._imports.copy()
        definition_path_names = self._path_names.copy()
        arguments = _function_arguments(node.args)
        previous_scope = self._scope
        self._scope = node.name
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        for argument in arguments:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        self._imports = (
            body_imports.copy() if body_imports is not None else definition_imports.copy()
        )
        self._path_names = (
            body_path_names.copy() if body_path_names is not None else definition_path_names.copy()
        )
        bindings = _function_bindings(node)
        bindings.update(argument.arg for argument in arguments)
        bindings.add(node.name)
        for name in bindings:
            self._imports.pop(name, None)
            self._path_names.discard(name)
        for argument in arguments:
            if argument.annotation is not None and self._is_path_annotation(argument.annotation):
                self._path_names.add(argument.arg)
        if node.name.startswith("test_"):
            fixture_names = {argument.arg for argument in arguments}
            prohibited_fixtures = sorted(fixture_names & _FILESYSTEM_FIXTURES)
            for fixture_name in prohibited_fixtures:
                self.violations.append(
                    UnitTierViolation(
                        self._path,
                        node.lineno,
                        node.name,
                        UnitTierRule.FILESYSTEM_FIXTURE,
                        fixture_name,
                    )
                )
        previous_class_lexical_bindings = self._class_lexical_bindings
        self._class_lexical_bindings = []
        for statement in node.body:
            self.visit(statement)
        self._class_lexical_bindings = previous_class_lexical_bindings
        self._scope = previous_scope
        self._imports = definition_imports
        self._path_names = definition_path_names
        self._imports.pop(node.name, None)
        self._path_names.discard(node.name)

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        results: tuple[ast.expr, ...],
    ) -> None:
        previous_imports = self._imports.copy()
        previous_path_names = self._path_names.copy()
        escaped_bindings: dict[str, bool] = {}
        self._comprehension_bindings.append(escaped_bindings)
        first_generator, *remaining_generators = generators
        self.visit(first_generator.iter)
        if self._class_lexical_bindings:
            self._imports = self._class_lexical_bindings[-1][0].copy()
            self._path_names = self._class_lexical_bindings[-1][1].copy()
        for name in _binding_names(first_generator.target):
            self._imports.pop(name, None)
            self._path_names.discard(name)
        for condition in first_generator.ifs:
            self.visit(condition)
        for generator in remaining_generators:
            self.visit(generator.iter)
            for name in _binding_names(generator.target):
                self._imports.pop(name, None)
                self._path_names.discard(name)
            for condition in generator.ifs:
                self.visit(condition)
        for result in results:
            self.visit(result)
        self._comprehension_bindings.pop()
        self._imports = previous_imports
        self._path_names = previous_path_names
        for name, is_path in escaped_bindings.items():
            self._imports.pop(name, None)
            self._path_names.discard(name)
            if is_path:
                self._path_names.add(name)
        if self._comprehension_bindings:
            self._comprehension_bindings[-1].update(escaped_bindings)

    def _record_path_names(self, targets: Sequence[ast.expr], *, is_path: bool) -> None:
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if is_path:
                self._path_names.add(target.id)
            else:
                self._path_names.discard(target.id)

    def _is_path_annotation(self, node: ast.expr) -> bool:
        return self._qualified_name(node) in _PATH_CONSTRUCTORS

    def _is_path_expression(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            is_path = node.id in self._path_names
        elif isinstance(node, ast.Call):
            qualified_name = self._qualified_name(node.func)
            if qualified_name in _PATH_CONSTRUCTORS:
                is_path = True
            elif isinstance(node.func, ast.Attribute):
                is_path = node.func.attr in _PATH_RETURNING_METHODS and self._is_path_expression(
                    node.func.value
                )
            else:
                is_path = False
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            is_path = self._is_path_expression(node.left)
        elif isinstance(node, ast.Attribute) and node.attr == "parent":
            is_path = self._is_path_expression(node.value)
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
            is_path = node.value.attr == "parents" and self._is_path_expression(node.value.value)
        else:
            is_path = False
        return is_path

    def _is_filesystem_call(self, node: ast.Call, qualified_name: str) -> bool:
        if qualified_name in _FILESYSTEM_FUNCTION_CALLS:
            return self._is_import_bound(node.func)
        if not isinstance(node.func, ast.Attribute) or node.func.attr not in _FILESYSTEM_CALLS:
            return False
        return (
            qualified_name.startswith("pathlib.Path.") and self._is_import_bound(node.func)
        ) or self._is_path_expression(node.func.value)

    def _is_import_bound(self, node: ast.expr) -> bool:
        while isinstance(node, (ast.Attribute, ast.Call)):
            node = node.value if isinstance(node, ast.Attribute) else node.func
        return isinstance(node, ast.Name) and node.id in self._imports

    def _check_import(self, qualified_name: str, line: int) -> None:
        rule = _import_rule(qualified_name)
        if rule is None:
            return
        self.violations.append(
            UnitTierViolation(self._path, line, self._scope, rule, qualified_name)
        )

    def _append_call_violation(
        self,
        node: ast.Call,
        rule: UnitTierRule,
        subject: str,
    ) -> None:
        self.violations.append(
            UnitTierViolation(self._path, node.lineno, self._scope, rule, subject)
        )

    def _qualified_name(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return self._imports.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            prefix = self._qualified_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        if isinstance(node, ast.Call):
            return self._qualified_name(node.func)
        return ""

    @staticmethod
    def _display_name(qualified_name: str) -> str:
        for prefix in ("pathlib.", "datetime.", "builtins."):
            if qualified_name.startswith(prefix):
                return qualified_name.removeprefix(prefix)
        return qualified_name


def _function_bindings(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    visitor = _FunctionBindingVisitor()
    for statement in node.body:
        visitor.visit(statement)
    return visitor.names - visitor.global_names - visitor.nonlocal_names


def _binding_names(target: ast.expr) -> tuple[str, ...]:
    if isinstance(target, ast.Name):
        return (target.id,)
    if isinstance(target, ast.Starred):
        return _binding_names(target.value)
    if isinstance(target, (ast.List, ast.Tuple)):
        return tuple(name for element in target.elts for name in _binding_names(element))
    return ()


def _function_arguments(arguments: ast.arguments) -> tuple[ast.arg, ...]:
    positional_and_keyword = (
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
    )
    variadic = tuple(
        argument for argument in (arguments.vararg, arguments.kwarg) if argument is not None
    )
    return (*positional_and_keyword, *variadic)


def _import_rule(qualified_name: str) -> UnitTierRule | None:
    import_groups = (
        (_FILESYSTEM_IMPORTS, UnitTierRule.FILESYSTEM_IMPORT),
        (_SUBPROCESS_IMPORTS, UnitTierRule.SUBPROCESS_IMPORT),
        (_DATABASE_IMPORTS, UnitTierRule.DATABASE_IMPORT),
        (_NETWORK_IMPORTS, UnitTierRule.NETWORK_IMPORT),
    )
    for prefixes, rule in import_groups:
        if any(
            qualified_name == prefix or qualified_name.startswith(f"{prefix}.")
            for prefix in prefixes
        ):
            return rule
    return None


def check_unit_test_source(source: str, *, path: Path) -> tuple[UnitTierViolation, ...]:
    """Return clear unit-tier violations without executing or reading the test source."""
    tree = ast.parse(source, filename=str(path))
    visitor = _UnitTestVisitor(path)
    visitor.visit(tree)
    return tuple(visitor.violations)


def check_unit_test_repository(root: Path) -> tuple[UnitTierViolation, ...]:
    """Inspect every unit-test module below one repository root in stable path order."""
    unit_root = root / "tests" / "unit"
    violations: list[UnitTierViolation] = []
    for source_path in sorted(unit_root.rglob("*.py")):
        relative_path = source_path.relative_to(root)
        violations.extend(
            check_unit_test_source(
                source_path.read_text(encoding="utf-8"),
                path=relative_path,
            )
        )
    return tuple(violations)


def main(argv: Sequence[str] | None = None) -> int:
    """Report unit-tier violations and return a nonzero status when any exist."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    violations = check_unit_test_repository(arguments.root)
    for violation in violations:
        sys.stderr.write(
            f"{violation.path}:{violation.line}: {violation.test_name} "
            f"[{violation.rule}]: {violation.subject}\n"
        )
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
