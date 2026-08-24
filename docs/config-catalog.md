# Configuration catalog

This catalog records implemented configuration only. `investment-domain.md` owns approved policy
semantics; do not invent future runtime keys before their implementation slice.

The typed schema and resolver live in
`src/agentic_investment_os/entrypoints/configuration.py`. This catalog owns the field-level operator
contract and points to that executable source of truth.

## Configuration rules

- Entrypoints resolve configuration before invoking application or execution capabilities.
- Runtime modules receive validated immutable values; they do not read environment variables or
  configuration files directly.
- Defaults are explicit in one resolution step, never scattered through control flow.
- Every policy that can change a material decision has a schema version and stable content hash.
- A session pins its material configuration before evidence or research begins. Governance changes
  activate only at a later session boundary.
- Secret values are never configuration artifacts, hashes, logs, fixtures, or model inputs. Typed
  secret references may be resolved only inside the credentialed adapter or entrypoint.
- Unknown keys, invalid values, incomplete policy, and conflicting sources fail closed.

## Runtime configuration

The runtime resolves one or more explicit non-secret sources without precedence. Repeating the same
value is valid; conflicting values, unknown fields, missing fields, and unsupported schema versions
return a typed refusal. Structured values are compared recursively and type-strictly, so nested
numeric lookalikes such as integer `1` and float `1.0` conflict in either source order. All fields are
required and have no default.

| Field | Type and validation | Run fingerprint | Activation and authority | Disclosure |
| --- | --- | --- | --- | --- |
| `schema_version` | Integer; must equal `1` | Included | Resolved at process composition; changes require a compatible code release | Safe to log or expose to a model |
| `state_root` | Absolute path string; must be symlink-free and either outside the repository or below the top-level `var/`, `data/`, or `artifacts/` root; Git's effective rules must ignore both the root and fixed database path | Included after canonical path resolution | Resolved at process composition by the local operator | Do not log or expose the local path; only its configuration hash enters receipts and lifecycle records |
| `enabled_asset_classes` | Exact JSON list `["us_equity"]`; no empty, duplicate, unknown, crypto, option, or multi-class activation is accepted in V0 | Included canonically | Resolved at process composition; it authorizes only the implemented equity lifecycle and policy | Safe to log or expose to a model after validation |
| `universe_policy` | Complete object matching the schema below | Included canonically; its own content hash is also pinned into the run identity | Resolved at process composition; changes activate through a later run identity | Safe to log or expose to a model after validation |
| `evidence_policy` | Complete object matching the schema below | Included canonically | Resolved at process composition; it authorizes the configured market, news, SEC, issuer, and official-macro captures for the pinned Data Regime | Safe to log or expose to a model after validation |

The resolver hashes canonical JSON containing exactly these validated fields with SHA-256. The state
root is created with mode `0700`; the fixed `lifecycle.sqlite3` file is created with mode `0600`.
Existing paths with broader permissions, unsafe shapes, or final symlinks fail closed. The database
filename and private modes are safety constants rather than tunable configuration.

### Universe policy

