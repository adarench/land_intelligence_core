from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bedrock") not in sys.path:
    sys.path.insert(0, str(ROOT / "bedrock"))
if str(ROOT / "bedrock" / "apps" / "python-api") not in sys.path:
    sys.path.insert(0, str(ROOT / "bedrock" / "apps" / "python-api"))

from main import create_app


def test_main_app_openapi_includes_layout_and_evaluation_routes() -> None:
    client = TestClient(create_app())

    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/layout/search" in paths
    assert "/layout/candidates" in paths
    assert "/layout/export" in paths
    assert "/evaluation/benchmark" in paths
    assert "/pipeline/optimize" in paths
    assert "/optimization/runs" in paths
    assert "/optimization/runs/{optimization_run_id}" in paths
