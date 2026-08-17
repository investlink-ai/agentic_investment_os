# ADR 0001: Python 3.12 modular monolith

- Status: Accepted
- Date: 2026-08-17

## Context

V0 needs rapid iteration around data retrieval, LLM research, SQLite projections, portfolio analysis,
and Alpaca paper trading. Its critical safety property is not distributed scale; it is keeping
untrusted research separate from deterministic portfolio and execution authority. The first runtime
will be driven by scheduled Codex research, while the internal state machine may later move to
LangGraph.

## Decision

Use Python 3.12 in one implementation repository and one installable package. Organize it as a modular
monolith with explicit domain modules. Run the Order Execution Module as a credential-isolated process
and keep Research Lab state separate, while sharing deterministic domain contracts inside the same
codebase. Use `uv`, `pyproject.toml`, Ruff, strict mypy, pytest, and local versioned Git hooks.

## Consequences

- Python minimizes integration friction for LLM, data, statistics, SQLite, and Alpaca work.
- One repository keeps contracts and deterministic tests easy to evolve during v0.
- Process and authority isolation are enforced by composition roots and credentials, not by premature
  network services.
- CPU-bound or latency-critical components may later be replaced behind typed ports if measurement
  justifies it.
- LangGraph may replace only internal orchestration; external lifecycle and safety contracts remain.
