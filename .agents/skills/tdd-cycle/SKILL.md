---
name: tdd-cycle
description: Optional red-green-refactor TDD workflow for implementation
---

# TDD Cycle Skill

This skill defines the test-driven development (TDD) workflow using the
red-green-refactor cycle. **TDD is optional, not mandatory.** Use it
when it fits the work being done.

## When to Use TDD

- **Well-defined interfaces** — You know inputs and outputs before coding
- **Bug fixes** — Write a test that reproduces the bug, then fix it
- **Complex logic** — Algorithms, state machines, parsers, edge cases
- **Refactoring** — Write characterization tests first, then refactor

## When TDD May Not Fit

- Exploratory spikes
- Configuration changes
- UI layout and styling
- Documentation updates
- Simple glue code

## The Red-Green-Refactor Cycle

### Step 1: Red — Write a Failing Test

Write a test that describes the desired behavior. The test must call
the function/method and assert the expected outcome. Do not write the
production code yet.

### Step 2: Confirm Red — Watch It Fail

Run the test suite and confirm the new test fails. Record the failure
message. The failure should be for the expected reason. If unexpected,
fix the test first.

### Step 3: Green — Write Minimal Code

Write the minimum production code to make the failing test pass.
Do not add features or handle edge cases yet.

### Step 4: Confirm Green — Watch It Pass

Run the test suite. Confirm the new test passes and all existing
tests still pass.

### Step 5: Commit — Save at Green

Commit immediately before refactoring. This is your safety net.

### Step 6: Refactor

Clean up the code while keeping all tests green. Improve naming,
extract methods, remove duplication.

### Step 7: Commit — Save After Refactor

Commit the refactored code.

## Integration with Ticket Workflow

When using TDD within a ticket:
1. Read the ticket's acceptance criteria and testing section.
2. For each piece of functionality, run one red-green-refactor cycle.
3. Commit after each green phase and after each refactor.
4. After all cycles, run the full test suite.

TDD is a tool, not a rule. Use it when it helps you think through the
design. The key discipline: **if you choose TDD, follow the full cycle**.
