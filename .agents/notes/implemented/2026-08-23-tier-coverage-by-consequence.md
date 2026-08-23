# Agent Note: Tier coverage by consequence

Status: implemented

## Problem

Requiring perfect line and branch coverage across the production package treats disposable wiring
and financial-authority decisions as equally consequential. It encourages low-value tests around
ordinary code while an aggregate threshold can still conceal an uncovered branch in a module whose
result authorizes, duplicates, or misreports a financial effect.

## Decision

The repository applies line and branch thresholds by consequence. The active
[`testing policy`](../../../docs/testing.md#coverage) owns the tier meanings and thresholds;
[`pyproject.toml`](../../../pyproject.toml) owns their executable values and current module patterns.

Classification follows the reachable consequence of incorrect behavior. Code that can construct or
authorize portfolio and execution intent, repeat or conceal an effect, or defeat an exact authority
refusal is critical. Provenance, as-of integrity, snapshot integrity, non-authorizing reconstruction,
and semantic boundary normalization support safety without directly authorizing an effect. Other
production modules require evidence selected for their observable behavior.

A repository checker consumes one branch-enabled JSON report rather than rerunning tests by path. It
validates the configured patterns against implemented production modules, rejects tier overlap and
unmeasured modules, calculates line and branch results independently, and reports uncovered files and
branch edges. Modules without branch opportunities contribute 100% branch coverage.

## Alternatives considered

- Keep 100% line and branch coverage for the whole package. This was rejected because equal numerical
  treatment rewards tests for low-risk implementation detail and makes ordinary growth unnecessarily
  expensive without adding semantic assurance.
- Enforce only aggregate package floors. This was rejected because strong coverage in ordinary code
  could mask one unexecuted critical authority path.
- Run separate pytest sessions for each tier. This was rejected because repeated test execution adds
  latency and duplicates collection and coverage orchestration; one validated report supports all
  independent calculations.
- Exclude low-risk or difficult code from measurement. This was rejected because exclusions can hide
  drift and turn coverage configuration into a mechanism for manufacturing a pass.

## Consequences

Every new or moved production module must be assessed against the consequence rules. A configured
pattern that stops matching, overlaps another tier, or lacks measurement fails closed. Mixed-risk
modules are split only when a precise owned boundary is necessary to classify a critical decision;
ordinary serialization, diagnostics, and projections do not become critical merely to raise their
test count.

Coverage retains a bounded claim: it proves execution, not correct assertions or complete semantic
behavior. Critical paths continue to require refusal, corruption, idempotency, property, and mutation
evidence where applicable, and no blanket exclusion or trivial assertion substitutes for that
evidence.

## Verification

Focused repository fixtures exercise exact-threshold acceptance, modules without branch
opportunities, each line and branch floor, high aggregate coverage with a critical gap, invalid and
overlapping classifications, unmatched paths, missing measurements, and malformed coverage reports.
`make check` generates the report and invokes the checker; `make harness` verifies that the checker
remains part of the repository command surface.
