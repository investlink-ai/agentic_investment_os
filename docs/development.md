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

## Issue worktrees

Agent-written changes use one open issue, one `issue/<number>-<slug>` branch, one linked worktree, and
one pull request. The main checkout remains a clean `dev` control checkout. Run the project
`start-issue-worktree` skill before editing; its executable operation is:

```bash
./scripts/start-issue.sh <issue-number>
```

The script reads the issue and either resumes its exact registered worktree or fetches and
fast-forwards from `origin/dev` before creating `.agents/worktrees/<number>-<slug>`. New branches have
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
reject commits outside linked issue branches and direct pushes to `dev` or `main`; repository rules
must independently protect those remote branches because hooks can be bypassed.

## Daily workflow

1. Enter the issue's linked worktree, then read the documents referenced by the relevant `AGENTS.md`
   trigger and search applicable Agent Notes before making a durable design choice.
2. Make the smallest coherent change and run the narrowest relevant test while iterating.
3. Run `make format` when Python code changes.
4. Inspect the diff for generated state, secrets, accidental source-of-truth duplication, and missing
   documentation.
5. Run `make check` before handoff or commit.

The pre-commit hook checks formatting, lint, and staged-diff whitespace. The pre-push hook runs the
full local gate. GitHub Actions installs the locked environment and runs the same `make check` target.
Hooks and CI are guardrails, not substitutes for focused tests.

## Review workflow

Ordinary feature pull requests target `dev`. A branch that depends on an unmerged pull request targets
that parent branch and is retargeted to `dev` after the parent merges. Deliberate promotion pull
requests from `dev` to `main` are separate from feature review.

Use the repository's `create-pull-request` skill to open or update a pull request. It derives the
blast radius from the final diff and maps acceptance criteria and changed invariants to observed
verification evidence. The issue owns the original problem and acceptance criteria; the pull request
owns the delivered delta, review risk, and merge evidence.

GitHub closing keywords take effect only when a pull request targets the repository's default branch.
Keep `dev` as the default branch for the ordinary workflow. A stacked pull request's `Closes` link
becomes active only after it is retargeted to `dev`.

## Quality commands

The root `AGENTS.md` indexes the stable command surface and the `Makefile` is executable authority.
Run a focused test directly with `uv run pytest <path-or-node-id>`; finish with `make check`.
Run `make mutation` for mutation-critical domain, portfolio, or execution behavior. The mutation gate
is deliberately separate from the fast default gate and Git hooks.

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
