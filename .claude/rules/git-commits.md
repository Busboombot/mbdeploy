---
paths:
  - "**/*.py"
  - "**/*.md"
---

Before committing, verify:
1. All tests pass (run the project's test suite).
2. If on a sprint branch, the sprint has an execution lock.
3. Commit message references the ticket ID if working on a ticket.

## Version bump cadence

Cadence: **once per sprint, at `close_sprint`. Do not run
`dotconfig version bump` manually during ticket work on a sprint
branch** — `close_sprint` already bumps and tags exactly once per
sprint (`version_trigger` setting, default `every_change`, evaluated
at sprint close). A manual mid-sprint bump would double-count against
that, not add signal.

Tools are installed editable, so "which code is live" still needs an
answer between commits — that need hasn't gone away, it's now met by
an automatic check instead of a manual one. CLASI's own staleness
detection (`clasi.staleness.check_staleness`, wired into `get_version()`
and the role/mcp guards) compares the running build against this
project's source on effectively every hook call and fails closed
(`stale-guard`) on drift. That fires far more often, and more reliably,
than an agent remembering to bump after each commit — bumping is a
release-note-style marker of "a sprint finished," not the live-build
check anymore.

**Exception — OOP / non-sprint commits**: if working directly on
`master` (no sprint branch), there is no `close_sprint` event to anchor
to. Run `dotconfig version bump` after each OOP commit and commit the
result (`chore: bump version`), same as before.

See `instructions/git-workflow` for full rules.
