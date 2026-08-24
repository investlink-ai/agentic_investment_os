---
name: manage-agent-notes
description: Manage Agent Notes for agentic-investment-os when a durable feature, simplification, testing, prevention, or process trade-off falls below the ADR threshold. Covers proposal through consolidation; not routine work tracking.
---

# Manage Agent Notes

Agent Notes preserve decision reasoning that active code and documentation should not carry. They are
not work trackers or current-behavior authorities.

## Establish the owner

1. Read `.agents/notes/README.md` completely.
2. Search active notes, ADRs, issues referenced in the change, and owning documentation for the same
   decision. Update an existing owner instead of creating a parallel explanation.
3. Classify the decision:
   - use an ADR for architecture, authority, persistence, durable or wire formats, or another
     hard-to-reverse system boundary;
   - use an Agent Note for durable feature, simplification, bug-prevention, testing, or process
     reasoning below that threshold;
   - use an issue for priority, ownership, work state, and ordinary acceptance criteria; and
   - use no decision document for a routine or obvious local change.

The owner is established when one artifact has an explicit reason to exist and no current authority
would be duplicated.

## Apply the lifecycle

### Propose

Create `.agents/notes/proposed/YYYY-MM-DD-topic.md` before implementation when real alternatives need
review. State the problem independently of the preferred solution, then record the proposal, actual
alternatives, acceptance criteria, and risks. Proposed notes have no implementation authority.

### Implement

Move the same file to `implemented/` in the implementing change. Preserve its original date, change
the status, rewrite future-tense proposals as a present-tense decision, replace plans with consequences,
and retain concise verification evidence and known gaps. Verify every named path, interface, and
mechanism against shipped code.

### Reject

Move a declined proposal to `rejected/`, preserve the proposal and alternatives, and put the durable
rejection reason on the status line. Keep the note only while the rejected idea remains a plausible,
meaningful mistake.

### Consolidate or delete

Before adding or changing a note, check for full or partial supersession. Consolidate every unique
rationale, alternative, consequence, negative guarantee, and reintroduction condition into the current
owner before deleting a fully superseded note. Cross-link partial supersession. Delete low-value
implemented or rejected notes rather than creating an archive; Git history preserves removed material.

## Write for future reasoning

- Write from the repository's present vantage; remove PR choreography and reasoning transcripts.
- Record alternatives that were actually considered and why they lost. Do not invent alternatives to
  fill a section.
- Preserve authority, timing, ordering, failure, durable semantics, security rules, and negative
  guarantees that could change a future decision.
- Link active authorities rather than copying requirements, architecture, configuration, or code
  inventories into the note.
- Keep issues as work trackers and pull requests as review evidence.
- Keep broker credentials, account identifiers, mutable runtime facts, and generated financial
  research out of notes.

Use [prose-standard](../prose-standard/SKILL.md) for a difficult or substantial note edit.

## Validate the note change

1. Confirm the file directory and `Status:` value agree and the filename keeps its first-proposed date.
2. Search for duplicate decisions and repair every inbound relative link affected by a move or deletion.
3. For implemented notes, compare the decision and verification with the actual outgoing change.
4. Run `git diff --check` and `make harness`; run `make check` when the note accompanies code or a
   handoff.
5. Report notes proposed, implemented, rejected, consolidated, or deleted, plus any borderline owner
   decision.

The change is complete when future agents can recover the trade-off from one active note without
mistaking it for current executable authority.
