## Milestone 2 - Zoning Intelligence

Status:

`Foundation implemented, integration validation pending`

Current state:

- `POST /zoning/lookup` is implemented in `bedrock/api/zoning_api.py`
- the current zoning service performs district lookup against stub district polygons
- the public API returns `ZoningDistrictLookupResult`, not canonical `ZoningRules`
- an internal shim exists for future rules composition

Validation still pending:

- replacement of district-only lookup with canonical parcel-scoped `ZoningRules`
- alignment between milestone API behavior and the governance-approved contract boundary
