---
name: estimation-rubric
description: Hour-range estimates for common web application features and tasks based on industry data
---

# Web Application Development Estimation Rubric

Provides hour-range estimates for common web application features,
based on industry data, developer forum anecdotes, and agency estimation
guides. Use for bottom-up estimation: classify each ticket against the
appropriate category, apply the hour range, sum for total.

All estimates assume a single mid-level full-stack developer in a modern
framework. Adjust for experience and stack familiarity.

## Estimation Ground Rules

- Effective coding hours per day: 5-6 (of 8)
- Effective coding hours per week: 32 (of 40)
- Standard overhead multiplier: 1.2-1.5x
- QA/testing multiplier: +25-40% of dev time
- Risk buffer: +15-25%

## Categories

### Project Setup: 19-75 hours
Repo, dev environment, framework scaffolding, DB schema, CI/CD,
deployment, monitoring.

### Auth: 14-44 hours (typical system)
Email/password, OAuth, password reset, RBAC, session management.

### CRUD (per entity)
- Simple: 4-10 hours (single table, few fields)
- Medium: 8-20 hours (multi-field, 1-2 relationships)
- Complex: 16-40 hours (multiple relationships, file uploads, workflows)

### UI/UX: 16-40 hours (fit and finish pass)
Layout, navigation, dashboard, forms, data tables, responsive design.

### Feature Modules
Email: 2-8h, file upload: 2-12h, search: 4-12h, notifications: 4-12h,
CSV import/export: 2-12h, reporting: 8-32h, PDF: 4-16h, real-time: 8-24h,
calendar: 8-24h, payments: 8-24h, multi-tenancy: 16-40h, API: 8-32h.

### Integrations
Maps: 4-12h, Slack: 2-8h, external API: 4-16h, SSO: 16-40h.

### Testing: 15-60+ hours
Unit, integration, E2E, cross-browser, manual QA, bug fixing.

## Complexity Tiers

| Tier | Hours | Calendar (1 dev) |
|------|-------|-----------------|
| Simple | 200-500 | 2-3 months |
| Medium | 500-1,200 | 3-6 months |
| Complex | 1,200-2,500+ | 6-12+ months |

## How to Use

1. Inventory tickets/features
2. Classify each against rubric categories
3. Assign hour ranges (low/mid/high)
4. Sum estimates
5. Apply multipliers: +15% PM, +10% review, +30% testing, +20% risk
6. Convert to calendar: total / 32 hours per week
