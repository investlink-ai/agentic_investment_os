# ADR 0002: Own lifecycle policy in a pure domain kernel

- Status: Accepted
- Date: 2026-08-22

## Context

Stage 1 lifecycle ordering, recovery, replay, refusal, and conflict behavior crossed application and
SQLite methods. Adding a phase required coordinated branches in both layers, and storage code could
change lifecycle policy while reconstructing rows. The system needs one deterministic owner without
changing its append-only schema, wire values, request-scoped failure behavior, or external authority.

## Decision

Place lifecycle reconstruction and transition decisions in a framework-free domain kernel. The
kernel accepts typed request-scoped history and session-occupancy facts, then returns one typed record
to append or a terminal receipt. Application code repeats this operation until it receives a terminal
receipt.

Persistence adapters validate hostile representations, select only history relevant to an `Advance`
request, invoke the kernel inside an atomic transaction, and append the selected record. Global
status reconstruction continues to validate all authoritative history. Existing SQLite tables, event
names, phase values, and append-only triggers remain unchanged.

## Alternatives considered

- Keep phase ordering and recovery in phase-specific application and SQLite methods. Rejected because
  lifecycle policy would remain duplicated across an orchestration boundary and a persistence
  boundary.
- Reconstruct every stream for every `Advance` request. Rejected because corruption in unrelated
  history would change an otherwise valid request's established receipt and durable effects. Global
  validation belongs to `Status`; request advancement remains scoped to its key and session
  occupancy.

## Consequences

- Later phases extend one lifecycle transition definition and one durable representation path.
- SQLite remains responsible for transactionality, hostile-row parsing, request-scoped selection,
  and append mechanics, but not transition ordering or recovery policy.
- Pure tests can exhaust transition behavior without SQLite, while adapter tests retain responsibility
  for atomicity, compatibility, append-only enforcement, corruption boundaries, and concurrency.
- The kernel remains deterministic and has no filesystem, subprocess, framework, ambient clock, or
  network dependency.
