# Agent Note: Budget project skill routing metadata

Status: implemented

## Problem

Codex loads every enabled skill's frontmatter description into a shared context budget. The project
skills had accumulated workflow detail in that routing field, so the 12 project descriptions alone
used 5,434 characters and could trigger description shortening when combined with built-in skills and
plugins. Shortening does not remove skill bodies or explicit invocation, but it weakens the metadata
used for automatic selection and can blur adjacent project workflows.

## Decision

Keep each project skill's `description` as concise, unquoted, single-line printable-ASCII YAML plain
text: its positive intent, its nearest exclusion or delegation boundary, and any distinction needed
from an adjacent skill. Keep the full procedure, approval rules, safety contract, and terminal
outcomes in the body.
Enforce a 320-character individual limit and a 3,200-character aggregate project limit through the
deterministic [`make harness`](../../../Makefile) check documented in
[`docs/testing.md`](../../../docs/testing.md#project-skill-catalog).

Treat global plugin selection as operator-owned state. The repository documents a minimal supported
baseline in [`docs/development.md`](../../../docs/development.md#codex-plugin-baseline), but neither
hard-codes user-specific skill paths nor changes or removes global plugins.

## Alternatives considered

- Delete or consolidate project skills. This would save more metadata, but it would collapse distinct
  ownership boundaries between planning, issue creation, delivery, worktree setup, implementation,
  general review, safety review, pull-request publication, prose, notes, simplification, and
  reflection.
- Store complete workflows in frontmatter. This makes automatic discovery self-contained but spends
  the scarce routing budget on instructions that Codex reads again after selecting the skill.
- Enforce token counts for one tokenizer. That would track one model more closely, but the repository
  would acquire a model-specific and change-prone dependency. Character counts provide a stable proxy
  without claiming to predict every client or model budget.
- Configure global plugins from the repository. This could produce a narrower local catalog, but it
  would mutate user-owned state and require non-portable absolute paths.

## Consequences

Automatic selection retains the cue that chooses a skill and the boundary that prevents common
misrouting, while explicit `$skill-name` invocation and the complete body remain available. The
catalog has limited headroom, so adding a skill may require tightening existing routing copy even when
the new description satisfies its individual limit. The guard controls only repository-owned
metadata; optional plugins can still exhaust the client budget, and a clean harness cannot guarantee
that an arbitrary global plugin set will avoid the warning.

## Verification

The implemented catalog contains 12 descriptions totaling 2,881 characters, with a 287-character
maximum. Integration fixtures cover exact individual and aggregate limits, missing and malformed
descriptions, duplicate fields, individual overage diagnostics, and aggregate contributor reporting.
Representative model-backed agent workflow scenarios remain the behavioral evidence for explicit
selection and automatic routing; the character guard does not replace them.
