#!/bin/sh
set -eu

fail() {
    printf 'start-issue: %s\n' "$*" >&2
    exit 1
}

if [ "$#" -ne 1 ]; then
    fail "usage: ./scripts/start-issue.sh <issue-number>"
fi

issue_number=$1
case "$issue_number" in
    '' | *[!0-9]*) fail "issue number must contain decimal digits only" ;;
esac

for required_command in git gh make; do
    command -v "$required_command" >/dev/null 2>&1 || fail "$required_command is required"
done

control_root=$(git rev-parse --show-toplevel 2>/dev/null) || fail "run from a Git worktree"
control_root=$(CDPATH= cd -- "$control_root" && pwd -P)
common_dir=$(git rev-parse --path-format=absolute --git-common-dir)
main_worktree=$(CDPATH= cd -- "$common_dir/.." && pwd -P)

if [ "$control_root" != "$main_worktree" ]; then
    fail "run from the main control checkout, not a linked worktree"
fi

current_branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null) || \
    fail "the main control checkout must not be detached"
if [ "$current_branch" != "main" ]; then
    fail "the control checkout must be on main, not $current_branch"
fi

status=$(git status --porcelain=v1 --untracked-files=all)
if [ -n "$status" ]; then
    fail "the main control checkout must be clean before starting issue work"
fi

if [ ! -d "$control_root/.agents" ] || [ -L "$control_root/.agents" ]; then
    fail ".agents must be a real directory in the control checkout"
fi

worktree_root="$control_root/.agents/worktrees"
if [ -L "$worktree_root" ]; then
    fail ".agents/worktrees must not be a symbolic link"
fi
mkdir -p "$worktree_root"
worktree_root=$(CDPATH= cd -- "$worktree_root" && pwd -P)

gh auth status >/dev/null 2>&1 || fail "GitHub CLI authentication is required"
repository=$(gh repo view --json nameWithOwner --jq '.nameWithOwner') || \
    fail "cannot resolve the GitHub repository"
issue_state=$(gh issue view "$issue_number" --repo "$repository" --json state --jq '.state') || \
    fail "cannot read issue #$issue_number from $repository"
if [ "$issue_state" != "OPEN" ]; then
    fail "issue #$issue_number is not open"
fi
issue_title=$(gh issue view "$issue_number" --repo "$repository" --json title --jq '.title') || \
    fail "cannot read the title of issue #$issue_number"

slug=$(printf '%s' "$issue_title" \
    | LC_ALL=C tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//' \
    | cut -c 1-48 \
    | sed -E 's/-+$//')
if [ -z "$slug" ]; then
    slug=issue
fi

branch="issue/$issue_number-$slug"
git check-ref-format --branch "$branch" >/dev/null || fail "generated branch is invalid: $branch"

existing_branches=$(git for-each-ref --format='%(refname:short)' \
    "refs/heads/issue/$issue_number-*")
if [ -n "$existing_branches" ]; then
    existing_count=$(printf '%s\n' "$existing_branches" | awk 'NF { count++ } END { print count + 0 }')
    if [ "$existing_count" -ne 1 ]; then
        fail "multiple local branches exist for issue #$issue_number"
    fi

    existing_branch=$existing_branches
    registered_path=$(git worktree list --porcelain | awk \
        -v target="refs/heads/$existing_branch" \
        '$1 == "worktree" { path = substr($0, 10) }
         $1 == "branch" && $2 == target { print path; exit }')
    if [ -z "$registered_path" ]; then
        fail "$existing_branch exists without a linked worktree; inspect it before recovery"
    fi
    case "$registered_path" in
        "$worktree_root"/*) ;;
        *) fail "$existing_branch is registered outside .agents/worktrees" ;;
    esac
    if [ ! -d "$registered_path" ] || [ -L "$registered_path" ]; then
        fail "the registered worktree path is missing or symbolic: $registered_path"
    fi
    registered_path=$(CDPATH= cd -- "$registered_path" && pwd -P)
    registered_branch=$(git -C "$registered_path" symbolic-ref --quiet --short HEAD 2>/dev/null) || \
        fail "the existing issue worktree is detached"
    if [ "$registered_branch" != "$existing_branch" ]; then
        fail "the existing issue worktree branch does not match its registration"
    fi
    if ! make -C "$registered_path" bootstrap; then
        fail "bootstrap failed; worktree preserved at $registered_path"
    fi

    printf 'Resuming issue #%s\nPath: %s\nBranch: %s\n' \
        "$issue_number" "$registered_path" "$existing_branch"
    exit 0
fi

git fetch origin || fail "cannot fetch origin"
git rev-parse --verify 'refs/remotes/origin/main^{commit}' >/dev/null 2>&1 || \
    fail "origin/main does not exist"

remote_branches=$(git for-each-ref --format='%(refname:short)' \
    "refs/remotes/origin/issue/$issue_number-*")
if [ -n "$remote_branches" ]; then
    fail "remote issue work already exists; fetch and inspect it before creating a local worktree"
fi

if ! git merge-base --is-ancestor main origin/main; then
    fail "local main is ahead of or diverged from origin/main; reconcile it explicitly"
fi
git merge --ff-only origin/main || fail "cannot fast-forward local main to origin/main"

worktree_path="$worktree_root/$issue_number-$slug"
if [ -e "$worktree_path" ] || [ -L "$worktree_path" ]; then
    fail "worktree path already exists: $worktree_path"
fi

base_commit=$(git rev-parse 'refs/remotes/origin/main^{commit}')
git worktree add --no-track -b "$branch" "$worktree_path" "$base_commit" || \
    fail "cannot create the issue worktree"

created_root=$(git -C "$worktree_path" rev-parse --show-toplevel)
created_root=$(CDPATH= cd -- "$created_root" && pwd -P)
created_branch=$(git -C "$worktree_path" symbolic-ref --quiet --short HEAD 2>/dev/null) || \
    fail "created worktree is detached; it has been preserved for inspection"
if [ "$created_root" != "$worktree_path" ] || [ "$created_branch" != "$branch" ]; then
    fail "created worktree failed path or branch verification; it has been preserved for inspection"
fi

if ! make -C "$worktree_path" bootstrap; then
    fail "bootstrap failed; worktree preserved at $worktree_path"
fi

printf 'Created issue #%s worktree\nPath: %s\nBranch: %s\nBase: %s\n' \
    "$issue_number" "$worktree_path" "$branch" "$base_commit"
