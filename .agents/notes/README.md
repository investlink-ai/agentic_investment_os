# Agent Notes

Agent Notes preserve durable decision reasoning below the ADR threshold: the problem, genuine
alternatives, trade-offs, negative guarantees, and evidence that future agents need but current code
and documentation should not carry. They are not authoritative for shipped behavior.

## Ownership

| Artifact | Owns |
| --- | --- |
| Active documentation, schemas, configuration, and code | Current required and executable truth |
| ADR | Architecture, authority, persistence, durable or wire format, or another hard-to-reverse boundary decision |
| Agent Note | Feature, simplification, bug-prevention, testing, or development-process reasoning below the ADR threshold |
| GitHub issue | Work state, ownership, priority, and delivery acceptance |
| Pull request | Review and merge evidence |

A proposed Agent Note has no implementation authority. An implemented note explains why shipped
behavior was chosen; it links to the current owner rather than duplicating that owner's facts.

## When a note earns its place

Write a note when a reasonable future maintainer is likely to revisit a choice and understanding it
requires real alternatives, a non-obvious trade-off, a negative guarantee, or a reintroduction
condition. Routine vertical slices, obvious fixes, dependency bumps, local refactors, and decisions
already owned completely by an issue or ADR do not earn another artifact.

If a proposal changes architecture, authority, persistence, a durable or wire format, or another
hard-to-reverse system boundary, use an ADR instead. If an Agent Note grows across that threshold,
move its unique reasoning into the ADR and remove the duplicate note in the same change.

## Layout and lifecycle

Use one English Markdown file named `YYYY-MM-DD-topic.md`, where the date is when the topic was first
proposed. Search is the index.

```text
.agents/notes/
  proposed/       Decisions awaiting implementation or rejection
  implemented/    Reasoning that still guides shipped behavior
  rejected/       Losing proposals that remain plausible mistakes
```

Do not add class subdirectories, translations, sidecars, generated indexes, manifests, or an archive.
Git history preserves deleted notes.

Every note begins with exactly:

```markdown
# Agent Note: <title>

Status: <status>
```

The status agrees with its directory:

- `Status: proposed`
- `Status: implemented`
- `Status: rejected — <durable reason>`

Use these bodies:

- **Proposed:** `Problem`, `Proposal`, `Alternatives considered`, `Acceptance criteria`, `Risks`.
- **Implemented:** `Problem`, `Decision`, `Alternatives considered`, `Consequences`; add concise
  `Verification` or `Related` sections when they preserve useful evidence.
- **Rejected:** preserve the proposal and alternatives; the status line carries the rejection reason.

Record only alternatives actually considered. Use an action-oriented title, present-tense prose for
implemented decisions, and relative links for repository sources.

## Moving and retaining notes

- Move a proposal to `implemented/` with the implementing change. Preserve its date, rewrite future
  plans as shipped decisions, and replace acceptance tasks with consequences or verification.
- Move a declined proposal to `rejected/` and keep it only while its rationale prevents a tempting,
  meaningful mistake.
- Before adding a note, search notes and ADRs for the same decision. Update the owner instead of
  creating a duplicate.
- Cross-link partial supersession. Before deleting a fully superseded note, move every unique
  rationale, alternative, consequence, negative guarantee, and reintroduction condition to its
  current owner.
- Delete implemented notes whose reasoning no longer has future decision value. Do not archive them.

Use the `manage-agent-notes` skill for lifecycle changes and `prose-standard` for substantial prose
edits. Keep credentials, account identifiers, mutable runtime facts, and generated financial research
out of notes.
