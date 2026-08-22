from __future__ import annotations

import ast
from dataclasses import dataclass
from importlib.util import resolve_name
from pathlib import Path

PACKAGE_NAME = "agentic_investment_os"
PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / PACKAGE_NAME
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "module_graph"
MAX_REPORTED_VIOLATIONS = 20
MUTATING_ALL_METHODS = frozenset(
    {
        "__delitem__",
        "__iadd__",
        "__imul__",
        "__setitem__",
        "append",
        "clear",
        "extend",
        "insert",
        "pop",
        "remove",
        "reverse",
        "sort",
    }
)
ALLOWED_EDGES = {
    "entrypoints": {"application", "execution", "adapters"},
    "application": {"evidence", "memory", "research", "portfolio", "evaluation", "domain"},
    "evidence": {"domain"},
    "memory": {"domain"},
    "research": {"domain"},
    "portfolio": {"domain"},
    "execution": {"domain"},
    "evaluation": {"domain"},
    "adapters": {"domain", "evidence", "memory", "research", "execution"},
    "domain": set(),
}


@dataclass(frozen=True, slots=True)
class ImportReference:
    module: str
    symbols: tuple[str, ...] | None
    line: int


@dataclass(frozen=True, slots=True)
class ImportViolation:
    source: Path
    line: int
    problem: str


def test_production_imports_follow_the_module_graph() -> None:
    violations = _production_import_violations(PACKAGE_ROOT)

    assert not violations, _format_violations(violations)


def test_declared_cross_module_imports_preserve_internal_import_freedom() -> None:
    fixture = FIXTURE_ROOT / "declared"

    assert not _production_import_violations(fixture / "src" / PACKAGE_NAME)


def test_undeclared_cross_module_import_is_rejected() -> None:
    fixture = FIXTURE_ROOT / "undeclared"

    violations = _production_import_violations(fixture / "src" / PACKAGE_NAME)

    assert len(violations) == 1
    assert "UndeclaredContract" in violations[0].problem
    assert "__all__" in violations[0].problem


def test_reassigned_interface_declaration_is_rejected() -> None:
    fixture = FIXTURE_ROOT / "reassigned"

    violations = _production_import_violations(fixture / "src" / PACKAGE_NAME)

    assert len(violations) == 1
    assert "static __all__ declaration" in violations[0].problem


def test_private_cross_module_import_is_rejected_even_when_declared() -> None:
    fixture = FIXTURE_ROOT / "private"

    violations = _production_import_violations(fixture / "src" / PACKAGE_NAME)

    assert len(violations) == 1
    assert "_PrivateContract" in violations[0].problem
    assert "private" in violations[0].problem


def test_module_style_cross_module_import_is_rejected() -> None:
    fixture = FIXTURE_ROOT / "module_style"

    violations = _production_import_violations(fixture / "src" / PACKAGE_NAME)

    assert len(violations) == 1
    assert "must name declared symbols" in violations[0].problem


def test_disallowed_capability_direction_is_rejected() -> None:
    fixture = FIXTURE_ROOT / "disallowed_direction"

    violations = _production_import_violations(fixture / "src" / PACKAGE_NAME)

    assert len(violations) == 1
    assert "domain -> application" in violations[0].problem


def test_violation_diagnostics_are_bounded_and_actionable() -> None:
    violations = tuple(
        ImportViolation(source=Path(f"module_{index}.py"), line=index + 1, problem="problem")
        for index in range(MAX_REPORTED_VIOLATIONS + 1)
    )

    diagnostic = _format_violations(violations)

    assert "module_0.py:1: problem" in diagnostic
    assert f"module_{MAX_REPORTED_VIOLATIONS}.py" not in diagnostic
    assert "1 additional violation(s) omitted" in diagnostic


