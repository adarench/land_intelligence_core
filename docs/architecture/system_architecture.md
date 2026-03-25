# Bedrock System Architecture

## System Overview

Bedrock is the orchestration and contract layer for a land intelligence platform composed of independent runtimes and milestone-local services.

Core capabilities:

- parcel ingestion and normalization
- zoning district lookup
- layout generation through the GIS runtime
- layout benchmarking and regression measurement
- deterministic financial feasibility evaluation
- pipeline orchestration and telemetry

## Architecture Layers

- canonical contracts in `bedrock/contracts/*`
- public Bedrock APIs in `bedrock/api/*`
- milestone and adapter services in `bedrock/services/*`
- active runtime dependencies in adjacent repositories

## Public API State

Implemented public APIs:

- `POST /parcel/load`
- `POST /zoning/lookup`
- `POST /layout/search`
- `POST /feasibility/evaluate`
- `POST /pipeline/run`

These endpoints are part of the active platform surface.

## Canonical Target Pipeline

`Parcel -> ZoningRules -> LayoutResult -> FeasibilityResult`

## Architecture Diagram (Text)

```
UI
↓
Feasibility API
↓
Pipeline Orchestrator
↓
Parcel Service -> Zoning Service -> Layout Service -> Feasibility Service
```

## Current Implemented Pipeline

1. Parcel load and normalization inside Bedrock.
2. Zoning lookup inside Bedrock backed by `zoning_data_scraper`.
3. Layout search inside Bedrock backed by the GIS layout runtime.
4. Feasibility evaluation inside Bedrock with deterministic economics.
5. Pipeline orchestration API for end-to-end parcel-to-feasibility execution.

## Current System Status

Operational:

- layout generation API is operational (`POST /layout/search`)

Partially implemented:

- parcel ingestion layer is partially implemented
- zoning intelligence system is under development
- feasibility modeling is partially implemented

Missing / not yet operational:

- full pipeline orchestration is not yet operational as a validated production flow

## External Dependencies

- `GIS_lot_layout_optimizer`: active layout runtime
- `GIS_lot_layout_optimizer/model_lab`: offline research and experimentation surface
- `zoning_data_scraper`: active zoning data and normalization support

`takeoff_archive` is frozen legacy research code and is not part of the active Land Feasibility Platform.
