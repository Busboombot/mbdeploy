---
paths:
  - clasi/**
---

You are modifying CLASI planning artifacts. Before making changes:

1. If the OOP bypass is active (`clasi oop status`; enabled via
   `clasi oop on --reason '...'`, or the emergency file `.clasi/oop`),
   the stakeholder has opted out of CLASI for this session. Skip these
   gates entirely and proceed.
2. Confirm you have an active sprint (`list_sprints()` — a sprint whose
   status is not `roadmap` or `done` is active), or the stakeholder said
   "out of process" / "direct change".
3. If creating or modifying tickets, the sprint must be in `ticketing`
   or `executing` phase (`get_sprint_phase(sprint_id)`).
4. Use CLASI MCP tools for all artifact operations — do not create
   sprint/ticket/TODO files manually.

Direct edits to `clasi/sprints/` are blocked for team-lead. Use MCP tools.
