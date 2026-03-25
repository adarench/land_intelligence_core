"""Layout benchmarking service for reproducible LI-5 evaluation runs."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BEDROCK_ROOT = WORKSPACE_ROOT / "bedrock"

for candidate in (WORKSPACE_ROOT, BEDROCK_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from contracts.parcel import Parcel
from contracts.validators import validate_contract
from contracts.zoning_rules import ZoningRules
from services import layout_service

DEFAULT_DATASET_ROOT = WORKSPACE_ROOT / "test_data"
DEFAULT_MANIFEST_PATH = DEFAULT_DATASET_ROOT / "layout_benchmark_manifest.json"
DEFAULT_OUTPUT_PATH = WORKSPACE_ROOT / "bedrock" / "benchmarks" / "layout_benchmark_results.json"
DEFAULT_ALGORITHM_VARIANT = "gis_layout_runtime.prior_guided"


@dataclass(frozen=True)
class LayoutBenchmarkCase:
    case_id: str
    parcel: Parcel
    zoning_rules: ZoningRules
    max_candidates: int
    notes: str = ""


@dataclass(frozen=True)
class LayoutBenchmarkExperimentRun:
    run_id: str
    dataset: str
    algorithm_variant: str
    metrics: dict[str, Any]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "dataset": self.dataset,
            "algorithm_variant": self.algorithm_variant,
            "metrics": self.metrics,
            "timestamp": self.timestamp,
        }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _load_case(entry: dict[str, Any], dataset_root: Path) -> LayoutBenchmarkCase:
    parcel_payload = _load_json(dataset_root / entry["parcel_file"])
    zoning_payload = _load_json(dataset_root / entry["zoning_file"])
    parcel = validate_contract("Parcel", parcel_payload)
    zoning_rules = validate_contract("ZoningRules", zoning_payload)
    max_candidates = int(entry.get("layout", {}).get("max_candidates", 24))
    return LayoutBenchmarkCase(
        case_id=str(entry["case_id"]),
        parcel=parcel,
        zoning_rules=zoning_rules,
        max_candidates=max(1, max_candidates),
        notes=str(entry.get("notes", "")),
    )


def load_layout_benchmark_cases(
    dataset_root: Path | None = None,
    *,
    manifest_path: Path | None = None,
    case_ids: Sequence[str] | None = None,
) -> list[LayoutBenchmarkCase]:
    dataset_root = (dataset_root or DEFAULT_DATASET_ROOT).resolve()
    manifest_path = (manifest_path or DEFAULT_MANIFEST_PATH).resolve()
    manifest = _load_json(manifest_path)
    selected = set(case_ids or [])
    cases = [
        _load_case(entry, dataset_root)
        for entry in manifest.get("cases", [])
        if not selected or entry.get("case_id") in selected
    ]
    if selected and len(cases) != len(selected):
        missing = sorted(selected.difference({case.case_id for case in cases}))
        raise ValueError(f"Unknown benchmark case ids: {', '.join(missing)}")
    return cases


def _dataset_name(manifest_path: Path, manifest_payload: dict[str, Any]) -> str:
    value = manifest_payload.get("dataset")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return manifest_path.stem


def _parcel_complexity_metrics(parcel: Parcel) -> dict[str, float | int]:
    polygon_local, _projection = layout_service._geometry_to_local_feet(parcel.geometry)
    area_sqft = float(polygon_local.area)
    perimeter_ft = float(polygon_local.length)
    vertex_count = max(len(list(polygon_local.exterior.coords)) - 1, 0)
    compactness = 0.0
    if perimeter_ft > 0.0:
        compactness = (4.0 * math.pi * area_sqft) / (perimeter_ft * perimeter_ft)
    return {
        "parcel_area": area_sqft,
        "parcel_area_acres": area_sqft / 43560.0,
        "parcel_vertex_count": vertex_count,
        "parcel_perimeter_ft": perimeter_ft,
        "parcel_compactness": compactness,
    }


def _count_disconnected_road_segments(candidate) -> int:
    segments = list(getattr(candidate.result, "segments", []) or [])
    if not segments:
        return 0
    adjacency: dict[tuple[float, float], set[tuple[float, float]]] = {}
    edge_nodes: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for segment in segments:
        start = (round(float(segment.start[0]), 3), round(float(segment.start[1]), 3))
        end = (round(float(segment.end[0]), 3), round(float(segment.end[1]), 3))
        edge_nodes.append((start, end))
        adjacency.setdefault(start, set()).add(end)
        adjacency.setdefault(end, set()).add(start)

    visited: set[tuple[float, float]] = set()
    component_index: dict[tuple[float, float], int] = {}
    component_sizes: dict[int, int] = {}
    component_id = 0
    for node in adjacency:
        if node in visited:
            continue
        queue = [node]
        visited.add(node)
        component_nodes = 0
        while queue:
            current = queue.pop()
            component_index[current] = component_id
            component_nodes += 1
            for neighbor in adjacency.get(current, ()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        component_sizes[component_id] = component_nodes
        component_id += 1
    if len(component_sizes) <= 1:
        return 0
    largest_component = max(component_sizes, key=component_sizes.get)
    return sum(
        1
        for start, end in edge_nodes
        if component_index.get(start) != largest_component or component_index.get(end) != largest_component
    )


def _evaluate_layout_validity(case: LayoutBenchmarkCase, candidate, solver_constraints) -> dict[str, Any]:
    violations: list[str] = []
    invalid_lot_count = 0
    lot_count = int(candidate.result.metrics.get("lot_count", len(candidate.result.lots)))
    if lot_count > solver_constraints.max_units:
        violations.append("exceeds_density_cap")
    for lot in candidate.result.lots:
        lot_invalid = False
        if lot.area_sqft + 1e-6 < solver_constraints.min_lot_area_sqft:
            lot_invalid = True
            violations.append("lot_below_min_lot_size")
        if lot.depth_ft - 1e-6 > solver_constraints.max_buildable_depth_ft * 1.02:
            lot_invalid = True
            violations.append("lot_exceeds_depth_cap")
        if lot.frontage_ft + 1e-6 < solver_constraints.required_buildable_width_ft + (2.0 * solver_constraints.side_setback_ft):
            lot_invalid = True
            violations.append("lot_below_required_frontage_with_setbacks")
        if lot_invalid:
            invalid_lot_count += 1
    return {
        "constraint_violations": sorted(set(violations)),
        "invalid_lot_count": invalid_lot_count,
        "disconnected_road_segments": _count_disconnected_road_segments(candidate),
    }


def _metrics_from_layout(
    *,
    case: LayoutBenchmarkCase,
    layout,
    candidate,
    solver_constraints,
    runtime: float,
) -> dict[str, Any]:
    complexity = _parcel_complexity_metrics(case.parcel)
    parcel_acres = max(float(complexity["parcel_area_acres"]), 1e-9)
    validity = _evaluate_layout_validity(case, candidate, solver_constraints)
    return {
        "units": int(layout.unit_count),
        "road_length": float(layout.road_length_ft),
        "runtime": float(runtime),
        "constraint_violations": validity["constraint_violations"],
        "invalid_lot_count": int(validity["invalid_lot_count"]),
        "disconnected_road_segments": int(validity["disconnected_road_segments"]),
        "parcel_area": float(complexity["parcel_area"]),
        "parcel_compactness": float(complexity["parcel_compactness"]),
        "candidate_search_count": int(case.max_candidates),
        "candidate_result_count": 0,
        "lot_yield_per_acre": float(layout.unit_count) / parcel_acres,
        "layout_score": float(layout.score),
        "algorithm_variant": DEFAULT_ALGORITHM_VARIANT,
        # compatibility aliases
        "units_generated": int(layout.unit_count),
        "road_length_ft": float(layout.road_length_ft),
        "runtime_seconds": float(runtime),
        "parcel_area_acres": float(complexity["parcel_area_acres"]),
    }


def run_layout_case(case: LayoutBenchmarkCase, *, algorithm_variant: str = DEFAULT_ALGORITHM_VARIANT) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        parcel_polygon_local, projection = layout_service._geometry_to_local_feet(case.parcel.geometry)
        parcel_area_sqft = float(case.parcel.area_sqft or parcel_polygon_local.area)
        solver_constraints, search_heuristics = layout_service._build_layout_parameters(
            case.parcel,
            case.zoning_rules,
            parcel_area_sqft,
        )
        candidates = layout_service.run_layout_search(
            parcel_polygon=parcel_polygon_local,
            area_sqft=parcel_area_sqft,
            to_lnglat=lambda x_ft, y_ft: layout_service._to_geojson_coords([(x_ft, y_ft)], projection)[0],
            n_candidates=case.max_candidates,
            n_top=case.max_candidates,
            zoning_rules=solver_constraints.zoning_rules,
            solver_constraints=layout_service._solver_constraint_payload(solver_constraints),
            search_heuristics=layout_service._search_heuristics_payload(search_heuristics),
            road_width_ft=search_heuristics.road_width_ft,
            lot_depth=search_heuristics.target_lot_depth_ft,
            min_frontage_ft=search_heuristics.frontage_hint_ft,
            min_lot_area_sqft=solver_constraints.min_lot_area_sqft,
            side_setback_ft=solver_constraints.side_setback_ft,
            min_buildable_width_ft=solver_constraints.required_buildable_width_ft,
            max_units=solver_constraints.max_units,
            use_prior=True,
        )
        if not candidates:
            raise RuntimeError("No layout candidates returned from layout search")

        best_candidate = candidates[0]
        layout_service._validate_candidate_constraints(case.parcel.parcel_id, best_candidate, solver_constraints)
        layout = layout_service._normalize_candidate(case.parcel, best_candidate)
        runtime = time.perf_counter() - started
        metrics = _metrics_from_layout(
            case=case,
            layout=layout,
            candidate=best_candidate,
            solver_constraints=solver_constraints,
            runtime=runtime,
        )
        metrics["candidate_result_count"] = int(len(candidates))
        metrics["algorithm_variant"] = algorithm_variant
        return {
            "case_id": case.case_id,
            "status": "success",
            "notes": case.notes,
            "algorithm_variant": algorithm_variant,
            "layout_id": layout.layout_id,
            "metrics": metrics,
        }
    except Exception as exc:
        runtime = time.perf_counter() - started
        complexity = _parcel_complexity_metrics(case.parcel)
        metrics = {
            "units": 0,
            "road_length": 0.0,
            "runtime": float(runtime),
            "constraint_violations": [str(exc)],
            "invalid_lot_count": 0,
            "disconnected_road_segments": 0,
            "parcel_area": float(complexity["parcel_area"]),
            "parcel_compactness": float(complexity["parcel_compactness"]),
            "candidate_search_count": int(case.max_candidates),
            "candidate_result_count": 0,
            "lot_yield_per_acre": 0.0,
            "layout_score": 0.0,
            "algorithm_variant": algorithm_variant,
            # compatibility aliases
            "units_generated": 0,
            "road_length_ft": 0.0,
            "runtime_seconds": float(runtime),
            "parcel_area_acres": float(complexity["parcel_area_acres"]),
        }
        return {
            "case_id": case.case_id,
            "status": "failure",
            "notes": case.notes,
            "algorithm_variant": algorithm_variant,
            "layout_id": None,
            "metrics": metrics,
            "error": str(exc),
        }


def _build_summary(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    successful = [record for record in records if record["status"] == "success"]
    runtimes = [float(record["metrics"]["runtime"]) for record in successful]
    scores = [float(record["metrics"]["layout_score"]) for record in successful]
    yields = [float(record["metrics"]["lot_yield_per_acre"]) for record in successful]
    units = [int(record["metrics"]["units"]) for record in successful]
    road_lengths = [float(record["metrics"]["road_length"]) for record in successful]
    invalid_lot_counts = [int(record["metrics"]["invalid_lot_count"]) for record in successful]
    return {
        "dataset_size": len(records),
        "success_count": len(successful),
        "failure_count": len(records) - len(successful),
        "avg_runtime": (sum(runtimes) / len(runtimes)) if runtimes else 0.0,
        "avg_layout_score": (sum(scores) / len(scores)) if scores else 0.0,
        "avg_lot_yield_per_acre": (sum(yields) / len(yields)) if yields else 0.0,
        "avg_units": (sum(units) / len(units)) if units else 0.0,
        "avg_road_length": (sum(road_lengths) / len(road_lengths)) if road_lengths else 0.0,
        "total_invalid_lot_count": sum(invalid_lot_counts),
    }


def run_layout_benchmark(
    dataset_root: Path | None = None,
    *,
    manifest_path: Path | None = None,
    case_ids: Sequence[str] | None = None,
    output_path: Path | None = None,
    algorithm_variant: str = DEFAULT_ALGORITHM_VARIANT,
    run_id: str | None = None,
) -> dict[str, Any]:
    dataset_root_resolved = (dataset_root or DEFAULT_DATASET_ROOT).resolve()
    manifest_path_resolved = (manifest_path or DEFAULT_MANIFEST_PATH).resolve()
    manifest_payload = _load_json(manifest_path_resolved)
    cases = load_layout_benchmark_cases(dataset_root_resolved, manifest_path=manifest_path_resolved, case_ids=case_ids)
    records = [run_layout_case(case, algorithm_variant=algorithm_variant) for case in cases]
    summary = _build_summary(records)
    experiment_run = LayoutBenchmarkExperimentRun(
        run_id=run_id or f"layout-benchmark-{uuid4()}",
        dataset=_dataset_name(manifest_path_resolved, manifest_payload),
        algorithm_variant=algorithm_variant,
        metrics=summary,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    report = {
        "experiment_run": experiment_run.to_dict(),
        "dataset_root": str(dataset_root_resolved),
        "manifest_path": str(manifest_path_resolved),
        "records": records,
    }
    write_layout_benchmark_results(report, output_path=output_path)
    return report


def write_layout_benchmark_results(report: dict[str, Any], *, output_path: Path | None = None) -> Path:
    path = (output_path or DEFAULT_OUTPUT_PATH).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    return path


def _records(report: dict[str, Any]) -> list[dict[str, Any]]:
    value = report.get("records")
    if isinstance(value, list):
        return value
    return []


def _timestamp(report: dict[str, Any]) -> str | None:
    run = report.get("experiment_run")
    if isinstance(run, dict):
        timestamp = run.get("timestamp")
        if isinstance(timestamp, str):
            return timestamp
    generated = report.get("generated_at")
    return generated if isinstance(generated, str) else None


def _metric_float(metrics: dict[str, Any], primary: str, fallback: str) -> float:
    if primary in metrics:
        return float(metrics[primary])
    return float(metrics.get(fallback, 0.0))


def _metric_int(metrics: dict[str, Any], primary: str, fallback: str) -> int:
    if primary in metrics:
        return int(metrics[primary])
    return int(metrics.get(fallback, 0))


def compare_layout_benchmark_runs(
    *,
    current: dict[str, Any],
    baseline: dict[str, Any],
    max_runtime_regression_pct: float = 0.2,
    max_yield_drop_pct: float = 0.05,
    max_score_drop_pct: float = 0.05,
) -> dict[str, Any]:
    baseline_records = {record["case_id"]: record for record in _records(baseline)}
    regressions: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []

    for current_record in _records(current):
        case_id = current_record["case_id"]
        baseline_record = baseline_records.get(case_id)
        if baseline_record is None:
            comparisons.append({"case_id": case_id, "status": "new_case"})
            continue
        if current_record["status"] != "success" or baseline_record["status"] != "success":
            if current_record["status"] != baseline_record["status"]:
                regressions.append(
                    {
                        "case_id": case_id,
                        "metric": "status",
                        "baseline": baseline_record["status"],
                        "current": current_record["status"],
                        "reason": "status_changed",
                    }
                )
            comparisons.append({"case_id": case_id, "status": "status_compared"})
            continue

        current_metrics = current_record["metrics"]
        baseline_metrics = baseline_record["metrics"]
        yield_delta_pct = _pct_change(
            _metric_float(current_metrics, "lot_yield_per_acre", "lot_yield_per_acre"),
            _metric_float(baseline_metrics, "lot_yield_per_acre", "lot_yield_per_acre"),
        )
        score_delta_pct = _pct_change(
            _metric_float(current_metrics, "layout_score", "layout_score"),
            _metric_float(baseline_metrics, "layout_score", "layout_score"),
        )
        runtime_delta_pct = _pct_change(
            _metric_float(current_metrics, "runtime", "runtime_seconds"),
            _metric_float(baseline_metrics, "runtime", "runtime_seconds"),
        )
        units_delta = _metric_int(current_metrics, "units", "units_generated") - _metric_int(
            baseline_metrics,
            "units",
            "units_generated",
        )

        if yield_delta_pct < (-1.0 * max_yield_drop_pct):
            regressions.append(
                {
                    "case_id": case_id,
                    "metric": "lot_yield_per_acre",
                    "baseline": _metric_float(baseline_metrics, "lot_yield_per_acre", "lot_yield_per_acre"),
                    "current": _metric_float(current_metrics, "lot_yield_per_acre", "lot_yield_per_acre"),
                    "delta_pct": yield_delta_pct,
                }
            )
        if score_delta_pct < (-1.0 * max_score_drop_pct):
            regressions.append(
                {
                    "case_id": case_id,
                    "metric": "layout_score",
                    "baseline": _metric_float(baseline_metrics, "layout_score", "layout_score"),
                    "current": _metric_float(current_metrics, "layout_score", "layout_score"),
                    "delta_pct": score_delta_pct,
                }
            )
        if runtime_delta_pct > max_runtime_regression_pct:
            regressions.append(
                {
                    "case_id": case_id,
                    "metric": "runtime",
                    "baseline": _metric_float(baseline_metrics, "runtime", "runtime_seconds"),
                    "current": _metric_float(current_metrics, "runtime", "runtime_seconds"),
                    "delta_pct": runtime_delta_pct,
                }
            )
        if units_delta < 0:
            regressions.append(
                {
                    "case_id": case_id,
                    "metric": "units",
                    "baseline": _metric_int(baseline_metrics, "units", "units_generated"),
                    "current": _metric_int(current_metrics, "units", "units_generated"),
                    "delta": units_delta,
                }
            )
        comparisons.append(
            {
                "case_id": case_id,
                "yield_delta_pct": yield_delta_pct,
                "score_delta_pct": score_delta_pct,
                "runtime_delta_pct": runtime_delta_pct,
                "units_delta": units_delta,
            }
        )

    return {
        "baseline_timestamp": _timestamp(baseline),
        "current_timestamp": _timestamp(current),
        "comparison_count": len(comparisons),
        "regression_count": len(regressions),
        "has_regression": bool(regressions),
        "comparisons": comparisons,
        "regressions": regressions,
    }


def load_benchmark_report(path: Path) -> dict[str, Any]:
    return _load_json(path.resolve())


def _pct_change(current: float, baseline: float) -> float:
    baseline_value = float(baseline)
    if baseline_value == 0.0:
        return 0.0 if float(current) == 0.0 else 1.0
    return (float(current) - baseline_value) / baseline_value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="layout_benchmark_service")
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--algorithm-variant", default=DEFAULT_ALGORITHM_VARIANT)
    parser.add_argument("--case", dest="cases", action="append", default=[])
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = run_layout_benchmark(
        dataset_root=args.dataset_root,
        manifest_path=args.manifest,
        case_ids=args.cases or None,
        output_path=args.output,
        algorithm_variant=args.algorithm_variant,
        run_id=args.run_id,
    )
    payload: dict[str, Any] = {"report": report}
    if args.baseline:
        baseline = load_benchmark_report(args.baseline)
        payload["regression"] = compare_layout_benchmark_runs(current=report, baseline=baseline)
    print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
