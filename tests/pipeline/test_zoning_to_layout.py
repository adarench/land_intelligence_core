from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[2]
BEDROCK_ROOT = ROOT / "bedrock"
for candidate in (ROOT, BEDROCK_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from bedrock.contracts.parcel import Parcel
import bedrock.services.layout_service as layout_service_module
from bedrock.services.layout_service import generate_candidates, search_layout
from bedrock.services.zoning_service import ZoningService
from zoning_data_scraper.services import rule_normalization as rule_normalization_module
from zoning_data_scraper.services import zoning_code_rules as zoning_code_rules_module
from zoning_data_scraper.services import zoning_overlay as zoning_overlay_module


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _geojson_polygon(coords: list[tuple[float, float]]) -> dict:
    return {"type": "Polygon", "coordinates": [[list(point) for point in coords]]}


@dataclass(frozen=True)
class LayoutIntegrationCase:
    jurisdiction: str
    district: str
    parcel_id: str
    parcel_coords: list[tuple[float, float]]
    min_lot_size_sqft: float
    max_units_per_acre: float
    front_setback_ft: float
    side_setback_ft: float
    rear_setback_ft: float

    @property
    def parcel(self) -> Parcel:
        min_x = min(point[0] for point in self.parcel_coords[:-1])
        min_y = min(point[1] for point in self.parcel_coords[:-1])
        max_x = max(point[0] for point in self.parcel_coords[:-1])
        max_y = max(point[1] for point in self.parcel_coords[:-1])
        return Parcel(
            parcel_id=self.parcel_id,
            geometry=_geojson_polygon(self.parcel_coords),
            area_sqft=(max_x - min_x) * (max_y - min_y),
            centroid=[(min_x + max_x) / 2.0, (min_y + max_y) / 2.0],
            bounding_box=[min_x, min_y, max_x, max_y],
            jurisdiction=self.jurisdiction,
            utilities=[],
            access_points=[],
            topography={},
            existing_structures=[],
        )

    @property
    def expected_lot_depth_ft(self) -> float:
        return max(40.0, 110.0 - self.front_setback_ft - self.rear_setback_ft)


CASES = (
    LayoutIntegrationCase(
        jurisdiction="Salt Lake City",
        district="R-1-7000",
        parcel_id="slc-r1-layout",
        parcel_coords=[(100.0, 100.0), (100.0, 600.0), (600.0, 600.0), (600.0, 100.0), (100.0, 100.0)],
        min_lot_size_sqft=7000.0,
        max_units_per_acre=6.2,
        front_setback_ft=10.0,
        side_setback_ft=8.0,
        rear_setback_ft=10.0,
    ),
    LayoutIntegrationCase(
        jurisdiction="Salt Lake City",
        district="RMF-35",
        parcel_id="slc-rmf-layout",
        parcel_coords=[(1100.0, 100.0), (1100.0, 600.0), (1600.0, 600.0), (1600.0, 100.0), (1100.0, 100.0)],
        min_lot_size_sqft=3000.0,
        max_units_per_acre=14.0,
        front_setback_ft=5.0,
        side_setback_ft=5.0,
        rear_setback_ft=5.0,
    ),
    LayoutIntegrationCase(
        jurisdiction="Lehi",
        district="TH-5",
        parcel_id="lehi-th-layout",
        parcel_coords=[(100.0, 1100.0), (100.0, 1600.0), (600.0, 1600.0), (600.0, 1100.0), (100.0, 1100.0)],
        min_lot_size_sqft=5000.0,
        max_units_per_acre=8.5,
        front_setback_ft=15.0,
        side_setback_ft=5.0,
        rear_setback_ft=10.0,
    ),
    LayoutIntegrationCase(
        jurisdiction="Draper",
        district="R3",
        parcel_id="draper-r3-layout",
        parcel_coords=[(1100.0, 1100.0), (1100.0, 1600.0), (1600.0, 1600.0), (1600.0, 1100.0), (1100.0, 1100.0)],
        min_lot_size_sqft=8000.0,
        max_units_per_acre=5.4,
        front_setback_ft=10.0,
        side_setback_ft=8.0,
        rear_setback_ft=5.0,
    ),
)


@pytest.fixture
def normalized_rule_dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dataset_root = tmp_path / "zoning_dataset_sample"
    normalized_root = tmp_path / "normalized_rules"
    monkeypatch.setattr(zoning_code_rules_module, "NORMALIZED_RULES_ROOT", normalized_root)

    city_specs = {
        "salt-lake-city": {
            "name": "Salt Lake City",
            "districts": [
                {
                    "zoning_code": "R-1-7000",
                    "geometry": _geojson_polygon([(0.0, 0.0), (0.0, 900.0), (900.0, 900.0), (900.0, 0.0), (0.0, 0.0)]),
                },
                {
                    "zoning_code": "RMF-35",
                    "geometry": _geojson_polygon([(1000.0, 0.0), (1000.0, 900.0), (1900.0, 900.0), (1900.0, 0.0), (1000.0, 0.0)]),
                },
            ],
        },
        "lehi": {
            "name": "Lehi",
            "districts": [
                {
                    "zoning_code": "TH-5",
                    "geometry": _geojson_polygon([(0.0, 1000.0), (0.0, 1900.0), (900.0, 1900.0), (900.0, 1000.0), (0.0, 1000.0)]),
                }
            ],
        },
        "draper": {
            "name": "Draper",
            "districts": [
                {
                    "zoning_code": "R3",
                    "geometry": _geojson_polygon([(1000.0, 1000.0), (1000.0, 1900.0), (1900.0, 1900.0), (1900.0, 1000.0), (1000.0, 1000.0)]),
                }
            ],
        },
    }

    for slug, spec in city_specs.items():
        city_dir = dataset_root / slug
        _write_json(
            city_dir / "metadata.json",
            {
                "city": spec["name"],
                "city_slug": slug,
                "county_name": "Salt Lake" if slug != "lehi" else "Utah",
                "feature_count": len(spec["districts"]),
                "layer_sources": [{"source_layer": "zoning", "name": "Official Zoning"}],
            },
        )
        _write_json(
            city_dir / "normalized_zoning.json",
            [
                {
                    "city": spec["name"],
                    "zoning_code": district["zoning_code"],
                    "zoning_name": district["zoning_code"],
                    "density": None,
                    "source_layer": "zoning",
                    "geometry": district["geometry"],
                }
                for district in spec["districts"]
            ],
        )

    _write_json(
        normalized_root / "salt-lake-city.json",
        {
            "jurisdiction": "Salt Lake City",
            "jurisdiction_slug": "salt-lake-city",
            "districts": {
                "R-1-7000": {
                    "district": "R-1-7000",
                    "min_lot_size_sqft": 7000.0,
                    "max_units_per_acre": 6.2,
                    "setbacks": {"front": 10.0, "side": 8.0, "rear": 10.0},
                    "height_limit_ft": 35.0,
                    "lot_coverage_max": 0.45,
                },
                "RMF-35": {
                    "district": "RMF-35",
                    "min_lot_size_sqft": 3000.0,
                    "max_units_per_acre": 14.0,
                    "setbacks": {"front": 5.0, "side": 5.0, "rear": 5.0},
                    "height_limit_ft": 45.0,
                    "lot_coverage_max": 0.65,
                },
            },
        },
    )
    _write_json(
        normalized_root / "lehi.json",
        {
            "jurisdiction": "Lehi",
            "jurisdiction_slug": "lehi",
            "districts": {
                "TH-5": {
                    "district": "TH-5",
                    "min_lot_size_sqft": 5000.0,
                    "max_units_per_acre": 8.5,
                    "setbacks": {"front": 15.0, "side": 5.0, "rear": 10.0},
                    "height_limit_ft": 40.0,
                    "lot_coverage_max": 0.55,
                }
            },
        },
    )
    _write_json(
        normalized_root / "draper.json",
        {
            "jurisdiction": "Draper",
            "jurisdiction_slug": "draper",
            "districts": {
                "R3": {
                    "district": "R3",
                    "min_lot_size_sqft": 8000.0,
                    "max_units_per_acre": 5.4,
                    "setbacks": {"front": 10.0, "side": 8.0, "rear": 5.0},
                    "height_limit_ft": 35.0,
                    "lot_coverage_max": 0.4,
                }
            },
        },
    )

    zoning_overlay_module._dataset_info.cache_clear()
    zoning_overlay_module._zoning_features.cache_clear()
    zoning_overlay_module._overlay_features.cache_clear()
    zoning_overlay_module._build_strtree.cache_clear()
    zoning_overlay_module._filtered_zoning_rows.cache_clear()
    zoning_overlay_module._layer_source_index.cache_clear()
    rule_normalization_module._load_rule_index.cache_clear()
    rule_normalization_module._load_rule_index_with_sources.cache_clear()
    zoning_code_rules_module._load_normalized_rule_index.cache_clear()
    zoning_code_rules_module._load_normalized_rules_document.cache_clear()
    return tmp_path


def _lot_metrics(layout) -> dict[str, float]:
    lot_areas = [float(shape(geometry).area) for geometry in layout.lot_geometries]
    return {
        "min_lot_area": min(lot_areas),
    }


def _run_layout_candidate(case: LayoutIntegrationCase, zoning_rules, *, use_prior: bool = True, max_candidates: int = 8):
    translation = layout_service_module.translate_zoning_for_layout(case.parcel, zoning_rules)
    parcel_polygon_local, _projection = layout_service_module._geometry_to_local_feet(case.parcel.geometry)
    parcel_area_sqft = float(case.parcel.area_sqft or parcel_polygon_local.area)
    solver_constraints, _search_heuristics = layout_service_module._build_layout_parameters(
        case.parcel,
        translation.zoning,
        parcel_area_sqft,
        additional_constraints=translation.additional_constraints,
    )
    candidates = generate_candidates(case.parcel, zoning_rules, max_candidates=max_candidates)
    assert candidates, f"No layout candidates generated for {case.jurisdiction}:{case.district}"
    return candidates[0], solver_constraints


def _candidate_metrics(candidate, solver_constraints) -> dict[str, float]:
    lot_areas = [float(lot.area_sqft) for lot in candidate.result.lots]
    lot_depths = [float(lot.depth_ft) for lot in candidate.result.lots]
    frontages = [float(lot.frontage_ft) for lot in candidate.result.lots]
    return {
        "units": float(candidate.result.metrics.get("lot_count", len(candidate.result.lots))),
        "road_length_ft": float(candidate.result.metrics.get("total_road_ft", 0.0)),
        "min_lot_area_sqft": min(lot_areas),
        "max_lot_depth_ft": max(lot_depths),
        "min_frontage_ft": min(frontages),
        "min_buildable_width_ft_one_side": min(max(0.0, frontage - solver_constraints.side_setback_ft) for frontage in frontages),
        "min_buildable_width_ft_two_side": min(max(0.0, frontage - (2.0 * solver_constraints.side_setback_ft)) for frontage in frontages),
    }


def _assert_candidate_compliance(case: LayoutIntegrationCase, candidate, solver_constraints) -> dict[str, float]:
    metrics = _candidate_metrics(candidate, solver_constraints)
    required_buildable_width_ft = case.min_lot_size_sqft / case.expected_lot_depth_ft

    assert metrics["units"] > 0
    assert metrics["min_lot_area_sqft"] + 1e-6 >= case.min_lot_size_sqft
    assert metrics["max_lot_depth_ft"] <= case.expected_lot_depth_ft * 1.02
    assert metrics["min_buildable_width_ft_two_side"] + 1e-6 >= required_buildable_width_ft
    return metrics


@pytest.mark.parametrize("case", CASES, ids=lambda case: f"{case.jurisdiction}-{case.district}")
def test_zoning_rules_integrate_with_layout_search(normalized_rule_dataset: Path, case: LayoutIntegrationCase) -> None:
    zoning_result = ZoningService(dataset_root=normalized_rule_dataset).lookup(case.parcel)
    layout = search_layout(case.parcel, zoning_result.rules, max_candidates=8)
    candidate, solver_constraints = _run_layout_candidate(case, zoning_result.rules)
    metrics = _assert_candidate_compliance(case, candidate, solver_constraints)

    assert zoning_result.district == case.district
    assert layout.unit_count > 0

    max_units_by_lot_size = math.floor(case.parcel.area_sqft / case.min_lot_size_sqft)
    max_units_by_density = math.floor((case.parcel.area_sqft / 43560.0) * case.max_units_per_acre)
    assert layout.unit_count <= max_units_by_lot_size
    assert layout.unit_count <= max_units_by_density

    public_metrics = _lot_metrics(layout)
    assert public_metrics["min_lot_area"] + 1e-6 >= case.min_lot_size_sqft
    assert metrics["road_length_ft"] == pytest.approx(layout.road_length_ft)


@pytest.mark.parametrize("case", CASES, ids=lambda case: f"fallback-{case.jurisdiction}-{case.district}")
def test_zoning_layout_fallback_keeps_constraints(normalized_rule_dataset: Path, case: LayoutIntegrationCase) -> None:
    zoning_result = ZoningService(dataset_root=normalized_rule_dataset).lookup(case.parcel)
    real_run_layout_search = layout_service_module.run_layout_search
    call_count = {"value": 0}

    def _force_fallback(*args, **kwargs):
        call_count["value"] += 1
        if call_count["value"] == 1:
            return []
        return real_run_layout_search(*args, **kwargs)

    with patch.object(layout_service_module, "run_layout_search", side_effect=_force_fallback):
        layout = search_layout(case.parcel, zoning_result.rules, max_candidates=8)

    assert call_count["value"] >= 2
    assert layout.unit_count > 0

    fallback_candidate, solver_constraints = _run_layout_candidate(case, zoning_result.rules, use_prior=False, max_candidates=8)
    _assert_candidate_compliance(case, fallback_candidate, solver_constraints)


def test_zoning_to_layout_reports_rule_inconsistencies(normalized_rule_dataset: Path) -> None:
    inconsistencies: list[str] = []
    metrics_by_case: dict[str, dict[str, float]] = {}

    for case in CASES:
        try:
            zoning_result = ZoningService(dataset_root=normalized_rule_dataset).lookup(case.parcel)
            layout = search_layout(case.parcel, zoning_result.rules, max_candidates=8)
            candidate, solver_constraints = _run_layout_candidate(case, zoning_result.rules)
            metrics = _assert_candidate_compliance(case, candidate, solver_constraints)
            metrics_by_case[f"{case.jurisdiction}:{case.district}"] = {
                "units": float(layout.unit_count),
                "road_length_ft": float(layout.road_length_ft),
                "min_lot_area_sqft": metrics["min_lot_area_sqft"],
                "max_lot_depth_ft": metrics["max_lot_depth_ft"],
                "min_buildable_width_ft_two_side": metrics["min_buildable_width_ft_two_side"],
            }
        except Exception as exc:  # pragma: no cover - exercised only on integration regressions
            inconsistencies.append(f"{case.jurisdiction}:{case.district}: {exc}")

    assert not inconsistencies, json.dumps(
        {
            "layout_success_metrics": metrics_by_case,
            "zoning_rule_inconsistencies": inconsistencies,
        },
        indent=2,
        sort_keys=True,
    )
