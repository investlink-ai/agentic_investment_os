# Agentic Investment OS

An evidence-bound, agentic-first investment operating system for US equities research and Alpaca
paper trading. LLMs perform structured research under a fixed investment constitution; deterministic
code owns provenance, portfolio construction, risk, execution, and evaluation.

The project is deliberately a modular monolith for v0. It exposes a small lifecycle API, runs the
credentialed executor as a separate process boundary, and keeps experimental stage replay inside an
isolated Research Lab.

## Status

Engineering scaffold only. The approved solution specification remains in Sam's Second Brain; this
repository is the independent implementation boundary and has no runtime dependency on that repo.

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
- `docs/architecture.md`: module, authority, and state boundaries
- `docs/adr/`: durable architectural decisions
- `.agents/skills/`: canonical repository-specific coding-agent skills

See `docs/development.md` for the development workflow.
