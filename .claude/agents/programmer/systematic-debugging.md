---
name: systematic-debugging
description: Structured four-phase debugging protocol with attempt cap and escalation — replaces ad hoc fix attempts
---

# Systematic Debugging Skill

This skill defines a structured debugging protocol that replaces ad hoc
speculative fix attempts. When something breaks, follow this protocol
instead of making rapid guesses. The protocol ensures that debugging is
methodical, documented, and capped to prevent indefinite thrashing.

## Trigger Conditions

Invoke this skill when any of the following occur:

- **A test that was passing starts failing** — something you changed
  broke existing behavior.
- **An implementation attempt fails its acceptance criteria** — the code
  does not do what the ticket requires.
- **Two consecutive failed fix attempts** — you have tried to fix
  something twice and it is still broken. Stop guessing and switch to
  this protocol.

Do not invoke this skill for initial test failures during normal TDD
(where red is expected). This is for unexpected failures and situations
where simple diagnosis has not worked.

## The Four Phases

### Phase 1: Evidence Gathering

Collect all available evidence before forming any hypothesis. Do not
change any code during this phase.

1. **Read the error output carefully.** Copy the exact error message,
   stack trace, and any relevant log output. Do not paraphrase — exact
   text matters.
2. **Reproduce the issue.** Run the failing test or operation and
   confirm you can trigger the failure reliably. If the failure is
   intermittent, note the conditions under which it does and does not
   occur.
3. **Identify the exact trigger.** What specific input, configuration,
   or sequence of operations causes the failure? Narrow it down to the
   smallest reproduction case you can find.
4. **Review recent changes.** What changed since this last worked? Check
   git log, recent commits, and any files modified in this session.
5. **Record the evidence.** Write down what you found — error messages,
   reproduction steps, relevant code sections, and recent changes. This
   record is the foundation for everything that follows.

### Phase 2: Pattern Analysis

Analyze the evidence to understand the failure pattern. Still do not
change code.

1. **Compare working vs. broken states.** If you can identify a known
   good state (e.g., a previous commit, a different input), compare
   the two. What is different?
2. **Identify what changed.** If the code was working before, isolate
   the change that broke it. Use git diff, git bisect, or manual
   comparison.
3. **Narrow the scope.** Reduce the reproduction case to the minimum
   possible. Remove unrelated code, simplify inputs, isolate the
   failing component.
4. **Look for patterns.** Does the failure match a known category?
   - Type error / argument mismatch
   - Missing or wrong import
   - State mutation / ordering dependency
   - Resource exhaustion or timeout
   - Configuration / environment difference

### Phase 3: Hypothesis Testing

Now form and test hypotheses systematically. Each hypothesis is an
explicit statement that can be confirmed or refuted.

1. **Form a specific hypothesis.** State it clearly: "The failure
   occurs because X, and if I change Y, the test will pass." A vague
   hypothesis ("something is wrong with the data") is not actionable.
2. **Design a test for the hypothesis.** Before making any fix, decide
   how you will know if the hypothesis is correct. What observation
   would confirm it? What would refute it?
3. **Test the hypothesis.** Make the minimal change needed to test it.
   Run the failing test.
4. **Record the result.** Whether the hypothesis was confirmed or
   refuted, write it down. Include what you changed, what you expected,
   and what actually happened.
5. **If refuted, form a new hypothesis.** Do not retry the same
   approach. Use the new evidence from the failed test to inform your
   next hypothesis.

### Phase 4: Root Cause Fix

Once a hypothesis is confirmed and you understand the root cause:

1. **Fix the root cause, not the symptom.** If the test fails because
   of a wrong return value, do not patch the test — fix the code that
   produces the wrong value. Trace the problem to its origin.
2. **Verify the fix.** Run the originally failing test and confirm it
   passes.
3. **Check for regressions.** Run your ticket's scoped tests (the
   modules you touched), in the foreground -- never backgrounded. The
   full suite runs exactly once per sprint, inside `close_sprint`
   itself (031/008), not a per-ticket step. Confirm your fix did not
   break anything in scope.
4. **Review the fix.** Does it make sense? Is it the right fix, or a
   workaround that will cause problems later? If it feels like a hack,
   consider whether a better fix exists.

## Three-Attempt Cap

**After three failed fix attempts, you MUST stop.** Do not continue
making speculative changes. Three failed attempts means your mental
model of the problem is wrong, and more attempts are unlikely to help.

When the cap is reached:

1. **Stop changing code.** Revert any partial or broken changes from
   the failed attempts.
2. **Document what was tried.** For each attempt, record:
   - The hypothesis that motivated the attempt
   - What change was made
   - What the expected result was
   - What the actual result was
3. **Escalate to the stakeholder.** Present:
   - The original error and reproduction steps
   - The evidence collected (Phase 1)
   - The pattern analysis (Phase 2)
   - The three hypotheses tested and their results (Phase 3)
   - A recommendation: either a different approach to investigate, or
     a suggestion that the problem may require architectural changes
4. **Wait for guidance.** Do not continue fixing until the stakeholder
   responds.

## Audit Trail

Every debugging session must produce a written record. This prevents
repeating the same failed approach across sessions and provides context
for anyone who picks up the problem later.

**What to record:**

- **Evidence collected** — Error messages, stack traces, reproduction
  steps, relevant code sections.
- **Hypotheses formed** — Each hypothesis, stated clearly.
- **Test results** — For each hypothesis: what was changed, what was
  expected, what happened.
- **Resolution** — What the root cause was and how it was fixed, or
  that the issue was escalated.

**Where to record it:**

- If working within a ticket, add a `## Debug Log` section to the
  ticket plan file.
- If the debugging is outside a ticket context, create a note in the
  sprint directory or document the findings in the commit message.

The record does not need to be long or formal — but it must exist. A
few bullet points per phase is sufficient.

## Integration with Other Skills

- **execute-ticket**: The `execute-ticket` skill references this skill
  in its Error Recovery section. When test failures are not resolved by
  simple diagnosis, switch to this protocol.
- **tdd-cycle**: During TDD, unexpected failures in the "confirm green"
  step (Step 4) may trigger this skill. Expected failures during the
  "confirm red" step (Step 2) do not trigger it.
- **self-reflect**: If the three-attempt cap is reached and the root
  cause turns out to be a process gap, consider using `self-reflect`
  to capture the learning.
