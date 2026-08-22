# Agent Note: Prefer executable invariants to conventions

Status: implemented

## Problem

The repository states consequential authority, determinism, provenance, durability, and isolation
rules in active documentation. Prose remains necessary authority, but convention and review alone
can detect a violation only after an implementation has already made the unsafe behavior possible.
Mechanizing every sentence would create a different failure: brittle gates would duplicate
authority, reward syntax over semantics, and claim assurance they cannot provide.

## Decision

The [architecture](../../../docs/architecture.md#executable-invariants) requires stable,
proportionate mechanical enforcement for safety-critical invariants when an owning layer can express
the contract reliably. Enforcement starts with types and module structure, continues through
validation at seams and deterministic behavioral tests, and leaves irreducibly semantic or
contextual judgment to human review. A mechanical pass is evidence for its bounded claim and never
approval of the complete semantic contract.

## Alternatives considered

- Keep safety rules in prose and rely on review. This preserves flexibility but makes recurring,
  mechanically detectable violations depend on every author and reviewer remembering the same rule.
- Require a mechanical check for every normative statement. This was rejected because some rules are
  semantic, contextual, or too unstable to encode without false confidence and duplicated authority.
- Express every rule as a behavioral test. This was rejected because types, constructors, module
  structure, and seam validation can prevent invalid behavior earlier and more locally than a test.

## Consequences

- Implementations and reviews use the earliest stable layer that can prove an invariant instead of
  defaulting every safeguard to a test or checklist.
- Static and behavioral gates remain narrowly scoped, state what they prove, and avoid broad
  exemptions or claims of complete correctness.
- Human review remains required for semantics and proportionality, and a changed review or
  publication gate cannot approve itself.
- Rules without a stable, proportionate mechanical expression remain explicit prose contracts rather
  than receiving brittle automation.

## Verification

Manual inspection confirms that root instructions route agents to the active architecture,
module-graph, and testing owners and that those documents bound the evidence supplied by their
mechanisms. `make harness` checks required documentation paths and symlinks, `git diff --check`
checks patch whitespace, and `make check` remains the normal repository handoff gate; none proves
the semantic documentation contract.
