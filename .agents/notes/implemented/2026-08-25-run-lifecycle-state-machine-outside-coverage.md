# Agent Note: Run the lifecycle state machine outside coverage

Status: implemented

## Problem

The persistence-level lifecycle state machine can dominate `make check` because coverage
instrumentation and orthogonal fixture breadth multiply the cost of its bounded generated sequences.
The remaining deterministic suite already satisfies every consequence-tier coverage threshold, while
the state machine supplies behavioral evidence for replay, interruption, conflict, reopen, and
reference-model agreement. Focused contract and integration tests separately own provider,
source-kind, schema, and payload variants.

## Decision

Keep the full derandomized Hypothesis budget in the handoff gate, but collect coverage from the rest
of the deterministic suite concurrently. The [testing policy](../../../docs/testing.md#selection)
owns the required behavior, and the [Makefile](../../../Makefile) owns its local, hook, and hosted-CI
orchestration. The state machine uses an explicitly named minimal fixture containing the required
market and news evidence while focused deterministic tests retain every optional official-source
contract. Both pytest legs report their slowest tests so cost growth remains visible without a flaky
shared-runner duration threshold.

## Alternatives considered

- Reduce the Hypothesis examples or stateful step count. This shortens feedback by exploring fewer
  generated sequences and weakens the persistence contract.
- Keep the state machine under coverage. This preserves one pytest invocation but spends most gate
  time collecting coverage that does not determine whether any configured tier passes.
- Remove the state machine from the default gate. This loses mandatory generated interruption,
  replay, and reopen evidence.
- Split hosted CI into separately named jobs. This introduces another branch-protection check and
  does not improve local or pre-push feedback.
- Add optional provider and source variants to every generated example. This multiplies setup and
  persistence work across a dimension owned more precisely by focused tests without increasing
  lifecycle-state exploration.
- Enforce a fixed wall-time assertion. Shared-runner variance can fail unchanged behavior; diagnostic
  timings and the existing outer timeout provide useful evidence without turning speed into a brittle
  correctness result.

## Consequences

Parallel pytest processes share no intentional writable state. The state-machine leg disables pytest
caching, uses the disabled Hypothesis database, and retains per-example temporary directories. Future
coverage changes must preserve the explicit deselection boundary and tier check. A fixture reduction
must retain direct accepted and refused evidence for every removed orthogonal variant; it cannot
remove a state, persistence, or safety seam owned by the generated test.

## Verification

The [gate integration test](../../../tests/integration/test_check_gate.py) exercises the public target
with controlled commands to prove the exact partition, concurrency, and failure propagation from
either leg. The complete gate confirms that the independently collected report passes every coverage
tier while the state machine passes at its full Hypothesis budget. Dedicated lifecycle and contract
tests retain all five optional source types plus cutoff, required-source, malformed-input, and
provenance refusal evidence.
