# Architecture decision records

Use numbered Markdown files for architecture, authority, persistence, durable or wire formats, and
other hard-to-reverse boundaries or constraints. An ADR states status, context, decision, alternatives
considered, and consequences; add `Supersedes` or `Superseded by` links when applicable.

## Decision index

Each ADR header is authoritative for status and supersession metadata.

| ADR | Status | Supersession | Affected seam |
| --- | --- | --- | --- |
| [0001: Python 3.12 modular monolith](0001-python-modular-monolith.md) | Accepted | — | Runtime topology and process trust |
| [0002: Own lifecycle policy in a pure domain kernel](0002-lifecycle-policy-in-domain-kernel.md) | Accepted | — | Application-to-domain lifecycle policy |
| [0003: Version the complete SQLite schema](0003-version-sqlite-schema.md) | Superseded | Superseded by [0004](0004-require-current-sqlite-schema.md) | SQLite schema identity and compatibility |
| [0004: Require the current SQLite schema](0004-require-current-sqlite-schema.md) | Accepted | Supersedes [0003](0003-version-sqlite-schema.md) | Persistence open and schema compatibility |
| [0005: Share core contracts with typed asset variants](0005-share-core-contracts-with-typed-asset-variants.md) | Accepted | — | Shared core, asset-owned variants, adapter translation, and entrypoint composition |
| [0006: Separate absolute instants from market time](0006-separate-absolute-instants-from-market-time.md) | Accepted | — | Durable instants and exchange-calendar time |
| [0007: Enforce bounded capability effects](0007-enforce-bounded-capability-effects.md) | Accepted | — | Every production capability except effect-owning adapters and entrypoints |

Agent Notes under `.agents/notes/` own durable feature, simplification, testing, bug-prevention, and
development-process reasoning below this threshold. Issues own work state. Do not create an Agent Note
that restates an ADR.

Do not rewrite an accepted decision into a different decision. Supersede it with a new numbered ADR,
link both records, and preserve the original context and consequences.
