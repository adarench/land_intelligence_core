# Feasibility Service

Last updated: 2026-03-17
Code: `bedrock/services/feasibility_service.py`, `bedrock/api/feasibility_api.py`

## Purpose

Run deterministic financial feasibility on a layout and produce canonical `FeasibilityResult`.

## Inputs

Service (`FeasibilityService.evaluate`):
- `parcel: Parcel`
- `layout: SubdivisionLayout` (currently `LayoutResult` alias)
- `market_data: MarketData | None`

API:
- `POST /feasibility/evaluate` body:
  - `parcel: Parcel`
  - `layout: SubdivisionLayout`
  - `market_context: MarketDataInput | null` (alias accepted for request field)

## Outputs

- `FeasibilityResult` including:
  - projected revenue/cost/profit
  - ROI and margins
  - risk score
  - constraint violations
  - explanation + financial summary

## API Endpoints

- `POST /feasibility/evaluate` -> `FeasibilityResult`
  - `422` on parcel/layout contract mismatch (`LayoutResult.parcel_id` mismatch).
  - Validation failures bubble through contract validators.

## Dependencies

- Financial + cost models (`bedrock/models/financial_models.py`, `bedrock/models/cost_models.py`)
- Market assumptions (`bedrock/contracts/market_data.py`)
- Contract validators for parcel/layout/result

## Known Limitations

- Deterministic baseline model only; no stochastic simulation.
- Default market assumptions are static unless overrides are supplied.
- `risk_score` is heuristic and intentionally simple at current phase.
- Confidence currently fixed (`0.9`) in service output.

## System State vs Roadmap

- Complete:
  - API endpoint, deterministic evaluation, canonical result contract.
- In progress:
  - Scenario breadth/risk sophistication improvements.
- Missing:
  - Advanced uncertainty/sensitivity modeling for investment-grade analysis.
