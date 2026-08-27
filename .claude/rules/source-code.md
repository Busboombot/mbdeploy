You are modifying source code or tests. This rule applies everywhere
except CLASI's own process artifacts and docs — `.clasi/`, `.claude/`,
`docs/`, and `*.md` files are not source code and are not gated by this
rule (no glob can express "everything except these four," so this
exclusion lives here in prose instead of in `paths:`).

Before writing code:

1. If the OOP bypass is active (`clasi oop status`; enabled via
   `clasi oop on --reason '...'`, or the emergency file `.clasi/oop`),
   the stakeholder has opted out of CLASI for this session. Skip these
   gates entirely and proceed.
2. You must have a ticket in `in-progress` status, or the stakeholder
   said "out of process".
3. If you have a ticket, follow the programmer agent definition — call
   `get_agent_definition("programmer")` if unsure of the steps.
4. Run tests after changes, in the foreground (never
   `run_in_background: true`). If working a ticket, scope the run to
   the modules the ticket touches — the full suite runs exactly once
   per sprint, inside `close_sprint` itself, not a per-ticket step. If
   working out-of-process (no sprint), run the full test suite, since
   there is no later sprint-close gate to catch what a scoped run
   would miss.
5. A commit message is not a process action. Only an MCP call (e.g.
   `update_ticket_status`, `move_ticket_to_done`) moves a ticket —
   writing "closes 005" or similar in a commit message does not.
