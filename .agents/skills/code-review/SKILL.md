---
name: code-review
description: Review a committed agentic-investment-os diff against repository Standards and its originating Spec as independent axes. Use for non-empty pull requests or requested branch, range, or pull-request reviews. General review does not replace investment-safety-review.
---

# Review Code Against Standards and Spec

Pin one diff, send its Standards and Spec questions to separate reviewers, and report both results
without allowing one axis to mask the other.

## Pin the review contract

1. Resolve the exact base and head commits. Prefer a user-supplied fixed point, a verified pull-request
   base, or the merge base of the current issue branch and `origin/main`, in that order.
2. Record `git diff <base>...<head>` and `git log <base>..<head> --oneline`. Stop on an unresolved ref
   or empty diff; never let reviewers inspect a moving symbolic range.
3. Resolve the Spec source:

   - for an `issue/<number>-<slug>` branch, read that exact GitHub issue and its applicable authority
     links;
   - otherwise use a user-supplied specification or path;
   - if no Spec exists, record that limitation and skip only the Spec axis.

4. Resolve Standards sources from `AGENTS.md`, instructions governing changed paths, active engineering
   documentation, and relevant implemented or proposed Agent Notes. Include `.github/AGENTS.md` for
   pull-request workflow changes.
5. Read every changed file and enough callers, consumers, tests, and owned documentation to give both
   reviewers a self-contained brief.
6. Record the review plan before invoking or reusing a reviewer: pinned base and head, Spec or its
   explicit absence, applicable Standards, selected axes and reasons, authority and blast-radius
   surfaces, affected consumers, review mode, review epoch, and evidence that invalidates the plan.
   Standards is always selected for a committed non-empty diff. Select Spec whenever one exists;
   absence skips only that axis. The caller separately records why investment safety is selected or
   not applicable.

The contract is pinned only when these review-plan inputs and the commit list are immutable inputs to
the reviewers. The first review in an epoch is `full` for every selected axis.

## Run independent reviewers

Spawn the Standards and Spec reviewers as parallel subagents with no shared review conclusions. Give
both the pinned commits, diff command, commit list, repository root, and a read-only instruction.

Require reviewers to return one complete initial batch rather than stopping after the first defect.
Give every candidate a stable axis-prefixed identifier (`STD-###` or `SPEC-###`) that remains unchanged
across verification rounds, plus one severity. Use **Blocker** when the diff is unsafe or impossible to
review or publish as pinned, **High** for incorrect required behavior or a likely runtime failure,
**Medium** for a reachable edge case or maintainability defect likely to cause incorrect change, and
**Low** for a bounded conformance, documentation, test-evidence, or judgment-call concern. Severity
prioritizes repair; the separately assigned delivery disposition determines whether handoff blocks.

### Standards axis

Give the Standards reviewer the exact Standards-source paths and require it to inspect the files.
Require findings for documented-standard violations plus these judgment-call smells when tooling does
not already enforce them:

- mysterious names and unclear ownership;
- duplicated policy, source-of-truth copies, and shotgun edits;
- speculative generality, unnecessary indirection, pass-through modules, and compatibility shims;
- weak validation, error, retry, or external-effect boundaries;
- tests coupled to private layout rather than observable behavior; and
- stale prose, unresolved references, or mixed workflow responsibilities.

Repository rules override a smell heuristic. Require each finding to state its stable identifier and
severity, distinguish a documented breach from a judgment call, cite the governing rule and exact
`file:line`, explain the reachable consequence, and propose the smallest correction. Limit the report
to 400 words.

### Spec axis

Give the Spec reviewer the issue or specification contents, not merely its location. Require findings
for missing or partial requirements, incorrect behavior, scope creep, and acceptance criteria without
observable evidence. Each finding states its stable identifier and severity, cites the exact
requirement and `file:line`, explains the mismatch, and proposes the smallest correction. Limit the
report to 400 words.

Do not ask either reviewer for an investment-safety verdict or select the supplemental safety review
from this skill. Its caller applies
[investment-safety-review](../investment-safety-review/SKILL.md) independently when the caller's
review plan selects that axis for the same pinned diff.

## Substantiate and disposition

