---
name: prose-standard
description: Preserve complete technical and financial contracts while writing or reviewing agentic-investment-os Markdown, docstrings, comments, tests, prompts, schemas, diagnostics, and Agent Notes. Use for substantial, duplicated, sparse, or verbose prose.
---

# Prose Standard

Preserve the complete proposition, then remove repetition, narration, and decoration. Prose is useful
when it states a contract, invariant, rationale, limitation, or consequence that code and types do not
make obvious.

## Establish scope and authority

1. Resolve the requested file, directory, diff, or document scope. For a change task, use the outgoing
   change as scope; do not silently expand to a repository-wide rewrite.
2. Read the owning code and the applicable authority named in `AGENTS.md` before judging prose.
3. Route each fact to one home: product outcomes, investment rules, architecture, terminology,
   configuration, testing policy, ADR rationale, or Agent Note reasoning. Keep only the local contract
   a caller or maintainer needs and link deeper rationale to its owner.
4. Treat generated catalogs, recorded fixtures, runtime ledgers, generated financial research, and
   secrets as excluded from bulk prose editing. Change their owner or generator instead.

The scope is established when every passage under review has an identified audience and factual owner.

## Preserve the contract

Before changing a passage, identify every relevant:

- actor and action;
- condition, timing, ordering, unit, and as-of cutoff;
- `must`, `may`, or `never` obligation;
- exception, refusal state, and negative guarantee;
- authority owner, side effect, failure mode, and consequence; and
- provenance or reconstruction requirement.

Shorter prose is better only when all load-bearing propositions survive. Keep non-obvious rationale
when omitting it would invite unsafe use or an incorrect simplification.

## Match prose to its location

- **Public Python docstrings:** begin with a concise imperative summary. Add prose or compact
  Google-style sections only for non-obvious return distinctions, typed refusals, units, timestamps,
  ownership, durability, side effects, and failure behavior. Omit sections that merely restate names,
  annotations, or signatures.
- **Internal comments:** explain invariants, authority, as-of semantics, effect ordering, concurrency,
  or surprising failure behavior. Let code show ordinary control flow.
- **Deferred-work comments:** use `TODO(#N): <removal condition>` only when an open issue owns the
  work. Put durable rationale in its authority owner rather than an inline essay.
- **Git commit messages:** apply the subject and body contract in
  [`docs/development.md`](../../../docs/development.md#commit-messages). Preserve the observable
  outcome and non-obvious constraint; remove editing narration, duplicated issue or PR prose, and
  temporary-history markers.
- **Tests:** explain only why a fixture, real entry path, negative control, or indirect observation is
  required. Do not narrate setup and assertions.
- **Prompts and model-visible schemas:** treat wording and field meaning as behavior. Preserve authority
  prohibitions and evidence requirements; test schemas and observable consequences rather than
  arbitrary model prose.
- **Diagnostics, CLI, and reports:** identify the failing subject, violated rule, durable disposition,
  and corrective action when one is safe. Do not expose secrets or internal reasoning.
- **Active documentation:** state current truth and link to the owning detail. Move durable decision
  rationale to an ADR or Agent Note and task state to an issue.
- **Agent Notes:** preserve the problem, real alternatives, trade-offs, consequences, verification,
  and negative guarantees. Implemented notes describe shipped reality in the present tense.

## Write from the repository's vantage

Apply the HEAD-reader test: a reader with only committed repository state must be able to resolve every
internal reference and verify every claim.

- Replace PR, review, draft, and reasoning-session narration with present-tense facts.
- Replace “used to,” “now,” or “this change” with the current behavior or a present-tense
  counterfactual explaining the invariant.
- Keep resolvable issue and ADR links, external standards, measured bounds, and suppression reasons.
- Delete walkthroughs of obvious branches. If they protect a hidden invariant, state only that
  invariant.

Read [the distilled examples](references/examples.md) when calibrating a difficult edit or reviewing a
large prose change.

## Complete the pass

1. Classify each passage as keep, add, trim, restore, restructure, move, or defer.
2. Edit the canonical owner before references or generated outputs.
3. Re-read the result for lost modality, timing, authority, failure, or negative guarantees.
4. Run the narrow documentation or behavior checks plus `git diff --check`; run `make check` when the
   prose change is part of a handoff.
5. Report the inspected scope, material changes, deliberate keeps, and checks run.

The pass is complete when every changed proposition is accurate at HEAD, locally sufficient for safe
use, and owned in exactly one durable place.
