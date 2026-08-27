---
name: tdd-cycle
description: Optional red-green-refactor TDD workflow for implementation — available when the agent or stakeholder chooses test-driven development
---

# TDD Cycle Skill

This skill defines the test-driven development (TDD) workflow using the
red-green-refactor cycle. **TDD is optional, not mandatory.** It is an
available approach that the agent or stakeholder can choose when it fits
the work being done. The default implement-then-test approach remains
valid for all work.

## When to Use TDD

TDD is most valuable when:

- **Well-defined interfaces** — You know the inputs and outputs before
  writing the implementation. TDD lets you encode the contract as tests
  first.
- **Bug fixes** — Write a test that reproduces the bug, then fix it.
  This guarantees the bug is actually fixed and prevents regressions.
- **Complex logic** — Algorithms, state machines, parsers, or anything
  where edge cases matter. Writing tests first forces you to think
  through the edge cases before coding.
- **Refactoring with confidence** — When restructuring existing code,
  write characterization tests first to lock in current behavior, then
  refactor while keeping tests green.

## When TDD May Not Fit

TDD adds overhead that is not always justified. Implement-then-test is
fine for:

- **Exploratory spikes** — You are investigating feasibility, not
  building production code. Write tests after the spike clarifies the
  approach.
- **Configuration changes** — Updating config files, environment
  variables, or deployment settings. These are better verified by
  running the system.
- **UI layout and styling** — Visual correctness is hard to capture in
  unit tests. Use visual review and snapshot tests after implementation.
- **Documentation updates** — No code behavior to test-drive.
- **Simple glue code** — Wiring that connects existing tested components
  with no logic of its own.

## The Red-Green-Refactor Cycle

When TDD is chosen, follow these seven steps for each unit of behavior.
Each step is a discrete action the agent must perform and report on.

### Step 1: Red — Write a Failing Test

Write a test that describes the desired behavior. The test must be
specific: it calls the function or method that does not yet exist (or
does not yet handle this case) and asserts the expected outcome.

- Write exactly one test for one behavior.
- The test should be clear enough that someone reading it understands
  what the code is supposed to do.
- Do not write the production code yet.

### Step 2: Confirm Red — Watch It Fail

Run the test suite and confirm the new test fails.

- **Record the failure message.** Copy the exact error output. This
  proves the test is actually testing something and establishes the
  baseline.
- The failure should be for the expected reason (e.g., `NameError`
  because the function does not exist, or `AssertionError` because the
  return value is wrong). If the failure is for an unexpected reason,
  fix the test first.
- **This step is mandatory within the cycle.** If you did not watch it
  fail first, you do not know your test works. A test you have never
  seen fail tells you nothing.

**Handling unexpected passes:** If the test passes when you expected it
to fail, STOP. Do not proceed to the green phase. Investigate:

1. Does the feature already exist? If so, you may not need to write
   new code — verify the existing behavior is correct and move on.
2. Is the test wrong? Check that the assertion actually tests what you
   intended. A test that passes trivially (e.g., asserting a default
   value) is not useful.
3. Is the test hitting the wrong code path? Verify the test is
   exercising the code you think it is.

Resolve the unexpected pass before continuing the cycle.

### Step 3: Green — Write Minimal Code

Write the minimum production code needed to make the failing test pass.

- Do not write more code than the test requires.
- Do not add features, handle edge cases, or refactor yet.
- The goal is to go from red to green as quickly as possible.
- It is acceptable for the code to be ugly, hardcoded, or naive at
  this point.

### Step 4: Confirm Green — Watch It Pass

Run the test suite and confirm:

1. The new test passes.
2. All existing tests still pass (no regressions).

If any test fails, fix the issue before proceeding. Do not move to
the commit step with failing tests.

### Step 5: Commit — Save at Green (Before Refactoring)

Once all tests pass, **commit immediately** — before starting any
refactoring. This captures the minimal working implementation as a
known-good snapshot:

```
feat: implement <behavior> (#NNN, sprint NNN)
```

This commit is your safety net: if a subsequent refactor goes wrong,
you can revert to this known-good state without losing the working
implementation.

See `instructions/git-workflow.md` § Commit Timing, rule 4.

### Commit Points Summary

| Phase               | Step | Commit? |
|---------------------|------|---------|
| Red (failing test)  | 1-2  | No      |
| Green (make pass)   | 3-4  | **Yes** — commit before refactoring (step 5) |
| Refactor (clean up) | 6    | **Yes** — commit after refactoring (step 7) |

Each commit represents a known-good test state per
`instructions/git-workflow.md` § Commit Timing, rule 5.

## Integration with Ticket Workflow

This skill is invoked during the implementation phase (step 4) of the
`execute-ticket` skill. It does not replace the existing test
requirements — tests are still required regardless of whether TDD was
used.

When using TDD within a ticket:

1. Read the ticket's acceptance criteria and testing section.
2. For each piece of functionality, run one red-green-refactor cycle.
3. Commit after each green phase (step 5) and after each refactor (step 7).
4. After all cycles are complete, run your ticket's scoped tests (the
   modules you touched) in the foreground to verify no regressions --
   never background this step. The full suite runs exactly once per
   sprint, inside `close_sprint` itself (031/008), not per ticket.
5. Continue with the remaining execute-ticket steps (code review,
   documentation, completion).

## Summary

TDD is a tool, not a rule. Use it when it helps you think through the
design before coding. Skip it when it would slow you down without
adding value. The key discipline is: **if you choose TDD, follow the
full cycle** — do not skip the red step, do not skip confirming
failure, do not write more code than the test demands.
