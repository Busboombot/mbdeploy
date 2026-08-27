---
name: bootstrap-design
description: Bootstrap the persistent per-subsystem architecture doc set (docs/design/) by reading declared source roots and writing design.md plus one co-located DESIGN.md per subsystem
---

# Bootstrap Design Skill

This skill produces the *first* persistent, per-subsystem architecture
doc set for a project: a system-level `docs/design/design.md` plus one
design doc per logical subsystem, co-located as `<subsystem>/DESIGN.md`
directly in that subsystem's own source directory. There is no README
step — the doc's location under the subsystem's own directory is its
identity, so nothing needs to backlink to it. It is run once per project
(or once per newly declared source root) to establish the doc set that
`architecture-authoring`'s Mode 2 (sprint overlays) then maintains going
forward.

## When to Use

Run when:
- `Project.sources` is configured (a `sources:` list exists in
  `.clasi/config.yaml`) but `docs/design/design.md` does not yet exist,
  or a declared source root has subsystem directories with no
  corresponding design doc.
- The stakeholder has just opted in to the persistent design-doc set
  (`design_docs: enabled`) and asked for the initial doc set to be
  written.
- `clasi design validate` reports "Unmapped source root" or "Missing
  top-level design document" failures and the fix is to author the
  missing docs, not to change config.

This is NOT run automatically on every sprint — see "How This Differs
from consolidate-architecture" below for when to reach for this skill
versus that one.

## Prerequisites

1. **Read `Project.sources`.** If `sources:` is empty or absent, there
   is nothing to bootstrap — **stop and flag this to the stakeholder**
   rather than guessing at source roots. Do not invent a `sources:`
   list; that is a stakeholder config decision (see
   `Project.set_design_docs_opt_in` / `.clasi/config.yaml`'s `sources:`
   key), not something this skill infers from directory scanning.
2. Confirm `design_docs_opt_in` is `True` (or the stakeholder has
   explicitly asked for a one-time bootstrap regardless). If opt-in is
   unset or `False`, confirm with the stakeholder before writing
   anything — writing `docs/design/` content into a project that hasn't
   opted in surprises the next `clasi design validate` run and the
   sprint lifecycle tools that gate on the flag.

## Process

### 1. Identify Roots and Subsystems

**Each declared root itself gets a required root-level `DESIGN.md`.** A
source root owes its own overview doc at `<root>/DESIGN.md` — a map of
that whole tree (what the root is, its subsystem list with one line each,
and any conventions every subsystem doc under it may assume). The
validator requires this doc for every declared root; omitting it fails
`clasi design validate` (and therefore `close_sprint`). Write one per
root, in addition to the per-subsystem docs below.

Then, for each root in `Project.sources`, enumerate its top-level
directories as candidate subsystems — the same "one level down, no
deeper" rule `clasi.design.store._subsystem_dirs` applies
mechanically (hidden directories and `__pycache__` excluded). A nested
directory belongs to the subsystem that contains it; it does not get
its own doc. A `DESIGN.md` nested deeper than one level below a root is
flagged as an orphan.

Not every top-level directory is automatically a subsystem worth its
own document, though — use judgment here the same way
`architecture-authoring` Step 2 ("Identify Responsibilities") asks for
judgment when grouping responsibilities into modules:

- A directory that is a genuine cohesive unit (one sentence of
  purpose, no "and") gets its own design doc.
- A directory that is pure infrastructure with no interesting design
  content (e.g. a `__pycache__`-like generated-artifacts directory, or
  a directory that is just a flat bag of unrelated one-off scripts)
  may be worth a thinner doc or, in genuinely trivial cases, a
  one-line "no architecturally significant content" doc rather than a
  padded one — the doc set's job is to be read, not to hit a page
  count.
- When in doubt, err toward giving the directory its own doc: an
  under-scoped subsystem doc is easy to thicken later; a subsystem
  silently merged into a neighbor's doc is harder to notice is
  missing.

Do **not** hand-construct a subsystem's design-doc path — that
derivation is `clasi.design.paths.design_doc_path_for`'s job (see Step
3): the doc always lives at `<subsystem_path>/DESIGN.md`, no
slugification or source-root disambiguation involved.

### 2. Read the Code

For each identified subsystem, read its source to understand what it
actually does: responsibilities, key data structures, control flow,
its public interface, and what it depends on from other subsystems.
This is the same "verify against actual code" discipline
`consolidate-architecture` already applies — the doc must describe
reality, not aspiration.

### 3. Start Each Subsystem Doc from the Packaged Template

Do not write a subsystem design doc from a blank page. Call
`clasi.design.store.subsystem_template()` (or, from the CLI/MCP surface,
read the equivalent packaged resource) to get the full template text —
HTML-comment section guidance included — and fill in each section from
what Step 2 turned up. Delete each HTML comment once its section is
written, per the template's own instructions.

