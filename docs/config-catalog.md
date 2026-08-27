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
| `attention_policy` | Complete object matching the schema below | Included canonically; its own content hash binds every Attention Artifact | Resolved at process composition; it bounds research admission without granting research or execution authority | Safe to log or expose to a model after validation |
| `research_policy` | Complete object matching the schema below | Included canonically; its own content hash is also pinned into the run identity and every production role input | Resolved at process composition; it authorizes only the bounded production research workflow and grants no portfolio or execution authority | Safe to log or expose to a model after validation |

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
Alpaca news, SEC EDGAR, issuer investor relations, Federal Reserve, BLS, and BEA, and at least one
retrieval must be required. Each capture appends intent before consulting the recorded source, then
persists a typed outcome and any immutable content before lifecycle completion. This schema
configures offline recorded adapters only; live HTTP
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
| `requests[].required` | Exact boolean; at least one request must be true in runtime configuration | Makes any non-captured outcome fail closed when true; when false, the typed outcome remains durable without blocking otherwise complete required capture |

The Evidence Vault lives at the fixed `evidence-vault/` child of `state_root`. Its directories and
files use private modes `0700` and `0600`; those names and modes are safety constants, not tunable
configuration.

### Attention policy

`attention_policy` contains every tunable used by the local `SelectAttention` scan. The scan uses no
model, credential, network call, or ambient randomness. Values outside the fixed product ceilings or
the approved weekly exploration ratio fail configuration before runtime storage is prepared. Every
Attention Artifact retains this complete validated policy object, not only its hash, so standalone
artifact validation can prove the published counts and allocations obey the pinned limits.

| Field | Type and validation | Effect |
| --- | --- | --- |
| `schema_version` | Integer; must equal `1` | Versions the policy representation |
| `policy_type` | String; must equal `v0_attention` | Discriminates the implemented local selector |
| `candidate_card_limit` | Integer from `1` through `20` | Caps Candidate Cards published by one cycle |
| `new_dossier_limit` | Integer from `1` through `5`, no greater than `candidate_card_limit` or `weekly_dossier_budget` | Caps new Dossier requests in one cycle; holding refreshes do not consume it |
| `weekly_dossier_budget` | Integer from `new_dossier_limit` through `25` | Caps all new Dossier requests in one ISO week |
| `weekly_exploration_budget` | Positive integer below `weekly_dossier_budget` and exactly 10–20 percent of it | Reserves weekly capacity for otherwise eligible exploration subjects without bypassing later gates |
| `exploration_seed` | Lowercase bounded identifier | Pins deterministic weekly exploration selection; it is policy material, not ambient randomness |

### Production research policy

`research_policy` is the complete production model contract used by `Advance`. It has no default and
cannot be inferred from a model adapter, prior run, or Lab configuration. The policy must contain the
five role contracts exactly once and in this order: Evidence Collector, Thesis Builder, Independent
Skeptic, Scenario Forecaster, and CIO. Unknown fields, a missing or reordered role, inconsistent
nested hashes, or an unsupported schema fails configuration before runtime storage is prepared.

| Field | Type and validation | Effect |
| --- | --- | --- |
| `schema_version` | Integer; must equal `1` | Versions the production research policy representation |
| `policy_type` | String; must equal `v0_production_research` | Discriminates production authority from Research Lab material |
| `maximum_belief_events` | Integer from `1` through `100` | Bounds the as-of Belief Graph supplied to each role |
| `maximum_evidence_artifacts` | Integer from `1` through `100` | Bounds evidence supplied for each selected research subject |
| `role_contracts` | Exact ordered list of the five required roles | Prevents ambient role discovery, omission, or substitution |
| `role_contracts[].prompt` | Schema version `1`, bounded prompt identifier and content, and the SHA-256 hash of that exact content | Pins the complete prompt artifact; the derived prompt fingerprint enters model intent |
| `role_contracts[].model_configuration` | Schema version `1`, exposed model identity, bounded reasoning effort, maximum output tokens, maximum turns, and the SHA-256 hash of that exact material | Pins model identity and resource limits before a call |
| `role_contracts[].tools` | Ordered list of inert tool names with canonical JSON schemas and exact schema hashes; it may be empty | Describes model-visible declarations without granting a callable capability |

`configure_advance` additionally requires an explicit research-owned model port. The keyless recorded
adapter is the only implemented production adapter. There is no ambient model selection, network
client, subscription switch, metered fallback, broker capability, or credential path in production
research composition.

## Constitution governance composition

Constitution governance adds no runtime configuration field or default. Version 1 is the exact
baseline owned by `investment-domain.md`; an amendment enters through `Govern` as a complete immutable
artifact plus its public approval proof and exact future Market Session. Composition injects an
operator-approval verifier and an exchange-session policy. The policy uses a trusted UTC instant and
the exchange calendar to classify the approved boundary as past, current, future, or ineligible;
host-local time is not a fallback. Their implementations, operator identity, signing ceremony, key
provisioning, rotation, and revocation are outside the implemented runtime configuration contract.
Signing secrets never enter the database, configuration fingerprint, receipt, lifecycle state, or
model context. Any future configurable key reference must be typed, non-secret, resolved only at
composition, and documented here before use.

## Research Lab composition

Research Lab replay adds no production runtime configuration field or default. `configure_replay`
requires an explicit bounded namespace, Lab state root, complete set of production state roots, model
port, and repository root. Callers may inject a clock; otherwise composition uses the system clock at
the entrypoint. The Lab root follows the same absolute, symlink-free, repository-safe location rule as
production state and must be disjoint from every production root; composition refuses the request
before creating state when that proof is incomplete or false.

The root directory and fixed `research-lab.sqlite3` file use private modes `0700` and `0600`. Their
names and modes are safety constants, not tunable policy. The namespace is immutable database
metadata, and a different namespace cannot reopen the ledger. Model identity, reasoning limits,
prompt, inert canonical tool schemas and fingerprints, Data Regime, cutoff, Constitution, bounded
Belief Graph, portfolio-context fingerprint, and material input hashes belong to each `Replay` request
and role-specific durable call intent rather than ambient configuration. A resolution request supplies
exactly one ordered contract for Thesis Builder, Independent Skeptic, Scenario Forecaster, and CIO;
there is no ambient role prompt or inherited model conversation. The model port has no default. The
keyless recorded adapter is the only implemented adapter; no metered or network fallback exists.

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