1. Reproduce or trace each candidate finding against the pinned diff. Preserve severity, then assign
   exactly one merge disposition and one automation action:

   - `disproved` requires concrete evidence that the claimed condition or consequence does not exist;
   - `out_of_scope` requires evidence that the condition was pre-existing and the diff neither
     introduces, worsens, relies on, nor makes it newly reachable;
   - `must_fix` applies to every Blocker or High finding, every reachable Medium correctness, safety,
     or maintainability defect, and every unmet acceptance criterion, required gate, explicit
     repository obligation, or safety invariant regardless of severity; and
   - `advisory` is limited to a Low judgment call that violates no documented obligation, acceptance
     criterion, gate, or safety invariant.

   Automation actions are `fix_in_batch`, `human_review_required`, `track_follow_up`, or `none`.
   `fix_in_batch` is available only while an authorized remediation round can safely correct the
   finding without widening scope. `human_review_required` preserves a blocking disposition when the
   round cap, uncertainty, or smallest safe correction requires a human decision. `track_follow_up`
   is limited to an independent pre-existing condition that the diff does not introduce, worsen, rely
   upon, or make newly reachable, or to a non-contractual advisory improvement. Follow-up tracking
   never changes merge disposition and requires separate authority to publish an issue.

   Precedence matters: an explicit obligation cannot become advisory because its estimated impact is
   Low or its correction is large. Keep unresolved evidence or reviewer uncertainty explicit; do not
   use it to manufacture a pass.
2. Preserve the axes under separate `Standards` and `Spec` headings. Order findings by severity within
   each axis, without merging or reranking findings across axes. For each finding report its stable
   identifier, axis, severity, rule or requirement, reachable consequence, evidence, merge
   disposition, automation action, residual risk, and follow-up reference or explicit absence.
3. State questions, assumptions, skipped axes, advisories, and residual risks under their owning
   axis. An axis may pass with advisories but cannot pass with a `must_fix` finding.
4. End with finding, disposition, and action counts and the highest severity within each axis. Passing
   one axis never implies that the other passed.

## Verify a remediation batch

For a correction batch that addresses only recorded findings and changes no scope, authority,
reviewer contract, review selection, public interface, dependency, schema, configuration,
persistence behavior, external effect, blast-radius surface, or affected consumer, use `incremental`
verification from the affected axes. Keep the original ledger and identifiers, inspect the pinned
remediation delta, verify every correction and regression test, and scan the complete current diff for
remediation regressions. A changed HEAD alone does not make unrelated axes repeat full review.

Use `full` review from every affected axis when the Spec, applicable Standard, reviewer contract,
review selection, public interface, dependency, schema, configuration, persistence behavior,
authority path, external effect, blast-radius surface, affected consumer, or unresolved semantic
uncertainty changes. Record which invalidator selected each full axis. Clean equivalence and focused
manual-conflict review retain their separate fail-closed evidence contracts.

Keep one review epoch across incremental and full affected-axis passes: preserve its ledger,
identifiers, novel-finding rules, and delivery-wide round count. A material reviewer-contract change
starts a new epoch with an explicit reason, but cannot reset the delivery's two-round autonomous
budget or expand its scope.

A novel post-initial finding can enter automated remediation only when it is Blocker or High, proves
an unmet explicit obligation or safety invariant, or results from evidence materially unavailable to
the initial review. Other new Low judgment calls are advisory. Classify a new Medium finding normally;
when it remains `must_fix` but another autonomous round is not authorized, assign
`human_review_required` without converting it to advisory. A regression introduced by remediation is
not pre-existing and follows the normal disposition rules. The caller owns the round budget.

After each pass, emit a bounded, non-authoritative summary: reviewer model and effort when exposed,
reviewer-contract identity, per-axis elapsed time, candidate, disposition, and action counts,
remediation rounds used, novel-finding reasons, and any cap or escalation reason. This telemetry does
not approve the diff and is not a persistent review cache.

The review is complete when every reported finding is tied to the pinned diff and its owning standard
or requirement, every finding has an explicit merge disposition and automation action, and both axes
have an explicit pass, pass-with-advisories, or must-fix disposition.
