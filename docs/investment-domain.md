# Investment domain

This document is the source of truth for the investment system's stable decision rules. It owns what
evidence, research, memory, portfolio construction, execution policy, evaluation, and controlled
learning mean. `CONTEXT.md` owns canonical terms; `architecture.md` owns where modules and process
seams implement these rules; `config-catalog.md` owns implemented tunable values.

## Mandate and proof standard

V0 tests whether evidence-bound, event-sensitive research can produce a better prospective HouseView
than its benchmark. It does not assume that language-model reasoning produces alpha.

- Personal, single-operator US equity research and Alpaca paper trading only.
- US-listed common stocks plus a small curated allowlist of liquid, unlevered ETFs.
- Long-only, unlevered, daily decisions; cash is a valid allocation.
- Intended thesis horizons are one to twenty trading days.
- Balanced is the only executable paper Risk Profile. Conservative and Growth are internal shadows
  generated from the same HouseView.
- SPY buy-and-hold is the official external benchmark.
- Forward paper outcomes are the primary proof. Historical replay establishes mechanics, leakage
  resistance, failure behavior, and cost sensitivity, not investment validity.
- “No demonstrated edge” is a valid terminal conclusion.

## Investment Constitution

The operator owns the short, versioned Constitution. Every decision-relevant model context receives
the pinned version, and each run records its content hash. Amendments start a new regime at a future
Market Session boundary.

1. Invest against expectations, not merely good or bad company news.
2. State the market's apparent expectation and the variant view.
3. Use scenarios rather than one precise forecast.
4. Explain the causal chain from evidence to business or market impact.
5. Name the catalyst, expected horizon, and observable resolution.
6. State invalidators before taking risk.
7. Invert the thesis and examine permanent-loss paths.
8. Stay within the evidence-backed circle of competence; otherwise abstain.
9. Prefer asymmetric payoff with survivable downside.
10. Respect liquidity, concentration, common-cause exposure, and correlation.
11. Update beliefs when evidence changes while preserving the prior record.
12. Separate process quality from outcome luck and never force activity.

These twelve clauses are the immutable version-1 baseline. A later artifact may change their content
only through operator-approved `Govern`: its declared hash and approval proof bind a stable request
identity to one exact future Market Session. A pending version does not affect an earlier or
in-progress run. Activation at the eligible boundary starts a new regime without rewriting the
baseline or any superseded version, and Advance pins the selected version and hash before model work.

## Decision flow

```mermaid
flowchart LR
    artifact[Evidence Artifact] --> assertion[Evidence Assertion]
    assertion --> dossier[Dossier]
    dossier --> thesis[Thesis]
    thesis --> forecast[Forecast Record]
    forecast --> house[HouseView]
    house --> sizing[Deterministic Risk Profile]
    sizing --> band[Target Band]
    band --> packet[DecisionPacket]
    packet --> receipt[Execution Receipt]
    receipt --> outcome[Outcome Observation]
    outcome --> evaluation[Evaluation]

    assertion --> belief[Belief Event]
    belief --> graph[Belief Graph]
    graph --> dossier
    evaluation --> lesson[Journal Lesson]
    lesson -. bounded retrieval or attention influence .-> dossier
```

The model may contribute through HouseView. Deterministic code owns sizing, risk clamps, packet
construction, broker actions, reconciliation, and evaluation arithmetic.

## Evidence and as-of truth

Allowed V0 sources are Alpaca market, asset, account, order, and news data under the account's
entitlement; SEC EDGAR filings and submissions; issuer investor-relations releases; and official
Federal Reserve, BLS, and BEA schedules or releases. A later flat subscription creates a new Data
Regime. No metered fallback is permitted.

Alpaca Basic IEX coverage is acceptable for V0 paper research only when every evaluation names the
active Data Regime and its coverage limitations. A later flat SIP entitlement starts a new regime and
cannot be mixed silently into baseline evidence.

Every Evidence Artifact retains, when applicable:

- source event or effective time;
- publication or filing-acceptance time;
- first-observed or ingestion time;
- derived availability time used by as-of reads;
- retrieval identity, observation-specific artifact identity, and source-content hash;
- source entitlement or feed;
- parser and normalization version; and
- entity-mapping confidence, mapping version, and mapping availability.

