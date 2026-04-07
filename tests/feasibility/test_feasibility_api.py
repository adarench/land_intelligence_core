from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (REPO_ROOT, REPO_ROOT / "bedrock"):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from bedrock.api.feasibility_api import router


class FeasibilityApiTest(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_evaluate_returns_feasibility_result_without_market_context(self) -> None:
        payload = {
            "parcel": {
                "parcel_id": "parcel-api-001",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]],
                },
                "area": 43560.0,
                "jurisdiction": "Example City",
            },
            "layout": {
                "layout_id": "layout-api-001",
                "parcel_id": "parcel-api-001",
                "lot_count": 10,
                "road_length": 500.0,
                "open_space_area": 0.0,
                "utility_length": 0.0,
            },
        }

        response = self.client.post("/feasibility/evaluate", json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["parcel_id"], "parcel-api-001")
        self.assertEqual(body["layout_id"], "layout-api-001")
        self.assertEqual(body["units"], 10)
        self.assertEqual(body["estimated_home_price"], 480000.0)
        self.assertEqual(body["construction_cost_per_home"], 410880.0)
        self.assertEqual(body["projected_revenue"], 4800000.0)
        self.assertAlmostEqual(body["projected_cost"], 4724827.5, places=0)
        self.assertAlmostEqual(body["projected_profit"], 75172.5, places=0)
        self.assertAlmostEqual(body["ROI"], 75172.5 / 4724827.5, places=4)

    def test_evaluate_accepts_market_context_override(self) -> None:
        payload = {
            "parcel": {
                "parcel_id": "parcel-api-002",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]],
                },
                "area": 43560.0,
                "jurisdiction": "Example City",
            },
            "layout": {
                "layout_id": "layout-api-002",
                "parcel_id": "parcel-api-002",
                "lot_count": 8,
                "road_length": 200.0,
                "open_space_area": 0.0,
                "utility_length": 0.0,
            },
            "market_context": {
                "estimated_home_price": 420000.0,
                "construction_cost_per_home": 230000.0,
                "road_cost_per_ft": 250.0,
            },
        }

        response = self.client.post("/feasibility/evaluate", json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["projected_revenue"], 3360000.0)
        self.assertAlmostEqual(body["projected_cost"], 2168375.0, places=0)
        self.assertAlmostEqual(body["projected_profit"], 1191625.0, places=0)
        self.assertAlmostEqual(body["ROI"], 1191625.0 / 2168375.0, places=4)

    def test_evaluate_accepts_partial_market_context_with_defaults(self) -> None:
        payload = {
            "parcel": {
                "parcel_id": "parcel-api-003",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]],
                },
                "area": 43560.0,
                "jurisdiction": "Example City",
            },
            "layout": {
                "layout_id": "layout-api-003",
                "parcel_id": "parcel-api-003",
                "lot_count": 2,
                "road_length": 100.0,
                "open_space_area": 0.0,
                "utility_length": 0.0,
            },
            "market_context": {
                "estimated_home_price": 500000.0,
            },
        }

        response = self.client.post("/feasibility/evaluate", json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["estimated_home_price"], 500000.0)
        self.assertEqual(body["construction_cost_per_home"], 260000.0)
        self.assertEqual(body["development_cost_total"], 76150.0)
        self.assertEqual(body["projected_revenue"], 1000000.0)
        self.assertAlmostEqual(body["projected_cost"], 655765.0, places=0)

    def test_evaluate_rejects_parcel_layout_contract_mismatch(self) -> None:
        payload = {
            "parcel": {
                "parcel_id": "parcel-api-mismatch",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]],
                },
                "area": 43560.0,
                "jurisdiction": "Example City",
            },
            "layout": {
                "layout_id": "layout-api-mismatch",
                "parcel_id": "parcel-other",
                "lot_count": 2,
                "road_length": 100.0,
                "open_space_area": 0.0,
                "utility_length": 0.0,
            },
        }

        response = self.client.post("/feasibility/evaluate", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["error"], "contract_mismatch")


if __name__ == "__main__":
    unittest.main()
