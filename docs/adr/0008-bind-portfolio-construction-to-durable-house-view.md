# ADR 0008: Bind portfolio construction to a durable HouseView

- Status: Accepted
- Date: 2026-08-28

## Context

Production research ends with one CIO resolution per Attention-owned request, and `UpdateMemory`
records the admissible belief changes. Deterministic portfolio construction must consume that exact
terminal production set without allowing a model, Research Lab artifact, ambient market read, or
partial retry to substitute material. The resulting weights and Target Bands change accepted paper
exposure, so their provenance and retry identity must remain independently reconstructable after a
process restart.

A lifecycle checkpoint that retained only an opaque result hash would not let `Status` prove which
session, cutoff, governance, research, memory, universe, policy, and as-of portfolio inputs produced
the decision. Re-running production research during a later phase would also create a second model-
effect opportunity or observe changed predecessor state.

## Decision

Add `ConstructPortfolio` immediately after `UpdateMemory` as the current terminal production phase.
Before construction, reconstruct the exact persisted terminal production resolution set from the
recorded production intents and observations. This replay is effect-free: an incomplete or
inconsistent path fails closed and never calls the model.

Create exactly one schema-versioned immutable HouseView per successful run. Its canonical material
binds the run, Market Session, Evidence Cutoff, Data Regime, runtime configuration, Constitution,
research policy, complete research artifact set, memory event set, universe snapshot, portfolio
policy, portfolio input set, source request and resolution identities, stances, uncertainty, event
eligibility, and canonical Instrument Identities. The HouseView contains no sizing field and accepts
only production CIO authority.

The `portfolio` capability owns HouseView admission, Balanced sizing, risk clamps, cash preservation,
Target Bands, typed refusals, and the input and result ports. Application orchestrates the capability
without acquiring sizing authority. Recorded and SQLite adapters validate or persist the owner-
defined contracts. One append-only portfolio result records the HouseView, targets, cash remainder,
Target Bands, policy and input identities, or a typed refusal. Exact retry replays; changed material
conflicts. `Status` reparses the complete canonical result, re-derives every identity, and validates
the lifecycle reference, exact run identity, and canonical recorded instant without trusting a
projection or accepting an identical row from another run.

Packet construction, broker credentials, order construction, and execution remain absent from this
phase and from the portfolio input source and result ledger.

## Alternatives considered

- Re-run production research before sizing. Rejected because it creates a second model-effect seam,
  can observe changed durable state, and cannot prove exact terminal-set identity.
- Persist only weights and one opaque result hash. Rejected because standalone reconstruction could
  not prove the decision's complete production and as-of input bindings.
- Let the CIO emit weights or construct HouseView in model output. Rejected because it transfers
  deterministic sizing authority to untrusted text and prevents independent clamp enforcement.
- Construct targets in application or the SQLite adapter. Rejected because orchestration and storage
  would own financial policy instead of depending on the portfolio-owned interface.
- Force capped remainder into other names. Rejected because cap interaction would increase exposure
  beyond the independently accepted risk budget and make cash an invalid outcome.

## Consequences

- A completed construction is reproducible from authoritative local records without a model, network,
  credential, packet, or broker capability.
- HouseView and result payloads are larger, but each required binding is independently inspectable and
  hash-validatable rather than implied by an opaque identifier.
- Portfolio inputs and the complete policy become pinned run material; changed retry data conflicts
  before another lifecycle effect.
- The current lifecycle stops at `ConstructPortfolio`. A later packet-publication slice must consume
  this immutable result through a separately reviewed authority seam.
- Construction is critical financial-authority code and therefore requires 100% line and branch
  coverage plus mutation evidence; recorded input and SQLite persistence remain safety-supporting.
