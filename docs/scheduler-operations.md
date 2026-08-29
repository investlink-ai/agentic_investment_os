# Scheduler operations

The local scheduler polls under macOS `launchd`, reconstructs eligible NYSE Market Sessions from its
pinned policy and trusted UTC clock, and invokes only the public `Advance` and `Status` capabilities.
The five-minute launch interval is a wake-up hint, not schedule truth: startup, delayed launch, sleep,
wake, interruption, and retry all rebuild due work from the append-only scheduler ledger.

## Prepare the runner

Create an operator-owned executable outside the repository. It must load complete non-secret runtime
and scheduler policy, compose the production `Advance` and `Status` capabilities, pass both to
`configure_scheduler`, invoke the returned `Scheduler`, and render its bounded receipt or
`scheduler.status()`. Keep the runner, configuration, runtime root, and logs untracked. Do not put
broker or model credentials, account identifiers, generated state, or a repository working directory
in the runner or launch-agent plist.

The scheduler policy contract is defined in the
[configuration catalog](config-catalog.md#market-session-scheduler-policy). The runner must treat a
configuration refusal, unsupported calendar year, persistence error, started or resumed session, missed
session, or refused lifecycle receipt as an operator-visible failure. Lifecycle liveness still comes
only from public `Status`; a successful launch or resident process is not liveness evidence.

## Install on macOS

Pass the runner's absolute path to the installer:

```bash
./scripts/install-scheduler-launch-agent.sh /absolute/path/to/operator-scheduler-runner
```

The installer refuses non-macOS hosts, relative paths, symlinks, non-executable runners, and an
existing plist. It creates a mode-`0600` user LaunchAgent at
`~/Library/LaunchAgents/ai.investlink.agentic-investment-os.scheduler.plist`, runs once when loaded,
and polls every five minutes. The generated plist contains the fixed label, absolute runner path,
launch flags, and interval only. Installation fails closed and removes the new plist if `launchctl`
does not accept it.

Inspect public scheduler and lifecycle status after installation; do not use `launchctl` state or log
timestamps as substitutes for either ledger. Updating the runner path or policy requires explicit
removal followed by installation. Reopening an existing scheduler ledger with changed policy refuses;
activate a future versioned plan under separately approved work instead of rewriting history.

## Remove from macOS

Removal is idempotent:

```bash
./scripts/uninstall-scheduler-launch-agent.sh
```

The remover unloads the fixed user agent and deletes only its fixed plist. It does not delete the
runner, configuration, logs, `scheduler.sqlite3`, `scheduler.lock`, lifecycle state, or evidence.
Retain authoritative state according to the repository's append-only and recovery policy.

## Verification and recovery

- Exercise configuration and calendar changes with an injected clock and recorded adapters before
  installing them. CI never loads a LaunchAgent or reads the wall clock.
- A `missed` result is an observation, not permission to backfill `Advance`; a `started` or `resumed`
  result may resume only after the configured recovery delay and under the process lock.
- A process crash releases the OS lock. The next poll appends a `resumed` attempt with the same public
  idempotency identity. Concurrent live polls serialize and exact terminal replay causes no new
  lifecycle request.
- Corrupt schema, hashes, permissions, timestamps, policy identity, or unsupported calendar years
  fail closed. Restore trusted state through an approved recovery procedure; never edit an event or
  manufacture completion.
