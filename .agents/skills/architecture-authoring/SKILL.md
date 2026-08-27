---
name: architecture-authoring
description: Design and write architecture documents — initial architecture or sprint updates
---

# Architecture Authoring Skill

This skill guides writing architecture documents, whether an initial
architecture from scratch or a sprint update.

## Two Modes

### Mode 1: Initial Architecture

Design the system architecture from scratch when no architecture document
exists yet.

Given `overview.md` and `usecases.md` in the project's configured
design directory (`paths.design` in `.clasi/config.yaml`, default
`docs/design/`), produce the first architecture document following
steps 1-7 below.

This mode is superseded, for a project that has opted into the persistent
per-subsystem design-doc set (`Project.design_docs_opt_in` is `True`), by
the `bootstrap-design` skill, which produces `docs/design/design.md` plus
a root-level `DESIGN.md` per declared source root plus one doc per
subsystem, instead of a single architecture document. Use Mode 1 only for
a project that has not opted in.

### Mode 2: Sprint Architecture Update

Write the sprint's architecture output, sized to the change — or write
"N/A — trivial" when the change has no architectural impact. This output
is authored after the effort decision is made and use cases are defined,
and **before tickets exist** — tickets are derived from it, not the other
way around. The guiding question throughout is: "Is this description
clear enough that tickets can be derived from it without ambiguity?"

**Where the output goes** depends on `Project.design_docs_opt_in`:

- **Not opted in** (`design_docs_opt_in` is `None` or `False`): write the
  Architecture section of the sprint's `sprint.md`, exactly as before.
  Nothing below in this subsection changes that path.
- **Opted in** (`design_docs_opt_in` is `True`) and the sizing decision is
  **compact or substantial**: the output goes into the sprint's `design/`
  overlay instead of a `sprint.md` Architecture section (Mode 2a below).
- **Opted in** and the sizing decision is **trivial**: no overlay is
  created and no `sprint.md` Architecture section is written either — same
  "N/A — trivial" skip as the not-opted-in path (Open Question 4's
  resolution: trivial-sprint behavior is unchanged by opt-in). Do not call
  `seed_sprint_design_overlay` for a trivial sprint.

The tiering decision itself — and everything in Steps 1-7 below about
*how* to reason about the content — is identical regardless of where the
output lands. Opt-in changes the output's destination, not the sizing
logic or the seven-step process that produces it.

Make an explicit sizing decision first, using three tiers, not two:

- **Trivial / small** — a bug fix, config tweak, or change confined to
  one module with no new component or data-model impact: minimal or
  omitted Architecture section (may read "N/A — trivial").
- **Compact** — one new or changed module/component, with *no* new
  cross-module dependency, *no* dependency-direction change, and *no*
  data-model change: full section structure (What Changed, Why, Impact,
  Migration Concerns) but no diagrams (see Step 4) and prose sized to
  describing one module — typically 300-500 words as a consequence of
  scope, not a target. This tier exists because "adds one module" is not
  the same problem as "introduces a subsystem," and treating it as such
  is what produces bloated plans for small projects.
- **Substantial / structural** — 3+ modules touched, a new/changed
  cross-module dependency, a dependency-direction change, or a data-model
  change: the full write-up below, diagrams included.

Judge the tier by concrete signals (module count, dependency changes,
data-model changes), not by guessing a word count first and writing to
it — a heuristic that misjudges a genuinely complex sprint as compact is
worse than one that occasionally treats a simple sprint as substantial.
When borderline, prefer the heavier tier and say why in the sizing
sentence.

