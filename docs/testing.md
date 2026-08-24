# Testing policy

This document is the source of truth for test tiers, deterministic acceptance scenarios, contract
fixtures, and live rehearsal policy. Tests prove `product-requirements.md` and
`investment-domain.md` through public lifecycle and execution interfaces.

## Tiers

- **Unit — `tests/unit/`:** pure domain and policy behavior with no filesystem, network, wall clock,
  subprocess, or database. Exercise edge cases, invalid states, and deterministic calculations.
- **Integration — `tests/integration/`:** SQLite/filesystem persistence, composition roots,
  checkpoints, projections, and recorded adapter behavior. Deterministic system journeys live under
  `tests/integration/system/` as a named subset. Temporary resources remain isolated.
- **Contract — `tests/contract/`:** hostile model/parser input and recorded SEC, Alpaca, Codex, and
  other external representations. Prove both accepted and rejected forms at the boundary.
- **Live rehearsal:** explicitly selected tests against paper or public services. They require operator
  intent, never run in `make check`, and never make the default suite depend on credentials or network.

`make harness` statically rejects clear effectful imports, calls, and fixtures under `tests/unit/`
before the suite runs. Each diagnostic identifies the test or module scope and the violated rule.
The guard permits value-only use of `Path`, timezone-aware timestamps, immutable dataclasses, and
injected clocks; its bounded syntax check supports but does not replace tier review or behavioral
evidence.

## Deterministic system journeys

A deterministic system journey proves that project-owned components cooperate through one compact
product path. It enters through production composition, invokes public capabilities, uses real local
persistence, and, when the implemented slice includes an authority seam, crosses it through the same
serialized artifact used in production. External providers remain behind their owned ports and use
scripted or recorded adapters.
When the architecture requires process isolation, the journey uses separate processes with explicit
environments. Assertions cover public receipts, authoritative state, rebuildable outputs, observed
external state, and the absence of forbidden effects.

System journeys remain under `tests/integration/system/` while they share the integration tier's
runtime, ownership, environment, and `make check` cadence. Introduce a top-level `tests/e2e/` tier only
after a measured difference in one of those properties makes separate ownership clearer, and update
pytest discovery, Make targets, CI, and this policy together. Directory naming alone never creates a
new acceptance gate.

Keep each journey smaller than the focused tests that support it. A journey covers cross-component
assembly and one representative recovery or refusal; unit, contract, integration, state-machine,
corruption, and mutation tests retain their narrower exhaustive evidence. Add a journey only when its
owning vertical slice exists. Live model, public-source, and Alpaca paper rehearsals remain explicit
operator actions outside deterministic CI and do not certify deterministic correctness or investment
validity.

## Selection

Run the narrowest relevant node while iterating:

```bash
uv run pytest tests/unit/test_package.py
uv run pytest <path-to-test.py>::<test-name>
make check
```

`make check` is the handoff gate: harness and architecture checks, formatting, lint, strict mypy, and
the deterministic pytest suite.

The default Hypothesis profile in `tests/conftest.py` uses derandomized generation, no example
database or wall-clock deadline, bounded example and state-machine step counts, one reported failure,
and printed replay blobs. Generated tests remain credential-free and network-free, inject any clock,
and shrink a failure to a reproducible counterexample; neither Hypothesis shrinking nor replay may
turn a failure into a passing retry.

## Static architecture gates

