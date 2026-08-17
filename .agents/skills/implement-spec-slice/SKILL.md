---
name: implement-spec-slice
description: Implement one coherent vertical slice from the active agentic-investment-os requirements and domain documents. Use when asked to implement, build, start, continue, or complete a product requirement or implementation-sequencing slice involving lifecycle, persistence, evidence, memory, research, portfolio, execution, evaluation, adapters, entrypoints, or runtime configuration. Do not use for explanation-only, review-only, or documentation-only requests.
---

# Implement a Specification Slice

Deliver one dependency-complete, externally observable behavior without weakening an authority,
durability, evidence, or testing rule to make progress appear faster.

## Establish the slice

1. Require an open issue and apply [start-issue-worktree](../start-issue-worktree/SKILL.md). Continue
   only from its registered linked worktree on the matching `issue/<number>-<slug>` branch.
2. Read `AGENTS.md`, the exact `docs/product-requirements.md` outcome in scope, and
   `docs/architecture.md`.
3. Read `CONTEXT.md` and `docs/investment-domain.md` when the slice changes domain language or
   investment rules. Read `docs/module-graph.md` when an import or interface may change. Follow the
   conditional pointers in `AGENTS.md` for configuration, defensive patterns, and testing.
4. Inspect current code, tests, ADRs, and relevant proposed or implemented Agent Notes. Distinguish
   approved requirements from executable behavior and notes from authority; never infer that a
   scaffolded module or proposal already implements the requirement.
5. State the smallest vertical outcome that satisfies the request, its public interface, its authority
   owner, and acceptance criteria traceable to the active documents. Split unrelated outcomes.

The slice is established when every planned change is necessary for one observable outcome and every
acceptance criterion names evidence a test can inspect.

## Design through a deep interface

- Prefer an existing lifecycle capability: `Advance`, `Record`, `Govern`, `Apply`, or `Reconcile`.
  Keep research stages and storage mechanics behind that interface.
- Put an interface with the module that owns the behavior. Add an adapter only at a real external or
  substitutable seam; keep construction in `entrypoints`.
- Model inputs, results, refusals, and identifiers as explicit immutable types. Preserve deterministic
  domain logic and keep external frameworks outside `domain`.
- Map every model, parser, storage, process, and broker crossing to validation on entry and validation
  of the emitted artifact.
- Identify the durable checkpoint before each external effect, the effect-local idempotency key, and
  the append-only observation recorded afterward.
- Keep deployment choices in typed configuration. Record a durable architecture change in an ADR.
- When real alternatives create durable reasoning below the ADR threshold, use
  [manage-agent-notes](../manage-agent-notes/SKILL.md). Do not create a note for routine slice work.

The design is ready when callers and tests need one narrow interface and no planned step grants Codex,
the Research Lab, or the executor new authority.

## Make the behavior red

1. Add the smallest test through the interface appropriate to the tier in `docs/testing.md`.
2. Include the applicable refusal, duplicate-delivery, stale-input, or hostile-boundary case alongside
   the success path.
3. Run the focused test and confirm it fails for the missing behavior, not for setup or an unrelated
   defect.

The test is red only when its failure demonstrates the requested observable behavior is absent.

## Implement vertically

- Add only the domain types, persistence, orchestration, adapter behavior, and composition required by
  the established outcome.
- Persist intent before effects, append observed results, and make retries return the prior disposition
  rather than repeat the effect.
- Parse hostile representations once, construct typed values from validated fields, and keep untrusted
  text out of workflow control.
- Update affected architecture, configuration, module-graph, defensive-pattern, testing, and public
  documentation in the same change. Link to one authoritative home rather than copying rules.
- Apply [prose-standard](../prose-standard/SKILL.md) to substantial changed prose. If the slice
  implements a proposed Agent Note, move and rewrite that note as shipped reality in the same change.
- Add no speculative framework, generic seam, metered dependency, compatibility layer, or future
  configuration key.

The implementation is complete when the public acceptance test passes and no private stage call is
needed to prove the behavior.

## Verify the slice

1. Run the focused tests that cover the changed behavior and its invalid case.
2. Run `make format` after Python edits and inspect the complete diff for secrets, generated runtime
   state, source-of-truth duplication, and module-direction violations.
3. Run `make check`. Resolve failures at their cause; preserve every gate and safety invariant.
4. Report the implemented requirement, observable evidence, tests run, documentation, ADR, or Agent
   Note changes, and explicitly deferred work.

Do not claim the slice complete while a required acceptance path, invalid case, documentation update,
or repository gate remains unresolved.
