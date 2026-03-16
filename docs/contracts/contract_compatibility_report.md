# Contract Compatibility Report

## Scope

This report records compatibility coverage between the governance-approved Bedrock contracts and the currently implemented APIs and adapters.

Canonical contracts:

- `Parcel`
- `ZoningRules`
- `LayoutResult`
- `FeasibilityResult`

## Canonical decisions

### Parcel

- canonical area field is `area_sqft`
- `area` remains an accepted input alias

### ZoningRules

- canonical zoning handoff is parcel-scoped `ZoningRules`
- older `ZoningDistrict + DevelopmentStandard[]` payloads remain a supported compatibility shape through validator normalization

### LayoutResult

- canonical unit field is `unit_count`
- legacy layout payloads using `units` or `lot_count` remain accepted
- `parcel_id` is mandatory in the canonical model and is injected by `build_layout_result(...)` when omitted upstream

### FeasibilityResult

- canonical capacity field is `units`
- compatibility aliases `feasible_units` and `max_units` remain accepted
- financial projections are embedded directly in the canonical contract
- the public feasibility API currently wraps canonical results in a response envelope

## Compatibility matrix

| Source | Current shape | Canonical outcome | Status |
| --- | --- | --- | --- |
| `bedrock/contracts/parcel.py` consumers | `Parcel(area)` | `Parcel(area_sqft)` with property alias `area` | Compatible |
| `bedrock/engines/zoning_engine.py` | `ZoningDistrict + DevelopmentStandard[]` | `ZoningRules` via `build_zoning_rules(...)` | Compatible with adapter normalization |
| `bedrock/services/zoning_service.py` | `ZoningDistrictLookupResult` or local shim `ZoningLookupResult` | not yet canonical public output | Transitional only |
| `bedrock/services/layout_service.py` | local `LayoutResult(units, road_length, ...)` | canonical `LayoutResult(unit_count, road_length_ft, parcel_id, ...)` via conversion helpers and validators | Compatible with shim |
| `bedrock/pipelines/parcel_feasibility_pipeline.py` | `FeasibilityResult(max_units, ...)` | `FeasibilityResult(units, ...)` via alias compatibility | Compatible |
| `bedrock/api/feasibility_api.py` | `FeasibilityEvaluationResponse(results=[FeasibilityResult])` | wrapper around canonical results | Compatible with API-layer wrapper |

## Validation rules for Bedrock services

### `bedrock.engines.parcel_engine.get_parcel`

- must emit `Parcel`
- must normalize area into `area_sqft`
- must reject non-polygon parcel geometry

### `bedrock.engines.zoning_engine.get_zoning`

- input must validate as `Parcel`
- output must be normalized to parcel-scoped `ZoningRules`
- standards and scalar zoning fields must agree after normalization

### `bedrock.engines.parcel_engine.generate_layout`

- canonical input zoning contract is `ZoningRules`
- canonical output is `LayoutResult`
- adapter must enrich missing `parcel_id` for legacy runtime output

### `bedrock.pipelines.parcel_feasibility_pipeline.score_layout`

- canonical output is `FeasibilityResult`
- `layout_id` linkage must be preserved exactly
- financial projections and normalized risk metrics must remain deterministic

## Known gaps

- the public zoning API still returns district-only lookup output rather than canonical `ZoningRules`
- the public layout API still uses milestone-local request and response models from `bedrock.services.layout_service`
- the legacy GIS layout service model does not carry `parcel_id`; Bedrock must continue enriching that field
- the public Bedrock feasibility API returns a response wrapper rather than a bare `FeasibilityResult`
