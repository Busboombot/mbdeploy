---
name: project-initiation
description: Bootstrap a new project from a stakeholder's specification — produces overview, specification, and use cases
---

# Project Initiation Skill

Process a written specification into structured project documents that
all other processes will reference throughout the project lifecycle.

## When to Use

At the start of a new project when the stakeholder has provided a
written specification. There are no existing `overview.md`,
`specification.md`, or `usecases.md` documents yet.

## Inputs

- A path to a written specification file from the stakeholder

## Process

1. **Resolve the project's configured design directory.** Read
   `paths.design` from the `paths:` map in `.clasi/config.yaml`. If
   `.clasi/config.yaml` is absent, or has no `design` entry under
   `paths:`, the default is `docs/design/`. Do not assume
   `.clasi/design/` — that is not the default and is wrong for most
   projects; a write there leaves `overview_exists()` (which checks
   `design_dir/overview.md`) permanently unable to see the result, and
   the `initialize` transition stays blocked regardless.

2. **Dispatch to the sprint-planner agent** via the Agent tool with:
   - The specification file path
   - The resolved design directory path (from step 1)
   - Instruction to write all three documents there

   The sprint-planner agent writes all three documents. Do not write
   them yourself.

3. **Await completion.** The sprint-planner returns when all three
   documents are written.

4. **Report the result** to the stakeholder — confirm the files
   created and any key decisions made.

## Documents the Sprint-Planner Produces

All three documents are written to the project's configured design
directory (resolved in step 1 above — `paths.design` in
`.clasi/config.yaml`, default `docs/design/`):

**`overview.md`** — A one-page summary of the
project. An elevator pitch for quick context. It is additive, NOT a
replacement for the specification.

**`specification.md`** — The full feature
specification, preserving ALL stakeholder detail. Exact messages,
behavior rules, edge cases, test expectations — if the stakeholder
wrote it, it MUST survive. Reorganize for clarity, but do not lose
information. Do not summarize, paraphrase, or omit.

**`usecases.md`** — Numbered use cases (UC-001,
UC-002, etc.) extracted from the specification. Each use case has: ID,
title, actor, preconditions, main flow, postconditions, and error flows.

## Critical Rule

**Do not write documents yourself.** Dispatch to the sprint-planner
agent. Your role is orchestration, not authorship.

## Output

Three documents in the project's configured design directory (default
`docs/design/`; see `paths.design` in `.clasi/config.yaml`):
overview.md, specification.md, usecases.md.
