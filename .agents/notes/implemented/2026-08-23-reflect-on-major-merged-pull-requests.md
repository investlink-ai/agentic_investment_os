# Agent Note: Reflect on major merged pull requests without creating backlog

Status: implemented

## Problem

Issue delivery and pull-request review establish whether an exact change satisfies its existing
contract, but implementation can reveal recurring assurance gaps, operational friction, or design
costs that were not part of that contract. Leaving those lessons in conversation or review history
loses useful evidence. Turning every merge into a retrospective or issue batch creates ceremony and a
speculative backlog instead.

## Decision

[The development workflow](../../../docs/development.md#post-merge-reflection) provides the optional,
read-only [`reflect-on-merged-pr` skill](../../skills/reflect-on-merged-pr/SKILL.md) after a materially
significant pull request merges and authorized cleanup finishes. Materiality follows authority, risk,
recurring cost, and assurance impact rather than diff size. The skill reconstructs durable merged
evidence, separates confidence from expected return on effort, and may return no action.

Only a small evidence-backed set receives a follow-up disposition. Implementation recommendations
require direct merged evidence, active authority, a stable intervention seam, an independently
verifiable outcome, and a duplicate check. Research, capability-stage planning, Agent Notes,
monitoring, and no action remain distinct dispositions. Reflection never mutates the repository or
GitHub; each later workflow requires a separate explicit request.

## Alternatives considered

- Extend `deliver-issue` through merge and reflection. This was rejected because delivery ends at a
  verified draft pull request while merge and cleanup remain separately authorized human actions.
- Run reflection automatically after every merge. This was rejected because routine changes rarely
  justify the model cost or follow-up pressure, and automatic execution would turn optional learning
  into a delivery gate.
- Repeat code review after merge. This was rejected because review judges the original specification
  and repository standards, while reflection asks whether the implementation exposed new bounded work.
- Put the complete checklist in `AGENTS.md`. This was rejected because the workflow is conditional and
  would add context to unrelated tasks; the root instructions should retain only stable routing rules.

## Consequences

- Significant merges can yield durable, evidence-ranked follow-up decisions after the shipped state is
  known.
- Routine merges and empty categories create no required artifact, issue, or Agent Note.
- Missing, inconsistent, unmerged, or observably incomplete cleanup evidence limits the reflection
  instead of producing a confident recommendation.
- Cleanup, issue publication, stage planning, implementation, and investment authority remain outside
  the reflection workflow.

## Verification

The `merged-pr-reflection-is-read-only` and `incomplete-pr-reflection-is-limited` workflow scenarios
exercise the successful and fail-closed decisions against an exact repository and pull-request
subject. `make harness` validates their contracts and `make check` remains the complete handoff gate.
