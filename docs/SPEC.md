# Agentic Investment OS v0

**Status:** `ready-for-agent`

**Date:** 2026-08-17

**Artifact role:** approved solution design in Sam's Second Brain

**Implementation boundary:** implementation will live in a separate sibling repository

## Problem Statement

Sam wants to test whether an agentic, evidence-driven investment system is a better fit for a
dynamic, noisy, frequently irrational market than a traditional workflow centered on mining fixed
signals and repeatedly backtesting them. The intended system should continuously research new
information, maintain an explicit belief state, challenge its own reasoning, learn cautiously from
resolved decisions, and make daily long-only US equity decisions with Alpaca as the market-data and
paper-execution engine.

The hypothesis is plausible but unproven. Market adaptation does not imply that an LLM can forecast
returns. LLMs bring their own historical-pattern bias, hallucinations, consensus imitation,
uncalibrated confidence, recency effects, and sensitivity to prompt framing. Persistent memory can
amplify those problems by turning one lucky or unlucky outcome into a durable rule. Autonomous
self-modification can become online overfitting or reward hacking. A polished research workflow can
improve process quality without producing alpha.

The design problem is therefore not “how can an LLM pick stocks?” It is how to build a harness that
makes agentic investment reasoning:

- evidence-bound and as-of reproducible;
- skeptical rather than narrative-seeking;
- token-efficient across a broad large- and mid-cap-oriented universe;
- separated from deterministic portfolio risk and broker execution;
- capable of bounded, auditable learning without rewriting history;
- measurable through forward paper outcomes rather than persuasive prose; and
- able to return “no demonstrated edge” as a valid result.

The system must work with the available subscription boundary. It must not require a metered API.
Codex uses the existing subscription. Alpaca uses the account's existing entitlement. SEC EDGAR and
official public calendars are keyless. Any later flat subscription is an explicit, versioned data
regime rather than a silent dependency.

This is not a rejection of quantitative methods. Deterministic calculations remain necessary for
data quality, risk, sizing, execution, cost accounting, and evaluation. The distinction is that v0
does not search a large fixed factor grammar and treat historical backtest selection as its source of
truth. Its investment hypothesis is agentic interpretation of current evidence and expectation
changes; its proof standard is prospective performance.

## Solution

Build `agentic-investment-os` as a local modular monolith with three high-level seams:

1. **Investment Operating System** owns the complete research and decision lifecycle through three
   operations: advance a session, record due outcomes, and apply an approved governance change.
2. **Order Execution Module** is a separate deterministic hot path that validates an approved
   decision packet, manages Alpaca paper orders, and reconciles fills into outcome records. Codex is
   not reachable from this module and never receives trading credentials.
3. **Research Lab** can replay or stop at individual research stages in an isolated namespace. It
   cannot write champion memory, publish executable decisions, or reach the broker.

The normal production caller sees one lifecycle operation rather than a menu of research stages.
Internally, the operating system uses a checkpointed append-only state machine so a killed or
repeated run resumes idempotently. A later LangGraph implementation may replace that internal
orchestrator without changing the external lifecycle, journals, authority boundaries, or execution
contract.

Each daily decision begins with a short, versioned Investment Constitution. A bounded discovery
funnel finds information-sensitive candidates locally, then Codex follows a fixed research workflow:
Evidence Collector, Thesis Builder, independent Skeptic, Scenario Forecaster, and CIO. Every material
claim must point to captured evidence. The CIO expresses stance, scenarios, horizon, invalidators,
uncertainty, and abstention; it does not choose weights or orders.

Critical financial memory is external and event-sourced:

- an immutable Evidence Vault stores captured source artifacts;
- a bitemporal Belief Ledger stores every belief transition without overwriting the past;
- a compact Belief Graph is rebuilt as an as-of projection for retrieval; and
- a Decision Journal separates the original decision, observed outcome, agent attribution, and
  operator note.

The deterministic Risk Kernel converts accepted theses into target bands. V0 uses capped
inverse-volatility risk budgeting for the Balanced paper portfolio and maintains a capped
equal-weight shadow comparator. Conservative and Growth risk profiles remain internal shadows.
Black–Litterman and other expected-return optimizers remain later challengers because the system does
not yet possess calibrated numeric return views, view-error covariance, or a defensible equilibrium
prior.

The official empirical objective is to beat SPY net of modeled costs while respecting drawdown,
turnover, concentration, calibration, and invariant gates. Sixty completed sessions plus fifty
resolved forecasts can only make the system eligible for a separately approved capped micro-live
design. At least 252 sessions and multiple regimes are required before any scaling or validity claim.

## User Stories

### Operator and governance

1. As Sam, I want one production lifecycle command for a market session, so that I do not need to
   orchestrate research stages manually.
2. As Sam, I want repeated lifecycle calls to be idempotent, so that retries cannot duplicate
   beliefs, decisions, or orders.
3. As Sam, I want to inspect the active phase and last completed session, so that a silent failure is
   visible without reading internal state.
4. As Sam, I want a concise daily Markdown or HTML digest, so that normal oversight fits into an
   existing routine.
5. As Sam, I want every run pinned to one Constitution version, so that its reasoning standard cannot
   change halfway through a decision.
6. As Sam, I want to be the only authority who amends the Constitution, so that the agent cannot
   redefine acceptable investment behavior.
7. As Sam, I want material workflow and policy changes to run as challengers before promotion, so that
   self-evolution remains testable.
8. As Sam, I want champion promotion to require my explicit approval, so that the agent cannot grade
   and deploy its own changes.
9. As Sam, I want an isolated Research Lab, so that I can inspect or replay a stage without bypassing
   production invariants.
10. As Sam, I want production to reject arbitrary stage execution, so that no caller can jump directly
    from a persuasive thesis to a broker action.
11. As Sam, I want the project to run unattended on one Mac, so that it remains compatible with a
    maintenance-only side-project budget.
12. As Sam, I want a clean stop result when no edge is demonstrated, so that the system does not become
    a permanent rationalization engine.

### Evidence and universe

13. As the research system, I want a broad membership-agnostic universe of liquid US companies, so
    that the opportunity set approximates large- and mid-cap coverage without an unavailable index
    feed.
14. As the research system, I want the eligibility rules frozen and versioned before formal paper
    evaluation, so that the universe cannot be tuned retrospectively to winners.
15. As the research system, I want a zero-token local scan before any LLM work, so that broad coverage
    does not exhaust the Codex subscription.
16. As the research system, I want no more than twenty candidate cards and five new deep dossiers per
    daily cycle, so that reasoning stays focused.
