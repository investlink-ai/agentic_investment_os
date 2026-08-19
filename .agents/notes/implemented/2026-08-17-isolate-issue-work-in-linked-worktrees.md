# Agent Note: Isolate issue work in linked worktrees

Status: implemented

## Problem

Parallel or resumed agent sessions can overwrite unrelated changes, commit from the integration
branch, or publish the wrong ref when issue identity exists only in prose. A worktree convention alone
does not prevent those mistakes, while a general shell-command parser would add a large and bypassable
policy implementation.

## Decision

[The development workflow](../../../docs/development.md#issue-worktrees) binds each agent-written
change to one open issue, one `issue/<number>-<slug>` branch, one ignored worktree below
`.agents/worktrees/`, and one pull request. The primary `main` checkout remains a clean control
checkout.

[The creation script](../../../scripts/start-issue.sh) owns deterministic Git and GitHub operations:
issue validation, a fresh `origin/main` base, branch and path derivation, collision refusal, creation
without an upstream, bootstrap, and conservative resumption. [The
skill](../../skills/start-issue-worktree/SKILL.md) owns agent preflight and binds subsequent file and
command operations to the reported worktree.

The versioned pre-commit hook accepts commits only from linked issue branches. The pre-push hook
checks Git's resolved destination refs, rejects direct publication to `main`, and then runs
the repository gate after clearing Git's repository-local environment. This prevents nested fixture
repositories from inheriting and mutating the issue worktree's index or refs. These hooks prevent
routine mistakes; remote branch protection remains the enforcement boundary because local hooks are
bypassable.

## Alternatives considered

- Continuing to switch branches in the main checkout was rejected because concurrent and resumed
  writers would still share files and an index, and the integration checkout could accumulate
  unreviewed commits.
- Using a tool-specific worktree directory was rejected because Codex and Claude share the same
  repository workflow. `.agents/worktrees/` expresses project ownership without duplicating setup.
- Parsing arbitrary shell commands was rejected because Git already exposes exact refs to hooks and
  GitHub protects publication more reliably than a local command-language parser.
- Adding a daemon, registry, automatic rebases, or forced cleanup was rejected until measured
  concurrency requires orchestration beyond Git's worktree inventory and refusal behavior.

## Consequences

Starting work requires an actionable open issue and a clean `main` control checkout. Each worktree has
its own environment and can survive setup failure, interruption, or agent handoff without losing
state. Existing mismatched branches, paths, remote work, or divergent integration history stop the
workflow for explicit recovery.

Worktrees provide file and index isolation, not a security sandbox: refs, Git configuration, hooks,
credentials, caches, and external services remain shared. The workflow therefore copies no secrets or
runtime financial state, serializes repository-wide maintenance, and relies on the execution sandbox
and GitHub rules for stronger boundaries. Cleanup remains manual and non-forcing so unreviewed work
cannot be discarded merely to restore a tidy inventory.

## Verification

Focused integration tests exercise creation, resumption, invalid context, collisions, setup failure,
hook destination-ref behavior, and repository-environment isolation before the pre-push gate.
`make harness` verifies the canonical skill, executable script, and ignore rule; `make check` remains
the complete local handoff gate.
