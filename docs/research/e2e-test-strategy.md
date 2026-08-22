# End-to-end test strategy

Status: non-authoritative research input

Date: 2026-08-22

## Question

What end-to-end (E2E) test strategy best verifies this system's architecture and correctness without
weakening its deterministic, credential-free development gate or confusing paper-trading behavior
with investment validity?

## Executive conclusion

**Recommendation.** Treat E2E as a topology within the existing test policy, not initially as a new
test tier. Deterministic product journeys should start under `tests/integration/system/` because they
boot real composition, use real SQLite and filesystem state, cross public capability seams, and
replace only external providers with scripted or recorded adapters. Create `tests/e2e/` only after a
measured runtime, ownership, environment, or orchestration difference makes these tests materially
different from integration tests.

The complete strategy has three complementary lanes:

1. **Required deterministic system journeys:** keyless and network-free journeys through production
   composition, authoritative stores, atomic artifact handoff, and separately spawned process seams.
   These belong in `make check` while they remain within its feedback budget.
2. **Boundary and invariant evidence:** contract fixtures, independent state-machine oracles,
   property tests, corruption tests, and targeted mutation testing. These make failures local and
   prevent a shallow happy-path journey from supplying false confidence.
3. **Explicit external rehearsals:** operator-invoked Codex evaluations and Alpaca paper rehearsals.
   They verify integration drift and operational readiness, but remain outside deterministic CI and
   cannot certify deterministic correctness or investment performance.