17. As the portfolio manager, I want every current holding researched even when it is not newly
    promoted, so that existing risk never disappears from attention.
18. As the research system, I want ten to twenty percent of the weekly research budget reserved for
    exploration, so that promoters do not permanently suppress unfamiliar opportunities.
19. As the evidence collector, I want price, quote, status, liquidity, news, filing, issuer, and public
    calendar evidence captured as artifacts, so that conclusions can be audited.
20. As the evidence collector, I want publication, source-event, availability, and ingestion times
    retained, so that later reasoning can reconstruct what was knowable.
21. As the evidence collector, I want SEC filings keyed by accession and acceptance time, so that later
    amendments and restatements cannot leak backward.
22. As the research system, I want unsupported analyst-consensus, retail-sentiment, or earnings-calendar
    claims marked Unknown, so that absent data is not replaced by model memory.
23. As the evaluator, I want every data entitlement and feed version recorded, so that IEX and any
    later SIP observations are not mixed silently.

### Research and decision reasoning

24. As the Evidence Collector, I want to distinguish source facts from interpretation, so that the
    thesis does not masquerade as evidence.
25. As the Thesis Builder, I want each thesis to state the market expectation and a variant view, so
    that positive company news alone is not treated as an opportunity.
26. As the Thesis Builder, I want a causal chain, catalyst, horizon, and explicit invalidators, so that
    the decision can later be resolved rather than rationalized.
27. As the independent Skeptic, I want a clean context and the strongest countercase, so that I am not
    anchored by the thesis author's narrative.
28. As the Scenario Forecaster, I want bull, base, and bear cases with observable resolution criteria,
    so that uncertainty is represented rather than hidden in one point estimate.
29. As the CIO, I want the option to abstain or hold cash, so that the daily schedule does not force
    activity.
30. As the CIO, I want information and sentiment interpreted alongside growth, quality, valuation,
    price behavior, and catalyst risk, so that attention does not become one-dimensional news chasing.
31. As the CIO, I want ObservedView, InferredPerception, and Unknown kept distinct for retail and Wall
    Street beliefs, so that imagined consensus is not treated as observed consensus.
32. As the CIO, I want a compact investment doctrine based on durable world-class investment wisdom,
    so that reasoning is principled without imitating a famous investor's historical trades.
33. As the risk system, I want Codex to emit no exact weights or order instructions, so that
    uncalibrated language-model confidence cannot directly control capital.
34. As the execution system, I want material opening gaps and new public events to invalidate stale
    entries, so that a pre-open thesis is not executed into a different market state.

### Memory and learning

35. As the auditor, I want raw evidence immutable and content-addressed, so that cited source material
    cannot change unnoticed.
36. As the agent, I want a compact as-of Belief Graph rather than an ever-growing prompt transcript, so
    that critical memory is relevant and token-efficient.
37. As the auditor, I want the Belief Ledger—not the graph projection—to be the source of truth, so
    that the graph can be rebuilt after corruption or schema changes.
38. As the agent, I want beliefs to expire, weaken, contradict, supersede, become dormant, or be
    refuted, so that memory does not preserve every historical view as simultaneously true.
39. As the agent, I want contradictory evidence retained rather than overwritten, so that evolving
    views remain auditable.
40. As the evaluator, I want the original decision separated from its later outcome and commentary, so
    that hindsight cannot alter the initial rationale.
41. As the agent, I want to propose lessons from repeated journal outcomes, so that the research funnel
    can adapt to demonstrated process failures.
42. As the risk owner, I want a journal lesson limited to retrieval or one attention-state movement, so
    that recent outcomes cannot rewrite sizing or execution.
43. As the agent, I want lessons to expire unless reinforced by independent decisions, so that regime-
    specific observations do not become permanent doctrine.

### Portfolio construction and execution

44. As the portfolio manager, I want accepted positions to begin with equal standalone risk budgets,
    so that prose confidence is not mistaken for calibrated expected return.
45. As the portfolio manager, I want deterministic volatility scaling and hard concentration caps, so
    that volatile names cannot dominate portfolio risk accidentally.
46. As the portfolio manager, I want unallocated risk to remain cash, so that constraints do not force
    redistribution into weaker ideas.
47. As the evaluator, I want a capped equal-weight shadow on the same HouseView, so that the value of
    sizing can be isolated from the value of selection.
48. As the evaluator, I want Conservative and Growth profiles simulated from the same HouseView, so
    that risk-profile behavior can be compared without opening more broker accounts.
49. As the execution system, I want only the Balanced champion portfolio to reach Alpaca paper
    trading, so that v0 operational complexity remains bounded.
50. As the execution system, I want target bands rather than continuously chased point weights, so that
    daily reassessment does not create daily churn.
51. As the execution system, I want whole-share regular-session day-limit orders, so that order
    behavior remains simple and inspectable.
52. As the execution system, I want at most one bounded cancel-and-reprice attempt, so that v0 cannot
    enter an uncontrolled execution loop.
53. As the execution system, I want partial fills reconciled before any replacement order, so that a
    retry cannot double the intended exposure.
54. As the execution system, I want hard exits to use bounded aggressive limits rather than unbounded
    market orders, so that urgency does not remove price protection.
55. As the risk owner, I want no-trade behavior on stale, missing, halted, or contradictory state, so
    that failure defaults to safety.

### Evaluation and controlled evolution

56. As the evaluator, I want every forecast frozen before its evidence cutoff, so that later knowledge
    cannot improve the recorded prediction.
57. As the evaluator, I want forecast calibration scored separately from portfolio P&L, so that luck
    and reasoning quality are not conflated.
58. As Sam, I want the official Balanced objective to beat SPY net of modeled costs, so that success has
    a clear external benchmark.
59. As Sam, I want Sharpe ratio reported alongside information ratio, return, drawdown, turnover, cost,
    calibration, and constraint adherence, so that no single metric can hide a fragile result.
60. As the evaluator, I want exposure-matched SPY-plus-cash diagnostics for shadow profiles, so that
    lower gross exposure is not mistaken for stock-selection skill.
61. As Sam, I want sixty sessions and fifty resolved forecasts before considering a micro-live design,
    so that one short winning streak cannot trigger capital deployment.
62. As Sam, I want at least 252 sessions and multiple regimes before scaling or claiming validity, so
    that preliminary paper results remain appropriately qualified.
63. As the evolution system, I want each challenger to change one variable, so that performance changes
    remain attributable.
64. As the evolution system, I want prompt, workflow, model, retrieval, and policy fingerprints stored
    with every decision, so that champion and challenger records are reproducible.
