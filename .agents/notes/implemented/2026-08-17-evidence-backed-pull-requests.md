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
review. A caller may supply review results only when they pin the exact base and head. Passing either
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
a draft when material verification is incomplete.

## Verification

The review skills are validated with the skill validator. The selection rule classifies changes to
review routing and model-visible safety contracts as safety-sensitive while keeping unrelated
mechanical changes conditional on reachable behavior. `make check` verifies the template, skills,
documentation, and harness integration with the repository's normal gate.
