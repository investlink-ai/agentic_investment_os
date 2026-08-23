# ADR 0007: Enforce bounded capability effects

- Status: Accepted
- Date: 2026-08-23

## Context

The modular monolith assigns external effects to adapters and process assembly to entrypoints, while
domain and application capabilities are deterministic. Import direction prevents a protected
capability from reaching a project adapter, but it does not prevent direct use of the standard library
or a third-party client for clocks, randomness, host state, I/O, model calls, broker authority, or
process control. Review can catch those dependencies, but the boundary is stable and important enough
to provide earlier feedback.

An earlier approach attempted broad static inference across aliases, annotations, containers,
protocols, branches, and interprocedural returns. That approach approached a partial type checker,
created a large maintenance surface, and still could not prove the semantic boundary exhaustively.
The useful requirement is narrower: reject common, high-signal concrete effects and make new
production capabilities protected without requiring a manually maintained allowlist.

## Decision

Protect every Python source under `src/agentic_investment_os/` except the top-level `adapters` and
`entrypoints` capabilities. Protected code receives typed values or owner-defined ports and cannot
directly obtain these fixed effect categories:

- `CAP001` ambient wall-clock values;
- `CAP002` ambient or unseeded randomness;
- `CAP003` nondeterministic identifiers;
- `CAP004` environment or host-local time state;
- `CAP005` network access;
- `CAP006` model authority;
- `CAP007` broker authority;
- `CAP008` database access;
- `CAP009` filesystem effects; and
- `CAP010` process control.

Use Ruff's `flake8-tidy-imports` banned-API rule as the primary catalog and run it with inline
suppression disabled. Add a repository-owned syntax check only for distinctions the catalog cannot
express: a seeded `Random`, explicit timezone arguments, built-in `open`, and filesystem methods on a
directly constructed or annotated `Path`. The syntax check follows only imports, simple name and
attribute aliases, direct constructors, and recognized annotations. A parse or lint-engine failure is
`CAP000` and fails closed.

Keep diagnostics fixed, repository-relative, and free of source text or parsed values. Extend the
catalog only for demonstrated high-signal cases with allowed and denied fixtures. The gate explicitly
does not claim arbitrary control-flow, container, protocol, reflection, dynamic-import,
interprocedural, or evasive-code analysis. Module direction remains owned by `module-graph.md`, and
semantic review remains required.

## Alternatives considered

- Rely on conventions and review alone. Rejected because common concrete dependencies have stable
  syntax and deserve immediate deterministic feedback.
- Build an exhaustive repository-specific analyzer. Rejected because it would duplicate a type
  checker, grow with Python semantics, and imply assurance it cannot provide.
- Use only Ruff's banned-API catalog. Rejected because it cannot allow an explicitly seeded local
  `Random` while rejecting an unseeded one, require timezone arguments, or identify effects through a
  typed `Path` value.
- Ban effect APIs in adapters and entrypoints too. Rejected because those capabilities own external
  effects and composition; their safety comes from typed ports, configuration, process isolation,
  boundary validation, and behavioral evidence.

## Consequences

- Existing and future deterministic capabilities receive one automatic architecture check through
  `make architecture` and `make check`.
- The implementation stays small by delegating ordinary import and alias resolution to an existing
  pinned development tool and limiting repository analysis to bounded contextual seams.
- Deliberately forbidden Python examples remain inert `.py.txt` fixture data until an integration test
  materializes them in a temporary production tree.
- A pattern outside the bounded catalog can still pass. Review and tests remain necessary, and a new
  rule requires evidence that it is precise enough to avoid broad inference or routine false positives.
