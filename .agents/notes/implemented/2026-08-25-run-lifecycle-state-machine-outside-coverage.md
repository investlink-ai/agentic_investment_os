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
owns the required behavior, the [Makefile](../../../Makefile) owns each gate leg and their local
orchestration, and the [CI workflow](../../../.github/workflows/ci.yml) schedules those legs on
separate hosted runners. The workflow preserves the existing `make check` status as an aggregate
that fails unless both legs pass, so branch protection retains one stable mandatory identity. The
state machine uses an explicitly named minimal fixture containing the required market and news
evidence while focused deterministic tests retain every optional official-source contract. Both
pytest legs report their slowest tests so cost growth remains visible without a flaky shared-runner
duration threshold.

## Alternatives considered

- Reduce the Hypothesis examples or stateful step count. This shortens feedback by exploring fewer
  generated sequences and weakens the persistence contract.
- Keep the state machine under coverage. This preserves one pytest invocation but spends most gate
  time collecting coverage that does not determine whether any configured tier passes.
- Remove the state machine from the default gate. This loses mandatory generated interruption,
  replay, and reopen evidence.
- Keep both hosted legs inside one parallel Make process. This avoids another runner setup, but the
  CPU-heavy state machine can starve the coverage suite on a small shared runner and make both slower.
- Publish the hosted legs as independent required check names. This exposes each result directly but
  requires a synchronized branch-protection change; a stable aggregate retains the existing contract.
- Add optional provider and source variants to every generated example. This multiplies setup and
  persistence work across a dimension owned more precisely by focused tests without increasing
  lifecycle-state exploration.
- Enforce a fixed wall-time assertion. Shared-runner variance can fail unchanged behavior; diagnostic
  timings and the existing outer timeout provide useful evidence without turning speed into a brittle
  correctness result.

## Consequences

Local parallel pytest processes share no intentional writable state. Hosted legs receive isolated
runners and locked environments, while the aggregate performs no repository or dependency work.
The state-machine leg disables pytest caching, uses the disabled Hypothesis database, and retains
per-example temporary directories. Future coverage changes must preserve the explicit deselection
boundary, tier check, both hosted jobs, and fail-closed aggregate. A fixture reduction must retain
direct accepted and refused evidence for every removed orthogonal variant; it cannot remove a state,
persistence, or safety seam owned by the generated test.

## Verification

The [gate integration test](../../../tests/integration/test_check_gate.py) exercises the public target
with controlled commands to prove the exact local partition, concurrency, and failure propagation
from either leg. It also pins the hosted matrix targets, independent scheduling, and stable
fail-closed aggregate. The complete gate confirms that the independently collected report passes
every coverage tier while the state machine passes at its full Hypothesis budget. Dedicated lifecycle
and contract tests retain all five optional source types plus cutoff, required-source,
malformed-input, and provenance refusal evidence.
