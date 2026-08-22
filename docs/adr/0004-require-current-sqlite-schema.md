# ADR 0004: Require the current SQLite schema

- Status: Accepted
- Date: 2026-08-22
- Supersedes: [ADR 0003](0003-version-sqlite-schema.md)

## Context

The lifecycle database contains several authoritative append-only ledgers whose tables, indexes,
constraints, and triggers must evolve together. Run-configuration versions describe pinned decision
inputs, and record schema versions describe serialized durable artifacts; neither identifies the
physical database shape. Replaying `CREATE IF NOT EXISTS` statements at startup cannot distinguish a
current database from a partial, unsupported, or corrupt schema and can silently add objects to a
shape the runtime does not understand.

The project has no deployed runtime-state preservation obligation. Supporting provisional database
shapes would add branching schema definitions and recovery behavior before a real upgrade boundary
exists. Local runtime state can be recreated while the product remains pre-deployment.

## Decision

Use SQLite `PRAGMA user_version` as the single database-wide physical schema version. Keep it
independent of run-configuration and durable-record schema versions. The adapter owns exactly one
schema definition and accepts exactly two startup states: an empty, unversioned database that it
initializes atomically, or a database already carrying the current version and exact current schema.
Every other non-empty shape or physical version is unsupported and fails before lifecycle writes.

Fresh initialization creates every authoritative table, index, constraint, and append-only trigger,
validates the resulting schema and rows, and records the current physical version in one transaction.
Failure rolls the database back to its empty, unversioned state, so retry executes the same path.
Opening a current database validates its exact schema, full SQLite integrity, and cross-ledger row
invariants without writing it.

Only the lifecycle-status projection table and its owned indexes or triggers are excluded from the
authoritative signature. A view or same-named object with another owner remains part of the signature.
Integrity validation temporarily drops a recognized projection under a savepoint, runs the full
database check, and rolls back that temporary change. Projection-only corruption remains rebuildable,
while corruption shared with authoritative or global SQLite structures fails startup.

## Alternatives considered

- An ordered upgrade framework was rejected because no deployed database requires preservation;
  it would preserve provisional shapes and multiply startup paths without a current consumer.
- Replaying `CREATE IF NOT EXISTS` statements was rejected because it can bless a partial or manually
  changed schema instead of failing closed.
- Omitting a physical version was rejected because the complete database shape must remain distinct
  from run-configuration and durable-record schema versions.

## Consequences

- There is one schema definition, one current physical version, and one validation path.
- A non-empty database without the current marker and exact schema is not recoverable in place.
- While runtime state remains disposable, a schema change replaces the current definition and requires
  a fresh database. Introduce upgrade behavior only when preserving deployed state becomes an explicit
  product requirement.
- Startup performs database-wide validation and can cost more as authoritative history grows; this is
  accepted for the local single-operator V0 safety boundary.
- Startup diagnostics remain bounded and omit database contents, SQL text, credentials, and local
  paths.
