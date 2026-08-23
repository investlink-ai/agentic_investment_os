# Agent Note: Make pull requests evidence-backed review handoffs

Status: implemented

## Problem

The prior pull-request template asked whether broad checks passed but did not show that the delivered
behavior was correct or complete. It also mixed outcome, safety, documentation, and issue metadata
without guiding a reviewer from acceptance criteria and changed invariants to evidence. A longer
template can fix omissions while creating a second copy of the issue or rewarding verbose authoring
history.

## Decision

[The pull-request template](../../../.github/pull_request_template.md) uses four review-owned sections:
`Outcome`, `Blast radius`, `Verification`, and `Out of scope`. The issue-closing relationship is placed
at the top, with an optional stacked-parent relationship immediately below it.

`Outcome` states the observable final behavior and only the material implementation choice needed to
understand it. It replaces `What and why` because the issue remains authoritative for the original
problem and acceptance criteria. A separate `Decision delta` section is not required for every pull
request; financial or authority changes are the first mandatory surface under `Blast radius` so a
material decision change cannot disappear, while documentation and tooling changes avoid an empty
domain-specific section.

`Verification` maps every applicable issue criterion and changed contract or invariant to a test,
command, inspection, or artifact and its observed result. It includes relevant negative or refusal
paths and names what remains unverified. Passing `make check` or another command is supporting evidence,
not a completeness claim by itself.

[The pull-request skill](../../skills/create-pull-request/SKILL.md) owns base and issue resolution,
final-diff inspection, review-readiness enforcement, evidence collection, publication, and read-back
verification. Every pull request receives the general Standards and Spec review. The independent
`investment-safety-review` owns its behavioral selection boundary; uncertainty selects the safety
review. A complete handoff from the active issue-delivery session may satisfy publication review and
verification only when the skill independently revalidates its exact current base, head, issue scope,
clean worktree, review selection, and immutable trusted instruction identity for every review axis.
When a base update changed the refs, the handoff also carries the same-session equivalence or focused-
conflict evidence owned by
[the review-remediation decision](2026-08-23-bound-review-remediation-and-evidence-reuse.md). Direct
requests and incomplete or reconstructed evidence execute the publication-time gates. Passing either
review does not imply that the other passed.

Ordinary feature pull requests target `main`; stacked pull requests target their unmerged parent and
are retargeted to `main` after it merges. Because `main` is the default branch, GitHub activates
closing keywords when an ordinary feature pull request merges.

## Alternatives considered

- Keeping the checkbox template was rejected because a successful command does not prove that each
  acceptance criterion, changed invariant, and forbidden effect was exercised.
- Adding both `What and why` and `Decision delta` was rejected because most of their content belongs in
  the issue or `Blast radius`, and requiring both encourages duplication and empty prose.
- Using only a summary and test plan was rejected because this system needs explicit visibility into
  authority, durable state, and external effects before merge.
- Folding investment safety into the general code review was rejected because whole-spec and
  repository-conformance findings have different scope and severity semantics from reachable
  authority, financial, provenance, and durability failures.
- Requiring the investment safety review for every mechanical change was rejected because it adds no
  assurance when behavior cannot reach a safety-sensitive surface. The general review remains the
  default, while uncertainty about behavioral reach selects the safety review.
- Repeating exact-commit review and verification during publication was rejected because the delivery
  orchestrator has already closed those gates. Reuse still requires the complete active-session
  handoff contract: matching issue or scope, exact current object IDs, clean worktree, review
  selection, verification results, immutable reviewer identity for every axis, and any evidence that
  rebound retained review after a base update.
- Persisting a review cache was rejected because same-session handoff evidence is sufficient and a
  durable cache would add another stale authority surface.
- Treating an external fixture claim as same-session evidence was rejected. Model-backed scenarios
  use a hash-pinned harness control input, record its exact materialized digest and substitutions, and
  require a direct ledger read plus repository- and subject-bound live observations of its issue,
  canonical full object IDs, whole-worktree cleanliness, and both general and safety reviewer digests
  before accepting reuse or rejecting a mismatch.
- Treating a reviewer mismatch as harmless while a ready pull request remains mergeable was rejected.
  Reviewer-identity invalidation invokes the same demotion-only safeguard as a stale object ID on the
  pull request resolved from the expected issue branch, verifies its draft state afterward, and
  publication independently re-evaluates safety-review selection before reuse.
- Automating body generation in a script was deferred because the work requires semantic inspection
  of the issue, diff, consumers, and evidence; a fixed script would either miss that reasoning or add
  another schema to maintain.

## Consequences

Pull-request authors must provide behavioral evidence rather than check boxes, and reviewers receive a
stable place to assess completeness and the riskiest invariant. Small documentation or harness changes
remain concise by marking inapplicable blast-radius surfaces as `none` or `unchanged`. Feature work
receives a general code review, and most production features receive the supplemental investment
safety review because their behavior reaches a listed safety-sensitive surface. Stacked work carries
an explicit retargeting step to `main` before its closing keyword becomes effective.

The skill does not implement findings, commit, merge, or push protected branches. It stops when
in-scope changes are not committed or a substantiated finding lacks an explicit disposition, and uses
a draft when material verification is incomplete. Pre-push and CI continue to run the full repository
gate independently of any delivery handoff.

## Verification

The review skills are validated with the skill validator. The selection rule classifies changes to
review routing and model-visible safety contracts as safety-sensitive while keeping unrelated
mechanical changes conditional on reachable behavior. `make check` verifies the template, skills,
documentation, and harness integration with the repository's normal gate.