The suite should grow with each implemented vertical slice. The current repository implements only
`Advance` through `PinRunInputs` and rebuildable `Status`; research, portfolio, execution, evaluation,
and the full operator path remain scaffolds ([current status](../../README.md#status)). Current
Stage 1 integration tests are strong foundations for lifecycle E2E behavior, but the repository must
not claim full-product E2E coverage before the remaining public capabilities and operator entrypoints
exist.

## Authority and evidence base

This note does not change product, architecture, investment, or testing policy. Any accepted change
to tiers, directories, commands, gates, or live-test policy belongs in
[`docs/testing.md`](../testing.md); a runtime seam or process-boundary change belongs in
[`docs/architecture.md`](../architecture.md).

The recommendation follows these current repository contracts:

- Acceptance is proved through public lifecycle and execution interfaces, and deterministic
  acceptance tests require no network, credentials, or mutable local account
  ([testing policy](../testing.md#test-contracts),
  [operational constraints](../product-requirements.md#operational-constraints)).
- The system has three authority seams: the uncredentialed Investment Operating System, the
  credentialed executor without model capability, and the isolated Research Lab
  ([system topology](../architecture.md#system-topology)).
- The production lifecycle is checkpointed and append-only; every external effect has prior durable
  intent and a subsequent observed result ([session lifecycle](../architecture.md#session-lifecycle),
  [defensive patterns](../defensive-patterns.md#persist-intent-before-effects)).
- Tests should assert receipts, durable records, artifacts, projections, packets, and world state
  rather than private methods or mock choreography
  ([test contracts](../testing.md#test-contracts)).
- Required scenarios become mandatory only when their owning behavior is implemented
  ([required deterministic scenarios](../testing.md#required-deterministic-scenarios)).
- The architecture assigns enforcement to the earliest stable layer: types, module structure, seam
  validation, deterministic tests, then semantic review
  ([executable invariants](../architecture.md#executable-invariants)). E2E therefore complements,
  rather than replaces, unit, module-graph, contract, mutation, and review evidence.

## What E2E should mean here

**Recommendation.** Define a deterministic system journey as a test that:

- enters through a real composition root or, once implemented, the operator-facing process command;
- invokes only public lifecycle or execution capabilities;
- uses the real domain, application, validation, SQLite, filesystem, packet publication, and
  projection code in that path;
- crosses an authority boundary through the same serialized artifact used in production, rather than
  passing an in-memory object directly;
- replaces network, model, exchange-calendar feed, and broker services at their owned outer ports
  with deterministic test implementations;
- runs separate operating-system processes where the architecture promises process or credential
  isolation; and
- verifies externally observable state plus the absence of forbidden effects.

This definition intentionally stops the required suite at provider ports. “End to end” does not mean
that every pull request contacts Codex, SEC, issuer sites, or Alpaca. It means that all project-owned
links in one product journey execute together while external behavior is controlled and inspectable.

The following evidence classes should remain distinct:

| Evidence class | Real components | Replaced components | Primary claim |
| --- | --- | --- | --- |
| Unit/property | One deterministic owner | Filesystem, database, processes, providers | Local policy, arithmetic, and invariant correctness |
| Contract | One hostile external seam and its parser/mapper | Provider transport | Accepted and rejected external representations |
| Integration/system journey | Production composition, stores, public capabilities, artifact handoffs | External providers | Project-owned components cooperate correctly across a journey |
| Process-isolation journey | Real subprocesses, serialized packets, separate roots and environments | External providers | Authority and credential reachability match the topology |
| Live rehearsal | Deployed local composition and selected real paper/public services | No in-scope provider replacement | Current external compatibility and operator readiness |
| Forward paper evaluation | Frozen prospective decisions and outcomes | Live capital | Evidence about the investment hypothesis, not software correctness |

The final distinction is required by the investment contract: historical replay verifies mechanics,
leakage resistance, failure behavior, and cost sensitivity, while forward paper outcomes are the
primary evidence for investment validity
([mandate and proof standard](../investment-domain.md#mandate-and-proof-standard)).

## Placement and suite shape

### Start inside integration

**Repository fact.** The existing integration tier already owns SQLite/filesystem persistence,
composition roots, checkpoints, projections, and recorded adapter behavior
([test tiers](../testing.md#tiers)). Current tests call the Stage 1 composition root, reopen real
SQLite state in a fresh process, inject interruptions around each write, compare generated sequences
with an independent reference model, and verify append-only and corruption behavior
([Advance integration tests](../../tests/integration/test_advance_lifecycle.py)).

**Recommendation.** Add deterministic product journeys as a clearly named subset:

```text
tests/
  unit/
  integration/
    system/
      test_stage1_lifecycle_journey.py
      test_research_refusal_journey.py
      test_execution_recovery_journey.py
      test_market_session_journey.py
  contract/
```

Do not move or duplicate every existing integration test. A system test should cover one compact
cross-capability journey; focused integration tests retain the exhaustive boundary, corruption, and
rollback matrices. Shared scripted adapters can live beside the system tests or in a test-only support
package once more than one scenario needs them. Production code must never import that package.

Introduce a top-level `tests/e2e/` only when at least one of these conditions becomes true:

- its runtime cannot fit the normal integration feedback budget;
- it requires a distinct environment or artifact-retention policy;
- it needs cross-process orchestration with independent ownership;
- it must run on a different cadence or deployment platform; or
- selecting it independently is materially clearer than its existing integration ownership.

If that point arrives, update the authoritative testing policy, pytest discovery, Make targets, and CI
together. Directory naming alone must not silently create a new acceptance gate.

### Keep the scenario harness small

**Recommendation.** Use ordinary pytest tests, typed fixture builders, and explicit scenario data.
Do not build a general workflow DSL or reuse the repository-agent workflow harness for product E2E.
The lifecycle kernel is intentionally specific until a second concrete lifecycle proves a smaller
shared abstraction
([lifecycle-kernel decision](../../.agents/notes/implemented/2026-08-22-keep-lifecycle-kernel-specific.md)).
Extract a declarative scenario schema only after several product journeys demonstrate stable repeated
fields and a real need for independent replay tooling.

## Recommended test topology

### Deterministic operating-system composition

Boot the uncredentialed operating system with:

- a private temporary runtime root;
- real configuration resolution and composition;
- real lifecycle, evidence, memory, research validation, portfolio, evaluation, SQLite, filesystem,
  projection, and packet-store implementations as each becomes available;
- a scripted, monotonic clock and frozen exchange-calendar fixture;
- recorded evidence-source adapters; and
- a scripted model adapter that captures its validated request and returns a selected hostile or valid
  representation.

The test should call `Advance`, `Status`, `Record`, and `Govern` only through their public interfaces.
It should inspect returned receipts and canonical read APIs first; direct SQL inspection is a secondary
storage oracle for append-only, sequence, and projection claims.

### Deterministic executor composition

Start the executor separately and give it only:

- the serialized, atomically published `DecisionPacket` or its identifier;
- an isolated executor ledger root;
- a synthetic paper-broker credential sentinel confined to the child environment;
- a scripted broker implementation; and
- the same deterministic clock and recorded market state required for independent validation.

The executor must deserialize and revalidate the packet. Passing an already validated Python object
from the operating-system process would bypass the architecture's most important trust boundary
([safe execution handoff](../architecture.md#safe-execution-handoff)).

### Scripted broker, not call mocks

**Recommendation.** Implement a small stateful broker simulator behind the execution-owned broker
port. It should understand submit, query-by-client-ID, cancel, order events, positions, account state,
and deterministic fills. Scenario scripts should cover accepted, rejected, canceled, unavailable,
ambiguous-timeout, partial-fill, duplicate, and out-of-order events, matching the existing external
fault contract ([external contracts](../testing.md#external-contracts-and-fault-injection)).

The simulator should maintain an independent world state and an append-only request/response
transcript. It must not call production order-planning or reconciliation helpers; otherwise the system
and its oracle can share the same defect. Contract tests should separately feed recorded Alpaca JSON
through the production mapper. Where practical, one adapter integration test should run the production
Alpaca adapter against a fake transport to join request mapping with the semantic simulator, but broad
system journeys need not repeat every provider representation.

Alpaca provides lookup by client order ID, so an ambiguous submission can be reconciled by the stable
domain identifier rather than resubmitted blindly
([Alpaca order lookup](https://docs.alpaca.markets/us/reference/getorderbyclientorderid)).

### Scripted model boundary

**Recommendation.** A deterministic model adapter should return bounded fixtures keyed by role and
validated request fingerprint. It should capture the Constitution hash, prompt/model/tool identity,
Evidence Cutoff, evidence hashes, and portfolio context seen by each role. Fixtures should include:

- valid role outputs;
- hallucinated or missing evidence references;
- a weight or order instruction;
- missing clean-context Skeptic output;
- invalid enums, inconsistent horizons, conflicting or extra fields;
- prompt injection embedded in evidence;
- refusal, timeout, quota exhaustion, and oversized or truncated output; and
- a syntactically valid result whose values violate evidence or authority rules.

Schema conformance is necessary but is not a semantic oracle. OpenAI documents that Structured
Outputs can still contain incorrect values and can diverge on refusal or interrupted generation
([Structured Outputs limitations](https://openai.com/index/introducing-structured-outputs-in-the-api/#limitations-and-restrictions)).
The production validators must therefore remain inside the journey, and live-model results must not
replace hostile fixture coverage.

### Evidence, clock, cutoff, and provenance

Use a scenario clock that advances only when the test asks it to. Represent both UTC and exchange
time explicitly and exercise weekends, holidays, early closes, daylight-saving transitions, late
starts, and sleep/wake recovery. No assertion should depend on the machine's current date or `sleep()`.

At minimum, every evidence journey should contain a metamorphic pair:

- the same source-event time with availability before the cutoff, which may be admitted; and
- the same source-event time with availability after the cutoff, which must be rejected.

Add cases for identical content observed twice, later amendments/restatements, changed entity mapping,
and a Data Regime change. Assert the evidence, parser, entitlement, configuration, Constitution,
prompt, model, tool, and cutoff fingerprints carried into material outputs. These are direct
consequences of the repository's availability and reconstructability contract
([evidence and as-of truth](../investment-domain.md#evidence-and-as-of-truth),
[durable state](../architecture.md#durable-state)).

### Append-only durability and replay

Every journey that writes authoritative financial state should prove all of the following:

1. retrying the same effect identity changes neither exposure nor authoritative record count;
2. replay returns the prior durable disposition or resumes at the last committed boundary;
3. a correction appends a superseding record and preserves the prior record;
4. deleting or corrupting a projection produces the same canonical projection after rebuild;
5. corrupting authoritative state fails closed and does not publish an executable artifact;
6. a new process reconstructs the same receipt, packet hash, status, and logical world state; and
7. the ex-ante decision is byte- or field-stable under later outcomes, apart from separately appended
   observations.

Compare canonical logical records and content hashes rather than SQLite database bytes. Raw database
files contain storage-level details that are not product behavior. If a test needs to copy a live
database, first reach quiescence or use SQLite's backup facilities; SQLite warns that copying the
database file during a transaction can create an inconsistent copy
([SQLite corruption guidance](https://www.sqlite.org/howtocorrupt.html#_backup_or_restore_while_a_transaction_is_active)).

### Fault and crash matrix

The most valuable system failures sit around the durable/effect boundary. For each external effect,
exercise these deterministic interruption points:

| Interruption point | Required recovery observation |
| --- | --- |
| Before durable intent | Retry may create one intent and one effect |
| After intent, before effect | Resume from intent using the same effect identity |
| After provider acceptance, before local response | Reconcile by stable identity; never duplicate exposure |
| After observed result, before phase advance | Replay the stored result without repeating the effect |
| During projection or digest publication | Authoritative history remains valid; derived output rebuilds atomically |

Use port wrappers, subprocess barriers, transaction-failure triggers, and scripted provider faults at
stable seams. Avoid production `if testing` branches. Run a child process for restart behavior and
terminate it only after an explicit harness barrier, so the scenario is repeatable rather than timing
dependent.

Focused tests should cover disk-full, rollback, canceled tasks, corrupt rows, and compound recovery
failures. A smaller system matrix can select one representative failure from each class and rely on
focused integration tests for the full combinatorics. SQLite itself uses I/O fault injection,
separate-process crash simulation, post-crash state checks, and `PRAGMA integrity_check`; that is a
useful primary-source model for application-level recovery testing, not a reason to reproduce
SQLite's internal test suite
([how SQLite is tested](https://www.sqlite.org/testing.html#crash_testing)).

### Process, credential, and Research Lab isolation

Class boundaries and monkeypatched environment variables cannot prove process isolation. Add
subprocess journeys that construct each architecture seam with an allowlisted child environment:

- the operating-system/research process receives no broker credential names or values;
- the executor receives only synthetic broker secrets and no model client, model token, or model
  executable path;
- the Research Lab receives a distinct copied or synthetic root and no writable Champion packet or
  executor state path; and
- rejected packets, model output, and lab artifacts produce no broker transcript entry.

Use synthetic sentinels, then scan captured model requests, durable records, reports, diagnostics, and
subprocess output for absence of those values. Record only a boolean or hash proving the executor saw
its synthetic secret; never emit the value. Combine these dynamic checks with the existing module
graph, which statically rejects forbidden Python import direction but does not prove runtime
environment isolation ([module rules](../module-graph.md#rules)).

## Oracle strategy

No single golden output is strong enough for this system. Use independent, layered oracles:

| Oracle | What it checks | Independence requirement |
| --- | --- | --- |
| Public receipt | Disposition, phase, refusal, recovery, identifiers | Assert typed meaning, not prose |
| Authoritative history | Event order, uniqueness, append-only behavior, pinned facts | Canonical read or narrow SQL; never a projection alone |
| External world | Orders, fills, positions, and absence of forbidden calls | State owned by the scripted provider, not execution helpers |
| Reference state machine | Legal transitions over retries, crashes, conflicts, and reopen | Tests-only model with simpler state and no production transition calls |
| Replay equivalence | Rebuilt projections, reports, packets, and status | Compare canonical values and hashes from a new process |
| Financial calculation | Sizing, limits, costs, returns, and metrics | Hand-calculated fixtures, properties, and metamorphic relations |
| Security/isolation | Secret and capability reachability | Child environment, filesystem permissions, and observed effects |
| Cross-cutting invariant | One Champion decision, at most one active packet, no duplicate exposure | Derived independently from all relevant stores and provider state |

The current lifecycle suite already demonstrates the reference-model pattern by comparing generated
command sequences against a tests-only state machine
([Stage 1 state machine](../../tests/integration/test_advance_lifecycle.py)). Hypothesis rule-based
state machines are designed to generate chains of actions and can compare a system with a simpler
in-memory model
([Hypothesis stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html)). Keep the
default derandomized profile for reproducibility, print replay blobs, and use a higher bounded example
count only in the extended lane ([current profile](../../tests/conftest.py)).

Mutation testing should continue to prove that deterministic safety assertions are sensitive to wrong
behavior; it should not mutate transports, logs, or live harnesses
([mutation policy](../testing.md#mutation-testing)). Also test the test doubles: for example, prove the
broker simulator exposes a duplicate submission, refuses a prohibited order type, and can represent
“accepted but response lost.” Otherwise a fake that silently normalizes unsafe behavior can make every
system scenario pass vacuously.

## Minimum scenario portfolio

Each implemented slice should add a happy path and its highest-consequence refusal before moving on.
The eventual V0 system portfolio should include:

| Scenario family | Essential journey and oracle |
| --- | --- |
| Lifecycle replay | Advance, interrupt at every checkpoint, reopen, resume, replay; one stream and pinned identity |
| Evidence cutoff | Identical event time across opposite availability cutoffs; no future evidence in Dossier or decision |
| Research authority | Complete roles including clean-context Skeptic; hostile weight/order/citation output is rejected before portfolio |
| Abstention/cash | Valid no-action path publishes a digest and no active discretionary packet or broker effect |
| Champion and shadows | One HouseView and cutoff feed Balanced plus shadows; only Balanced can produce an executable paper packet |
| Execution refusal | Stale quote, halt/LULD, gap, account/position mismatch, pending order, buying power, or expiry blocks submission |
| Ambiguous acceptance | Broker accepts and loses response; restart queries stable client ID and creates no duplicate exposure |
| Partial fill | Partial fill, out-of-order event, bounded cancel/reprice, restart, and reconciliation preserve packet direction and envelope |
| Projection/ledger fault | Projection corruption rebuilds identically; authoritative corruption fails closed |
| Research Lab isolation | Copied/synthetic replay cannot write Champion state, publish an accepted packet, or reach broker |
| Scheduler recovery | Weekend, holiday, early close, DST, sleep/wake, missed start, and resume remain visible and deterministic |
| Quota exhaustion | No metered fallback, no incomplete discretionary packet, durable reason visible |
| Governance boundary | Approved future change does not alter an active run and appears only at the allowed session boundary |
| Evaluation horizon | Outcomes resolve only when due and cannot modify the frozen ex-ante Decision Record |
| Invalid formal session | Incomplete or invalid session remains recorded and cannot be removed or backfilled |

A compact full-session smoke should eventually traverse:

```text
Advance -> captured evidence -> validated roles -> HouseView -> deterministic portfolio
-> atomic DecisionPacket -> Apply -> broker simulation -> Reconcile -> Record -> digest -> Status
```

It should not absorb the full fault matrix. Separate journey families make the failed contract clear
and keep diagnosis tractable.

## Live Alpaca and model rehearsals

### Alpaca paper

**Recommendation.** Place live rehearsal code under a structurally excluded location such as
`tests/live/` only when pytest's default discovery is explicitly limited to deterministic directories.
Invoke it through a dedicated Make target or operator command that cannot be enabled merely by the
presence of credentials. Require an explicit paper endpoint, a bounded allowlisted action, a frozen
configuration hash, a quiescent starting account check, and a scrubbed artifact bundle. Never reset or
delete a paper account automatically.

Run the live paper rehearsal:

- before the formal baseline, as product acceptance already requires
  ([forward acceptance](../testing.md#forward-acceptance));
- after a material Alpaca adapter or execution-policy change, under explicit operator control; and
- periodically during the baseline only as an operational compatibility check, not as a PR gate.

Alpaca describes paper trading as a simulation and lists omitted effects including market impact,
latency slippage, queue position, price improvement, fees, and dividends
([Alpaca paper-trading limitations](https://docs.alpaca.markets/us/docs/paper-trading#paper-vs-live)).
Therefore a passing paper rehearsal proves endpoint compatibility, packet-to-receipt flow, and safe
recovery under that simulator. It does not prove live fill quality, market impact, profitability, or
micro-live safety.

### Live model

Run pinned model/prompt scenario evaluations only when model, prompt, tools, role contract, or
Constitution context changes, and optionally as an explicit periodic drift check. Evaluate schema,
evidence support, role separation, authority, citations, disposition, latency, and resource use across
several representative fixtures. Record model and input fingerprints and treat the result as advisory.
Do not compare exact wording or let one successful sample promote a model or prompt. This mirrors the
repository's existing separation between deterministic workflow-harness evaluation and explicit
model-backed scenarios ([agent workflow scenarios](../testing.md#agent-workflow-scenarios)).

## CI cadence and budgets

**Repository fact.** `make check` currently runs the deterministic suite in an Ubuntu job with a
ten-minute timeout; targeted mutation testing runs separately on relevant pull requests and weekly on
macOS ([CI workflow](../../.github/workflows/ci.yml),
[mutation workflow](../../.github/workflows/mutation.yml)).

**Recommendation.** Use this cadence:

| Cadence | Required evidence |
| --- | --- |
| Developer loop | Narrow unit, contract, integration, or one system node |
| Every pull request / `make check` | All deterministic unit, contract, integration, and compact system journeys; no credentials or network |
| Relevant pull request | Existing targeted mutation gate; adapter contract fixtures when their seam changes |
| Main or nightly | Extended stateful sequences, full fault matrix, higher example counts, and deployment-platform macOS process/filesystem smoke |
| Weekly | Existing mutation drift gate plus optional long replay/corruption campaign |
| Explicit operator | Pinned model scenarios and Alpaca paper rehearsal |
| Before formal baseline | Full deterministic acceptance, mutation, frozen hashes, and successful explicit paper rehearsal |

Keep compact system journeys in `make check` initially. If measured runtime threatens fast feedback,
first reduce duplicated setup and split extended matrices to nightly. If core journeys still require a
separate job, retain them as a required handoff gate with an explicit Make command and update
`docs/testing.md`; do not quietly remove them from acceptance.

## Failure artifacts and observability

Each deterministic system run should be reconstructable from a small, secret-free artifact bundle:

- scenario identifier and scenario-data version;
- source revision and dirty-state indicator;
- fixed clock timeline and any Hypothesis replay blob;
- configuration, policy, Constitution, prompt, model, tool, data-regime, evidence, and fixture hashes;
- public receipts and canonical durable-event export;
- packet and Decision Record identifiers and hashes;
- scripted model and broker transcripts, with hostile payloads bounded or hashed;
- final provider world state, status, and digest;
- injected fault point and expected recovery class; and
- bounded logs and a machine-readable failure classification.

Retain complete bundles for failures and concise manifests for passes. Store them only under ignored
temporary or CI-artifact roots. Redact local paths, credentials, account identifiers, unpublished
research, and generated runtime ledgers, consistent with the fixture and credential rules
([fixtures](../testing.md#fixtures),
[credential isolation](../product-requirements.md#operational-constraints)).

## Anti-patterns

Avoid these designs:

- **One giant happy-path test:** it is slow, hard to diagnose, and weak on the refusal paths that
  protect authority.
- **A new `tests/e2e/` tier by name alone:** it duplicates the integration contract before a different
  runtime or owner exists.
- **Mocking private methods or asserting call order:** it freezes implementation and skips public
  serialization, persistence, and validation seams.
- **Passing an in-memory packet to execution:** it bypasses publication, deserialization, and
  independent validation.
- **Using production calculation code in the expected-value builder:** it makes the oracle repeat the
  defect.
- **Exact LLM prose or digest snapshots:** they confuse presentation drift with schema, evidence, or
  authority behavior.
- **Golden SQLite database bytes:** they bind tests to irrelevant storage layout and can mishandle
  journal state.
- **Live Alpaca, public sources, or Codex in default CI:** they violate the keyless deterministic gate
  and turn provider variability into product correctness noise.
- **Environment-controlled surprise live tests:** credentials must never make an ordinary `pytest`
  command acquire external authority.
- **Timing-based crashes and sleeps:** use fixed clocks and explicit barriers.
- **Production `TESTING` branches:** inject owned ports or harness wrappers at stable boundaries.
- **Blind fixture regeneration:** recorded external differences are behavior changes requiring review.
- **Treating coverage, E2E, paper fills, or one model sample as complete proof:** each supplies bounded
  evidence only.

## Staged implementation roadmap

### 0. Ratify placement and vocabulary

Promote the accepted definition, placement, live-discovery rule, and gate ownership into
`docs/testing.md`. Open delivery issues through the repository's issue-planning workflow. Do not add
an E2E framework dependency; pytest, Hypothesis, subprocesses, SQLite, and typed test doubles cover the
present need.

### 1. Name the current Stage 1 system slice

Add one compact journey under `tests/integration/system/` that uses `configure_advance` and
`configure_status`, a fixed clock, real private storage, a fresh-process reopen, an interruption, a
projection rebuild, and a replayed receipt. Reuse existing helpers where that does not couple the
oracle to internals. This labels existing architectural evidence without claiming a full operator
journey or duplicating the exhaustive Stage 1 integration suite.

### 2. Add evidence and research journeys with their slice

Introduce recorded evidence artifacts, explicit source and availability times, content hashes, a
scripted model adapter, validator-focused hostile outputs, and a no-action path. Prove future-data
exclusion, provenance, clean-context Skeptic participation, and the absence of packet publication on
refusal.

### 3. Add portfolio and packet publication journeys

Drive the same validated HouseView into Balanced and shadow portfolios. Prove deterministic sizing,
limits, Target Bands, cash/no-trade behavior, one frozen Decision Record, and atomic publication of
only a complete Balanced packet. Add hand-calculated and property oracles before relying on the system
journey.

### 4. Add executor process and broker recovery journeys

Create the separately spawned executor composition and scripted broker state machine. Pass only the
serialized packet and executor-scoped synthetic credentials. Prioritize packet refusal, ambiguous
acceptance, duplicate delivery, partial fills, cancellation, out-of-order events, and reconciliation.
Join recorded Alpaca representations through contract tests.

### 5. Add the full Market Session operator journey

When a real operator command, scheduler, digest, and all public capabilities exist, add the compact
full-session smoke and a two-session restart journey. Exercise actual process handoffs and separate
roots. Add scheduler calendar and missed-session scenarios without wall-clock waits.

### 6. Add evaluation, governance, and lab journeys

Extend across multiple sessions to verify due horizons, frozen ex-ante decisions, benchmark and cost
oracles, future-boundary governance activation, Challenger restrictions, invalid-session visibility,
and Research Lab noninterference.

### 7. Operationalize explicit rehearsals

Add structurally excluded, operator-invoked model and Alpaca paper commands with preflight refusal,
bounded effects, fingerprinted artifacts, and teardown/reconciliation. Run the complete paper
rehearsal before freezing the formal baseline.

## Decision summary

The architecture needs a **system of proofs**, not a single “realistic” E2E test. The optimal first
step is to recognize deterministic system journeys as a narrow subset of integration tests, preserve
external seams with controlled adapters, cross real authority boundaries through serialized artifacts
and subprocesses, and judge correctness with independent state, replay, and absence-of-effect oracles.
Live model and Alpaca runs then answer the narrower question of current external compatibility under
explicit operator authority. This structure provides strong architectural evidence now, scales with
the planned vertical slices, and avoids claiming correctness that the current Stage 1 scaffold or a
paper simulator cannot establish.
