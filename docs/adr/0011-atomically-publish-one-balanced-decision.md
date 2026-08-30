# ADR 0011: Atomically publish one Balanced decision

- Status: Accepted
- Date: 2026-08-29

## Context

The complete Balanced result and its three same-input shadows establish portfolio intent, but they do
not by themselves provide a bounded executable handoff. An executor needs one independently
verifiable artifact that cannot substitute a shadow, omit an ex-ante input, widen risk, change account
scope, or become partially visible after interruption. The operating-system process must construct
and publish that artifact without receiving broker credentials or gaining broker-effect authority.

Signing time and packet expiry are intentionally not part of deterministic decision identity. A retry
after an interrupted atomic append may observe a different wall-clock instant or signing result while
still representing the exact same authorized portfolio intent. Treating those observations as a new
decision would either duplicate authority or make safe process recovery impossible.

## Decision

`portfolio` constructs one immutable Champion Decision Record for each Market Session after loading
and semantically revalidating the exact durable Balanced result and all three required shadows. The
record content-identifies the session, HouseView, Evidence Cutoff, forecasts, Target Bands, benchmark,
position snapshot, cash, shadows, Constitution, portfolio and cost policies, Data Regime, sources,
models, and complete material fingerprint set. Balanced trade-eligible Target Bands alone may produce
one packet. An in-band or otherwise empty instruction set preserves the Decision Record and records a
typed no-action result without a packet.

The packet is a closed, schema-versioned US-equity contract. It fixes Balanced paper authority,
canonical instrument identities, whole-share units, increase-or-decrease direction, authorized and
target weights, Target Band bounds and reason, the complete risk envelope, long-only and no-leverage
policy, the maximum one-percent fraction of median dollar volume, regular-session day-limit policy,
cycle, validity window, non-secret account scope, Decision Record and policy identities, content
identity, and signature. Hostile readers validate exact fields, canonical encodings, content
identities, scope, signature, and semantic agreement with the durable portfolio cycle before admitting
it.

One SQLite transaction inserts the complete Decision Record and optional packet in a single
append-only row. Unique run and cycle keys permit one publication per Market Session. A fresh packet
must carry the exact official issue and expiry times and become durably visible before the regular
open. Exact replay reparses, verifies, and semantically reconstructs the stored publication without
resigning it, including revalidating its recorded visibility against the same calendar policy.
Concurrent candidates with the same decision and authorization intent return the first complete
winner even when issue time or signature differs; changed decision, account, risk, or instruction
material conflicts. Lifecycle history retains only the bounded Decision Record identity, optional
packet identity and expiry, publication time, and typed no-action reason. `Advance` then terminates at
`PublishDecision`; `Status` reports `AwaitExecution` and revalidates authoritative decision history.

The code-owned validity source admits publication from the approved fifteen-minute pre-open freeze
until the regular open and expires the packet thirty minutes after open. It derives those instants
from the same pinned Market Session calendar used by scheduling. The lifecycle uses that policy before
signing and again after signing; the durable ledger applies it at insertion and reopen. Early,
at-open, post-open, unsupported, wrongly expired, or otherwise invalid packet windows produce no
publication. This timing gate applies only to executable packets: a valid no-action result retains and
atomically publishes its Champion Decision Record outside the packet window.

Signing and verification enter through typed ports assembled by the uncredentialed operating-system
entrypoint. The concrete signer owns private key bytes only in memory, exposes a derived key identity,
and has no broker, model, network, or account-identifier capability. Packet account scope is a
non-secret content identity rather than a broker account identifier. Executor validation, quote and
limit policy, order effects, reconciliation, and outcomes remain outside this process and decision.

## Alternatives considered

- Let the future executor reconstruct intent from portfolio tables. Rejected because reconstruction
  would give it discretion over omitted fields and create more than one authority representation.
- Publish the packet after the Decision Record in a second transaction. Rejected because interruption
  could expose an incomplete or ambiguous executable handoff.
- Include issue time and signature in exact retry identity. Rejected because a crash before lifecycle
  checkpointing would make recovery conflict with the already durable authorization.
- Persist signing secrets or real broker account identifiers beside the packet. Rejected because the
  operating-system store is outside the credentialed executor boundary and does not require them.
- Allow a new packet to replace an expired packet for the same Market Session. Rejected because V0
  correction is append-only and one session has one immutable Champion decision.

## Consequences

- A complete Balanced decision becomes independently inspectable without granting the operating
  system broker authority.
- Publication consumes extra storage by retaining canonical decision and packet representations and
  revalidates the complete upstream portfolio cycle on replay and status.
- An expired stored packet remains immutable historical authority and cannot be refreshed in place;
  later retry returns that exact publication.
- Account-scope provisioning, signing-key lifecycle, and future executor verification remain explicit
  composition or later-stage obligations. Scheduler composition must use the approved pre-open
  policy, while lifecycle and durable packet admission share that policy to enforce issue, visibility,
  and execution-deadline times.
- Packet construction and admission are critical financial-authority behavior and require complete
  branch coverage, hostile-contract tests, mutation certification, atomicity, replay, concurrency,
  corruption, and process-boundary journey evidence.
