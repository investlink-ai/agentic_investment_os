---
name: find-simplifications
description: Find and prove safe simplifications in agentic-investment-os. Use when asked to simplify, reduce complexity, remove dead code or configuration, collapse shallow modules, challenge overengineering, replace hand-rolled infrastructure, audit unused interfaces, or identify speculative behavior in Python code, tests, documentation, persistence, adapters, and process tooling.
---

# Find Simplifications

Prefer a few evidence-backed reductions over a catalogue of suspicions. A strong simplification removes
an interface, representation, dependency, branch, or maintenance obligation without weakening required
behavior or an investment-safety seam.

## Establish intent and protected context

1. Resolve the requested scope and whether the task authorizes edits or only findings.
2. Read `AGENTS.md`, `docs/architecture.md`, `docs/module-graph.md`,
   `docs/defensive-patterns.md`, `docs/testing.md`, applicable ADRs, and relevant active Agent Notes.
3. Read the product or investment requirement for any behavior that might change. A removal with an
   observable effect is a feature decision, not cleanup.

Treat these seams as intentional unless stronger evidence changes their owning architecture or policy:

- model research versus deterministic portfolio and execution authority;
- credentialed executor versus model-capable processes;
- append-only authoritative history versus rebuildable projections;
- availability time versus source event time;
- durable intent, effect-local idempotency, and independent observations;
- validation at model, parser, storage, process, configuration, and broker crossings; and
- Champion state versus Research Lab state.

The context is established when every candidate can be judged against its current consumer and owning
requirement rather than its apparent size.

## Survey real consumers

Use `rg` first, then read call sites and composition:

- **Production evidence:** `src/`, entrypoints, runtime configuration, migrations, and owned scripts.
- **Supporting evidence:** tests, docs, Agent Notes, comments, snapshots, and fixtures.
- **Ambiguous evidence:** examples, maintenance scripts, compatibility code, and generated artifacts;
  inspect their actual runtime role.

Search exact symbols, import paths, configuration keys, durable event names, schema fields, and wire
strings. Tests or docs as the only consumers are evidence of possible excess, not proof that a contract
is disposable.

## Look for leverage

Strong candidates include:

- public methods, configuration keys, events, or schema fields with no production consumer;
- shallow pass-through modules whose deletion would not redistribute complexity;
- two authoritative-looking representations of one fact;
- ports with one implementation and no demonstrated variation;
- generic repositories or frameworks introduced ahead of a real use case;
- rollback, compatibility, or defensive paths protecting behavior outside approved scope;
- duplicated validation after data is already trusted within one process; and
- hand-rolled parsing, retry, calendar, or serialization code safely replaced with maintained standard
  library or non-metered dependencies.

Apply the deletion test: if deleting the module makes its complexity reappear across callers, it is
earning depth. If the complexity disappears, it is likely a pass-through.

For a dependency substitution, count net deletion after adapter code, types, tests, transitive
dependencies, operational behavior, and failure modes. Prefer the standard library when it safely owns
the semantics; never propose a metered service.

## Prove or reject each candidate

For every retained finding, record:

- the exact surface and production-consumer evidence;
- the behavior, authority, or compatibility it currently provides;
- what would be deleted or collapsed;
- the capability or guarantee given up;
- affected tests, docs, schemas, migrations, and durable data; and
- the smallest verification that would prove the simplified result.

Reject a candidate when a current production consumer exists, an ADR or safety rule justifies it, the
change merely relocates complexity, persisted compatibility survives, or the evidence is speculative.

## Deliver the result

- For a review request, report ranked candidates without editing.
- For an authorized implementation, make the smallest coherent reduction and preserve observable
  behavior and all safety gates.
- Record a substantial debatable simplification as a proposed Agent Note through
  [manage-agent-notes](../manage-agent-notes/SKILL.md); include alternatives, consumer evidence,
  capability loss, and reintroduction conditions. Keep tiny local cleanups out of the note corpus.
- Run focused tests, `git diff --check`, and `make check` for implemented changes.

The audit is complete when every reported candidate is supported by call-site evidence and every
intentional seam inspected remains protected by an explicit owner or requirement.
