# ADR 0006: Enforce capability effect boundaries statically

- Status: Accepted
- Date: 2026-08-23

## Context

Deterministic capabilities must receive clocks, randomness, identifiers, configuration, storage, and
external observations through typed values or owner-defined ports. Python imports and ambient APIs
otherwise let capability code acquire filesystem, process, network, model, broker, or SQLite
authority without crossing the adapter and entrypoint boundaries. Review alone does not provide a
stable, repeatable refusal before such code enters production.

The repository also needs future capability packages to inherit the restriction automatically.
Adapters and entrypoints must retain effect authority, but their location-based permission must not
become reusable through a reverse import or package-root re-export.

## Decision

Parse every production Python file outside `adapters/` and `entrypoints/` with a repository-owned
static gate. Reject a fixed catalog of direct ambient clocks, unseeded randomness, derived UUIDs,
environment and host-local timezone reads, concrete transports and clients, SQLite, filesystem
operations, and external process creation. Resolve ordinary import aliases, direct local re-exports,
simple assignments, and the typed receivers needed to distinguish immutable values from effects.
Reject invalid source and effect-bearing wildcard imports without a local suppression mechanism.

Keep allowed import directions in the independent module-graph gate. That gate prevents every
capability and the package root from importing or re-exporting effect-zone implementations. A new
top-level package is protected by default; adding another exempt path changes this decision.

## Alternatives considered

- Rely on code review and runtime tests. Rejected because neither provides exhaustive, early feedback
  for mechanically recognizable authority acquisition.
- Build an interprocedural Python type and control-flow analyzer. Rejected because inference through
  arbitrary callables, containers, classes, and generic aliases duplicates mature type-checker work,
  makes the repository gate disproportionate, and still cannot prove semantic authority.
- Maintain a list of protected capability directories. Rejected because a newly introduced package
  could begin outside the gate until someone remembered to extend the list.
- Allow inline suppressions or broad dependency exceptions. Rejected because a local exception would
  create reusable authority and weaken fail-closed behavior.
- Introduce a dependency-injection framework. Rejected because typed values and owner-defined ports
  already express the required seams without a runtime framework dependency.

## Consequences

- `make architecture` and `make check` reject recognized ambient effects before handoff.
- New effect APIs and concrete clients require a catalog entry and a negative fixture.
- Capability code expresses ambiguous or indirect effects through an owner-defined port; semantic
  review remains responsible for authority hidden behind reflection, dynamic dispatch, or misleading
  interfaces.
- Adapters and entrypoints retain their intended effects, while module-graph enforcement prevents
  capabilities and package-root modules from reusing that location-based permission.
