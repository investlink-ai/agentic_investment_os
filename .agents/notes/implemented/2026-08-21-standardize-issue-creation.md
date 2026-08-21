# Agent Note: Standardize issue creation around one atomic contract

Status: implemented

## Problem

The repository templates provide stable issue fields, but a template cannot trace active authority,
detect overlapping work, select existing metadata, or recover safely after part of an issue set is
published. Stage planning supplies decomposition without defining a reusable atomic issue contract,
while general GitHub tooling performs writes without carrying repository quality and safety rules.

## Decision

[The development workflow](../../../docs/development.md#issue-authoring) uses the project
[`create-issue` skill](../../skills/create-issue/SKILL.md) to draft, validate, and publish standalone
issues or caller-defined issue sets. One issue remains the atomic authoring and publication unit; a
calling session or [`plan-stage-issues`](../../skills/plan-stage-issues/SKILL.md) retains scope,
decomposition, and graph ownership. Standalone issues and children receive implementation contracts,
while coordination parents receive stage-outcome contracts.

The repository issue templates remain the format authority. The skill supplies the conditional
reasoning around them: active-authority tracing, implementation and duplicate inspection, issue-type
selection, relationship validation, existing-metadata selection, complete preview and approval, and
read-back receipts that make partial publication resumable. Creating an issue grants no authority to
start a worktree, implement it, or publish a pull request.

## Alternatives considered

- A separate generic batch-creation skill was rejected because singular and multi-issue publication
  share the same per-issue quality contract. Capability-stage planning remains separate because it
  has an independent trigger and owns frontier selection, decomposition, and stage closure.
- General GitHub tooling without a project skill was rejected because it owns API operations rather
  than repository-specific authority, safety, template, and acceptance standards.
- A deterministic issue-generation script was deferred because duplicate assessment, requirement
  tracing, and issue-type selection require semantic judgment. A script becomes useful only if
  repeated mechanical transformations emerge.
- Embedding the full workflow in `AGENTS.md` was rejected because issue creation is conditional and
  would add context load to unrelated repository work.

## Consequences

Agents have one verb-led atomic issue-creation capability, while stage-planning requests retain their
specialized entry point. Role-specific validation prevents a coordination parent from masquerading as
one implementation issue. Publishing an issue set incurs a deliberate preview-and-approval round trip
and stops on uncertain writes, trading speed for reviewability and duplicate resistance. Repository
templates and active documents remain the single sources of truth; the skill must follow their future
changes rather than cache their contents.

The skill creates no label taxonomy, milestones, project structure, worktrees, implementation, or
pull requests. Those remain separate decisions and workflows.

## Verification

The skill validator checks the package and interface metadata. `make harness` verifies project
discovery, and `make check` remains the complete repository handoff gate.
