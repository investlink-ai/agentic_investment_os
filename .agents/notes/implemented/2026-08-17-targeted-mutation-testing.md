# Agent Note: Target mutation testing at critical authority behavior

Status: implemented

## Problem

Line and branch coverage prove execution, not that assertions reject incorrect financial behavior.
A repository-wide mutation gate would also spend time on transport, logging, and wiring where contract
or integration tests provide a stronger signal.

## Decision

The repository uses `mutmut` through the single `make mutation` entrypoint and configures it in
`pyproject.toml`. [The testing policy](../../../docs/testing.md#mutation-testing) owns the
consequence-based scope, current candidate inventory, static source and test allowlists, and acceptance
rules. Mutation certification is reserved for deterministic behavior that can directly alter
financial authority or a broker effect; deterministic safety-supporting behavior retains evidence
selected for its own contract.

The repository pays the clean-run cost only for the narrow authority spine. It rejects mutation
outcome categories that cannot be traced to killed behavior, including the engine's unqualified skip
category, rather than treating an aggregate score as certification. The testing policy owns the
current activation, focused-test, clean-state, scaffold, and strict-accounting contract. The
[workflow](../../../.github/workflows/mutation.yml) owns its pull-request and manual triggers.

## Alternatives considered

- Coverage alone was rejected because a test can execute a safety branch without distinguishing its
  correct result from a mutated one.
- Broad mutation of deterministic domain, orchestration, persistence, configuration, and boundary code
  was rejected because a killed mutant does not prove a direct authority consequence. Contract,
  corruption, integration, architecture, property, and coverage evidence are stronger for those
  behaviors.
- Repository-wide mutation on every default check was rejected because runtime and equivalent-mutant
  review would concentrate effort on low-risk plumbing and slow the primary feedback loop.
- Automatic changed-path classification was deferred because file location cannot establish the
  reachable authority consequence reliably at this maturity stage. Issue authors and reviewers own
  the label until repeated classifications justify an executable rule.
- Deferring all configuration until critical code exists was rejected because the activation rule
  could be missed by the first implementation. An explicit scaffold exemption keeps the harness
  ready without claiming product-level mutation evidence.
- Cosmic Ray was not selected because its session and distributor machinery adds configuration that
  the current local and single-job workflow does not need.

## Consequences

The locked development environment carries an additional local, non-metered tool. Critical
deterministic code must be added to exact source and focused-test allowlists and must kill each
meaningful mutation. Clean certification gives up incremental speed; direct `mutmut` commands remain
available for local diagnosis. Human label ownership can miss a qualifying pull request, so review
must compare the changed behavior with the testing policy. If runtime exceeds the independent CI
budget, the gate may be split by capability while retaining one Make entrypoint and the same outcome
policy.

## Verification

On 2026-08-24, clean certification at pinned base `b5c5525` killed all 2,961 generated mutants in
113.10 seconds. The exact historical source and test selections remain reconstructable from that
commit's `pyproject.toml`.
The initial targeted scaffold completed in 0.08 seconds with no callable and therefore no generated
mutant. Current selection, behavior, and verification details remain in the active testing policy,
configuration, runner, and tests rather than being copied into this rationale record.
