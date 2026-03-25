"""Constraint enrichment experiment for layout quality and realism.

Compares baseline simple zoning constraints against richer constraint profiles:
  - operational enrichments (min_frontage_ft, road_right_of_way_ft)
  - metadata-heavy enrichments (height, lot coverage, standards payloads)
  - strict enrichments to identify over-constraint behavior
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BEDROCK_ROOT = WORKSPACE_ROOT / "bedrock"
for candidate in (WORKSPACE_ROOT, BEDROCK_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from bedrock.contracts.zoning_rules import DevelopmentStandard, ZoningRules
from bedrock.services import layout_service
from bedrock.services.layout_benchmark_service import load_layout_benchmark_cases

OUTPUT_PATH = WORKSPACE_ROOT / "bedrock" / "benchmarks" / "constraint_enrichment_experiment.json"


@dataclass(frozen=True)
class ConstraintProfile:
    profile_id: str
    description: str
    zoning_kwargs: dict[str, Any]


def _standards_from_values(
    district: str,
    *,
    min_lot_size_sqft: float,
    max_units_per_acre: float,
    front_setback_ft: float,
    side_setback_ft: float,
    rear_setback_ft: float,
    min_frontage_ft: float | None = None,
    road_right_of_way_ft: float | None = None,
    min_depth_ft: float | None = None,
) -> list[DevelopmentStandard]:
    standards = [
        DevelopmentStandard(
            id=f"{district}:min_lot_size_sqft",
            district_id=district,
            standard_type="min_lot_size_sqft",
            value=min_lot_size_sqft,
            units="sqft",
        ),
        DevelopmentStandard(
            id=f"{district}:max_units_per_acre",
            district_id=district,
            standard_type="max_units_per_acre",
            value=max_units_per_acre,
            units="du/ac",
        ),
        DevelopmentStandard(
            id=f"{district}:front_setback_ft",
            district_id=district,
            standard_type="front_setback_ft",
            value=front_setback_ft,
            units="ft",
        ),
        DevelopmentStandard(
            id=f"{district}:side_setback_ft",
            district_id=district,
            standard_type="side_setback_ft",
            value=side_setback_ft,
            units="ft",
        ),
        DevelopmentStandard(
            id=f"{district}:rear_setback_ft",
            district_id=district,
            standard_type="rear_setback_ft",
            value=rear_setback_ft,
            units="ft",
        ),
    ]
    if min_frontage_ft is not None:
        standards.append(
            DevelopmentStandard(
                id=f"{district}:min_frontage_ft",
                district_id=district,
                standard_type="min_frontage_ft",
                value=min_frontage_ft,
                units="ft",
            )
        )
    if road_right_of_way_ft is not None:
        standards.append(
            DevelopmentStandard(
                id=f"{district}:road_right_of_way_ft",
                district_id=district,
                standard_type="road_right_of_way_ft",
                value=road_right_of_way_ft,
                units="ft",
            )
        )
    if min_depth_ft is not None:
        standards.append(
            DevelopmentStandard(
                id=f"{district}:min_depth_ft",
                district_id=district,
                standard_type="min_depth_ft",
                value=min_depth_ft,
                units="ft",
            )
        )
    return standards


def _profiles() -> list[ConstraintProfile]:
    district = "R-1"
    base = {
        "district": district,
        "min_lot_size_sqft": 5500.0,
        "max_units_per_acre": 6.0,
        "setbacks": {"front": 20.0, "side": 8.0, "rear": 15.0},
    }
    return [
        ConstraintProfile(
            profile_id="baseline_simple",
            description="Core constraints only: min lot size, density cap, setbacks.",
            zoning_kwargs=base,
        ),
        ConstraintProfile(
            profile_id="enriched_frontage_only",
            description="Adds only min frontage constraint.",
            zoning_kwargs={
                **base,
                "min_frontage_ft": 50.0,
            },
        ),
        ConstraintProfile(
            profile_id="enriched_row_only",
            description="Adds only road ROW width constraint.",
            zoning_kwargs={
                **base,
                "road_right_of_way_ft": 44.0,
            },
        ),
        ConstraintProfile(
            profile_id="enriched_operational",
            description="Adds operational realism constraints: min frontage + road ROW.",
            zoning_kwargs={
                **base,
                "min_frontage_ft": 50.0,
                "road_right_of_way_ft": 44.0,
                "standards": _standards_from_values(
                    district,
                    min_lot_size_sqft=5500.0,
                    max_units_per_acre=6.0,
                    front_setback_ft=20.0,
                    side_setback_ft=8.0,
                    rear_setback_ft=15.0,
                    min_frontage_ft=50.0,
                    road_right_of_way_ft=44.0,
                ),
            },
        ),
        ConstraintProfile(
            profile_id="enriched_metadata_heavy",
            description="Adds non-operational constraints (height, lot coverage, overlays) plus standards payload.",
            zoning_kwargs={
                **base,
                "height_limit_ft": 35.0,
                "lot_coverage_max": 0.45,
                "allowed_uses": ["single_family"],
                "overlays": ["hillside-review"],
                "standards": _standards_from_values(
                    district,
                    min_lot_size_sqft=5500.0,
                    max_units_per_acre=6.0,
                    front_setback_ft=20.0,
                    side_setback_ft=8.0,
                    rear_setback_ft=15.0,
                    min_depth_ft=80.0,
                ),
            },
        ),
        ConstraintProfile(
            profile_id="enriched_strict",
            description="Strict operational enrichments to test over-constraint risk.",
            zoning_kwargs={
                **base,
                "setbacks": {"front": 25.0, "side": 12.0, "rear": 20.0},
                "min_frontage_ft": 60.0,
                "road_right_of_way_ft": 50.0,
                "standards": _standards_from_values(
                    district,
                    min_lot_size_sqft=5500.0,
                    max_units_per_acre=6.0,
                    front_setback_ft=25.0,
                    side_setback_ft=12.0,
                    rear_setback_ft=20.0,
                    min_frontage_ft=60.0,
                    road_right_of_way_ft=50.0,
                    min_depth_ft=90.0,
                ),
            },
        ),
    ]


def _count_disconnected_road_segments(candidate) -> int:
    segments = list(getattr(candidate.result, "segments", []) or [])
    if not segments:
        return 0
    adjacency: dict[tuple[float, float], set[tuple[float, float]]] = {}
    edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for segment in segments:
        start = (round(float(segment.start[0]), 3), round(float(segment.start[1]), 3))
        end = (round(float(segment.end[0]), 3), round(float(segment.end[1]), 3))
        edges.append((start, end))
        adjacency.setdefault(start, set()).add(end)
        adjacency.setdefault(end, set()).add(start)
    visited: set[tuple[float, float]] = set()
    component: dict[tuple[float, float], int] = {}
    sizes: dict[int, int] = {}
    cid = 0
    for node in adjacency:
        if node in visited:
            continue
        stack = [node]
        visited.add(node)
        count = 0
        while stack:
            current = stack.pop()
            component[current] = cid
            count += 1
            for nxt in adjacency[current]:
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
        sizes[cid] = count
        cid += 1
    if len(sizes) <= 1:
        return 0
    main = max(sizes, key=sizes.get)
    return sum(
        1
        for start, end in edges
        if component.get(start) != main or component.get(end) != main
    )


def _safe_mean(values: list[float]) -> float:
    return float(statistics.mean(values)) if values else 0.0


def run_experiment() -> dict[str, Any]:
    cases = load_layout_benchmark_cases()
    profiles = _profiles()
    records: list[dict[str, Any]] = []

    for profile in profiles:
        for case in cases:
            zoning = ZoningRules(
                parcel_id=case.parcel.parcel_id,
                jurisdiction=case.parcel.jurisdiction,
                **profile.zoning_kwargs,
            )
            started = time.perf_counter()
            try:
                parcel_polygon_local, projection = layout_service._geometry_to_local_feet(case.parcel.geometry)
                parcel_area_sqft = float(case.parcel.area_sqft or parcel_polygon_local.area)
                solver_constraints, search_heuristics = layout_service._build_layout_parameters(
                    case.parcel,
                    zoning,
                    parcel_area_sqft,
                )
                candidates = layout_service.run_layout_search(
                    parcel_polygon=parcel_polygon_local,
                    area_sqft=parcel_area_sqft,
                    to_lnglat=lambda x_ft, y_ft: layout_service._to_geojson_coords([(x_ft, y_ft)], projection)[0],
                    n_candidates=case.max_candidates,
                    n_top=3,
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
                    raise RuntimeError("no candidates")
                candidate = candidates[0]
                layout_service._validate_candidate_constraints(case.parcel.parcel_id, candidate, solver_constraints)
                metrics = dict(candidate.result.metrics)
                units = int(metrics.get("lot_count", 0))
                road_ft = float(metrics.get("total_road_ft", 0.0))
                records.append(
                    {
                        "case_id": case.case_id,
                        "profile_id": profile.profile_id,
                        "status": "success",
                        "runtime_s": time.perf_counter() - started,
                        "units": units,
                        "road_length_ft": road_ft,
                        "road_eff_units_per_1000ft": (units / max(road_ft / 1000.0, 1e-9)) if road_ft > 0 else 0.0,
                        "layout_score": float(layout_service._candidate_rank_key(candidate)[0]) * -1.0,
                        "avg_lot_compactness": float(metrics.get("avg_lot_compactness", 0.0)),
                        "compliance_rate": float(metrics.get("compliance_rate", 0.0)),
                        "dev_area_ratio": float(metrics.get("dev_area_ratio", 0.0)),
                        "disconnected_road_segments": _count_disconnected_road_segments(candidate),
                        "invalid_lot_count": 0,
                        "constraint_violations": [],
                    }
                )
            except Exception as exc:
                records.append(
                    {
                        "case_id": case.case_id,
                        "profile_id": profile.profile_id,
                        "status": "failure",
                        "runtime_s": time.perf_counter() - started,
                        "units": 0,
                        "road_length_ft": 0.0,
                        "road_eff_units_per_1000ft": 0.0,
                        "layout_score": 0.0,
                        "avg_lot_compactness": 0.0,
                        "compliance_rate": 0.0,
                        "dev_area_ratio": 0.0,
                        "disconnected_road_segments": 0,
                        "invalid_lot_count": 1,
                        "constraint_violations": [str(exc)],
                    }
                )

    by_profile: dict[str, dict[str, Any]] = {}
    for profile in profiles:
        subset = [r for r in records if r["profile_id"] == profile.profile_id]
        success = [r for r in subset if r["status"] == "success"]
        by_profile[profile.profile_id] = {
            "description": profile.description,
            "total_cases": len(subset),
            "success_rate": len(success) / max(len(subset), 1),
            "avg_units": _safe_mean([float(r["units"]) for r in success]),
            "avg_road_eff_units_per_1000ft": _safe_mean([float(r["road_eff_units_per_1000ft"]) for r in success]),
            "avg_lot_compactness": _safe_mean([float(r["avg_lot_compactness"]) for r in success]),
            "avg_compliance_rate": _safe_mean([float(r["compliance_rate"]) for r in success]),
            "avg_dev_area_ratio": _safe_mean([float(r["dev_area_ratio"]) for r in success]),
            "avg_disconnected_road_segments": _safe_mean([float(r["disconnected_road_segments"]) for r in success]),
            "avg_runtime_s": _safe_mean([float(r["runtime_s"]) for r in subset]),
            "failure_count": len(subset) - len(success),
        }

    baseline = by_profile["baseline_simple"]
    comparisons: dict[str, dict[str, float]] = {}
    for profile_id, summary in by_profile.items():
        if profile_id == "baseline_simple":
            continue
        comparisons[profile_id] = {
            "delta_success_rate": summary["success_rate"] - baseline["success_rate"],
            "delta_avg_units": summary["avg_units"] - baseline["avg_units"],
            "delta_avg_road_eff_units_per_1000ft": summary["avg_road_eff_units_per_1000ft"]
            - baseline["avg_road_eff_units_per_1000ft"],
            "delta_avg_lot_compactness": summary["avg_lot_compactness"] - baseline["avg_lot_compactness"],
            "delta_avg_compliance_rate": summary["avg_compliance_rate"] - baseline["avg_compliance_rate"],
            "delta_avg_dev_area_ratio": summary["avg_dev_area_ratio"] - baseline["avg_dev_area_ratio"],
        }

    return {
        "experiment": "constraint_enrichment_layout_quality",
        "total_cases": len(cases),
        "profiles": by_profile,
        "comparisons_vs_baseline": comparisons,
        "records": records,
    }


def main() -> int:
    report = run_experiment()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved experiment report to {OUTPUT_PATH}")
    for profile_id, summary in report["profiles"].items():
        print(
            f"[{profile_id}] success={summary['success_rate']:.2%} "
            f"units={summary['avg_units']:.2f} "
            f"road_eff={summary['avg_road_eff_units_per_1000ft']:.2f} "
            f"compact={summary['avg_lot_compactness']:.3f} "
            f"compliance={summary['avg_compliance_rate']:.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
