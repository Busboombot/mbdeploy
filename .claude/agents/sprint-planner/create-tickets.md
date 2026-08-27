---
name: create-tickets
description: Breaks a sprint architecture into sequenced, numbered implementation tickets with dependency ordering
---

# Create Tickets Skill

This skill breaks a sprint's architecture document (especially the Sprint
Changes section) into actionable implementation tickets. The sprint-planner
handles ticket creation inline.

## Agent Used

**sprint-planner**

## Inputs

- Sprint's `sprint.md` (must exist in the sprint directory) -- its
  Architecture and Use Cases sections, sized to the sprint's effort
  decision (may read "N/A -- trivial" for a trivial/small sprint)

## Process

1. **Read artifacts**: Read the sprint's `sprint.md` Architecture and
   Use Cases sections.
2. **Verify issue linkage first**: Read `sprint.md`'s frontmatter `issues:`
   field. If this sprint claims any issue not yet listed there, call
   `link_sprint_issues(sprint_id, [filenames])` before creating any tickets.
   Do not assume an earlier planning step already did this — confirm it.
   `create_ticket`'s auto-link (step 4 below) only fires when the sprint
   has **exactly one** linked issue — that's the only case where "the
   sprint's issue" is an unambiguous default for "this ticket's issue."
   **On any multi-issue sprint (2+ linked issues), pass `issue=<filename>`
   explicitly on every `create_ticket` call** — omitting it leaves the
   ticket's `issue:` field empty rather than guessing.
4. **Identify work units**: Break the Sprint Changes into coherent
   implementation units. Each unit should be completable in one focused
   session.
5. **Order by dependency**: Number tickets so that foundation work comes
   before features that depend on it. Record dependencies in each ticket's
   `depends-on` field.
6. **Create ticket files**: Write each ticket to the sprint's
   `tickets/NNN-slug.md` with YAML frontmatter (id, title, status,
   use-cases, depends-on, github-issue) and body (description, acceptance
   criteria, implementation notes). Ticket numbering is per-sprint
   (starts at 001).

   **Issue lifecycle:** When you call `create_ticket(sprint_id, title,
   issue=<filename>)`, the referenced issue file is physically moved from
   `clasi/issues/` into `<sprint>/issues/` and its frontmatter is updated
   to `status: in-progress`. When all tickets referencing that issue are
   moved to done, `Issue.move_to_done()` is called automatically, which
   moves the file into `<sprint>/issues/done/`. No manual
   `move_issue_to_done` call is needed in the happy path. If step 2 already
   linked the issue at the sprint level **and it is the sprint's only
   linked issue**, omitting `issue=` still auto-links via the sprint's
   `issues:` field. On a sprint with 2+ linked issues, auto-link does
   *not* fire — pass `issue=<filename>` explicitly, and always
   double-check the result (step 7) rather than assuming it fired.

7. **Propagate issue and GitHub issue references — verify, don't assume**:
   When creating tickets from issues, confirm the ticket's `issue`
   frontmatter field is set to the issue filename (e.g.,
   `issue: "my-idea.md"`). This creates the back-link from ticket to issue.
   For every ticket implementing an issue that did NOT get the field set
   automatically (a second or later ticket for the same issue, or an issue
   linked after the first `create_ticket` call), call
   `add_issue_ref(ticket_path, issue_filename)` explicitly — do not leave
   the back-reference unset. Also copy `github-issue` if present. After all
   tickets are created, collect all `github-issue` references from the
   sprint's tickets and list them in the sprint doc's `## GitHub Issues`
   section using the format `owner/repo#N`.
8. **Verify coverage**: Every use case must be covered by at least one
   ticket. Every ticket must trace to at least one use case.
9. **Verify sequencing**: No circular dependencies. Foundation before
   features.

## Output

Numbered ticket files in the sprint's `tickets/` directory, ready for
implementation.
