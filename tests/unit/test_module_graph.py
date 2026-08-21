from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_NAME = "agentic_investment_os"
PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / PACKAGE_NAME
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


def test_production_imports_follow_the_module_graph() -> None:
    actual_edges: set[tuple[str, str]] = set()
    for source_file in PACKAGE_ROOT.rglob("*.py"):
        owner = source_file.relative_to(PACKAGE_ROOT).parts[0]
        if owner.endswith(".py"):
            continue
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for node in ast.walk(tree):
            imported_modules = _imported_modules(node)
            for imported in imported_modules:
                target = _capability(imported)
                if target is not None and target != owner:
                    actual_edges.add((owner, target))

    disallowed = {
        (owner, target)
        for owner, target in actual_edges
        if target not in ALLOWED_EDGES.get(owner, set())
    }

    assert not disallowed, f"disallowed production import edges: {sorted(disallowed)}"


def _imported_modules(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module is not None:
        return (node.module,)
    return ()


def _capability(module: str) -> str | None:
    prefix = f"{PACKAGE_NAME}."
    if not module.startswith(prefix):
        return None
    return module.removeprefix(prefix).split(".", maxsplit=1)[0]
