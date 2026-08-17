# Product requirements

This document is the source of truth for V0 product outcomes, scope, and acceptance. It states what
the system must deliver without duplicating architecture, investment rules, configuration, or test
procedure. Follow the linked authoritative documents for those details.

## Problem and hypothesis

The system tests whether evidence-bound, event-sensitive agentic research can improve prospective
investment decisions in a dynamic market while deterministic code retains provenance, sizing, risk,
execution, and evaluation authority.

The null hypothesis is that it does not beat SPY net of costs and constraints. Better prose or process
quality is not automatically alpha. The product must be able to report an inconclusive or negative
result and stop without rationalizing continued activity.

## Success outcomes

### Operation and governance

- **PR-OPS-001 — Single lifecycle:** One production lifecycle interface advances or resumes a Market
  Session; normal operation never requires manually sequencing research stages.
- **PR-OPS-002 — Idempotent operation:** Repeated operations survive process interruption without
  duplicating evidence, beliefs, decisions, outcomes, or orders.
- **PR-OPS-003 — Observable status:** Status exposes the active phase, last completed session, pinned
  run identity, liveness, and any durable no-action or fail-closed reason.
- **PR-OPS-004 — Operator digest:** A concise Markdown or static HTML digest links to stable detailed
  artifacts and reports decisions, objections, changes, fills, costs, resolutions, Challenger state,
  resource use, and failures.
- **PR-OPS-005 — Human governance:** The operator alone approves Constitution amendments, controlled
  policy activation, Champion promotion, and any future live-capital design. Activation occurs only at
  a later session boundary.
- **PR-OPS-006 — Isolated laboratory:** The Research Lab can replay or stop at stages against copied or
  synthetic state without changing Champion state, publishing executable artifacts, or reaching the
  broker.
- **PR-OPS-007 — Unattended schedule:** Operation on one Mac uses NYSE-calendar-relative scheduling and
  visibly reports missed or incomplete eligible sessions.

### Evidence, research, and memory

- **PR-EVD-001 — Reconstructable evidence:** The system captures allowed market, news, filing, issuer,
  and official-calendar evidence with content identity, entitlement, parser version, and complete
  as-of provenance.
- **PR-EVD-002 — Bounded attention:** Broad universe coverage begins with deterministic local attention
  and bounded research capacity; all current holdings remain in scope and a fixed exploration budget
  prevents permanent funnel blindness.
- **PR-EVD-003 — Evidence-bound claims:** Every material research claim cites captured evidence.
  Unsupported facts and unavailable consensus remain explicitly unknown.
- **PR-EVD-004 — Independent research roles:** The workflow includes evidence collection, Thesis
  construction, clean-context skepticism, scenario forecasting, and CIO resolution under a pinned
  Constitution.
- **PR-EVD-005 — Abstention without sizing authority:** The CIO may abstain or choose cash and cannot
  emit accepted weights or order instructions.
- **PR-EVD-006 — Append-only memory:** Evidence and belief history remain append-only; the Belief Graph
  and reports rebuild from authoritative records; later outcomes cannot alter ex-ante decisions.
- **PR-EVD-007 — Bounded lessons:** Journal Lessons require repeated independent evidence, expire
  without reinforcement, and can affect only retrieval or attention within fixed limits.

### Portfolio and execution

- **PR-PEX-001 — Shared HouseView:** One HouseView feeds deterministic Champion and shadow portfolio
  construction so selection and sizing effects remain separable.
- **PR-PEX-002 — Bounded paper authority:** Balanced alone reaches Alpaca paper. Cash remains valid;
  constraints never force full investment.
- **PR-PEX-003 — Deterministic portfolio intent:** Target Bands suppress unnecessary churn; sizing,
  risk clamps, packet construction, order policy, and reconciliation are deterministic and versioned.
- **PR-PEX-004 — Independent packet validation:** The executor validates complete, immutable, signed,
  scoped, and expiring packets before any broker effect.
- **PR-PEX-005 — Effect idempotency:** Stable client order identifiers and durable checkpoints prevent
  duplicate exposure after retry, timeout, cancellation, or partial fill.
- **PR-PEX-006 — Fail-closed trading:** Stale, missing, halted, contradictory, unauthorized, or
  unreconciled state blocks new discretionary orders with a durable disposition.

