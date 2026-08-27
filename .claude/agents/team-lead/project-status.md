---
name: project-status
description: Analyzes current project state by reading SE artifacts and reports stage, progress, and next actions
---

# Project Status Skill

This skill scans the SE artifacts and tickets to determine where a project
stands and what should happen next.

## Agent Used

**team-lead** (or can be invoked standalone)

## Process

1. **Check artifacts**: Verify which SE documents exist:
   - `.clasi/brief.md`
   - `usecases.md` in the project's configured design directory
     (`paths.design` in `.clasi/config.yaml`, default `docs/design/`)
   - `docs/architecture/` (versioned architecture documents)
2. **Check sprints**: Scan `clasi/sprints/` for active sprints and
   `clasi/sprints/done/` for completed sprints.
3. **Scan tickets**: For each active sprint, read ticket files in the
   sprint's `tickets/` and `tickets/done/` directories. Extract frontmatter
   status for each.
4. **Determine stage**:
   - No brief → Stage 1 (Requirements) not started
   - Brief but no use cases → Stage 1 in progress
   - Use cases but no architecture → Stage 1b (Architecture) needed
   - Architecture exists, no active sprint → Ready for sprint planning
   - Active sprint with `open` tickets → Stage 3 (Implementation)
   - Active sprint, all tickets `done` → Sprint ready to close
   - No active sprint, all tickets `done` → Stage 4 (Maintenance)
5. **Report progress**:
   - Artifacts: which exist, their status
   - Sprint: active sprint name, status, branch
   - Tickets: count by status (open, in-progress, done)
   - Next action: what should be done next
6. **Identify blockers**: Missing dependencies, in-progress tickets without
   plans, tickets with unmet depends-on.

## Output

A structured status report showing current stage, artifact status, ticket
progress, and recommended next action.
