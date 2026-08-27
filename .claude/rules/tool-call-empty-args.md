---
paths:
  - "**"
---

# Tool Call Empty-Argument Bug

## The Bug

Confirmed in Claude Code (VS Code extension and CLI harness): if **any** argument in
a tool call is empty (`""`) or null (`None`/omitted), the harness silently drops **all**
arguments. The tool receives `input_value={}` — a completely empty input — and Pydantic
validation raises `Field required` for any required fields.

Symptoms observed: sprint-closure failures with `sprint_id: Field required, input_value={}`.

## Mitigation: Use "NONE" for optional parameters

When a parameter is optional and you have no value to pass, use the literal string
`"NONE"` instead of empty string or null:

  CORRECT:   close_sprint(sprint_id="016", test_command="NONE")
  INCORRECT: close_sprint(sprint_id="016", test_command="")
  INCORRECT: close_sprint(sprint_id="016")   # if tool call omits optional args as null

## Server-side stripping

The CLASI MCP server converts `"NONE"` back to `None` before dispatching to the tool
function. The `_strip_none_sentinel` function in `clasi/mcp_server.py` handles this
transparently. Tool implementations receive `None` and apply their defaults normally.

Do NOT pass `"NONE"` for required parameters — only for optional ones.

Note: a parameter that legitimately accepts the string `"NONE"` as a real value would
be incorrectly stripped. This is a known limitation of the blanket-sentinel approach.

## ToolSearch first for deferred tools

MCP tools listed as "deferred" in system-reminder messages have no loaded schema.
Calling them directly will fail with `InputValidationError`. Always call `ToolSearch`
with `query: "select:<ToolName>"` to load the schema before the first invocation.
