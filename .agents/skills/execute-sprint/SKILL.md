---
name: execute-sprint
description: Executes sprint tickets — dispatches programmer agents serially, in dependency order, then closes the sprint
---


# Execute Sprint Skill

This skill executes all tickets in an active sprint, one at a time, in
dependency order. There is exactly one execution path — no
`worktree`-flag branch, no parallel dispatch.

## Inputs

- Active sprint with tickets in `open` status
- Execution lock acquired (`acquire_execution_lock`)
- Sprint branch exists and is checked out

## Process

The controller itself never creates a git worktree — every ticket is
worked directly on the checked-out sprint branch (§3 below). `worktree.py`
still exists, but only as a reconcile/cleanup/audit module for worktrees
left behind by *other* tooling; `close_sprint`'s worktree-pruning step
calls `reconcile_worktrees`/`cleanup_worktree` as its own internal
safety net, unrelated to how many tickets a sprint ran (see the
close-sprint skill's "Worktree Pruning at Close" section for what that
step actually does). Nothing in this skill needs to call either
function directly.

### 1. Read Tickets

Read all tickets from the sprint's `tickets/` directory. Parse
frontmatter for `status`, `depends-on`, and `id`.

### 2. Order by Dependencies

Build a dependency graph from `depends-on` fields and produce a flat,
topologically-sorted list of tickets. Tie-breaks by ticket id ascending.

There are no execution groups. Tickets run one at a time.

### 3. Dispatch Programmer Agents Serially

For each ticket in dependency order:

1. Verify the ticket is `open` and all of its `depends-on` tickets are
   `done`. If not, stop and report the inconsistency.
2. Update the ticket status to `in-progress` via
   `update_ticket_status(path, "in-progress")`.
3. Invoke the programmer agent via the Agent tool with:
   - Path to the ticket file
   - Path to the ticket plan (if separate)
   - Sprint ID and ticket ID
   - Sprint branch name (the agent works on this branch directly)
   - Architecture context, sourced per `Project.design_docs_opt_in`:
     - **Not opted in**: the relevant Architecture section of the
       sprint's `sprint.md` (as today).
     - **Opted in**: the path(s) to the relevant canonical subsystem
       doc(s), co-located as `<subsystem_path>/DESIGN.md`
       (e.g. `src/clasi/tools/DESIGN.md`) — or, for the system-level
       document, `docs/design/design.md` — *plus* the path to this
       sprint's edited overlay copy of that same doc under
       `clasi/sprints/NNN-slug/design/<name>.md` — both paths, not just
       one. The canonical doc gives the agent the subsystem's settled,
       pre-sprint understanding; the overlay copy gives it this sprint's
       planned changes to that understanding. Identify which doc(s)
       apply by checking which docs the sprint's `design/` directory
       contains (the same doc_names the sprint-planner passed to
       `seed_sprint_design_overlay`) and matching against the ticket's
       scope; a ticket touching a subsystem with no corresponding
       overlay file gets only the canonical doc path (nothing changed
       there this sprint). If the sprint carries no `design/` directory
       at all (trivial/compact sprint, or opted-out project), fall back
       to the not-opted-in behavior above.
4. Wait for the programmer agent to complete before moving on.
5. Verify `status: done` is set in the ticket's frontmatter.
6. Call `move_ticket_to_done(ticket_path)` where `ticket_path` is the
   relative path: `docs/clasi/sprints/NNN-slug/tickets/NNN-slug.md`.
   This is a team-lead responsibility — the programmer sets the
   frontmatter; the team-lead moves the file.
7. Continue with the next ticket.

**Do not** invoke a second programmer agent until the first has
returned. Do not create git worktrees. Do not branch off the sprint
branch.

### 4. Handle Failures

If a programmer agent fails, escalate to the stakeholder. Do not skip
the ticket and continue — the dependency chain assumes each prior
ticket is complete.

If a programmer agent leaves a ticket in `in-progress` (e.g. because
tests failed and the agent reported back without marking it done):
fix the issue in-process or with a follow-up programmer dispatch on
the same ticket. Either way, the ticket must end at `done` before
moving to the next one.

**Ticket completion is mandatory.** When a programmer completes a
ticket, its status must be set to `done` and `move_ticket_to_done`
called. There is no valid reason to leave a completed ticket in an
incomplete state. If the stakeholder says "leave it open", that means
leave the sprint open — the ticket itself must still be marked done.

### 5. Close Sprint

After all tickets are `done`:

1. Verify all tickets have `status: done`.
2. Present sprint summary to stakeholder.
3. Invoke the `close-sprint` skill. Do **not** run the full test suite
   here first — `close_sprint`'s own internal test run (via
   `test_command`, default `uv run pytest`) is the sprint's *only*
   full-suite run (031/008). Each programmer agent ran only its own
   ticket's scoped tests during execution (see the programmer agent
   definition); running the full suite again here, before handing off
   to `close_sprint`, would just be a second identical run against an
   unchanged tree — exactly the redundant-run problem 031/008 removes.
   See `instructions/software-engineering.md`'s Testing Discipline
   section for the canonical statement of how many full-suite runs a
   sprint gets and who owns it.

## Output

- All tickets implemented and marked done
- Sprint ready for review and close; `close_sprint`'s own test run is
  the pass/fail gate on the suite, not a step performed here
