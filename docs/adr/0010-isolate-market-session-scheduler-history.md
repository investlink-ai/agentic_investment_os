# ADR 0010: Isolate Market Session scheduler history

- Status: Accepted
- Date: 2026-08-29

## Context

The operating system needs automatic NYSE-relative invocation without allowing an OS timer, calendar
provider, or scheduler process to become lifecycle authority. Startup, sleep, wake, interruption, and
retry also make a process heartbeat an unreliable account of whether a Market Session was requested,
missed, refused, or completed. Reusing lifecycle rows for scheduling intent would couple timer policy
to financial checkpoints and make a scheduler schema change capable of changing lifecycle truth.

Concurrent local invocations introduce a second distinction. Durable intent must precede `Advance`,
but a retry timeout alone can overlap a still-running process. Holding a SQLite transaction across the
complete lifecycle would serialize the effect while violating the checkpoint boundary and increasing
database-lock blast radius.

## Decision

The scheduler is an uncredentialed local capability outside the lifecycle state machine. A complete,
versioned `SchedulerPolicy` pins the `xnys-regular-2026a` code calendar, first session, NYSE-open
offset, lateness window, interruption recovery delay, and per-invocation action bound. The calendar
contains the official 2026 NYSE weekday, holiday, early-close, and `America/New_York` rules. Another
calendar identity refuses. Another year blocks new eligible effects subject to the status, recovery,
and missed-session bookkeeping exceptions below; the host timezone and provider calendar never
become authority.

Scheduling calls only public `Advance` and `Status`. It constructs the exact Market Session envelope,
fixed Champion mode, and stable policy-and-session idempotency identity. It has no private lifecycle
stage, research, model, portfolio, packet, signing, executor, broker, credential, or account port.
Lifecycle `Status` remains the only lifecycle-liveness authority.

The scheduler process invokes those capabilities through a process client. A separately composed
operating-system process owns lifecycle persistence, research and model ports, DecisionPacket signing
and verification, and account scope. Passing an in-process lifecycle object to the scheduler would
transfer its reachable authority and is therefore not an admissible composition even if the object is
typed only as the public callable protocol.

A private `scheduler.sqlite3` ledger stores immutable policy metadata and append-only `started`,
`resumed`, `completed`, `missed`, and `refused` events. `started` or `resumed` is appended before
`Advance`; a terminal event appends the exact public lifecycle receipt and hash afterward. Rebuild
validates the complete current schema, canonical UTC times, calendar-derived windows, sequence,
transitions, receipt identity, and event hashes. Changed policy conflicts. Missed sessions are
reported and never backfilled.

Calendar expiry blocks new eligible effects but does not block status reconstruction, bounded
classification of overdue supported sessions as missed, or recovery of an already-started supported
session. Missed classification never invokes `Advance`; recovery appends its observed outcome before
the scheduler refuses the unsupported current year. Both paths preserve append-only history without
extending the calendar's lifecycle authority. When the action bound leaves overdue sessions
unclassified, status exposes the oldest as pending until later polls drain the backlog; this marker
reports scheduler work and never restores lifecycle eligibility.

A mode-`0600` advisory lock is held across one scheduler reconstruction, claim, public lifecycle
invocation, and observation. The operating system releases it on process death, allowing durable
recovery; concurrent live instances serialize without duplicate public requests. The lock is only
live-process exclusion and never status, liveness, or completion truth.

## Alternatives considered

- Treat `launchd` execution or a heartbeat as completion. Rejected because sleep, wake, delay, and
  process death cannot prove a lifecycle effect or durable checkpoint.
- Put scheduler claims in the lifecycle database. Rejected because timer policy does not own
  financial lifecycle truth and should not widen that schema or transaction boundary.
- Backfill every overdue session. Rejected because late research would violate the approved as-of
  timing contract; overdue eligible sessions become durable `missed` observations.
- Use only an expiring durable lease. Rejected because expiry can overlap a slow live `Advance`.
- Hold a SQLite write transaction across `Advance`. Rejected because external effects belong between
  durable checkpoints, not inside an open database transaction.

## Consequences

- Operators can distinguish pending future work, incomplete attempts, completed lifecycle requests,
  lifecycle refusals, and missed sessions without granting scheduling state lifecycle authority.
- Scheduler and lifecycle histories can be inspected and recovered independently; neither may be
  rewritten to repair the other.
- The code-pinned calendar must be replaced by an explicitly reviewed future version before its
  supported year ends. New-session availability stops rather than consulting an ambient or mutable
  calendar; prior status remains inspectable and incomplete supported claims remain recoverable.
- macOS installation is reversible and contains no tracked machine path, credential, account
  identifier, or generated state. CI uses injected clocks, the pinned calendar, recorded adapters,
  private temporary roots, and fresh processes.
