from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bedrock") not in sys.path:
    sys.path.insert(0, str(ROOT / "bedrock"))

from bedrock.api.pipeline_api import create_app


def test_pipeline_run_completes_with_stub_zoning_and_returns_feasibility() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/pipeline/run",
        json={
            "parcel_geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [0, 400], [400, 400], [400, 0], [0, 0]]],
            },
            "jurisdiction": "Salt Lake City",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["layout_result"]["unit_count"] > 0
    assert payload["feasibility_result"]["units"] == payload["layout_result"]["unit_count"]
    assert payload["feasibility_result"]["ROI"] == payload["feasibility_result"]["projected_profit"] / payload["feasibility_result"]["projected_cost"]
    assert payload["zoning_bypassed"] is True
    assert payload["bypass_reason"] == "temporary_stub_zoning"
