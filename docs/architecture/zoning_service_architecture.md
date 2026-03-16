# Zoning Service Architecture

## Purpose

`bedrock/services/zoning_service.py` implements the active zoning lookup boundary for the Land Feasibility Platform.

Public boundary:

- `Parcel -> ZoningRules`

## Public API

Implemented endpoint:

- `POST /zoning/lookup`

Request body:

```json
{
  "parcel": { "...canonical Parcel..." }
}
```

Response body:

- canonical `ZoningRules`

## Current Runtime Design

Service flow:

1. Read parcel geometry and jurisdiction hint
2. Identify the best jurisdiction dataset from `zoning_data_scraper`
3. Resolve the top zoning district by parcel geometry overlap
4. Resolve overlay labels from overlay geometry when available
5. Normalize rule records into canonical zoning fields
6. Return parcel-scoped `ZoningRules`

Errors surfaced by the service:

- `NoJurisdictionMatchError`
- `NoZoningMatchError`
- `AmbiguousJurisdictionMatchError`
- `AmbiguousZoningMatchError`

## Module Boundaries

- Parcel ingestion owns geometry normalization, persistence, and jurisdiction inference
- Zoning service owns jurisdiction dataset selection, district lookup, overlay resolution, and rule normalization
- `zoning_data_scraper` supplies the datasets and normalization helpers used by the Bedrock zoning layer

## Supported Jurisdictions

Minimum active coverage:

- Salt Lake City
- Lehi
- Draper

## Boundary Note

`takeoff_archive` is frozen legacy research code and is not part of the active Land Feasibility Platform.
