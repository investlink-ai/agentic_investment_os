# Agent Note: Plan capability stages in issue batches

Status: implemented

## Problem

The issue-worktree workflow defines how to execute one issue safely but not how to derive a coherent
set of issues from the product requirements and architecture. Creating tickets one at a time repeats
context discovery and can hide dependencies. Creating the whole roadmap at once makes speculative
delivery detail look authoritative and leaves stale issues as the implementation evolves.

## Decision

Plan work in dependency-based capability stages. Select the earliest outcome in the active
implementation sequence whose predecessors are implemented and verified. Use a direct issue when
that outcome fits one coherent pull request; otherwise draft one parent delivery issue and usually
three to seven vertical child issues.

Review the complete stage graph before publishing. After explicit approval, represent hierarchy and
real blockers with GitHub's native parent, sub-issue, and blocked-by relationships. Execute only
unblocked children, each through one issue branch, linked worktree, and pull request. Reapply the
planning skill to audit and close the parent after its children close, then derive the next stage from
the code that shipped.

The parent owns capability decomposition and stage status. A child owns one observable delivery
outcome and falsifiable acceptance criteria. Active product, domain, architecture, testing, and ADR
documents remain authoritative. A pull request owns the delivered delta, blast radius, and
verification evidence.

The executable agent workflow lives in
[`plan-stage-issues`](../../skills/plan-stage-issues/SKILL.md); the stable human-facing policy lives in
[`docs/development.md`](../../../docs/development.md#stage-planning).

## Alternatives considered

- Create one issue immediately before each implementation. Rejected because repeated planning loses
  the stage-level dependency view and spends context rediscovering adjacent work.
- Plan by calendar sprint. Rejected because this repository's work is dependency-driven and does not
  yet need capacity allocation or release ceremony.
- Publish the complete V0 roadmap in one batch. Rejected because downstream granularity and blockers
  will change as real interfaces and evidence emerge.
- Port the external `to-tickets` workflow verbatim. Rejected because tracker setup, scratch planning
  files, labels, and a large backlog would duplicate this repository's authorities and add machinery
  without a demonstrated need.

## Consequences

- Each planning pass pays the context cost once for a small group of related issues while keeping the
  future adaptable.
- The native GitHub graph makes the executable frontier visible without a parallel planning artifact.
- Publication is a deliberate external write and therefore waits for user approval and repository
  write permission.
- Parent closure is an explicit, resumable outcome audit owned by the planning skill, not an automatic
  side effect of one child pull request.
- Milestones, Projects, label taxonomies, sprint objects, registries, and a full-roadmap issue backlog
  remain out of scope until coordination evidence justifies them.

## Verification

- `AGENTS.md` points planning requests to one project skill.
- `docs/development.md` defines the stable stage-to-issue-to-worktree policy without copying the
  publishing procedure.
- `implement-spec-slice` refuses blocked issues and maps completion evidence back to issue criteria.
- `make harness` verifies that the planning skill remains installed.
