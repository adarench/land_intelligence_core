## Milestone 1 - Real Parcel Inputs

Status:

`Foundation implemented, integration validation pending`

Current state:

- `POST /parcel/load` is implemented in `bedrock/api/parcel_api.py`
- parcel normalization and persistence are implemented in `bedrock/services/parcel_service.py`
- parcels are persisted in local SQLite via `bedrock/services/parcel_store.py`
- jurisdiction inference is implemented via `bedrock/services/jurisdiction_resolver.py`

Validation still pending:

- broader end-to-end validation against downstream zoning and layout APIs