def _production_import_violations(package_root: Path) -> tuple[ImportViolation, ...]:
    declarations: dict[str, frozenset[str] | None] = {}
    violations: list[ImportViolation] = []
    for source_file in _python_sources(package_root):
        relative_source = source_file.relative_to(package_root)
        owner = relative_source.parts[0]
        if owner.endswith(".py"):
            continue
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        current_package = _current_package(relative_source)
        for node in ast.walk(tree):
            for imported in _import_references(node, current_package):
                violations.extend(
                    _cross_module_violations(
                        package_root=package_root,
                        source=relative_source,
                        owner=owner,
                        imported=imported,
                        declarations=declarations,
                    )
                )
    return tuple(violations)


def _cross_module_violations(
    *,
    package_root: Path,
    source: Path,
    owner: str,
    imported: ImportReference,
    declarations: dict[str, frozenset[str] | None],
) -> tuple[ImportViolation, ...]:
    target = _capability(imported.module)
    if target is None or target == owner:
        return ()
    if target not in ALLOWED_EDGES.get(owner, set()):
        return (
            _violation(source, imported, f"disallowed production import edge: {owner} -> {target}"),
        )
    if imported.symbols is None:
        return (
            _violation(
                source,
                imported,
                f"cross-module import of {imported.module!r} must name declared symbols with "
                "'from ... import ...'",
            ),
        )
    return _symbol_violations(
        package_root=package_root,
        source=source,
        imported=imported,
        symbols=imported.symbols,
        declarations=declarations,
    )


def _symbol_violations(
    *,
    package_root: Path,
    source: Path,
    imported: ImportReference,
    symbols: tuple[str, ...],
    declarations: dict[str, frozenset[str] | None],
) -> tuple[ImportViolation, ...]:
    if "*" in symbols:
        return (
            _violation(
                source,
                imported,
                f"cross-module wildcard import from {imported.module!r} cannot identify the "
                "consumed interface",
            ),
        )
    module_objects = tuple(
        sorted(
            symbol
            for symbol in symbols
            if _module_source(package_root, f"{imported.module}.{symbol}") is not None
        )
    )
    violations: list[ImportViolation] = []
    if module_objects:
        violations.append(
            _violation(
                source,
                imported,
                f"cross-module import from {imported.module!r} uses module objects "
                f"{module_objects!r}; it must name declared symbols from their defining modules",
            )
        )
    private = tuple(
        sorted(
            symbol for symbol in symbols if symbol.startswith("_") and symbol not in module_objects
        )
    )
    if private:
        violations.append(
            _violation(
                source,
                imported,
                f"cross-module import from {imported.module!r} uses private symbols: {private!r}",
            )
        )
    public = frozenset(
        symbol for symbol in symbols if not symbol.startswith("_") and symbol not in module_objects
    )
    if not public:
        return tuple(violations)
    if imported.module not in declarations:
        declarations[imported.module] = _declared_symbols(package_root, imported.module)
    declared = declarations[imported.module]
    if declared is None:
        violations.append(
            _violation(
                source,
                imported,
                f"cross-module owner {imported.module!r} must define a static __all__ declaration",
            )
        )
        return tuple(violations)
    undeclared = tuple(sorted(public - declared))
    if undeclared:
        violations.append(
            _violation(
                source,
                imported,
                f"cross-module import from {imported.module!r} uses symbols absent from __all__: "
                f"{undeclared!r}",
            )
        )
    return tuple(violations)


def _violation(source: Path, imported: ImportReference, problem: str) -> ImportViolation:
    return ImportViolation(source=source, line=imported.line, problem=problem)


def _import_references(node: ast.AST, current_package: str) -> tuple[ImportReference, ...]:
    if isinstance(node, ast.Import):
        return tuple(
            ImportReference(module=alias.name, symbols=None, line=node.lineno)
            for alias in node.names
        )
    if not isinstance(node, ast.ImportFrom):
        return ()
    module = _resolved_module(node, current_package)
    if module is None:
        return ()
    if module == PACKAGE_NAME:
        return tuple(
            ImportReference(module=f"{module}.{alias.name}", symbols=None, line=node.lineno)
            for alias in node.names
        )
    return (
        ImportReference(
            module=module,
            symbols=tuple(alias.name for alias in node.names),
            line=node.lineno,
        ),
    )