65. As Sam, I want Constitution, objectives, evaluator, schemas, risk, execution, and champion promotion
    under human control, so that the system cannot redefine its own judge.
66. As the agent, I want bounded belief, retrieval, and attention updates to happen autonomously, so that
    the system can adapt without waiting for manual memory maintenance.

### Operations and verification

67. As the scheduler, I want all timing expressed against the NYSE session calendar, so that Melbourne
    daylight-saving changes do not move market operations.
68. As Sam, I want scheduled wake, launchd, and process keep-awake behavior, so that the Mac can run the
    daily cycle without babysitting.
69. As Sam, I want quota exhaustion to stop new discretionary trades rather than trigger a metered API
    fallback, so that the subscription-only rule is mechanical.
70. As a tester, I want recorded Alpaca, SEC, and Codex adapters, so that lifecycle behavior can be
    tested without network access or broker side effects.
71. As a tester, I want time-travel and as-of tests, so that future evidence cannot enter a historical
    decision context.
72. As a tester, I want injected failures for duplicated runs, invalid citations, gaps, halts, stale
    data, partial fills, and corrupted projections, so that fail-closed behavior is demonstrated.
73. As a future maintainer, I want a stable external lifecycle seam, so that replacing the native state
    machine with LangGraph does not change the domain contract.

## Implementation Decisions

### 1. Project and repository boundary

- `agentic-investment-os` is a new project, not a v2 mode inside the existing traditional-quant
  engine.
- This Second Brain directory stores the approved design, later decisions, research notes, and status.
  It contains no runtime dependency and no broker secrets.
- All executable code, tests, local runtime state, and deployment configuration belong in a separate
  sibling implementation repository with the same project identity.
- The existing AI Hedge OS and `evolving_trading` implementation remain frozen as a traditional-quant
  benchmark. Their useful patterns—append-only ledgers, deterministic gates, data/version
  fingerprints, fail-closed operation, and harness controls—may be reimplemented. Their factor DSL,
  backtest-first search loop, and runtime modules are not dependencies.
- GPTPortfolio is inspiration for an agentic portfolio loop, not an interface or implementation to
  clone.

### 2. Falsifiable thesis and success criteria

- The null hypothesis is that the agentic system does not produce net excess return over SPY after
  costs and constraints.
- The primary hypothesis is that evidence-bound, event-sensitive agentic research can adapt to new
  public information sufficiently well to produce a better forward HouseView than the benchmark.
- Secondary hypotheses are evaluated independently:
  - compact external belief memory improves forecast calibration relative to a stateless ablation;
  - the independent Skeptic reduces unsupported or one-sided decisions;
  - deterministic promoters and demoters preserve opportunity coverage while reducing LLM tokens;
  - capped risk budgeting improves drawdown-adjusted results relative to capped equal weight; and
  - journal-derived attention lessons improve prospective process quality without increasing
    turnover or concentration.
- A workflow-quality improvement is not automatically alpha. Each secondary hypothesis has its own
  shadow comparison.
- Historical replay establishes mechanics, leakage resistance, failure behavior, and rough cost
  sensitivity. It does not validate the investment thesis.
- Forward paper evaluation is the primary evidence. The system must report an inconclusive or negative
  result when the gates are not met.

### 3. V0 investment scope

- Personal, single-operator research and Alpaca paper trading only.
- US-listed common stocks plus a small curated allowlist of liquid, unlevered ETFs.
- Long-only; no leverage; cash is a valid and sometimes preferred allocation.
- Daily decision cadence with intended thesis holding horizons from one to twenty trading days.
- Soft target of fifteen to twenty concurrent positions. The system may hold fewer when evidence is
  weak.
- The HouseView is shared across risk profiles. Balanced is the only executable paper profile;
  Conservative and Growth are internal shadows.
- SPY is the sole official external benchmark. QQQ may be inspected informally but is not an objective
  or promotion criterion.

### 4. High-level seams and lifecycle interface

The design deliberately exposes lifecycle capabilities rather than internal research stages.

**Investment Operating System**

- **Advance:** accepts the desired market session, run mode, and an optional idempotency key. It
  resolves or resumes the session lifecycle and returns a receipt containing disposition, completed
  phase, pinned Constitution and configuration hashes, evidence cutoff, HouseView identifier,
  DecisionPacket identifier, and any fail-closed reason.
- **Record:** accepts a batch of due execution and market outcomes. It appends observations and returns
  the identifiers and horizons resolved. It never edits original decisions.
- **Govern:** accepts a Sam-approved, signed governance change. It versions the affected Constitution,
  champion, or controlled policy and returns the activation boundary. It cannot take effect inside an
  already-started session.

**Order Execution Module**

- **Apply:** accepts one validated DecisionPacket, independently verifies its signature/hash,
  Constitution, expiry, account, session, and risk envelope, then manages the allowed Alpaca paper
  orders and returns an ExecutionReceipt.
- **Reconcile:** reads broker order and position state, matches fills by idempotent client order ID, and
  returns an OutcomeBatch for the operating system to record.
- This module has no Codex adapter and no ability to alter the DecisionPacket.

**Research Lab**

- Replays evidence, retrieval, thesis, skeptic, scenario, CIO, sizing, or evaluation stages against a
  copied or synthetic namespace.
- May stop after any stage and expose intermediate artifacts.
- Cannot write the champion Belief Ledger, Decision Journal, execution outbox, or broker account.
- Lab output is clearly marked non-production and cannot be accepted by the Order Execution Module.

Production CLI operations map to the lifecycle: daily advance, status, outcome record/reconcile, and
approved governance. Commands such as “run only CIO,” “skip skeptic,” or “update graph directly” are
intentionally absent from production.

Internally, the operating system is a native checkpointed state machine over append-only events.
External calls occur only between durable checkpoints. Repetition resumes the last safe phase rather
than duplicating state. A future LangGraph adapter may implement the internal transition engine only;
it must preserve these external contracts.

### 5. Authority model

| Capability                       | Codex                    | Deterministic harness                | Risk Kernel                   | Executor                  | Sam                      |
| -------------------------------- | ------------------------ | ------------------------------------ | ----------------------------- | ------------------------- | ------------------------ |
| Collect and summarize evidence   | Propose                  | Validate and persist                 | None                          | None                      | Inspect                  |
| Create or revise beliefs         | Propose                  | Enforce provenance, time, and schema | None                          | None                      | Inspect                  |
| Build thesis and scenarios       | Propose                  | Validate completeness                | None                          | None                      | Inspect                  |
| Challenge a thesis               | Propose in clean context | Require and validate                 | None                          | None                      | Inspect                  |
| Choose stance or abstain         | Propose                  | Build HouseView record               | None                          | None                      | Inspect                  |
| Choose exact position size       | No                       | Supply market/risk inputs            | Decide deterministically      | No                        | Own policy               |
| Relax portfolio constraint       | No                       | No                                   | No                            | No                        | Approve versioned change |
| Create or alter broker order     | No                       | No                                   | Authorize bounded intent only | Execute deterministically | Own policy               |
| Change Constitution or evaluator | Propose at most          | No                                   | No                            | No                        | Approve and activate     |
| Promote a challenger             | Recommend                | Compute evidence                     | No                            | No                        | Approve                  |

