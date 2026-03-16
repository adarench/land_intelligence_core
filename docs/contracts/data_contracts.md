# Feasibility Pipeline Data Contracts

## Purpose

This document is the authoritative contract reference for Bedrock pipeline data exchange.

Canonical contracts:

- `Parcel`
- `ZoningRules`
- `LayoutResult`
- `FeasibilityResult`

Contract authority:

- model definitions in `bedrock/contracts/*.py`
- registry and service rules in `bedrock/contracts/schema_registry.py`
- validation helpers in `bedrock/contracts/validators.py`

## Contract Lineage

```mermaid
flowchart LR
    P[Parcel] --> Z[ZoningRules]
    Z --> L[LayoutResult]
    L --> F[FeasibilityResult]
```

Cross-stage invariants enforced by `validate_feasibility_pipeline_contracts(...)`:

- `ZoningRules.parcel_id == Parcel.parcel_id`
- `LayoutResult.parcel_id == Parcel.parcel_id`
- `FeasibilityResult.parcel_id`, when present, matches `Parcel.parcel_id`
- `FeasibilityResult.layout_id == LayoutResult.layout_id`

## 1. Parcel

Defined in `bedrock/contracts/parcel.py`.

### Canonical shape

- `schema_name = "Parcel"`
- `schema_version = "1.0.0"`
- `parcel_id`
- `geometry`
- `jurisdiction`
- `area_sqft`
- optional metadata and parcel context fields

### Compatibility rules

- `area` is accepted as an input alias and canonicalized to `area_sqft`
- property alias `Parcel.area` remains available

## 2. ZoningRules

Defined in `bedrock/contracts/zoning_rules.py`.

### Canonical shape

- `schema_name = "ZoningRules"`
- `schema_version = "1.0.0"`
- `parcel_id`
- `jurisdiction`
- `district`
- `district_id`
- `description`
- `overlays`
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
- `metadata`

### Overlay implementation

Current overlay behavior comes from `zoning_data_scraper.services.zoning_overlay.lookup_zoning_district(...)`:

- zoning district is resolved from `normalized_zoning.json`
- overlay labels are resolved from `overlay_layers.geojson` when available
- overlay labels are deduplicated and stored in `ZoningRules.overlays`

This means overlays are now part of the implemented canonical zoning payload, not just a future extension.

### Rule normalization behavior

Current rule normalization comes from `zoning_data_scraper.services.rule_normalization.normalize_zoning_rules(...)` and `bedrock.contracts.validators.build_zoning_rules_from_lookup(...)`.

Normalization behavior includes:

- matching district rules by normalized district code or district name
- coercing numeric values from strings
- deriving scalar fields from rule records
- normalizing setbacks from flat or nested input fields
- converting lot coverage percentages into `[0, 1]` fractions where needed
- carrying overlays forward from the overlay lookup layer
- mapping `height_limit` to canonical `height_limit_ft`
- mapping `lot_coverage_limit` to canonical `lot_coverage_max`
- binding the output to the calling parcel via `parcel_id`

### Supported jurisdictions

Minimum milestone coverage is implemented for:

- Salt Lake City
- Lehi
- Draper

These jurisdictions are present both in the Bedrock-side geometry/jurisdiction logic and in the zoning dataset discovery inputs used by the overlay service.

### Compatibility rules

- `district` accepts aliases `district`, `code`, and `zoning_district`
- `overlay` is accepted and normalized into `overlays`
- scalar convenience fields may be backfilled from `standards`
- synthetic standards are created for canonical scalar fields when those scalar values exist

## 3. LayoutResult

Defined in `bedrock/contracts/layout_result.py`.

### Canonical shape

- `schema_name = "LayoutResult"`
- `schema_version = "1.0.0"`
- `layout_id`
- `parcel_id`
- `unit_count`
- `road_length_ft`
- `lot_geometries`
- `road_geometries`
- `open_space_area_sqft`
- `utility_length_ft`
- optional `score`
- optional `buildable_area_sqft`
- optional `metadata`

### LayoutResult alias rules

- `unit_count` accepts `unit_count`, `units`, `lot_count`
- `road_length_ft` accepts `road_length_ft`, `road_length`
- `road_geometries` accepts `road_geometries`, `street_network`
- `open_space_area_sqft` accepts `open_space_area_sqft`, `open_space_area`
- `utility_length_ft` accepts `utility_length_ft`, `utility_length`

Compatibility properties remain available:

- `lot_count`
- `units`
- `road_length`
- `street_network`
- `open_space_area`
- `utility_length`

### Adapter shim rules

`bedrock/contracts/validators.py::build_layout_result(...)` currently:

- injects `parcel_id` when omitted by upstream payloads
- normalizes legacy metadata shapes into canonical `EngineMetadata`
- validates the final canonical layout payload

## 4. FeasibilityResult

Defined in `bedrock/contracts/feasibility_result.py`.

### Canonical shape

- `scenario_id`
- `layout_id`
- optional `parcel_id`
- `units`
- financial projection fields
- `risk_score`
- `constraint_violations`
- `confidence`
- `status`
- `financial_summary`
- optional `explanation`

### Compatibility rules

- canonical capacity field is `units`
- `units` accepts aliases `units`, `feasible_units`, and `max_units`
- financial aliases such as `roi`, `revenue`, `total_cost`, and `profit` remain accepted

## Known implementation notes

- public `POST /zoning/lookup` now returns canonical `ZoningRules`
- public `POST /layout/search` returns canonical `LayoutResult`
- public `POST /feasibility/evaluate` still returns a wrapper response containing canonical `FeasibilityResult` records
