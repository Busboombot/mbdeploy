---
name: issue
description: Create an issue file from user input and place it in clasi/issues/
---

# Issue Skill

This skill captures an idea or task as an issue file in the project's
issues directory.

## Process

1. Take the user's input (everything after `/issue` or `/se issue`).
2. Create a markdown file in `clasi/issues/` with:
   - YAML frontmatter: `status: pending`
   - A `# ` heading summarizing the idea
   - A description section expanding on the idea
3. Filename: slugified version of the heading (e.g., `my-idea.md`).
4. If `clasi/issues/` doesn't exist, create it.

## Output

Confirm the file was created and show its path.

## When to use this skill vs plan mode

This skill is for **quick capture** — the user has a clear idea and
wants it recorded as an issue. If the user wants to discuss, explore
options, or refine an idea before capturing it, use plan mode
(`EnterPlanMode`) instead. The plan-to-issue hook will create the
issue automatically when plan mode exits.

## Splitting an Issue

When a sprint planner discovers that only part of an issue fits in the
current sprint, use the `split_issue` MCP tool:

1. Call `split_issue(filename, new_filename, new_title, new_body)`.
   - `filename`: the original issue file.
   - `new_filename`: a new slug for the split-off piece.
   - `new_title`, `new_body`: content for the new file.
   - `updated_body` (optional): replacement body for the original.
2. The new file is created as a sibling of the original in the same
   directory. If the original is sprint-scoped and in-progress, the
   new file inherits the sprint context; otherwise it starts as pending
   in the pool.
3. Both files get mutual cross-link frontmatter (`split_from` on the
   new file, `split_into` appended on the original).
4. Then call `create_ticket(issue=<new_filename>)` if you want the new
   piece in the current sprint, or leave it in the pool for a future
   sprint.
