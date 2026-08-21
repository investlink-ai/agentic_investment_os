# ADR 0002: Complete numbered issues through draft pull requests

- Status: Accepted
- Date: 2026-08-22

## Context

The repository already binds implementation to one issue branch and linked worktree, and requires
pinned review, verification, and pull-request read-back. Its authorization contract nevertheless
treated implementation and draft publication as separate requests. A completed issue could therefore
stop on a local commit with no review surface, leaving an unowned triage step between implementation
and the pull request that the issue workflow requires.

Publication is an external effect, but a draft pull request neither merges code nor deploys the
system. The boundary must make routine delivery complete without allowing an issue request to imply
broader repository or operational authority.

## Decision

Treat a request to work on a numbered issue as authority for its complete guarded delivery path:
linked-worktree implementation, commit, required review and verification, issue-branch push, and
creation or update of a draft pull request. An explicit local-only or no-publication restriction
narrows the handoff to the verified commit. A coding request without a numbered issue grants no
GitHub publication authority.

`deliver-issue` remains the single delivery orchestrator and sole writer. It applies implementation
and remediation inline while independent Standards, Spec, and applicable investment-safety reviewers
inspect pinned commits read-only. `create-pull-request` owns publication-time review, remote base and
head verification, issue linkage, draft creation, and GitHub read-back. A changed review or
publication gate is judged under the verified base contract so it cannot approve itself.

On every entry, the orchestrator reconstructs progress from the issue, registered worktree, Git
history, reviews, and pull request rather than treating conversation history as delivery state. Leaf
skills retain their specialized procedures, and model or effort selection remains runtime
configuration instead of a per-leaf routing policy.

Publication binds the reviewed base and head commits to the remote branch and pull request by object
ID. A substantiated finding, incomplete verification, or object-ID mismatch blocks publication and
demotes an existing ready pull request before handoff. The issue request does not authorize marking a
pull request ready, merging, deploying, closing issues or stages, publishing issue graphs, or cleaning
up worktrees.

## Alternatives considered

- Requiring a second publication request was rejected because it leaves completed work undiscoverable
  on a local branch and makes draft creation an unowned triage step.
- Letting `create-pull-request` implement or remediate changes was rejected because a publication
  boundary should not own source edits.
- Encoding the workflow in `AGENTS.md` was rejected because detailed conditional policy would burden
  every session and duplicate its active owner.
- Creating separate feature, bug, refactor, and documentation orchestrators was rejected because
  issue evidence can select those paths and a mixed issue would still require a common owner.
- Delegating implementation to model-tiered subagents was rejected because one delivery ledger and
  one writer avoid shared-worktree mutation and provenance conflicts; only immutable review work
  benefits from independent contexts.
- Building a deterministic workflow engine was rejected because implementation and review routing
  require semantic judgment; scripts and hooks continue to own repeatable Git safeguards.

## Consequences

- A numbered-issue request normally ends at a verified draft pull request without another prompt.
- An explicit local-only request preserves a no-publication path.
- Human control begins at draft review and remains required for every broader external effect.
- The delivery agent serializes source, Git, and GitHub mutations while independent reviewers remain
  read-only.
- A resumed session rebuilds its delivery ledger from durable repository and GitHub state.
- A direct pull-request request remains supported and uses the same publication-time evidence gates.
- A changed commit invalidates prior review and verification, and mutable remote refs are re-resolved
  immediately before handoff.
