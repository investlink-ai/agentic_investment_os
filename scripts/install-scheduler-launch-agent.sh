#!/bin/sh
set -eu

label=ai.investlink.agentic-investment-os.scheduler
interval_seconds=300

fail() {
    printf 'install-scheduler-launch-agent: %s\n' "$*" >&2
    exit 1
}

if [ "$#" -ne 1 ]; then
    fail "usage: ./scripts/install-scheduler-launch-agent.sh <absolute-runner-path>"
fi
if [ "$(uname -s)" != Darwin ]; then
    fail "launch-agent installation is supported only on macOS"
fi

runner=$1
case "$runner" in
    /*) ;;
    *) fail "runner path must be absolute" ;;
esac
if [ ! -f "$runner" ] || [ ! -x "$runner" ] || [ -L "$runner" ]; then
    fail "runner must be an executable regular file, not a symbolic link"
fi
case "${HOME-}" in
    /*) ;;
    *) fail "HOME must be an absolute directory" ;;
esac

agent_directory=$HOME/Library/LaunchAgents
agent_path=$agent_directory/$label.plist
if [ -e "$agent_path" ] || [ -L "$agent_path" ]; then
    fail "launch agent already exists; remove it explicitly before reinstalling"
fi

umask 077
mkdir -p "$agent_directory"
temporary_path=$(mktemp "$agent_directory/.scheduler.XXXXXX") || fail "cannot create plist"
cleanup() {
    if [ -n "$temporary_path" ] && [ -e "$temporary_path" ]; then
        unlink "$temporary_path"
    fi
}
trap cleanup EXIT HUP INT TERM

plutil -create xml1 "$temporary_path"
plutil -insert Label -string "$label" "$temporary_path"
plutil -insert ProgramArguments -array "$temporary_path"
plutil -insert ProgramArguments.0 -string "$runner" "$temporary_path"
plutil -insert RunAtLoad -bool true "$temporary_path"
plutil -insert StartInterval -integer "$interval_seconds" "$temporary_path"
plutil -insert ProcessType -string Background "$temporary_path"
plutil -lint "$temporary_path" >/dev/null
chmod 600 "$temporary_path"
if ! ln "$temporary_path" "$agent_path"; then
    fail "launch-agent path appeared during installation"
fi
unlink "$temporary_path"
temporary_path=

uid=$(id -u)
if ! launchctl bootstrap "gui/$uid" "$agent_path"; then
    unlink "$agent_path"
    fail "launchctl refused the agent; generated plist was removed"
fi
trap - EXIT HUP INT TERM
printf 'Installed %s\n' "$agent_path"
