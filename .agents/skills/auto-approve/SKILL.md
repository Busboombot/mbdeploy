---
name: auto-approve
description: Enable autonomous mode — skip interactive breakpoints in skills
---

# Auto-Approve Mode

When the stakeholder says "auto-approve", "run without asking", or similar:

1. Acknowledge that auto-approve mode is now active.
2. At every `AskUserQuestion` breakpoint, automatically select the first
   (recommended) option without presenting the UI.
3. Log each auto-approval visibly:
   `Auto-approved: "[option selected]" at [skill name] step [N]`
4. Continue until deactivated or session ends.

This is **session-scoped** — does NOT persist across conversations.

## Activation Phrases

- "auto-approve"
- "run without asking"
- "don't ask, just do it"
- "proceed autonomously"

## Deactivation Phrases

- "stop auto-approving"
- "start asking again"
- "pause auto-approve"
