"""Compare current generated layouts against real layout patterns and rank realism heuristics."""

from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon, shape

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BEDROCK_ROOT = WORKSPACE_ROOT / "bedrock"
for candidate in (WORKSPACE_ROOT, BEDROCK_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from bedrock.services import layout_service
from bedrock.services.layout_benchmark_service import load_layout_benchmark_cases

REAL_LAYOUTS_PATH = WORKSPACE_ROOT / "GIS_lot_layout_optimizer" / "model_lab" / "datasets" / "layout_training" / "real_layouts.jsonl"
REAL_EXPORTS_DIR = WORKSPACE_ROOT / "GIS_lot_layout_optimizer" / "apps" / "python-api" / "data" / "exports"
OUTPUT_PATH = WORKSPACE_ROOT / "bedrock" / "benchmarks" / "layout_realism_gap_analysis.json"


def _safe_mean(values: list[float]) -> float:
    return float(statistics.mean(values)) if values else 0.0


def _safe_median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _safe_std(values: list[float]) -> float:
    return float(statistics.pstdev(values)) if len(values) > 1 else 0.0


def _compactness(poly: Polygon) -> float:
    perim = float(poly.length)
    area = float(poly.area)
    if perim <= 0.0 or area <= 0.0:
        return 0.0
    return (4.0 * math.pi * area) / (perim * perim)


def _rect_sides(poly: Polygon) -> tuple[float, float]:
    mrr = poly.minimum_rotated_rectangle
    coords = list(mrr.exterior.coords)
    lengths = []
    for i in range(4):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]
        lengths.append(math.hypot(x2 - x1, y2 - y1))
    lengths.sort()
    return lengths[0], lengths[-1]


def _distribution(values: list[float]) -> dict[str, float]:
    return {
        "mean": _safe_mean(values),
        "median": _safe_median(values),
        "std": _safe_std(values),
        "cv": (_safe_std(values) / _safe_mean(values)) if _safe_mean(values) > 0 else 0.0,
        "count": float(len(values)),
    }


def _current_layout_metrics() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in load_layout_benchmark_cases():
        parcel_polygon_local, projection = layout_service._geometry_to_local_feet(case.parcel.geometry)
        parcel_area_sqft = float(case.parcel.area_sqft or parcel_polygon_local.area)
        solver_constraints, heuristics = layout_service._build_layout_parameters(case.parcel, case.zoning_rules, parcel_area_sqft)
        candidates = layout_service.run_layout_search(
            parcel_polygon=parcel_polygon_local,
            area_sqft=parcel_area_sqft,
            to_lnglat=lambda x_ft, y_ft: layout_service._to_geojson_coords([(x_ft, y_ft)], projection)[0],
            n_candidates=case.max_candidates,
            n_top=3,
            zoning_rules=solver_constraints.zoning_rules,
            solver_constraints=layout_service._solver_constraint_payload(solver_constraints),
            search_heuristics=layout_service._search_heuristics_payload(heuristics),
            road_width_ft=heuristics.road_width_ft,
            lot_depth=heuristics.target_lot_depth_ft,
            min_frontage_ft=heuristics.frontage_hint_ft,
            min_lot_area_sqft=solver_constraints.min_lot_area_sqft,
            side_setback_ft=solver_constraints.side_setback_ft,
            min_buildable_width_ft=solver_constraints.required_buildable_width_ft,
            max_units=solver_constraints.max_units,
            use_prior=True,
        )
        if not candidates:
            continue
        c = candidates[0]
        lot_compactness = []
        frontage = []
        depth = []
        for lot in c.result.lots:
            lot_compactness.append(_compactness(lot.polygon))
            frontage.append(float(lot.frontage_ft))
            depth.append(float(lot.depth_ft))
        m = c.result.metrics
        parcel_area = float(m.get("parcel_area_sqft", parcel_area_sqft))
        dev_area = float(m.get("total_lot_area_sqft", 0.0))
        rows.append(
            {
                "source": "current",
                "case_id": case.case_id,
                "unit_count": float(m.get("lot_count", len(c.result.lots))),
                "road_density_ft_per_acre": float(m.get("road_density_ft_per_acre", 0.0)),
                "dev_area_ratio": float(m.get("dev_area_ratio", 0.0)),
                "leftover_ratio": max(0.0, 1.0 - (dev_area / max(parcel_area, 1e-9))),
                "lot_compactness_mean": _safe_mean(lot_compactness),
                "irregular_lot_share": (
                    sum(1 for value in lot_compactness if value < 0.60) / max(len(lot_compactness), 1)
                ),
                "frontage_cv": _distribution(frontage)["cv"],
                "depth_cv": _distribution(depth)["cv"],
            }
        )
    return rows


