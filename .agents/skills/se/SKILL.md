---
name: se
description: CLASI Software Engineering process dispatcher
---

# /se

Dispatch to the CLASI SE process. Parse the argument after `/se` and
invoke the matching skill from the table below.

If `/se` is called with **no arguments**, display this help listing
to the user and stop — do not execute any skill.

## Available commands

| Command | Description | Action |
|---------|-------------|--------|
| `/se status` | Show project status — sprints, tickets, next actions | Invoke the `project-status` skill |
| `/se issue <text>` | Create an issue file from the description | Invoke the `issue` skill |
| `/se init` | Start a new project with a guided interview | Invoke the `project-initiation` skill |
| `/se report` | Report a bug with the CLASI tools | Invoke the `report` skill |
| `/se gh-import [repo] [--labels L]` | Import GitHub issues as issues | Invoke the `gh-import` skill |
| `/se knowledge <description>` | Capture hard-won technical understanding | Invoke the `project-knowledge` skill |
| `/se oop` | Make a quick out-of-process change | Invoke the `oop` skill |
| `/se plan` | Enter plan mode for a discussed issue | Enter plan mode via `EnterPlanMode` |

Pass any remaining text after the subcommand as the argument to the
skill (e.g., `/se issue fix the login bug` passes "fix the login bug"
to the issue skill).

## When to use /se issue vs /se plan

- `/se issue <text>`: Quick capture. The user has a clear idea and just
  wants it recorded. One statement → one issue file.
- `/se plan`: The user wants to discuss, explore, and refine an idea
  before capturing it. Enters plan mode for a conversation. On exit,
  the plan-to-issue hook automatically creates the issue.
