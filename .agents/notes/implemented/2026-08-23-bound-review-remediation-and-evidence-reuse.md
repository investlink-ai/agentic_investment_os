# Agent Note: Bound review remediation and retain equivalent evidence

Status: implemented

## Problem

Issue delivery treated every substantiated finding as blocking, repeated focused and full verification
after each correction, and invalidated every review axis after any amended commit. That preserved
fail-closed handoff but could turn low-impact judgment calls into an unbounded review loop and repeat
review when a base update left the reviewed change unchanged. A manual conflict cannot receive the
same shortcut because resolving it makes a semantic choice even when most of the patch is unchanged.

## Decision

[Code review](../../skills/code-review/SKILL.md) begins from a pinned review plan and gives each finding
a stable identity, severity, governing rule, consequence, merge disposition, and automation action.
Severity prioritizes repair without overriding contractual obligations. Blocker and High findings,
reachable Medium defects, and every unmet acceptance, gate, repository, or safety obligation are
must-fix. Only a Low non-contractual judgment call may remain advisory. A late Medium blocker may stop
at `human_review_required` without becoming advisory or opening another autonomous round.

[Issue delivery](../../skills/deliver-issue/SKILL.md) batches all known must-fix findings and permits at
most two remediation-and-verification rounds after the initial review without explicit human
direction. A capped unresolved finding produces `human_review_required`; an existing ready pull
request is demoted rather than treated as approved.

The first pass is full for every selected axis. A correction-only batch receives incremental
verification from affected axes when it changes no scope, authority, reviewer contract, review
selection, public contract, dependency, schema, configuration, persistence, external effect, blast
surface, consumer, or unresolved semantic conclusion. A change to any such input requires full review
from affected axes. Both modes retain one epoch's finding ledger and delivery-wide round count; a new
epoch records a material contract change without resetting the autonomous budget.

Review may be retained across a clean base update only when a same-session, pinned record proves the
patch, authority, reviewer instructions, review selection, blast radius, consumers, and checks remain
equivalent. Every manual conflict receives independent focused review of the resolution and its base
context. Uncertain or changed semantics return to the affected full review axes. The retained evidence
is rebound to the new exact refs and is never persisted as a reusable cache.

## Alternatives considered

- Fix every finding regardless of consequence. This keeps one disposition but gives style and bounded
  judgment calls the same delivery effect as unmet requirements or reachable defects.
- Stop after a fixed number of model calls. This is easy to count but ignores whether a round batched
  all known findings and whether unresolved work is blocking.
- Trust patch identifiers or an empty blast-radius check alone. These are useful evidence but cannot
  prove authority, reviewer, consumer, or conflict semantics remained unchanged.
- Persist review results for later reuse. This creates a stale authority surface when same-session,
  exact delivery evidence is sufficient.

## Consequences

- Review work has a default finite budget while must-fix obligations remain fail-closed.
- Advisory, out-of-scope, and disproved findings stay visible without forcing automatic edits.
- A novel post-initial finding enters automated remediation only for severe impact, an explicit or
  safety obligation, or evidence materially unavailable to the initial review; other blockers fail
  closed to human direction.
- Equivalence checks do not consume remediation rounds; semantic corrections do.
- Patch IDs, range-diff, changed paths, and blast triggers support but never decide review reuse.
- Oversized required corrections cause scope reduction or a human split decision, not follow-up
  deferral. Follow-up tracking remains separate from merge disposition and requires independent
  publication authority.
- Bounded reviewer telemetry explains cost and escalation without becoming approval or a persistent
  review cache.
- Human merge control, trusted-base reviewer independence, hooks, pre-push checks, and CI remain
  unchanged.

Reconsider the two-round budget only from repeated delivery evidence, not because one change reached
the cap. A revision is justified when capped deliveries repeatedly show that materially unavailable
evidence arrives only after the second verification round, or when human audits demonstrate that a
different bound would reduce unnecessary escalation without increasing regressions or weakening a
mandatory obligation. Any revision requires its own approved issue, updated note and workflow
contract, and counter-scenarios proving that unresolved must-fix findings still fail closed.

## Verification

[The testing policy](../../../docs/testing.md#agent-workflow-scenarios) owns deterministic validation
and explicit model-backed scenarios for finding triage, batching, cap exhaustion, remediation
regressions, incremental and full affected-axis selection, late findings, route selection, clean base
updates, focused conflict review, and failed equivalence. The publication fixtures also preserve
exact-commit reuse and reviewer-identity invalidation under the expanded delivery ledger.