def _real_layout_metrics() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not REAL_LAYOUTS_PATH.exists():
        return rows
    with REAL_LAYOUTS_PATH.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            unit_id = str(rec.get("unit_id", ""))
            if not unit_id.startswith("real-"):
                continue
            run_id = unit_id.replace("real-", "", 1)
            geojson_path = REAL_EXPORTS_DIR / run_id / "subdivision_layout.geojson"
            if not geojson_path.exists():
                continue
            geo = json.loads(geojson_path.read_text(encoding="utf-8"))
            features = geo.get("features", [])
            lots = []
            parcel_geom = None
            for feat in features:
                layer = str(feat.get("properties", {}).get("layer", "")).lower()
                geom = feat.get("geometry")
                if not isinstance(geom, dict):
                    continue
                if layer == "lots":
                    try:
                        lots.append(shape(geom))
                    except Exception:
                        pass
                elif layer == "parcel" and parcel_geom is None:
                    try:
                        parcel_geom = shape(geom)
                    except Exception:
                        pass
            if not lots:
                continue
            lot_compactness = []
            frontage_proxy = []
            depth_proxy = []
            for lot in lots:
                if lot.is_empty:
                    continue
                lot_compactness.append(_compactness(lot))
                short_side, long_side = _rect_sides(lot)
                frontage_proxy.append(short_side)
                depth_proxy.append(long_side)
            if not lot_compactness:
                continue
            unit_count = float(rec.get("layout_metrics", {}).get("lot_count", len(lots)))
            parcel_area = float(parcel_geom.area) if parcel_geom is not None else float(rec.get("layout_metrics", {}).get("parcel_area_sqft", 0.0))
            dev_area = sum(float(l.area) for l in lots)
            road_density = float(rec.get("layout_metrics", {}).get("road_density_ft_per_acre", 0.0))
            if road_density <= 0.0:
                road_density = float(rec.get("road_graph", {}).get("metrics", {}).get("road_density_ft_per_acre", 0.0))
            if road_density <= 0.0 or road_density > 500.0:
                continue
            rows.append(
                {
                    "source": "real",
                    "case_id": str(rec.get("parcel_id", run_id)),
                    "unit_count": unit_count,
                    "road_density_ft_per_acre": road_density,
                    "dev_area_ratio": (dev_area / max(parcel_area, 1e-9)) if parcel_area > 0 else 0.0,
                    "leftover_ratio": max(0.0, 1.0 - (dev_area / max(parcel_area, 1e-9))) if parcel_area > 0 else 0.0,
                    "lot_compactness_mean": _safe_mean(lot_compactness),
                    "irregular_lot_share": (
                        sum(1 for value in lot_compactness if value < 0.60) / max(len(lot_compactness), 1)
                    ),
                    "frontage_cv": _distribution(frontage_proxy)["cv"],
                    "depth_cv": _distribution(depth_proxy)["cv"],
                }
            )
    return rows


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    metrics = [
        "unit_count",
        "road_density_ft_per_acre",
        "dev_area_ratio",
        "leftover_ratio",
        "lot_compactness_mean",
        "irregular_lot_share",
        "frontage_cv",
        "depth_cv",
    ]
    return {metric: _safe_mean([float(r[metric]) for r in rows]) for metric in metrics}


def _heuristic_rank(current: dict[str, float], real: dict[str, float], gaps: dict[str, float]) -> list[dict[str, Any]]:
    # Positive score = larger normalized realism gap addressed by the heuristic.
    specs = [
        {
            "heuristic": "Enforce Consistent Block Depth Bands",
            "targets": ["depth_cv", "dev_area_ratio", "leftover_ratio"],
            "rationale": "Reduce depth variance by snapping road offsets/block slicing to target depth bands (e.g., 95-120 ft).",
        },
        {
            "heuristic": "Penalize Irregular Lot Geometry",
            "targets": ["lot_compactness_mean", "irregular_lot_share"],
            "rationale": "Add shape regularity penalty using compactness and minimum-angle checks during candidate scoring.",
        },
        {
            "heuristic": "Enforce Realistic Frontage Distributions",
            "targets": ["frontage_cv", "irregular_lot_share"],
            "rationale": "Apply frontage band priors (district-specific p10-p90) and penalize outliers.",
        },
        {
            "heuristic": "Improve Road Placement Logic",
            "targets": ["road_density_ft_per_acre", "leftover_ratio", "depth_cv"],
            "rationale": "Optimize spacing/offset of primary roads to avoid under-served blocks and leftover slivers.",
        },
    ]

    def contribution(metric: str) -> float:
        cur = float(current.get(metric, 0.0))
        ref = float(real.get(metric, 0.0))
        gap = float(gaps.get(metric, 0.0))
        scale = max(abs(ref), 1e-6)
        # Relative absolute gap keeps scores comparable across units.
        rel_gap = abs(gap) / scale
        # Cap to reduce runaway influence from one metric.
        return min(rel_gap, 3.0)

    ranked = []
    for spec in specs:
        score = sum(contribution(m) for m in spec["targets"])
        ranked.append(
            {
                **spec,
                "impact_score": round(score, 4),
                "gap_signals": {m: round(float(gaps.get(m, 0.0)), 4) for m in spec["targets"]},
            }
        )
    ranked.sort(key=lambda item: item["impact_score"], reverse=True)
    return ranked


def run_analysis() -> dict[str, Any]:
    current_rows = _current_layout_metrics()
    real_rows = _real_layout_metrics()
    current = _aggregate(current_rows)
    real = _aggregate(real_rows)
    gaps = {k: current[k] - real[k] for k in current}
    ranked_heuristics = _heuristic_rank(current, real, gaps)
    return {
        "analysis": "layout_realism_gap_analysis",
        "sample_sizes": {"current_layouts": len(current_rows), "real_layouts": len(real_rows)},
        "current_means": current,
        "real_means": real,
        "gaps_current_minus_real": gaps,
        "ranked_heuristics": ranked_heuristics,
    }


def main() -> int:
    report = run_analysis()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved realism gap report to {OUTPUT_PATH}")
    print("sample sizes:", report["sample_sizes"])
    print("top heuristics:")
    for idx, item in enumerate(report["ranked_heuristics"], start=1):
        print(f"{idx}. {item['heuristic']} (impact={item['impact_score']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
