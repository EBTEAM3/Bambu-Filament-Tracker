# Migrations

## Approach

The project uses application-driven migrations tracked in `schema_migrations`.
Each migration is identified by a version key and executed exactly once.

## Lifecycle

1. Schema bootstrap (`core/db.py`) creates baseline tables and additive compatibility columns.
2. Runtime migration runner (`FilamentTracker._apply_schema_migrations`) checks `schema_migrations`.
3. New migration functions run in-order and are marked applied.

## Current Domain Foundation Migration

Version: `20260316_0001_domain_foundation`

Repository: `MigrationRepository.migrate_domain_foundation()`

What it does:

- scans legacy rows from `spools`
- creates or reuses `filament_products` by brand/material/color/finish
- creates `spool_instances` preserving:
  - `legacy_tray_uuid`
  - RFID identity where present
  - AMS location metadata
  - remaining values and archival state

## Idempotency Rules

- migration checks if a spool with same `legacy_tray_uuid` already exists
- existing rows are skipped
- migration version marker prevents re-running the same migration

## Compatibility Guarantees

Migrations are designed so that:

- legacy UI keeps working against `spools`
- new APIs can rely on domain tables
- shared identity is available for cross-model lookups

## Adding New Migrations

When adding a migration:

1. create a new version id with chronological ordering
2. implement migration in repository/service layer (avoid route-level SQL)
3. make migration idempotent where feasible
4. append version + function to migration list in app runtime
5. document data impacts in this file and PR notes
