"""Persistence and retrieval for pipeline execution logs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from bedrock.contracts.base import BedrockModel
from bedrock.contracts.optimization_run import OptimizationRun
from bedrock.contracts.pipeline_run import PipelineRun
from bedrock.contracts.validators import validate_optimization_run_output, validate_pipeline_run_output


class PipelineRunStore:
    """Persist full pipeline runs and lightweight append-only summaries."""

    def __init__(
        self,
        log_path: Path | str | None = None,
        runs_dir: Path | str | None = None,
        optimization_runs_dir: Path | str | None = None,
    ) -> None:
        self.log_path = Path(log_path) if log_path is not None else Path(__file__).resolve().parents[1] / "data" / "pipeline_runs.jsonl"
        self.runs_dir = Path(runs_dir) if runs_dir is not None else Path(__file__).resolve().parents[1] / "runs"
        self.optimization_runs_dir = (
            Path(optimization_runs_dir)
            if optimization_runs_dir is not None
            else Path(__file__).resolve().parents[1] / "optimization_runs"
        )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.optimization_runs_dir.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self.log_path.touch()

    def save(self, record: BedrockModel) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json())
            handle.write("\n")

    def save_run(self, run_id: str, record: BedrockModel) -> Path:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        path = self.runs_dir / f"{run_id}.json"
        payload = record.model_dump(mode="json") if hasattr(record, "model_dump") else dict(record)
        canonical = self._normalize_pipeline_run_payload(payload)
        path.write_text(PipelineRun.model_validate(canonical).model_dump_json(indent=2), encoding="utf-8")
        return path

    def save_optimization_run(self, optimization_run_id: str, record: BedrockModel) -> Path:
        self.optimization_runs_dir.mkdir(parents=True, exist_ok=True)
        path = self.optimization_runs_dir / f"{optimization_run_id}.json"
        payload = record.model_dump(mode="json") if hasattr(record, "model_dump") else dict(record)
        canonical = validate_optimization_run_output(payload).model_dump(mode="json")
        path.write_text(OptimizationRun.model_validate(canonical).model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_run(self, run_id: str) -> dict:
        path = self.runs_dir / f"{run_id}.json"
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return self._normalize_pipeline_run_payload(payload)

    def load_optimization_run(self, optimization_run_id: str) -> dict:
        path = self.optimization_runs_dir / f"{optimization_run_id}.json"
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return validate_optimization_run_output(payload).model_dump(mode="json")

    def list_optimization_runs(
        self,
        *,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[dict]:
        summaries: list[dict] = []
        for path in self.optimization_runs_dir.glob("*.json"):
            payload = self.load_optimization_run(path.stem)
            best = payload.get("best_candidate") or {}
            best_feasibility = best.get("feasibility_result") or {}
            ranking_metrics = payload.get("ranking_metrics") or {}
            summaries.append(
                {
                    "optimization_run_id": payload["optimization_run_id"],
                    "timestamp": payload["timestamp"],
                    "parcel_id": payload.get("parcel_id"),
                    "candidate_count": ranking_metrics.get("candidate_count", len(payload.get("layout_candidates") or [])),
                    "best_layout_id": best.get("layout_result", {}).get("layout_id"),
                    "best_roi": best_feasibility.get("ROI"),
                    "best_projected_profit": best_feasibility.get("projected_profit"),
                    "selected_pipeline_run_id": payload.get("selected_pipeline_run_id"),
                }
            )
        ordered = sorted(summaries, key=lambda item: item["timestamp"], reverse=True)
        if offset > 0:
            ordered = ordered[offset:]
        if limit is not None:
            ordered = ordered[:limit]
        return ordered

    def list_runs(
        self,
        *,
        sort: Literal["ROI", "projected_profit", "units", "timestamp"] = "timestamp",
        order: Literal["asc", "desc"] = "desc",
        min_roi: Optional[float] = None,
        max_roi: Optional[float] = None,
        min_units: Optional[int] = None,
        max_units: Optional[int] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[dict]:
        summaries: list[dict] = []
        for path in self.runs_dir.glob("*.json"):
            payload = self.load_run(path.stem)
            feasibility = payload.get("feasibility_result") or {}
            near_feasible = payload.get("near_feasible_result") or {}
            financial_upside = near_feasible.get("financial_upside") or {}
            summaries.append(
                {
                    "run_id": payload["run_id"],
                    "timestamp": payload["timestamp"],
                    "parcel_id": payload.get("parcel_id"),
                    "units": feasibility.get("units") or financial_upside.get("relaxed_units"),
                    "projected_profit": feasibility.get("projected_profit", financial_upside.get("projected_profit")),
                    "ROI": feasibility.get("ROI", financial_upside.get("ROI")),
                }
            )

        filtered = [
            item
            for item in summaries
            if self._passes_filters(
                item,
                min_roi=min_roi,
                max_roi=max_roi,
                min_units=min_units,
                max_units=max_units,
            )
        ]

        ordered = sorted(
            filtered,
            key=lambda item: self._sort_value(item, sort),
            reverse=order == "desc",
        )
        if offset > 0:
            ordered = ordered[offset:]
        if limit is not None:
            ordered = ordered[:limit]
        return ordered

    def load_recent(self, limit: int = 10) -> list[dict]:
        if limit <= 0:
            return []
        with self.log_path.open("r", encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
        selected = lines[-limit:]
        return [json.loads(line) for line in reversed(selected)]

    @staticmethod
    def _normalize_pipeline_run_payload(payload: dict) -> dict:
        if "zoning_result" in payload and "layout_result" in payload and "feasibility_result" in payload:
            return validate_pipeline_run_output(payload).model_dump(mode="json")

        if "parcel" in payload and "zoning" in payload:
            parcel_payload = payload.get("parcel") or {}
            translated = {
                "run_id": payload.get("run_id"),
                "status": payload.get("status", "completed"),
                "parcel_id": parcel_payload.get("parcel_id"),
                "zoning_result": payload.get("zoning"),
                "layout_result": payload.get("layout"),
                "feasibility_result": payload.get("feasibility"),
                "near_feasible_result": payload.get("near_feasible_result") or payload.get("near_feasible"),
                "timestamp": payload.get("timestamp"),
                "git_commit": payload.get("git_commit"),
                "input_hash": payload.get("input_hash"),
                "stage_runtimes": payload.get("stage_runtimes") or {},
                "zoning_bypassed": payload.get("zoning_bypassed", False),
                "bypass_reason": payload.get("bypass_reason"),
            }
            return validate_pipeline_run_output(translated).model_dump(mode="json")

        return validate_pipeline_run_output(payload).model_dump(mode="json")

    @staticmethod
    def _sort_value(item: dict, field: str) -> tuple[int, object]:
        value = item.get(field)
        return (value is None, value)

    @staticmethod
    def _passes_filters(
        item: dict,
        *,
        min_roi: Optional[float],
        max_roi: Optional[float],
        min_units: Optional[int],
        max_units: Optional[int],
    ) -> bool:
        roi = item.get("ROI")
        units = item.get("units")

        if min_roi is not None:
            if not isinstance(roi, (int, float)) or float(roi) < float(min_roi):
                return False
        if max_roi is not None:
            if not isinstance(roi, (int, float)) or float(roi) > float(max_roi):
                return False
        if min_units is not None:
            if not isinstance(units, (int, float)) or int(units) < int(min_units):
                return False
        if max_units is not None:
            if not isinstance(units, (int, float)) or int(units) > int(max_units):
                return False
        return True
