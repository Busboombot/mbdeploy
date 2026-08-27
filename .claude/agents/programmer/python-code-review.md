---
name: python-code-review
description: Performs comprehensive code reviews of Python code checking for quality, PEP 8 compliance, security, and performance
---

# Python Code Review Skill

This skill performs comprehensive code reviews of Python code using the Python Expert agent.

## Description

Conducts thorough code reviews of Python files or modules, checking for:
- Code quality and readability
- PEP 8 compliance
- Best practices and design patterns
- Security vulnerabilities
- Performance issues
- Documentation completeness
- Type hints usage

## Agent Used

**python-expert** - Leverages the Python Expert agent's deep knowledge of Python best practices.

## Usage

To use this skill, invoke it with a Python file or directory path:

```
Review the Python code in src/utils/helpers.py
```

Or for a broader review:

```
Perform a code review of all Python files in the src/ directory
```

## Process

1. **Analyze Structure**: Examine the overall code organization
2. **Check Style**: Verify PEP 8 compliance and coding conventions
3. **Review Logic**: Assess algorithms, data structures, and design patterns
4. **Security Scan**: Look for common security vulnerabilities
5. **Performance Check**: Identify potential performance bottlenecks
6. **Documentation**: Evaluate docstrings and comments
7. **Generate Report**: Provide actionable feedback and recommendations

## Output

The skill produces a structured review report containing:
- Summary of findings
- List of issues organized by severity (critical, high, medium, low)
- Specific recommendations for improvements
- Code examples where applicable
- Best practice references

## Example

**Input:**
```
Review src/data_processor.py
```

**Output:**
```
# Code Review Report: src/data_processor.py

## Summary
Overall code quality: Good
Issues found: 5 (0 critical, 1 high, 2 medium, 2 low)

## Issues

### High Priority
1. Missing input validation on line 45
   - Risk: Potential security vulnerability
   - Recommendation: Add validation for user input

### Medium Priority
2. Function `process_data` exceeds recommended length (85 lines)
   - Recommendation: Refactor into smaller functions
3. Missing type hints on function parameters
   - Recommendation: Add type hints for better clarity

### Low Priority
4. Docstring missing for helper function `_normalize`
5. Variable name `tmp` is not descriptive on line 67

## Recommendations
- Add input validation using validators or custom checks
- Consider breaking down large functions
- Add comprehensive type hints throughout
- Improve documentation coverage
```

## Benefits

- **Consistency**: Ensures code follows established Python conventions
- **Quality**: Catches issues before they reach production
- **Learning**: Provides educational feedback for developers
- **Efficiency**: Automates time-consuming review tasks