### Evaluation and controlled learning

- **PR-EVL-001 — Separated evaluation:** Forecast, portfolio, and process quality remain separate and
  reconstructable from frozen ex-ante records and due Outcome Observations.
- **PR-EVL-002 — Official benchmark:** Balanced is evaluated against full SPY buy-and-hold net of a
  frozen modeled-cost overlay; shadow diagnostics do not replace the official objective.
- **PR-EVL-003 — Micro-live eligibility:** A separately approved design becomes eligible only after
  sixty completed sessions, fifty resolved forecasts, positive net benchmark performance, and all
  risk and process gates.
- **PR-EVL-004 — Scaling evidence:** No scaling or validity claim is permitted before 252 sessions
  across multiple market regimes.
- **PR-EVL-005 — Controlled Challengers:** Each Challenger changes one preregistered variable, runs
  prospectively with equivalent inputs, and cannot promote itself or change its evaluator.

The detailed investment contract for these outcomes lives in `investment-domain.md`.

## Operational constraints

- **PR-CON-001 — Subscription boundary:** Use the existing Codex subscription, the account's Alpaca
  entitlement, and allowed keyless public sources. Add no metered API dependency or fallback.
- **PR-CON-002 — Credential isolation:** Keep broker credentials outside model processes, source
  control, logs, fixtures, reports, and local research artifacts.
- **PR-CON-003 — Runtime-state isolation:** Store runtime ledgers and generated research only under
  ignored configurable roots.
- **PR-CON-004 — Deterministic local gate:** Default development and acceptance tests require no
  network, mutable local account, or credential.
- **PR-CON-005 — Maintenance budget:** Pause or narrow V0 if it creates routine manual research, daily
  recovery work, or another paid operating burden.

## V0 scope

Included:

- local single-operator research;
- US-listed common stocks and a curated unlevered ETF allowlist;
- long-only, unlevered daily portfolio decisions;
- Alpaca paper market data and execution;
- SEC, issuer, and official macro evidence;
- deterministic Champion and shadow portfolios;
- static operator reporting; and
- prospective paper evaluation and controlled shadow Challengers.

Excluded:

- live capital, client accounts, public portfolios, copy trading, personalized advice, distribution,
  subscriptions, or regulated advisory activity;
- shorts, leverage, options, futures, crypto, pairs, market-neutral, or intraday strategies;
- social-media ingestion, paid sentiment or consensus feeds, and any metered service;
- official point-in-time S&P 500 or S&P MidCap 400 replication;
- backtest-selected factor mining as the alpha engine;
- Black–Litterman or another optimizer as the V0 Champion;
- unbounded or advanced order types, extended hours, and autonomous execution-policy changes;
- arbitrary production execution of private research stages;
- autonomous code, objective, Constitution, evaluator, risk, broker-policy, or Champion changes;
- LangGraph in V0; and
- a dashboard, mobile application, notification platform, or multi-user permission system.

## Acceptance

The deterministic scenarios in `testing.md` are required acceptance evidence. Before the formal
sixty-session clock begins:

- **PR-AC-001 — Paper rehearsal:** The end-to-end Alpaca paper rehearsal passes under explicit operator
  control.
- **PR-AC-002 — Frozen baseline:** Baseline configuration and calibration-only parameters are frozen
  and hashed.
- **PR-AC-003 — Visible invalid sessions:** Invalid or incomplete formal sessions remain recorded
  rather than removed or backfilled.
- **PR-AC-004 — Safety gates:** All lifecycle, authority, provenance, durability, risk, execution, and
  Research Lab isolation gates pass.

## Implementation sequence

Implement independently verifiable vertical slices in this dependency order:

1. domain events, SQLite ledgers, projections, lifecycle checkpoints, and invariants;
2. recorded adapters, Evidence Vault, as-of provenance, universe, and attention funnel;
3. model role contracts, validation, Constitution, and Research Lab isolation;
4. HouseView, deterministic portfolio construction, Target Bands, and shadow accounting;
5. Alpaca paper executor, reconciliation, scheduler, and operator digest; and
6. forecast resolution, evaluation, Journal Lessons, and Champion–Challenger governance.

Every slice preserves the authority and safety architecture while delivering observable acceptance
evidence through a public interface.
