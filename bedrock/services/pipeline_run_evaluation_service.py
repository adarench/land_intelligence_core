"""Evaluation utilities over persisted pipeline run artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import fmean
from typing import Any

from bedrock.contracts.pipeline_run import PipelineRun
from bedrock.services.pipeline_run_store import PipelineRunStore


class PipelineRunEvaluationService:
    """Compute benchmark metrics from stored PipelineRun JSON artifacts."""

    def __init__(self, runs_dir: Path | str | None = None) -> None:
        self.runs_dir = Path(runs_dir) if runs_dir is not None else Path(__file__).resolve().parents[1] / "runs"

    def benchmark(
        self,
        *,
        run_ids: list[str] | None = None,
        min_roi: float | None = None,
        min_units: int | None = None,
    ) -> dict[str, Any]:
        runs = self._load_runs(run_ids=run_ids)
        filtered = [run for run in runs if self._passes_filters(run, min_roi=min_roi, min_units=min_units)]
        return self._aggregate(filtered)

    def compare(
        self,
        *,
        candidate_run_ids: list[str] | None = None,
        candidate_min_roi: float | None = None,
        candidate_min_units: int | None = None,
        baseline_run_ids: list[str] | None = None,
        baseline_min_roi: float | None = None,
        baseline_min_units: int | None = None,
    ) -> dict[str, Any]:
        candidate = self.benchmark(
            run_ids=candidate_run_ids,
            min_roi=candidate_min_roi,
            min_units=candidate_min_units,
        )
        baseline = self.benchmark(
            run_ids=baseline_run_ids,
            min_roi=baseline_min_roi,
            min_units=baseline_min_units,
        )
        return {
            "candidate": candidate,
            "baseline": baseline,
            "delta": self._delta(candidate, baseline),
        }

    def _load_runs(self, *, run_ids: list[str] | None = None) -> list[PipelineRun]:
        selected = {str(run_id) for run_id in run_ids} if run_ids else None
        rows: list[PipelineRun] = []
        if not self.runs_dir.exists():
            return rows

        paths = sorted(self.runs_dir.glob("*.json"), key=lambda path: path.name)
        for path in paths:
            run_id = path.stem
            if selected is not None and run_id not in selected:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows.append(self._coerce_pipeline_run(payload))
        if selected is not None:
            rows.sort(key=lambda item: str(item.run_id))
        return rows

    @staticmethod
    def _passes_filters(run: PipelineRun, *, min_roi: float | None, min_units: int | None) -> bool:
        if run.feasibility_result is None:
            return min_roi is None and min_units is None
        roi = run.feasibility_result.ROI
        units = run.feasibility_result.units

        if min_roi is not None:
            if not isinstance(roi, (int, float)):
                return False
            if float(roi) < float(min_roi):
                return False

        if min_units is not None:
            if not isinstance(units, (int, float)):
                return False
            if int(units) < int(min_units):
                return False

        return True

    @staticmethod
    def _aggregate(runs: list[PipelineRun]) -> dict[str, Any]:
        roi_values: list[float] = []
        profit_values: list[float] = []
        unit_values: list[float] = []

        for run in runs:
            if run.feasibility_result is None:
                continue
            roi = run.feasibility_result.ROI
            projected_profit = run.feasibility_result.projected_profit
            units = run.feasibility_result.units

            if isinstance(roi, (int, float)):
                roi_values.append(float(roi))
            if isinstance(projected_profit, (int, float)):
                profit_values.append(float(projected_profit))
            if isinstance(units, (int, float)):
                unit_values.append(float(units))

        return {
            "run_count": len(runs),
            "avg_ROI": fmean(roi_values) if roi_values else None,
            "avg_projected_profit": fmean(profit_values) if profit_values else None,
            "avg_units": fmean(unit_values) if unit_values else None,
            "min_ROI": min(roi_values) if roi_values else None,
            "max_ROI": max(roi_values) if roi_values else None,
        }

    @staticmethod
    def _coerce_pipeline_run(payload: dict[str, Any]) -> PipelineRun:
        return PipelineRun.model_validate(PipelineRunStore._normalize_pipeline_run_payload(payload))

    @staticmethod
    def _delta(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
        metrics = ("avg_ROI", "avg_projected_profit", "avg_units", "min_ROI", "max_ROI")
        delta: dict[str, Any] = {
            "run_count": int(candidate.get("run_count", 0)) - int(baseline.get("run_count", 0)),
        }
        for metric in metrics:
            current_value = candidate.get(metric)
            baseline_value = baseline.get(metric)
            if isinstance(current_value, (int, float)) and isinstance(baseline_value, (int, float)):
                delta[metric] = float(current_value) - float(baseline_value)
            else:
                delta[metric] = None
        return delta
