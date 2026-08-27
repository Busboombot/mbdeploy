---
name: sprint-roadmap
description: Group assessed TODOs into a sprint roadmap — a sequence of lightweight sprint plans
---

# Sprint Roadmap Skill

Group assessed TODOs into a sprint roadmap — a sequence of lightweight
sprint plans that organize the work into manageable, ordered chunks.

## When to Use

After TODOs have been created and the stakeholder wants to organize
them into sprints.

## Inputs

- `overview.md` in the project's configured design directory
  (`paths.design` in `.clasi/config.yaml`, default `docs/design/`) —
  must exist
- Current architecture (if available)
- Issue files in `clasi/issues/`

## Process

1. **Read issues**: Scan `clasi/issues/` for pending issue files.
   Read each to understand the work requested.

2. **Group into sprints** based on:
   - **Related functionality** — TODOs that touch the same feature
     belong together
   - **Dependency ordering** — TODOs that depend on others must come
     in later sprints
   - **Incremental value delivery** — Each sprint should deliver usable
     progress
   - **Difficulty balancing** — Mix complex and straightforward work

3. **Create sprint directories**: For each sprint, call `create_sprint(title)`.
   The tool creates the sprint directory and writes a `sprint.md` template
   with `status: roadmap`. No other files are created at this stage.
   Edit the generated `sprint.md` to fill in:
   - Sprint goals (what the sprint accomplishes)
   - TODO references (which TODOs are addressed)
   - Rationale for grouping
   - Dependency notes

4. **Link issues to sprint**: For each issue claimed by this sprint, call
   `link_sprint_issues(sprint_id, [filenames])`. This writes the `issues:`
   list in the sprint's frontmatter and updates each issue file's `sprint:`
   field. Do not write issue or sprint frontmatter manually.

## Output

Sprint directories with lightweight `sprint.md` files, ready for
detail planning via the `plan-sprint` skill.
