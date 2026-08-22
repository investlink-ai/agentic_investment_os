# ADR 0002: Version the complete SQLite schema

- Status: Accepted
- Date: 2026-08-22

## Context

The lifecycle database contains several authoritative append-only ledgers whose tables, indexes,
constraints, and triggers must evolve together. Run-configuration versions describe pinned decision
inputs, and record schema versions describe serialized durable artifacts; neither identifies the
physical database shape. Replaying `CREATE IF NOT EXISTS` statements at startup cannot distinguish a
current database from a partial, unsupported, or corrupt schema and can silently add objects to a
shape the runtime does not understand.

The first deployed schema predates the completed-conflict ledger, while later runtime databases may
already contain that table and its append-only triggers without a physical version marker. Upgrades
must preserve those records, remain recoverable after interruption, and refuse ambiguity before the
lifecycle appends new history.

## Decision

Use SQLite `PRAGMA user_version` as the single database-wide physical schema version. Keep it
independent of run-configuration and durable-record schema versions. The SQLite adapter owns a
private, contiguous migration sequence and an exact schema signature for every supported version;
this is not a cross-database migration framework.

An empty database advances through the same ordered migrations as an older database. An unversioned
non-empty database is assigned a version only when its complete schema exactly matches a pinned
supported signature. Before any change, startup verifies that the complete remaining migration path
exists and that the recorded version, schema, SQLite integrity check, and authoritative rows agree.

Each migration executes under `BEGIN IMMEDIATE`. Its schema statements and next `user_version` value
commit in the same transaction after post-migration validation. An exception or interruption rolls
that step back, leaving the prior committed version recoverable; retry resumes from that version.
Opening a validated current database performs no writes. A newer version, missing step, malformed
legacy shape, schema-version mismatch, invalid authoritative row, or SQLite failure produces a
bounded startup error before lifecycle writes.

## Alternatives considered

- A metadata table was rejected because SQLite already provides a transactional database-header
  version, while another table would itself need bootstrap and corruption rules.
- Replaying idempotent DDL and inferring a version from partial object presence was rejected because
  it can bless a hybrid schema and conceal an interrupted or manual change.
- One transaction for the entire multi-version upgrade was rejected because a completed step would
  not become a recoverable checkpoint. Atomic version-to-version steps retain ordering while bounding
  retry work.
- Copying records into a replacement database was rejected because the current migrations are
  additive and in-place transactions preserve authoritative row identity without introducing a
  second publication boundary.

## Consequences

- Every SQLite schema change adds one contiguous migration and one exact expected schema version.
- A release cannot open a database produced by a newer release or an unrecognized legacy shape.
- Startup performs database-wide validation and can cost more as authoritative history grows; this is
  accepted for the local single-operator V0 safety boundary.
- Additive migrations do not update or delete authoritative rows. A future transformation that must
  reinterpret record contents requires its own record-format authority and migration design.
- Migration diagnostics remain bounded and omit database contents, SQL text, credentials, and local
  paths.