Codex output is always untrusted structured input. It cannot construct the final DecisionPacket,
reach trading credentials, alter deterministic calculations, select evaluation thresholds, or change
the next lifecycle transition.

### 6. Investment Constitution

The Constitution is a short, fixed, versioned reasoning doctrine owned by Sam. It frames the CIO as a
patient, skeptical, world-class capital allocator without impersonating a named investor or copying
historical trades. V0 principles are:

1. Invest against expectations, not merely good or bad company news.
2. State the market's apparent expectation and the variant view explicitly.
3. Use scenarios rather than one precise forecast.
4. Explain the causal chain from evidence to business or market impact.
5. Name the catalyst, expected horizon, and observable resolution.
6. State invalidators before taking risk.
7. Invert the thesis and examine permanent-loss paths.
8. Stay within the evidence-backed circle of competence; otherwise abstain.
9. Prefer asymmetric payoff with survivable downside.
10. Respect liquidity, concentration, common-cause exposure, and correlation.
11. Update beliefs when evidence changes, while preserving the prior record.
12. Separate process quality from outcome luck; never force activity.

The Constitution is injected into every decision-relevant Codex context. A run records its content
hash. Amendments apply only at a new session boundary and create a new experimental regime.

### 7. Domain model

- **Market Session:** one NYSE trading date and its exchange-relative lifecycle.
- **Evidence Artifact:** immutable captured source content with identity, content hash, source,
  security/entity mapping, timestamps, entitlement, and parser version.
- **Evidence Assertion:** a typed fact extracted from an artifact, preserving its evidence reference
  and uncertainty.
- **Attention Subject:** a company, ETF, market theme, or held position moving through the attention
  funnel.
- **Candidate Card:** a compact, cheaply produced summary that explains why a subject deserves or does
  not deserve expensive research.
- **Dossier:** a bounded evidence bundle for one subject, including contradicting and missing evidence.
- **Belief Event:** an append-only transition to a typed claim, including confidence, valid interval,
  transaction time, evidence, falsifiers, and status.
- **Belief Graph:** a rebuildable as-of projection of relevant entities, evidence, beliefs,
  contradictions, catalysts, theses, decisions, and lessons.
- **Thesis:** a causal, horizon-bound investment argument with expectation gap, scenarios, catalyst,
  invalidators, and supporting/contradicting evidence.
- **Forecast Record:** an immutable ex-ante prediction and its resolution rule.
- **HouseView:** the CIO's stance over researched subjects before risk-profile sizing.
- **Target Band:** an allowed lower and upper portfolio weight around a deterministic target.
- **DecisionPacket:** the final risk-clamped, versioned, expiring artifact accepted by execution.
- **ExecutionReceipt:** broker submissions, changes, rejections, fills, quotes, timestamps, and
  divergence from the packet.
- **OutcomeObservation:** an append-only market, forecast, thesis, or execution result recorded only
  after its horizon becomes due.
- **Journal Lesson:** a bounded, expiring hypothesis about research or attention behavior derived from
  multiple resolved decisions.
- **Champion:** the production-approved version of a prompt, workflow, model, rule, or portfolio
  policy.
- **Challenger:** one prospectively evaluated variation from the champion.

### 8. Data sources, entitlement, and provenance

**Alpaca responsibilities**

- Asset identity, exchange, active/tradable state, account state, positions, buying power, orders, and
  order events.
- Historical and current bars, trades, quotes, market status, LULD/status information, clock, and
  calendar under the account's entitlement.
- Alpaca/Benzinga news as the v0 structured news source.
- Alpaca paper trading as the sole broker execution target.

**Keyless primary public sources**

- SEC EDGAR filings, submissions, and filing facts, captured prospectively with accession and exact
  acceptance time.
- Issuer investor-relations releases and filings when directly relevant to a dossier.
- Federal Reserve, BLS, and BEA official schedules and releases for known macro events.

**Explicit absences**

- Alpaca exposes no documented fundamentals endpoint, forward analyst estimates, consensus forecast,
  or complete structured earnings/macro calendar.
- Current SEC Company Facts or frames are convenient aggregates, not immutable point-in-time
  snapshots. They may include later amendments or recasts and must not be queried retrospectively as
  if they were historical truth.
- Alpaca news does not document immutable historical versions or original receipt time. V0 creates
  its own prospective receipt record from the moment the system begins capture.
- Social media is excluded. “Retail view” is Unknown unless supported by an allowed observed source;
  inferred price/news attention is labeled InferredPerception.
- “Wall Street view” is ObservedView only when an allowed artifact directly supports it. Without
  consensus data, the system must often use InferredPerception or Unknown.
- No free, canonical, machine-readable point-in-time S&P 500 or S&P MidCap 400 membership feed is
  assumed. Mutable SPY or MDY holdings are not treated as index truth.

Every Evidence Artifact records, where applicable:

- source event/effective time;
- source publication or filing-acceptance time;
- first observed/ingested time;
- derived availability time used by the as-of engine;
- content hash and retrieval identifier;
- source entitlement or feed (`IEX`, `SIP`, public keyless, or other approved flat plan);
- parser and normalization version; and
- entity mapping confidence.

Data upgrades create new regimes. Alpaca Basic's live IEX coverage is acceptable for v0 paper
research with its limitation stated in every evaluation. A later flat SIP subscription is allowed by
the subscription-only rule but cannot be mixed into the existing evaluation without a new data-regime
version. No metered API fallback exists.

### 9. Universe and attention funnel

- The universe starts from Alpaca assets that are active, tradable, US-listed common stocks on major
  exchanges, plus a curated ETF allowlist. OTC, inactive, non-tradable, leveraged, inverse, and
  structurally unsuitable instruments are excluded.
- Before the first formal paper session, a one-time mechanics calibration sweeps only price, median
  dollar-volume, and history-length thresholds. It selects the least restrictive set that remains
  operationally liquid and supports the local scan and dossier caps. It does not optimize historical
  return. The selected values and resulting universe rule are frozen and hashed for the baseline
  regime.
