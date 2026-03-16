# Parcel Layer

The Bedrock parcel layer is the geometric foundation for:

Parcel -> Zoning -> Layout -> Feasibility

## Responsibilities

- ingest GeoJSON parcel polygons
- normalize and validate geometry deterministically
- compute canonical parcel metadata
- resolve jurisdiction from cached GIS boundary geometry
- persist normalized parcels in SQLite
- support indexed spatial lookup of stored parcels

## Jurisdiction Resolution

Jurisdictions are resolved from local GIS geometry already present in the workspace under `zoning_data_scraper/`.

- municipal zoning polygons and linework are loaded once at process startup
- linework-only datasets are polygonized into coverage areas
- merged jurisdiction geometries are indexed with `STRtree`
- lookup uses point-in-polygon containment on parcel centroids

This keeps parcel logic independent from zoning-rule evaluation while still providing real spatial jurisdiction matching.

## Storage

Parcels are persisted in `bedrock/data/parcels.db`.

The store contains:

- normalized parcel records
- bounding-box columns
- an SQLite `RTree` sidecar index for bbox queries and future spatial operations

## Geometry Guarantees

Normalized parcel geometry is always returned as a GeoJSON `Polygon` and is validated for:

- minimum ring length
- closed rings
- duplicate vertex cleanup
- consistent winding order
- non-zero area
- self-crossing rejection if repair cannot yield a valid polygon
- valid numeric coordinate ranges

These guarantees are intended to make parcel outputs safe for downstream zoning lookup and layout engines.
