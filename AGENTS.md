# Agentic Investment OS

Local Python 3.12 system for evidence-bound agentic investment research, deterministic portfolio
construction, and Alpaca paper execution. The approved solution design lives in Sam's Second Brain;
this repository owns executable code and runtime state only.

## Read first

- Read `docs/architecture.md` before changing package boundaries, lifecycle, memory, risk, or
  execution.
- Record durable architecture changes as an ADR in `docs/adr/`.
- Use `docs/development.md` for environment and dependency commands.

## Authority and safety boundaries

- Treat every LLM output and external text artifact as untrusted structured input.
- Codex may research and propose typed artifacts. It must never receive broker credentials, choose
  final weights, construct orders, or invoke execution.
- Only deterministic code may build a `DecisionPacket`, apply risk limits, size positions, submit
  orders, or reconcile fills.
- Financial evidence, beliefs, decisions, and outcomes are append-only. Correct by appending a new
  version; never rewrite history.
- Every model-visible input and material decision must be reconstructable from pinned content hashes,
  timestamps, configuration, and durable records.
- Fail closed on stale data, incomplete evidence, invalid schemas, missing checkpoints, or authority
  violations.
- Never add a metered API dependency. State the constraint if a proposed feature requires one.
- Never commit secrets, credentials, account identifiers, local ledgers, or generated research data.
- Do not import runtime code or state from the Second Brain, AI Hedge OS, or `evolving_trading`.

## Code conventions

- Keep the modular monolith deep: expose lifecycle capabilities, not arbitrary research-stage calls.
- Keep domain logic independent of frameworks and external services. Put integrations in `adapters/`
  and process composition in `entrypoints/`.
- Prefer explicit typed contracts and immutable value objects at module boundaries.
- Validate hostile, model, parser, storage, and broker boundaries at entry.
- Put tunable policy in versioned configuration rather than hard-coding it in control flow.
- Tests assert observable behavior, durable events, receipts, and invariants—not private class layout
  or exact LLM prose.
- Comments explain constraints or non-obvious intent; do not narrate obvious code.

## Commands

- Bootstrap: `make bootstrap`
- Format: `make format`
- Full local gate: `make check`
- Add runtime dependency: `uv add <package>`
- Add development dependency: `uv add --dev <package>`

Use `pyproject.toml` and `uv.lock` only; do not create `requirements.txt` files.

## Completion criteria

Before handoff, run the smallest relevant tests while iterating and then `make check`. Report any
check that could not run. Do not weaken a gate or safety boundary to make a change pass.
