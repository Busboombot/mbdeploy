---
name: systematic-debugging
description: Structured four-phase debugging protocol with attempt cap and escalation
---

# Systematic Debugging Skill

A structured debugging protocol that replaces ad hoc fix attempts.
When something breaks, follow this protocol instead of making rapid
guesses.

## Trigger Conditions

- A test that was passing starts failing
- An implementation fails its acceptance criteria
- Two consecutive failed fix attempts

## The Four Phases

### Phase 1: Evidence Gathering

Collect all evidence before forming any hypothesis. Do not change code.

1. Read the error output carefully. Copy exact error messages and stack
   traces.
2. Reproduce the issue reliably.
3. Identify the exact trigger — smallest reproduction case.
4. Review recent changes (`git log`, `git diff`).
5. Record the evidence.

### Phase 2: Pattern Analysis

Analyze evidence to understand the failure pattern. Still no code changes.

1. Compare working vs. broken states.
2. Identify what changed since it last worked.
3. Narrow the scope — simplify reproduction case.
4. Look for patterns: type error, missing import, state mutation,
   resource exhaustion, config difference.

### Phase 3: Hypothesis Testing

Form and test hypotheses systematically.

1. Form a specific hypothesis: "The failure occurs because X, and if I
   change Y, the test will pass."
2. Design a test for the hypothesis before making changes.
3. Make the minimal change to test it. Run the failing test.
4. Record the result — confirmed or refuted.
5. If refuted, form a new hypothesis using the new evidence.

### Phase 4: Root Cause Fix

Once a hypothesis is confirmed:

1. Fix the root cause, not the symptom.
2. Verify the fix — run the originally failing test.
3. Check for regressions — run the full test suite.
4. Review the fix — is it the right fix or a workaround?

## Three-Attempt Cap

After three failed fix attempts, STOP.

1. Revert any partial or broken changes.
2. Document what was tried (hypothesis, change, expected result, actual
   result for each attempt).
3. Escalate with: original error, evidence, pattern analysis, three
   hypotheses and results, recommendation.
4. Wait for guidance.

## Audit Trail

Every debugging session must produce a written record:
- Evidence collected
- Hypotheses formed
- Test results for each hypothesis
- Resolution or escalation

If working within a ticket, add a `## Debug Log` section to the ticket
plan file.