- The coverage objective is broad large- and mid-cap exposure, not official index replication.
- The daily zero-token scan uses freshness, price/liquidity change, gap, unusual volume, news arrival,
  filing arrival, known event proximity, existing beliefs, thesis expiry, and portfolio ownership.
  These are attention features, not alpha scores.
- Attention moves through `Observed → Watch → Candidate → Dossier → Thesis → Portfolio`.
  `Rejected`, `Refuted`, `Dormant`, and `Archived` retain why the subject left the active path.
- Deterministic promoters and demoters normally move a subject one state at a time. Holdings and
  urgent risk events may bypass the normal research priority but not the evidence, skeptic, scenario,
  or risk gates.
- Each normal daily cycle creates at most twenty Candidate Cards, opens at most five new Dossiers, and
  refreshes every current holding whose evidence or thesis is due.
- Ten to twenty percent of the weekly dossier budget is randomly or diversity-stratified across
  otherwise eligible subjects. Exploration may spend research tokens but cannot place orders without
  the complete champion workflow.
- Token usage, turns, candidates, dossiers, holdings, and quota errors are recorded per cycle.

### 10. Research workflow and contextual lenses

The logical workflow is fixed, although token-efficient batching is allowed. The independent Skeptic
must always run in a fresh context.

1. **Evidence Collector:** builds the dossier, separates observed facts from interpretations, checks
   freshness, highlights missing data, and returns only evidence-referenced assertions.
2. **Thesis Builder:** states the apparent expectation, variant view, causal path, catalyst, horizon,
   scenarios, invalidators, and what would make the subject uninvestable.
3. **Independent Skeptic:** receives the dossier and proposed thesis in a clean context, constructs the
   strongest countercase, seeks contradictions and base-rate errors, and may recommend rejection or
   further evidence.
4. **Scenario Forecaster:** creates bull/base/bear cases, observable resolution criteria, probability
   estimates for later calibration, downside paths, and the expected time at which the forecast can
   be judged.
5. **CIO:** applies the Constitution, resolves the thesis-versus-skeptic dispute, and emits `long`,
   `hold`, `reduce`, `exit`, or `abstain`. It records conviction and uncertainty for calibration but
   emits no position weight.

All five roles consider six contextual lenses:

1. information and sentiment;
2. growth and change in growth expectations;
3. quality, resilience, and accounting/business-model risk;
4. valuation and expectations embedded in price;
5. market behavior, liquidity, positioning proxies, and price response; and
6. catalyst timing, known events, and downside risk.

These lenses are context and risk evidence, not a fixed factor score. A dossier may emphasize only the
relevant lenses but must state which were omitted and why.

Guardrails operate at three layers:

- reasoning instructions require the Constitution, workflow, inversion, and explicit Unknown values;
- typed schemas reject missing fields, unsupported enum values, absent evidence references, or
  inconsistent horizons; and
- deterministic validators independently check content hashes, entity mapping, timestamps,
  availability, source support, constraints, and allowed transitions.

Each Codex process is stateless. It receives only the pinned Constitution, bounded relevant graph,
current portfolio state, candidate/dossier evidence, applicable journal lessons, and task schema.
Model name/version, reasoning configuration, prompt hash, tool set, source cutoff, and input artifact
hashes are recorded. Base-model parametric memory is never treated as financial evidence.

### 11. Evidence Vault, Belief Ledger, Belief Graph, and Decision Journal

**Evidence Vault**

- Stores immutable raw or normalized artifacts by content hash.
- Deduplicates identical content without discarding repeated observation times.
- Keeps extraction and entity-mapping results versioned rather than mutating the source.

**Belief Ledger**

- Uses append-only bitemporal events in SQLite as the source of truth.
- Records when a claim is valid in the modeled world and when the system learned or changed it.
- Supports active, weakened, contradicted, superseded, refuted, expired, dormant, and archived states.
- Never deletes the prior confidence, evidence, or rationale when a belief changes.

**Belief Graph**

- Is a replaceable, bounded as-of projection, not a second source of truth.
- Connects companies, people, products, sectors, events, artifacts, assertions, beliefs, theses,
  catalysts, invalidators, decisions, outcomes, and lessons.
- Includes explicit supports, contradicts, supersedes, caused-by, exposed-to, and resolved-by edges.
- Retrieves the smallest relevant subgraph for the current role and horizon.
- Rebuilds deterministically from the Evidence Vault and Belief Ledger after corruption or schema
  migration.

**Decision Journal**

- `DecisionRecord` freezes the ex-ante HouseView, evidence cutoff, thesis, scenarios, forecast,
  invalidators, target bands, benchmark state, and all configuration fingerprints.
- `OutcomeObservation` appends market, thesis, forecast, execution, and benchmark outcomes only when
  the declared horizon is due.
- `AgentAttribution` is an untrusted, evidence-linked analysis of thesis quality, sizing, execution,
  surprise, and luck. A clean-context skeptic challenges it.
- `OperatorNote` is Sam's separate annotation. It never becomes source evidence automatically.

A single outcome may create only a provisional LessonCandidate. A lesson can become active only after
at least three independent resolved decisions support the same typed pattern, no unresolved outcome
directly contradicts it, and a clean-context skeptic accepts the generalization. An active lesson:

- expires after twenty market sessions unless independently reinforced;
- may change retrieval priority or move an Attention Subject by at most one state;
- cannot change stance directly, position size, risk limits, execution, Constitution, or evaluator;
  and
- remains versioned and reversible through new ledger events.

### 12. Portfolio construction and risk profiles

The CIO supplies eligibility and stance, not weights. Portfolio construction is deterministic and
versioned.

**V0 champion: Capped Risk Budget**

- Accepted long positions receive equal standalone risk budgets.
- Raw notional is proportional to inverse realized volatility, subject to a volatility floor and
  stale/insufficient-history rejection.
- The volatility estimator, lookback, floor, price-adjustment convention, and rebalance bands are
  selected during pre-paper mechanics calibration and frozen before the baseline begins. They are not
  selected on investment return.
- Constitution-defined uncertainty, event risk, evidence incompleteness, and downside conditions may
  only reduce or reject a raw allocation. LLM conviction and self-reported scenario probability cannot
  increase v0 size.
- Position, sector, liquidity, gross-exposure, and common-cause/correlation clamps then apply.
- The allocator does not force gross exposure or redistribute every capped remainder. Unused capital
  remains cash.

**V0 comparator: Capped Equal Weight**

- Runs locally on the identical HouseView, constraints, timing, and standardized cost model.
- Never reaches Alpaca.
- Is evaluated to isolate whether risk budgeting adds value beyond selection.

