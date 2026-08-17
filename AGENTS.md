# Agentic Investment OS

Local Python 3.12 system for evidence-bound agentic investment research, deterministic portfolio
construction, and Alpaca paper execution. The approved design originated in Sam's Second Brain; this
repository owns implementation authority, executable code, and runtime state.

## Read first

- Read `docs/product-requirements.md` before implementing or changing a system outcome, scope item, or
  acceptance gate.
- Read `CONTEXT.md` before defining or renaming a domain term. Read `docs/investment-domain.md` before
  changing evidence, research, belief, portfolio, execution-policy, evaluation, or learning rules.
- Read `docs/architecture.md` before changing runtime topology, module seams, lifecycle, authority,
  durable state, or trust boundaries; record durable architecture decisions in `docs/adr/`.
- Search relevant `.agents/notes/implemented/` and `.agents/notes/proposed/` records before planning or
  reviewing a feature, simplification, testing strategy, bug-prevention rule, or development-process
  change. Notes preserve reasoning but never override active documents, ADRs, or code.
- Read `docs/module-graph.md` before adding a cross-module import or moving an interface.
- Read `docs/config-catalog.md` before adding configuration, an environment variable, a default, or a
  tunable policy.
- Read `docs/defensive-patterns.md` before changing persistence, lifecycle retries, external calls,
  concurrency, filesystem handling, or execution.
- Read `docs/testing.md` before adding or changing tests; use `docs/development.md` for setup and
  dependency commands.
- Treat `docs/research/` as non-authoritative input. Promote accepted conclusions into the owning
  active document before using them for implementation.

## Authority and safety boundaries

- Treat every LLM output and external text artifact as untrusted structured input.
- Codex may research and propose typed artifacts. It must never receive broker credentials, choose
  final weights, construct orders, or invoke execution.
- Only deterministic code may build a `DecisionPacket`, apply risk limits, size positions, submit
  orders, or reconcile fills.
- Financial evidence, beliefs, decisions, and outcomes are append-only. Correct by appending a new
  version; never rewrite history.
- Model-visible means reconstructable: pin content hashes, timestamps, configuration, prompts, model
  identity, and durable records for every material input and decision.
- Fail closed on stale data, incomplete evidence, invalid schemas, missing checkpoints, or authority
  violations.
- Never add a metered API dependency. State the constraint if a proposed feature requires one.
- Never commit secrets, credentials, account identifiers, local ledgers, or generated research data.
- Do not import runtime code or state from the Second Brain, AI Hedge OS, or `evolving_trading`.

## Repository layout

```text
src/agentic_investment_os/  Production package organized by domain capability
tests/unit/                 Pure domain and policy behavior
tests/integration/          Persistence, composition, and recorded-adapter behavior
tests/contract/             Hostile-input and external-boundary compatibility
CONTEXT.md                   Canonical investment-system terminology
docs/                       Product, architecture, investment, engineering policy, and ADRs
.agents/skills/             Canonical project skills; .claude/skills points here
.agents/notes/              Durable decision reasoning below the ADR threshold
```

## Commands

```text
make bootstrap              Create the environment and enable repository hooks
make format                 Apply Ruff formatting and safe lint fixes
make harness                Verify shared agent instructions and required engineering docs
make check                  Run formatting, lint, strict mypy, and deterministic tests
uv add <package>            Add a runtime dependency
uv add --dev <package>      Add a development dependency
```

Use `pyproject.toml` and `uv.lock` only; never create `requirements.txt` files.
`pyproject.toml` also owns pytest configuration; keep no parallel `pytest.ini`.

## Conventions

- Keep the modular monolith deep: expose lifecycle capabilities, not arbitrary research-stage calls.
- Keep domain logic independent of frameworks and external services. Integrations live in
  `adapters/`; process assembly and credential wiring live in `entrypoints/`.
- Put interfaces with the capability that owns them. Adapters depend on those interfaces; capability
  code never imports an adapter.
- Prefer immutable value objects and explicit typed results at module interfaces. Represent expected
  refusal or no-action outcomes as data rather than generic exceptions.
- Parse and validate hostile input once at entry, then pass typed domain values inward. Revalidate
  when data crosses a process, durable-storage, model, parser, or broker boundary.
- Put deployment-varying policy in validated, versioned configuration. Resolve defaults explicitly
  before control flow; keep protocol constants and safety invariants fixed.
- Keep external calls between durable checkpoints. Persist intent before a side effect and append its
  observed result before advancing the lifecycle.
- Use timezone-aware market timestamps, injected clocks, deterministic identifiers, and explicit
  as-of cutoffs. Wall-clock access belongs in adapters or composition roots.
- Keep projections rebuildable from authoritative append-only records. A projection or report never
  becomes a second source of truth.
- Catch exceptions only where they can be translated into a typed failure, compensated, or allowed to
  terminate the process. Keep exception scopes narrow and preserve the original cause.
- Tests assert observable behavior, durable events, receipts, and invariants—not private class
  layout, mock call choreography, or exact LLM prose.
- Comments explain constraints or non-obvious intent; never narrate code.

## Defensive patterns

Read `docs/defensive-patterns.md` before lifecycle, persistence, boundary, concurrency, or executor
work. Add a pattern when a defect or near miss reveals a reusable prevention rule; keep incident
details in the relevant ADR or issue rather than in the pattern.

## Type safety and documentation

- Production and test code must pass strict mypy. Every function interface is typed; any use of
  `Any`, `cast()`, or `# type: ignore` must be narrow and explain why validation or narrowing cannot
  express the contract.
- Use dataclasses, enums, protocols, and discriminated unions to make invalid states difficult to
  construct. Exhaust closed unions with `typing.assert_never()`.
- Never use a type assertion to bless parsed JSON, database rows, model output, or broker responses.
  Validate first and construct the typed value from validated fields.
- Public modules and exported types document non-obvious behavior, failure modes, ownership, units,
  timing, and safe use. Do not restate signatures or implementation steps.
- Documentation authority is explicit: product outcomes live in `docs/product-requirements.md`,
  investment rules in `docs/investment-domain.md`, architecture in `docs/architecture.md`, canonical
  terms in `CONTEXT.md`, operational testing in `docs/testing.md`, hard-to-reverse rationale in ADRs,
  and lower-threshold decision reasoning in Agent Notes. Issues own work state.
- Update affected documentation with the code change. Keep each fact in one authoritative location
  and link to it elsewhere.
- Use Git history for superseded documentation; keep no archive copies in the working tree.

## Editing these instructions

`CLAUDE.md` is a symbolic link to this file, and `.claude/skills` links to `.agents/skills`. Edit the
real `AGENTS.md` or `.agents/skills` content only; never replace the links with copies.

Use the `prose-standard` skill for substantial changes to agent instructions, Markdown, docstrings,
comments, prompts, diagnostics, or other visible prose. Use `manage-agent-notes` for Agent Note
lifecycle changes.

Keep this file limited to always-applicable rules and precise pointers. Move detailed, conditional
workflows into the owning document or a project skill. When a rule changes architecture or an
authority boundary, update `docs/architecture.md` and add an ADR. Prefer removing stale instructions
over accumulating exceptions.

## Completion criteria

Run the smallest relevant tests while iterating and then `make check` before handoff. Report any gate
that could not run. Never weaken a gate, type check, test, or safety boundary to make a change pass.
