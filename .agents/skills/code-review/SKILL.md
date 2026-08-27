---
name: code-review
description: Two-phase code review — correctness against acceptance criteria, then quality against coding standards
---

# Code Review Skill

This skill reviews code changes through a two-phase process: first
correctness against acceptance criteria, then quality against coding
standards. It does not implement code or fix issues — it reports findings.

## Two-Phase Review Process

### Phase 1: Correctness

**Goal**: Verify the implementation satisfies every acceptance criterion.
Binary pass/fail per criterion.

1. Read the ticket to extract all acceptance criteria.
2. Read the ticket plan to understand the intended approach.
3. Read the changed files.
4. Evaluate each criterion: does the implementation satisfy it?
   PASS or FAIL. For failures, explain what is missing and where.
5. Check that tests exist for each criterion and actually pass.

**Phase 1 verdict**:
- **PASS**: Every acceptance criterion passes.
- **FAIL**: One or more fail. Stop here — do not proceed to Phase 2.

### Phase 2: Quality

**Goal**: Review against coding standards, security, architectural
consistency. Only reached when Phase 1 passes.

1. Review against coding standards: naming, error handling, patterns.
2. Review security: injection risks, hardcoded secrets, unsafe input.
3. Review architectural consistency with the sprint's architecture doc.
4. Review maintainability: naming, abstraction, complexity.
5. Rank each issue by severity:

| Severity | Definition | Action |
|----------|-----------|--------|
| **Critical** | Security vulnerability, data loss risk | Must fix |
| **Major** | Standards violation, missing error handling | Should fix |
| **Minor** | Style inconsistency, suboptimal naming | Fix if time permits |
| **Suggestion** | Improvement idea, not a defect | Consider later |

**Phase 2 verdict**:
- **PASS**: Zero critical or major issues.
- **FAIL**: One or more critical or major issues.

### Python-Specific Checks

When reviewing Python code, also check:
- PEP 8 compliance
- Type hints on public functions
- Proper use of `uv` tooling and `pyproject.toml`
- Pytest patterns and test organization
- Import ordering and naming conventions

## Output

- Phase 1 results: each criterion with PASS/FAIL
- Phase 2 results (if Phase 1 passed): each issue with severity
- Overall verdict: PASS or FAIL
