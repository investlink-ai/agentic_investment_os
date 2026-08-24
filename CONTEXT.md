# Domain language

This glossary is the source of truth for investment-system terminology. It contains domain meaning,
not implementation structure, field schemas, or configurable values.

## Time and evidence

- **Absolute Instant:** one unambiguous point on the shared timeline used to order lifecycle,
  evidence, decision, execution, and observation facts.
- **Decision Cycle:** one asset-class-relative interval whose schedule, cutoff, and required activities
  delimit a deterministic operating-system run.
- **Decision Cycle Identity:** the versioned, asset-class-discriminated durable identity of a Decision
  Cycle.
- **Market Session:** the US-equity Decision Cycle for one NYSE trading date and its exchange-relative
  operating lifecycle, represented durably by the `MarketSession` variant of `DecisionCycleIdentity`.
- **Evidence Artifact:** immutable captured source content with identity, provenance, relevant times,
  entitlement, and content hash.
- **Evidence Assertion:** a typed fact extracted from an Evidence Artifact that preserves its source,
  uncertainty, and as-of availability.
- **Evidence Cutoff:** the latest availability time admitted to one decision context.
- **Data Regime:** a versioned combination of feeds, entitlements, and interpretation rules under which
  observations are comparable.

## Instruments and positions

- **Instrument:** a financial security, spot pair, or listed contract that can be observed, held, or
  traded under one asset class's rules.
- **Instrument Identity:** the durable asset-class-discriminated identity of an Instrument. A display
  symbol is only a source alias; a crypto identity includes its pair and venue, while an option identity
  includes its underlying Instrument and expiration.

## Attention and research

- **Attention Subject:** a company, ETF, market theme, or held position moving through the research
  attention funnel.
- **Candidate Card:** a compact, locally produced explanation of why an Attention Subject does or does
  not merit expensive research.
- **Attention Artifact:** the immutable result of one zero-token attention scan, binding Candidate
  Cards, due-holding refreshes, bounded Dossier requests, and resource accounting to pinned inputs.
- **Dossier:** a bounded evidence bundle for one Attention Subject, including contradicting and missing
  evidence.
- **Thesis:** a causal, horizon-bound investment argument containing the apparent expectation, variant
  view, scenarios, catalyst, invalidators, and supporting and contradicting evidence.
- **Forecast Record:** an immutable ex-ante prediction with mutually exclusive outcomes, probabilities
  when applicable, a horizon, and an observable resolution rule.
- **HouseView:** the CIO's validated stance over researched subjects before any risk-profile sizing.

## Memory and learning

- **Belief Event:** an append-only transition to a typed claim, including confidence, valid time,
  transaction time, evidence, falsifiers, and status.
- **Belief Graph:** a rebuildable as-of projection connecting relevant evidence, beliefs,
  contradictions, theses, decisions, outcomes, and lessons.
- **Decision Record:** the immutable ex-ante record of a HouseView, its evidence cutoff, forecasts,
  target bands, benchmark state, and material fingerprints.
- **Outcome Observation:** an append-only market, forecast, thesis, benchmark, or execution result
  recorded only when its declared horizon is due.
- **Journal Lesson:** a bounded, expiring hypothesis about retrieval or attention behavior derived from
  multiple independent resolved decisions.

## Portfolio and execution

- **Risk Profile:** a versioned deterministic transformation of one HouseView into portfolio exposure
  under a declared risk envelope.
- **Target Band:** the allowed lower and upper portfolio weight around a deterministic target; remaining
  inside the band is a no-trade outcome.
- **DecisionPacket:** a complete, immutable, risk-clamped, versioned, account-bound, cycle-bound,
  signed, and expiring artifact that the executor may accept.
- **Execution Receipt:** the append-only record of broker submissions, changes, rejections, fills,
  quotes, timestamps, and divergence from a DecisionPacket.

## Governance

- **Constitution:** the operator-owned, versioned doctrine that constrains investment reasoning and
  risk; amendments activate only at a future Market Session boundary.
- **Champion:** the operator-approved production version of a prompt, workflow, model, rule, or
  portfolio policy.
- **Challenger:** one preregistered, prospectively evaluated variation from a Champion.
- **Research Lab:** an isolated, non-production namespace for stage replay using copied or synthetic
  state; its artifacts have no champion or execution authority.
