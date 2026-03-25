"""Experiment grouping service over persisted pipeline runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from bedrock.contracts.experiment_run import ExperimentRun
from bedrock.contracts.validators import validate_experiment_run_output
from bedrock.services.experiment_run_store import ExperimentRunStore
from bedrock.services.pipeline_run_evaluation_service import PipelineRunEvaluationService
from bedrock.services.pipeline_run_store import PipelineRunStore

class ExperimentRunService:
    """Create reproducible experiment records that reference existing pipeline runs."""

    def __init__(
        self,
        *,
        runs_dir: Path | str | None = None,
        experiments_dir: Path | str | None = None,
        evaluation_service: PipelineRunEvaluationService | None = None,
        run_store: PipelineRunStore | None = None,
        experiment_store: ExperimentRunStore | None = None,
    ) -> None:
        self.run_store = run_store or PipelineRunStore(runs_dir=runs_dir)
        self.evaluation_service = evaluation_service or PipelineRunEvaluationService(runs_dir=self.run_store.runs_dir)
        self.experiment_store = experiment_store or ExperimentRunStore(experiments_dir=experiments_dir)

    def create(self, *, run_ids: list[str], config: dict[str, Any] | None = None) -> ExperimentRun:
        normalized_run_ids = [str(run_id) for run_id in run_ids]
        if not normalized_run_ids:
            raise ValueError("ExperimentRun requires at least one run_id")

        missing = [run_id for run_id in normalized_run_ids if not (self.run_store.runs_dir / f"{run_id}.json").exists()]
        if missing:
            raise FileNotFoundError(f"Missing PipelineRun artifacts: {', '.join(missing)}")

        record = ExperimentRun(
            experiment_id=str(uuid4()),
            run_ids=normalized_run_ids,
            config=dict(config or {}),
            metrics=self.evaluation_service.benchmark(run_ids=normalized_run_ids),
        )
        self.experiment_store.save(record.experiment_id, record)
        return record

    def get(self, experiment_id: str) -> dict[str, Any]:
        return validate_experiment_run_output(self.experiment_store.load(experiment_id)).model_dump(mode="json")
