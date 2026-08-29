# ADR 0009: Account for required same-input portfolio shadows

- Status: Accepted
- Date: 2026-08-29
- Supersedes: [ADR 0008](0008-bind-portfolio-construction-to-durable-house-view.md)

## Context

Balanced construction already admits one immutable HouseView and one complete as-of portfolio input
set. Prospective comparison also requires Conservative, Growth, and capped equal-weight accounts, but
letting a variant select its own research, price, cutoff, position state, or constraints would mix
selection and sizing effects. Treating a shadow as a portfolio result shaped for packet publication
would also create an authority path that its accounting purpose does not require.

Shadow records are authoritative ex-ante accounting facts. They must survive retry, reconstruction,
and projection loss without becoming fills, outcomes, Champion decisions, or executable instructions.
A lifecycle checkpoint that can complete while one required shadow is absent would make the later
Balanced publication seam unable to prove a complete same-input comparison.

## Decision

`ConstructPortfolio` admits exactly one HouseView and one portfolio-input identity, then constructs
Balanced and the three required shadows as one portfolio cycle. Conservative and Growth use the same
capped inverse-volatility algorithm under their approved gross, name, and sector envelopes. The
capped equal-weight account changes only the initial sizing score and retains Balanced timing,
eligibility, downside reductions, liquidity, event, common-cause, correlation, Target Band, and
cash-preservation rules.

The versioned portfolio policy declares all three shadows and one frozen ex-ante cost-input policy.
The cost-input policy fixes available-at-time prices and absolute hypothetical adjustment weight as
the reconstructable turnover inputs; it does not infer a fill, apply a later outcome, or implement the
official evaluation cost overlay. Each shadow record binds its run, cycle, account and algorithm
identity, shared HouseView, policy and input identities, cutoff, starting position-snapshot identity,
cash and equity, complete validated portfolio input and policy material, targets, accounting bands,
retained cash, available-at-time prices, median-dollar-volume and risk-group inputs, modeled turnover
inputs, source availability, material fingerprints, and content hash. Reconstruction verifies the
embedded input and policy identities against Balanced, then re-derives every shadow target, Target
Band, cash remainder, and cost input. Agreement among the three shadows is not evidence of a shared
input when all three could carry the same substitution.

The portfolio ledger appends Balanced and every required shadow in one SQLite transaction. Exact
redelivery returns the prior identities. Changed material conflicts, an incomplete shadow set is
invalid authoritative history, and concurrent first delivery serializes before checking for a
winning append. `Advance` revalidates terminal replay, while `Status` reparses every record before
accepting the lifecycle checkpoint. `Advance` scopes that validation to its requested run so unrelated
history cannot block a valid retry; `Status` retains global validation and rejects corruption anywhere
in authoritative portfolio history. Both expose only the fixed account kind and content hash for each
shadow.
The shadow schema has no Champion, packet, order, fill, outcome, broker, credential, or trade-
eligibility field, and no shadow type crosses into `execution`.

## Alternatives considered

- Reconstruct each profile from a separately created HouseView. Rejected because even identical
  values would create another selection identity and allow cutoff or eligibility substitution.
- Store shadows as ordinary portfolio-construction results. Rejected because the executable Balanced
  result and non-executable accounting facts require different authority-shaped interfaces.
- Persist shadows after the lifecycle checkpoint. Rejected because interruption could leave a
  publishable Balanced checkpoint with incomplete comparison history.
- Compute fills, realized costs, or evaluation outcomes during construction. Rejected because those
  are later observations and would contaminate the ex-ante record.

## Consequences

- One production cycle preserves selection equivalence while making sizing policy differences
  independently inspectable.
- A missing or corrupt required shadow blocks reconstruction and any later Champion publication.
- Shadow payloads duplicate the complete validated input and policy plus bounded target and cost-input
  material, accepting storage cost in return for independent semantic re-derivation and append-only
  reconstruction.
- The current lifecycle still stops at `ConstructPortfolio`; packet publication, broker effects,
  outcome observation, and evaluation remain separately authorized work.
- Shadow sizing is critical deterministic portfolio behavior and requires hand-oracle, property,
  mutation, retry, concurrency, interruption, corruption, and system-journey evidence.
