---
paths:
  - "**"
---

This project uses the CLASI MCP server. Before doing ANY work:

1. **Check for the OOP override first.** If `clasi oop status` (or the
   presence of the emergency file `.clasi/oop`) shows the bypass active,
   ignore CLASI entirely for this session: skip MCP verification, skip
   all CLASI process gates (sprints, tickets, TODOs, artifact rules), and
   proceed with the user's request as you would in any non-CLASI repo.
   Do not call `get_version()` or any other CLASI MCP tool. The
   stakeholder has explicitly opted out for this checkout via
   `clasi oop on --reason '...'`.
2. Otherwise, call `get_version()` to verify the MCP server is running.
3. If the call fails, STOP. Do not proceed. Tell the stakeholder:
   "The CLASI MCP server is not available. Check .mcp.json and
   restart the session, or run `clasi oop on --reason '...'` to bypass
   CLASI for this session (or create `.clasi/oop` as an emergency
   fallback if `clasi` itself is broken)."
4. Do NOT create sprint directories, tickets, TODOs, or planning
   artifacts manually. Do NOT improvise workarounds. All SE process
   operations require the MCP server.
