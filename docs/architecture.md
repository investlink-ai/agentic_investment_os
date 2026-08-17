# Architecture

Read this document before changing module boundaries, lifecycle contracts, memory, portfolio risk, or
execution.

## Shape

V0 is a local Python 3.12 modular monolith with three process-level seams:

1. **Investment Operating System** advances or resumes the daily lifecycle, records outcomes, and
   applies approved governance changes.
2. **Order Execution Module** independently validates signed decision packets, submits only permitted
   Alpaca paper orders, and reconciles fills. It runs with broker credentials and no Codex capability.
3. **Research Lab** replays individual stages in a copied or synthetic namespace. Its artifacts can
   never enter champion state or execution.

Internally, the operating system is a checkpointed state machine over append-only SQLite records.
External calls happen only between durable checkpoints. A later LangGraph adapter may replace the
transition engine, but not the lifecycle, authority, durability, or idempotency contracts.

## Package responsibilities

- `domain`: framework-free types, events, identifiers, invariants, and ports.
- `application`: lifecycle capabilities and checkpointed orchestration.
- `evidence`: content-addressed artifacts, as-of provenance, and evidence assertions.
- `memory`: append-only belief ledger, rebuildable Belief Graph, and journal lessons.
- `research`: typed Codex role contracts and evidence-bound research workflow.
- `portfolio`: deterministic HouseView validation, sizing, risk limits, and decision packets.
- `execution`: deterministic packet verification, order policy, idempotency, and reconciliation.
- `evaluation`: outcome resolution, benchmark comparison, calibration, and promotion evidence.
- `adapters`: SQLite, filesystem, clock, Codex, SEC, Alpaca, and other external implementations.
- `entrypoints`: composition roots for lifecycle, executor, lab, scheduler, and operator commands.

Dependencies point inward. Domain code imports no adapter, orchestration framework, broker SDK, or LLM
client. Adapters implement ports defined by the owning domain or application module. Entrypoints are
the only place allowed to assemble concrete adapters and credentials.

## Authority boundary

```text
public evidence -> deterministic curation -> Codex research artifacts
     -> schema/provenance validation -> HouseView
     -> deterministic sizing and risk -> signed DecisionPacket
     -> isolated deterministic executor -> Alpaca paper account
```

Codex does not see broker credentials and cannot emit accepted weights or orders. The executor cannot
invoke Codex or change portfolio intent. External text is evidence data, never an instruction.

## Durable state

The source of truth is append-only, event-preserving records plus content-addressed evidence. The
Belief Graph and reports are rebuildable projections, not authoritative memory. Every run pins its
constitution, prompts, policies, model configuration, data cutoff, and source hashes. Corrections are
new records with explicit relationships to superseded records.

Local runtime state belongs under ignored root directories such as `var/`, `data/`, and `artifacts/`.
Production paths must be configurable and must never point into the source tree.

## Testing seams

Acceptance tests call lifecycle capabilities and inspect receipts, events, projections, packets, and
outcomes. Executor tests use recorded broker responses and synthetic order-event streams. Lab tests
prove isolation. Tests use fixed clocks, deterministic identifiers, and as-of data; live network tests
are separate from the default gate.
