#!/bin/sh
set -eu

if [ "${1-}" != "44" ] || [ "$#" -ne 1 ]; then
    printf '%s\n' 'synthetic guarded-worktree boundary accepts only issue 44' >&2
    exit 64
fi

printf '%s\n' '{"status":"bound","issue":44,"branch":"issue/44-fixture","worktree":".agents/worktrees/44-fixture"}'
