# Layout Service

Last updated: 2026-03-17
Code: `bedrock/services/layout_service.py`, `bedrock/api/layout_api.py`

## Purpose

Generate zoning-compliant parcel subdivision layouts using the GIS runtime and return canonical `LayoutResult` output.

## Inputs

Service:
- `search_layout(parcel: Parcel, zoning: ZoningRules, max_candidates: int=50)`
- `search_subdivision_layout(parcel: Parcel, zoning: ZoningRules, max_candidates: int=50)`

API:
- `POST /layout/search` body:
  - `parcel: Parcel`
  - `zoning: ZoningRules`
  - `max_candidates: int` (1..250, default 50)

## Outputs

- Best-ranked `LayoutResult` from candidate search.
- Candidate generation uses strategies: `grid`, `spine-road`, `cul-de-sac`.

## API Endpoints

- `POST /layout/search` -> `LayoutResult`
  - `400` invalid input or parcel/zoning contract mismatch.
  - `422` structured `LayoutSearchError` (e.g., `no_buildable_units`, `runtime_budget_exceeded`, `no_viable_layout`).
  - `422` runtime layout errors wrapped as `layout_runtime_error`.

## Dependencies

- GIS runtime via `GIS_lot_layout_optimizer/apps/python-api/services/layout_engine/layout_search.py`
- Contract validators (`validate_parcel_output`, `validate_zoning_rules_for_layout`, `validate_layout_result_output`)
- Shapely geometry operations

## Known Limitations

- Depends on external GIS runtime package/module loading.
- Runtime budget default is 55 seconds; long/complex parcels can still fail.
- Uses deterministic heuristics and selected strategies, not exhaustive optimization.
- Open space/utility outputs are basic (`open_space_area_sqft` fixed in normalized candidates; utility length defaults to 0).

## System State vs Roadmap

- Complete:
  - Operational API, candidate search, ranking, and canonical output.
- In progress:
  - Solver/runtime stabilization and compatibility cleanup.
- Missing:
  - Broader scenario generation and advanced optimization dimensions.
