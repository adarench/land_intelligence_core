## Milestone 2 - Zoning Intelligence

Status:

`Foundation implemented, integration validation pending`

Current state:

- `POST /zoning/lookup` is implemented in `bedrock/api/zoning_api.py`
- the active zoning service performs geometry-based district lookup with `zoning_data_scraper` datasets
- the public API returns canonical `ZoningRules`
- contract validation is enforced at the zoning service boundary

Validation still pending:

- full runtime validation reliability across all target jurisdictions (Salt Lake City, Lehi, Draper)
- end-to-end reconciliation with pipeline stabilization benchmarks
- gate evidence (2026-03-19):
  - `bedrock/benchmarks/po2_stabilization_latest.json`
  - `zoning_success_rate = 0.13043478260869565` vs target `0.90`