`universe_policy` contains the complete discriminated equity policy for `SnapshotUniverse`. Its
`asset_class` and `policy_type` must match the sole enabled class, and its `data_regime` must equal
the recorded instrument and position input regime. Decimal thresholds use plain, non-negative
strings of at most 64 characters and are normalized canonically, so configuration never inherits
exponent expansion, binary floating-point, or ambient parsing behavior. The mechanics-calibration
constraint and investment meaning remain owned by
[`investment-domain.md`](investment-domain.md#universe-and-attention).

| Field | Type and validation | Effect |
| --- | --- | --- |
| `asset_class` | String; must equal `us_equity` | Selects the equity-owned policy variant and must match `enabled_asset_classes` |
| `schema_version` | Integer; must equal `1` | Versions the policy representation |
| `policy_type` | String; must equal `equity_universe` | Discriminates the concrete policy before its payload is interpreted |
| `data_regime` | Lowercase bounded identifier | Pins the feed, entitlement, and interpretation regime and must match recorded inputs |
| `approved_exchanges` | Non-empty, unique list drawn from `ARCA`, `NASDAQ`, and `NYSE` | Restricts new-entry eligibility to configured major US exchanges |
| `etf_allowlist` | Unique list of canonical `EquityInstrumentIdentity` envelopes in the `alpaca-paper` provider-and-environment catalog namespace; may be empty | Admits only explicitly identified ETF listings; aliases and display symbols never become membership keys, and leverage or inverse flags still exclude an allowlisted ETF |
| `minimum_price` | Plain non-negative decimal string of at most 64 characters, normalized canonically | Excludes lower-priced new entries |
| `minimum_median_dollar_volume` | Plain non-negative decimal string of at most 64 characters, normalized canonically | Excludes less-liquid new entries |
| `minimum_history_days` | Integer from `0` through `1,000,000` | Excludes new entries without sufficient recorded history |
| `maximum_snapshot_age_seconds` | Integer from `1` through `86,399,999,999,999` seconds | Fails closed when either required recorded snapshot is older than the Evidence Cutoff allows; the upper bound keeps every accepted duration representable |

Unknown, missing, duplicate, malformed, mixed-variant, or out-of-domain policy values fail
configuration before the runtime database is prepared. Provider entitlement or observed instruments
cannot activate an asset class. Policy fields cannot be inferred from recorded data; threshold
values are mechanics-calibrated and must not be selected by optimizing investment return.

### Evidence policy

`evidence_policy` contains the complete recorded-source capture policy for `CaptureEvidence`. Its
Data Regime must match `universe_policy`; an entitlement, feed, or source change cannot enter an
existing regime. Runtime composition requires at least one retrieval for each V0 source: IEX market,
Alpaca news, SEC EDGAR, issuer investor relations, Federal Reserve, BLS, and BEA. Each capture appends
intent before consulting the recorded source, then persists a typed outcome and any immutable content
before lifecycle completion. This schema configures offline recorded adapters only; live HTTP
collection, bulk historical backfill, and LLM online retrieval are outside the implemented boundary.

| Field | Type and validation | Effect |
| --- | --- | --- |
| `schema_version` | Integer; must equal `2` | Versions the policy representation |
| `policy_type` | String; must equal `v0_evidence` | Discriminates the implemented evidence policy |
| `data_regime` | Lowercase bounded identifier equal to `universe_policy.data_regime` | Prevents observations from crossing regime boundaries |
| `requests` | One through 100 objects with unique retrieval identities; runtime composition requires coverage of all seven V0 sources and canonicalizes order by kind, source, and identity | Makes the complete scheduled capture set explicit |
| `requests[].kind` | String; `market`, `news`, `sec_filing`, `issuer_release`, or `official_macro` | Selects the typed normalized-content and time contract |
| `requests[].source` | String; `iex`, `alpaca_news`, `sec_edgar`, `issuer_investor_relations`, `federal_reserve`, `bls`, or `bea`, compatible with the selected kind | Pins the authorized provider or official authority; another source cannot substitute |
| `requests[].retrieval_identity` | Lowercase bounded identifier | Creates a stable idempotent intent identity within the run and cutoff |
| `requests[].maximum_age_seconds` | Integer from `1` through `86,399,999,999,999` seconds | Marks an otherwise valid observation stale when its derived availability precedes the allowed cutoff window |
| `requests[].required` | Exact boolean | Makes any non-captured outcome fail closed when true; when false, the typed outcome remains durable without blocking otherwise complete required capture |

The Evidence Vault lives at the fixed `evidence-vault/` child of `state_root`. Its directories and
files use private modes `0700` and `0600`; those names and modes are safety constants, not tunable
configuration.

## Repository tooling configuration

| Source | Owns | Editing policy |
| --- | --- | --- |
| `.python-version` | Local Python minor line | Keep aligned with `pyproject.toml` |
| `pyproject.toml` | Package metadata, dependencies, Ruff, mypy, pytest, coverage thresholds, and consequence-tier path classification | Edit directly; use `uv add` for dependencies |
| `uv.lock` | Exact resolved dependency graph | Generated by `uv`; never edit manually |
| `Makefile` | Stable developer command surface | Keep commands thin and composable |
| `.githooks/` | Versioned local commit and push gates | Keep aligned with documented completion criteria |
| `.github/workflows/ci.yml` | Hosted pull-request and main-branch gate | Delegate to `make check`; keep permissions read-only |
| `.github/dependabot.yml` | Low-noise dependency update schedule | Cover `uv` and GitHub Actions without metered services |
| `.gitignore` | Secret, runtime-state, cache, and artifact exclusions | Treat removals from safety exclusions as security-sensitive |

Environment files are ignored, but no runtime `.env` contract exists yet. Adding an environment
variable requires a typed owner, validation, documentation here, and a non-secret test path.
