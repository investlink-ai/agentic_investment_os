---
name: create-pull-request
description: Create or update a concise, evidence-backed GitHub pull request for agentic-investment-os. Use when asked to open, create, raise, draft, publish, or revise a pull request for the current branch. This skill resolves the correct base, preserves issue and stacked-PR relationships, derives blast radius from the final diff, and records behavioral verification rather than test-pass checkboxes. It does not commit changes or merge the pull request.
---

# Create a Pull Request

Create the review handoff for the branch's final state. Keep the issue as the owner of the original
problem and acceptance criteria; the pull request owns the resulting delta, review risk, and evidence.

## Establish repository and branch state

1. Read `AGENTS.md`, `.github/AGENTS.md`, `.github/pull_request_template.md`, and any instructions
   governing changed files. Read applicable requirements, ADRs, and Agent Notes before interpreting
   the diff.
2. Run `gh auth status` and query the repository, viewer permission, and default branch with `gh`.
   Require write, maintain, or admin permission before publishing. Report an account or permission
   mismatch; do not switch the active GitHub account implicitly.
3. Fetch the remote base, verify that it exists, and resolve the current branch, its upstream, the
   proposed base, and any existing pull request for the same head. Compare against the fresh remote
   base rather than a possibly stale local branch. Update an existing pull request instead of opening
   a duplicate.
4. Use `dev` as the base for an ordinary feature pull request. If the branch depends on an unmerged
   pull request, use that pull request's head branch as the base and identify it with `Stacked on`.
   A deliberate `dev`-to-`main` promotion is allowed only when the user requests it.
5. For an ordinary feature pull request, require an `issue/<number>-<slug>` branch registered in a
   linked worktree below `.agents/worktrees/`. Read the encoded issue, require it to be open, and
   reject a supplied issue number that does not match the branch. A deliberate `dev`-to-`main`
   promotion is the only exception.
6. Reject `main` as a pull-request head. Reject `dev` as a head unless the user explicitly requested a
   `dev`-to-`main` promotion. For feature work, require at least one committed delta from the selected
   base and do not push directly to `main` or `dev`.
7. Do not publish while in-scope changes are uncommitted; report them because this skill does not
   stage or commit work.

GitHub applies closing keywords only when a pull request targets the repository's default branch.
Require `dev` to be the default branch for an ordinary feature pull request that uses `Closes #N`.
For a stacked pull request, explain that the issue-closing link becomes active only after the parent
merges and the pull request is retargeted to `dev`. Never claim that a non-default-base merge closes
the issue.

## Establish the review contract

1. For feature work, read the issue encoded in the verified branch and extract each applicable
   acceptance criterion. Do not invent an issue. Remove the `Closes` line for a promotion without an
   issue.
2. Inspect `base...HEAD`, every changed file, and enough callers and consumers to understand the final
   behavior. Describe the net diff, not the sequence of commits or authoring journey.
3. Trace the change across these blast-radius surfaces:

   - capabilities, interfaces, dependencies, and their consumers;
   - financial decisions and authority boundaries;
   - durable state, schemas, configuration, and compatibility;
   - external effects, operator action, deployment, and rollback; and
   - the riskiest invariant or path on which review should focus.

4. When lifecycle, evidence, memory, runtime or investment configuration, portfolio, execution,
   evaluation, adapters, or entrypoints change, apply `investment-safety-review` before publishing
   and resolve or disclose its findings.
5. Map every applicable issue criterion and every changed contract or invariant to implementation
   evidence and verification evidence. Include negative, refusal, retry, or fail-closed behavior when
   the change can cause an external effect or cross a trust or authority boundary.
6. Inspect the complete committed diff for accidental files, credentials, account identifiers,
   generated runtime state, unsupported claims, and documentation required by the changed behavior.

The review contract is complete only when every changed blast-radius surface has evidence or an
explicit verification gap. A green command alone proves only that the command completed; record the
behavior observed and the assertion, artifact, receipt, or inspection that proves it.

## Verify before drafting

Record the current HEAD, run the smallest focused checks that exercise the changed behavior, then run
`make check`. Run `make mutation` when mutation-critical domain, portfolio, or execution behavior
changed. Never infer a result from CI or a previous run. Recheck HEAD before publication so the body
describes the exact commits that were verified. If a required check cannot run, capture the precise
gap and create a draft pull request unless the user explicitly chooses a different safe handoff.

## Draft from the repository template

Copy `.github/pull_request_template.md` to a unique temporary file created with `mktemp` because
`gh pr create --body-file` bypasses automatic template population. Replace all placeholders and
remove instructional comments. Keep the exact temporary path and delete that file after pull-request
read-back; do not leave a generated PR body in the repository.

- `Closes`: use the verified issue number, or delete the line. Do not use `Related to` when the pull
  request actually delivers the issue.
- `Stacked on`: uncomment it only for a verified dependency on an unmerged parent pull request and
  name that parent; delete it for ordinary `dev` feature pull requests and `dev`-to-`main` promotions.
- `Outcome`: in one to three sentences, state the observable final behavior and any material
  implementation choice needed to understand it. Link to the issue instead of restating its problem,
  history, or acceptance criteria.
- `Blast radius`: complete every line with `unchanged`, `none`, or a concrete impact. Name the review
  focus rather than saying only that the change is low risk.
- `Verification`: give each criterion or invariant a table row pairing it with the test, command,
  inspection, or artifact and its observed result. Combine claims only when the same evidence proves
  them. Include relevant negative paths and a `Not verified` line containing either a concrete gap
  and reason or `none`.
- `Out of scope`: name only adjacent work a reviewer could reasonably mistake as missing. Link its
  issue or note when one exists; otherwise write `None`.

Keep the body scan-friendly. Do not paste the issue, enumerate files, transcribe commit history, or
claim completeness that the evidence does not establish.

## Publish and verify

1. Push only the current feature branch with `git push -u origin HEAD` when its commits are ready.
2. Create the pull request with explicit repository, base, head, title, and body-file arguments. Use
   `--draft` when requested or when material verification remains incomplete.
3. When a pull request already exists, update its title, base, and body instead of recreating it.
   Preserve user-authored context that remains relevant and its draft or ready-for-review state unless
   the user asked to change that state.
4. Read the published pull request back with `gh pr view`. Verify the base and head, title, rendered
   body, issue relationship, stacked dependency, and draft state.
5. Report the pull request URL, base and head, verification performed, and every remaining gap. Do not
   merge it unless the user separately requests a merge.
