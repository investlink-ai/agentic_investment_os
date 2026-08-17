# Agent Note: Use lightweight Agent Notes for decision reasoning

Status: implemented

## Problem

Active documentation and code preserve current truth, ADRs preserve hard-to-reverse architecture,
and issues preserve work state. Decisions below the ADR threshold can still involve alternatives,
negative guarantees, and trade-offs that future agents need when designing, reviewing, or simplifying
the system. Leaving that reasoning only in a conversation or pull request makes it difficult to
recover and encourages re-litigation.

## Decision

Use English-only Agent Notes under `.agents/notes/proposed`, `implemented`, and `rejected`. Write a note
only when durable reasoning below the ADR threshold is likely to guide a future choice. Proposed notes
are not authority; implemented notes explain shipped decisions and link to active owners. Relevant
notes are explicit inputs to implementation, review, prose, and simplification workflows.

Search is the index. The repository uses no note archive, class hierarchy, translation pair, hash
sidecar, or mandatory note for every non-trivial change. Fully superseded or low-value notes are
consolidated and deleted, with Git history preserving removed material.

## Alternatives considered

**Use only issues and ADRs.** This keeps fewer artifacts but leaves feature, simplification, testing,
and process trade-offs either below the ADR threshold or mixed into work-tracking discussion. It does
not reliably give future agents a focused reasoning source.

**Copy the full upstream Agent Notes system.** Mandatory notes, class directories, translations,
sidecars, format manifests, and a frozen archive support a much larger corpus. They add maintenance
without improving reasoning in this small English-only repository.

**Require a note for every non-trivial change.** This guarantees coverage but turns routine work into
documentation ceremony and makes low-value notes compete with decisions that genuinely matter.

## Consequences

Agents gain durable problem framing, alternatives, and trade-offs when a choice merits them. Authors
must decide whether an issue, ADR, Agent Note, or active document owns each fact, and relevant task
skills must read the notes for the system to work. The lightweight threshold leaves some routine
reasoning only in Git and pull-request history by design.

## Verification

Root instructions route relevant work through Agent Notes. The implementation, safety-review, prose,
simplification, and note-management skills search or maintain the corpus. `make harness` verifies the
convention and rejects an archive directory.
