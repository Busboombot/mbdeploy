---
name: oop
description: Out-of-process mode — skip SE ceremony for small, targeted changes
---

# /oop — Out of Process

Skip all SE process ceremony and make a quick, targeted change directly
on master. This is for changes where the full process would be overkill.

The stakeholder may request this mode with a variety of phrases, such as: 

- "DO it out of process"
- "Skip the process, just change it"
- "Don't create tickets, just fix it"
- "This is a one-off, just change it on master"
- "oop, plesse"  

## When to use

- When the stakeholder requests it. 

## When NOT to use

- THe stakeholder did not explicitly request it.  

## Enabling the bypass

The primary way to enable OOP is the CLI, which records who/why/until-when
in the state DB — this is what makes the bypass auditable instead of a
silent, forgettable flag:

- `clasi oop on --reason '<why>'` — enable the bypass. Reason is
  required; if omitted, the command prompts for it interactively rather
  than defaulting to blank. Defaults to an 8-hour TTL (`--ttl-hours` to
  override); the bypass self-expires so it can never be forgotten on
  indefinitely.
- `clasi oop off` — disable the bypass. Clears the DB record **and**
  removes any leftover flag files. "Off" means off everywhere.
- `clasi oop status` — show whether the bypass is active, its source
  (DB, file, or both), reason, age, and expiry.

**Emergency fallback**: if `clasi` itself is broken (e.g. the CLI won't
run), create an empty file at `.clasi/oop` in the project root as an
unconditional override — it works even when the DB layer or the `clasi`
command itself is unavailable. This file mechanism has no audit trail
(no reason, no timestamp, no expiry), so prefer the CLI whenever `clasi`
is working. Remove `.clasi/oop` (or run `clasi oop off`, which removes
it too) once the emergency has passed.

## Process

1. Read the relevant code.
2. Make the change.
3. Run the full test suite: `uv run pytest`.
4. If tests pass, commit directly to master with a descriptive message.
5. Run `dotconfig version bump` and commit the result (`chore: bump
   version`). Tools are installed editable, so the version is how
   sessions tell which code is live — bump after every OOP commit.
6. If the work addressed an issue (from `clasi/issues/`), call
   `move_issue_to_done(filename)` to close it. The commit is not the
   finish line — the issue lifecycle must be closed too.
7. If tests fail, fix the issue and re-run.

That's it. No sprint, no tickets, no review gates, no architecture review.

## Rules

- Do NOT create sprints, tickets, or planning documents.
- Do NOT use `create_sprint`, `create_ticket`, or other artifact tools.
- Do NOT ask for stakeholder approval at process gates — there are no gates.
- DO run tests before committing. Tests are never optional.
- DO write a clear commit message explaining the change.
- DO run `dotconfig version bump` after each commit and commit the result.