Times that identify a moment follow the architecture's
[Absolute Instant contract](architecture.md#temporal-semantics); a source-calendar date remains a
date and does not acquire a synthetic midnight instant.

Availability at the pinned Evidence Cutoff determines whether evidence may enter a decision. A source
event date alone is insufficient. SEC artifacts are keyed by accession and acceptance time; current
aggregates, later amendments, or restatements cannot leak into prior contexts. Repeated capture of
identical content retains each observation time and artifact identity while sharing immutable content
bytes.

Absent data remains absent:

- Retail view is `Unknown` without an allowed observed source; price or news attention may support only
  `InferredPerception`.
- Wall Street view is `ObservedView` only when an allowed artifact directly supports it; otherwise it
  is `InferredPerception` or `Unknown`.
- Model memory is never financial evidence.
- External text is evidence data and cannot change tools, lifecycle control, risk, or memory policy.

## Universe and attention

The eligible universe begins with active, tradable, US-listed common stocks on major exchanges and a
curated ETF allowlist. OTC, inactive, non-tradable, leveraged, inverse, and structurally unsuitable
instruments are excluded. The goal is broad large- and mid-cap coverage, not official index
replication.

Before formal paper evaluation, mechanics-only calibration chooses the least restrictive operational
price, median-dollar-volume, and history thresholds. It cannot optimize investment return. The
resulting universe rule is then versioned and frozen for the baseline Data Regime.

The local, zero-token attention scan considers freshness, price and liquidity change, gaps, unusual
volume, news and filing arrivals, known-event proximity, existing beliefs, thesis expiry, and current
holdings. These are attention features, not alpha scores.

Attention normally moves one state at a time:

```text
Observed -> Watch -> Candidate -> Dossier -> Thesis -> Portfolio
```

`Rejected`, `Refuted`, `Dormant`, and `Archived` preserve why a subject left the active path. Holdings
and urgent risk events may receive priority but cannot bypass evidence, skeptic, scenario, or risk
gates.

Each normal cycle produces at most twenty Candidate Cards, opens at most five new Dossiers, and
refreshes every holding whose evidence or Thesis is due. Ten to twenty percent of the weekly dossier
budget explores otherwise eligible subjects using random or diversity-stratified selection.
Exploration may spend research capacity but has no shortcut to execution.
Each cycle records token use, turns, Candidate Cards, Dossiers, holding refreshes, and quota errors.

## Research workflow

The logical role order is fixed; batching may reduce cost without weakening role separation.

1. **Evidence Collector** builds a bounded Dossier, separates facts from interpretation, checks
   freshness, names missing data, and emits evidence-referenced assertions.
2. **Thesis Builder** states apparent expectations, variant view, causal path, catalyst, horizon,
   scenarios, invalidators, and uninvestable conditions.
3. **Independent Skeptic** receives the Dossier and Thesis in a clean context, constructs the strongest
   countercase, checks contradictions and base rates, and may reject or request evidence.
4. **Scenario Forecaster** produces bull, base, and bear cases with observable resolutions,
   probabilities where valid, downside paths, and evaluation horizons. A resolution may use only an
   allowed observation available no later than its Thesis horizon; a later release never resolves the
   forecast.
5. **CIO** applies the Constitution, resolves the Thesis and Skeptic disagreement, and emits `long`,
   `hold`, `reduce`, `exit`, or `abstain` with uncertainty but no position weight.

Each role considers information and sentiment; growth and expectation change; quality and resilience;
valuation and embedded expectations; market behavior and liquidity; and catalyst timing and downside.
A Dossier may omit an irrelevant lens only when it records why.

Each model process is stateless. Model output is untrusted structured input. Validators enforce
schemas, evidence references, entity mapping, relevant times, allowed transitions, and authority.
Each call pins its Constitution, bounded Belief Graph, portfolio context, prompt, model, reasoning
configuration, tools, Evidence Cutoff, and input hashes.

Production `Advance` applies that workflow only to Dossier requests and due holding refreshes named by
the pinned Attention Artifact. Evidence Collector runs during `BuildDossiers`; the remaining roles run
in the fixed order during `RunResearch`, with each role receiving only its declared material. Each
subject input is the exact evidence-artifact set recorded by its Candidate Card or holding refresh;
Stage 3 cannot widen that set. The set must be non-empty, stay within the configured evidence-artifact
bound, and include captured evidence from at least one admitted official source. A Thesis with an
active uninvestable condition is a durable no-valid-Thesis outcome and skips later roles for that
subject. A Skeptic rejection records the Thesis and rejection but skips Scenario Forecaster and CIO.
A request for more evidence fails closed for the current cycle rather than substituting stale or
undeclared material. CIO output remains research: it contains one of the five approved stances and
never a weight, target, order, packet, or broker instruction.

When every researched subject is rejected or every CIO abstains, the cycle records a durable no-action
outcome. Otherwise `UpdateMemory` submits each validated non-abstaining CIO resolution through
`Record`. The resulting active expectation Belief Event preserves the Thesis claim, cited evidence,
invalidators, cutoff, and transition predecessor. Its evidence references distinguish the exact
supporting citations of the Thesis variant view from its exact contradicting citations. CIO uncertainty
maps deterministically to memory confidence: `low` to `0.75`, `medium` to `0.5`, and `high` to `0.25`.
This mapping is a memory-admission representation only; it cannot size a position or authorize
execution. A malformed or refused research result creates no Belief Event, and a refused memory append
prevents lifecycle advancement without deleting prior history.

`UpdateMemory` keeps the research view and append coordination distinct. Research receives only the
bounded Belief Graph at the pinned evidence cutoff; memory admission separately resolves the current
authoritative belief head before naming `transition_from_event_id`. An unavailable or truncated
current history refuses the update instead of treating an existing stream as new.

## Memory and learning

The Evidence Vault stores immutable captured content. The bitemporal Belief Ledger is authoritative
for beliefs; its events preserve modeled valid time, transaction time, confidence, evidence,
falsifiers, and status. Beliefs may become active, weakened, contradicted, superseded, refuted,
expired, dormant, or archived without deleting prior states.

A belief stream begins only with `active`. Every later event must name the current event as
`transition_from_event_id`, preserve the canonical subject and claim kind, and use a transaction time
no earlier than the current event. The allowed status transitions are:

| Current status | Allowed next status |
| --- | --- |
| `active`, `weakened`, `contradicted`, or `dormant` | Any defined belief status |
| `expired` | `active`, `superseded`, or `archived` |
| `refuted` | `superseded` or `archived` |
| `superseded` | `archived` |
| `archived` | None |

When `supersedes_event_id` is present, it must identify an existing event in the same belief stream.
Corrections therefore append a traceable transition; they never replace prior material.
Exact redelivery of an accepted event returns its original durable receipt after authoritative
history validation. The current record clock bounds a new append but cannot invalidate that replay.

The Belief Graph is a bounded as-of projection. It may connect entities, artifacts, assertions,
beliefs, contradictions, catalysts, theses, decisions, outcomes, and lessons, but it never becomes a
second source of truth. Corruption or schema change triggers deterministic rebuild from authoritative
records; corrupted authoritative records fail closed.

The Decision Journal separates:

- the immutable ex-ante Decision Record;
- append-only Outcome Observations recorded when their horizon is due;
- untrusted, evidence-linked Agent Attribution challenged in a clean context; and
- separate Operator Notes that never become evidence automatically.

One outcome can create only a provisional lesson candidate. A Journal Lesson becomes active only
after at least three independent resolved decisions support the typed pattern, no unresolved outcome
directly contradicts it, and a clean-context skeptic accepts it. An active lesson expires after twenty
Market Sessions unless independently reinforced. It may reprioritize retrieval or move one Attention
Subject by at most one state; it cannot directly change stance, size, limits, execution, the
Constitution, or evaluation.

## Portfolio construction

HouseView supplies eligibility and stance, not weights. Portfolio construction is deterministic and
versioned. After `UpdateMemory`, `ConstructPortfolio` creates exactly one immutable HouseView from the
terminal production CIO resolution set. The HouseView binds the Market Session, Evidence Cutoff,
Data Regime, Constitution, runtime configuration, research policy, complete production artifact set,
admitted memory events, universe snapshot, portfolio policy, as-of portfolio inputs, canonical
Instrument Identities, and each source request and resolution hash. Research Lab material and model-
authored weights have no admission path. An empty terminal set is a valid full-cash HouseView.

The V0 champion uses capped inverse-volatility risk budgeting:

- Accepted longs begin with equal standalone risk budgets.
- Raw notional is proportional to inverse realized volatility computed from twenty daily returns over
  twenty-one split-adjusted closes. The estimator is sample standard deviation, annualized over 252
  periods with a 10% floor. Every close must belong to one consecutive regular session under the
  code-pinned `xnys-regular-2026a` calendar. Inputs older than two hours, future-available material,
  duplicate, unordered, gapped, holiday, unsupported-year, or insufficient histories reject sizing.
- All allocation arithmetic runs under the portfolio-owned decimal context: precision 28,
  round-half-even, with invalid operation, division by zero, and overflow trapped. Ambient process
  decimal settings cannot change targets, cash, bands, or their hashes.
- Constitution-defined uncertainty, event risk, incomplete evidence, and downside may reduce or reject
  allocation. Low, medium, and high uncertainty multiply an otherwise identical allocation by 1,
  0.75, and 0.5. Model conviction and self-reported probabilities cannot increase it.
- The 8% name limit and a liquidity limit of 1% of median daily dollar volume apply per instrument.
  Sector, common-cause, and correlation-cluster exposure are each capped at 25%; gross exposure is
  capped at 80%. Each cap scales only the affected accepted weights downward in canonical identity
  order.
- The allocator does not force gross exposure or redistribute capped remainder; unused capital stays
  cash.
- A `reduce` stance begins at half the current weight and remains subject to every clamp. `exit` and
  `abstain` target zero. Missing, stale, contradictory, incomplete, non-production, or altered
  terminal material produces a typed refusal rather than a partial portfolio. Every held asset must
  be one canonical long US-equity position with positive quantity, positive USD valuation, matching
  price and risk inputs, and no mixed-asset companion; otherwise construction fails closed. An
  ineligible existing holding may be preserved or reduced but cannot increase.

A capped equal-weight comparator consumes the identical HouseView, constraints, timing, and frozen
cost model but never reaches Alpaca. Conservative and Growth shadows also consume the same HouseView
and as-of inputs.

The approved baseline risk envelopes are:

| Risk Profile | Maximum gross | Maximum name | Maximum sector | Execution |
| --- | ---: | ---: | ---: | --- |
| Conservative | 60% | 5% | 20% | Internal shadow |
| Balanced | 80% | 8% | 25% | Alpaca paper champion |
| Growth | 100% | 12% | 30% | Internal shadow |

The system seeks fifteen to twenty positions but never weakens admission criteria to reach that
count. When these values become executable configuration, their versioned schema is the runtime
authority and `config-catalog.md` links back to this approved policy.

Black–Litterman is not a V0 champion because the system lacks calibrated numeric return views, view-
error covariance, and a defensible equilibrium prior. It or another optimizer may enter only as a
one-change shadow Challenger behind the same HouseView interface and with prospective evidence.

## Rebalancing and execution policy

Daily research produces Target Bands, not instructions to chase exact weights. The V0 band extends
one percentage point below and above target, clipped at zero and the 8% name limit. A current weight
inside that band creates no trade. Outside the band, a Thesis entry, reduction, or ordinary breach
moves halfway from current weight toward target; a Thesis exit moves to zero. Adjustments below USD
100 remain at current weight. A hard name, liquidity, sector, common-cause, correlation, or gross-risk
breach instead authorizes the full reduction to the already compliant target. There is no forced
weekly rebalance and capped exposure is never redistributed.

The complete known material company and macro release set blocks a new `long` or `hold` position
until every release is captured in the Evidence Vault and cited by the exact fresh terminal research
resolution for its request. Claimed booleans, an earlier cycle, or one cleared event cannot clear
another pending event. An official schedule identifies upcoming risk but does not clear that risk;
only the validated publication subtype can satisfy a macro release. Event risk does not suppress a
deterministic reduction or exit for an existing position. Target Bands retain the target, bounds,
current weight, permitted adjustment, eligibility, and the exact entry, exit, reduction, band,
minimum-notional, event, or hard-risk reason. Weekly maintenance reconciles stale state,
configuration, upcoming events, and long-dormant beliefs without creating a calendar-driven trade
requirement.

The pre-open DecisionPacket freezes fifteen
minutes before the regular session. Five minutes after open, execution rechecks quote freshness,
spread, gap, status or LULD, account, positions, pending orders, buying power, packet expiry, and new
evidence.

A deterministic material-gap or surprise rule cancels stale entries and may request one complete
operating-system cycle. It cannot request only CIO or sizing. If no new packet arrives before thirty
minutes after open, the affected entry is skipped. Existing-position risk actions remain within their
preauthorized deterministic exit policy.

V0 execution permits:

- whole-share, regular-session, day-limit orders between five and thirty minutes after open;
- at most one bounded cancel-and-reprice inside the original price and risk envelope; and
- a more aggressive but still bounded limit for authorized risk exits.

Partial fills are reconciled before replacement. A stable client order identifier binds packet
identity, canonical Instrument Identity, effect kind, side, and attempt; a display symbol never
contributes to that identifier. Market, stop, stop-limit, trailing-stop, bracket, extended-hours,
auction, IOC, and FOK orders are outside V0.

Stale, missing, halted, contradictory, unauthorized, expired, or unreconciled state produces a
durable no-action or fail-closed outcome before any new discretionary order.

## Evaluation

Evaluation keeps three questions separate:

- **Forecast quality:** direction, scenario resolution, catalyst timing, probability calibration,
  invalidation accuracy, and abstention quality. Proper scores require immutable ex-ante probabilities
  over mutually exclusive outcomes.
- **Portfolio quality:** net return, SPY excess return, Sharpe ratio, information ratio, drawdown,
  concentration, turnover, modeled costs, execution divergence, cash exposure, and constraint
  adherence.
- **Process quality:** evidence coverage, unsupported claims, stale-belief use, contradiction handling,
  skeptic impact, invalid schemas, abstention, funnel recall, token consumption, and invariant
  failures.

Balanced compares against full SPY buy-and-hold. Exposure-matched SPY-plus-cash is diagnostic for all
Risk Profiles and cannot replace the official objective. Paper fills and a frozen conservative cost
overlay are reported separately. No single metric governs promotion.

After sixty completed Market Sessions and fifty resolved Forecast Records, a separately designed,
operator-approved, tightly capped micro-live proposal may be considered only when Balanced beats SPY
net of modeled costs and all risk and process gates pass. This is eligibility, not proof. Scaling or a
validity claim requires at least 252 sessions across multiple materially different regimes. Every
readiness assessment states the paper-to-live gap.

## Controlled evolution

Within fixed schemas the system may update belief state, Attention Subject state, evidence-source
usefulness, retrieval priority, provisional bounded Journal Lessons, and allocation of the fixed
exploration budget.

Promoter and demoter rules, Dossier composition, role prompts, workflow routing, model configuration,
portfolio policy, and lesson policy may be proposed only as shadow Challengers. Each Challenger:

- changes exactly one declared variable;
- preregisters a hypothesis, comparison, metrics, prospective observation window, and stop conditions;
- receives equivalent evidence cutoffs and portfolio state where required;
- records model, prompt, tool, data, Constitution, and policy fingerprints;
- remains ineligible for promotion during the initial baseline; and
- requires improved return or information ratio without material regression in drawdown, calibration,
  turnover, cost, stability, or invariants, followed by explicit operator approval.

Reasonable input perturbations must not reverse the apparent benefit. A Challenger that wins only at
one threshold or through hidden turnover is rejected.

Objectives, Constitution, evaluation rules, schemas, as-of semantics, risk envelopes, sizing
authority, execution policy, broker access, model promotion, and any live-capital design remain under
operator control.
