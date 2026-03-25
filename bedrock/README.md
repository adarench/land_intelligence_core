# Bedrock

Bedrock is a lightweight orchestration platform for a land intelligence system composed of independent engines. It provides domain contracts, engine adapters, reproducible pipelines, agent coordination artifacts, database schema, and platform documentation.

## Principles

- Preserve engine independence.
- Enforce strict domain contracts.
- Keep orchestration modular and observable.
- Support parallel development by multiple agents.
- Avoid duplicating engine logic.

## Repository Layout

- `contracts/`: Pydantic domain models and shared contract utilities.
- `pipelines/`: Composable platform pipelines.
- `engines/`: Thin wrappers around external engines.
- `agents/`: Agent responsibilities and guardrails.
- `database/`: Postgres and PostGIS schema assets.
- `orchestration/`: Pipeline execution and telemetry utilities.
- `configs/`: System configuration.
- `docs/`: Platform architecture and domain documentation.

## Quick Start

1. Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

2. Configure engine module paths and database settings in `configs/system_config.yaml`.

3. Run a pipeline from Python:

```python
from orchestration.pipeline_runner import PipelineRunner
from pipelines.parcel_feasibility_pipeline import ParcelFeasibilityPipeline

runner = PipelineRunner()
pipeline = ParcelFeasibilityPipeline()
result = runner.run_pipeline("parcel_feasibility", pipeline.run, parcel_id="PARCEL-123")
```

## Status

This repository provides production-grade scaffolding for orchestration. External engine implementations remain out of scope and must be integrated via the adapter contracts in `engines/`.
