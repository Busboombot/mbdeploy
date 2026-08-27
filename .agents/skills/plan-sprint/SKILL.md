---
name: plan-sprint
description: Creates sprint plans using a two-phase model — roadmap (batch, lightweight) and detail (full artifacts, pre-execution)
---


# Plan Sprint Skill

This skill creates sprint plans using a two-phase model:

- **Phase 1 — Roadmap**: Batch planning. Multiple sprints can be planned
  in one session. Produces a lightweight `sprint.md` only (goals, scope,
  TODO references). No branches created.

- **Phase 2 — Detail**: One sprint at a time. Produces full planning
  artifacts via the sprint-planner agent: a single `sprint.md` with
  right-sized Architecture and Use Cases sections (or, when the project
  has opted into the persistent design-doc set, a `design/` overlay
  instead of the Architecture section — see "Design overlay opt-in
  check" below), plus tickets. Sizing is a three-tier decision (trivial /
  compact / substantial) — see the sprint-planner `agent.md` for the full
  criteria and how each tier handles diagrams and the architecture review
  gate (`skipped` for trivial, a scoped review for compact, the full
  review for substantial).

Branches are created later via `acquire_execution_lock`, not during
planning. All planning happens on main.

## Inputs

- Stakeholder conversation describing the work to be done
- `docs/clasi/design/overview.md` (must exist)

## Critical Rules

**DO NOT create tickets** during roadmap mode or before the sprint's
`architecture_review` gate has been recorded. `create_ticket` checks that
gate's recorded result directly on the sprint's first call (not a phase
index) and rejects if it is not `passed`/`skipped`; on success it
auto-advances the phase to `ticketing` itself — there is no separate
`stakeholder-review` phase to wait for and no `advance_sprint_phase` call
to make (sprint 031 ticket 002). Tickets are therefore created *before*
the stakeholder ever reviews the plan (Phase 2 step 3 below), not after —
the stakeholder reviews the completed plan **with its tickets already in
place** (Phase 2 step 3).

**DO NOT create a git branch** during planning. Branches are created
at execution time by `acquire_execution_lock`.

**Team-lead (tier 0) may call `create_sprint` and write sprint files
under `clasi/sprints/` directly** — mcp-guard's tier-0 block on
`create_sprint` was lifted (sprint 031 ticket 003); only `create_ticket`
remains tier-0-blocked, since ticket creation stays sprint-planner-owned.
This flow (Phase 1 step 3 below) is written generically because either
actor may call `create_sprint` — team-lead directly, or a dispatched
sprint-planner as part of authoring the roadmap content in the same
call — see `.claude/agents/team-lead/agent.md` for which one a given
scenario uses.

## Issue Linkage

During the planning-docs phase (Phase 1, after `create_sprint`), call
`link_sprint_issues(sprint_id, issue_filenames)` to associate issues with
the sprint. This writes the `issues:` list in the sprint's frontmatter and
updates each issue file's `sprint:` field. Do not write the `issues:` field
manually via `write_artifact_frontmatter`.

The correct tool signature is: `link_sprint_issues(sprint_id, issue_filenames)`

## Phase 1: Roadmap Mode

For batch roadmap planning of multiple sprints.

### Process

1. **Determine sprint number**: Check `docs/clasi/sprints/` and
   `docs/clasi/sprints/done/` for existing sprints. Next sequential number.

2. **Mine the TODO directory**: Scan `docs/clasi/todo/` for relevant ideas.
   Discuss with the stakeholder.

3. **Create sprint directory**: Use the `create_sprint` MCP tool.

4. **Write sprint.md**: Lightweight plan with:
   - Frontmatter: `status: roadmap`
   - Goals and feature scope
   - Issue references
   - No tickets, no architecture, no use cases

5. **Link issues**: Call `link_sprint_issues(sprint_id, issue_filenames)`
   for each issue claimed by this sprint. Do not set `issues:` frontmatter
   manually.

6. **Repeat** for additional sprints as needed.

## Phase 2: Detail Mode

When a roadmap sprint is ready for execution. Invoke the sprint-planner
agent via the Agent tool to fill in full planning artifacts.

