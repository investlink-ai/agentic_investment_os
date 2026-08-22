# Agentic Investment OS

An evidence-bound, agentic-first investment operating system for US equities research and Alpaca
paper trading. LLMs perform structured research under a fixed investment constitution; deterministic
code owns provenance, portfolio construction, risk, execution, and evaluation.

The project is deliberately a modular monolith for v0. It exposes a small lifecycle API, runs the
credentialed executor as a separate process boundary, and keeps experimental stage replay inside an
isolated Research Lab.

## Status

Stage 1 implements `Advance` through the durable `PinRunInputs` checkpoint and rebuildable operator
`Status` over validated append-only SQLite lifecycle history. The lifecycle ledger uses database-wide
versioned migrations, while the status projection is disposable and never substitutes for invalid
authoritative records. A pure domain kernel owns reconstruction and transition decisions while SQLite
validates representations and atomically appends the selected record. Research, portfolio, execution,
evaluation, and later lifecycle phases remain scaffolds. Active repository documentation owns
implementation authority, and the runtime has no dependency on the design's Second Brain origin.

## Setup

Requirements: Python 3.12, `uv`, `git`, and `make`.

```bash
make bootstrap
make check
```

`make bootstrap` creates `.venv`, installs the locked development environment, and enables the local
Git hooks. Dependencies live only in `pyproject.toml` and `uv.lock`.

## Repository map

- `src/agentic_investment_os/`: production package, organized by domain capability
- `tests/unit/`: fast domain and policy behavior
- `tests/integration/`: persistence and composition behavior with local or recorded adapters
- `tests/contract/`: external schema, LLM, SEC, and Alpaca boundary fixtures
- `CONTEXT.md`: canonical investment-system terminology
- `docs/product-requirements.md`: V0 outcomes, scope, acceptance, and implementation order
- `docs/architecture.md`: system topology, module seams, lifecycle, authority, and durable state
- `docs/investment-domain.md`: evidence, research, memory, portfolio, execution, and evaluation rules
- `docs/config-catalog.md`: implemented configuration sources and ownership
- `docs/defensive-patterns.md`: reusable prevention rules for high-risk bug classes
- `docs/testing.md`: deterministic, contract, integration, and live-test policy
- `docs/module-graph.md`: allowed Python import directions
- `docs/adr/`: durable architectural decisions
- `docs/research/`: non-authoritative investigations awaiting explicit promotion
- `.github/`: keyless CI, dependency updates, and concise contribution templates
- `.agents/skills/`: canonical repository-specific coding-agent skills
- `.agents/notes/`: durable feature, simplification, testing, and process decision reasoning

See `docs/development.md` for the development workflow.
