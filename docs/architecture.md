# Architecture

## Problem Scope

The project tracks filament usage from Bambu AMS data and exposes:

- real-time inventory visibility
- legacy spool workflows used by current UI
- foundation domain for SKU, spool instances, and calibration history

The architecture is intentionally incremental: old and new models coexist to preserve compatibility while enabling upstream evolution.

## Backend Layout

- `app.py`: runtime bootstrap, app factory, blueprint wiring, live/test startup
- `filament_tracker.py`: compatibility wrapper (`from filament_tracker import FilamentTracker`)
- `core/`: low-level technical primitives
  - `config.py`: runtime/environment configuration
  - `db.py`: schema bootstrap and DB connection
  - `security.py`: headers + CSRF helpers
  - `errors.py`: API error response helper
- `repositories/`: SQL-only data access
- `services/`: business/domain orchestration
- `api/`: Flask blueprints grouped by domain and compatibility
- `models/`: shared enums, DTO serializers, validation utilities

## Runtime Flow

1. `main()` in `app.py` parses args and configures logging.
2. Test mode:
   - initializes tracker in isolated DB path
   - seeds deterministic mock data
   - serves Flask directly
3. Live mode:
   - resolves `config.py`
   - validates Bambu credentials
   - creates MQTT client and tracker
   - registers AMS callback (`mqtt_client.on_ams_data(tracker.update_ams_data)`)
   - optionally attaches FCM bridge
   - starts web server thread and blocks on MQTT loop

## App Factory and Request Security

`FilamentTracker._create_flask_app()` centralizes:

- Flask app setup (templates/static, session settings)
- hardening headers (`CSP`, `X-Content-Type-Options`, `X-Frame-Options`)
- authentication gate middleware for UI/API
- blueprint registration order

Write authorization uses `require_write_auth()`:

- auth enabled: requires authenticated session + CSRF token
- API key remains supported for automation
- when auth is enabled, valid session is enough even without API key

## Compatibility Strategy

Compatibility is explicit, not accidental:

- legacy routes remain available (`/api/spools/...` tray UUID workflows)
- numeric spool IDs in legacy routes route into new `spool_instances`
- AMS sync writes both old and new persistence models
- migration stores `legacy_tray_uuid` for cross-model continuity

This strategy allows upstream maintainers to deprecate legacy paths gradually instead of via one disruptive cutover.