**Initial risk envelopes**

| Profile      | Maximum gross exposure | Maximum name weight | Maximum sector weight | Execution             |
| ------------ | ---------------------: | ------------------: | --------------------: | --------------------- |
| Conservative |                    60% |                  5% |                   20% | Internal shadow       |
| Balanced     |                    80% |                  8% |                   25% | Alpaca paper champion |
| Growth       |                   100% |                 12% |                   30% | Internal shadow       |

- The risk kernel seeks fifteen to twenty positions but does not weaken admission criteria to reach
  that count.
- Risk-profile transformations use the same HouseView, evidence cutoff, and security eligibility.
- Shadow portfolios use the same available-at-time prices and standardized cost overlay as the
  champion evaluation.

**Black–Litterman and other optimizers**

- Standard Black–Litterman is not the v0 champion. Its equilibrium prior is not the Belief Graph's
  epistemic prior.
- A later Black–Litterman challenger requires a reproducible benchmark/market-cap prior,
  horizon-consistent regularized covariance, numeric view returns, forward-calibrated view-error
  covariance including correlated LLM errors, fixed risk-aversion and uncertainty conventions,
  transaction-cost-aware rebalancing, and prospective shadow evidence.
- Classical mean-variance, minimum-variance, equal-risk-contribution, hierarchical risk, or other
  construction methods may enter only as one-change shadow challengers behind the same interface.

### 13. Rebalancing and paper execution

- Daily research produces target bands, not a requirement to trade to exact weights every day.
- A trade is eligible when a thesis enters, exits, expires, or is invalidated; a hard risk constraint
  is breached; or the current weight moves outside its no-trade band by more than the minimum
  executable notional.
- Normal drift trades move to the nearest band boundary or partially toward the target. They do not
  automatically chase the frictionless point estimate.
- There is no forced weekly portfolio rebalance. Weekly processing reconciles stale state,
  configurations, upcoming events, and long-dormant beliefs.
- Known material company or macro releases block a new pre-event binary position. Entry may be
  reconsidered only after the public release is captured and a fresh complete cycle finishes.
- The pre-open DecisionPacket is frozen fifteen minutes before the regular session opens.
- Five minutes after the open, execution rechecks live quote, spread, opening gap, status/LULD,
  account, positions, pending orders, buying power, packet expiry, and newly captured evidence.
- A deterministic, versioned material-gap or surprise rule cancels affected stale entries and permits
  one additional complete operating-system cycle. It never permits a CIO-only or sizing-only rerun.
- If that complete cycle does not publish a newly validated packet before the thirty-minute execution
  window closes, the affected entry is skipped for the session. Existing-position risk actions remain
  governed by the preauthorized deterministic exit policy.
- Normal entry and rebalance orders are whole-share, regular-session, day-limit orders submitted
  between five and thirty minutes after the open.
- V0 permits at most one bounded cancel-and-reprice per order. The replacement remains inside the
  Constitution-owned price band and original risk envelope.
- Partial fills are reconciled before replacement. Idempotent client order IDs bind every broker order
  to one packet, symbol, side, and attempt.
- Risk exits may use a more aggressive but still bounded limit policy. Market, stop, stop-limit,
  trailing-stop, bracket, extended-hours, auction, IOC, and FOK orders are excluded from v0.
- The deterministic hot path listens to order events during the execution window and can cancel or
  reconcile orders. It cannot invoke Codex or alter target direction.

### 14. Evaluation and benchmark contract

Evaluation separates three questions.

**Forecast quality**

- Direction, scenario resolution, catalyst timing, probability calibration, invalidation accuracy,
  and abstention quality.
- Proper scoring rules are calculated only for forecasts with defined mutually exclusive outcomes and
  immutable ex-ante probabilities.

**Portfolio quality**

- Net return, SPY excess return, Sharpe ratio, information ratio, maximum drawdown, concentration,
  turnover, modeled costs, execution divergence, cash exposure, and constraint adherence.
- The Balanced paper portfolio is compared with full SPY buy-and-hold as the official objective.
- Exposure-matched SPY-plus-cash is a diagnostic for Conservative, Balanced, and Growth profiles; it
  does not replace the full-SPY must-beat goal.
- Alpaca paper fills are recorded, but paper execution is not assumed realistic. Evaluation also
  applies a frozen conservative cost overlay using observed quotes, spread proxies, turnover, and
  latency. Paper and modeled-net results are reported separately.

**Process quality**

- Evidence coverage, unsupported-claim rate, stale-belief use, contradiction handling, skeptic impact,
  invalid schema rate, abstention discipline, candidate-funnel recall diagnostics, token consumption,
  and invariant failures.

No single metric governs promotion. Sharpe is required but cannot override a material regression in
drawdown, calibration, costs, turnover, concentration, or constraints.

The initial champion configuration is frozen for the first formal baseline:

- At sixty completed market sessions and fifty resolved forecasts, the system may be assessed for a
  separately designed, human-approved, tightly capped micro-live pilot only if the Balanced portfolio
  has beaten SPY net of modeled costs and all risk/process gates pass.
- This threshold is an eligibility gate, not statistical proof.
- No scaling or validity claim is allowed before at least 252 completed sessions and observations from
  multiple materially different market regimes.
- The paper/live gap—market impact, queue priority, fills, outages, and behavioral pressure—must be
  stated in every readiness assessment.

### 15. Controlled self-evolution

Self-evolution means controlled changes to beliefs, retrieval, attention, and prospectively tested
policies. It does not mean autonomous rewriting of code, prompts, objectives, or the judge.

**May evolve autonomously within fixed schemas**

- belief confidence, status, evidence links, contradiction state, and expiry;
- Attention Subject transitions under deterministic rules;
- evidence-source usefulness statistics and retrieval priority;
- provisional and bounded Journal Lessons; and
- allocation of the fixed research exploration budget.

**May be proposed by the agent but must run as a shadow challenger**

- promoter or demoter rules;
- retrieval or dossier composition rules;
- role prompts and reasoning instructions below the Constitution;
- workflow routing or role packing;
- Codex model/configuration changes;
- portfolio-construction policy; and
- lesson activation or expiry policy beyond the v0 bounds.

**Always human-controlled**

- Constitution and investment objective;
- benchmark and evaluation metrics;
- schemas, evidence standards, and as-of semantics;
- risk envelopes and sizing authority;
- execution policy and broker access;
- model migration into the champion;
- promotion gates; and
- any live-capital decision.

Each challenger:

- changes exactly one declared variable;
- starts from a preregistered hypothesis, comparison, metrics, minimum prospective observation window,
  and stop conditions;
