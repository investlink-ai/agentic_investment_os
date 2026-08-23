# ADR 0005: Share core contracts with typed asset variants

- Status: Accepted
- Date: 2026-08-23

## Context

Alpaca exposes US equities, crypto spot pairs, and listed options through some common endpoints, but
their identities, schedules, quantities, currencies, entitlements, order constraints, and settlement
activities differ materially. The deterministic core must preserve one authority and durability model
without treating an Alpaca response shape as the domain model. The first eligible-universe slice also
creates durable identity and snapshot contracts that would be expensive to replace after later stages
depend on display symbols.

## Decision

Keep the public lifecycle result contracts, provenance envelopes, immutable snapshot and receipt
identity, append-only events, packet integrity, effect-local idempotency, and reconciliation completion
in the shared deterministic core. Represent instrument identity, schedule rules, lifecycle phases,
quantities, order intents, position effects, and settlement activities as closed discriminated
variants owned by the relevant asset class. Every variant carries exact units and its own schema
version; unrelated optional fields are invalid.

This decision fixes the shared contract, not a generic workflow engine. The implemented
`domain.lifecycle` kernel remains specific to the Market Session lifecycle. A separately approved,
still-disabled second asset lifecycle implementation must first provide the concrete comparison that
justifies extracting shared transition machinery; composition dispatches before a specific transition
kernel, never through asset-class branches inside one kernel.

Adapters validate and translate Alpaca identifiers, enums, feeds, and response shapes into those owned
contracts. Display symbols remain aliases rather than durable keys. Composition explicitly enables a
closed set of supported asset classes; V0 accepts only US equities, regardless of observed provider
entitlements, and rejects disabled or unknown variants before research or effects.

Entrypoints may therefore depend directly on public immutable `domain` contracts to validate
configuration and select the closed composition. This intentional `entrypoints -> domain` edge keeps
variant activation at the composition root; it does not permit domain code to depend on entrypoints,
adapters, credentials, or provider representations.

## Alternatives considered

- A generic phase protocol before a second lifecycle exists was rejected because it would freeze the
  equity sequence as an accidental cross-asset abstraction.
- Separate durable envelopes, receipt reconstruction, and idempotency protocols per asset class were
  rejected because they would duplicate authority and replay rules that must remain consistent.
- One broker-shaped record with fields optional for unrelated asset classes was rejected because it
  admits invalid combinations, spreads Alpaca semantics into the core, and makes schema evolution
  ambiguous.
- A dynamic plugin registry was rejected because no third-party extension ecosystem exists and runtime
  discovery would make supported authority harder to audit and fail closed.
- Retaining equity display symbols as identities until crypto or options implementation was rejected
  because universe snapshots, positions, packets, effects, and receipts would acquire incompatible
  durable keys.

## Consequences

- A new asset class may add owned lifecycle phases, policy, variants, adapters, configuration, and tests
  without changing the public lifecycle results, durable envelope, receipt reconstruction,
  packet-integrity checks, or effect-idempotency rules.
- The current Market-Session-specific implementation is not generalized until a second concrete
  lifecycle proves the smaller shared transition contract.
- Any exception to that extension budget requires an architecture update and a superseding or
  additional ADR before implementation.
- Crypto and options remain outside V0 investment and execution authority; defining their variants
  does not approve strategies or broker use.
