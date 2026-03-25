"""Reusable layout evaluation framework for performance and regression analysis."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from shapely.geometry import shape

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BEDROCK_ROOT = WORKSPACE_ROOT / "bedrock"
for candidate in (WORKSPACE_ROOT, BEDROCK_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from bedrock.contracts.parcel import Parcel
from bedrock.contracts.zoning_rules import ZoningRules
from bedrock.services.layout_service import search_layout

DEFAULT_DATASET_ROOT = WORKSPACE_ROOT / "test_data"
DEFAULT_MANIFEST_PATH = DEFAULT_DATASET_ROOT / "layout_evaluation_manifest.json"
DEFAULT_OUTPUT_PATH = WORKSPACE_ROOT / "bedrock" / "benchmarks" / "layout_evaluation_results.json"
DEFAULT_ALGORITHM_VARIANT = "gis_layout_runtime.prior_guided"


@dataclass(frozen=True)
class EvaluationParcel:
    parcel_id: str
    parcel_type: str
    geometry: dict[str, Any]
    area_sqft: float


@dataclass(frozen=True)
class ZoningScenario:
    scenario_id: str
    min_lot_size_sqft: float
    max_units_per_acre: float
    setbacks: dict[str, float]
    min_frontage_ft: float
    road_right_of_way_ft: float
    max_candidates: int


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * max(0.0, min(1.0, q))
    lo = int(math.floor(index))
    hi = int(math.ceil(index))
    if lo == hi:
        return float(ordered[lo])
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo))


def load_evaluation_inputs(
    dataset_root: Path | None = None,
    *,
    manifest_path: Path | None = None,
    parcel_ids: Sequence[str] | None = None,
    scenario_ids: Sequence[str] | None = None,
) -> tuple[str, list[EvaluationParcel], list[ZoningScenario]]:
    dataset_root = (dataset_root or DEFAULT_DATASET_ROOT).resolve()
    manifest_path = (manifest_path or DEFAULT_MANIFEST_PATH).resolve()
    manifest = _load_json(manifest_path)
    parcels_payload = _load_json(dataset_root / manifest["parcels_file"])
    selected_parcels = set(parcel_ids or [])
    selected_scenarios = set(scenario_ids or [])

    parcels = [
        EvaluationParcel(
            parcel_id=str(item["parcel_id"]),
            parcel_type=str(item.get("parcel_type", "unknown")),
            geometry=dict(item["geometry"]),
            area_sqft=float(item["area_sqft"]),
        )
        for item in parcels_payload.get("records", [])
        if not selected_parcels or item.get("parcel_id") in selected_parcels
    ]
    scenarios = [
        ZoningScenario(
            scenario_id=str(item["scenario_id"]),
            min_lot_size_sqft=float(item["min_lot_size_sqft"]),
            max_units_per_acre=float(item["max_units_per_acre"]),
            setbacks={k: float(v) for k, v in dict(item.get("setbacks", {})).items()},
            min_frontage_ft=float(item.get("min_frontage_ft", 0.0)),
            road_right_of_way_ft=float(item.get("road_right_of_way_ft", 32.0)),
            max_candidates=max(1, int(item.get("max_candidates", 50))),
        )
        for item in manifest.get("scenarios", [])
        if not selected_scenarios or item.get("scenario_id") in selected_scenarios
    ]
    return str(manifest.get("dataset", manifest_path.stem)), parcels, scenarios


def _build_contracts(parcel: EvaluationParcel, scenario: ZoningScenario) -> tuple[Parcel, ZoningRules]:
    parcel_contract = Parcel(
        parcel_id=parcel.parcel_id,
        geometry=parcel.geometry,
        jurisdiction="BenchmarkCounty_UT",
        area_sqft=parcel.area_sqft,
        centroid=None,
        bounding_box=None,
        zoning_district="R-1",
        utilities=[],
        access_points=[],
        topography={},
        existing_structures=[],
    )
    zoning_rules = ZoningRules(
        parcel_id=parcel.parcel_id,
        jurisdiction="BenchmarkCounty_UT",
        district="R-1",
        min_lot_size_sqft=scenario.min_lot_size_sqft,
        max_units_per_acre=scenario.max_units_per_acre,
        setbacks=scenario.setbacks,
        min_frontage_ft=scenario.min_frontage_ft,
        road_right_of_way_ft=scenario.road_right_of_way_ft,
    )
    return parcel_contract, zoning_rules


def _lot_size_distribution(lot_geometries: list[dict[str, Any]]) -> dict[str, float]:
    sizes = [float(shape(geometry).area) for geometry in lot_geometries]
    if not sizes:
        return {"min": 0.0, "p25": 0.0, "median": 0.0, "p75": 0.0, "max": 0.0, "mean": 0.0}
    return {
        "min": min(sizes),
        "p25": _percentile(sizes, 0.25),
        "median": _percentile(sizes, 0.50),
        "p75": _percentile(sizes, 0.75),
        "max": max(sizes),
        "mean": statistics.mean(sizes),
    }


def _theoretical_max_units(parcel_area_sqft: float, scenario: ZoningScenario) -> int:
    by_lot_size = max(1, math.floor(parcel_area_sqft / scenario.min_lot_size_sqft))
    by_density = max(1, math.floor((parcel_area_sqft / 43560.0) * scenario.max_units_per_acre))
    return max(1, min(by_lot_size, by_density))


def _runtime_distribution(values: list[float]) -> dict[str, float]:
    if not values:
        return {"avg_seconds": 0.0, "median_seconds": 0.0, "p95_seconds": 0.0, "max_seconds": 0.0}
    return {
        "avg_seconds": sum(values) / len(values),
        "median_seconds": _percentile(values, 0.5),
        "p95_seconds": _percentile(values, 0.95),
        "max_seconds": max(values),
    }


def run_layout_evaluation(
    dataset_root: Path | None = None,
    *,
    manifest_path: Path | None = None,
    parcel_ids: Sequence[str] | None = None,
    scenario_ids: Sequence[str] | None = None,
    output_path: Path | None = None,
    algorithm_variant: str = DEFAULT_ALGORITHM_VARIANT,
    run_id: str | None = None,
) -> dict[str, Any]:
    dataset_name, parcels, scenarios = load_evaluation_inputs(
        dataset_root,
        manifest_path=manifest_path,
        parcel_ids=parcel_ids,
        scenario_ids=scenario_ids,
    )
    records: list[dict[str, Any]] = []
    runtimes: list[float] = []
    unit_yields: list[float] = []
    road_lengths: list[float] = []

    for parcel in parcels:
        for scenario in scenarios:
            started = time.perf_counter()
            case_id = f"{parcel.parcel_id}:{scenario.scenario_id}"
            theoretical_max = _theoretical_max_units(parcel.area_sqft, scenario)
            parcel_contract, zoning_rules = _build_contracts(parcel, scenario)
            try:
                layout = search_layout(parcel_contract, zoning_rules, max_candidates=scenario.max_candidates)
                runtime = time.perf_counter() - started
                runtimes.append(runtime)
                units = int(layout.unit_count)
                road_length = float(layout.road_length_ft)
                unit_yields.append(float(units))
                road_lengths.append(road_length)
                yield_efficiency = units / theoretical_max if theoretical_max else 0.0
                road_efficiency = road_length / units if units > 0 else float("inf")
                records.append(
                    {
                        "case_id": case_id,
                        "parcel_id": parcel.parcel_id,
                        "parcel_type": parcel.parcel_type,
                        "scenario_id": scenario.scenario_id,
                        "max_candidates": scenario.max_candidates,
                        "algorithm_variant": algorithm_variant,
                        "status": "success",
                        "metrics": {
                            "unit_yield": units,
                            "road_length": road_length,
                            "lot_size_distribution": _lot_size_distribution(layout.lot_geometries),
                            "solver_runtime": runtime,
                            "constraint_violations": [],
                            "yield_efficiency": yield_efficiency,
                            "road_efficiency": road_efficiency,
                            "layout_score": float(layout.score),
                            "theoretical_max_units": theoretical_max,
                        },
                    }
                )
            except Exception as exc:
                runtime = time.perf_counter() - started
                runtimes.append(runtime)
                records.append(
                    {
                        "case_id": case_id,
                        "parcel_id": parcel.parcel_id,
                        "parcel_type": parcel.parcel_type,
                        "scenario_id": scenario.scenario_id,
                        "max_candidates": scenario.max_candidates,
                        "algorithm_variant": algorithm_variant,
                        "status": "failure",
                        "metrics": {
                            "unit_yield": 0,
                            "road_length": 0.0,
                            "lot_size_distribution": {"min": 0.0, "p25": 0.0, "median": 0.0, "p75": 0.0, "max": 0.0, "mean": 0.0},
                            "solver_runtime": runtime,
                            "constraint_violations": [str(exc)],
                            "yield_efficiency": 0.0,
                            "road_efficiency": float("inf"),
                            "layout_score": 0.0,
                            "theoretical_max_units": theoretical_max,
                        },
                        "error": str(exc),
                    }
                )

    successful = [record for record in records if record["status"] == "success"]
    avg_yield = sum(unit_yields) / len(unit_yields) if unit_yields else 0.0
    avg_road = sum(road_lengths) / len(road_lengths) if road_lengths else 0.0
    stability = {
        "success_count": len(successful),
        "failure_count": len(records) - len(successful),
        "success_rate": (len(successful) / len(records)) if records else 0.0,
    }
    poor_road_threshold = 250.0
    low_yield_threshold = 0.6
    report = {
        "run_id": run_id or f"layout-eval-{uuid4()}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset_name,
        "algorithm_variant": algorithm_variant,
        "dataset_summary": {
            "parcel_count": len(parcels),
            "scenario_count": len(scenarios),
            "case_count": len(records),
            "parcel_type_distribution": {
                parcel_type: sum(1 for parcel in parcels if parcel.parcel_type == parcel_type)
                for parcel_type in sorted({parcel.parcel_type for parcel in parcels})
            },
        },
        "aggregate_metrics": {
            "average_unit_yield": avg_yield,
            "average_road_length": avg_road,
            "runtime_distribution": _runtime_distribution(runtimes),
            "solver_stability": stability,
        },
        "highlights": {
            "failed_cases": [record["case_id"] for record in records if record["status"] == "failure"],
            "poor_road_efficiency_cases": [
                record["case_id"]
                for record in successful
                if record["metrics"]["road_efficiency"] > poor_road_threshold
            ],
            "low_yield_efficiency_cases": [
                record["case_id"]
                for record in successful
                if record["metrics"]["yield_efficiency"] < low_yield_threshold
            ],
        },
        "records": records,
    }
    write_layout_evaluation_report(report, output_path=output_path)
    return report


def write_layout_evaluation_report(report: dict[str, Any], *, output_path: Path | None = None) -> Path:
    path = (output_path or DEFAULT_OUTPUT_PATH).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    return path


def load_layout_evaluation_report(path: Path) -> dict[str, Any]:
    return _load_json(path.resolve())


def compare_layout_evaluation_reports(
    *,
    current: dict[str, Any],
    baseline: dict[str, Any],
    max_runtime_regression_pct: float = 0.2,
    max_yield_drop_pct: float = 0.05,
) -> dict[str, Any]:
    regressions: list[dict[str, Any]] = []
    cur_stability = current.get("aggregate_metrics", {}).get("solver_stability", {})
    base_stability = baseline.get("aggregate_metrics", {}).get("solver_stability", {})
    if float(cur_stability.get("success_rate", 0.0)) + 1e-9 < float(base_stability.get("success_rate", 0.0)):
        regressions.append(
            {
                "metric": "solver_stability.success_rate",
                "baseline": base_stability.get("success_rate", 0.0),
                "current": cur_stability.get("success_rate", 0.0),
            }
        )

    cur_yield = float(current.get("aggregate_metrics", {}).get("average_unit_yield", 0.0))
    base_yield = float(baseline.get("aggregate_metrics", {}).get("average_unit_yield", 0.0))
    if base_yield > 0 and cur_yield < base_yield * (1.0 - max_yield_drop_pct):
        regressions.append(
            {
                "metric": "average_unit_yield",
                "baseline": base_yield,
                "current": cur_yield,
            }
        )

    cur_runtime = float(current.get("aggregate_metrics", {}).get("runtime_distribution", {}).get("avg_seconds", 0.0))
    base_runtime = float(baseline.get("aggregate_metrics", {}).get("runtime_distribution", {}).get("avg_seconds", 0.0))
    if base_runtime > 0 and cur_runtime > base_runtime * (1.0 + max_runtime_regression_pct):
        regressions.append(
            {
                "metric": "runtime_distribution.avg_seconds",
                "baseline": base_runtime,
                "current": cur_runtime,
            }
        )

    return {"has_regression": bool(regressions), "regression_count": len(regressions), "regressions": regressions}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="layout_evaluation_service")
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--run-id")
    parser.add_argument("--algorithm-variant", default=DEFAULT_ALGORITHM_VARIANT)
    parser.add_argument("--parcel", dest="parcels", action="append", default=[])
    parser.add_argument("--scenario", dest="scenarios", action="append", default=[])
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = run_layout_evaluation(
        dataset_root=args.dataset_root,
        manifest_path=args.manifest,
        parcel_ids=args.parcels or None,
        scenario_ids=args.scenarios or None,
        output_path=args.output,
        algorithm_variant=args.algorithm_variant,
        run_id=args.run_id,
    )
    payload: dict[str, Any] = {"report": report}
    if args.baseline:
        baseline = load_layout_evaluation_report(args.baseline)
        payload["regression"] = compare_layout_evaluation_reports(current=report, baseline=baseline)
    print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
