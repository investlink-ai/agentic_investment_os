---
name: start-issue-worktree
description: Create or resume the guarded linked worktree for an agentic-investment-os GitHub issue. Use before an agent writes tracked files for an issue, when asked to start, begin, implement, fix, or continue an issue, or when an implementation session is still in the dev control checkout. This skill validates the issue relationship, delegates Git mechanics to the repository script, and binds subsequent work to the issue worktree.
---

# Start Issue Worktree

Establish one writable workspace for one open issue. Leave the main `dev` checkout as a clean control
checkout and delegate branch and worktree mechanics to `scripts/start-issue.sh`.

## Establish the issue

1. Read `AGENTS.md` and the issue-worktree section of `docs/development.md`.
2. Require a GitHub issue number. Read the issue with `gh`, confirm it is open, and determine whether
   its acceptance criteria are actionable. Do not invent or create an issue without an explicit user
   request.
3. From the clean main checkout on `dev`, run:

   ```bash
   ./scripts/start-issue.sh <issue-number>
   ```

   The script owns authentication checks, issue state, base freshness, naming, collision detection,
   worktree creation, and bootstrap. Do not reproduce those operations as an ad hoc command sequence.
4. If the script refuses the operation, report the exact refusal. Preserve the control checkout and
   existing worktrees; do not stash, reset, rename, delete, or force through the condition.

The issue is established when the script reports one path and one `issue/<number>-<slug>` branch for
the requested open issue.

## Bind the implementation session

1. Resolve the reported path physically and verify that it is below `.agents/worktrees/`, is
   registered by `git worktree list --porcelain`, and has the reported issue branch checked out.
2. Continue only when every file and command tool can use that worktree as its working root. If the
   environment cannot bind subsequent work to that root, hand the path to a new isolated session and
   stop before editing.
3. Re-read applicable repository instructions from the worktree. Keep its `.venv` local; rely on the
   uv cache rather than sharing an environment.
4. Keep credentials, `.env`, local ledgers, and generated financial evidence out of the worktree.
   Worktrees isolate files and indexes, not Git refs, credentials, caches, or external services.

The session is bound when its repository root and current branch equal the script's reported values.

## Preserve the lifecycle

- Let repeated invocation resume the registered worktree. One writing agent uses it at a time; a
  concurrent writer requires a different issue or an explicit stack.
- Commit only from the linked issue worktree. Publish the first time with `git push -u origin HEAD`;
  the project hooks prohibit direct pushes to `dev` and `main`.
- Use `create-pull-request` for review handoff. Ordinary issue branches target `dev`.
- After merge or explicit abandonment, remove only a clean worktree with `git worktree remove`, then
  delete the local branch with `git branch -d`. Leave any refusal intact for human inspection and
  never automate forced cleanup.

The lifecycle is preserved when the issue, branch, worktree, verified commit, and pull request remain
one traceable unit and cleanup cannot discard unreviewed work.
