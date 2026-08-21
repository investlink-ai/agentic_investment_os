---
name: create-issue
description: Draft, validate, and create one or more actionable GitHub issues for agentic-investment-os. Use when asked to create, open, or file a standalone issue or a caller-defined issue set, and as the issue-format and publication contract delegated by plan-stage-issues. This skill selects the repository template, traces active authority, checks duplicates and existing metadata, previews exact writes for approval, and safely resumes partial publication. It does not select a capability frontier, design a stage graph, start worktrees, implement issues, or create pull requests.
---

# Create an Issue

Turn a requested outcome into one issue-sized delivery contract. For a caller-defined set, apply the
same contract to every issue while the calling session retains graph and scope ownership.

## Establish the issue scope

1. Read `AGENTS.md`, [the issue-authoring workflow](../../../docs/development.md#issue-authoring),
   `.github/AGENTS.md`, and every file in `.github/ISSUE_TEMPLATE/`.
2. Resolve the repository from the checkout or an explicit user target. Confirm authenticated issue
   write access through the available GitHub connector or `gh`; preserve the selected account.
3. Read the active document that owns the requested outcome. Follow the conditional reading rules in
   `AGENTS.md`, including relevant ADRs and proposed or implemented Agent Notes. Treat research files
   as input rather than implementation authority.
4. Inspect enough code and tests to distinguish missing behavior from shipped behavior. Search open
   and closed issues using the outcome, requirement identifiers, domain terms, and likely title
   variants. Reuse or report an existing issue whose scope already owns the outcome.
5. Read the repository's current labels and milestones. Select only exact existing names. Treat
   creating, renaming, or deleting label, milestone, or project definitions as a separate workflow
   requiring its own preview and authority.

The scope is established when its authority and current implementation state are known, no existing
issue owns it, and each proposed issue has an explicit implementation or coordination role.

## Select the issue contract

Use the repository template whose meaning matches the work:

- `feature.md`: add or intentionally change observable behavior, including agent-visible behavior;
- `bug.md`: correct observable behavior that already has an active expectation;
- `research.md`: answer a bounded decision-relevant question with a stated evidence standard; and
- `task.md`: complete bounded delivery work that is neither behavior change, defect, nor research.

Write an outcome-oriented title without stage, priority, or type prefixes. Complete the selected
template rather than inventing a parallel format, remove its frontmatter, and leave no prompt text or
empty field. Make the first sentence state the observable result or decision-relevant question.

For every issue:

- cite an exact active requirement or state that no active requirement applies;
- make each acceptance criterion observable and name the expected evidence without claiming it
  already exists;
- include applicable invalid, refusal, stale-input, retry, idempotency, or fail-closed behavior;
- state the authority and financial-safety impact, including `unchanged` when that is the result;
- represent prerequisites through approved native GitHub relationships when available, and repeat
  them in prose only when the repository lacks a relationship surface;
- state adjacent work a reviewer could reasonably mistake as included; and
- apply only labels whose current repository meaning matches the issue.

For a standalone issue or child, keep one pull-request-sized outcome. Split outcomes that can be
delivered or accepted independently; combine scaffolding that has no observable value without its
consumer. The implementation contract is ready when an implementer can determine done from the issue
alone while resolving all deeper rules through its links.

For a coordination parent supplied by `plan-stage-issues`, require the capability outcome, authority,
stage-completion evidence, child inventory, and explicit stage boundaries. Do not require the parent
to fit one pull request or authorize direct implementation; its children own implementation-ready
contracts. Keep priority, status, and assignee choices out unless the user supplies them or the
repository has an explicit policy owner.

## Validate an issue set

Require the calling session to supply a short local key, type, outcome, and direct relationships for
every issue in a set. Use `plan-stage-issues` when those decisions must be derived from requirements
or architecture; this skill validates but does not invent that graph.

Draft every issue before publishing any of them. Reject overlapping outcomes, dependency cycles,
missing relationship targets, and preference-only or transitive blocker edges. Order the final set
topologically. Use local keys in the preview for relationships whose GitHub numbers do not exist yet;
replace each key with the verified issue number when publishing downstream issues.

The set is ready when every issue passes its role-specific contract, the relationships are consistent
and acyclic, and the caller still owns the complete requested outcome.

## Preview and authorize

Inspect every proposed title, body, attachment, and metadata value for secrets, credentials, account
identifiers, local ledgers, generated financial research, or other prohibited runtime state. Refuse
publication and report the unsafe source without echoing its contents; user approval cannot override
this boundary.

Show the complete proposed write set before changing GitHub:

- repository and authenticated account;
- ordered issue key, type, exact title, complete body, labels, milestone, and assignees; and
- publication order and native relationships.

Report any requested repository-metadata definition change separately as unapproved. State that no
issue has been created and request explicit approval of the exact issue preview. Reuse an approval
obtained by a calling skill only when it covered the same complete preview. Treat edits as a new
preview requiring approval. Approval authorizes the listed issue writes, including assignment of
existing metadata. It does not authorize metadata-definition changes, worktrees, implementation,
branches, commits, pushes, pull requests, or issue closure.

The write set is authorized only after the user approves the latest complete preview.

## Publish and verify

1. Repeat the prohibited-data inspection, then re-check open and recently closed matches plus current
   native relationships immediately before the first write. Reuse an open issue only when its outcome,
   acceptance scope, and relationships require no material rewrite. Treat a matching closed issue as
   implementation evidence; return it to the calling workflow for scope or frontier reevaluation
   rather than reopening or duplicating it. Stop for a new preview and approval when any match or
   relationship materially changes the write set.
2. Create issues in topological order through structured GitHub connector arguments when available.
   For a `gh` fallback, pass each complete body through `--body-file` using a unique temporary file
   outside the repository, then remove the file as soon as the command returns on success or failure;
   never interpolate issue content into a shell command. Create a parent before its children and
   resolve every local relationship key to a verified issue number before creating the dependent
   issue.
3. Read each created or reused issue back after its write or reuse decision. Record its number and URL
   only after its title, body, labels, milestone, assignees, state, and native relationships match the
   approved preview at that point.
4. On an error or uncertain response, stop the batch. Search for the exact title and distinguishing
   body content before retrying; never create a replacement while the prior outcome is uncertain. If
   the issue exists, compare every approved field and relationship, apply only missing already-
   approved mutations, read it back, and then record its receipt. Stop for a new preview and approval
   when any existing value conflicts with the approved write set.
5. Resume a partial batch from verified receipts, reconciled existing issues, and a fresh duplicate
   search. Preserve created issue numbers, create only missing approved entries, and ask again if the
   remaining write set changes.
6. After the last mutation, read every created and reused issue plus the completed native relationship
   graph again, immediately before handoff. Require all approved fields, states, hierarchy, and
   blockers to match; treat a missing or different value as invalidated evidence and stop without
   reporting publication success.
7. Report created, reused, skipped, failed, and uncreated issues with their dependency order. Hand an
   individual implementation issue to `start-issue-worktree` only when the user separately asks to
   begin its work.

Publication is complete when the final live issue set and graph match the approved preview, every
incomplete item has an explicit disposition, and the report enables a retry without duplicate
creation.
