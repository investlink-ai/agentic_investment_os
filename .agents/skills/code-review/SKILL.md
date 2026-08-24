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

The contract is pinned only when the base, head, commit list, Spec source or explicit absence, and
Standards sources are immutable inputs to the reviewers.

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
[investment-safety-review](../investment-safety-review/SKILL.md) independently when that skill's
description matches the same pinned diff.

## Substantiate and disposition

1. Reproduce or trace each candidate finding against the pinned diff and assign exactly one delivery
   disposition without changing its severity:

   - `disproved` requires concrete evidence that the claimed condition or consequence does not exist;
   - `out_of_scope` requires evidence that the condition was pre-existing and the diff neither
     introduces, worsens, relies on, nor makes it newly reachable;
   - `must_fix` applies to every Blocker or High finding, every reachable Medium correctness, safety,
     or maintainability defect, and every unmet acceptance criterion, required gate, explicit
     repository obligation, or safety invariant regardless of severity; and
   - `advisory` is limited to a Low judgment call that violates no documented obligation, acceptance
     criterion, gate, or safety invariant.

   Precedence matters: an explicit obligation cannot become advisory because its estimated impact is
   Low. Keep unresolved evidence or reviewer uncertainty explicit; do not use it to manufacture a
   pass.
2. Preserve the axes under separate `Standards` and `Spec` headings. Order findings by severity within
   each axis, without merging or reranking findings across axes. For each finding report its stable
   identifier, axis, severity, rule or requirement, reachable consequence, and disposition with the
   evidence supporting that disposition.
3. State questions, assumptions, skipped axes, advisories, and residual risks under their owning
   axis. An axis may pass with advisories but cannot pass with a `must_fix` finding.
4. End with finding and disposition counts and the highest severity within each axis. Passing one axis
   never implies that the other passed.

## Verify a remediation batch

For a caller-requested verification round, keep the original finding ledger and identifiers. Inspect
the pinned remediation delta and the complete current diff, verify every correction and regression
test, and report all remaining findings as one batch. Do not reissue an equivalent finding under a new
identifier.

A novel post-initial finding can restart blocking remediation only when it is Blocker or High, proves
an unmet explicit obligation or safety invariant, or results from evidence that was materially
unavailable to the initial review. Other new Low judgment calls are advisory. A regression introduced
by remediation is not pre-existing and follows the normal disposition rules. The caller owns the
round budget and decides whether another remediation round is authorized.

The review is complete when every reported finding is tied to the pinned diff and its owning standard
or requirement, every finding has an explicit delivery disposition, and both axes have an explicit
pass, pass-with-advisories, or must-fix disposition.
