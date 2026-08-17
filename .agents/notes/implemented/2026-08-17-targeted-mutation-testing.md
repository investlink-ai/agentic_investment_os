# Agent Note: Target mutation testing at deterministic safety behavior

Status: implemented

## Problem

Line and branch coverage prove execution, not that assertions reject incorrect financial behavior.
A repository-wide mutation gate would also spend time on transport, logging, and wiring where contract
or integration tests provide a stronger signal.

## Decision

The repository uses `mutmut` through the single `make mutation` entrypoint and configures it in
`pyproject.toml`. The gate initially mutates callable behavior in `domain/`, `portfolio/`, and
`execution/`; [the testing policy](../../../docs/testing.md#mutation-testing) owns the active scope and
acceptance rules.

Only killed mutants and narrowly documented equivalent skips pass. Surviving, uncovered, timed-out,
suspicious, interrupted, crashing, or unaccounted mutants fail without being reduced to an aggregate
score. Mutation runs select deterministic test tiers and have no broker credentials or live network
path. The Make target discards its prior ignored mutation workspace before certification so an
incremental cache cannot supply a stale safety result.

The command remains outside `make check` and Git hooks. A path-filtered pull-request job and a weekly
job run it independently with a bounded timeout. Until a configured package contains a callable, the
runner reports an explicit scaffold exemption; the first such implementation activates the gate
without a second process decision.

## Alternatives considered

- Coverage alone was rejected because a test can execute a safety branch without distinguishing its
  correct result from a mutated one.
- Repository-wide mutation on every default check was rejected because runtime and equivalent-mutant
  review would concentrate effort on low-risk plumbing and slow the primary feedback loop.
- Deferring all configuration until critical code exists was rejected because the activation rule
  could be missed by the first implementation. An explicit scaffold exemption keeps the harness
  ready without claiming product-level mutation evidence.
- Cosmic Ray was not selected because its session and distributor machinery adds configuration that
  the current local and single-job workflow does not need.

## Consequences

The locked development environment and scheduled CI carry an additional local, non-metered tool.
Critical deterministic code must provide tests that kill each meaningful mutation. Equivalent skips
remain visible in source and require semantic justification. Clean certification gives up incremental
speed; direct `mutmut` commands remain available for local diagnosis. If runtime exceeds the independent
CI budget, the gate may be split by capability while retaining one Make entrypoint and the same outcome
policy.

## Verification

`make mutation` validates configuration and reports the scaffold exemption at the current repository
state. A temporary scoped probe killed both generated mutants with its boundary assertion and produced
a nonzero gate with one survivor when that assertion was removed; the probe is not part of the shipped
tree. `make check` verifies the normal deterministic gate, documentation structure, and mutation harness
files independently.
