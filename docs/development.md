# Development

This guide owns local setup, the edit loop, and dependency workflow. Architecture belongs in
`architecture.md`, test policy in `testing.md`, and implemented configuration in
`config-catalog.md`.

## Prerequisites

- Python 3.12, matching `.python-version` and `pyproject.toml`
- `uv`
- Git
- Make

## First-time setup

From the repository root, run:

```bash
make bootstrap
```

Bootstrap creates `.venv`, installs the locked development environment, and configures Git to use
the versioned hooks in `.githooks/`. Setup is complete when `make check` passes.

## Codex plugin baseline

For repository work, keep the project and built-in skills available, enable the GitHub plugin, and
disable unrelated optional plugins. In the Codex CLI, run `/plugins`, select a plugin, and press
Space to toggle it; start a new session after changing the selection. This is an operator-controlled
baseline, not repository configuration. The repository does not encode user-specific skill paths,
change global Codex settings, or uninstall plugins.

The project skill catalog keeps only automatic-routing cues in each frontmatter `description`; the
skill body owns the complete workflow. `make harness` enforces the catalog limits described in
[`testing.md`](testing.md#project-skill-catalog). If Codex still reports that skill descriptions were
shortened with this baseline, capture the active plugin list and warning in the issue or pull request
evidence. See the official [Codex plugin browser
instructions](https://learn.chatgpt.com/docs/plugins#plugin-browser-in-codex-cli) for the current CLI
controls.

## Issue authoring

Use the project `create-issue` skill to draft and publish standalone issues or a caller-defined issue
set. The files under `.github/ISSUE_TEMPLATE/` own the format for features, bugs, tasks, and research;
active product, domain, architecture, and engineering documents own their requirements. Each
implementation issue defines one independently verifiable outcome suitable for one worktree and one
pull request.

Preview the complete write set before publishing. GitHub writes require approval of that preview,
use only existing repository metadata, and are read back so a partial set can resume without creating
duplicates. Issue publication grants no authority to start implementation or publish a pull request.
Use `plan-stage-issues` when the request requires capability-stage decomposition and native issue
relationships.

## Stage planning

Plan delivery as dependency-based capability stages, not time-boxed sprints. The
`plan-stage-issues` skill turns only the next unimplemented frontier in the active product and
architecture documents into a reviewed GitHub issue graph. A standalone change uses one issue; a
multi-PR capability uses one parent delivery issue and a small batch of vertical child issues linked
with native parent and blocked-by relationships.

Draft and review the entire stage graph before publishing it, then execute only its unblocked
frontier. Each child proceeds through one issue, linked worktree, and pull request. Reapply
`plan-stage-issues` to audit and close the parent after all children close, then plan the next stage
from the code that actually shipped. Do not introduce milestones, boards, labels, or a full-roadmap
backlog until a demonstrated coordination need justifies them.

## Issue worktrees

Agent-written changes use one open issue, one `issue/<number>-<slug>` branch, one linked worktree, and
one pull request. The primary checkout remains a clean `main` control checkout. Run the project
`start-issue-worktree` skill before editing; its executable operation is:

```bash
./scripts/start-issue.sh <issue-number>
```

The script reads the issue and either resumes its exact registered worktree or fetches and
fast-forwards from `origin/main` before creating `.agents/worktrees/<number>-<slug>`. New branches have
no upstream. Creation and resumption run `make bootstrap`; a refusal preserves existing state for
inspection, so resolve it explicitly rather than stashing, resetting, forcing, or deleting work.

Run the implementation session with the linked worktree as its exact working root. Every concurrent
writer requires a different worktree; serial handoff may reuse the same one. Worktrees isolate files,
`HEAD`, and indexes, but share Git refs, configuration, hooks, credentials, caches, and external
services. Keep `.venv` local to each worktree and never copy `.env`, broker credentials, ledgers, or
generated financial evidence into it.

Publish a new issue branch with `git push -u origin HEAD`. After its pull request merges or the user
explicitly abandons it, remove only a clean worktree and then its merged local branch:

```bash
git worktree remove .agents/worktrees/<number>-<slug>
git branch -d issue/<number>-<slug>
git worktree prune --dry-run
```

Git's refusal to remove dirty work is a safety boundary. Do not automate forced cleanup. Local hooks
reject commits outside linked issue branches and direct pushes to `main`; repository rules must
independently protect `main` because local hooks are not remote enforcement.

## Post-merge reflection

After a materially significant pull request merges and its authorized clean-worktree cleanup finishes,
an operator may apply the `reflect-on-merged-pr` skill. It reconstructs the merged issue, diff, review,
checks, current authority, Agent Notes, and existing issues, then returns only evidence-backed
architecture, operational, verification, agent-workflow, or domain-interface follow-up dispositions.
Materiality follows authority, risk, recurring cost, and assurance impact rather than diff size.

Reflection is optional and read-only. It may conclude that no follow-up is warranted, and it never
performs cleanup, repeats pull-request acceptance, creates repository artifacts, or publishes an issue.
Observable incomplete cleanup limits the reflection instead of authorizing or assuming cleanup. Issue
creation, capability-stage planning, and Agent Note changes require their own explicit workflows.

## Daily workflow

Use `deliver-issue` as the agent entrypoint for a ready issue. It selects an implementation path from
the issue and current code, keeps supporting skills conditional, remediates findings against a pinned
commit, and hands complete exact-commit review evidence to draft-pull-request publication. Detailed
routing lives in the skill. One strong delivery agent remains the sole writer and applies
implementation skills inline; independent Standards, Spec, and conditional investment-safety
reviewers are read-only subagents. The stable local loop remains:

1. Confirm the issue has no open blocker, enter its linked worktree, then read the documents referenced
   by the relevant `AGENTS.md` trigger and search applicable Agent Notes before making a durable
   design choice. For architecture, domain, configuration, requirements, or workflow changes, remove
   or defer proposed concepts that do not map to both the issue and an active authority owner.
2. Make the smallest coherent change and run the narrowest relevant test while iterating.
3. Run `make format` when Python code changes.
4. Inspect the diff for generated state, secrets, accidental source-of-truth duplication, and missing
   documentation.
5. Run `make check` before handoff or commit.

The review loop separates severity from delivery disposition. Blocker and High findings, reachable
Medium correctness, safety, or maintainability defects, and every unmet acceptance criterion, gate,
explicit repository obligation, or safety invariant are must-fix regardless of severity. Only a Low
non-contractual judgment call may remain advisory. Pre-existing conditions are out of scope only when
the change does not introduce, worsen, rely on, or make them reachable; disproved findings retain the
evidence that refuted them.

Reviewers return one initial batch with stable finding identifiers. The delivery agent corrects all
known must-fix findings together, uses focused checks while editing, runs the full gate once for that
batch, and requests one affected-axis verification. Without explicit human direction, delivery has at
most two such remediation-and-verification rounds after the initial review. Advisory findings may
remain visible at handoff. An unresolved must-fix finding at the cap produces
`human_review_required`, demotes any existing ready pull request, and stops; it never becomes an
automatic pass.

A base update retains review only through a same-session, pinned equivalence record proving unchanged
patch semantics, authority, reviewer instructions, review selection, blast radius, consumers, and
passing checks on the new exact head. Patch IDs, range-diff, changed paths, and an absent blast trigger
support that conclusion but never establish it alone. Every manual conflict receives independent
focused review of the resolution, both sides' intent, intervening base changes, and affected
consumers. Uncertain or changed semantics rerun the affected full review axes; proven equivalence does
not consume a remediation round.

The pre-commit hook checks formatting, lint, and staged-diff whitespace. After checking destination
refs, the pre-push hook clears Git's repository-local environment before running the full local gate,
so nested fixture repositories cannot alter the issue worktree's index or refs. GitHub Actions
installs the locked environment and runs the same `make check` target. Hooks and CI are guardrails,
not substitutes for focused tests. Treat a hook refusal as a failed gate to resolve through the issue
worktree; never commit or push with `--no-verify`.

A request to work on, implement, fix, refactor, document, complete, resume, deliver, or ship a numbered
issue authorizes local edits, checks, commits, issue-branch push, and creation or update of a draft
pull request after the exact final commit passes required gates and reviews. An explicit local-only or
no-publication restriction stops after the verified commit. A coding request not bound to a numbered
issue does not authorize GitHub publication. Marking a pull request ready, merging, deployment, issue
or stage closure, issue-graph publication, and worktree cleanup remain separately authorized actions.
Fail-closed demotion of an existing ready pull request is always authorized when the current issue has
a must-fix finding, reaches the remediation cap, has incomplete material verification, or encounters
an object-ID or reviewer-identity mismatch; it must not push commits or change other pull-request
metadata.

## Commit messages

Use a Conventional Commit-style subject for final issue-branch history:

```text
type(scope): imperative outcome
```

Choose `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `build`, `ci`, or `perf`. The scope is
optional; use it only when it names a stable capability such as `lifecycle`, `evidence`, or `harness`,
not a filename. Keep the subject at most 72 characters, omit a trailing period, and describe the
repository outcome rather than the editing activity.

Use a body only when a reviewer needs non-obvious rationale, an invariant, a migration constraint, or
a consequence that the subject cannot carry. Do not repeat the issue, diff, or pull-request blast
radius and verification evidence. The pull request owns `Closes #N`; commits do not. Keep temporary
`WIP`, `fixup!`, and `squash!` commits out of final review history, and omit generated-by or AI
co-author trailers.

Prefer one coherent commit for a small issue. Use multiple commits only when each is independently
reviewable and leaves the repository in a valid state; the final branch must pass `make check`.

## Review workflow

Ordinary feature pull requests target `main`. A branch that depends on an unmerged pull request targets
that parent branch and is retargeted to `main` after the parent merges.

Use the repository's `create-pull-request` skill to open or update a pull request. It derives the
blast radius from the final diff and maps acceptance criteria and changed invariants to observed
verification evidence. The issue owns the original problem and acceptance criteria; the pull request
owns the delivered delta, review risk, and merge evidence. A complete handoff from the active
`deliver-issue` session can satisfy review and verification prerequisites only while its exact base,
head, scope, clean worktree, review selection, and immutable reviewer-contract identity for every
axis still match. A handoff retained across a base update must also carry its complete equivalence or
focused-conflict record and remain bound to the new exact refs. A direct invocation executes the
review and verification gates because retained evidence cannot be reconstructed outside its active
delivery session.

GitHub closing keywords take effect only when a pull request targets the repository's default branch.
Keep `main` as the default branch. A stacked pull request's `Closes` link becomes active only after it
is retargeted to `main`.

## Quality commands

The root `AGENTS.md` indexes the stable command surface and the `Makefile` is executable authority.
Run a focused test directly with `uv run pytest <path-or-node-id>`; finish with `make check`.
Run `make mutation` when a change can directly alter critical financial or broker authority behavior.
The issue author and reviewer apply the human-owned `mutation:critical` label when a pull request
requires the same keyless certification; label application, a later push, or reopening reruns the
labeled job. Use `workflow_dispatch` for explicit investigation or baseline preparation. The mutation
gate remains separate from the fast default gate and Git hooks; `testing.md` owns its exact scope,
scaffold exemption, and strict outcome policy.

`make harness` enforces the pure unit-test tier and validates the versioned agent-workflow scenario
schemas, references, fixture hashes, and deterministic evaluator without invoking a model. An operator
can explicitly run one model-backed scenario with `make agent-workflow SCENARIO=<scenario-id>`;
`testing.md` owns both checks and the model-backed scenario's isolation, evidence, and
non-authoritative result contract.

## Dependency workflow

Prefer the standard library until a dependency makes a boundary materially safer or removes more
owned code than it adds. Runtime dependencies require a concrete use case and cannot introduce a
metered service.

```bash
uv add <package>
uv add --dev <package>
uv lock
uv sync --all-groups
```

`pyproject.toml` declares dependencies and `uv.lock` pins them. Do not install packages manually into
`.venv`, edit `uv.lock`, or create a requirements file. Keep model, broker, and data-provider clients
behind typed ports so deterministic tests require no credentials or network.

## Documentation workflow

- Update `architecture.md` when runtime topology, module seams, lifecycle, authority, durable state,
  or trust boundaries change; add an ADR only for a durable trade-off.
- Update `product-requirements.md` when a system outcome, scope item, acceptance gate, or implementation
  order changes.
- Update `CONTEXT.md` when domain language changes and `investment-domain.md` when stable investment
  decision rules change.
- Update `config-catalog.md` in the same change that adds or changes implemented configuration.
- Add reusable prevention rules to `defensive-patterns.md` after a defect or near miss establishes the
  bug class.
- Update `testing.md` when test tiers, fixtures, gates, or live-test policy change.
- Update `module-graph.md` when allowed dependency directions change; once a generated graph exists,
  update its generator rather than its output.
- Use `.agents/notes/` for durable feature, simplification, testing, bug-prevention, or process
  reasoning below the ADR threshold. Issues own task state; ADRs own hard-to-reverse boundaries.
- Apply the `prose-standard` skill to substantial documentation, docstring, prompt, diagnostic, or
  Agent Note edits.

Active documentation is authoritative within the ownership declared at the top of each file. Git
history preserves superseded documents and deleted notes; do not keep parallel archive copies.