- receives identical evidence cutoffs and portfolio state where the comparison requires it;
- records full model, prompt, tool, data, Constitution, and policy fingerprints;
- cannot be promoted during the initial sixty-session/fifty-forecast baseline; and
- requires better net return or information ratio without material regression in drawdown,
  calibration, turnover, cost, stability, or invariants, followed by Sam's explicit approval.

Reasonable input perturbations must not reverse the apparent benefit. A challenger that wins only at
one threshold or through higher hidden turnover is rejected.

### 16. Daily operating lifecycle

All schedules are expressed relative to the NYSE calendar and regular-session open.

1. Reconcile prior orders and positions; append broker observations and any due forecast/thesis
   outcomes.
2. Pin Constitution, champion policies, data entitlement, model configuration, and evidence cutoff.
3. Snapshot the eligible universe and run the zero-token attention scan.
4. Incrementally capture Alpaca market/news evidence and relevant SEC, issuer, and official-calendar
   evidence.
5. Build Candidate Cards, open bounded Dossiers, and refresh all due holdings.
6. Run Evidence Collector and Thesis Builder stages.
7. Run the independent Skeptic in a clean context.
8. Run Scenario Forecaster and CIO, validate all outputs, and produce the HouseView.
9. Append accepted evidence assertions and belief transitions; rebuild or increment the Belief Graph
   projection.
10. Construct champion and shadow target bands; run deterministic risk and invariant checks.
11. Append the immutable DecisionRecord and publish an expiring DecisionPacket only if approved or
    safely clamped.
12. At the post-open recheck, execute, cancel, or trigger the one allowed full re-evaluation according
    to deterministic market-state rules.
13. Reconcile fills and publish the digest, liveness stamp, token use, and failure state.

The Mac scheduler uses a scheduled wake, launchd, and a scoped process keep-awake mechanism. A missed
run resumes the next safe lifecycle phase; it does not synthesize a historical decision after seeing
future prices.

### 17. Failure model and security boundary

Expected no-action outcomes include non-trading day, already-complete session, insufficient attention,
no investable thesis, and deterministic risk rejection.

The following conditions fail closed for new discretionary orders:

- Alpaca or required primary evidence unavailable beyond the freshness policy;
- stale, incomplete, or contradictory required market state;
- Codex timeout, quota exhaustion, invalid structured output, or missing independent Skeptic;
- invented, unsupported, future, or unavailable-at-cutoff evidence reference;
- Constitution or champion change detected during a run;
- invalid or expired DecisionPacket;
- graph projection unable to rebuild from the ledger;
- ledger or journal integrity failure;
- account, position, buying-power, quote, status, halt/LULD, or pending-order mismatch;
- persistence failure before packet publication; or
- execution receipt that cannot be reconciled idempotently.

Completed evidence ingestion and due OutcomeObservations may remain committed after a later failure.
A partial DecisionRecord or executable packet may not.

Credential and process boundaries are explicit:

- Codex receives curated evidence artifacts and schemas only. Its environment contains no Alpaca
  trading credential and no metered API key.
- Market-data collection and order execution run through harness-owned adapters.
- The executor runs in a restricted environment with broker credentials and no Codex capability.
- DecisionPackets are immutable, hashed, Constitution-bound, account-bound, session-bound, and
  expiring.
- Reports, graph projections, and Markdown are outputs, not executable instructions.
- Web pages, filings, and news are untrusted data. Text within them cannot change workflow, tool use,
  risk, or memory policy.

### 18. Operator surfaces and reporting

- Normal operator surfaces are lifecycle run/status/reconcile/govern operations and the daily digest.
- The digest reports portfolio and benchmark results, new/changed theses, strongest skeptic objections,
  decision changes, target-band actions, fills, costs, forecast resolutions, lesson candidates,
  challenger state, token usage, data entitlement, and failures.
- Detailed artifacts remain addressable by stable identifiers from the digest without expanding the
  routine output.
- Markdown and static HTML are sufficient for v0. There is no dashboard or interactive application.
- A liveness warning surfaces after a missed eligible market session. No second autonomous monitor
  loop is introduced; the warning terminates in Sam's existing Second Brain routine.

## Testing Decisions

### Testing seams

The highest primary seam is the Investment Operating System lifecycle. Most acceptance tests call
Advance, Record, or Govern against recorded external adapters and then inspect receipts, append-only
records, projections, packets, and reports. Tests do not call private research stages directly.

The Order Execution Module is the second necessary seam because it has a separate credential,
availability, timing, and safety boundary. It is tested with a recorded Alpaca broker adapter and
synthetic order-event streams.

Research Lab isolation is tested as a boundary property: arbitrary stage replay may create lab
artifacts but can never change champion state or produce a packet accepted by execution.

### Test principles

- Assert observable domain behavior, durable events, receipts, and invariants rather than internal
  class layout or exact LLM wording.
- Use fixed clocks, exchange calendars, content hashes, deterministic identifiers, and recorded
  adapters so failures are reproducible.
- Validate LLM output for evidence support, schema, role separation, and prohibited authority. Do not
  snapshot prose style.
- Test as-of availability, not merely event timestamps.
- Treat every external text artifact as adversarial input.
- Keep live network and Alpaca paper tests separate from the fast deterministic suite.

### Required deterministic tests

- Lifecycle idempotency across every checkpoint, including process termination immediately before and
  after each durable write.
- Exactly one champion DecisionRecord and at most one active packet per market session.
- Repeated Record calls do not duplicate OutcomeObservations.
- Constitution, model, prompt, tool, policy, data, and source hashes remain pinned within a run.
- Evidence unavailable at the cutoff is rejected even when its source-event time is earlier.
- SEC amendments and restatements cannot appear in a prior as-of graph.
- Belief transitions preserve prior values and contradiction history.
- Belief Graph rebuild produces the same as-of projection from the ledger.
- A corrupted graph projection rebuilds; a corrupted ledger fails closed.
- Attention caps, holding refresh, and weekly exploration budgets remain invariant under universe size.
- No Research Lab artifact can enter champion stores or the execution boundary.
- Codex output containing a weight, order, unsupported citation, prompt injection, or invalid enum is
  rejected or stripped according to the schema contract.
- Risk construction respects gross, name, sector, liquidity, correlation, cash, whole-share, and
  no-leverage rules under randomized portfolios.
- The inverse-volatility champion never increases size from LLM confidence and handles volatility
  floors, missing history, and caps deterministically.
- Equal-weight and risk-budget shadows consume the same HouseView and evidence cutoff.
- Target-band logic avoids trades inside the band and moves only according to the partial-adjustment
  rule outside it.
