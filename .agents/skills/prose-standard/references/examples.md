# Distilled prose examples

Use these examples to identify the governing principle, not as text templates. The balanced form
preserves every load-bearing proposition needed at that location.

## Preserve authority and consequence

**Over-trimmed:** “Research cannot trade.”

**Balanced:** “Research emits validated candidate artifacts without broker credentials; deterministic
portfolio and execution modules alone may construct packets and submit paper orders.”

The process separation, credential rule, accepted output, and authority owners are distinct facts.

## Preserve as-of semantics

**Over-trimmed:** “Reject future evidence.”

**Balanced:** “An as-of read admits evidence by availability at the pinned cutoff, even when the source
event occurred earlier.”

The admission timestamp and the non-obvious event-time contrast are the contract.

## Public docstrings include typed refusals

**Over-trimmed:** “Advances the session.”

**Balanced:** “Advance or resume one market session. Return a durable no-action or fail-closed receipt
when eligibility, pinned inputs, or authoritative state prevent progress.”

The return distinctions are caller-visible; private transition mechanics are not.

## Comments state effect ordering

**Narration:** “First write to the database, then call the broker, and finally save the response.”

**Balanced:** “Persist effect-local intent before broker submission so reconciliation can resolve an
ambiguous timeout without duplicating exposure.”

The invariant and consequence matter; the visible control-flow walkthrough does not.

## Preserve a negative guarantee

**Over-trimmed:** “Unused capital stays cash.”

**Balanced:** “Risk clamps do not redistribute capped remainder or force gross exposure; unused capital
stays cash.”

The two forbidden behaviors explain why the outcome is stable.

## Model-visible text follows ownership

**Over-detailed:** Copying a complete research schema into an adapter README.

**Balanced:** State the adapter's validation and information-loss behavior locally, then link the schema
owned by the research module.

Exact model-visible fields are behavior, but one schema still has one owner.

## Implemented Agent Notes state shipped reality

**Planning residue:** “We will add a SQLite ledger and should test retries.”

**Balanced:** “The lifecycle appends intent to SQLite before advancement. Integration tests reconstruct
the process and verify that an identical idempotency key returns the prior receipt.”

An implemented note keeps the decision and evidence, not the implementation checklist.

## Delete reasoning transcripts

**Narration:** “The first branch handles missing input, so after returning there the cast below is safe.”

**Balanced:** No comment when types and control flow already prove the narrowing. If an external
validator establishes the invariant, document that validator contract instead.
