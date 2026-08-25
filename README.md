# Agentic Investment OS

An evidence-bound, agentic-first investment operating system for US equities research and Alpaca
paper trading. LLMs perform structured research under a fixed investment constitution; deterministic
code owns provenance, portfolio construction, risk, execution, and evaluation.

The project is deliberately a modular monolith for v0. It exposes a small lifecycle API, runs the
credentialed executor as a separate process boundary, and keeps experimental stage replay inside an
isolated Research Lab.

## Status

| Surface | Delivery state | Code owner | Verification surface |
| --- | --- | --- | --- |
| Production lifecycle, evidence, and attention | **Implemented:** `Advance` persists `SnapshotUniverse`, `CaptureEvidence`, and `SelectAttention`; `Status` rebuilds from authoritative history | `application`, `domain`, `evidence`, `adapters` | [Lifecycle journey](tests/integration/system/test_lifecycle_journey.py), [attention lifecycle](tests/integration/test_attention_lifecycle.py) |
| Belief memory | **Implemented:** `Record` appends evidence-bound Belief Events and rebuilds bounded as-of Belief Graphs | `application`, `memory`, `adapters` | [Belief journey](tests/integration/system/test_belief_record_journey.py), [Belief Event contract](tests/contract/test_belief_event_contract.py) |
| Constitution governance | **Implemented:** operator-only `Govern` schedules signed amendments; `Advance` pins the active version before work | `application`, `domain`, `adapters`, `entrypoints` | [Governance integration](tests/integration/test_constitution_governance.py), [governance contract](tests/contract/test_constitution_governance.py) |
| Isolated Research Lab | **Implemented:** `Replay` validates a non-production Dossier and runs Thesis, Skeptic, Scenario, and CIO roles | `application`, `research`, `adapters`, `entrypoints` | [Resolution journey](tests/integration/system/test_research_lab_resolution_journey.py), [model contract](tests/contract/test_research_lab_model.py) |
| Production research, portfolio, execution, evaluation, and later lifecycle phases | **Scaffolded:** accepted contracts exist, but production behavior and composition are not enabled | `research`, `portfolio`, `execution`, `evaluation`, `application` | No production behavior gate yet; [stage acceptance](docs/product-requirements.md) remains authoritative |
| Crypto spot and listed options | **Reserved and disabled:** shared typed contracts exist; V0 composition remains US-equity-only | `domain`, `entrypoints` | [Identity unit tests](tests/unit/test_identity.py), [runtime configuration](tests/integration/test_runtime_configuration.py) |

Implemented production retries reproduce durable identities, transitions, bounded refusals, and
pinned evidence, policy, governance, and time provenance without widening model or broker authority.
The attention scan admits at most twenty Candidate Cards and five new Dossier requests, while retained
holdings are refreshed outside that research cap. Authoritative records are append-only; projections
remain disposable and rebuildable.

Research Lab calls use copied or synthetic inputs and a separate durable namespace. A completed call
replays without another effect; an unobserved prior effect, changed retry material, invalid artifact,
prohibited authority, timeout, or quota exhaustion fails closed without a network or metered fallback.
Implemented Lab behavior does not activate production research.

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
