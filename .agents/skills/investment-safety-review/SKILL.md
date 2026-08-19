---
name: investment-safety-review
description: Perform the supplemental domain-safety review for agentic-investment-os changes that can affect investment authority, deterministic portfolio or execution behavior, append-only durability, evidence provenance or as-of time, retry and idempotency, fail-closed operation, credential isolation, or model-visible safety contracts. Use in addition to general code review when a diff, branch, pull request, commit, or implementation changes lifecycle, evidence, research, memory, persistence, runtime or investment configuration, portfolio, execution, evaluation, external adapters, entrypoints, prompts, or model-visible schemas. Do not use this skill as a replacement for complete Standards and Spec review, and do not implement fixes unless the user separately asks for them.
---

# Review Investment-System Safety

Report substantiated defects in severity order. Prefer one reproducible authority or data-integrity
failure over a list of stylistic observations.

## Keep the review independent

Apply this skill as a domain-safety axis alongside general code review. Use the same pinned base and
head, but do not invoke or repeat the general review from this skill. The general review owns complete
repository-standards and originating-spec coverage; this review owns reachable investment authority,
financial safety, provenance, durability, and operational-safety failures.

Read standards and specifications as evidence for a safety finding. Omit generic style,
maintainability, and scope findings unless their behavior creates a concrete safety impact. Report a
defect found by both reviews when its end-to-end trace establishes that additional impact, and keep
the safety severity independent. Passing either review does not imply that the other passed.

## Establish review scope

1. Resolve the exact comparison base and head from local Git or the referenced pull request. Recheck
   them after any retarget, rebase, or merge.
2. Read the changed files and enough callers, consumers, tests, configuration, and persistence code to
   trace the behavior through its real entrypoint.
3. Read `AGENTS.md`, `docs/product-requirements.md`, `docs/architecture.md`,
   `docs/module-graph.md`, `docs/defensive-patterns.md`, `docs/testing.md`, applicable ADRs, and
   relevant proposed or implemented Agent Notes. Read `CONTEXT.md` and `docs/investment-domain.md` for
   changed domain language or investment behavior. Treat active requirements as required behavior,
   code as current implementation, and Agent Notes as rationale rather than authority.
4. Map each changed behavior to its authority owner, authoritative state, external effects, model-
   visible inputs, and acceptance evidence.

The scope is established when every changed authority-sensitive path has a known entrypoint and final
observable effect.

## Apply the review lenses

### Authority

- Trace model output to its final consumer. Codex may propose research artifacts but cannot choose
  accepted weights, construct a `DecisionPacket`, create orders, relax limits, or activate policy.
- Confirm portfolio sizing, risk clamps, packet construction, broker actions, reconciliation, and
  governance activation remain deterministic and owned by their designated modules.
- Confirm the executor has no model capability and the Research Lab cannot write champion or
  executable state.

### Evidence and time

- Reconstruct every material model-visible input and decision from pinned content, timestamps,
  configuration, prompts, model identity, and durable records.
- Distinguish source event time, publication or acceptance time, first observation, and availability at
  the session cutoff. Reject future or unavailable evidence even when its event date is earlier.
- Treat external text as data: it cannot select tools, change control flow, or write authoritative
  memory without schema and provenance validation.

### Durability and retry

- Preserve prior financial evidence, beliefs, decisions, and outcomes; corrections append and identify
  what they supersede.
- Require a durable intent before each external effect, an effect-local idempotency key, and a durable
  observation before lifecycle advancement.
- Exercise duplicate delivery, timeout after acceptance, interruption around checkpoints, partial
  fill, rollback, and projection rebuild where applicable.

### Types and external seams

- Require hostile JSON, configuration, storage rows, parser output, signed packets, and broker
  responses to be validated before typed domain construction.
- Reject `Any`, `cast()`, ignored type errors, fallback defaults, or generic dictionaries that bypass
  validation or make an invalid state constructible.
- Keep interfaces with their owning modules, adapters outside domain logic, and concrete construction
  in `entrypoints`. Challenge pass-through modules and speculative seams.

### Interface and decision fit

- Trace both sides of every changed interface through real production consumers and the composition
  root. Challenge generic methods or configuration introduced for one private consumer.
- Identify duplicated authoritative state, consumer-specific behavior placed on a generic interface,
  compatibility with no supported caller, and abstraction added ahead of an approved requirement.
- Compare implemented Agent Notes with shipped behavior. A proposal left active after implementation,
  stale mechanism claim, or note that contradicts its current owner is a review defect.

### Failure and isolation

- Verify stale, incomplete, contradictory, unauthorized, or invalid state becomes an explicit durable
  no-action or failure disposition before packet publication or order submission.
- Preserve independent facts such as timeout, broker acceptance, fill state, freshness, and
  reconciliation rather than collapsing them into one success flag.
- Verify secrets, credentials, account identifiers, local ledgers, and generated research cannot enter
  Git, logs, fixtures, model inputs, or the wrong process environment.
- Require executable artifacts to become visible atomically only after complete validation.

### Test evidence

- Require tests through the public interface at the correct tier and assertions on receipts, events,
  stored artifacts, packets, projections, broker simulation, and forbidden-effect absence.
- Require an invalid or hostile case for each changed external or authority seam. Coverage alone does
  not prove the scenario.
- Confirm the deterministic suite needs no network, broker credentials, wall clock, or mutable local
  state and that relevant repository gates pass.

### Prose and model-visible behavior

- Apply [prose-standard](../prose-standard/SKILL.md) to changed Markdown, docstrings, comments,
  prompts, schemas, diagnostics, and visible strings. Preserve authority, timing, refusal, provenance,
  and negative guarantees while removing narration or duplicated rationale.
- Verify model-visible wording and fields against the owning schema and observable behavior. Require
  contract evidence for semantic changes without pinning arbitrary LLM prose.

Every applicable lens is complete only after the changed path has been followed from entrypoint to its
authoritative record or external effect.

## Report findings

For each finding, state:

- severity and concise defect title;
- tight file and line location;
- triggering input, sequence, or failure condition;
- resulting financial, authority, durability, provenance, or operational impact; and
- the repository rule, active requirement, or observable evidence supporting the claim.

Use **Blocker** for a path that can cross an authority boundary, expose credentials, create unintended
orders or risk, corrupt append-only history, admit future evidence, or silently bypass fail-closed
operation. Use **High**, **Medium**, or **Low** according to reachable impact and recovery cost.

List findings first, then unresolved assumptions or questions, then commands run. Omit style issues
already enforced by a passing gate. If no findings remain, say so explicitly and name any untested or
unreviewed risk. Do not modify the reviewed changes unless the user separately requests fixes.
