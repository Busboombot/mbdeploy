---
name: gh-import
description: Import GitHub issues as CLASI TODOs with issue reference tracking
---

# GitHub Import Skill

This skill fetches open issues from a GitHub repository and creates
CLASI TODO files from them, with `github-issue` references for lifecycle
tracking.

## Process

1. **Parse arguments**: Extract optional repo name and label filter from
   the user's input. If no repo specified, use the current repository.

2. **Verify access**: Call `list_github_issues` with `limit: 1` to check
   `gh` CLI access. If it fails, report the error and stop.

3. **Fetch issues**: Call `list_github_issues` with full parameters
   (repo, labels, state "open", limit 30).

4. **Bulk import gate**: If more than 5 issues are returned:
   - Present the list to the user (number, title, labels for each)
   - Ask the user to choose: import all, select specific, filter by label, or cancel
   - Do not proceed without user confirmation.

   If 5 or fewer issues, proceed directly.

5. **Create issues**: For each selected issue, create an issue file in
   `clasi/issues/` with:
   - Filename: slugified issue title
   - YAML frontmatter: `status: pending`, `github-issue: "owner/repo#N"`
   - Heading: issue title
   - Body: issue body (truncated to first 2000 chars if very long)
   - Source line: `> Imported from [owner/repo#N](url)`

6. **Confirm**: Report how many issues were created with issue numbers
   and corresponding filenames.

## Example Usage

```
/se gh-import
/se gh-import ericbusboom/other-repo
/se gh-import --labels bug
/se gh-import ericbusboom/other-repo --labels "bug,enhancement"
```

## Notes

- The `github-issue` field uses `owner/repo#N` format for unambiguous
  cross-repo references.
- When TODOs are consumed by a sprint, the `github-issue` field carries
  forward to tickets and the sprint doc.
