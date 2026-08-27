---
name: plan-sprint
description: Creates sprint plans using a two-phase model -- roadmap (batch, lightweight) and detail (full artifacts, pre-execution)
---

# Plan Sprint Skill

This skill creates sprint plans using a two-phase model:

- **Phase 1 -- Roadmap**: Batch planning. Multiple sprints can be planned
  in one session. Produces a lightweight `sprint.md` only (goals, scope,
  TODO references). No branches created.

- **Phase 2 -- Detail**: One sprint at a time. Produces full planning
  artifacts: a single `sprint.md` with right-sized Architecture and Use
  Cases sections, plus tickets. Runs architecture review (or records it
  `skipped` for a trivial/small sprint). No branches created.

Branches are created later via `acquire_execution_lock`, not during
planning. All planning happens on main.

## Agent Used

**sprint-planner** (orchestrates all phases inline — architecture,
review, and ticket creation)

## Inputs

- Stakeholder conversation describing the work to be done
- `.clasi/brief.md` or `overview.md` in the project's configured design
  directory (`paths.design` in `.clasi/config.yaml`, default
  `docs/design/`) — must exist
- `usecases.md` in the same configured design directory (project-level
  use cases, must exist, or overview covers use cases) — distinct from
  the sprint-level use cases written into each sprint's own `sprint.md`

## Critical Rule

**DO NOT create tickets** during roadmap mode or in steps 1-11 of detail
mode. Tickets are only created in step 13, after the sprint has advanced
to the `ticketing` phase. The `create_ticket` MCP tool will reject
attempts to create tickets before that phase. Follow the phases in order.

**DO NOT create a git branch** during planning. Branches are created
at execution time by `acquire_execution_lock`.

## Phase 1: Roadmap Mode

The team-lead dispatches to sprint-planner in roadmap mode for batch
planning. Multiple sprints can be planned in a single session.

### Roadmap Process

1. **Determine sprint number**: Check `clasi/sprints/` and
   `clasi/sprints/done/` for existing sprints. The new sprint gets the
   next sequential number (NNN format: 001, 002, ...).

2. **Mine the issues directory**: Scan `clasi/issues/` for ideas relevant
   to the sprint. Discuss relevant issues with the stakeholder.

   For each issue claimed by this sprint, set `sprint: "NNN"` in the
   issue's YAML frontmatter (using `write_artifact_frontmatter`).

3. **Create sprint directory**: Use the `create_sprint` MCP tool. This
   creates the directory structure and registers the sprint.

4. **Write sprint.md**: Create a lightweight `sprint.md` with:
   - Frontmatter: `status: roadmap`
   - Goals and feature scope
   - TODO references
   - No tickets, no architecture, no use cases

5. **Repeat** for additional sprints as needed.

### Roadmap Output

- Sprint directory `clasi/sprints/NNN-slug/` with `sprint.md`
- Sprint `sprint.md` status set to `roadmap`
- No branch created
- No tickets created

## Phase 2: Detail Mode

The team-lead dispatches to sprint-planner in detail mode when a
roadmap sprint is ready for execution. Detail mode fills in full
planning artifacts for one sprint at a time.

### Detail Process

1. **Verify sprint exists**: The sprint directory and roadmap `sprint.md`
   should already exist from Phase 1.

2. **Update sprint.md**: Update the existing `sprint.md` with full
   details. Set frontmatter `status: planning-docs`.

3. **Make the effort decision**: Decide, by feature size, whether this
   sprint is trivial/small (minimal or omitted Architecture/Use Cases
   sections, architecture review skipped) or substantial/structural
   (full sections, full review). See the sprint-planner `agent.md` for
   the full decision criteria.

4. **Write the Use Cases section**: Sprint-level use cases (SUC-NNN)
   within `sprint.md`, sized to the effort decision -- full use cases for
   a substantial sprint, "N/A -- trivial" for a small one.

5. **Write the Architecture section**: Within `sprint.md`, sized to the
   effort decision -- a full write-up describing what changed in this
   sprint, why, and the impact on existing components for a substantial
   sprint, or "N/A -- trivial" for a small one.

6. **Advance to architecture-review**: Call `advance_sprint_phase` to
   move from `planning-docs` to `architecture-review`.

7. **Architecture review**: If the effort decision was trivial/small,
   skip the review and call `record_gate_result` with gate
   `architecture_review` and result `skipped`. Otherwise, review the
   Architecture section (self-review by the sprint-planner, or delegate
   to a reviewer) against the existing architecture and codebase, then
   produce a verdict (APPROVE / APPROVE WITH CHANGES / REVISE).
   - If REVISE: update the Architecture section and re-review.
   - If APPROVE WITH CHANGES: note the changes for ticket creation.
   - Call `record_gate_result` with gate `architecture_review` and result
     `passed` or `failed`.

8. **Advance to stakeholder-review**: If architecture review passed or
   was skipped, call `advance_sprint_phase` to move to `stakeholder-review`.

9. **Breakpoint (conditional)**: Check the sprint's `sprint.md`
   Architecture section for a `## Open Questions` subsection.
   - If open questions **exist**: skip this breakpoint and proceed to
     step 10 (which resolves them interactively via `AskUserQuestion`).
   - If **no open questions** exist: present an `AskUserQuestion` to
     confirm continuation.

10. **Resolve open questions**: If open questions exist in the
    Architecture section:
    - Parse each numbered question into a separate `AskUserQuestion` call.
    - Provide 2-4 concrete options where possible.
    - After all questions are answered, replace `## Open Questions` with
      `## Decisions` listing each question and answer.

11. **Stakeholder review gate**: Present the sprint plan and architecture
    review to the stakeholder. Use `AskUserQuestion`:
    - "Approve sprint plan" (recommended)
    - "Request changes"
    - Call `record_gate_result` with gate `stakeholder_approval`.

12. **Advance to ticketing**: If stakeholder approved, call
    `advance_sprint_phase` to move to `ticketing`.

12b. **Split partial-scope issues**: Before creating tickets, review
    each issue claimed by this sprint. If an issue covers more work than
    fits in this sprint, call `split_issue(filename, new_filename,
    new_title, new_body)` to carve out the in-scope piece. The new file
    is a sibling of the original in the same directory. Then reference
    `new_filename` in the ticket's `issue` field. The original issue
    retains the out-of-scope portion for a future sprint.

13. **Create tickets**: Create tickets inline. Tickets are created in
    the sprint's `tickets/` directory with per-sprint numbering (001, 002, ...).

13b. **Update sprint.md ticket table**: After all tickets are created,
     update the `## Tickets` section in `sprint.md` with a summary table
     listing each ticket's number, title, and depends-on values, in
     dependency order. Tickets execute serially in the order listed.

14. **Acquire execution lock**: Call `acquire_execution_lock` to claim
    the lock and create the sprint branch. Then call
    `advance_sprint_phase` to move to `executing`.

15. **Set sprint status**: Update the sprint document status to `active`.

16. **Confirm before execution**: Present the list of tickets to the
    stakeholder. Use `AskUserQuestion`:
    - "Start executing tickets" (recommended)
    - "Review tickets first"

    **Do NOT ask again between individual tickets** -- once execution
    starts, tickets proceed without interruption.

### Detail Output

- Sprint directory with full planning documents: `sprint.md` with
  right-sized Architecture and Use Cases sections, plus `tickets/`
- Sprint `sprint.md` status set to `active`
- Sprint branch `sprint/NNN-slug` created (via acquire_execution_lock)
- Sprint phase advanced to `executing` in the state database
- Execution lock acquired for this sprint
- Tickets in `tickets/` ready for execution
