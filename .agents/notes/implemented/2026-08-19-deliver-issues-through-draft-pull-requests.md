# Agent Note: Deliver issues through draft pull requests

Status: implemented

## Problem

The repository's planning, worktree, implementation, review, and pull-request skills each own a sound
local workflow, but no owner carries one ready issue across their handoffs. An implementation session
can stop after tests, disclose a review finding without remediation, or require a human to remember
which optional skill and publication step comes next.

## Decision

[The `deliver-issue` skill](../../skills/deliver-issue/SKILL.md) is the thin issue-delivery
orchestrator. It binds the issue worktree, selects the primary implementation path from evidence,
keeps optional simplification and prose work conditional, builds a committed evidence set, closes the
review-remediation loop, and hands an authorized final commit to the pull-request skill in draft mode.
On every entry it reconstructs progress from the issue, registered worktree, Git history, reviews, and
pull request rather than treating conversation history as delivery state.

One strong delivery agent applies every implementation and remediation skill inline and remains the
sole writer of files, Git state, issues, and pull requests. Only the independent Standards, Spec, and
selected investment-safety reviews run in separate read-only subagents. This preserves focused review
contexts without splitting the delivery ledger or creating write contention. Model and effort
selection remains session or runtime configuration rather than a per-leaf routing matrix.

Leaf skills retain their specialized procedures. `implement-spec-slice` owns product behavior,
`code-review` owns independent Standards and Spec axes, `investment-safety-review` owns its safety
scope, and `create-pull-request` owns publication readiness and GitHub read-back. The delivery agent
passes pinned inputs to reviewers and repeats an affected review after the commit changes; publication
reruns required reviews until a durable provenance-bearing receipt can replace that execution. It
does not copy detailed rules into `AGENTS.md` or `docs/development.md`.

A diff that changes review or publication gates is reviewed under the verified base contract or a
trusted reviewer outside the diff. This prevents a proposed workflow from defining the rules by which
it approves itself.

Publication binds the reviewed base and head commits to the pushed branch and pull request by exact
object ID. It re-resolves both live remote refs after pull-request read-back because the object IDs in
pull-request metadata are snapshots, not proof that a mutable ref remained unchanged. A known finding,
incomplete verification, or object-ID mismatch demotes an existing ready pull request before the
workflow can leave it pointing at affected state.

Separating local implementation from publication preserves explicit human control over external
effects without interrupting safe local progress. The current authorization and hook contract lives
in [the development workflow](../../../docs/development.md#daily-workflow).

## Alternatives considered

- Extending `create-pull-request` to implement and remediate changes was rejected because publication
  would own source edits and lose a clear refusal boundary.
- Copying a complete delivery DAG into `AGENTS.md` was rejected because every session would pay its
  context cost and conditional rules would acquire multiple owners.
- Creating separate feature, bug, refactor, and documentation delivery orchestrators was rejected
  because issue evidence can select those paths dynamically and mixed issues would still need a router.
- Delegating implementation to model-tiered subagents was rejected because sequential issue work
  benefits from one context owner and one writer, while shared-worktree writers introduce coordination
  and provenance risk before this small repository has evaluation evidence that routing improves
  outcomes.
- Building a deterministic workflow engine was rejected because implementation and review routing
  require semantic judgment. Existing scripts and hooks continue to own repeatable Git safeguards.

## Consequences

- One entrypoint can take a ready issue to a verified local commit or an authorized draft pull request
  without making optional skills mandatory.
- Review subagents can run concurrently on immutable inputs, but implementation, remediation, Git, and
  publication remain serialized through the delivery agent.
- A changed commit invalidates prior review results, so remediation cannot inherit a stale pass.
- A resumed session rebuilds its delivery ledger from durable repository and GitHub state, so context
  compaction cannot establish completion by itself.
- Direct pull-request requests remain supported; the publication skill runs required reviews against
  the exact final commit.
- Remote-base, remote-head, and pull-request read-back must identify the exact reviewed object IDs,
  then both live refs must be re-resolved immediately before handoff; a branch name or pull-request
  snapshot alone is not proof of the reviewed state.
- Human review remains the merge boundary.

## Verification

- `AGENTS.md` exposes one issue-delivery pointer while detailed routing remains in the skill.
- The skill validator checks the orchestrator and its leaf skills, and `make harness` checks their
  installation.
- `make check` exercises repository hooks and the normal deterministic handoff gate.