`make architecture` verifies both the production import graph and the bounded
[capability effect boundary](architecture.md#capability-effect-boundaries). The effect gate protects
all production capabilities except the adapter and entrypoint effect owners, ignores inline lint
suppressions, and fails closed when protected source cannot be parsed or its fixed lint engine cannot
run. A passing result is supporting evidence for explicit dependency injection, not a substitute for
semantic review or observable-behavior tests.

Each contextual rule and prohibited category has compact allowed and denied source fixtures under
`tests/fixtures/capability_dependencies/`. These fixtures end in `.py.txt` because they are inert
source data: pytest, Ruff, mypy, package discovery, and editors must not treat deliberately forbidden
examples as repository Python. The integration test copies selected fixtures to temporary `.py`
production paths before invoking the real gate. An independently maintained catalog fixture must
exactly match the configured APIs and categories; the test synthesizes one denied access from each
fixture entry. Add an entry only with that executable negative evidence and a neighboring allowed seam
that prevents the rule from expanding into general type or control-flow analysis.

## Test contracts

Use the earliest stable enforcement layer defined by the
[executable-invariants policy](architecture.md#executable-invariants). Tests own observable and
temporal behavior that types, constructors, module structure, or seam validation cannot prove; a
passing suite remains supporting evidence rather than proof of complete semantic correctness.

- Assert public receipts, durable events, stored artifacts, projections, packets, and observable world
  state. Avoid private methods and mock call choreography.
- Use fixed timezone-aware clocks, canonical UTC Absolute Instants, deterministic identifiers,
  exchange calendars, content hashes, and explicit evidence cutoffs.
- Prove that equivalent aware offsets normalize identically, while naive, malformed, over-precise,
  and noncanonical durable timestamps fail before authoritative state or downstream effects.
- Exercise the real entry path for the tested tier. A unit may call a domain capability directly; an
  integration test boots the relevant composition with recorded adapters.
- Test lifecycle transition policy as a pure state machine. Exercise generated command, interruption,
  replay, conflict, and reopen sequences against an independent reference model at the persistence
  tier.
- Verify the world after an operation: persisted rows, emitted receipts, filesystem state, packet
  acceptance, broker simulation, and absence of forbidden effects.
- Cover refusal and recovery paths before the happy-path change is complete: stale input, malformed
  schemas, retry, duplicate delivery, timeout, interruption, rollback, partial fill, and corruption.
- Test LLM output for schema, evidence support, role separation, and prohibited authority. Do not pin
  wording, style, or hidden reasoning.
- Prove as-of availability rather than comparing only source timestamps.
- Exercise every instrument, cycle, position, order, and broker-activity discriminator through its
  owner-defined interface. Unknown, disabled, mixed, or version-incompatible variants are negative
  controls and must produce no downstream authority or effect.
- Key fixtures and assertions by canonical `InstrumentIdentity`; use display-symbol changes,
  collisions, and legacy aliases to prove that no join, retry, packet, or reconciliation rule depends
  on a symbol.
- Test provider capability profiles from recorded contracts. Conflicting official enum or order
  support is not normalized to the wider set; an unverified combination remains refused.
- Keep the Research Lab and executor isolation properties under integration or contract test.

## Coverage

`make check` produces machine-readable line and branch coverage through pytest, then applies the
consequence tiers configured in `pyproject.toml`:

- **Critical authority code:** 100% line and 100% branch coverage. This tier contains deterministic
  portfolio sizing, risk clamps, cash preservation, whole-share conversion, Target Bands,
  DecisionPacket construction and authorization, stable effect identity and order planning,
  intent-before-effect transitions, retry and timeout handling, partial-fill replacement,
  reconciliation, authority-sensitive append-only reducers, and exact authority or asset refusals
  as those behaviors are implemented.
- **Safety-supporting code:** at least 90% line and 85% branch coverage. This tier contains provenance
  and as-of validation, universe and position snapshot integrity, non-authorizing lifecycle
  durability and reconstruction, runtime safety configuration, persistence validation, and semantic
  normalization at external boundaries.
- **Production package overall:** at least 85% line and 80% branch coverage. Ordinary wiring,
  serialization, diagnostics, reports, and disposable projections remain subject to this floor and
  require unit, contract, integration, architecture, or reconstruction evidence appropriate to
  their behavior; tests do not exist solely to improve the aggregate percentage.

This section owns the policy meaning; the versioned `tool.coverage_tiers` table is the single
executable owner of current critical and safety-supporting module patterns and threshold values.
Critical patterns cover the complete `portfolio/` and `execution/` capabilities before their first
implementation so new financial-authority modules cannot silently inherit only the package floor.
Every configured pattern must match a Python module below the configured package root, the two tiers
must not overlap, and every production module must appear in the coverage report. Empty, missing,
malformed, conflicting, unmatched, or unmeasurable declarations fail the gate. A module with no
branch opportunities has 100% branch coverage; it is not excluded from classification.

The repository checker calculates line and branch percentages independently from coverage counts and
fails closed on missing branch measurement, malformed JSON, incomplete summaries, or inconsistent
uncovered-line and uncovered-branch details. A failure names the tier, measured and required
percentages, and affected files with their uncovered lines or branch edges. Consequently, aggregate
package coverage cannot hide one uncovered critical line or branch. GitHub Actions delegates to
`make check` and carries no duplicate threshold logic.

Coverage is an execution and design signal, not proof of correctness. Critical behavior still
requires applicable refusal, property, corruption, idempotency, and mutation evidence. Do not add
blanket exclusions, broad `pragma: no cover` directives, trivial assertions, unreachable branches,
or unnecessary mixed-risk module splits to manufacture a passing result. Split a mixed-risk module
only when a precise owned boundary is needed to isolate a critical decision. If a line cannot be
exercised through a useful contract, simplify or remove it.

## Mutation testing

`make mutation` uses `mutmut` to certify **critical authority behavior**: deterministic behavior whose
mutation can directly:

- alter accepted exposure, portfolio weights, cash preservation, sizing, or risk limits;
- construct or admit an unauthorized, invalid, or expired `DecisionPacket`;
- construct, duplicate, omit, or misidentify a broker effect;
- change executor authorization, retry, replacement, partial-fill, timeout, or reconciliation
  decisions;
- corrupt append-only authoritative financial history; or
- bypass an exact refusal that prevents unauthorized execution.

Evidence provenance, universe construction, configuration parsing, serialization, diagnostics,
reports, disposable projections, raw transport, filesystem mechanics, SQL mechanics, composition,
and non-authorizing lifecycle durability use their owning unit, property, contract, corruption,
integration, architecture, and coverage evidence instead. A killed mutant does not make behavior
critical; classification follows the direct consequence of an incorrect result.

The implemented candidate inventory is:

| Candidate source | Classification and evidence owner |
| --- | --- |
| `adapters/filesystem_evidence.py` | Non-critical Evidence Vault filesystem mechanics and append-only observation durability; integration, corruption, and coverage evidence |
| `adapters/recorded_evidence.py` | Non-critical recorded-boundary validation and normalization; contract, hostile-input, and coverage evidence |
| `adapters/sqlite_lifecycle.py` | Non-critical SQL mechanics and non-authorizing lifecycle durability; integration, corruption, architecture, and coverage evidence |
| `application/lifecycle.py` | Non-critical orchestration through evidence capture and bounded attention; unit, integration, system-journey, and coverage evidence |
| `domain/attention.py` | Safety-supporting, non-authorizing admission to bounded research capacity with no stance, sizing, packet, order, or execution effect; unit, property, integration, hostile-input, and coverage evidence |
| `domain/identity.py` | Non-critical current identity serialization with no implemented packet or broker effect; unit, property, contract, and coverage evidence |
| `domain/lifecycle.py` | Non-authorizing lifecycle, evidence-intent, and reconstruction decisions; unit, state-machine, integration, corruption, and coverage evidence |
| `domain/temporal.py` | Non-critical time and provenance validation; unit, property, contract, and coverage evidence |
| `domain/universe.py` | Non-critical universe construction; unit, property, integration, contract, and coverage evidence |
| `evidence/capture.py` | Non-critical evidence identity, availability, staleness, and capture policy; unit, integration, contract, corruption, and coverage evidence |
| `evidence/attention.py` | Safety-supporting derivation of approved local attention features from exact captured evidence; unit, integration, corruption, and coverage evidence |
| `entrypoints/configuration.py` | Non-critical configuration parsing; integration, hostile-input, and coverage evidence |
| `entrypoints/lifecycle.py` | Non-critical composition; integration, system-journey, architecture, and coverage evidence |
| `domain/__init__.py` | Non-critical package scaffold with no callable |
| `execution/__init__.py`, `portfolio/__init__.py` | Critical-authority package scaffolds with no callable; explicit mutation scaffold exemption |

`tool.mutmut.only_mutate` is the single static mutation source allowlist. It contains exact implemented
critical module paths, never package globs or an engine exclusion list. `source_paths = ["src"]` is only
the engine's copy root and confers no mutation scope. The current exact entries are the empty
`execution` and `portfolio` initializers; they bound direct diagnostic runs without claiming that a
critical callable exists. The separate `tool.mutation_gate` inventory names the authority-owning
capability roots and any callable module a human has classified as non-critical; it never adds or
removes a mutation source. A callable below those roots that appears in neither exact classification
fails the gate, so the scaffold exemption cannot hide a new module while classification remains
consequence-based rather than inferred from its path.

Adding the first critical callable adds its exact module and its owning unit or property test files in
the same change. `pytest_add_cli_args_test_selection` contains exact test files, not whole test tiers;
it adds a cross-boundary test only when that test distinguishes a selected critical outcome. The
runner rejects wildcard, symbolic-link, missing, duplicate, out-of-root, directory-wide,
unclassified-authority, or callable-without-test selection before invoking `mutmut`. Extract a pure
kernel only when an implemented critical decision cannot be isolated at file scope; do not add an
empty module for the configuration.

The gate generates mutants on covered and uncovered lines, accepts only killed mutants, and fails on
surviving, uncovered, skipped, timed-out, suspicious, interrupted, crashing, or unaccounted mutants
without reducing them to an aggregate score. `mutmut` does not bind its skipped result category to a
source-adjacent equivalence explanation, so this gate has no approved equivalent-skip mechanism. Add
and review an explicit justification-binding mechanism before accepting any equivalent mutation.
Prefer an observable-behavior test or removal of meaningless code. Source outside the active
allowlist carries no mutation-specific pragma.

The Make target removes the ignored `mutants/` state before every certification, including a file or
symbolic link at that path, so cached diagnostics cannot certify changed authority behavior. Direct
`mutmut` runs remain incremental diagnostics and do not satisfy handoff evidence. If the exact
allowlist contains no critical callable, `make mutation` reports the existing scaffold exemption and
zero mutants without claiming product-level mutation evidence.

Mutation remains separate from `make check`, pre-commit, and pre-push. The pull-request workflow runs
the same clean, keyless Make target only when the PR carries the `mutation:critical` label: applying
the label triggers it, and pushing or reopening the labeled PR reruns it at the latest commit. An
unlabeled PR skips the job. The issue author and reviewer own label classification; no changed-path
classifier assigns authority automatically. `workflow_dispatch` runs the same certification for an
explicit investigation or baseline, and no scheduled mutation workflow exists. Mutation tests use
fake or recorded boundaries and receive no credentials, network path, mutable broker account, model
invocation, or metered service.

## Fixtures

- Record the smallest external representation needed to preserve the contract under test.
- Store no credentials, account identifiers, unpublished research, or mutable local ledgers.
- Annotate the source type, capture or synthetic status, relevant timestamps, entitlement, and any
  intentional redaction.
- Keep fixtures deterministic and portable across macOS and Linux.
- Review fixture changes as behavior changes; never normalize away a material provider difference just
  to preserve an expected file.

## Required deterministic scenarios

Every implementation slice selects its applicable scenarios below and adds both success and refusal
evidence. A scenario becomes mandatory when its owning behavior is implemented.

### Lifecycle and durability

- Advance resumes idempotently across every checkpoint, including termination immediately before and
  after each durable write.
- One Market Session has exactly one Champion Decision Record and at most one active packet.
- Repeated Record calls do not duplicate Outcome Observations.
- Constitution, model, prompt, tool, policy, data, and source hashes remain pinned within a run.
- Universe snapshots retain every current position by canonical instrument identity, apply the
  versioned structural and threshold policy at the pinned cutoff, and fail closed on partial, stale,
  contradictory, mixed-variant, or changed retry inputs.
- Corrupted projections rebuild deterministically; corrupted authoritative ledgers fail closed.
- Lifecycle records persist one fixed-width UTC timestamp representation; retry, reopen, and
  reconstruction preserve it independently of the clock's supplied offset or the host timezone.
- SQLite startup atomically initializes the one current schema or validates that exact current shape.
  Failed initialization returns to an empty unversioned database; every other non-empty version or
  shape and index inconsistency fails closed. Advance validates request-relevant history, while Status
  fails closed on contradictory global cross-ledger history.
- Persistence handles disk-full, transaction rollback, interrupted writes, duplicate event delivery,
  and projection corruption without publishing partial executable state.

### Asset extension seams

- The public `DecisionCycleIdentity` accepts its equity `MarketSession` variant and unwraps it into the
  current Market-Session-specific kernel. A disabled or malformed variant is rejected before the
  clock, adapter, ledger, or state preparation, and a later concrete lifecycle cannot add an
  asset-class branch to that kernel. A valid cycle remains exact in later durable refusal receipts.
- Common receipt, checkpoint, and durable-event envelopes preserve their schema versions, payload
  discriminator, exact cycle where available, authority scope, relevant times, material fingerprints,
  and content hash across retry and reopen. Successful receipt times are the actual recording time,
  not the earlier Evidence Cutoff. A recognized disabled cycle returns a typed receipt before any
  authoritative lifecycle row changes, and an equity success envelope rejects a crypto cycle even
  when every hash is internally consistent.
  `Status.universe_snapshot_cycle` preserves the cycle discriminator and advances atomically with the
  exact eligible-universe snapshot identifier, while `last_completed_cycle` remains unset until a
  durable `Complete` event exists.
- V0 configuration accepts exactly the equity asset-class set. Crypto, options, unknown classes,
  duplicate activation, provider entitlement alone, and class/policy mismatch fail before runtime
  state, research, portfolio, packet, credential, network, or broker effects.
- Universe and position snapshots retain canonical instrument identities and aliases separately,
  require globally one-to-one alias and provider-catalog mappings under the adapter's
  provider-and-environment namespace, reject unresolved references, and preserve a disabled-class
  holding as an explicit portfolio mismatch rather than dropping or activating it.
- Snapshot and durable-record readers select the closed variant from its discriminator before
  validating exact payload fields. Instrument and position snapshots preserve observed and available
  times, Data Regime, authority scope, source fingerprint, and their own content hash; position
  variants preserve signed quantity, unit, and valuation amount, currency, and source. Retry, reopen,
  and projection rebuild rederive canonical cycle, instrument-snapshot, position-snapshot, and policy
  hashes rather than trusting stored summaries.
- `PinRunInputs` persists the first full normalized universe provenance once. Interruption followed
  by an alias-only adapter change must publish that prepared snapshot, while material changes still
  conflict and completed retry remains idempotent.
- Asset payload unions reject missing or unknown discriminators, unsupported schema versions,
  unrelated optional fields, mixed equity/crypto/option fields, implicit units, and implicit currency
  or multipliers.
- A future crypto cycle may complete while the NYSE calendar has no eligible session; its 24/7 clock,
  pair quantities, increments, venue, order policy, and fee currency remain inside crypto-owned
  variants.
- A future option exercise, assignment, or expiration reconciles the contract, the owner's underlying
  position, and cash from independent append-only observations. Delayed or missing activity, changed
  positions, duplicate delivery, and contradictory multipliers remain unresolved and block
  discretionary progress. An index or unknown underlying is refused before equity-owned schedule or
  settlement policy.
- Adding an asset lifecycle leaves the existing MarketSession phase order, common durable envelope,
  snapshot hashes, packet integrity, receipt reconstruction, effect keys, and projection rebuild
  evidence unchanged. The new lifecycle proves its own phase and checkpoint behavior without changing
  the MarketSession kernel.

### Evidence, memory, and research

- Evidence unavailable at the cutoff is rejected even when its source-event time is earlier.
- SEC acceptance times, amendments, restatements, duplicate content, and versioned entity mappings
  cannot leak future information into an as-of read; mapping availability participates in the derived
  evidence availability.
- Belief transitions preserve prior values, evidence, falsifiers, and contradiction history.
- Belief Graph rebuild produces the same as-of projection from the Evidence Vault and Belief Ledger.
- Attention caps, holding refresh, and weekly exploration budgets remain invariant as universe size
  and ordering change. Identical pinned retry and reopen preserve cards, transitions, selections,
  counts, and artifact identifiers without duplicate authoritative rows. Hostile tests reject policy
  spoofing, unsupported card reasons, discontinuous transition history, changed cutoffs or Data
  Regimes, and artifact timestamps that precede publication. Real Evidence Vault integration preserves
  unavailable optional sources as missing features and proves missing or corrupt checkpoint content
  produces a typed durable refusal with no attention event. Resuming an older partial cycle after a
  later selection has published likewise refuses rather than retroactively changing the append-only
  transition chain. A changing-clock interruption test proves retry preserves deterministic selection
  identity while the envelope content hash records the later truthful publication time.
- Model output containing a weight, order, unsupported citation, prompt injection, invalid enum,
  inconsistent horizon, missing Skeptic, timeout, quota exhaustion, or oversized result is rejected or
  safely normalized only as its schema explicitly permits.
- Research Lab artifacts cannot enter Champion stores or produce a packet accepted by execution.

### Portfolio and execution

- Randomized portfolios respect gross, name, sector, liquidity, correlation, cash, whole-share, and
  no-leverage rules.
- Capped inverse-volatility sizing never increases allocation from model confidence and handles
  volatility floors, missing history, stale input, and caps deterministically.
- Equal-weight and Risk Profile shadows consume the same HouseView, eligibility, Evidence Cutoff, and
  available-at-time prices as the Champion.
- Target Bands create no trade inside the band and apply only the approved partial adjustment outside
  it.
- Gaps, events, halt or LULD state, stale quotes, buying power, account or position mismatch, packet
  expiry, and pending orders block or reroute orders exactly as policy states.
- Stable client order identifiers prevent duplicate exposure after timeout, retry, cancellation, and
  partial fill.
- Only Balanced Champion packets are accepted by the Alpaca paper executor.
- The executor cannot alter packet direction or target, invoke a model, accept a Research Lab packet,
  or proceed from incomplete validation.

### Evaluation and governance

- Forecast and benchmark outcomes resolve only after their declared horizons.
- Cost, turnover, return, Sharpe ratio, information ratio, drawdown, calibration, and constraint
  metrics match hand-calculated fixtures.
- A single journal outcome cannot activate a lesson; support, contradiction, bounded influence,
  reinforcement, and expiry follow the investment-domain contract.
- A Challenger cannot alter more than one declared variable, promote itself, or activate without
  operator-approved governance.
- Quota exhaustion cannot select a metered fallback or publish new discretionary orders.
- Invalid formal sessions remain visible and cannot be silently removed or backfilled.

## External contracts and fault injection

- Recorded Alpaca market-data and trading fixtures cover normal, stale, unavailable, partial,
  rejected, canceled, ambiguous-timeout, and out-of-order events.
- Recorded Alpaca market and news evidence fixtures cover feed and entitlement mismatches, malformed
  outer or payload timestamps and enums, hash-consistent but invalid normalized content, inconsistent
  payload or entity identifiers, ambiguous entity mapping, oversized content, duplicate content
  observations, complete lifecycle-to-intent reconstruction across policy changes, missing or
  corrupt pinned policy snapshots, changed durable refusal policy references, interrupted policy
  deletion, unrelated corrupt lifecycle history, derived availability, and inert hostile external
  text.
- Recorded Alpaca fixtures keep equity assets, crypto pairs, option contracts, positions, orders,
  account entitlements, fills, fees, and non-trade activities in their raw adapter-owned shapes. They
  cover provider ID and alias mapping, feed entitlement, quantity and currency precision, option
  underlyings and multipliers, and delayed exercise, assignment, or expiration activity.
- When current Alpaca references disagree about a field name, activity code, order form, or
  time-in-force, contract evidence pins the accepted capability profile and rejects the unresolved or
  wider interpretation. Tests never choose a provider contract from prose recency alone.
- Recorded SEC, issuer, Federal Reserve, BLS, and BEA fixtures cover source and document identities,
  acceptance or publication times, first observation, derived availability, parser identity,
  amendments, duplicate content, versioned or ambiguous mappings, required and optional absence, and
  hostile embedded instructions. These deterministic tests use no network, credentials, metered API,
  or model retrieval.
- Scripted model fixtures cover valid output, hallucinated citations, prohibited authority, missing
  Skeptic, conflicting schemas, long output, timeout, and quota exhaustion.
- Scheduler fixtures cover weekends, exchange holidays, early closes, daylight-saving transitions,
  machine sleep and wake, late starts, and resume behavior.
- Changed external or authority seams prove the absence of network, credential, broker, Champion,
  packet-publication, or other downstream effects on every rejected path. A trustworthy request key
  instead asserts exactly its permitted bounded refusal under the configured runtime-state root; a
  configuration refusal before state-root preparation asserts no filesystem change.

## Project skill catalog

Project skill frontmatter is routing metadata, not a duplicate workflow contract. Every
`.agents/skills/*/SKILL.md` frontmatter uses canonical unquoted top-level keys and has one unquoted,
non-empty, single-line printable-ASCII YAML plain-text `description` of at most 320 characters. All
project descriptions together use at most 3,200 characters. Alternate key spellings, control
characters, and quoted, block, folded, collection, typed, or continued YAML values are outside this
deliberately narrow catalog format. The detailed positive triggers, exclusions, delegation rules,
approvals, safety boundaries, and terminal outcomes remain in the skill body.

`make harness` runs `scripts.check_skill_catalog` and its integration fixtures. The checker fails
closed on missing or malformed descriptions and reports the path of each individual overage; an
aggregate overage lists every contributing skill and its measured character count. Character counts
are a deterministic repository guard, not a promise about a particular Codex model's tokenizer or
the metadata contributed by globally enabled plugins. Use the supported operator baseline in
[`development.md`](development.md#codex-plugin-baseline) when collecting clean-session evidence.

## Agent workflow scenarios

Versioned contracts under `.agents/harness/scenarios/` exercise repository agent routing, required
decisions, terminal dispositions, and observable effects. Each scenario names only current repository
files and skills, binds an isolated fixture by SHA-256, and uses the decision and effect vocabulary in
`.agents/harness/decision-catalog.json`. A scenario that permits guarded worktree startup also binds
the exact issue number accepted at that boundary. `make harness` validates the schemas, references,
fixture integrity, and evaluator deterministically; it never invokes a model.

Delivery scenarios distinguish a complete same-session handoff for an unchanged exact base and head
from direct claims and absent, incomplete, stale, or mismatched evidence. The harness places simulated
same-execution delivery state in its reserved control context rather than the untrusted external
fixture. Its hash-pinned template is materialized only with the disposable workspace path and exact
fixture refs, while trusted reviewer copies remain outside the tested diff. The run record pins both
the template and materialized digests plus the substituted path and refs. Exact-reuse scenarios
require a direct read of the active delivery ledger and separate observable checks of the exact base,
exact head, whole-worktree cleanliness, and expected issue in the expected repository. Only canonical
full-object-ID ref reads and status forms that cannot suppress untracked changes or narrow paths
qualify. Every active-ledger scenario requires all of these live observations even when one mismatch
is already visible. They also require direct SHA-256 observations for the general and safety
reviewers; help, version, check mode, and ambiguous or conflicting algorithm selectors do not
qualify. Isolated scenarios change only the reviewed head, one reviewer identity, or the
safety-review selection. A reviewer-identity mismatch with an existing ready pull request must
resolve that pull request from the expected issue branch, invoke the demotion-only safeguard on the
same pull request, and read back its draft state in order. Only a complete handoff whose scope,
exact refs, checks, review selection, and immutable per-axis reviewer identities still match may
satisfy publication prerequisites without repeating them; stale live refs remain a refusal.
Review-remediation scenarios separately exercise Low advisory judgment calls, Low contractual
must-fix findings, complete finding batches, one full gate per batch, cap exhaustion and ready-pull-
request demotion, and regressions introduced during remediation. Review-equivalence scenarios require
the final disposition to distinguish clean conflict-free base updates, independently reviewed manual
conflicts, and isolated counterexamples for changed patches, authority, reviewer content, review
selection, consumers, blast surfaces, unresolved safety reachability, and uncertainty reported while
reviewing a manual conflict. Mechanical Git signals are supporting observations, never an equivalence
verdict. These scenarios verify routing and declared effects; they do not implement a deterministic
review engine or replace semantic review.
Scope-delta scenarios require proposed domain,
durable-state, configuration, interface, ownership, and external-source concepts to map to the issue
and active authority before implementation or reviewer fan-out.
Post-merge reflection scenarios bind the expected repository and pull-request number to a direct
pull-request observation. They distinguish a materially significant merged subject from unmerged,
mismatched, or incomplete evidence and forbid filesystem, Git, GitHub, credential, broker, and network
effects in both cases. Their synthetic pull-request view reports a deterministic commit containing
exactly the advertised changed paths; the run record pins the rendered view digest and base/head
identities. A passing reflection scenario proves routing and bounded decisions, not that a particular
follow-up is correct or authorized for publication.

An operator runs one model-backed scenario explicitly:

```bash
make agent-workflow SCENARIO=issue-publication-awaits-approval
```

The runner copies only scenario-declared repository files into a temporary Git repository and
supplies fake GitHub and disabled external commands. Supported Codex CLI 0.149.x runs with `exec
--ephemeral --json`, a structured final-output schema, and a granular permission profile that denies
the filesystem root, admits only Codex's minimal runtime paths and the disposable workspace as
read-only, denies temporary-directory and command-network access, and disables approval escalation.
The Codex client retains the operator's authentication environment, but its model-generated local
commands do not inherit that environment and cannot read outside the permission profile. App,
plugin, browser, computer-use, image, multi-agent, and tool-discovery features are disabled. Missing
authentication or executables, unsupported CLI versions, timeouts, nonzero processes, malformed or
incomplete JSONL, and ambiguous effects are explicit non-passing outcomes.

Evaluation derives skill routes only from successful, direct, top-level full-file reads of the
declared `SKILL.md` files and compares them with the final-output claim. It requires the exact scenario
decision set, checks dispositions, every observed tool attempt, directly proven successful required
effects, and final filesystem and Git state, and records the accepted decisions in the result.
Compound or wrapped commands still expose forbidden attempts but cannot supply positive route or
required-effect evidence. Path-limited or dirt-suppressing Git status, abbreviated ref reads, and
GitHub queries for another repository, issue, branch, or pull request do not satisfy the corresponding
observation. The fake GitHub boundary refuses an explicitly wrong repository before returning fixture
state. GitHub help, version, web, and ambiguous compact short-option modes do not prove a subject
observation or mutation. Reviewer
identity requires a digest-producing `sha256sum` invocation or one unambiguous
explicit SHA-256 selection with `shasum`; weaker, non-hashing, multiple, or conflicting selectors do
not satisfy the effect. It never compares exact prose or hidden reasoning. Unknown event,
item, command, GitHub, or MCP shapes fail closed.
Every result records hashes for the scenario, fixture, skills, copied repository files, output
contracts, runner, prompt, and execution policy, plus an in-band UTC timestamp, the source revision
and dirty state, Codex version, exposed model identity, observed effects, disposition, and failure
classification under
ignored `.agents/harness/results/`. Results are advisory review evidence: they cannot approve
workflow changes, authorize publication, replace an independent review, or become repository
authority. Model-backed scenarios remain outside Git hooks and default CI; `make harness` exercises
the deterministic evaluator tests but never invokes a model.

## Forward acceptance

An end-to-end Alpaca paper rehearsal runs only with explicit operator intent. Before the formal
baseline begins, it must demonstrate lifecycle completion, packet handoff, paper execution,
reconciliation, reporting, and safe recovery while the baseline configuration is frozen and hashed.
