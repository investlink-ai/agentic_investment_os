---
name: investment-safety-review
description: Review agentic-investment-os changes for violations of authority, deterministic portfolio and execution behavior, append-only durability, evidence provenance and as-of time, idempotency, fail-closed operation, credential isolation, and required tests. Use when asked to review a diff, branch, pull request, commit, or implementation touching lifecycle, evidence, memory, research, configuration, portfolio, execution, evaluation, adapters, or entrypoints. Do not implement fixes unless the user separately asks for them.
---

# Review Investment-System Safety

Report substantiated defects in severity order. Prefer one reproducible authority or data-integrity
failure over a list of stylistic observations.

## Establish review scope

1. Resolve the exact comparison base and head from local Git or the referenced pull request. Recheck
   them after any retarget, rebase, or merge.
2. Read the changed files and enough callers, consumers, tests, configuration, and persistence code to
   trace the behavior through its real entrypoint.
3. Read `AGENTS.md`, `docs/product-requirements.md`, `docs/architecture.md`,
   `docs/module-graph.md`, `docs/defensive-patterns.md`, `docs/testing.md`, and applicable ADRs. Read
   `CONTEXT.md` and `docs/investment-domain.md` for changed domain language or investment behavior.
   Treat active requirements as required behavior and code as current implementation.
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
