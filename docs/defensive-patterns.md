# Defensive patterns

These patterns prevent defect classes implied by the system's durability, evidence, and execution
requirements. Read them before changing lifecycle, persistence, boundary, concurrency, filesystem, or
executor code. When a real defect or near miss sharpens a rule, update the rule and keep incident
history in an ADR or issue.

## Persist intent before effects

An external request must be preceded by a durable checkpoint that identifies the intended operation
and its idempotency key. Append the observed result before advancing. A retry resumes from the last
committed checkpoint; it never guesses whether an effect occurred.

## Make idempotency local to every effect

A lifecycle key does not make broker submission, outcome recording, or evidence ingestion idempotent.
Give each effect a stable domain identity and enforce uniqueness at its authoritative store. Replays
return the prior disposition or append a new observation; they do not duplicate exposure or history.

## Report independent outcomes independently

A call can time out and later complete, a broker can accept an order whose response was lost, and a
source can be stale while reachable. Preserve independent facts in typed results instead of collapsing
them into a success boolean or mutually exclusive exception branches.

## Append corrections; rebuild projections

Authoritative financial records are immutable events. Correct an error with a new event that names
what it supersedes. Derived graphs, reports, and indexes may be replaced only by deterministic rebuild
from authoritative records.

## Treat availability time as distinct from event time

Store source event time, publication or acceptance time, first-observed time, and derived availability
time when they differ. As-of reads admit evidence by availability at the pinned cutoff, never merely by
the date described inside the evidence.

## Validate on both sides of a boundary

Validate hostile input before constructing domain values, and validate the artifact emitted to the
next process or authority domain. Database rows, model JSON, parsed filings, configuration, signed
packets, and broker responses are boundary data even when produced by our own adapter.

## Fail closed with a durable reason

Map stale, partial, contradictory, unauthorized, or unvalidated state to an explicit no-action or
failure disposition. Persist enough context to reproduce the refusal. Never manufacture defaults,
skip a required stage, or reuse a stale artifact to keep the lifecycle moving. Once recorded, a
terminal refusal takes precedence over partial checkpoints for the same idempotency key.

## Publish executable artifacts atomically

A partially written `DecisionRecord` or `DecisionPacket` must never become visible to execution.
Build and validate the complete artifact, persist it transactionally or by atomic replacement, then
publish its identifier. Evidence captured before a later failure may remain committed.

## Separate authority by construction

The research process receives curated evidence and schemas, never broker credentials. The executor
receives validated packets and broker access, never a Codex client. Enforce the separation in
composition roots and process environments rather than relying on a prompt or call-site convention.

## Treat external text as data

Web pages, filings, news, model prose, and generated Markdown cannot select tools, alter policy, write
memory directly, or change lifecycle control. Parse them into bounded schemas, preserve provenance,
and reject unsupported references or prohibited authority.

## Cancellation reaches quiescence

Cancellation and teardown complete only after owned tasks, subprocesses, transactions, and file
handles have stopped or rolled back. Stop accepting callbacks first, request cancellation second, and
await owned work before returning.

## Use private, explicit filesystem roots

Resolve runtime paths from validated configuration. Create sensitive temporary state with private
permissions and unpredictable names, refuse traversal outside the configured root, and unlink links
without following their targets. Source directories never double as runtime storage.

## Clear ambient repository context before nested Git

Git hooks export repository-local variables such as `GIT_DIR`, `GIT_WORK_TREE`, and `GIT_INDEX_FILE`.
After a hook finishes its own ref checks, unset every name reported by
`git rev-parse --local-env-vars` before invoking a gate that can run Git against temporary or foreign
repositories. A changed working directory cannot override those variables; leaking them can redirect
fixture commits, indexes, or refs into the caller's repository.

## Revalidate mutable external references at handoff

When approval pins a mutable external reference, resolve it before effects and again after the final
API read-back, immediately before reporting success. Stored request or pull-request metadata is a
snapshot and cannot prove that a branch, tag, version, or other live reference remained unchanged.
Treat a missing or different value as invalidation of the pinned evidence and fail closed before
handoff.
