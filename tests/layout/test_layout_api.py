from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BEDROCK_ROOT = WORKSPACE_ROOT / "bedrock"

for candidate in (WORKSPACE_ROOT, BEDROCK_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from bedrock.api.layout_api import app  # type: ignore  # noqa: E402
from bedrock.contracts.layout_candidate_batch import LayoutCandidateBatch, LayoutSearchPlan  # type: ignore  # noqa: E402
from bedrock.contracts.layout_result import LayoutResult  # type: ignore  # noqa: E402
from bedrock.contracts.parcel import Parcel  # type: ignore  # noqa: E402
from bedrock.contracts.validators import build_zoning_rules_from_lookup  # type: ignore  # noqa: E402
from bedrock.contracts.zoning_rules import ZoningRules  # type: ignore  # noqa: E402
from bedrock.services.layout_export_service import (  # type: ignore  # noqa: E402
    _to_export_layout,
    export_layout_artifact,
)
from bedrock.services.layout_service import LayoutSearchError, generate_candidates, search_layout, search_layout_debug  # type: ignore  # noqa: E402


def _exp_lehi_case_payload() -> tuple[dict, dict]:
    config = json.loads((BEDROCK_ROOT / "benchmarks" / "http_validation_config.json").read_text())
    case = next(item for item in config["cases"] if item["case_id"] == "exp-lehi-001")
    run_payload = json.loads((BEDROCK_ROOT / "runs" / "b56d2990-edf8-4c8d-8e97-92e00daa8f33.json").read_text())
    zoning = dict(run_payload["zoning_result"])
    zoning["metadata"] = None
    zoning["standards"] = [
        {**standard, "metadata": None}
        for standard in zoning.get("standards", [])
    ]
    return case, zoning


class LayoutApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.request_payload = {
            "parcel": {
                "parcel_id": "parcel-123",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [-111.9, 40.7],
                        [-111.9, 40.701],
                        [-111.899, 40.701],
                        [-111.899, 40.7],
                        [-111.9, 40.7],
                    ]],
                },
                "area": 120000.0,
                "jurisdiction": "Salt Lake County",
                "zoning_district": "R-1",
                "utilities": [],
                "access_points": [],
                "topography": {},
                "existing_structures": [],
                "metadata": None,
            },
            "zoning": {
                "parcel_id": "parcel-123",
                "district": "R-1",
                "min_lot_size_sqft": 6000.0,
                "max_units_per_acre": 5.0,
                "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
            },
            "max_candidates": 12,
        }

    def test_layout_search_endpoint_returns_canonical_layout_result(self) -> None:
        with patch("bedrock.api.layout_api.search_layout") as mock_search:
            mock_search.return_value = LayoutResult(
                layout_id="layout-1",
                parcel_id="parcel-123",
                unit_count=14,
                lot_geometries=[{"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}],
                road_geometries=[{"type": "LineString", "coordinates": [[0, 0], [1, 1]]}],
                road_length_ft=820.5,
                open_space_area_sqft=1000.0,
                utility_length_ft=0.0,
                score=0.73,
            )
            response = self.client.post("/layout/search", json=self.request_payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json().keys()),
            {
                "schema_name",
                "schema_version",
                "layout_id",
                "parcel_id",
                "unit_count",
                "road_length_ft",
                "lot_geometries",
                "road_geometries",
                "open_space_area_sqft",
                "utility_length_ft",
                "score",
                "buildable_area_sqft",
                "metadata",
            },
        )
        self.assertEqual(response.json()["unit_count"], 14)
        self.assertEqual(response.json()["parcel_id"], "parcel-123")

    def test_layout_search_endpoint_maps_validation_errors(self) -> None:
        with patch("bedrock.api.layout_api.search_layout", side_effect=ValueError("bad parcel geometry")):
            response = self.client.post("/layout/search", json=self.request_payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["error"], "invalid_layout_input")
        self.assertEqual(response.json()["detail"]["message"], "bad parcel geometry")

    def test_layout_search_endpoint_maps_deterministic_failure_codes(self) -> None:
        with patch(
            "bedrock.api.layout_api.search_layout",
            side_effect=LayoutSearchError("no_viable_layout", "No viable layouts generated for parcel parcel-123"),
        ):
            response = self.client.post("/layout/search", json=self.request_payload)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["error"], "no_viable_layout")

    def test_layout_search_endpoint_invalid_parcel_payload_returns_422(self) -> None:
        invalid = dict(self.request_payload)
        invalid["parcel"] = dict(self.request_payload["parcel"])
        invalid["parcel"]["area"] = -1.0

        response = self.client.post("/layout/search", json=invalid)

        self.assertEqual(response.status_code, 422)

    def test_layout_search_endpoint_invalid_zoning_payload_returns_400(self) -> None:
        invalid = dict(self.request_payload)
        invalid["zoning"] = dict(self.request_payload["zoning"])
        invalid["zoning"]["setbacks"] = {"front": 25.0, "side": 0.0, "rear": 20.0}

        response = self.client.post("/layout/search", json=invalid)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["error"], "non_usable_zoning")

    def test_layout_search_endpoint_rejects_parcel_id_mismatch(self) -> None:
        mismatched = dict(self.request_payload)
        mismatched["zoning"] = dict(self.request_payload["zoning"])
        mismatched["zoning"]["parcel_id"] = "parcel-other"

        response = self.client.post("/layout/search", json=mismatched)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["error"], "invalid_layout_input")
        self.assertIn("Contract mismatch", response.json()["detail"]["message"])

    def test_layout_candidates_endpoint_returns_ranked_batch(self) -> None:
        batch = LayoutCandidateBatch(
            parcel_id="parcel-123",
            search_plan=LayoutSearchPlan(
                label="broad_sampling",
                strategies=["grid", "spine-road"],
                max_candidates=12,
                max_layouts=2,
            ),
            candidate_count_generated=12,
            candidate_count_valid=2,
            layouts=[
                LayoutResult(
                    layout_id="layout-1",
                    parcel_id="parcel-123",
                    unit_count=12,
                    lot_geometries=[{"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}],
                    road_geometries=[{"type": "LineString", "coordinates": [[0, 0], [1, 1]]}],
                    road_length_ft=820.5,
                    open_space_area_sqft=1000.0,
                    utility_length_ft=0.0,
                    score=0.73,
                ),
                LayoutResult(
                    layout_id="layout-2",
                    parcel_id="parcel-123",
                    unit_count=10,
                    lot_geometries=[{"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}],
                    road_geometries=[{"type": "LineString", "coordinates": [[0, 0], [1, 1]]}],
                    road_length_ft=760.0,
                    open_space_area_sqft=1200.0,
                    utility_length_ft=0.0,
                    score=0.69,
                ),
            ],
            search_debug={"attempt_profile": "broad_sampling"},
        )

        with patch("bedrock.api.layout_api.search_layout_candidates_debug", return_value=batch):
            response = self.client.post(
                "/layout/candidates",
                json={**self.request_payload, "label": "broad_sampling", "max_layouts": 2, "strategies": ["grid", "spine-road"]},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema_name"], "LayoutCandidateBatch")
        self.assertEqual(payload["parcel_id"], "parcel-123")
        self.assertEqual(payload["candidate_count_generated"], 12)
        self.assertEqual(payload["candidate_count_valid"], 2)
        self.assertEqual(len(payload["layouts"]), 2)
        self.assertEqual(payload["search_plan"]["label"], "broad_sampling")

    def test_layout_export_endpoint_returns_dxf_file(self) -> None:
        export_payload = {
            "parcel": self.request_payload["parcel"],
            "layout": {
                "layout_id": "layout-export-1",
                "parcel_id": "parcel-123",
                "unit_count": 2,
                "lot_geometries": [
                    {
                        "type": "Polygon",
                        "coordinates": [[
                            [-111.9, 40.7],
                            [-111.9, 40.7004],
                            [-111.8996, 40.7004],
                            [-111.8996, 40.7],
                            [-111.9, 40.7],
                        ]],
                    },
                    {
                        "type": "Polygon",
                        "coordinates": [[
                            [-111.8996, 40.7],
                            [-111.8996, 40.7004],
                            [-111.8992, 40.7004],
                            [-111.8992, 40.7],
                            [-111.8996, 40.7],
                        ]],
                    },
                ],
                "road_geometries": [
                    {
                        "type": "LineString",
                        "coordinates": [[-111.8998, 40.7], [-111.8998, 40.701]],
                    }
                ],
                "road_length_ft": 240.0,
                "open_space_area_sqft": 0.0,
                "utility_length_ft": 0.0,
                "score": 0.91,
                "metadata": None,
            },
            "zoning": self.request_payload["zoning"],
            "format": "dxf",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("bedrock.services.layout_export_service.EXPORT_ROOT", Path(tmpdir)):
                response = self.client.post("/layout/export", json=export_payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/dxf")
        self.assertIn("SECTION", response.text)

    def test_layout_export_service_is_deterministic_for_same_layout(self) -> None:
        parcel = Parcel.model_validate(self.request_payload["parcel"])
        zoning = ZoningRules.model_validate(self.request_payload["zoning"])
        layout = LayoutResult(
            layout_id="layout-export-deterministic",
            parcel_id="parcel-123",
            unit_count=1,
            lot_geometries=[
                {
                    "type": "Polygon",
                    "coordinates": [[
                        [-111.9, 40.7],
                        [-111.9, 40.7005],
                        [-111.8995, 40.7005],
                        [-111.8995, 40.7],
                        [-111.9, 40.7],
                    ]],
                }
            ],
            road_geometries=[
                {
                    "type": "LineString",
                    "coordinates": [[-111.89975, 40.7], [-111.89975, 40.701]],
                }
            ],
            road_length_ft=180.0,
            open_space_area_sqft=0.0,
            utility_length_ft=0.0,
            score=0.88,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("bedrock.services.layout_export_service.EXPORT_ROOT", Path(tmpdir)):
                artifact_one = export_layout_artifact(parcel, layout, export_format="dxf", zoning=zoning)
                artifact_two = export_layout_artifact(parcel, layout, export_format="dxf", zoning=zoning)
                bytes_one = artifact_one.path.read_bytes()
                bytes_two = artifact_two.path.read_bytes()

        self.assertEqual(bytes_one, bytes_two)
        self.assertEqual(
            hashlib.sha256(bytes_one).hexdigest(),
            hashlib.sha256(bytes_two).hexdigest(),
        )

    def test_layout_result_is_deterministic_without_runtime_metadata_timestamp(self) -> None:
        class FakeResult:
            metrics = {"lot_count": 2, "total_road_ft": 180.0}

        class FakeCandidate:
            def __init__(self) -> None:
                self.geojson = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[
                                    [-111.9, 40.7],
                                    [-111.9, 40.7004],
                                    [-111.8996, 40.7004],
                                    [-111.8996, 40.7],
                                    [-111.9, 40.7],
                                ]],
                            },
                            "properties": {"layer": "lots"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[-111.89975, 40.7], [-111.89975, 40.701]],
                            },
                            "properties": {"layer": "road"},
                        },
                    ],
                }
                self.result = FakeResult()
                self.score = 0.75

        with patch("bedrock.services.layout_service.run_layout_search", return_value=[FakeCandidate()]):
            first = search_layout(
                parcel=Parcel.model_validate(self.request_payload["parcel"]),
                zoning=ZoningRules.model_validate(self.request_payload["zoning"]),
                max_candidates=12,
            )
            second = search_layout(
                parcel=Parcel.model_validate(self.request_payload["parcel"]),
                zoning=ZoningRules.model_validate(self.request_payload["zoning"]),
                max_candidates=12,
            )

        self.assertEqual(first.model_dump(), second.model_dump())
        self.assertIsNone(first.metadata)

    def test_export_road_polygon_decomposition_matches_segment_level_ui_semantics(self) -> None:
        parcel = Parcel.model_validate(self.request_payload["parcel"])
        zoning = ZoningRules.model_validate({**self.request_payload["zoning"], "road_right_of_way_ft": 32.0})
        layout = LayoutResult(
            layout_id="layout-road-segments",
            parcel_id="parcel-123",
            unit_count=1,
            lot_geometries=[
                {
                    "type": "Polygon",
                    "coordinates": [[
                        [-111.9, 40.7],
                        [-111.9, 40.7005],
                        [-111.8995, 40.7005],
                        [-111.8995, 40.7],
                        [-111.9, 40.7],
                    ]],
                }
            ],
            road_geometries=[
                {
                    "type": "LineString",
                    "coordinates": [
                        [-111.8999, 40.7],
                        [-111.8999, 40.7002],
                        [-111.8997, 40.7002],
                    ],
                }
            ],
            road_length_ft=180.0,
            open_space_area_sqft=0.0,
            utility_length_ft=0.0,
            score=0.5,
        )

        export_layout = _to_export_layout(parcel, layout, zoning=zoning)

        self.assertEqual(len(export_layout.road), 2)
        first_points = export_layout.road[0].closed_points()
        second_points = export_layout.road[1].closed_points()
        self.assertEqual(len(first_points), 5)
        self.assertEqual(len(second_points), 5)

    def test_layout_id_changes_when_engine_geometry_changes_with_same_metrics(self) -> None:
        parcel = Parcel.model_validate(self.request_payload["parcel"])
        zoning = ZoningRules.model_validate(self.request_payload["zoning"])

        class FakeResult:
            def __init__(self) -> None:
                self.metrics = {"lot_count": 2, "total_road_ft": 180.0, "avg_lot_area_sqft": 5000.0}
                self.lots = []

        class FakeCandidate:
            def __init__(self, road_x: float) -> None:
                self.geojson = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[
                                    [-111.9, 40.7],
                                    [-111.9, 40.7004],
                                    [-111.8996, 40.7004],
                                    [-111.8996, 40.7],
                                    [-111.9, 40.7],
                                ]],
                            },
                            "properties": {"layer": "lots"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[road_x, 40.7], [road_x, 40.701]],
                            },
                            "properties": {"layer": "road"},
                        },
                    ],
                }
                self.result = FakeResult()
                self.score = 0.75

        state = {"calls": 0}

        def _side_effect(**_kwargs):
            state["calls"] += 1
            return [FakeCandidate(-111.89975 if state["calls"] <= 3 else -111.89965)]

        with patch("bedrock.services.layout_service.run_layout_search", side_effect=_side_effect):
            first = search_layout(parcel=parcel, zoning=zoning, max_candidates=12)
            second = search_layout(parcel=parcel, zoning=zoning, max_candidates=12)

        self.assertNotEqual(first.road_geometries, second.road_geometries)
        self.assertNotEqual(first.layout_id, second.layout_id)

    def test_search_layout_frontage_change_produces_geometry_change(self) -> None:
        parcel = Parcel.model_validate(
            {
                "parcel_id": "frontage-sensitive-parcel",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [0.0, 0.0],
                        [0.0, 520.0],
                        [700.0, 520.0],
                        [700.0, 0.0],
                        [0.0, 0.0],
                    ]],
                },
                "area_sqft": 364000.0,
                "centroid": [350.0, 260.0],
                "bounding_box": [0.0, 0.0, 700.0, 520.0],
                "jurisdiction": "BenchmarkCounty_UT",
                "utilities": [],
                "access_points": [],
                "topography": {},
                "existing_structures": [],
            }
        )
        base_zoning = {
            "parcel_id": parcel.parcel_id,
            "district": "R-1",
            "min_lot_size_sqft": 4000.0,
            "max_units_per_acre": 4.0,
            "setbacks": {"front": 20.0, "side": 8.0, "rear": 20.0},
        }
        narrow = search_layout(
            parcel=parcel,
            zoning=ZoningRules.model_validate({**base_zoning, "min_frontage_ft": 45.0}),
            max_candidates=12,
        )
        wide = search_layout(
            parcel=parcel,
            zoning=ZoningRules.model_validate({**base_zoning, "min_frontage_ft": 70.0}),
            max_candidates=12,
        )

        self.assertNotEqual(narrow.layout_id, wide.layout_id)
        self.assertNotEqual(narrow.lot_geometries, wide.lot_geometries)
        self.assertNotEqual(narrow.road_geometries, wide.road_geometries)

    def test_search_layout_front_and_rear_setbacks_change_layout_geometry(self) -> None:
        parcel = Parcel.model_validate(
            {
                "parcel_id": "setback-sensitive-parcel",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [0.0, 0.0],
                        [0.0, 520.0],
                        [700.0, 520.0],
                        [700.0, 0.0],
                        [0.0, 0.0],
                    ]],
                },
                "area_sqft": 364000.0,
                "centroid": [350.0, 260.0],
                "bounding_box": [0.0, 0.0, 700.0, 520.0],
                "jurisdiction": "BenchmarkCounty_UT",
                "utilities": [],
                "access_points": [],
                "topography": {},
                "existing_structures": [],
            }
        )
        base_zoning = {
            "parcel_id": parcel.parcel_id,
            "district": "R-1",
            "min_lot_size_sqft": 4000.0,
            "max_units_per_acre": 4.0,
            "min_frontage_ft": 55.0,
        }
        base = search_layout(
            parcel=parcel,
            zoning=ZoningRules.model_validate({**base_zoning, "setbacks": {"front": 10.0, "side": 8.0, "rear": 10.0}}),
            max_candidates=12,
        )
        tighter_front = search_layout(
            parcel=parcel,
            zoning=ZoningRules.model_validate({**base_zoning, "setbacks": {"front": 40.0, "side": 8.0, "rear": 10.0}}),
            max_candidates=12,
        )
        tighter_rear = search_layout(
            parcel=parcel,
            zoning=ZoningRules.model_validate({**base_zoning, "setbacks": {"front": 10.0, "side": 8.0, "rear": 40.0}}),
            max_candidates=12,
        )

        self.assertNotEqual(base.layout_id, tighter_front.layout_id)
        self.assertNotEqual(base.layout_id, tighter_rear.layout_id)
        self.assertNotEqual(base.road_geometries, tighter_front.road_geometries)
        self.assertNotEqual(base.road_geometries, tighter_rear.road_geometries)
        self.assertNotEqual(base.unit_count, tighter_front.unit_count)
        self.assertNotEqual(base.unit_count, tighter_rear.unit_count)

    def test_search_layout_block_depth_change_produces_road_geometry_change_on_exp_lehi(self) -> None:
        case, zoning_payload = _exp_lehi_case_payload()
        parcel = Parcel.model_validate(
            {
                "parcel_id": "exp-lehi-001",
                "geometry": case["geometry"],
                "area_sqft": 100000.0,
                "jurisdiction": "Lehi",
                "zoning_district": "TH-5",
                "centroid": None,
                "bounding_box": None,
                "utilities": [],
                "access_points": [],
                "topography": {},
                "existing_structures": [],
            }
        )

        shallow = search_layout(
            parcel=parcel,
            zoning=ZoningRules.model_validate(
                {
                    **zoning_payload,
                    "parcel_id": parcel.parcel_id,
                    "standards": zoning_payload["standards"]
                    + [
                        {
                            "id": "ui:block-depth-shallow",
                            "district_id": None,
                            "standard_type": "block_depth_ft",
                            "value": 90.0,
                            "units": "ft",
                            "conditions": [],
                            "citation": None,
                            "metadata": None,
                        }
                    ],
                }
            ),
            max_candidates=8,
        )
        deep = search_layout(
            parcel=parcel,
            zoning=ZoningRules.model_validate(
                {
                    **zoning_payload,
                    "parcel_id": parcel.parcel_id,
                    "standards": zoning_payload["standards"]
                    + [
                        {
                            "id": "ui:block-depth-deep",
                            "district_id": None,
                            "standard_type": "block_depth_ft",
                            "value": 150.0,
                            "units": "ft",
                            "conditions": [],
                            "citation": None,
                            "metadata": None,
                        }
                    ],
                }
            ),
            max_candidates=8,
        )

        self.assertNotEqual(shallow.layout_id, deep.layout_id)
        self.assertNotEqual(shallow.road_geometries, deep.road_geometries)
        self.assertNotEqual(shallow.lot_geometries, deep.lot_geometries)

    def test_search_layout_frontage_change_produces_road_geometry_change_on_exp_lehi(self) -> None:
        case, zoning_payload = _exp_lehi_case_payload()
        parcel = Parcel.model_validate(
            {
                "parcel_id": "exp-lehi-001",
                "geometry": case["geometry"],
                "area_sqft": 100000.0,
                "jurisdiction": "Lehi",
                "zoning_district": "TH-5",
                "centroid": None,
                "bounding_box": None,
                "utilities": [],
                "access_points": [],
                "topography": {},
                "existing_structures": [],
            }
        )

        narrow = search_layout(
            parcel=parcel,
            zoning=ZoningRules.model_validate({**zoning_payload, "parcel_id": parcel.parcel_id, "min_frontage_ft": 45.0}),
            max_candidates=8,
        )
        wide = search_layout(
            parcel=parcel,
            zoning=ZoningRules.model_validate({**zoning_payload, "parcel_id": parcel.parcel_id, "min_frontage_ft": 70.0}),
            max_candidates=8,
        )

        self.assertNotEqual(narrow.layout_id, wide.layout_id)
        self.assertNotEqual(narrow.road_geometries, wide.road_geometries)
        self.assertNotEqual(narrow.lot_geometries, wide.lot_geometries)

    def test_service_normalizes_existing_layout_engine_output(self) -> None:
        class FakeResult:
            metrics = {"lot_count": 9, "total_road_ft": 640.0}

        class FakeCandidate:
            def __init__(self) -> None:
                self.geojson = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "LineString", "coordinates": [[-111.9, 40.7], [-111.899, 40.701]]},
                            "properties": {"layer": "road"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[
                                    [-111.9, 40.7],
                                    [-111.9, 40.703],
                                    [-111.897, 40.703],
                                    [-111.897, 40.7],
                                    [-111.9, 40.7],
                                ]],
                            },
                            "properties": {"layer": "lots"},
                        },
                    ],
                }
                self.result = FakeResult()
                self.score = 0.61

        with patch("bedrock.services.layout_service.run_layout_search", return_value=[FakeCandidate()]):
            result = search_layout(
                parcel=Parcel.model_validate(self.request_payload["parcel"]),
                zoning=ZoningRules.model_validate(self.request_payload["zoning"]),
                max_candidates=12,
            )
            result_repeat = search_layout(
                parcel=Parcel.model_validate(self.request_payload["parcel"]),
                zoning=ZoningRules.model_validate(self.request_payload["zoning"]),
                max_candidates=12,
            )

        self.assertEqual(result.unit_count, 1)
        self.assertEqual(result.road_length_ft, 640.0)
        self.assertEqual(len(result.road_geometries), 1)
        self.assertEqual(len(result.lot_geometries), 1)
        self.assertEqual(result.layout_id, result_repeat.layout_id)

    def test_service_consumes_normalized_canonical_zoning_rules(self) -> None:
        parcel = Parcel.model_validate(self.request_payload["parcel"])
        zoning = build_zoning_rules_from_lookup(
            parcel,
            {
                "jurisdiction": "Salt Lake County",
                "district": "R-1",
                "rules": {
                    "district": "R-1",
                    "min_lot_size_sqft": 7200.0,
                    "max_units_per_acre": 4.0,
                    "min_frontage_ft": 58.0,
                    "road_right_of_way_ft": 40.0,
                    "setbacks": {"front": 30.0, "side": 10.0, "rear": 25.0},
                },
            },
        )

        class FakeResult:
            metrics = {"lot_count": 7, "total_road_ft": 700.0}

        class FakeCandidate:
            def __init__(self) -> None:
                self.geojson = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[
                                    [-111.9, 40.7],
                                    [-111.9, 40.702],
                                    [-111.898, 40.702],
                                    [-111.898, 40.7],
                                    [-111.9, 40.7],
                                ]],
                            },
                            "properties": {"layer": "lots"},
                        },
                    ],
                }
                self.result = FakeResult()
                self.score = 0.55

        with patch("bedrock.services.layout_service.run_layout_search", return_value=[FakeCandidate()]) as mock_search:
            search_layout(parcel=parcel, zoning=zoning, max_candidates=9)

        _, kwargs = mock_search.call_args
        self.assertEqual(kwargs["zoning_rules"]["district"], "R-1")
        self.assertEqual(kwargs["solver_constraints"]["min_lot_area_sqft"], 7200.0)
        self.assertEqual(kwargs["solver_constraints"]["max_units"], 11)
        self.assertEqual(kwargs["solver_constraints"]["min_frontage_ft"], 58.0)
        self.assertEqual(kwargs["solver_constraints"]["required_buildable_width_ft"], 130.9090909090909)
        self.assertEqual(kwargs["solver_constraints"]["side_setback_ft"], 10.0)
        self.assertEqual(kwargs["solver_constraints"]["max_buildable_depth_ft"], 55.0)
        self.assertEqual(kwargs["search_heuristics"]["frontage_hint_ft"], 58.0)
        self.assertEqual(kwargs["search_heuristics"]["target_lot_depth_ft"], 55.0)
        self.assertEqual(kwargs["min_frontage_ft"], 58.0)
        self.assertEqual(kwargs["road_width_ft"], 40.0)
        self.assertEqual(kwargs["lot_depth"], 55.0)

    def test_service_rejects_missing_required_canonical_zoning_fields(self) -> None:
        parcel = Parcel.model_validate(self.request_payload["parcel"])
        zoning = ZoningRules.model_validate(
            {
                "parcel_id": parcel.parcel_id,
                "district": "R-1",
                "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
            }
        )

        with self.assertRaises(LayoutSearchError) as exc:
            search_layout(parcel=parcel, zoning=zoning, max_candidates=12)
        self.assertEqual(exc.exception.code, "non_usable_zoning")
        self.assertIn("min_lot_size_sqft", exc.exception.message)

    def test_service_rejects_missing_density_limit_with_clear_error(self) -> None:
        parcel = Parcel.model_validate(self.request_payload["parcel"])
        zoning = ZoningRules.model_validate(
            {
                "parcel_id": parcel.parcel_id,
                "district": "R-1",
                "min_lot_size_sqft": 6000.0,
                "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
            }
        )
        with self.assertRaises(LayoutSearchError) as exc:
            search_layout(parcel=parcel, zoning=zoning, max_candidates=12)
        self.assertEqual(exc.exception.code, "non_usable_zoning")
        self.assertIn("max_units_per_acre", exc.exception.message)

    def test_generate_candidates_aggregates_multiple_strategies(self) -> None:
        parcel = Parcel.model_validate(self.request_payload["parcel"])
        zoning = ZoningRules.model_validate(self.request_payload["zoning"])

        class FakeResult:
            def __init__(self, lot_count: int, road_ft: float) -> None:
                self.metrics = {"lot_count": lot_count, "total_road_ft": road_ft}
                self.lots = []

        class FakeCandidate:
            def __init__(self, score: float, lot_count: int, road_ft: float) -> None:
                self.geojson = {"type": "FeatureCollection", "features": []}
                self.result = FakeResult(lot_count=lot_count, road_ft=road_ft)
                self.score = score

        def _strategy_score(kwargs) -> float:
            strategies = kwargs.get("search_heuristics", {}).get("strategies", [])
            strategy = strategies[0] if strategies else "unknown"
            return {
                "grid": 0.52,
                "spine-road": 0.71,
                "cul-de-sac": 0.64,
            }.get(strategy, 0.4)

        def _side_effect(*_args, **kwargs):
            score = _strategy_score(kwargs)
            return [FakeCandidate(score=score, lot_count=9, road_ft=650.0)]

        with patch("bedrock.services.layout_service.run_layout_search", side_effect=_side_effect) as mock_search:
            candidates = generate_candidates(parcel=parcel, zoning=zoning, max_candidates=12)

        self.assertGreaterEqual(mock_search.call_count, 3)
        invoked = [
            call.kwargs.get("search_heuristics", {}).get("strategies", [None])[0]
            for call in mock_search.call_args_list
        ]
        self.assertIn("grid", invoked)
        self.assertIn("spine-road", invoked)
        self.assertIn("cul-de-sac", invoked)
        self.assertGreaterEqual(len(candidates), 3)
        self.assertGreaterEqual(candidates[0].score, candidates[1].score)

    def test_search_layout_returns_highest_scoring_strategy_candidate(self) -> None:
        parcel = Parcel.model_validate(self.request_payload["parcel"])
        zoning = ZoningRules.model_validate(self.request_payload["zoning"])

        class FakeResult:
            def __init__(self, lot_count: int, road_ft: float) -> None:
                self.metrics = {"lot_count": lot_count, "total_road_ft": road_ft}
                self.lots = []

        class FakeCandidate:
            def __init__(self, strategy: str, score: float) -> None:
                self.geojson = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[
                                    [-111.9, 40.7],
                                    [-111.9, 40.702],
                                    [-111.898, 40.702],
                                    [-111.898, 40.7],
                                    [-111.9, 40.7],
                                ]],
                            },
                            "properties": {"layer": "lots"},
                        },
                    ],
                }
                self.result = FakeResult(lot_count=8, road_ft=700.0)
                self.score = score
                self.strategy = strategy

        strategy_scores = {
            "grid": 0.4,
            "spine-road": 0.8,
            "cul-de-sac": 0.5,
        }

        def _side_effect(*_args, **kwargs):
            strategy = kwargs.get("search_heuristics", {}).get("strategies", [None])[0]
            return [FakeCandidate(strategy=strategy, score=strategy_scores.get(strategy, 0.1))]

        with patch("bedrock.services.layout_service.run_layout_search", side_effect=_side_effect):
            result = search_layout(parcel=parcel, zoning=zoning, max_candidates=12)

        self.assertIsNotNone(result.score)
        self.assertAlmostEqual(float(result.score), 0.8, places=6)

    def test_search_layout_skips_invalid_candidate_and_returns_next_valid(self) -> None:
        parcel = Parcel.model_validate(self.request_payload["parcel"])
        zoning = ZoningRules.model_validate(self.request_payload["zoning"])

        class FakeResult:
            def __init__(self, lot_count: int, road_ft: float) -> None:
                self.metrics = {"lot_count": lot_count, "total_road_ft": road_ft}
                self.lots = []

        class FakeCandidate:
            def __init__(self, score: float, with_lot_geometry: bool) -> None:
                features = []
                if with_lot_geometry:
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[
                                    [-111.9, 40.7],
                                    [-111.9, 40.702],
                                    [-111.898, 40.702],
                                    [-111.898, 40.7],
                                    [-111.9, 40.7],
                                ]],
                            },
                            "properties": {"layer": "lots"},
                        }
                    )
                self.geojson = {"type": "FeatureCollection", "features": features}
                self.result = FakeResult(lot_count=3, road_ft=500.0)
                self.score = score

        with patch(
            "bedrock.services.layout_service.run_layout_search",
            return_value=[
                FakeCandidate(score=0.9, with_lot_geometry=False),
                FakeCandidate(score=0.7, with_lot_geometry=True),
            ],
        ):
            result = search_layout(parcel=parcel, zoning=zoning, max_candidates=12)

        self.assertAlmostEqual(float(result.score), 0.7, places=6)
        self.assertEqual(len(result.lot_geometries), 1)

    def test_search_layout_tie_break_is_deterministic(self) -> None:
        parcel = Parcel.model_validate(self.request_payload["parcel"])
        zoning = ZoningRules.model_validate(self.request_payload["zoning"])

        class FakeResult:
            def __init__(self) -> None:
                self.metrics = {"lot_count": 3, "total_road_ft": 300.0}
                self.lots = []

        class FakeCandidate:
            def __init__(self, point_x: float) -> None:
                self.geojson = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[
                                    [point_x, 0.0],
                                    [point_x, 1.0],
                                    [point_x + 1.0, 1.0],
                                    [point_x + 1.0, 0.0],
                                    [point_x, 0.0],
                                ]],
                            },
                            "properties": {"layer": "lots"},
                        },
                    ],
                }
                self.result = FakeResult()
                self.score = 0.8

        with patch(
            "bedrock.services.layout_service.run_layout_search",
            return_value=[FakeCandidate(point_x=10.0), FakeCandidate(point_x=0.0)],
        ):
            first = search_layout(parcel=parcel, zoning=zoning, max_candidates=12)
            second = search_layout(parcel=parcel, zoning=zoning, max_candidates=12)

        self.assertEqual(first.layout_id, second.layout_id)

    def test_search_layout_is_bitwise_deterministic_for_ten_runs(self) -> None:
        parcel = Parcel.model_validate(self.request_payload["parcel"])
        zoning = ZoningRules.model_validate(self.request_payload["zoning"])

        class FakeResult:
            def __init__(self) -> None:
                self.metrics = {"lot_count": 1, "total_road_ft": 120.0, "avg_lot_area_sqft": 5000.0}
                self.lots = []
                self.segments = []

        class FakeCandidate:
            def __init__(self) -> None:
                self.geojson = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[0.0, 0.0], [0.0, 50.0], [100.0, 50.0], [100.0, 0.0], [0.0, 0.0]]],
                            },
                            "properties": {"layer": "lots"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [100.0, 0.0]]},
                            "properties": {"layer": "road"},
                        },
                    ],
                }
                self.result = FakeResult()
                self.score = 0.8

        with patch("bedrock.services.layout_service.run_layout_search", return_value=[FakeCandidate()]):
            payloads = [search_layout(parcel=parcel, zoning=zoning, max_candidates=12).model_dump(mode="json") for _ in range(10)]

        self.assertEqual(len({json.dumps(item, sort_keys=True, separators=(",", ":")) for item in payloads}), 1)

    def test_search_layout_rejects_overlapping_lots_and_disconnected_roads(self) -> None:
        parcel = Parcel.model_validate(self.request_payload["parcel"])
        zoning = ZoningRules.model_validate({**self.request_payload["zoning"], "road_right_of_way_ft": 32.0})

        class FakeLot:
            def __init__(self, polygon, area_sqft: float, frontage_ft: float, depth_ft: float) -> None:
                self.polygon = polygon
                self.area_sqft = area_sqft
                self.frontage_ft = frontage_ft
                self.depth_ft = depth_ft

        class FakeSegment:
            def __init__(self, line) -> None:
                self.line = line

        class FakeResult:
            def __init__(self, lots, segments) -> None:
                self.metrics = {"lot_count": len(lots), "total_road_ft": 220.0, "avg_lot_area_sqft": 5000.0}
                self.lots = lots
                self.segments = segments

        from shapely.geometry import LineString, Polygon

        overlapping_lot_a = Polygon([(0, 0), (0, 80), (80, 80), (80, 0), (0, 0)])
        overlapping_lot_b = Polygon([(40, 0), (40, 80), (120, 80), (120, 0), (40, 0)])
        disconnected_roads = [LineString([(0, 0), (0, 100)]), LineString([(200, 0), (200, 100)])]

        class FakeCandidate:
            def __init__(self) -> None:
                self.geojson = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 80], [80, 80], [80, 0], [0, 0]]]},
                            "properties": {"layer": "lots"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "Polygon", "coordinates": [[[40, 0], [40, 80], [120, 80], [120, 0], [40, 0]]]},
                            "properties": {"layer": "lots"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "LineString", "coordinates": [[0, 0], [0, 100]]},
                            "properties": {"layer": "road"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "LineString", "coordinates": [[200, 0], [200, 100]]},
                            "properties": {"layer": "road"},
                        },
                    ],
                }
                self.result = FakeResult(
                    lots=[
                        FakeLot(overlapping_lot_a, overlapping_lot_a.area, 60.0, 60.0),
                        FakeLot(overlapping_lot_b, overlapping_lot_b.area, 60.0, 60.0),
                    ],
                    segments=[FakeSegment(line) for line in disconnected_roads],
                )
                self.score = 0.9

        with patch("bedrock.services.layout_service.run_layout_search", return_value=[FakeCandidate()]):
            with self.assertRaises(LayoutSearchError) as exc:
                search_layout(parcel=parcel, zoning=zoning, max_candidates=12)

        self.assertEqual(exc.exception.code, "no_viable_layout")

    def test_search_layout_debug_emits_candidate_metrics(self) -> None:
        parcel = Parcel.model_validate(self.request_payload["parcel"])
        zoning = ZoningRules.model_validate(self.request_payload["zoning"])

        class FakeResult:
            def __init__(self) -> None:
                self.metrics = {"lot_count": 1, "total_road_ft": 120.1234567, "avg_lot_area_sqft": 5000.0}
                self.lots = []
                self.segments = []

        class FakeCandidate:
            def __init__(self) -> None:
                self.geojson = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[0.123456789, 0.0], [0.123456789, 1.0], [1.0, 1.0], [1.0, 0.0], [0.123456789, 0.0]]],
                            },
                            "properties": {"layer": "lots"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [1.123456789, 0.0]]},
                            "properties": {"layer": "road"},
                        },
                    ],
                }
                self.result = FakeResult()
                self.score = 0.812345678

        with patch("bedrock.services.layout_service.run_layout_search", return_value=[FakeCandidate()]):
            layout, debug_metrics = search_layout_debug(parcel=parcel, zoning=zoning, max_candidates=12)

        self.assertEqual(debug_metrics["final_selected_layout_index"], 0)
        self.assertIn("total_candidates_generated", debug_metrics)
        self.assertIn("candidates_surviving", debug_metrics)
        self.assertIn("total_runtime_seconds", debug_metrics)
        self.assertEqual(layout.score, 0.812346)
        self.assertEqual(layout.road_length_ft, 120.123457)
        self.assertEqual(layout.lot_geometries[0]["coordinates"][0][0][0], 0.123457)

    def test_service_rejects_ambiguous_direct_and_standard_values(self) -> None:
        parcel = Parcel.model_validate(self.request_payload["parcel"])
        zoning = ZoningRules.model_validate(
            {
                "parcel_id": parcel.parcel_id,
                "district": "R-1",
                "min_lot_size_sqft": 6000.0,
                "max_units_per_acre": 5.0,
                "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
                "standards": [
                    {
                        "id": "R-1:min_lot_size_sqft",
                        "standard_type": "min_lot_size_sqft",
                        "value": 7200.0,
                        "units": "sqft",
                    }
                ],
            }
        )

        with self.assertRaises(LayoutSearchError) as exc:
            search_layout(parcel=parcel, zoning=zoning, max_candidates=12)
        self.assertEqual(exc.exception.code, "non_usable_zoning")
        self.assertIn("ambiguous_zoning_value", exc.exception.message)


if __name__ == "__main__":
    unittest.main()
