# Pipeline Orchestrator

Last updated: 2026-03-17
Code: `bedrock/services/pipeline_service.py`, `bedrock/api/pipeline_api.py`, `bedrock/services/pipeline_run_store.py`

## Purpose

Orchestrate full execution of `parcel -> zoning -> layout -> feasibility` and expose a single execution endpoint.

## Inputs

Service (`PipelineService.run`):
- `parcel: Parcel | None`
- `parcel_geometry: dict | None`
- `max_candidates: int`
- `parcel_id: str | None`
- `jurisdiction: str | None`
- `market_data: MarketData | None`

API:
- `POST /pipeline/run` with same fields (`market_context` API field mapped to `market_data`).

## Outputs

- Canonical `FeasibilityResult`.
- Pipeline run log record persisted via `PipelineRunStore`:
  - run id, parcel id, zoning district, layout units/score, feasibility ROI, timestamp.

## API Endpoints

- `POST /pipeline/run` -> `FeasibilityResult`
  - `400` invalid parcel input
  - `404` no district match
  - `409` ambiguous district match
  - `422` incomplete/invalid zoning rules or layout stage typed errors
  - `500` solver/runtime or feasibility stage failure

## Dependencies

- Parcel: `ParcelService`
- Zoning: `ZoningService`
- Layout: `search_subdivision_layout`
- Feasibility: `FeasibilityService`
- Contract validation linkage: `validate_feasibility_pipeline_contracts`
- Logging: `PipelineRunStore` (JSONL)

## Known Limitations

- Compatibility endpoint stabilization is still tracked as open in milestone/state artifacts.
- Runtime behavior still sensitive to zoning data completeness and solver runtime constraints.
- Run logging is append-only JSONL (no query/index service layer).

## System State vs Roadmap

- Complete:
  - End-to-end orchestrator endpoint and stage-aware error model.
- In progress:
  - Compatibility/stabilization follow-up and benchmark hardening.
- Missing:
  - Fully stabilized compatibility behavior across all consumption modes and clients.
