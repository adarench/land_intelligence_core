# Parcel Service

Last updated: 2026-03-17
Code: `bedrock/services/parcel_service.py`, `bedrock/api/parcel_api.py`, `bedrock/services/parcel_store.py`

## Purpose

Normalize inbound parcel geometry into canonical `Parcel` contract and persist it for downstream pipeline use.

## Inputs

Service (`ParcelService.load_parcel`):
- `geometry: GeoJSON`
- `parcel_id: str | None`
- `jurisdiction: str | None`

API:
- `POST /parcel/load` body:
  - `parcel_id: str | null`
  - `geometry: object` (required)
  - `jurisdiction: str | null`
- `GET /parcel/{parcel_id}`

## Outputs

- Canonical `Parcel` contract.
- If `parcel_id` exists and payload is equivalent, returns stored record.
- If `parcel_id` exists with different data, returns error.

## API Endpoints

- `POST /parcel/load` -> `Parcel`
  - `400` on geometry/validation/data conflicts.
- `GET /parcel/{parcel_id}` -> `Parcel`
  - `404` if not found.

## Dependencies

- Geometry normalization utilities (`utils/geometry_normalization.py`)
- Jurisdiction resolution (`services/jurisdiction_resolver.py`)
- SQLite persistence (`services/parcel_store.py`, `bedrock/data/parcels.db`)
- Contract validation (`contracts.validators.validate_parcel_output`)

## Known Limitations

- Uses local SQLite store; no distributed/shared persistence layer.
- Enrichment fields (`utilities`, `topography`, `existing_structures`) default empty unless provided elsewhere.
- Jurisdiction resolution is boundary-data dependent and may return `unknown`.
- Import-path style currently mixed (`contracts.*`/`services.*`), part of stabilization backlog.

## System State vs Roadmap

- Complete:
  - Ingestion, normalization, persistence, retrieval endpoints.
- In progress:
  - Packaging/import consistency stabilization.
- Missing:
  - Full parcel enrichment and external-source synchronization.