def _resolved_module(node: ast.ImportFrom, current_package: str) -> str | None:
    if node.level == 0:
        return node.module
    relative_module = f"{'.' * node.level}{node.module or ''}"
    try:
        return resolve_name(relative_module, current_package)
    except ImportError:
        return None


def _current_package(relative_source: Path) -> str:
    module_parts = list(relative_source.with_suffix("").parts)
    module_parts.pop()
    return ".".join((PACKAGE_NAME, *module_parts))


def _declared_symbols(package_root: Path, module: str) -> frozenset[str] | None:
    source_file = _module_source(package_root, module)
    if source_file is None:
        return None
    tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
    declarations = tuple(
        value for statement in tree.body if (value := _all_declaration_value(statement)) is not None
    )
    if len(declarations) != 1 or _module_all_mutation_count(tree) != 1:
        return None
    return _string_collection(declarations[0])


def _all_declaration_value(statement: ast.stmt) -> ast.AST | None:
    if (
        isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and statement.targets[0].id == "__all__"
    ):
        return statement.value
    if (
        isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        and statement.target.id == "__all__"
    ):
        return statement.value
    return None


def _module_all_mutation_count(node: ast.AST) -> int:
    if isinstance(
        node,
        (
            ast.AsyncFunctionDef,
            ast.ClassDef,
            ast.DictComp,
            ast.FunctionDef,
            ast.GeneratorExp,
            ast.Lambda,
            ast.ListComp,
            ast.SetComp,
        ),
    ):
        return 0
    return int(_mutates_all(node)) + sum(
        _module_all_mutation_count(child) for child in ast.iter_child_nodes(node)
    )


def _mutates_all(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "__all__" and isinstance(node.ctx, (ast.Del, ast.Store))
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        return isinstance(node.ctx, (ast.Del, ast.Store)) and _rooted_in_all(node)
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in MUTATING_ALL_METHODS
        and _rooted_in_all(node.func)
    )


def _rooted_in_all(node: ast.AST) -> bool:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return isinstance(node, ast.Name) and node.id == "__all__"


def _module_source(package_root: Path, module: str) -> Path | None:
    prefix = f"{PACKAGE_NAME}."
    if not module.startswith(prefix):
        return None
    relative_parts = module.removeprefix(prefix).split(".")
    module_path = package_root.joinpath(*relative_parts)
    for suffix in (".py", ".py.fixture"):
        module_file = module_path.with_suffix(suffix)
        if module_file.is_file():
            return module_file
    package_file = package_root.joinpath(*relative_parts, "__init__.py")
    return package_file if package_file.is_file() else None


def _python_sources(package_root: Path) -> tuple[Path, ...]:
    return tuple(sorted((*package_root.rglob("*.py"), *package_root.rglob("*.py.fixture"))))


def _string_collection(node: ast.AST) -> frozenset[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values: set[str] = set()
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        values.add(element.value)
    return frozenset(values)


def _capability(module: str) -> str | None:
    prefix = f"{PACKAGE_NAME}."
    if not module.startswith(prefix):
        return None
    return module.removeprefix(prefix).split(".", maxsplit=1)[0]


def _format_violations(violations: tuple[ImportViolation, ...]) -> str:
    reported = violations[:MAX_REPORTED_VIOLATIONS]
    lines = [f"{violation.source}:{violation.line}: {violation.problem}" for violation in reported]
    remaining = len(violations) - len(reported)
    if remaining:
        lines.append(f"... {remaining} additional violation(s) omitted")
    return "production import violations:\n" + "\n".join(lines)
