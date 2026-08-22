# Testing policy

This document is the source of truth for test tiers, deterministic acceptance scenarios, contract
fixtures, and live rehearsal policy. Tests prove `product-requirements.md` and
`investment-domain.md` through public lifecycle and execution interfaces.

## Tiers

- **Unit — `tests/unit/`:** pure domain and policy behavior with no filesystem, network, wall clock,
  subprocess, or database. Exercise edge cases, invalid states, and deterministic calculations.
- **Integration — `tests/integration/`:** SQLite/filesystem persistence, composition roots,
  checkpoints, projections, and recorded adapter behavior. Temporary resources remain isolated.
- **Contract — `tests/contract/`:** hostile model/parser input and recorded SEC, Alpaca, Codex, and
  other external representations. Prove both accepted and rejected forms at the boundary.
- **Live rehearsal:** explicitly selected tests against paper or public services. They require operator
  intent, never run in `make check`, and never make the default suite depend on credentials or network.

## Selection

Run the narrowest relevant node while iterating:

```bash
uv run pytest tests/unit/test_package.py
uv run pytest <path-to-test.py>::<test-name>
make check
```

`make check` is the handoff gate: formatting, lint, strict mypy, and the deterministic pytest suite.

## Test contracts

- Assert public receipts, durable events, stored artifacts, projections, packets, and observable world
  state. Avoid private methods and mock call choreography.
- Use fixed timezone-aware clocks, deterministic identifiers, exchange calendars, content hashes, and
  explicit evidence cutoffs.
- Exercise the real entry path for the tested tier. A unit may call a domain capability directly; an
  integration test boots the relevant composition with recorded adapters.
- Test lifecycle transition policy as a pure state machine. Exercise generated command, interruption,
  replay, conflict, and reopen sequences against an independent reference model at the persistence
  tier.
- Verify the world after an operation: persisted rows, emitted receipts, filesystem state, packet
  acceptance, broker simulation, and absence of forbidden effects.
- Cover refusal and recovery paths before the happy-path change is complete: stale input, malformed
  schemas, retry, duplicate delivery, timeout, interruption, rollback, partial fill, and corruption.
- Test LLM output for schema, evidence support, role separation, and prohibited authority. Do not pin
  wording, style, or hidden reasoning.
- Prove as-of availability rather than comparing only source timestamps.
- Keep the Research Lab and executor isolation properties under integration or contract test.

## Coverage

The configured gate requires 100% line and branch coverage of the production package. Coverage is a
deletion and design signal, not proof of correctness. Do not add meaningless assertions, exclusions,
or unreachable branches to satisfy the number. If a line cannot be exercised through a useful
contract, simplify or remove it.

## Mutation testing

`make mutation` uses `mutmut` to verify that tests reject incorrect deterministic safety behavior.
It is a separate, slower gate: `make check`, pre-commit, and pre-push do not run it. Pull requests that
change its source, tests, configuration, or runner execute the same Make target in a keyless job; a
weekly run detects drift outside those path filters.

The active scope is every callable under `domain/`, `portfolio/`, and `execution/`, together with the
implemented Stage 1 configuration resolution, `Advance` orchestration, SQLite lifecycle persistence,
and lifecycle composition. This includes decision-packet validation, deterministic sizing and limits,
target bands, order planning, executor authorization, effect-local idempotency, execution state
transitions, reconciliation, pinned run-input integrity, and lifecycle receipt reconstruction as those
capabilities are implemented. Add semantic Alpaca request mapping, response parsing, and status
normalization to the configured scope with their implementation; keep raw transport and process
wiring under contract and integration tests. Add evidence cutoff, staleness, and append-only research
transition logic when those behaviors become executable safety decisions.

The gate accepts only killed mutants and narrowly skipped equivalent mutations. It fails on surviving,
uncovered, timed-out, suspicious, interrupted, or crashing mutants; it does not reduce those outcomes
to a repository-wide score. A skip must use the narrowest supported pragma with an adjacent explanation
of the semantic equivalence. Prefer adding an observable-behavior test or simplifying meaningless code
over excluding a mutant.

The Make target starts from a clean ignored `mutants/` workspace so a cached result cannot certify a
changed safety path. Invoke `mutmut` directly only for incremental local diagnosis; its cached outcome
is not the repository gate.

Mutation runs select only deterministic unit, integration, and contract tiers. They use fake or
recorded broker boundaries and receive no credentials or network-enabled live rehearsal. The issue-
worktree harness is excluded because it exercises repository scripts outside the mutated production
source tree and cannot distinguish product mutants. While the repository contains no callable in the
configured scope, the runner reports the scaffold exemption and exits successfully; adding the first
scoped callable activates the gate automatically.

## Fixtures

