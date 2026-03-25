# Service Interfaces

## Purpose

This document describes the active Bedrock API surface and its current implementation behavior for Parcel, Zoning, Layout, Feasibility, and Pipeline services.

Canonical pipeline contract chain:

`Parcel -> ZoningRules -> LayoutResult -> FeasibilityResult`

## Interface Topology

```text
Client
  -> POST /parcel/load
  -> GET  /parcel/{id}
  -> POST /zoning/lookup
  -> POST /layout/search
  -> POST /feasibility/evaluate
  -> POST /pipeline/run

Service chain:
ParcelService -> ZoningService -> LayoutService -> FeasibilityService
```

## 1. Parcel API

### `POST /parcel/load`

Purpose:

- normalize and persist parcel geometry as canonical `Parcel`

Input schema (`ParcelLoadRequest`):

- `parcel_id?: string`
- `geometry: GeoJSON Polygon|MultiPolygon`
- `jurisdiction?: string`

Output schema:

- `Parcel`

Example request:

```json
{
  "parcel_id": "parcel-001",
  "geometry": {
    "type": "Polygon",
    "coordinates": [[[-111.9, 40.7], [-111.899, 40.7], [-111.899, 40.701], [-111.9, 40.701], [-111.9, 40.7]]]
  },
  "jurisdiction": "Salt Lake City"
}
```

Example response:

```json
{
  "schema_name": "Parcel",
  "schema_version": "1.0.0",
  "parcel_id": "parcel-001",
  "geometry": { "type": "Polygon", "coordinates": [[[-111.9, 40.7], [-111.899, 40.7], [-111.899, 40.701], [-111.9, 40.701], [-111.9, 40.7]]] },
  "jurisdiction": "Salt Lake City",
  "area_sqft": 5234.2,
  "centroid": [-111.8995, 40.7005],
  "bounding_box": [-111.9, 40.7, -111.899, 40.701]
}
```

### `GET /parcel/{id}`

Purpose:

- retrieve a persisted canonical parcel by `parcel_id`

Input schema:

- path param `id: string` (maps to `parcel_id`)

Output schema:

- `Parcel`

Example request:

```text
GET /parcel/parcel-001
```

Example response:

```json
{
  "schema_name": "Parcel",
  "schema_version": "1.0.0",
  "parcel_id": "parcel-001",
  "geometry": { "type": "Polygon", "coordinates": [[[-111.9, 40.7], [-111.899, 40.7], [-111.899, 40.701], [-111.9, 40.701], [-111.9, 40.7]]] },
  "jurisdiction": "Salt Lake City",
  "area_sqft": 5234.2
}
```

## 2. Zoning API

### `POST /zoning/lookup`

Purpose:

- resolve district and normalized zoning development rules for a parcel

Input schema (`ZoningLookupRequest`):

- `parcel: Parcel`

Output schema:

- `ZoningRules`

Example request:

```json
{
  "parcel": {
    "schema_name": "Parcel",
    "schema_version": "1.0.0",
    "parcel_id": "parcel-001",
    "geometry": { "type": "Polygon", "coordinates": [[[-111.9, 40.7], [-111.899, 40.7], [-111.899, 40.701], [-111.9, 40.701], [-111.9, 40.7]]] },
    "jurisdiction": "Salt Lake City",
    "area_sqft": 5234.2
  }
}
```

Example response:

```json
{
  "schema_name": "ZoningRules",
  "schema_version": "1.0.0",
  "parcel_id": "parcel-001",
  "jurisdiction": "Salt Lake City",
  "district": "R-1-7000",
  "overlays": ["Hillside Overlay"],
  "setbacks": { "front": 20.0, "side": 8.0, "rear": 25.0 },
  "min_lot_size_sqft": 7000.0,
  "max_units_per_acre": 6.0
}
```

## 3. Layout API

### `POST /layout/search`

Purpose:

- generate a zoning-constrained subdivision layout

Input schema (`LayoutSearchRequest`):

- `parcel: Parcel`
- `zoning: ZoningRules`
- `max_candidates: int` (1..250, default 50)

Output schema:

- `LayoutResult`

Example request:

```json
{
  "parcel": { "schema_name": "Parcel", "schema_version": "1.0.0", "parcel_id": "parcel-001", "geometry": { "type": "Polygon", "coordinates": [[[-111.9, 40.7], [-111.899, 40.7], [-111.899, 40.701], [-111.9, 40.701], [-111.9, 40.7]]] }, "jurisdiction": "Salt Lake City", "area_sqft": 5234.2 },
  "zoning": { "schema_name": "ZoningRules", "schema_version": "1.0.0", "parcel_id": "parcel-001", "district": "R-1-7000", "setbacks": { "front": 20.0, "side": 8.0, "rear": 25.0 }, "min_lot_size_sqft": 7000.0, "max_units_per_acre": 6.0 },
  "max_candidates": 50
}
```

Example response:

