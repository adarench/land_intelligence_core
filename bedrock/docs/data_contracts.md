# Canonical Data Contracts (Current Implementation)

Last updated: 2026-03-17
Authoritative source files:
- `bedrock/contracts/parcel.py`
- `bedrock/contracts/zoning_rules.py`
- `bedrock/contracts/layout_result.py`
- `bedrock/contracts/feasibility_result.py`
- `bedrock/contracts/validators.py`

## Contract Governance Rules

- All contract models inherit `BedrockModel` (`extra="forbid"`), so unknown fields are rejected.
- Canonical cross-stage link invariants are enforced in `validate_feasibility_pipeline_contracts(...)`:
  - `ZoningRules.parcel_id == Parcel.parcel_id`
  - `LayoutResult.parcel_id == Parcel.parcel_id`
  - `FeasibilityResult.parcel_id` (if present) must match `Parcel.parcel_id`
  - `FeasibilityResult.layout_id == LayoutResult.layout_id`
- Layout-safe zoning minimum requirements are enforced before layout stage:
  - `district`, `min_lot_size_sqft`, `max_units_per_acre`, `setbacks.front|side|rear`

## Parcel (`schema_name=Parcel`, `schema_version=1.0.0`)

### Required fields
- `parcel_id: str`
- `geometry: GeoJSON Polygon|MultiPolygon`
- `jurisdiction: str`
- `area_sqft: float (>0)`

### Optional fields
- `centroid: [x, y]`
- `bounding_box: [min_x, min_y, max_x, max_y]`
- `land_use: str`
- `slope_percent: float (>=0)`
- `flood_zone: str`
- `zoning_district: str`
- `utilities: list[str]`
- `access_points: list[Geometry]`
- `topography: dict`
- `existing_structures: list[dict]`
- `metadata: EngineMetadata`

### Compatibility and aliases
- Accepts `area` as input alias for `area_sqft`.
- `topography.slope_percent` and root `slope_percent` are synchronized by model validator.
- Compatibility property: `area` returns `area_sqft`.

## ZoningRules (`schema_name=ZoningRules`, `schema_version=1.0.0`)

### Required fields
- `parcel_id: str`
- `district: str` (aliases: `code`, `zoning_district`)

### Key layout-critical fields
- `min_lot_size_sqft: float|None`
- `max_units_per_acre: float|None`
- `setbacks.front|side|rear: float|None`

### Additional fields
- `jurisdiction: str|None`
- `district_id: str|None`
- `description: str|None`
- `overlays: list[str]`
- `standards: list[DevelopmentStandard]`
- `height_limit_ft: float|None`
- `lot_coverage_max: float|None`
- `min_frontage_ft: float|None`
- `road_right_of_way_ft: float|None`
- `allowed_uses: list[str]`
- `citations: list[str]`
- `metadata: EngineMetadata|None`

### Compatibility and aliases
- Height aliases accepted inbound: `height_limit`, `max_height`, `max_building_height_ft`.
- Coverage aliases accepted inbound: `lot_coverage_limit`, `max_lot_coverage`.
- Frontage alias accepted inbound: `min_lot_width_ft`.
- Use alias accepted inbound: `allowed_use_types`.
- Overlay alias accepted inbound: `overlay` -> normalized to `overlays`.
- Contract auto-derives missing scalar fields from `standards` when possible and upserts derived standards.
- Compatibility property: `code` returns `district`.

## LayoutResult (`schema_name=LayoutResult`, `schema_version=1.0.0`)

### Required fields
- `layout_id: str`
- `parcel_id: str`
- `unit_count: int (>=0)`

### Main fields
- `road_length_ft: float (>=0)`
- `lot_geometries: list[Geometry]`
- `road_geometries: list[Geometry]`
- `open_space_area_sqft: float (>=0)`
- `utility_length_ft: float (>=0)`
- `score: float|None`
- `buildable_area_sqft: float|None`
- `metadata: EngineMetadata|None`

### Compatibility and aliases
- Input aliases: `units`/`lot_count` -> `unit_count`.
- Input alias: `street_network` -> `road_geometries`.
- Input alias: `road_length` -> `road_length_ft`.
- Input alias: `open_space_area` -> `open_space_area_sqft`.
- Input alias: `utility_length` -> `utility_length_ft`.
- Compatibility properties: `lot_count`, `units`, `road_length`, `street_network`, `open_space_area`, `utility_length`.
- `SubdivisionLayout` is currently an alias of `LayoutResult` (`bedrock/contracts/layout.py`).

## FeasibilityResult (`schema_name=FeasibilityResult`, `schema_version=1.0.0`)

### Required fields (validated)
- `scenario_id: str`
- `layout_id: str`
- `units: int (>=0)`
- `risk_score: float [0,1]`
- `confidence: float [0,1]`
- Financial minimum set:
  - `projected_revenue`
  - `projected_cost`
  - `projected_profit`

### Main financial fields
- `estimated_home_price`
- `construction_cost_per_home`
- `development_cost_total`
- `projected_revenue`
- `projected_cost`
- `projected_profit`
- `ROI` (`roi` alias accepted inbound)
- `profit_margin`
- `revenue_per_unit`
- `cost_per_unit`

### Additional fields
- `parcel_id: str|None`
- `rank: int|None`
- `requested_units: int|None`
- `constraint_violations: list[str]`
- `status: str` (auto-derived to `feasible` or `constrained` if missing/unknown)
- `financial_summary: dict`
- `explanation: FeasibilityExplanation|None`
- `assumptions: dict`
- `metadata: EngineMetadata|None`

### Compatibility and aliases
- Units aliases inbound: `feasible_units`, `max_units`.
- Cost/revenue/profit aliases inbound: `cost_per_home`, `development_cost`, `revenue`, `total_cost`, `profit`.
- `financial_summary` values can backfill top-level fields during validation.
- Compatibility properties: `feasible_units`, `max_units`, `roi`.

## Backward Compatibility Concerns

1. Strict `extra="forbid"` can break legacy clients sending unknown fields.
2. Canonical outbound naming should avoid deprecated keys (`max_height`, `max_lot_coverage`, singular `overlay`).
3. Layout/feasibility still use compatibility aliases and properties; clients should migrate to canonical names.
4. `SubdivisionLayout` is not a distinct schema yet, only a type alias to `LayoutResult`.
5. Import-path compatibility (`contracts.*` vs `bedrock.contracts.*`) is still being stabilized and may affect runtime packaging contexts.
