---
name: deliver-issue
description: Deliver one ready agentic-investment-os issue through its guarded worktree, implementation, verification, independent review, and draft pull request. Use for work on a numbered issue. Does not plan stages, merge, deploy, or close issues.
---

# Deliver an Issue

Own the issue lifecycle from an actionable issue through its authorized review handoff. Apply
specialized implementation skills inline and delegate independent review without copying the leaf
skills' procedures.

## Own the delivery path

The invoking delivery agent is the sole writer for the issue. Apply implementation, simplification,
prose, testing, remediation, commit, and publication skills inline so one agent retains the complete
delivery ledger and controls every workspace or external mutation.

Use separate, read-only review subagents only for the independent Standards, Spec, selected
investment-safety, and manually resolved conflict-equivalence reviews. They may inspect the pinned
diff and repository evidence but must not modify files, Git state, the issue, or the pull request. The
delivery agent substantiates their findings and implements every correction inline. Reviews may run
concurrently when they share no mutable work.

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
4. Before implementing an architecture, domain, configuration, requirements, or workflow change,
   inventory every proposed domain term, durable-state concept, configuration key, interface or
   ownership seam, and external-source assumption. Map each item to an issue criterion and its active
   authority owner. Remove or explicitly defer an ungrounded item before implementation or reviewer
   fan-out, and refresh the inventory when the implementation introduces another item. Keep this
   scope-delta inventory in the delivery ledger rather than a tracked artifact.
5. Decide whether a written execution plan adds value. Use a short working plan when the change has
   multiple dependent steps, unresolved design choices, cross-module effects, or material review risk;
   otherwise proceed directly. Never use `plan-stage-issues` as an implementation plan for one issue.

