---
name: plan-stage-issues
description: Plan or complete one agentic-investment-os capability stage as a reviewed GitHub issue graph. Use for next-frontier drafting or publication and completed-parent audit. Does not implement issues or plan the full roadmap.
---

# Plan Stage Issues

Turn the nearest unimplemented capability frontier into a small, dependency-correct issue batch. Keep
requirements and architecture authoritative; issues own delivery scope and status, not product truth.

## Select the frontier

1. Read `AGENTS.md`, the implementation sequence in `docs/product-requirements.md`,
   `docs/architecture.md`, and the current code and tests. Follow relevant pointers to domain,
   testing, module, ADR, and Agent Note documents.
2. Separate approved behavior from implementation status. Do not infer that a document, scaffold, or
   proposal is working code.
3. Select the earliest outcome in the active implementation sequence whose predecessor outcomes are
   implemented in code and verified by tests. If several outcomes share that position, choose the
   smallest authority-preserving outcome that unlocks observable downstream progress. Plan only that
   frontier, not every V0 stage or a calendar sprint.
4. Use one direct issue only when one agent can deliver the complete outcome in one coherent
   worktree and pull request without a hidden prerequisite issue. Use a parent whenever two or more
   independently mergeable vertical slices are required. Prefer three to seven children; justify a
   smaller or larger batch in the draft.
5. Add no milestone, project, label scheme, sprint, or release ceremony unless the user requests it.

## Draft tracer-bullet issues

Make each child an independently mergeable vertical result that leaves `main` green. It should own
the success path, applicable refusal or invalid path, documentation and configuration changes, and
observable verification for one public behavior. Do not split tickets by file, class, architectural
layer, or implementation phase.

Reject a graph such as validation -> persistence -> orchestration -> documentation for one behavior:
that is a horizontal implementation plan. Collapse it into one end-to-end issue or split by distinct
public outcomes. Documentation, tests, migrations, and configuration travel with the child whose
behavior requires them.

Require every child to be demonstrable through a lifecycle interface, durable receipt, CLI, or other
boundary visible outside its internal module. Prefer outcome titles that begin with a user or system
action such as start, advance, refuse, reconcile, or rebuild. Reject component-only tickets such as
schema engine, repository, service, types, tests, or docs when they merely name an implementation
layer.

The parent issue owns:

- the capability outcome and authoritative requirement links;
- the stage-completion evidence and child inventory;
- boundaries and work explicitly outside the stage.

Each child issue owns:

- a one-sentence observable outcome;
- `Authority`: exact requirement, architecture, domain, ADR, or policy references;
- `Acceptance criteria`: falsifiable behavior that is absent or fails on the base branch;
- `Verification contract`: the public interface, durable artifact, or operator-visible receipt to
  inspect; the planned command and observable evidence; and the required negative, refusal, retry,
  or safety path;
- `Out of scope`: nearby work intentionally excluded.

For every acceptance criterion, name what observation would disprove completion. Ensure the same
issue owns the implementation needed to satisfy it and does not depend on an unstated future issue.
Reference authoritative prose instead of copying it into the ticket.

Apply [create-issue](../create-issue/SKILL.md) to select and complete the repository template and
role-specific contract for each coordination parent or implementation child. This skill owns frontier
selection, decomposition, and relationships; `create-issue` owns issue validation and the GitHub
mutation safeguards.

Use blocker edges only for real implementation dependencies. Keep the graph acyclic and remove
transitive or preference-only edges. At least one child should normally be immediately unblocked.
If none is unblocked, narrow the stage until a child is executable or identify the missing predecessor
that must be planned first; do not publish a stranded stage.
Order broad migrations as expand, migrate, then contract only when a safe vertical slice cannot avoid
that sequence.

## Review before publishing

Present a numbered draft in topological order. Show the parent, then each child title, outcome,
authority, acceptance criteria, verification contract, exclusions, and direct blockers. Call out the
immediately executable frontier and any existing issue proposed for reuse. Before presenting the
draft, search for open and recently closed issues with overlapping outcomes, authority references, or
parent relationships.

Read the child titles once without their bodies. Redraft any issue whose observable capability and
completion boundary are not clear from its title and one-sentence outcome.

Include the complete issue bodies, metadata, and native relationships required by `create-issue` in
the numbered draft. Ask the user to approve the selected frontier, issue granularity, dependency
edges, and exact write set. Stop after the draft. Do not create or modify GitHub issues until the
user explicitly approves publication.

## Publish the approved graph

Apply [create-issue](../create-issue/SKILL.md) to publish the approved standalone issue or graph. Pass
the unchanged approved preview so it does not ask for redundant approval. Preserve these stage-owned
constraints:

- create the parent before children and children in topological order;
- represent hierarchy and direct blockers with native parent, sub-issue, and blocked-by
  relationships rather than issue prose;
- create no tracked issue-plan artifact, milestone, project, or label scheme; and
- on partial publication, resume from verified native relationships and exact outcomes rather than
  deleting and recreating the graph.

Child pull requests close their child issues. Do not put a closing keyword for the parent in a child
pull request. Close the parent only after every child is closed and its stage-completion evidence has
been audited.

## Audit a completed stage

Reapply this skill to the parent after its children close. Read the parent, every child, their merged
pull requests, and the current code and tests. Map the parent's capability outcome to observable
evidence from the merged state rather than treating closed children as proof by themselves.

Keep the parent open when a child remains open, an acceptance path is missing, or the integrated
behavior contradicts active authority. Report the exact gap and draft a corrective child through the
same review-before-publishing gate; do not reopen or silently widen a completed child. When the outcome
is satisfied, report the audit result without mutating GitHub. Add the evidence comment and close the
parent only when the user explicitly asked to close or complete the stage, or approves that action
after the audit. Read a closed parent back and verify its final comment, child inventory, and state.

## Completion criteria

Planning is complete only when the approved stage—and no speculative later stage—is represented by
an accurate native issue graph, every parent and child passes its role-specific `create-issue`
contract, and the unblocked frontier is explicit. Stage completion is complete only when the merged
outcome is audited against the parent, the evidence is recorded, and the parent's final state is
verified. Report issue URLs, direct dependencies, and any mismatch or unpublished item.
