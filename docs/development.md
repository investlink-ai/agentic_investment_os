# Development

## Environment

Python is fixed to the 3.12 minor line in `.python-version` and `pyproject.toml`. Create the local
environment and enable versioned hooks with:

```bash
make bootstrap
```

`uv` owns dependency resolution, `.venv`, and `uv.lock`. Add packages through `uv add`; do not install
into the environment manually and do not create `requirements.txt` files.

## Quality commands

```bash
make format     # apply Ruff formatting and safe lint fixes
make lint       # formatter and linter checks
make typecheck  # strict mypy over source and tests
make test       # deterministic tests with branch coverage
make check      # full local gate
```

The pre-commit hook runs fast formatting, lint, and staged-diff checks. The pre-push hook runs the full
gate. Hooks are guardrails, not substitutes for focused tests while developing.

## Test placement

- `tests/unit`: pure domain and policy behavior; no filesystem, network, wall clock, or subprocesses.
- `tests/integration`: local SQLite/filesystem composition and recorded adapter behavior.
- `tests/contract`: hostile inputs and external response/schema compatibility.

Use fixed clocks and deterministic identifiers. Assert public behavior and durable invariants rather
than internal call sequences. Keep live Alpaca paper and network checks explicitly marked and outside
the default test command.

## Dependency policy

Prefer the standard library until a dependency makes a boundary materially safer or simpler. Runtime
dependencies require a concrete use case and must not introduce a metered service. Keep broker and
model clients behind typed adapter interfaces so deterministic tests need no credentials or network.
