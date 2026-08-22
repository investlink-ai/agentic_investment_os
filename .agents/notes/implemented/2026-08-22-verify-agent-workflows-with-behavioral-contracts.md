# Agent Note: Verify agent workflows with behavioral contracts

Status: implemented

## Problem

Structural checks prove that shared instructions, skills, and engineering documents exist, but they
cannot detect a workflow that reads the right contract and still skips approval, chooses the wrong
review axis, publishes stale work, or reaches an unauthorized effect. Ordinary unit tests can verify
the deterministic evaluator but cannot show how a model routes a realistic repository request.

## Decision

[The testing policy](../../../docs/testing.md#agent-workflow-scenarios) separates deterministic
contract validation from explicit model-backed evaluation. Versioned scenarios declare the request,
isolated fixture, relevant repository files and skills, required decisions, permitted and forbidden
effects, and acceptable disposition. `make harness` validates those inputs and the trace evaluator
without model access.

An operator may run one scenario through supported non-interactive Codex in an ephemeral fixture. A
granular Codex permission profile denies host reads and writes outside the disposable workspace and
minimal tool runtime, command network access, and approval escalation. Fake external commands, a
stripped child environment, structured final output, JSONL trace inspection, and final filesystem
and Git comparison keep external observations local and make forbidden attempts visible. Skill
routes require successful full-file reads rather than trusting the model's claim. A result hashes
all model-visible repository and harness inputs, the prompt and execution policy, and records an
in-band UTC timestamp, source revision, Codex version, and exposed model identity, but remains
ignored advisory evidence rather than approval or authority.

## Alternatives considered

- Running model evaluations in default CI or Git hooks was rejected because public gates must remain
  keyless, deterministic, and free of metered model access.
- Comparing exact final prose was rejected because wording drift is not a workflow failure and would
  reward implementation-specific answers instead of required decisions and effects.
- Exercising real GitHub, broker, credential, or network boundaries was rejected because an
  evaluation must not acquire the authority or side effects it is meant to police.
- Building a general evaluation service was rejected because a repository-local scenario contract
  and process runner cover the present routing and safety risks with less infrastructure.

## Consequences

Instruction or skill changes can update a small scenario and receive reproducible local evidence,
while normal checks catch schema drift, missing references, stale fixture hashes, and evaluator
regressions. Model runs require explicit operator intent and deliberate CLI-version support. A timeout,
unsupported version, malformed trace, missing effect, or ambiguous tool is non-passing rather than a
skip. Passing results supplement but never replace the trusted-base, independent-review, publication,
or investment-authority contracts.

## Verification

Unit tests cover typed parsing, malformed and incomplete traces, observed skill routing, successful
required effects, obscured commands, fail-closed classification, behavior matching, and bounded
diagnostics. Integration tests use a fake Codex executable to execute the fake GitHub and disabled
network boundaries and cover pass, timeout, authentication failure, workspace-only permission flags,
undeclared-read refusal, and complete provenance recording without model or network access. `make
harness` validates all versioned scenarios and the deterministic evaluator; `make check` remains the
handoff gate. The documented `issue-publication-awaits-approval` operator smoke passed with Codex CLI
0.149.0, observing only repository and fake GitHub reads before returning the required
`awaiting_approval` disposition.
