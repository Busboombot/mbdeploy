---
name: project-knowledge
description: Captures hard-won technical understanding from difficult debugging sessions and non-obvious fixes
---

# Project Knowledge Skill

Captures hard-won technical understanding when agents work through
genuinely difficult problems. The knowledge is preserved for future
sessions.

**Different from reflections**: Reflections capture process failures.
Project knowledge captures technical victories — the problem was
genuinely hard, it was figured out, and that understanding should persist.

## Triggers

1. **Stakeholder excitement** — "it works!", "finally!", "that fixed it"
2. **Resolution after struggle** — multiple failed attempts, now working
3. **Non-obvious fix** — surprising, counterintuitive, poorly documented
4. **Agent self-recognition** — unusually difficult path to solution

## Process

1. **Confirm with stakeholder**: "This was hard-won knowledge. Want me
   to record it?"

2. **Gather context**: Review the conversation for the original problem,
   symptoms, failed attempts, working solution, and underlying explanation.

3. **Write knowledge file**: Create
   `docs/knowledge/YYYY-MM-DD-slug.md`:

   ```yaml
   ---
   date: YYYY-MM-DD
   tags: [relevant, searchable, terms]
   related-tickets: [NNN]
   ---
   ```

   Sections:
   - **Problem** — What was broken
   - **Symptoms** — Observable behavior
   - **What Was Tried** — Each attempt and why it failed
   - **What Worked** — The actual fix with enough detail to reproduce
   - **Why It Works** — The underlying explanation
   - **Future Guidance** — Actionable rules for future agents

4. **Commit**: `docs: record project knowledge -- <title>`

## Output

- Knowledge file in `docs/knowledge/`
- Commit with descriptive message