```json
{
  "schema_name": "LayoutResult",
  "schema_version": "1.0.0",
  "layout_id": "layout-parcel-001-abc123",
  "parcel_id": "parcel-001",
  "unit_count": 8,
  "road_length_ft": 420.5,
  "lot_geometries": [],
  "road_geometries": [],
  "open_space_area_sqft": 0.0,
  "utility_length_ft": 0.0,
  "score": 0.87
}
```

## 4. Feasibility API

### `POST /feasibility/evaluate`

Purpose:

- evaluate a layout using deterministic feasibility modeling

Input schema (`FeasibilityEvaluateRequest`):

- `parcel: Parcel`
- `layout: SubdivisionLayout` (`LayoutResult` compatible)
- `market_context?: MarketData`

Output schema:

- `PipelineRun`

Example request:

```json
{
  "parcel": { "schema_name": "Parcel", "schema_version": "1.0.0", "parcel_id": "parcel-001", "geometry": { "type": "Polygon", "coordinates": [[[-111.9, 40.7], [-111.899, 40.7], [-111.899, 40.701], [-111.9, 40.701], [-111.9, 40.7]]] }, "jurisdiction": "Salt Lake City", "area_sqft": 5234.2 },
  "layout": { "layout_id": "layout-parcel-001-abc123", "parcel_id": "parcel-001", "lot_count": 8, "lot_geometries": [], "street_network": [], "road_length": 420.5, "open_space_area": 0.0, "utility_length": 0.0 },
  "market_context": { "estimated_home_price": 520000, "construction_cost_per_home": 290000, "road_cost_per_ft": 325 }
}
```

Example response:

```json
{
  "schema_name": "PipelineRun",
  "schema_version": "1.0.0",
  "run_id": "run-123",
  "status": "completed",
  "parcel_id": "parcel-001",
  "zoning_result": {
    "schema_name": "ZoningRules",
    "schema_version": "1.0.0",
    "parcel_id": "parcel-001",
    "district": "R-1-7000",
    "setbacks": { "front": 20.0, "side": 8.0, "rear": 25.0 },
    "min_lot_size_sqft": 7000.0,
    "max_units_per_acre": 6.0
  },
  "layout_result": {
    "schema_name": "LayoutResult",
    "schema_version": "1.0.0",
    "layout_id": "layout-parcel-001-abc123",
    "parcel_id": "parcel-001",
    "unit_count": 8,
    "road_length_ft": 420.5,
    "lot_geometries": [],
    "road_geometries": [],
    "open_space_area_sqft": 0.0,
    "utility_length_ft": 0.0,
    "score": 0.87,
    "buildable_area_sqft": null,
    "metadata": null
  },
  "feasibility_result": {
    "schema_name": "FeasibilityResult",
    "schema_version": "1.0.0",
    "scenario_id": "scenario-123",
    "layout_id": "layout-parcel-001-abc123",
    "parcel_id": "parcel-001",
    "units": 8,
    "projected_revenue": 4160000.0,
    "projected_cost": 2450000.0,
    "projected_profit": 1710000.0,
    "ROI": 0.698,
    "risk_score": 0.22,
    "constraint_violations": [],
    "confidence": 0.9,
    "status": "feasible"
  },
  "timestamp": "2026-03-20T00:00:00Z",
  "git_commit": null,
  "input_hash": null,
  "stage_runtimes": {},
  "zoning_bypassed": false,
  "bypass_reason": null
}
```

## 5. Pipeline API

### `POST /pipeline/run`

Purpose:

- execute parcel -> zoning -> layout -> feasibility in one API call

Input schema (`PipelineRunRequest`):

- `parcel_geometry: GeoJSON Polygon|MultiPolygon`
- `parcel_id?: string`
- `jurisdiction?: string`
- `max_candidates: int` (1..250, default 50)
- `market_context?: MarketData`

Output schema:

- `PipelineRun`

Example request:

```json
{
  "parcel_geometry": {
    "type": "Polygon",
    "coordinates": [[[-111.9, 40.7], [-111.899, 40.7], [-111.899, 40.701], [-111.9, 40.701], [-111.9, 40.7]]]
  },
  "parcel_id": "parcel-001",
  "jurisdiction": "Salt Lake City",
  "max_candidates": 50,
  "market_context": {
    "estimated_home_price": 520000,
    "construction_cost_per_home": 290000,
    "road_cost_per_ft": 325
  }
}
```

Example response:

```json
{
  "schema_name": "FeasibilityResult",
  "schema_version": "1.0.0",
  "scenario_id": "scenario-123",
  "layout_id": "layout-parcel-001-abc123",
  "parcel_id": "parcel-001",
  "units": 8,
  "projected_revenue": 4160000.0,
  "projected_cost": 2450000.0,
  "projected_profit": 1710000.0,
  "ROI": 0.698,
  "risk_score": 0.22,
  "constraint_violations": [],
  "confidence": 0.9,
  "status": "feasible"
}
```

## Integration Status Note

- API endpoints exist for all five services.
- Full pipeline orchestration is not yet operational as a validated production workflow.
