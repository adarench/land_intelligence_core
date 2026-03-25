# Zoning Representation: Graded Usability and Translation Layer

Last updated: 2026-03-19
Source files:
- `bedrock/services/zoning_layout_translation.py`
- `bedrock/services/layout_service.py`

## Approved Graded Usability Model

Usability classes (`UsabilityClass`):
1. `layout_safe`
- Returned when required zoning fields are present and no derived/degraded fields are needed.

2. `partially_usable`
- Returned when zoning is made layout-usable by deterministic derivation/defaulting of specific optional fields.
- Current degraded fields used by translator:
  - `min_frontage_ft` (derived)
  - `road_right_of_way_ft` (defaulted to `32.0`)

3. `non_usable`
- Returned when required constraints are missing/invalid or ambiguous.
- Returns structured `issues` list with:
  - `code`
  - `field`
  - `message`

## Translation-Layer Behavior

Input:
- `Parcel`
- `ZoningRules` (or dict validated into `ZoningRules`)

Required checks:
- `district` must exist
- `parcel_id` must match `Parcel.parcel_id`
- Required numeric fields must resolve to finite `>0`:
  - `min_lot_size_sqft`
  - `max_units_per_acre`
  - `setbacks.front`
  - `setbacks.side`
  - `setbacks.rear`

Conflict handling:
- If direct value and standards-derived value conflict, translator emits `ambiguous_zoning_value` and returns `non_usable`.

Output:
- `LayoutZoningTranslationResult` with:
  - `usability_class`
  - translated `zoning` (or `None` if non-usable)
  - `degraded_fields`
  - `issues`

Layout integration behavior:
- Layout service calls translator before candidate generation.
- If translator returns `non_usable`, layout raises `LayoutSearchError(code="non_usable_zoning")`.
- Source: `bedrock/services/layout_service.py`

## Operator Note

The current PO-2 gate artifact does not expose aggregated counts by `usability_class` or `degraded_fields`; this remains a reporting gap for representation-phase observability.
