---
paths:
  - clasi/issues/**
---

Use the CLASI `issue` skill or `move_issue_to_done` MCP tool for issue
operations. Do not use the generic TodoWrite tool for CLASI issues.

Exception: if the OOP bypass is active (`clasi oop status`; enabled via
`clasi oop on --reason '...'`, or the emergency file `.clasi/oop`), the
stakeholder has opted out of CLASI for this session. Use whatever TODO
mechanism you prefer.
