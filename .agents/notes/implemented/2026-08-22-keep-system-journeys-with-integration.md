# Agent Note: Keep deterministic system journeys with integration

Status: implemented

## Problem

The testing policy needs compact evidence that production composition, persistence, public
capabilities, and process seams cooperate across a product journey. Calling that evidence “E2E” can
instead encourage a new test tier without a distinct owner, one slow happy-path test, or live provider
calls that make deterministic correctness depend on credentials and mutable services.

## Decision

Treat deterministic system journeys as a named subset of the
[integration tier](../../../docs/testing.md#deterministic-system-journeys). They exercise project-owned
links through real composition and local persistence while controlling external providers at their
owned ports. A top-level `tests/e2e/` tier requires a measured difference in runtime, environment,
ownership, orchestration, or cadence and a coordinated testing-policy and gate change.

Keep journeys compact and add them with implemented vertical slices. Focused unit, contract,
integration, state-machine, corruption, and mutation tests continue to own exhaustive local evidence.
Live model and Alpaca paper rehearsals remain explicitly invoked evidence for provider compatibility
and operational readiness, not deterministic correctness or investment validity.

## Alternatives considered

- Create `tests/e2e/` immediately. This was rejected because the current journey uses the integration
  tier's composition, persistence, environment, runner, and cadence; a new name would create a parallel
  taxonomy without a different operational contract.
- Use one full live happy-path scenario. This was rejected because provider variability would enter
  the keyless gate, refusal and recovery behavior would remain shallow, and a pass could be mistaken
  for financial or live-market validity.
- Rely only on focused integration tests. This was rejected because focused tests do not provide one
  compact observation that the implemented project-owned path composes across a fresh process,
  durable recovery, replay, and projection rebuild.

## Consequences

- `tests/integration/system/` contains a small number of cross-component journeys rather than copies
  of focused fault matrices.
- Each capability slice adds system evidence only after its public path and owned seams exist.
- A separate tier or scenario language remains unavailable until demonstrated operational differences
  justify its maintenance cost.
- Passing a system journey remains bounded evidence and never replaces seam validation, mutation
  testing, semantic review, live rehearsal, or prospective paper evaluation.

## Verification

The Stage 1 journey enters through `configure_advance` and `configure_status`, uses private SQLite
state and fresh processes, resumes after a committed interruption, rebuilds a damaged projection, and
replays without adding authoritative events. `make check` retains the deterministic system subset;
`make mutation` independently evaluates mutation-critical behavior.
