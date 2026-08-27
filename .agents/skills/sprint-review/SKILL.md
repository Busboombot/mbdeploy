---
name: sprint-review
description: Post-sprint validation — verifies all tickets are done, tests pass, and process was followed correctly
---


# Sprint Review Skill

Perform post-sprint validation before closing. This is a read-only
review that checks all process requirements were met.

## Inputs

- Sprint ID and path to the sprint directory
- All tickets should be in `done` status

## Validation

1. **Call `review_sprint_pre_close(sprint_id)`** (`ToolSearch` for
   `select:mcp__clasi__review_sprint_pre_close` first — it is
   deferred). This checks ticket completion (all `done`, all moved to
   `tickets/done/`), planning-doc status/content, and that you are on
   the sprint's branch. Interpret its `{passed, issues[]}` result
   directly as your Ticket Completion verdict below — do not re-derive
   these checks by hand.

2. **Do not run the test suite yourself.** `close_sprint`'s own
   internal test run is the sprint's single full-suite gate (031/008);
   re-running it here would recreate the exact redundant-run problem
   that ticket removed. If you want assurance beyond that gate, read
   `close_sprint`'s eventual result — its `repairs` list names whether
   tests ran for real or were skipped via the HEAD-sha marker — rather
   than invoking a second real run.

3. **Check by hand what `review_sprint_pre_close` does not cover**:

   ### Architecture
   - [ ] Architecture document reflects the actual end-of-sprint state
   - [ ] Sprint Changes section is filled in
   - [ ] Architecture version matches the sprint

   ### Git State
   - [ ] All changes are committed on the sprint branch
   - [ ] No uncommitted modifications related to the sprint
   - [ ] Commit messages reference ticket IDs

## Output

- **Verdict**: pass or fail — `review_sprint_pre_close`'s `passed`
  field, AND-ed with the manual Architecture/Git State checks above
- **Checklist results**: each item with pass/fail and details (tool
  `issues[]` entries plus your own manual findings)
- **Blocking issues**: anything that must be fixed before close
- **Advisory notes**: non-blocking observations for future improvement

## Rules

- This is read-only. Do not modify any files.
- Report all findings, not just failures.
- Be specific about failures: which ticket, which criterion, what is wrong.
- Distinguish blocking issues from advisory notes.