- Gap, event, halt/LULD, quote, buying-power, stale packet, and pending-order conditions block or reroute
  orders as specified.
- Client order identifiers prevent duplicate exposure after timeout, retry, or partial fill.
- Only Balanced champion packets are accepted by the Alpaca paper executor.
- Forecast and benchmark outcomes resolve only after their declared horizon.
- Cost, turnover, Sharpe, information ratio, drawdown, calibration, and constraint metrics are correct
  on hand-calculated fixtures.
- A single journal outcome cannot activate a lesson; activation, bounded influence, expiry, and
  contradiction behave as specified.
- A challenger cannot alter more than one declared variable or take effect without governance
  approval.
- Quota exhaustion never activates a metered fallback and cannot publish new discretionary orders.

### Contract and fault-injection tests

- Recorded Alpaca market-data and trading responses cover normal, stale, unavailable, partial,
  rejected, canceled, and out-of-order events.
- SEC and issuer fixtures cover filing acceptance times, amendments, duplicate content, changed entity
  mappings, and hostile embedded instructions.
- Scripted Codex fixtures cover valid output, hallucinated citations, missing skeptic, contradictory
  schemas, long output, timeout, and quota exhaustion.
- Scheduler tests cover weekends, exchange holidays, early closes, daylight-saving transitions,
  machine sleep/wake, late starts, and resume behavior.
- Persistence fault injection covers disk-full, transaction rollback, write interruption, duplicate
  event delivery, and projection corruption.

### Forward acceptance

- A full end-to-end Alpaca paper rehearsal must pass before the formal sixty-session clock begins.
- Baseline configuration and all calibration-only parameters are frozen and hashed at that boundary.
- Formal sessions with invalid data or incomplete lifecycle state are recorded as invalid sessions, not
  silently removed or backfilled.
- The existing traditional-quant engine supplies prior art for append-only ledgers, negative controls,
  metrics, and deterministic gates, but its tests and runtime are not imported as dependencies.

## Out of Scope

- Live capital, even at micro size; that requires a separate approved design after the forward gate.
- Public model portfolios, copy trading, personalized advice, client accounts, subscriptions,
  marketing, track-record distribution, AFSL/AR analysis, or other regulated distribution.
- Short selling, leverage, options, futures, crypto, pairs, market-neutral, or intraday strategies.
- Social-media ingestion or paid sentiment/consensus feeds.
- Any metered API, including metered LLM, search, market-data, fundamentals, or news services.
- Canonical point-in-time S&P 500 or S&P MidCap 400 replication.
- A complete structured company-earnings or analyst-estimates calendar.
- Treating Alpaca/SEC current aggregates as historical point-in-time truth.
- Traditional factor mining, genetic strategy search, or repeated strict backtest optimization as the
  alpha engine.
- Black–Litterman, mean-variance, risk parity, hierarchical risk parity, Kelly sizing, or CVaR as the
  v0 champion.
- Full order-type exploitation, market orders, stop/trailing/bracket orders, extended hours, auctions,
  IOC, or FOK.
- Arbitrary production execution of Evidence Collector, Thesis Builder, Skeptic, Scenario Forecaster,
  CIO, graph update, sizing, or journal-learning stages.
- Autonomous prompt rewriting, code generation into production, Constitution changes, evaluator
  changes, risk relaxation, broker-policy changes, or champion promotion.
- LangGraph in v0. It is a later internal orchestration substitution.
- A web dashboard, mobile application, notification platform, or multi-user permissions system.
- Modifying or extending the existing AI Hedge OS or `evolving_trading` implementation as part of this
  build.

## Further Notes

### Adversarial conclusion

The original reasoning contains a useful hypothesis but an unsafe implied leap. Markets being dynamic
and alpha decaying quickly does not make an agentic system superior by default. Agentic AI can update
its explicit state and investigate new information, but it can also chase noise faster, accumulate
confirmation bias, and optimize against its own journal. The design therefore treats the harness,
authority model, provenance, abstention, and prospective evaluator as the main product. Alpha is an
outcome to test.

The traditional-versus-agentic framing is also not binary. V0 still depends on price/liquidity
statistics, contextual growth/quality/valuation evidence, deterministic sizing, and quantitative
evaluation. What changes is where hypotheses originate and how they evolve—not the need for
mathematical discipline.

### Black–Litterman conclusion

Weighting matters because it controls concentration, drawdown, turnover, and how much a correct or
incorrect thesis affects the portfolio. It does not follow that a complex optimizer improves v0.
Standard Black–Litterman relocates judgment into a market-equilibrium prior, numeric view returns,
view uncertainty, covariance, risk aversion, and constraints. A qualitative LLM thesis and its verbal
confidence do not yet supply those inputs. Capped inverse-volatility risk budgeting is therefore the
transparent champion, capped equal weight is the comparator, and Black–Litterman is a later calibrated
challenger.

### Evidence record

The design is informed by the following Second Brain research notes:

- [Agentic AI portfolio evidence](../../research/2026-08-16-agentic-ai-portfolio-evidence.md)
- [Regulatory and memory boundaries](../../research/2026-08-17-ai-copy-trading-regulatory-memory.md)
- [Alpaca execution, gating, and event data](../../research/2026-08-17-alpaca-execution-gating-data.md)
- [Contextual lenses and data boundaries](../../research/2026-08-17-contextual-lens-data-boundary.md)
- [Black–Litterman versus deterministic v0 sizing](../../research/2026-08-17-black-litterman-v0-sizing-evidence.md)

These notes support constraints and adjacent evidence; none proves that the proposed system has alpha.

### Implementation sequencing

This specification is intentionally complete at the solution level. The implementation repository
should decompose it into independently verifiable slices in this order:

1. domain events, SQLite ledgers, projections, lifecycle checkpoints, and invariants;
2. recorded adapters, Evidence Vault, as-of provenance, universe, and attention funnel;
3. Codex role contracts, validation, Constitution, and Research Lab isolation;
4. HouseView, deterministic portfolio construction, target bands, and shadow accounting;
5. Alpaca paper executor, reconciliation, scheduler, and operator digest; and
6. forecast resolution, evaluation, Journal Lessons, and champion–challenger governance.

No slice may weaken the authority boundary to make a demo easier.

### Priority constraint

Sam's recorded priorities are more than two weeks old, but the available evidence still supports the
same provisional ordering: succeeding in the first ninety days at Savvyloans is the top priority, and
investing side projects remain maintenance-only. V0 should therefore be paused or narrowed if it
requires routine manual research, daily recovery, or an additional paid/metered operating burden.
