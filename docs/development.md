# Development Guide

## Local Setup

1. Create `config/config.py` from `config.example.py`.
2. Install dependencies from `requirements.txt`.
3. Run in live mode with `python filament_tracker.py`.
4. Run in UI-only mode with `python filament_tracker.py --test`.

The project keeps `filament_tracker.py` as the stable entrypoint for compatibility, while `app.py` contains the modular runtime.

## Repository Layout

- `app.py`: runtime orchestration and Flask app factory
- `core/`: low-level primitives (db, config, security)
- `repositories/`: SQL access only
- `services/`: business logic and orchestration
- `api/`: route handlers and HTTP contracts
- `models/`: shared enums/DTO/validation
- `docs/`: maintainers documentation

## Development Conventions

- Keep route handlers thin; push DB work to repositories and flow logic to services.
- Preserve backward compatibility for legacy endpoints unless a migration plan is explicitly documented.
- Prefer small refactors that reduce function complexity through extraction, not behavior rewrites.
- Add docstrings for non-obvious rules (security decisions, fallback precedence, compatibility bridges).

## Configuration and Secrets

- Never commit real credentials in `config.py`.
- Prefer env-based auth bootstrap values for production (`SECRET_KEY`, `ADMIN_PASSWORD_HASH`).
- Treat Bambu access tokens as sensitive credentials.

## Database and Migrations

- Schema changes must be additive and idempotent when possible.
- Register new migrations in `MigrationRepository` with a unique version key.
- Keep migration SQL explicit and reversible-by-strategy (backup/export before destructive operations).

## Manual Verification Checklist

When changing backend behavior, validate at least:

- login/logout and CSRF-protected write endpoints
- AMS update path (RFID and non-RFID slot cases)
- legacy route compatibility (`/api/spools/...`)
- dashboard and calibration endpoints after schema-affecting changes

## Working With Compatibility Paths

The codebase currently supports legacy and new inventory domains in parallel.
If you modify spool synchronization, ensure:

- legacy `spools` remain updated
- `spool_instances` and presence history stay coherent
- numeric spool references in legacy routes keep resolving to new model IDs
