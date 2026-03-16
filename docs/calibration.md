# Effective Calibration Resolution

## Purpose

Calibration can exist at multiple scopes. The service returns one effective profile for a spool given a print context.

## Scope Priority

Applied lowest to highest priority:

1. `global_material`
2. `filament_product`
3. `spool_instance`

Higher layers override only non-null fields, preserving lower-layer defaults.

## Context Matching

Supported context qualifiers:

- `printer_model`
- `nozzle_diameter_mm`
- `plate_type`
- `layer_height_mm`
- `slicer_name`
- `slicer_profile`

Matching rule:

- profile is valid if each qualifier is either `NULL` or equals current context value

This allows broad defaults plus exact overrides without duplicating every field.

## API Surface

- `GET /api/spools/<id>/calibration`
- `GET /api/spools/<id>` includes effective calibration in detail payload

## Response Structure

Typical fields:

- `spool_id`, `filament_product_id`, `material`
- `context` (input qualifiers)
- `layers` (which profiles contributed)
- `effective` (merged numeric/string tuning values)

## Failure Contract

If spool does not exist:

- service returns `{"error": "spool_not_found"}`
- route maps this to HTTP `404`

## Operational Guidance

- put broad defaults in `global_material`
- use `filament_product` for SKU-level tuning
- use `spool_instance` only for exceptions
- avoid setting fields to null in high-priority layers unless intent is explicit inheritance
