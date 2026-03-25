"""Persistence for experiment metadata over immutable pipeline runs."""

from __future__ import annotations

import json
from pathlib import Path

from bedrock.contracts.base import BedrockModel


class ExperimentRunStore:
    """Persist and retrieve ExperimentRun artifacts separately from PipelineRun storage."""

    def __init__(self, experiments_dir: Path | str | None = None) -> None:
        self.experiments_dir = (
            Path(experiments_dir)
            if experiments_dir is not None
            else Path(__file__).resolve().parents[1] / "data" / "experiments"
        )
        self.experiments_dir.mkdir(parents=True, exist_ok=True)

    def save(self, experiment_id: str, record: BedrockModel) -> Path:
        self.experiments_dir.mkdir(parents=True, exist_ok=True)
        path = self.experiments_dir / f"{experiment_id}.json"
        path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load(self, experiment_id: str) -> dict:
        path = self.experiments_dir / f"{experiment_id}.json"
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
