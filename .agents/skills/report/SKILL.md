---
name: report
description: Create a GitHub issue on the clasi repository to report bugs or problems
---

# Report Issue Skill

Creates a GitHub issue on the `ericbusboom/clasi` repository to report
bugs, issues, or problems with the CLASI tools, skills, or process.

## Process

1. **Collect issue information**:
   - Title: concise summary (auto-generate if missing)
   - Description: what you were trying to do, what went wrong, steps
     to reproduce, expected vs actual behavior, error messages, context

2. **Create the issue**: Prefer direct GitHub API when `GITHUB_TOKEN`
   or `GH_TOKEN` is available. Fall back to `gh` CLI:
   ```bash
   gh issue create \
     --repo ericbusboom/clasi \
     --title "TITLE" \
     --body "DESCRIPTION"
   ```

3. **Confirm**: Report the issue number and URL.

## Usage

```
/se report
/se report The plan-sprint skill doesn't handle empty sprints correctly
```
