# Agent Note: Keep the lifecycle kernel specific

Status: implemented

## Problem

[ADR 0002](../../../docs/adr/0002-lifecycle-policy-in-domain-kernel.md) places Stage 1 lifecycle
policy in a pure domain kernel. The implementation still needs an appropriate abstraction depth
without turning two implemented phases into a general workflow framework.

## Decision

Keep the framework-free kernel in
[`domain.lifecycle`](../../../src/agentic_investment_os/domain/lifecycle.py) specific to the Market
Session lifecycle. Extract shared workflow machinery only after a second concrete lifecycle proves a
smaller common contract.

## Alternatives considered

- Introduce a generic workflow engine or reusable graph abstraction. Rejected because Stage 1 has one
  lifecycle, no second workflow that proves a reusable contract, and V0 excludes LangGraph.
- Generalize the event sequence behind a workflow protocol now. Rejected because its extension points
  would be speculative and would make the lifecycle's closed typed states less explicit.

## Consequences

- A generic workflow abstraction requires a second concrete use case and evidence that sharing it
  simplifies both lifecycles without weakening typed state or fail-closed behavior.
- Lifecycle-specific names and closed unions remain visible to tests and future maintainers.

## Verification

Pure unit tests cover transition, recovery, replay, refusal, conflict, and invalid-history decisions.
Generated integration sequences compare reopened SQLite behavior with an independent reference state
machine while existing rollback, corruption, append-only, and concurrency scenarios remain in force.
