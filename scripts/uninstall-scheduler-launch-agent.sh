#!/bin/sh
set -eu

label=ai.investlink.agentic-investment-os.scheduler

fail() {
    printf 'uninstall-scheduler-launch-agent: %s\n' "$*" >&2
    exit 1
}

if [ "$#" -ne 0 ]; then
    fail "usage: ./scripts/uninstall-scheduler-launch-agent.sh"
fi
if [ "$(uname -s)" != Darwin ]; then
    fail "launch-agent removal is supported only on macOS"
fi
case "${HOME-}" in
    /*) ;;
    *) fail "HOME must be an absolute directory" ;;
esac

agent_path=$HOME/Library/LaunchAgents/$label.plist
if [ ! -e "$agent_path" ] && [ ! -L "$agent_path" ]; then
    printf 'Launch agent is already absent: %s\n' "$agent_path"
    exit 0
fi
if [ -L "$agent_path" ] || [ ! -f "$agent_path" ]; then
    fail "refusing to remove an unexpected launch-agent path shape"
fi

uid=$(id -u)
if ! domain_state=$(launchctl print "gui/$uid" 2>/dev/null); then
    fail "cannot inspect the operator launchd domain; plist was retained"
fi
case "$domain_state" in
    *"$label"*)
        if ! launchctl bootout "gui/$uid" "$agent_path" >/dev/null 2>&1; then
            fail "launchctl did not unload the installed agent; plist was retained"
        fi
        ;;
esac
unlink "$agent_path"
printf 'Removed %s\n' "$agent_path"