Apply [the repository authorization contract](../../../docs/development.md#daily-workflow) before each
external or scope-expanding step. Stop before any step that contract does not authorize.

Before any exit or suspension caused by a substantiated finding, incomplete material verification,
an object-ID mismatch, or a reviewer-identity mismatch, invoke
[create-pull-request](../create-pull-request/SKILL.md)'s demotion-only safeguard for any existing ready
pull request and verify its draft state. This invariant applies from every delivery phase; the
safeguard must not push or update other pull-request state.

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

Before initial review, review reuse, or remediation verification, record a review plan in the active
delivery ledger: pinned base and head, available Spec, applicable Standards, selected axes and reasons,
authority and blast-radius surfaces, affected consumers, review mode, review epoch, and invalidation
evidence. Standards is required for every committed non-empty diff. Spec is required when one exists;
an absent Spec skips only that axis and remains an evidence limitation.

Select investment-safety review for reachable or uncertain changes to investment authority,
deterministic portfolio or execution behavior, durability, provenance or time, retry or idempotency,
fail-closed behavior, credentials, hostile-input or external-effect seams, review routing, or another
model-visible safety contract. Record the reason when it is selected or not applicable; uncertainty
selects it.

1. Apply [code-review](../code-review/SKILL.md) to the committed base and head. The first review in an
   epoch is full for every selected axis. Its separate read-only subagents return the complete initial
   Standards and Spec finding batches to the delivery agent.
2. Apply [investment-safety-review](../investment-safety-review/SKILL.md) through a separate read-only
   subagent when the review plan selects it under the preceding safety contract. Use the same pinned
   base and head and require one complete initial safety batch with stable `SAFE-###` identifiers.
3. Substantiate every candidate and preserve its stable identifier, axis, severity, governing rule or
   requirement, reachable consequence, evidence, residual risk, and exactly one merge disposition
   from `must_fix`, `advisory`, `out_of_scope`, or `disproved`. Also assign exactly one automation
   action from `fix_in_batch`, `human_review_required`, `track_follow_up`, or `none`. Apply the
   precedence rules in `code-review`: Blocker, High,
   reachable Medium correctness, safety, or maintainability defects, explicit obligations, acceptance
   criteria, required gates, and safety invariants are `must_fix`; only a Low non-contractual judgment
   call may be advisory.
4. Correct all known `must_fix` findings whose action is `fix_in_batch` as one batch. Use focused
   checks while editing, update regression evidence, amend the unpushed commit when that preserves one
   coherent change, and run the full `make check` gate once after the batch. Re-pin the new head, then
   classify review mode.
   When the batch only corrects recorded findings and changes no semantic surface named below, ask
   each affected axis for incremental verification of the remediation delta and complete current diff
   while retaining finding IDs. A changed HEAD alone does not repeat unrelated full axes.
5. Permit one initial review and at most two remediation-and-verification rounds. The initial batch
   does not consume a remediation round; each semantic correction batch does, regardless of the
   number of findings it contains. A successful review-equivalence check consumes no round. A human
   may explicitly authorize a further round, but elapsed time, model preference, or a still-open
   finding does not extend the budget implicitly.
6. Require full review from every affected axis when the Spec, applicable Standard, reviewer contract,
   review selection, public interface, dependency, schema, configuration, persistence behavior,
   authority path, external effect, blast-radius surface, affected consumer, or unresolved semantic
   uncertainty changes. Preserve clean-equivalence and focused-conflict handling below.
7. Keep the original finding ledger, stable identifiers, novel-finding rules, and delivery-wide round
   count within one review epoch. Start a new epoch only for a material review-contract change and
   record why. A new epoch never resets the two-round autonomous budget or authorizes expanded scope.
8. Apply `code-review`'s novel-finding eligibility after the initial batch without creating a second
   disposition rule in this skill. A regression introduced by remediation is newly introduced and
   follows the normal disposition rules. A late Medium may remain `must_fix` with
   `human_review_required`; round refusal never makes it advisory.

When the smallest safe correction would materially widen the issue or pull request, first revert or
shrink the triggering change. Otherwise stop for human direction to split or expand scope. Never turn
a merge blocker into follow-up work because its correction is large. `track_follow_up` is limited to
independent pre-existing work the diff does not introduce, worsen, rely upon, or make newly reachable,
or to a non-contractual advisory improvement; publishing a follow-up issue requires separate
authorization.

The loop is closed when no `must_fix` finding remains and every required axis has a pass or
pass-with-advisories disposition bound to the exact final commit. `advisory`, `out_of_scope`, and
`disproved` findings remain visible in the handoff but do not block it. Verification gaps and
unresolved reviewer uncertainty cannot silently become a pass. If any `must_fix` finding remains after
the second authorized remediation round, invoke the demotion-only safeguard for an existing ready pull
request, record `human_review_required`, and stop without automatic publication or another review
round. Before stopping, give the human reviewer a capped-delivery handoff containing the complete
round ledger, latest focused and full check results, every unresolved finding record, disposition,
automation action, residual risk, and follow-up routing, the ready-pull-request demotion result when
applicable, and the explicit reason the autonomous budget was exhausted. A capped handoff reports
evidence for human direction; it does not satisfy publication prerequisites.

## Retain review only across proven equivalent changes

After a base update, re-resolve the old and new base and head object IDs before deciding whether a
prior review remains applicable. A clean conflict-free rebase or merge may retain the prior axis
dispositions only when one same-session, pinned equivalence record proves all of the following:

- the semantic patch and changed paths are unchanged;
- issue and active-authority inputs are unchanged;
- trusted installed reviewer digests are unchanged, while a verified-base reviewer has the same
  repository path and Git blob across old and new bases;
- Standards, Spec, and safety-review selection is unchanged;
- blast-radius surfaces and affected consumers are unchanged; and
- required focused checks and the full gate pass on the new exact head.

Record exact old and new base and head IDs, commands and observed results, reviewer identities, scope,
selection, consumers, and checks. For a verified-base reviewer, record both base/path/blob triples;
the base bindings differ by design, so equivalence requires the same repository path and Git blob, and
the retained final axis binds to the new triple. Patch IDs, range-diff output, changed-path comparison,
and absence of a known blast trigger are supporting evidence only; no one signal establishes
equivalence. If the patch, contract, reviewer content, selection, consumer, or safety conclusion
changed—or any required evidence is absent or uncertain—rerun every affected full review axis.

A manual conflict is never review-free. After resolving it, give an independent read-only reviewer the
exact conflict hunks, both sides' intent, intervening base changes, and affected consumers. A passing
focused conflict review may retain prior dispositions for the proven-unchanged remainder; it does not
approve semantic changes inside the resolution. Route changed semantics and any reviewer uncertainty
to the affected full axes. The writer who resolved the conflict cannot be this focused reviewer.

When equivalence succeeds, rebind the retained dispositions to the new exact base and head and record
whether the basis was `clean_equivalence` or `focused_conflict`. This is active-session evidence, not a
persistent cache, and cannot be reconstructed after the delivery context is lost.

After the loop closes, record a bounded handoff in the active delivery ledger: the issue or requested
scope, complete review plan, current reviewed base and head object IDs, initial-review identity,
review epoch, remediation rounds used, every finding record with merge disposition, automation action,
residual risk, and follow-up routing, Standards and Spec dispositions, safety-review selection and
disposition, incremental, focused, and full check results, mutation disposition, and review basis
(`fresh`, `clean_equivalence`, or `focused_conflict`). For each Standards, Spec, and investment-safety
axis, also record the trusted reviewer-instruction source and immutable content identity used for that
axis. Bind
a verified-base reviewer to its base object ID, repository path, and Git blob ID; bind a trusted
installed reviewer to its resolved path and SHA-256 digest. Record the investment-safety contract even
when its selection result is `not selected`. An equivalence basis also carries its complete pinned
record and original reviewed refs. Include bounded review telemetry: reviewer model and effort when
exposed, reviewer-contract identities, per-axis elapsed time, candidate and disposition counts,
rounds, novel-finding reasons, and cap or escalation reason. Telemetry is non-authoritative and is
never persisted as a reusable review cache. This evidence describes only the exact commit established
in this delivery session; an unproved amendment, missing field, unresolved `must_fix` finding, or
changed reviewer identity invalidates it.

## Hand off for human review

When the authorization contract selects publication, apply
[create-pull-request](../create-pull-request/SKILL.md) in draft mode and provide the complete bounded
handoff from the active delivery ledger. Let that skill decide whether the unchanged exact-commit
evidence satisfies its review and verification prerequisites, verify the remote base, push the issue
branch, publish or update the pull request, and read it back. An absent, incomplete, or mismatched
handoff selects that skill's fresh review and verification branch. If a live ref changes after review
or the publication skill finds a new readiness defect, return to the review loop rather than
publishing around it.

When the authorization contract selects a local handoff, stop after the verified commit and report
the exact branch, commit, checks, review dispositions, and omitted external action. Never merge the
pull request from this skill.

Delivery is complete only when every issue criterion has observable evidence, the exact final commit
passes required gates and reviews, and the selected handoff is verified.
