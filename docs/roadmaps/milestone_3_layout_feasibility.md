## Milestone 3 - Layout Feasibility

Status:

`Foundation implemented, integration validation pending`

Current state:

- `POST /layout/search` is implemented in `bedrock/api/layout_api.py`
- Bedrock layout search delegates to the GIS layout runtime
- the public response model is a milestone-local compatibility shape from `bedrock/services/layout_service.py`
- canonical `LayoutResult` normalization is still handled through conversion helpers and validators

Validation still pending:

- direct public emission of canonical `LayoutResult`
- full contract reconciliation between the layout API, orchestration pipeline, and GIS runtime