- Record the smallest external representation needed to preserve the contract under test.
- Store no credentials, account identifiers, unpublished research, or mutable local ledgers.
- Annotate the source type, capture or synthetic status, relevant timestamps, entitlement, and any
  intentional redaction.
- Keep fixtures deterministic and portable across macOS and Linux.
- Review fixture changes as behavior changes; never normalize away a material provider difference just
  to preserve an expected file.

## Required deterministic scenarios

Every implementation slice selects its applicable scenarios below and adds both success and refusal
evidence. A scenario becomes mandatory when its owning behavior is implemented.

### Lifecycle and durability

- Advance resumes idempotently across every checkpoint, including termination immediately before and
  after each durable write.
- One Market Session has exactly one Champion Decision Record and at most one active packet.
- Repeated Record calls do not duplicate Outcome Observations.
- Constitution, model, prompt, tool, policy, data, and source hashes remain pinned within a run.
- Corrupted projections rebuild deterministically; corrupted authoritative ledgers fail closed.
- SQLite startup upgrades exact supported schemas in order, preserves authoritative rows, and rolls
  back an interrupted or failed migration without advancing its physical schema version.
- Persistence handles disk-full, transaction rollback, interrupted writes, duplicate event delivery,
  and projection corruption without publishing partial executable state.

### Evidence, memory, and research

- Evidence unavailable at the cutoff is rejected even when its source-event time is earlier.
- SEC acceptance times, amendments, restatements, duplicate content, and changed entity mappings cannot
  leak future information into an as-of read.
- Belief transitions preserve prior values, evidence, falsifiers, and contradiction history.
- Belief Graph rebuild produces the same as-of projection from the Evidence Vault and Belief Ledger.
- Attention caps, holding refresh, and weekly exploration budgets remain invariant as universe size
  changes.
- Model output containing a weight, order, unsupported citation, prompt injection, invalid enum,
  inconsistent horizon, missing Skeptic, timeout, quota exhaustion, or oversized result is rejected or
  safely normalized only as its schema explicitly permits.
- Research Lab artifacts cannot enter Champion stores or produce a packet accepted by execution.

### Portfolio and execution

- Randomized portfolios respect gross, name, sector, liquidity, correlation, cash, whole-share, and
  no-leverage rules.
- Capped inverse-volatility sizing never increases allocation from model confidence and handles
  volatility floors, missing history, stale input, and caps deterministically.
- Equal-weight and Risk Profile shadows consume the same HouseView, eligibility, Evidence Cutoff, and
  available-at-time prices as the Champion.
- Target Bands create no trade inside the band and apply only the approved partial adjustment outside
  it.
- Gaps, events, halt or LULD state, stale quotes, buying power, account or position mismatch, packet
  expiry, and pending orders block or reroute orders exactly as policy states.
- Stable client order identifiers prevent duplicate exposure after timeout, retry, cancellation, and
  partial fill.
- Only Balanced Champion packets are accepted by the Alpaca paper executor.
- The executor cannot alter packet direction or target, invoke a model, accept a Research Lab packet,
  or proceed from incomplete validation.

### Evaluation and governance

- Forecast and benchmark outcomes resolve only after their declared horizons.
- Cost, turnover, return, Sharpe ratio, information ratio, drawdown, calibration, and constraint
  metrics match hand-calculated fixtures.
- A single journal outcome cannot activate a lesson; support, contradiction, bounded influence,
  reinforcement, and expiry follow the investment-domain contract.
- A Challenger cannot alter more than one declared variable, promote itself, or activate without
  operator-approved governance.
- Quota exhaustion cannot select a metered fallback or publish new discretionary orders.
- Invalid formal sessions remain visible and cannot be silently removed or backfilled.

## External contracts and fault injection

- Recorded Alpaca market-data and trading fixtures cover normal, stale, unavailable, partial,
  rejected, canceled, ambiguous-timeout, and out-of-order events.
- SEC and issuer fixtures cover acceptance times, amendments, duplicate content, changed mappings, and
  hostile embedded instructions.
- Scripted model fixtures cover valid output, hallucinated citations, prohibited authority, missing
  Skeptic, conflicting schemas, long output, timeout, and quota exhaustion.
- Scheduler fixtures cover weekends, exchange holidays, early closes, daylight-saving transitions,
  machine sleep and wake, late starts, and resume behavior.
- Changed external or authority seams prove the absence of network, credential, broker, Champion, or
  filesystem effects on every rejected path.

## Forward acceptance

An end-to-end Alpaca paper rehearsal runs only with explicit operator intent. Before the formal
baseline begins, it must demonstrate lifecycle completion, packet handoff, paper execution,
reconciliation, reporting, and safe recovery while the baseline configuration is frozen and hashed.
