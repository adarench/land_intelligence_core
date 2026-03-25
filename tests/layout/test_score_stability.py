from __future__ import annotations

import json
import importlib
from pathlib import Path

from bedrock.contracts.parcel import Parcel
from bedrock.contracts.zoning_rules import ZoningRules
from bedrock.services.layout_service import search_layout

lot_subdivision_module = importlib.import_module("gis_layout_runtime.lot_subdivision")
SubdivisionResult = lot_subdivision_module.SubdivisionResult
ValidationSummary = lot_subdivision_module.ValidationSummary
score_subdivision = lot_subdivision_module.score_subdivision


def _exp_lehi_inputs() -> tuple[Parcel, ZoningRules]:
    config = json.loads(Path("bedrock/benchmarks/http_validation_config.json").read_text())
    case = next(item for item in config["cases"] if item["case_id"] == "exp-lehi-001")
    run_payload = json.loads(Path("bedrock/runs/b56d2990-edf8-4c8d-8e97-92e00daa8f33.json").read_text())
    zoning = dict(run_payload["zoning_result"])
    zoning["metadata"] = None
    zoning["standards"] = [{**standard, "metadata": None} for standard in zoning.get("standards", [])]
    parcel = Parcel.model_validate(
        {
            "parcel_id": "exp-lehi-001",
            "geometry": case["geometry"],
            "area_sqft": 100000.0,
            "jurisdiction": "Lehi",
            "zoning_district": "TH-5",
            "utilities": [],
            "access_points": [],
            "topography": {},
            "existing_structures": [],
        }
    )
    return parcel, ZoningRules.model_validate({**zoning, "parcel_id": parcel.parcel_id})


def test_score_is_identical_for_same_input_across_ten_runs() -> None:
    parcel, zoning = _exp_lehi_inputs()
    scores = [float(search_layout(parcel, zoning, max_candidates=8).score or 0.0) for _ in range(10)]
    assert len(set(scores)) == 1


def test_score_changes_smoothly_under_small_parcel_perturbation() -> None:
    parcel, zoning = _exp_lehi_inputs()
    baseline = search_layout(parcel, zoning, max_candidates=8)

    shifted_coords = []
    for ring in parcel.geometry["coordinates"]:
        shifted_coords.append([[point[0] + 0.000001, point[1]] for point in ring])
    shifted_parcel = parcel.model_copy(update={"geometry": {"type": "Polygon", "coordinates": shifted_coords}})
    shifted = search_layout(shifted_parcel, zoning.model_copy(update={"parcel_id": shifted_parcel.parcel_id}), max_candidates=8)

    assert abs(float(baseline.score or 0.0) - float(shifted.score or 0.0)) <= 0.25


def _rank(values: list[float]) -> list[int]:
    ordered = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0] * len(values)
    for rank, index in enumerate(ordered, start=1):
        ranks[index] = rank
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float:
    x_ranks = _rank(xs)
    y_ranks = _rank(ys)
    n = len(xs)
    if n <= 1:
        return 1.0
    diff_sq = sum((x_ranks[i] - y_ranks[i]) ** 2 for i in range(n))
    return 1.0 - ((6.0 * diff_sq) / (n * ((n**2) - 1)))


def _synthetic_result(*, lot_count: int, total_road_ft: float, compliance_rate: float = 0.9) -> SubdivisionResult:
    return SubdivisionResult(
        lots=[],
        segments=[],
        strips=[],
        validation=ValidationSummary(0, 0, 0, 0, 0, 0),
        metrics={
            "lot_count": lot_count,
            "max_units": 12,
            "total_road_ft": total_road_ft,
            "dev_area_ratio": 0.42,
            "avg_lot_compactness": 0.62,
            "compliance_rate": compliance_rate,
        },
    )


def test_score_ranking_tracks_unit_ranking_strongly() -> None:
    results = [
        _synthetic_result(lot_count=4, total_road_ft=950.0),
        _synthetic_result(lot_count=6, total_road_ft=980.0),
        _synthetic_result(lot_count=8, total_road_ft=1020.0),
        _synthetic_result(lot_count=10, total_road_ft=1050.0),
        _synthetic_result(lot_count=12, total_road_ft=1080.0),
    ]
    unit_counts = [float(item.metrics["lot_count"]) for item in results]
    scores = [score_subdivision(item) for item in results]
    assert _spearman(unit_counts, scores) >= 0.9


def test_lower_yield_layout_is_not_rescued_by_secondary_terms() -> None:
    higher_yield = _synthetic_result(lot_count=12, total_road_ft=1000.0, compliance_rate=0.8)
    lower_yield = _synthetic_result(lot_count=10, total_road_ft=1000.0, compliance_rate=1.0)
    lower_yield.metrics["dev_area_ratio"] = 0.60
    lower_yield.metrics["avg_lot_compactness"] = 0.95

    assert score_subdivision(higher_yield) > score_subdivision(lower_yield)
