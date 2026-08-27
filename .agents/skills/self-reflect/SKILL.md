---
name: self-reflect
description: Produces a structured reflection document after a stakeholder correction
---

# Self-Reflect Skill

Produces a structured reflection when the agent makes an error that the
stakeholder corrects. Captures what went wrong and proposes improvements.

## Process

1. **Capture the error**: What action was wrong. Cite the specific step
   or decision point.

2. **Identify the correct action**: What should have been done instead.

3. **Analyze root cause**:
   - **Missing instruction**: No rule covers this scenario.
   - **Ambiguous instruction**: Instruction is vague or multi-interpretable.
   - **Ignored instruction**: Instruction exists and is clear, but wasn't followed.
   - **Emergent gap**: Process works for known scenarios but not this edge case.

4. **Propose a fix**: New instruction, clarified wording, stronger emphasis,
   or a TODO for future work.

5. **Write reflection**: Create `clasi/reflections/YYYY-MM-DD-slug.md`:

   ```yaml
   ---
   date: YYYY-MM-DD
   sprint: NNN (if active)
   category: missing-instruction | ambiguous-instruction | ignored-instruction | emergent-gap
   ---
   ```

   Sections: What Happened, What Should Have Happened, Root Cause, Proposed Fix.

6. **Create TODO if needed**: If the fix requires code/process changes.

## Output

- Reflection document in `clasi/reflections/`
- Optional TODO file
- Acknowledgment to the stakeholder