### Process

1. **Verify sprint exists**: Sprint directory and roadmap `sprint.md`
   should already exist from Phase 1.

2. **Invoke sprint-planner agent**: Use the Agent tool to dispatch the
   sprint-planner agent with:
   - Sprint ID and directory path
   - Sprint goals and TODO references
   - Path to `docs/clasi/design/overview.md`
   - Path to current architecture

   The sprint-planner handles architecture, architecture review, and
   ticket creation inline — no sub-dispatches needed.

   2a. **Split partial-scope issues**: If any issue in scope covers work
       that cannot all fit in this sprint, call `split_issue` first to
       carve out the in-scope piece as a separate issue file. The split
       issue will be a sibling of the original in the same directory.
       Then call `create_ticket(issue=<split-filename>)` to bring the
       in-scope piece into the sprint. The remainder stays in the pool
       or sprint for a future sprint.

   2b. **Design overlay opt-in check**: Read `Project.design_docs_opt_in`.
       - **Unset or `False`**: skip design-overlay steps entirely; the
         sprint-planner writes the Architecture section of `sprint.md` as
         it always has (Mode 2 of `architecture-authoring`). No
         `design/` directory is created for this sprint.
       - **`True`**: after the sprint-planner makes its sizing decision
         (trivial / compact / substantial) and identifies which canonical
         `docs/design/` doc(s) the sprint's changes affect (Mode 2a, Step
         1 of `architecture-authoring`), it makes **one single call** to
         `seed_sprint_design_overlay(sprint_id, doc_names)`, passing
         *every* affected doc's name in that one `doc_names` list — not
         one call per doc, and not just the first doc identified. A
         sprint that touches the system doc plus two subsystem docs
         seeds all three in that one call (see `architecture-authoring`
         Mode 2a Step 2 for the accepted `doc_names` forms and the
         per-doc overlay-slug mechanics that let multiple co-located
         `DESIGN.md` files be seeded together without colliding).
         **This is the sequencing resolution for where the
         seed-and-commit step fires**: it happens here, during Phase 2
         detail planning, once the affected docs are known — not at
         `create_sprint` (Phase 1), which runs before any sprint-planner
         analysis exists to name the affected docs. `create_sprint` itself
         never calls into the design-overlay machinery. The seed commit
         still lands on `main`, before `acquire_execution_lock` branches
         off it (sprint 021 Open Question 3's resolution), because Phase 2
         entirely precedes `acquire_execution_lock` (step 4 below) — so
         the sprint branch, once created, already contains the seed
         commit. For a trivial-tier sprint, skip this call: no `doc_names`
         means no overlay, matching the "N/A — trivial" skip on the
         not-opted-in path.
       - The sprint-planner then edits the seeded copies, generates diffs,
         and validates, per `architecture-authoring` Mode 2a Steps 2-5,
         before architecture review runs.

3. **Stakeholder review**: Present the completed plan to the stakeholder.
   Call `review_sprint_pre_execution`, which runs the sprint's
   precondition checks and, when opted in and the sprint carries a
   `design/` overlay, additionally generates diffs (`generate_diffs`) and
   commits the edited overlay copies (`commit_edits`) as its final step —
   only after every other precondition has already passed. This is the
   commit that closes the git-dirty window Mode 2a's Step 3 opened: from
   here on, the sprint's `design/` directory is clean until execution
   ends. Record stakeholder approval gate (`record_gate_result`).

4. **Acquire execution lock**: Call `acquire_execution_lock` to claim
   the lock and create the sprint branch. The call checks the
   `stakeholder_approval` gate recorded in step 3 and grants no lock if
   it is missing, then auto-advances the phase to `executing` itself —
   no separate `advance_sprint_phase` call. Because the edited-copy
   commit (step 3) already landed on `main`, the branch created here
   already contains it — no cross-branch reconciliation is needed.

5. **Set sprint status**: Update sprint doc status to `active`.

## Output

- Sprint directory with full planning documents
- Sprint branch created (via acquire_execution_lock)
- Tickets in `tickets/` ready for execution
