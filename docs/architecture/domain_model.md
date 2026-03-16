# Bedrock Domain Model

## Parcel

Represents a normalized parcel record used across feasibility and optimization pipelines.

Fields:

- `parcel_id`
- `geometry`
- `area_sqft`
- `jurisdiction`
- `centroid`
- `bounding_box`
- `zoning_district`
- `utilities`
- `access_points`
- `topography`
- `existing_structures`

## Jurisdiction

Represents the governing planning entity for a parcel or zoning district.

Fields:

- `id`
- `name`
- `state`
- `county`
- `planning_authority`

## ZoningDistrict

Compatibility district identity model.

Fields:

- `id`
- `jurisdiction_id`
- `code`
- `description`

## DevelopmentStandard

Compatibility rule model used to normalize into canonical zoning output.

Fields:

- `id`
- `district_id`
- `standard_type`
- `value`
- `units`
- `conditions`
- `citation`

## ZoningRules

Canonical parcel-scoped zoning handoff.

Fields:

- `parcel_id`
- `district`
- `standards`
- `setbacks`
- `min_lot_size_sqft`
- `max_units_per_acre`
- `height_limit_ft`
- `lot_coverage_max`
- `min_frontage_ft`
- `road_right_of_way_ft`
- `allowed_uses`
- `citations`

## SubdivisionLayout

Canonical layout contract alias built from `LayoutResult`.

Fields:

- `layout_id`
- `parcel_id`
- `street_network`
- `lot_geometries`
- `lot_count`
- `open_space_area`
- `road_length`
- `utility_length`

## FeasibilityScenario

Structured development request evaluated against parcel and layout context.

Fields:

- `scenario_id`
- `parcel_id`
- `requested_units`
- `assumptions`
- `constraints`

## FeasibilityResult

Canonical output for deterministic feasibility evaluation.

Fields:

- `scenario_id`
- `layout_id`
- `parcel_id`
- `units`
- `projected_revenue`
- `projected_cost`
- `projected_profit`
- `ROI`
- `risk_score`
- `constraint_violations`
- `confidence`
- `financial_summary`
- `explanation`

## ScenarioEvaluation

Ranked feasibility summary across multiple layouts.

Fields:

- `parcel_id`
- `layout_count`
- `best_layout_id`
- `best_roi`
- `best_profit`
- `best_units`
- `layouts_ranked`

## Modeling Rules

- all canonical contracts are defined with Pydantic and strict validation
- engine outputs should be normalized into canonical contracts at adapter boundaries
- public milestone APIs may still expose compatibility wrappers or shim models
