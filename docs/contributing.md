# Contributing

## Goal

Contributions should improve maintainability and correctness while preserving user-facing compatibility for existing installations.

## Preferred PR Shape

- One topic per PR (refactor, feature, migration, docs).
- Small, reviewable commits using Conventional Commits.
- Include rationale for compatibility-sensitive changes in PR description.

Example commit scopes:

- `refactor(runtime): split main startup orchestration`
- `refactor(sync): extract AMS tray processing helpers`
- `docs(architecture): document layered backend responsibilities`

## Code Expectations

- Keep functions focused and short where feasible.
- Avoid mixing SQL, HTTP contract logic, and domain decisions in one function.
- Preserve existing API contracts unless versioning/deprecation is explicitly planned.
- Document non-obvious fallback behavior and precedence rules.

## Compatibility Checklist

Before opening a PR, confirm:

- legacy spool endpoints still respond as expected
- non-RFID spool tracking remains deterministic
- migrations are idempotent and version-registered
- auth and CSRF protections are not bypassed on write routes

## Documentation Requirements

Update docs when behavior changes:

- architecture: module boundaries and wiring
- data model: new/changed entities
- migrations: new schema versions
- auth: route protection or token/session logic
- calibration/AMS docs: precedence and synchronization behavior

## Review Notes Template

Recommended PR summary structure:

1. Problem addressed
2. Design decision and trade-offs
3. Compatibility impact
4. Migration and rollout notes
5. Manual verification performed

## Out of Scope for Routine PRs

- large API removals without deprecation path
- schema rewrites without migration strategy
- broad formatting-only churn unrelated to behavior
