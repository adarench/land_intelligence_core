from __future__ import annotations

import json
import logging
from pathlib import Path
import sys

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestration.pipeline_runner import PipelineRunner
from pipelines.parcel_feasibility_pipeline import ParcelFeasibilityPipeline


def load_config() -> dict:
    config_path = Path(__file__).resolve().parents[1] / "configs" / "system_config.yaml"
    return yaml.safe_load(config_path.read_text()) or {}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _ = load_config()

    runner = PipelineRunner()
    pipeline = ParcelFeasibilityPipeline()
    result = runner.run_pipeline(
        pipeline_name="parcel_feasibility_pipeline",
        pipeline_fn=pipeline.run,
        inputs={"parcel_id": "test_parcel_001"},
    )

    run = next(reversed(runner.runs.values()))

    print("PIPELINE RUN: parcel_feasibility_pipeline")
    print(f"RUN_ID: {run.run_id}")
    print()

    for index, interaction in enumerate(run.interactions, start=1):
        print(f"Stage {index}: {interaction.pipeline_stage}")
        print(f"Engine: {interaction.engine_called}")
        print(f"Status: {interaction.status}")
        print(f"Execution Time: {interaction.execution_time:.6f}s")
        print(f"Contract Validation: {interaction.validation_result}")
        print(f"Stub Used: {'yes' if interaction.stub_used else 'no'}")
        if interaction.error:
            print(f"Error: {interaction.error}")
        print()

    print("PIPELINE COMPLETE")
    print()
    print("FeasibilityResult:")
    print(json.dumps(result.result.model_dump(mode="json"), indent=2))
    print()
    print("Stage Source Summary:")
    for interaction in run.interactions:
        source = "stub" if interaction.stub_used else "real"
        print(f"- {interaction.pipeline_stage}: {source}")


if __name__ == "__main__":
    main()
