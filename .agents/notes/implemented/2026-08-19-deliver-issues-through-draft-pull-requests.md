# Agent Note: Deliver issues through draft pull requests

Status: implemented

## Problem

The repository's planning, worktree, implementation, review, and pull-request skills each own a sound
local workflow, but no owner carries one ready issue across their handoffs. An implementation session
can stop after tests or require a human to remember which review and publication step comes next.

## Decision

[The `deliver-issue` skill](../../skills/deliver-issue/SKILL.md) is the thin issue-delivery
orchestrator. It binds the issue worktree, selects the primary implementation path from evidence,
builds a committed evidence set, closes the review-remediation loop, and hands the final commit to the
pull-request skill. On every entry it reconstructs progress from the issue, registered worktree, Git
history, reviews, and pull request rather than treating conversation history as delivery state.

Before architecture, domain, configuration, requirements, or workflow implementation, the delivery
ledger maps every proposed term, durable-state concept, configuration key, interface or ownership
seam, and external-source assumption to the issue and its active authority owner. An ungrounded item is
removed or deferred before implementation or reviewer fan-out.

One delivery agent applies implementation and remediation inline and remains the sole writer of
files, Git state, issues, and pull requests. Only independent Standards, Spec, and selected investment-
safety reviews use separate read-only agents. Leaf skills retain their specialized procedures, while
model and effort selection remains runtime configuration rather than a per-leaf routing policy.

[The development workflow](../../../docs/development.md#daily-workflow) treats a request to work on a
numbered issue as authority for the guarded path through a verified draft pull request, with an
explicit local-only escape hatch. Broader repository and operational actions remain human decisions.

A diff that changes review or publication gates is reviewed under the verified base contract or a
trusted reviewer outside the diff. Publication binds the reviewed base and head commits to the pushed
branch and pull request by exact object ID, then re-resolves both live refs after pull-request
read-back. The active delivery ledger may hand its complete review and verification evidence to
publication for the unchanged exact commit when every review axis remains bound to the same immutable
trusted reviewer identity; it is not a durable cache and cannot be reconstructed after its delivery
context is lost. A finding, incomplete verification, or object-ID or reviewer-identity mismatch
demotes an existing ready pull request before the workflow can leave it pointing at affected state.

## Alternatives considered

- Requiring a second publication request was rejected because it leaves completed work undiscoverable
  on a local branch and makes draft creation an unowned triage step.
- Extending `create-pull-request` to implement and remediate changes was rejected because publication
  would own source edits and lose a clear refusal boundary.
- Copying a complete delivery graph into `AGENTS.md` was rejected because every session would pay its
  context cost and conditional rules would acquire multiple owners.
- Creating separate feature, bug, refactor, and documentation orchestrators was rejected because
  issue evidence can select those paths and a mixed issue would still require a common owner.
- Delegating implementation to model-tiered subagents was rejected because one delivery ledger and
  one writer avoid shared-worktree mutation and provenance conflicts.
- Building a deterministic workflow engine was rejected because implementation and review routing
  require semantic judgment; scripts and hooks continue to own repeatable Git safeguards.
- Repeating review and full verification in both delivery and publication was rejected because an
  exact-commit handoff removes duplicate model work without carrying evidence across a changed commit.
- Persisting delivery attestations was rejected because publication needs only the active delivery
  ledger and independently resolved object IDs.

## Consequences

- A numbered-issue request normally ends at a verified draft pull request without another prompt.
- An explicit local-only request preserves a no-publication path.
- Human control begins at draft review and remains required for every broader external effect.
- A changed commit invalidates prior review and verification.
- A resumed session rebuilds its delivery ledger from durable repository and GitHub state.
- Direct pull-request requests and incomplete delivery handoffs execute publication-time evidence
  gates.

## Verification

- `AGENTS.md` exposes one issue-delivery pointer while detailed routing remains in the skill and
  development workflow.
- Skill validation and `make harness` check the orchestrator and its leaf skills.
- `make check` exercises repository hooks and the deterministic handoff gate.