At authoring time the section (or, under opt-in, the overlay copy) is a
structural plan. Not-opted-in behavior: after the sprint closes it
accumulates as a historical record (an ADR at sprint granularity,
embedded in that sprint's `sprint.md`). It is not merged back into the
canonical architecture docs — it stands on its own. See the
`consolidate-architecture` skill for how these per-sprint sections are
later merged into a consolidated architecture document, if needed.
Opted-in behavior is the opposite: the overlay is applied back onto the
canonical `docs/design/` doc set at sprint close (see Mode 2a below and
the `close-sprint` skill) — the whole point of opting in is that the
content *does* merge back, instead of standing alone.

Given the sprint plan and current architecture, write the content with:
Planned Changes, Rationale, Impact on Existing Components, Migration
Concerns. Under opt-in this becomes the body of each edited overlay
document rather than a `sprint.md` section — see Mode 2a.

### Mode 2a: Authoring the sprint's `design/` overlay (opt-in)

When `design_docs_opt_in` is `True` and the sizing decision is compact or
substantial, the sprint's architecture output is written into the
sprint's `clasi/sprints/NNN-slug/design/` overlay directory instead of a
`sprint.md` Architecture section. This is authored during Phase 2 detail
planning (see the `plan-sprint` skill for exactly where in that phase),
after the tiering decision above and after use cases are defined, same
as Mode 2's timing rule.

1. **Identify affected canonical docs.** Decide which of the persistent
   `docs/design/` docs this sprint's changes touch — the system-level
   `design.md`, one or more subsystem `DESIGN.md` files (co-located in
   each subsystem's own source directory), or both. This is a judgment
   call over the sprint's planned changes, the same "one sentence, no
   'and'" cohesion reasoning Step 2 below already asks for, applied to
   *which existing docs* rather than *which new modules*. For example, a
   sprint might touch `design.md` (system-level context changed) and
   `src/clasi/tools/DESIGN.md` (one subsystem's contract changed) —
   name both.

   **Relocation is not a content change.** This judgment call is only
   for sprints making *content-only* changes to a doc at its existing,
   stable location. A sprint that *moves* a doc — renaming it, or
   changing which directory it lives in (this sprint's own move from
   flat `docs/design/<slug>.md` files to co-located `<subsystem>/
   DESIGN.md` is the worked example, see this sprint's Design
   Rationale) — should not run that move through the overlay lifecycle
   at all. A relocation is a direct, ticket-scoped file operation
   (`git mv` plus updating whatever code/prose points at the old path),
   done once, in a ticket, not something `seed_sprint_design_overlay`
   /`generate_diffs`/`apply` are built to represent: the overlay
   lifecycle diffs *content* against a pristine baseline at a fixed
   path, not a path change.

2. **Seed the overlay — every affected doc, one call.** Call
   `seed_sprint_design_overlay(sprint_id, doc_names)` **exactly once**,
   passing *every* canonical doc identified in step 1 in a single
   `doc_names` list — never one call per doc, and never only the first
   doc found. `doc_names` accepts two forms per entry: a bare filename
   with no path separator (e.g. `"design.md"`), resolved relative to
   `docs/design/` — the system doc; or a path containing a separator
   (e.g. `"src/clasi/tools/DESIGN.md"`), resolved relative to the repo
   root with no `../../` escape — a subsystem's co-located canonical
   doc. A bare `"DESIGN.md"` with no separator does **not** reach a
   subsystem's doc (it resolves to `docs/design/DESIGN.md` instead) —
   always give the co-located path when the sprint touches a subsystem
   doc, e.g. `["design.md", "src/clasi/tools/DESIGN.md",
   "src/clasi/design/DESIGN.md"]` to seed the system doc plus two
   subsystem docs in one call. Since every co-located subsystem doc
   shares the bare basename `DESIGN.md`, the tool derives a unique
   overlay slug per doc from its path relative to its enclosing source
   root (see `_derive_overlay_slug` in `clasi.tools.artifact_tools`) so
   multiple `DESIGN.md` files seeded in the same call land under
   distinct filenames in the flat `design/` overlay directory rather
   than colliding; the seed step records each seeded file's full
   canonical source path in the overlay directory's `_sources.json`
   manifest, keyed by that slug, so `apply` can resolve it back to the
   right subsystem later. This copies each named canonical doc verbatim
   into `clasi/sprints/NNN-slug/design/` and commits the pristine copies
   immediately, before any edits — do not skip this step or edit a file
   that hasn't been seeded first; the diff-generation step below depends
   on a committed pristine baseline existing. This call is a no-op if
   `doc_names` is empty or opt-in is off, so skipping it entirely is
   exactly how a trivial sprint (see above) avoids creating an overlay.

3. **Edit the seeded copies in place.** Open each file under the sprint's
   `design/` directory and write a complete, updated copy of that
   document reflecting the sprint's planned changes — not a diff, not a
   patch, a full document, the same way the pristine copy itself is a
   full document. Reuse the seven-step process below (Understand the
   Problem through Flag Open Questions) to decide what the updated
   content should say; only the destination differs from Mode 2.

4. **Generate diffs.** Once edits are complete, run the diff-generation
   step of `clasi.design.overlay` (`generate_diffs`) to produce a
   `<name>.diff.md` sibling for each edited file. These diff files are
   what `architecture-review` reads (see that skill) — they are agent-
   reviewer input, not something the stakeholder is expected to read
   directly (the stakeholder reviews via `git diff` in VS Code instead,
   per the sprint 021 issue's framing).

5. **Validate before handoff.** Run `clasi design validate` (or the
   `validate_design` MCP tool) with `overlay_dir` pointed at the sprint's
   `design/` directory before handing off to `architecture-review`. Fix
   any reported failure (unresolved frontmatter reference, stale or
   missing `.diff.md`) and re-validate — do not hand off a failing
   overlay.

**What this changes and does not change**: the sizing tiers, the
seven-step authoring process, and the section content (Planned Changes,
Rationale, Impact, Migration Concerns) are identical to Mode 2 — only the
file(s) the content is written into differ (full document per affected
canonical doc, under `design/`, instead of one `sprint.md` section).

### Revising in place

When an exception loop triggers an architecture revision under the
not-opted-in path, revise the Architecture section of `sprint.md` **in
place** — edit the section
directly rather than creating a separate revision file. Add a brief
`## Revision` note (or update the section's Design Rationale) describing
what changed and why, so the revision is visible without relying on file
history.

This supersedes the older convention (used by sprints planned before
sprint 018's single-doc rewrite) of writing separate
`architecture-update-r1.md`, `-r2.md`, etc. files that preserved the
original `architecture-update.md` untouched. Sprints planned under the
old three-document model may still have those files on disk as a
historical record — that is expected for sprints 001-017 and is not a
defect. New sprints revise the `sprint.md` Architecture section in place.

Under opt-in (Mode 2a), the equivalent "in place" revision target is the
already-edited overlay copy under `clasi/sprints/NNN-slug/design/`, not
`sprint.md` — edit the overlay file directly and re-run diff generation
(`generate_diffs`) so its `.diff.md` sibling reflects the revision;
`architecture-review` re-reads the regenerated diff. Do not create a
second overlay file for the revision.

The team-lead and sprint-planner both reference this convention. The full
rule lives here; the sprint-planner agent carries only a brief
cross-reference.

## Steps

### 1. Understand the Problem
Read the overview, use cases, and (if updating) current architecture and
sprint plan.

### 2. Identify Responsibilities
List distinct responsibilities the system handles. Group related ones.
Separate those that change independently.

### 3. Define Subsystems and Modules
Map responsibility groups to modules. For each:
- **Purpose**: One sentence, no "and"
- **Boundary**: What is inside and outside
- **Use cases served**

### 4. Produce Diagrams
For **Mode 1 (Initial Architecture)** or a **substantial/structural**
sprint update, include:
1. **Component/Module Diagram** — subsystems as boxes, labeled edges.
   Required whenever 3+ modules are touched or a new cross-module
   dependency is introduced. If a substantial-tier sprint touches many
   existing modules for independent changes with no new composition
   between them, a diagram may be omitted — but state in one sentence why
   it wouldn't clarify anything (sprint 020 is a worked example: 9
   largely independent bugfix issues, no new subsystem, diagram omitted
   with a stated reason). Default to including it; the omission requires
   an articulated reason, not silence.
2. **Entity-Relationship Diagram** — entities, attributes, cardinality.
   Only if the data model changes.
3. **Dependency Graph** — module dependencies with labeled edges. Only if
   module dependencies change.

Guidelines: 5-12 nodes, label every edge, one concern per diagram.

For a **compact** sprint update (one new/changed module, no new
cross-module dependency, no data-model change), omit all diagrams. The
one-sentence purpose and boundary from Step 3 already say everything a
diagram would show for a single module.

### 5. Complete the Document
Sections: Architecture Overview, Technology Stack, Module Design, Data
Model, Dependency Graph, Security Considerations, Design Rationale, Open
Questions, Sprint Changes.

Stay at module/subsystem level. No function signatures or column schemas.

### 6. Document Design Rationale
For significant decisions: Decision, Context, Alternatives, Why this
choice, Consequences.

### 7. Flag Open Questions
List anything ambiguous or requiring stakeholder input.

## Quality Checks

- Every module addresses at least one use case
- Every use case addressed by at least one module
- Each module passes cohesion test (one sentence, no "and")
- Dependency graph has no cycles
- Fan-out no greater than 4-5 without justification
- Mermaid diagrams included for Mode 1 and substantial-tier sprint
  updates, unless omitted with a stated one-sentence reason; omitted by
  rule (no justification needed) for compact-tier sprint updates
- Document stays at module level
- For a compact-tier sprint update: no diagrams present, and length is
  proportionate to one module (roughly 300-500 words is typical, not a
  hard limit) — if the section runs much longer than that, check whether
  the sizing decision undercounted scope (a 3rd module, a new dependency,
  a data-model change) rather than trimming prose to fit
