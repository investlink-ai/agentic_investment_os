# Agentic Investment OS

An evidence-bound, agentic-first investment operating system for US equities research and Alpaca
paper trading. LLMs perform structured research under a fixed investment constitution; deterministic
code owns provenance, portfolio construction, risk, execution, and evaluation.

The project is deliberately a modular monolith for v0. It exposes a small lifecycle API, runs the
credentialed executor as a separate process boundary, and keeps experimental stage replay inside an
isolated Research Lab.

## Status

Stage 2 implements `Advance` through durable `SnapshotUniverse`, `CaptureEvidence`, and
`SelectAttention` checkpoints, with rebuildable operator `Status` over validated append-only SQLite
lifecycle history. The zero-token attention scan publishes at most twenty reconstructable Candidate
Cards and five new Dossier requests, reserves a pinned weekly exploration share, and refreshes every
retained holding outside that research cap. Exact evidence and policy fingerprints bind the artifact;
retry and reopen reproduce its identifiers, counts, transitions, and refusal reasons without model,
network, credential, portfolio, packet, or broker authority. Canonical asset-neutral identities,
common hashed envelopes, explicit position valuation provenance, and exact cycle-to-snapshot Status
identity keep future variants additive without activating non-equity behavior in V0. The lifecycle
ledger accepts one current database-wide schema, while the status projection is disposable and never
substitutes for invalid authoritative records. Absolute instants use canonical UTC text while Market
Sessions retain NYSE-date meaning. A pure domain kernel owns reconstruction and transition decisions
while SQLite validates representations and atomically appends the selected record. Evidence Capture
publishes immutable, cutoff-bound artifacts and append-only observations to the Evidence Vault. The
public `Record` capability appends schema-versioned, bitemporal Belief Events after resolving their
immutable Vault references, returns bounded idempotent receipts, and deterministically rebuilds an
explicitly bounded as-of Belief Graph with provenance and omission counts. Stage 3 adds operator-only
`Govern` for immutable, signed Constitution amendments scheduled at an exact future Market Session.
Advance resolves and pins one Constitution version and content hash before universe, evidence,
attention, or model work; Status rebuilds active, pending, superseded, refused, and conflicting
governance state. The isolated Research Lab exposes `Replay` for stateless model calls over copied or
synthetic inputs. It can validate a cited non-production Dossier, then run Thesis Builder,
clean-context Independent Skeptic, Scenario Forecaster, and CIO resolution with a separate durable
identity and pinned context for every role. Completed calls replay without another effect; an
interrupted unobserved effect, changed retry material, unsupported claim, invalid forecast, prohibited
sizing or execution directive, timeout, or quota exhaustion fails closed without a network or metered
fallback. Production research, portfolio, execution, evaluation, and remaining lifecycle phases
remain scaffolds.
Active repository documentation owns implementation authority, and the runtime has no dependency on
the design's Second Brain origin.

## Setup

Requirements: Python 3.12, `uv`, `git`, and `make`.

```bash
make bootstrap
make check
```

`make bootstrap` creates `.venv`, installs the locked development environment, and enables the local
Git hooks. Dependencies live only in `pyproject.toml` and `uv.lock`.

## Repository map

- `src/agentic_investment_os/`: production package, organized by domain capability
- `tests/unit/`: fast domain and policy behavior
- `tests/integration/`: persistence and composition behavior with local or recorded adapters
- `tests/contract/`: external schema, LLM, SEC, and Alpaca boundary fixtures
- `CONTEXT.md`: canonical investment-system terminology
- `docs/product-requirements.md`: V0 outcomes, scope, acceptance, and implementation order
- `docs/architecture.md`: system topology, module seams, lifecycle, authority, and durable state
- `docs/investment-domain.md`: evidence, research, memory, portfolio, execution, and evaluation rules
- `docs/config-catalog.md`: implemented configuration sources and ownership
- `docs/defensive-patterns.md`: reusable prevention rules for high-risk bug classes
- `docs/testing.md`: deterministic, contract, integration, and live-test policy
- `docs/module-graph.md`: allowed Python import directions
- `docs/adr/`: durable architectural decisions
- `docs/research/`: non-authoritative investigations awaiting explicit promotion
- `.github/`: keyless CI, dependency updates, and concise contribution templates
- `.agents/skills/`: canonical repository-specific coding-agent skills
- `.agents/notes/`: durable feature, simplification, testing, and process decision reasoning

See `docs/development.md` for the development workflow.
