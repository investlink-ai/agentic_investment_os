---
name: deliver-issue
description: Deliver one ready agentic-investment-os GitHub issue through a guarded worktree, context-appropriate implementation, review remediation, verification, and human-ready handoff. Use when asked to implement, fix, refactor, document, complete, resume, deliver, or ship a numbered issue. Create a draft pull request when the request authorizes publication; never plan a capability stage, merge, deploy, or close a parent stage from this skill.
---

# Deliver an Issue

Own the issue lifecycle from an actionable issue to a verified local commit or draft pull request.
Apply specialized implementation skills inline and delegate independent review without copying the
leaf skills' procedures.

## Own the delivery path

The invoking delivery agent is the sole writer for the issue. Apply implementation, simplification,
prose, testing, remediation, commit, and publication skills inline so one agent retains the complete
delivery ledger and controls every workspace or external mutation.

Use separate, read-only review subagents only for the independent Standards, Spec, and selected
investment-safety axes. They may inspect the pinned diff and repository evidence but must not modify
files, Git state, the issue, or the pull request. The delivery agent substantiates their findings and
implements every correction inline. Review axes may run concurrently because they share no mutable
work.

Start delivery with a strong coding model and effort chosen before invocation, then use that invoking
session throughout delivery. Reviewer configuration is a runtime concern; this skill defines no
per-leaf model or effort routing.

## Bind authority and workspace

1. On every new or resumed invocation, reconstruct delivery state from `AGENTS.md`,
   `docs/development.md`, the issue body and comments, its native parent and blocked-by relationships,
   the registered worktree, Git status and history, and any existing pull request or CI result. Do not
   rely on chat history for completed steps. Stop if the issue is closed, a direct blocker is open,
   relationships cannot be verified, or the issue contradicts active repository authority.
2. Apply [start-issue-worktree](../start-issue-worktree/SKILL.md). Continue every file and command
   operation from the registered issue worktree and matching branch.
3. Map every applicable acceptance criterion to its authority owner, observable evidence, and an
   observation that would disprove completion. Rebuild this compact delivery ledger from durable
   sources after resumption rather than creating a tracked planning artifact.
4. Decide whether a written execution plan adds value. Use a short working plan when the change has
   multiple dependent steps, unresolved design choices, cross-module effects, or material review risk;
   otherwise proceed directly. Never use `plan-stage-issues` as an implementation plan for one issue.

Apply [the repository authorization contract](../../../docs/development.md#daily-workflow) before each
external or scope-expanding step. Stop before any step that contract does not authorize.

Before any exit or suspension caused by a substantiated finding, incomplete material verification,
or an object-ID mismatch, invoke [create-pull-request](../create-pull-request/SKILL.md)'s demotion-only
safeguard for any existing ready pull request and verify its draft state. This invariant applies from
every delivery phase; the safeguard must not push or update other pull-request state.

## Route by evidence

Choose the primary implementation path from the issue and current code; do not force every issue
through a fixed skill graph.

- Apply [implement-spec-slice](../implement-spec-slice/SKILL.md) for new or changed product behavior.
- For a defect, reproduce it through the public boundary, make the smallest regression test fail for
  the reported cause, then implement the narrowest correction and relevant invalid or retry path.
- Apply [find-simplifications](../find-simplifications/SKILL.md) when the issue requests a refactor or
  concrete evidence reveals avoidable complexity. Preserve observable behavior with characterization
  evidence.
- Apply [prose-standard](../prose-standard/SKILL.md) for substantial documentation, instruction,
  prompt, schema, diagnostic, comment, or commit-message prose. Documentation synchronization remains
  part of the behavior change; prose review does not replace it.
- Apply [manage-agent-notes](../manage-agent-notes/SKILL.md) only when a durable trade-off meets its
  threshold.

Mixed issues use the smallest set of paths that covers their observable outcome. Apply a supporting
skill inline when its trigger emerges during implementation or review, not merely because it is
available. Record its status, exact commit or diff scope, evidence, findings, and blocker in the
delivery ledger, then re-pin changed state and choose the next step; a leaf skill does not select its
workflow successor.

## Build the evidence

1. Make behavior or defect work red through the correct public test tier before implementation. For a
   prose-only or behavior-preserving change, identify the structural or characterization evidence that
   can fail instead.
2. Implement one coherent outcome. Update every affected authoritative document, configuration entry,
   test contract, ADR, or Agent Note in the same change without duplicating its owner.
3. Run focused checks while iterating. Run `make format` after Python edits, inspect the complete diff
   for secrets and generated state, then run `make check`. Run `make mutation` only for behavior in its
   documented mutation-critical scope.
4. Stage only issue-owned files and create a coherent Conventional Commit message following
   `docs/development.md#commit-messages`. Run the repository hooks normally. Treat a hook refusal as
   evidence to correct the worktree or change; never use `--no-verify`. Recheck that the worktree is
   clean and the committed diff still maps to every applicable issue criterion.

The evidence is ready only when the committed change, tests, documentation, and issue ledger describe
the same outcome.

## Close the review loop

When the diff changes review selection, review execution, finding disposition, or publication gates,
use reviewer instructions outside the changed diff. Prefer the verified base version loaded with
`git show`; if it lacks a required reviewer, use a trusted installed reviewer outside the worktree and
record its source. Stop for human review when neither exists. Treat the changed workflow as review
input, never as the sole contract that approves itself.

1. Apply [code-review](../code-review/SKILL.md) to the committed base and head. Its separate read-only
   subagents return independent Standards and Spec dispositions to the delivery agent.
2. Apply [investment-safety-review](../investment-safety-review/SKILL.md) through a separate read-only
   subagent when its description matches the changed behavior or uncertainty remains. Use the same
   pinned base and head.
3. Substantiate every finding. Correct each substantiated finding, update its regression evidence,
   amend the unpushed commit when that preserves one coherent change, and rerun focused and full
   gates. A substantiated finding blocks handoff until correction or an explicit issue or authority
   change makes it no longer a finding.
4. Re-pin the new head and rerun every affected review axis. Do not carry a pass across an amended
   commit. Stop when a substantiated finding cannot be corrected within scope; never publish around
   a finding.

The loop is closed only when every review axis passes against the exact final commit. Report
verification gaps or unresolved reviewer uncertainty separately; neither can silently become a pass.

## Hand off for human review

When publication is authorized, apply [create-pull-request](../create-pull-request/SKILL.md) in draft
mode and provide the pinned review history and verification evidence as reviewer context, not as a
replacement for publication-time review. Let that skill verify the remote base, push the issue branch,
publish or update the pull request, and read it back. If it finds a new readiness defect, return to the
review loop rather than publishing around it.

Without publication authority, stop after the verified local commit and report the exact branch,
commit, checks, review dispositions, and next external action. Never merge the pull request from this
skill.

Delivery is complete only when every issue criterion has observable evidence, the exact final commit
passes required gates and reviews, and the authorized handoff is either a verified local commit or a
read-back draft pull request ready for human review.