The template carries **no frontmatter block** — a co-located
`DESIGN.md` requires none, since the doc's location under the
subsystem's own source directory already is its identity. Write the six
body sections only; there are no placeholder fields to replace.

### 4. Derive Paths and Write via the Design Store — Never by Hand

This is the load-bearing rule of this skill: **use
`clasi.design.paths` to locate files and `clasi.design.store` to write
them.** Do not hand-construct a `<subsystem>/DESIGN.md` path string or
a `docs/design/<name>.md` path string. That logic already exists
(tickets 002/003) specifically so this skill doesn't reimplement it in
prose, drift from it, or get a naming edge case wrong in a way the
validator then has to catch.

Concretely:
- Call `clasi.design.paths.design_doc_path_for(subsystem_path)` to get
  the canonical path for a subsystem doc — always
  `<subsystem_path>/DESIGN.md` — never construct it by joining path
  segments yourself.
- Call `clasi.design.store.write_design_doc(project, subsystem_path,
  content)` to write the subsystem doc. No frontmatter is written by
  default; do not pass `extra_frontmatter` unless there is a specific
  reason to attach optional metadata.
- Call `clasi.design.store.write_design_doc(project, root, content)` once
  per declared root to write its required root-level `DESIGN.md` overview
  — the same function and same `<path>/DESIGN.md` derivation, just with
  the root path itself rather than a subsystem path. Content is the
  root-tree map described in Step 1.
- Call `clasi.design.store.write_system_doc(project, content)` once,
  for `docs/design/design.md`, covering system-wide context: what the
  project is, the subsystem map (one line per subsystem, linking to
  its doc), and any global conventions every subsystem doc is allowed
  to assume without repeating.
- If a subsystem doc already exists (a partial prior bootstrap, or a
  hand-edited file), read it first
  (`clasi.design.store.read_design_doc`) and decide whether to preserve
  its body — the write functions are full-overwrite, per `store.py`'s
  documented overwrite semantics. Do not silently clobber hand-edited
  content.

### 5. Validate, Fix, Re-validate

After writing the doc set, run `clasi design validate` (or the
`validate_design` MCP tool for the equivalent programmatic check).
Read the reported failures — they are specific, actionable, one
message per defect (missing design doc, orphaned doc, unmapped source
root, empty doc, etc.).

On any failure: fix the specific thing the message names (write the
missing file, remove an orphaned doc that no longer maps to a
subsystem) and re-run validation. Repeat until `clasi design validate`
passes cleanly. Do not consider the bootstrap complete while validation
fails — this is SUC-001's acceptance criterion, not an optional nicety.

## How This Differs from `consolidate-architecture`

Both skills read source code and sprint history to produce
architecture documentation, but they produce different, non-overlapping
things:

| | `bootstrap-design` | `consolidate-architecture` |
|---|---|---|
| **Output** | Persistent doc *set*: `docs/design/design.md` + one co-located `DESIGN.md` per subsystem, each independently updatable | One single `docs/design/architecture.md` file |
| **When it runs** | Once, to establish the doc set (or to fill in a newly added subsystem/source root) | On demand, any time a fresh consolidated snapshot is wanted |
| **What happens after** | Maintained incrementally per-subsystem via sprint `design/` overlays (`architecture-authoring` Mode 2) and applied back at sprint close | Overwritten wholesale the next time it's run; no incremental update path |
| **Filenames/paths** | Derived mechanically via `clasi.design.paths`/`clasi.design.store` — never hand-written | No fixed schema; a single free-form document |
| **Validation** | `clasi design validate` checks structure and doc-set completeness | No validator; nothing to check structural correctness against |

**Rule of thumb for choosing:** if the project has (or is opting into)
`sources:` config and wants each subsystem's architecture to live in
its own maintained file, use `bootstrap-design`. If the project has
not opted into the persistent doc set — or you just want a quick,
disposable, single-file snapshot of "what does this system look like
right now" without setting up the doc-set machinery — use
`consolidate-architecture`. The two are not meant to be run against the
same project as parallel, competing outputs: once a project bootstraps
its persistent doc set, `bootstrap-design` (plus the sprint overlay
lifecycle) is the mechanism going forward, and `consolidate-architecture`
remains available only for sprints still on the pre-021, single-big-
document model.

## Output

- `docs/design/design.md` (system-level document)
- One `<root>/DESIGN.md` root-overview doc per declared source root
- One `<subsystem>/DESIGN.md` per identified subsystem, co-located in
  the subsystem's own source directory, at the path produced by
  `clasi.design.paths.design_doc_path_for`
- A passing `clasi design validate` run
