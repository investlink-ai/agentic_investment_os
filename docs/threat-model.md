# Threat model

This document owns material threat enumeration, current control coverage, and residual risk for the
[accepted topology](architecture.md#system-topology). It does not own architecture, investment rules,
control behavior, or test procedure; each mitigation and verification entry links to that fact's
canonical owner.

## Scope and assumptions

- V0 is local, single-operator, US-equity research with Alpaca paper authority. Live capital and
  multi-user operation are outside scope.
- The local operator and operating-system account are trusted roots. Host or operator compromise can
  bypass application controls; this design provides detection and refusal, not host-level tamper
  resistance.
- External text, source and broker data, model output, durable representations, and copied Lab inputs
  are untrusted at every receiving seam.
- Production research, portfolio, packet publication, execution, and evaluation remain
  [scaffolded](../README.md#status). A required target control is not implementation evidence.
- Loss of availability is preferable to unsupported research, corrupted authority, duplicated
  exposure, credential disclosure, or an unreconstructable decision.

## Protected assets

| Asset | Required property |
| --- | --- |
| Investment and governance authority | The operator alone approves policy; deterministic `portfolio` alone sizes positions and constructs packets; credentialed `execution` alone validates packet intent and causes broker effects |
| Broker credentials and account context | Credentials stay inside executor composition and out of models, research, logs, fixtures, and durable artifacts; non-secret account scope is packet-bound and independently revalidated |
| Evidence and model-visible decision inputs | Provenance-bound, cutoff-correct, content-identified, bounded, and reconstructable |
| Financial history, packets, and receipts | Append-only or immutable, integrity-checked, idempotent, and reconstructable from authoritative state |
| Process capabilities and state roots | Least-authority composition, disjoint Lab and production state, explicit private paths, and no ambient effect acquisition |

## Threat register

| Threat | Protected asset and entry seam | Required controls and owner | Verification owner | Residual risk |
| --- | --- | --- | --- | --- |
| Poisoned evidence or embedded prompt instructions | Research integrity at source adapter → Evidence Vault → model-input seams | Treat external text as inert data; bind content, source, mapping, availability, and cutoff provenance; admit only bounded evidence and declared tools. [Defensive patterns](defensive-patterns.md#treat-external-text-as-data) and [investment rules](investment-domain.md#research-workflow) own the controls. | [Recorded evidence contract](../tests/contract/test_recorded_evidence.py), [official evidence contract](../tests/contract/test_recorded_official_evidence.py), and [evidence/research scenarios](testing.md#evidence-memory-and-research) | A well-formed source can still be false, coordinated, or misleading. Schema and provenance validation do not prove truth; role challenge, abstention, operator review, and prospective evaluation retain that risk. |
| Model output, prompt, or tool authority escalation | Research, governance, portfolio, and execution authority at model port → research-validator seams | Treat model output as hostile; accept only closed role schemas and local evidence references; reject weights, orders, lifecycle, governance, credential, memory-write, and undeclared-tool directives. Deterministic modules retain downstream authority under [authority and trust](architecture.md#authority-and-trust). | [Model contract](../tests/contract/test_research_lab_model.py), [Lab resolution journey](../tests/integration/system/test_research_lab_resolution_journey.py), and [research scenarios](testing.md#evidence-memory-and-research) | Validators enforce encoded contracts, not semantic honesty. Production research is not enabled; its composition requires equivalent hostile-output evidence before activation. |
| Credential disclosure or capability bleed | Broker credentials and account scope at configuration → entrypoint → executor-process seams | Secret references resolve only in credentialed composition; the executor receives no model capability, while the operating system and Lab receive no broker credential or execution port. [Architecture](architecture.md#system-topology), [module rules](module-graph.md#rules), and [configuration](config-catalog.md#runtime-configuration) own the separation. | [Module/effect gates](testing.md#static-architecture-gates), [runtime configuration](../tests/integration/test_runtime_configuration.py), and [Lab composition](../tests/integration/test_research_lab_replay.py) | The future executor process is scaffolded, so end-to-end credential isolation is not yet executable acceptance evidence. Host or operator compromise remains outside application containment. |
| Governance, configuration, or packet tampering and replay | Constitution, pinned policy, account scope, and order authority at operator/configuration/packet-store seams | Sign and version governance, activate it only at an exact future session, pin material configuration and hashes, publish packets atomically, and independently validate complete signed, scoped, expiring packets before effect intent. Exact retry replays; changed material conflicts. [Architecture](architecture.md#safe-execution-handoff) and [product requirements](product-requirements.md#portfolio-and-execution) own the target. | [Governance contract](../tests/contract/test_constitution_governance.py), [governance integration](../tests/integration/test_constitution_governance.py), and required [portfolio/execution scenarios](testing.md#portfolio-and-execution) | Governance is implemented, but packet construction and execution are scaffolded. Packet signatures, account binding, and replay refusal have no production behavior gate yet. |
| Duplicate or unresolved broker effects | Paper exposure and receipts at executor → broker request/observation seams | Validate the packet before intent; persist effect-local intent before submission; use stable client order identity; append independent broker facts; reconcile ambiguous timeout before retry. [Effect ordering](architecture.md#safe-execution-handoff) and [defensive patterns](defensive-patterns.md#persist-intent-before-effects) own the controls. | Required [portfolio/execution](testing.md#portfolio-and-execution) and [fault-injection](testing.md#external-contracts-and-fault-injection) scenarios | Execution is scaffolded. Broker observations may remain late, partial, contradictory, or unavailable; unresolved state must block discretionary progress rather than infer success. |
| Ledger rewrite, coordinated suffix truncation, or projection substitution | Evidence, belief, governance, lifecycle, decision, and outcome authority at persistence/reopen seams | Append corrections and validate each ledger's canonical hashes and references. For belief history, require its singleton terminal anchor to equal the complete chain; for all state, require the exact current schema and rebuild projections without granting them authority. [Durable architecture](architecture.md#durable-state) and [defensive patterns](defensive-patterns.md#anchor-the-terminal-length-of-append-only-history) own the controls. | [Belief recording](../tests/integration/test_belief_recording.py), [lifecycle status](../tests/integration/test_lifecycle_status.py), [SQLite schema](../tests/integration/test_sqlite_schema.py), and [durability scenarios](testing.md#lifecycle-and-durability) | The belief anchor detects covered belief-history truncation. Other ledgers may not detect a coordinated rollback to a valid prefix; a privileged local actor can also alter or delete all state. V0 provides no external notarization, replicated ledger, backup, or disaster recovery guarantee. |
| Filesystem traversal, symlink escape, or Lab/production overlap | Credentials, repository contents, and authoritative state at configured-path → filesystem seams | Resolve explicit absolute roots, reject unsafe or symlinked paths, use private ignored storage, keep Lab state disjoint, and never use source directories as runtime state. [Filesystem patterns](defensive-patterns.md#use-private-explicit-filesystem-roots) and [configuration](config-catalog.md#runtime-configuration) own the controls. | [Runtime configuration](../tests/integration/test_runtime_configuration.py), [Evidence Vault](../tests/integration/test_evidence_vault.py), [lifecycle persistence](../tests/integration/test_advance_lifecycle.py), and [Research Lab replay](../tests/integration/test_research_lab_replay.py) | Filesystem validation cannot contain a compromised host account or privileged process. Backup and recovery procedure remains deferred until runnable portfolio or execution creates an operational state-preservation obligation. |
| Oversized, slow, unavailable, or quota-limited external input | Availability and bounded local resources at source/model/parser seams | Enforce schema and size bounds, attention caps, timeouts, durable refusals, resource metadata, and no metered fallback. Never relax authority or reuse stale material to preserve availability. [Fail-closed patterns](defensive-patterns.md#fail-closed-with-a-durable-reason) and [subscription constraints](product-requirements.md#operational-constraints) own the controls. | [Model contract](../tests/contract/test_research_lab_model.py), [evidence capture](../tests/integration/test_evidence_capture_lifecycle.py), and [fault injection](testing.md#external-contracts-and-fault-injection) | Local resource exhaustion and provider outage can stop a session. V0 accepts bounded denial of service rather than unsafe fallback and has no high-availability objective. |

## Maintenance

Update this document when a change adds a protected asset, trust seam, threat path, control-coverage
claim, or material residual risk. Change the owning architecture, requirement, defensive pattern, or
test policy when a mitigation itself changes; this register links that decision and does not approve
its implementation.

A missing required control fails closed and becomes issue work. Do not convert residual risk into an
operational runbook until runnable portfolio or execution behavior creates a real recovery boundary.
