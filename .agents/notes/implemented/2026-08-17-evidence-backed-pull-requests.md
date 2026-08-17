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

[The pull-request skill](../../skills/create-pull-request/SKILL.md) owns the operational workflow:
base and issue resolution, final-diff inspection, safety review triggers, evidence collection,
publication, and read-back verification. Ordinary feature pull requests target `dev`; stacked pull
requests target their unmerged parent and are retargeted after it merges. Because GitHub activates
closing keywords only for the default branch, the workflow requires `dev` to be the default branch
before claiming that `Closes #N` will take effect.

## Alternatives considered

- Keeping the checkbox template was rejected because a successful command does not prove that each
  acceptance criterion, changed invariant, and forbidden effect was exercised.
- Adding both `What and why` and `Decision delta` was rejected because most of their content belongs in
  the issue or `Blast radius`, and requiring both encourages duplication and empty prose.
- Using only a summary and test plan was rejected because this system needs explicit visibility into
  authority, durable state, and external effects before merge.
- Automating body generation in a script was deferred because the work requires semantic inspection
  of the issue, diff, consumers, and evidence; a fixed script would either miss that reasoning or add
  another schema to maintain.

## Consequences

Pull-request authors must provide behavioral evidence rather than check boxes, and reviewers receive a
stable place to assess completeness and the riskiest invariant. Small documentation or harness changes
remain concise by marking inapplicable blast-radius surfaces as `none` or `unchanged`. Feature work
depends on `dev` being the repository default branch for automatic issue closure. Stacked work carries
an explicit retargeting step before its closing keyword becomes effective.

The skill does not commit, merge, or push protected branches. It stops when in-scope changes are not
committed and uses a draft when material verification is incomplete.

## Verification

The skill structure is validated with the repository skill validator and forward-tested against a
realistic pull-request drafting request. `make check` verifies the template, skill, documentation,
and harness integration with the repository's normal gate.
