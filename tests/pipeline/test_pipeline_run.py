from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bedrock") not in sys.path:
    sys.path.insert(0, str(ROOT / "bedrock"))

from bedrock.api.pipeline_api import create_app
from bedrock.contracts.base import EngineMetadata
from bedrock.contracts.feasibility import FeasibilityResult
from bedrock.contracts.layout import SubdivisionLayout
from bedrock.contracts.market_data import MarketData
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.schema_registry import CANONICAL_SERIALIZATION_FIELDS
from bedrock.contracts.validators import validate_service_output
from bedrock.contracts.zoning_rules import SetbackSet, ZoningRules
from bedrock.services.pipeline_service import PipelineExecutionResult, PipelineService
from bedrock.services.pipeline_service import PipelineStageError
from zoning_data_scraper.services.zoning_overlay import AmbiguousZoningMatchError, NoZoningMatchError


def _geojson_polygon(coords: list[tuple[float, float]]) -> dict:
    return {"type": "Polygon", "coordinates": [[list(point) for point in coords]]}


def _parcel() -> Parcel:
    return Parcel(
        parcel_id="parcel-001",
        geometry=_geojson_polygon([(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]),
        area=1000,
        jurisdiction="Test City",
        zoning_district=None,
        utilities=[],
        access_points=[],
        topography={},
        existing_structures=[],
    )


def _zoning_rules() -> ZoningRules:
    return ZoningRules(
        parcel_id="parcel-001",
        jurisdiction="Test City",
        district="R-1",
        min_lot_size_sqft=8000,
        max_units_per_acre=4,
        setbacks=SetbackSet(front=25, side=8, rear=20),
        height_limit_ft=35,
        lot_coverage_max=0.45,
        metadata=EngineMetadata(source_engine="zoning_data_scraper", source_run_id="test"),
    )


def _layout_result(parcel: Parcel) -> SubdivisionLayout:
    return SubdivisionLayout(
        layout_id="layout-001",
        parcel_id=parcel.parcel_id,
        unit_count=4,
        road_length_ft=120.0,
        lot_geometries=[_geojson_polygon([(0, 0), (0, 4), (4, 4), (4, 0), (0, 0)])],
        road_geometries=[_geojson_polygon([(4, 0), (4, 10), (5, 10), (5, 0), (4, 0)])],
        open_space_area_sqft=200.0,
        utility_length_ft=0.0,
        score=0.91,
        metadata=EngineMetadata(source_engine="bedrock.layout_service", source_run_id=None),
    )


def _feasibility_result(parcel_id: str, layout_id: str) -> FeasibilityResult:
    return FeasibilityResult(
        scenario_id="scenario-001",
        parcel_id=parcel_id,
        layout_id=layout_id,
        units=4,
        estimated_home_price=480000.0,
        construction_cost_per_home=260000.0,
        development_cost_total=500000.0,
        projected_revenue=1920000.0,
        projected_cost=1540000.0,
        projected_profit=380000.0,
        ROI=0.2468,
        risk_score=0.18,
        confidence=0.9,
    )


def _pipeline_run_payload(
    *,
    run_id: str = "run-001",
    status: str = "completed",
    timestamp: str = "2026-03-20T00:00:00Z",
    parcel: Parcel | None = None,
    zoning_rules: ZoningRules | None = None,
    layout: SubdivisionLayout | None = None,
    feasibility: FeasibilityResult | None = None,
    zoning_bypassed: bool = False,
    bypass_reason: str | None = None,
) -> dict:
    parcel = parcel or _parcel()
    zoning_rules = zoning_rules or _zoning_rules()
    if status == "completed":
        layout = layout or _layout_result(parcel)
        feasibility = feasibility or _feasibility_result(parcel.parcel_id, layout.layout_id)
    return {
        "run_id": run_id,
        "status": status,
        "timestamp": timestamp,
        "parcel_id": parcel.parcel_id,
        "zoning_result": zoning_rules.model_dump(mode="json"),
        "layout_result": layout.model_dump(mode="json") if layout is not None else None,
        "feasibility_result": feasibility.model_dump(mode="json") if feasibility is not None else None,
        "zoning_bypassed": zoning_bypassed,
        "bypass_reason": bypass_reason,
    }


def test_pipeline_service_runs_end_to_end_and_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    parcel = _parcel()
    zoning_rules = _zoning_rules()
    layout = _layout_result(parcel)
    feasibility = _feasibility_result(parcel.parcel_id, layout.layout_id)
    store_path = tmp_path / "pipeline_runs.jsonl"
    runs_dir = tmp_path / "runs"

    monkeypatch.setattr("bedrock.services.pipeline_service.ParcelService.load_parcel", lambda self, **kwargs: parcel)
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.PipelineService._lookup_non_buildable_zoning_stage",
        lambda self, arg: None,
    )
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.ZoningService.lookup",
        lambda self, arg: type("ZoningLookup", (), {"rules": zoning_rules})(),
    )
    monkeypatch.setattr("bedrock.services.pipeline_service.search_subdivision_layout", lambda *args, **kwargs: layout)
    monkeypatch.setattr("bedrock.services.pipeline_service.FeasibilityService.evaluate", lambda self, **kwargs: feasibility)

    service = PipelineService()
    service.run_store.log_path = store_path
    service.run_store.runs_dir = runs_dir
    result = service.run(parcel_geometry=parcel.geometry, jurisdiction=parcel.jurisdiction)

    assert result.status == "completed"
    assert result.feasibility.layout_id == "layout-001"
    assert store_path.exists()
    persisted_path = runs_dir / f"{result.run_id}.json"
    assert persisted_path.exists()

    persisted_payload = json.loads(persisted_path.read_text())
    assert persisted_payload["run_id"] == result.run_id
    assert persisted_payload["schema_name"] == "PipelineRun"
    assert persisted_payload["parcel_id"] == "parcel-001"
    assert persisted_payload["zoning_result"]["district"] == "R-1"
    assert persisted_payload["layout_result"]["layout_id"] == "layout-001"
    assert persisted_payload["feasibility_result"]["layout_id"] == "layout-001"
    assert persisted_payload["input_hash"]
    assert isinstance(persisted_payload["stage_runtimes"], dict)
    assert set(persisted_payload["stage_runtimes"].keys()) == {
        "parcel.load",
        "zoning.lookup",
        "layout.search",
        "feasibility.evaluate",
    }

    log_lines = [line.strip() for line in store_path.read_text().splitlines() if line.strip()]
    assert len(log_lines) == 1
    payload = json.loads(log_lines[0])
    assert payload["run_id"] == result.run_id
    assert payload["parcel_id"] == "parcel-001"
    assert payload["zoning_district"] == "R-1"
    assert payload["zoning_source"] == "test"
    assert payload["zoning_geometry_match_success"] is True
    assert payload["layout_units"] == 4
    assert payload["layout_score"] == pytest.approx(0.91)
    assert payload["feasibility_roi"] == pytest.approx(0.2468)
    assert payload["timestamp"]


def test_pipeline_service_returns_unsupported_when_overlay_lookup_misses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parcel = _parcel()
    store_path = tmp_path / "pipeline_runs.jsonl"
    runs_dir = tmp_path / "runs"

    monkeypatch.setattr("bedrock.services.pipeline_service.ParcelService.load_parcel", lambda self, **kwargs: parcel)
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.PipelineService._lookup_non_buildable_zoning_stage",
        lambda self, arg: None,
    )
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.ZoningService.lookup",
        lambda self, arg: (_ for _ in ()).throw(NoZoningMatchError("No zoning district intersects the parcel geometry.")),
    )

    service = PipelineService()
    service.run_store.log_path = store_path
    service.run_store.runs_dir = runs_dir
    result = service.run(parcel_geometry=parcel.geometry, jurisdiction=parcel.jurisdiction)

    assert result.status == "unsupported"
    assert result.feasibility_result is None

    persisted_path = runs_dir / f"{result.run_id}.json"
    payload = json.loads(persisted_path.read_text())
    assert payload["status"] == "unsupported"
    assert payload["parcel_id"] == parcel.parcel_id
    assert payload["zoning_result"]["district"] == "UNSUPPORTED"
    assert payload["layout_result"] is None
    assert payload["feasibility_result"] is None
    assert payload["zoning_bypassed"] is True
    assert payload["bypass_reason"] == "unsupported_jurisdiction"

    log_payload = json.loads(store_path.read_text().strip())
    assert log_payload["status"] == "unsupported"
    assert log_payload["zoning_district"] == "UNSUPPORTED"
    assert log_payload["zoning_bypassed"] is True
    assert log_payload["bypass_reason"] == "unsupported_jurisdiction"
    assert log_payload["layout_units"] is None
    assert log_payload["feasibility_roi"] is None


def test_pipeline_service_normalizes_direct_parcel_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parcel = _parcel().model_copy(
        update={
            "geometry": _geojson_polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)]),
            "area_sqft": 1.0,
            "centroid": [0.0, 0.0],
            "bounding_box": [0.0, 0.0, 2.0, 2.0],
        }
    )
    normalized_seen: list[Parcel] = []
    zoning_rules = _zoning_rules()
    layout = _layout_result(_parcel())
    feasibility = _feasibility_result(parcel.parcel_id, layout.layout_id)
    store_path = tmp_path / "pipeline_runs.jsonl"
    runs_dir = tmp_path / "runs"

    monkeypatch.setattr(
        "bedrock.services.pipeline_service.PipelineService._lookup_non_buildable_zoning_stage",
        lambda self, arg: None,
    )

    def _lookup(self, candidate: Parcel):
        normalized_seen.append(candidate)
        return type("ZoningLookup", (), {"rules": zoning_rules})()

    monkeypatch.setattr("bedrock.services.pipeline_service.ZoningService.lookup", _lookup)
    monkeypatch.setattr("bedrock.services.pipeline_service.search_subdivision_layout", lambda *args, **kwargs: layout)
    monkeypatch.setattr("bedrock.services.pipeline_service.FeasibilityService.evaluate", lambda self, **kwargs: feasibility)

    service = PipelineService()
    service.run_store.log_path = store_path
    service.run_store.runs_dir = runs_dir
    result = service.run(parcel=parcel)

    assert result.status == "completed"
    assert len(normalized_seen) == 1
    assert normalized_seen[0].geometry["type"] in {"Polygon", "MultiPolygon"}
    assert normalized_seen[0].area_sqft > 0
    assert normalized_seen[0].geometry != parcel.geometry or normalized_seen[0].area_sqft != parcel.area_sqft


def test_pipeline_service_does_not_persist_ambiguous_zoning_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parcel = _parcel()
    store_path = tmp_path / "pipeline_runs.jsonl"
    runs_dir = tmp_path / "runs"

    monkeypatch.setattr("bedrock.services.pipeline_service.ParcelService.load_parcel", lambda self, **kwargs: parcel)
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.PipelineService._lookup_non_buildable_zoning_stage",
        lambda self, arg: None,
    )
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.ZoningService.lookup",
        lambda self, arg: (_ for _ in ()).throw(
            AmbiguousZoningMatchError("Parcel geometry overlaps multiple district candidates equally.")
        ),
    )

    service = PipelineService()
    service.run_store.log_path = store_path
    service.run_store.runs_dir = runs_dir

    with pytest.raises(PipelineStageError) as exc_info:
        service.run(parcel_geometry=parcel.geometry, jurisdiction=parcel.jurisdiction)

    assert exc_info.value.stage == "zoning.lookup"
    assert exc_info.value.error == "ambiguous_district_match"
    assert not runs_dir.exists() or not any(runs_dir.iterdir())
    if store_path.exists():
        assert not store_path.read_text().strip()


def test_pipeline_service_bypasses_murray_mg_when_overlay_lookup_misses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parcel = _parcel().model_copy(update={"jurisdiction": "Murray", "zoning_district": "M-G"})
    store_path = tmp_path / "pipeline_runs.jsonl"
    runs_dir = tmp_path / "runs"

    monkeypatch.setattr("bedrock.services.pipeline_service.ParcelService.load_parcel", lambda self, **kwargs: parcel)
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.PipelineService._lookup_non_buildable_zoning_stage",
        lambda self, arg: None,
    )
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.ZoningService.lookup",
        lambda self, arg: (_ for _ in ()).throw(NoZoningMatchError("No zoning district intersects the parcel geometry.")),
    )
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.search_subdivision_layout",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("layout.search should not execute")),
    )
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.FeasibilityService.evaluate",
        lambda self, **kwargs: (_ for _ in ()).throw(AssertionError("feasibility.evaluate should not execute")),
    )

    service = PipelineService()
    service.run_store.log_path = store_path
    service.run_store.runs_dir = runs_dir
    result = service.run(parcel_geometry=parcel.geometry, jurisdiction=parcel.jurisdiction)

    assert result.status == "non_buildable"
    assert result.feasibility_result is None

    persisted_path = runs_dir / f"{result.run_id}.json"
    payload = json.loads(persisted_path.read_text())
    assert payload["status"] == "non_buildable"
    assert payload["parcel_id"] == parcel.parcel_id
    assert payload["zoning_result"]["district"] == "M-G"
    assert payload["zoning_result"]["jurisdiction"] == "Murray"
    assert payload["layout_result"] is None
    assert payload["feasibility_result"] is None
    assert payload["zoning_bypassed"] is True
    assert payload["bypass_reason"] == "non_residential"

    log_payload = json.loads(store_path.read_text().strip())
    assert log_payload["status"] == "non_buildable"
    assert log_payload["zoning_district"] == "M-G"
    assert log_payload["zoning_bypassed"] is True
    assert log_payload["bypass_reason"] == "non_residential"


def test_pipeline_service_rejects_fallback_backed_zoning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    parcel = _parcel()
    zoning_rules = _zoning_rules().model_copy(
        update={"metadata": EngineMetadata(source_engine="zoning_data_scraper", source_run_id="jurisdiction_fallback")}
    )
    store_path = tmp_path / "pipeline_runs.jsonl"
    runs_dir = tmp_path / "runs"

    monkeypatch.setattr("bedrock.services.pipeline_service.ParcelService.load_parcel", lambda self, **kwargs: parcel)
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.PipelineService._lookup_non_buildable_zoning_stage",
        lambda self, arg: None,
    )
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.ZoningService.lookup",
        lambda self, arg: type("ZoningLookup", (), {"rules": zoning_rules})(),
    )

    service = PipelineService()
    service.run_store.log_path = store_path
    service.run_store.runs_dir = runs_dir

    with pytest.raises(PipelineStageError) as exc_info:
        service.run(parcel_geometry=parcel.geometry, jurisdiction=parcel.jurisdiction)

    assert exc_info.value.stage == "zoning.lookup"
    assert exc_info.value.error == "invalid_zoning_source"
    assert exc_info.value.details["zoning_source"] == "jurisdiction_fallback"
    assert exc_info.value.details["geometry_match_success"] is False
    assert not runs_dir.exists() or not any(runs_dir.iterdir())


@pytest.mark.parametrize(
    ("jurisdiction", "district", "reason"),
    [
        ("Provo", "RC", "historical_constraint"),
        ("Murray", "M-G", "non_residential"),
    ],
)
def test_pipeline_service_bypasses_non_buildable_districts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    jurisdiction: str,
    district: str,
    reason: str,
) -> None:
    parcel = _parcel().model_copy(update={"jurisdiction": jurisdiction})
    zoning_rules = ZoningRules(
        parcel_id=parcel.parcel_id,
        jurisdiction=jurisdiction,
        district=district,
        metadata=EngineMetadata(
            source_engine="zoning_data_scraper",
            source_run_id=f"/datasets/{jurisdiction.lower()}",
        ),
    )
    store_path = tmp_path / "pipeline_runs.jsonl"
    runs_dir = tmp_path / "runs"

    monkeypatch.setattr("bedrock.services.pipeline_service.ParcelService.load_parcel", lambda self, **kwargs: parcel)
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.PipelineService._lookup_non_buildable_zoning_stage",
        lambda self, arg: type(
            "Bypass",
            (),
            {"rules": zoning_rules, "status": "non_buildable", "bypass_reason": reason, "is_bypassed": True},
        )(),
    )
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.ZoningService.lookup",
        lambda self, arg: (_ for _ in ()).throw(AssertionError("zoning lookup should not execute after bypass")),
    )
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.search_subdivision_layout",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("layout.search should not execute")),
    )
    monkeypatch.setattr(
        "bedrock.services.pipeline_service.FeasibilityService.evaluate",
        lambda self, **kwargs: (_ for _ in ()).throw(AssertionError("feasibility.evaluate should not execute")),
    )

    service = PipelineService()
    service.run_store.log_path = store_path
    service.run_store.runs_dir = runs_dir
    result = service.run(parcel_geometry=parcel.geometry, jurisdiction=parcel.jurisdiction)

    assert result.status == "non_buildable"
    assert result.feasibility_result is None

    persisted_path = runs_dir / f"{result.run_id}.json"
    payload = json.loads(persisted_path.read_text())
    assert payload["status"] == "non_buildable"
    assert payload["parcel_id"] == parcel.parcel_id
    assert payload["zoning_result"]["district"] == district
    assert payload["layout_result"] is None
    assert payload["feasibility_result"] is None
    assert payload["zoning_bypassed"] is True
    assert payload["bypass_reason"] == reason

    log_payload = json.loads(store_path.read_text().strip())
    assert log_payload["status"] == "non_buildable"
    assert log_payload["zoning_district"] == district
    assert log_payload["zoning_bypassed"] is True
    assert log_payload["bypass_reason"] == reason
    assert log_payload["layout_units"] is None
    assert log_payload["feasibility_roi"] is None


def test_pipeline_api_valid_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = PipelineExecutionResult(
        run_id="run-001",
        status="completed",
        feasibility=_feasibility_result("parcel-001", "layout-001"),
    )

    class StubService:
        def __init__(self) -> None:
            self.run_store = type(
                "StubRunStore",
                (),
                {"load_run": lambda self, run_id: _pipeline_run_payload(run_id=run_id)},
            )()

        def run(self, **kwargs):
            return expected

    monkeypatch.setattr("bedrock.api.pipeline_api.PipelineService", StubService)
    client = TestClient(create_app())

    response = client.post(
        "/pipeline/run",
        json={"parcel_geometry": _geojson_polygon([(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]), "jurisdiction": "Test City"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == set(CANONICAL_SERIALIZATION_FIELDS["PipelineRun"])
    assert payload["schema_name"] == "PipelineRun"
    assert payload["schema_version"] == "1.0.0"
    assert payload["run_id"] == "run-001"
    assert payload["status"] == "completed"
    assert payload["parcel_id"] == "parcel-001"
    assert payload["zoning_result"]["district"] == "R-1"
    assert payload["layout_result"]["layout_id"] == "layout-001"
    assert payload["feasibility_result"]["layout_id"] == "layout-001"
    assert payload["timestamp"] == "2026-03-20T00:00:00Z"
    assert payload["git_commit"] is None
    assert payload["input_hash"] is None
    assert payload["stage_runtimes"] == {}
    assert payload["zoning_bypassed"] is False
    assert payload["bypass_reason"] is None
    validate_service_output("bedrock.api.pipeline_api.run_pipeline", payload)


def test_pipeline_api_non_buildable_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = PipelineExecutionResult(
        run_id="run-rc-001",
        status="non_buildable",
        feasibility_result=None,
    )

    class StubService:
        def __init__(self) -> None:
            self.run_store = type(
                "StubRunStore",
                (),
                {
                    "load_run": lambda self, run_id: _pipeline_run_payload(
                        run_id=run_id,
                        status="non_buildable",
                        zoning_rules=ZoningRules(
                            parcel_id="parcel-001",
                            jurisdiction="Provo",
                            district="RC",
                            metadata=EngineMetadata(source_engine="zoning_data_scraper", source_run_id="/datasets/provo"),
                        ),
                        layout=None,
                        feasibility=None,
                        zoning_bypassed=True,
                        bypass_reason="historical_constraint",
                    )
                },
            )()

        def run(self, **kwargs):
            return expected

    monkeypatch.setattr("bedrock.api.pipeline_api.PipelineService", StubService)
    client = TestClient(create_app())

    response = client.post(
        "/pipeline/run",
        json={"parcel_geometry": _geojson_polygon([(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]), "jurisdiction": "Test City"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "non_buildable"
    assert payload["zoning_result"]["district"] == "RC"
    assert payload["layout_result"] is None
    assert payload["feasibility_result"] is None
    assert payload["zoning_bypassed"] is True
    assert payload["bypass_reason"] == "historical_constraint"


def test_pipeline_api_unsupported_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = PipelineExecutionResult(
        run_id="run-unsupported-001",
        status="unsupported",
        feasibility_result=None,
    )

    class StubService:
        def __init__(self) -> None:
            self.run_store = type(
                "StubRunStore",
                (),
                {
                    "load_run": lambda self, run_id: _pipeline_run_payload(
                        run_id=run_id,
                        status="unsupported",
                        zoning_bypassed=True,
                        bypass_reason="unsupported_jurisdiction",
                        layout=None,
                        feasibility=None,
                    )
                },
            )()

        def run(self, **kwargs):
            return expected

    monkeypatch.setattr("bedrock.api.pipeline_api.PipelineService", StubService)
    client = TestClient(create_app())

    response = client.post(
        "/pipeline/run",
        json={"parcel_geometry": _geojson_polygon([(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]), "jurisdiction": "Test City"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unsupported"
    assert payload["layout_result"] is None
    assert payload["feasibility_result"] is None
    assert payload["zoning_bypassed"] is True
    assert payload["bypass_reason"] == "unsupported_jurisdiction"


def test_pipeline_api_invalid_parcel_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubService:
        def run(self, **kwargs):
            raise ValueError("Parcel.geometry must be a GeoJSON Polygon or MultiPolygon")

    monkeypatch.setattr("bedrock.api.pipeline_api.PipelineService", lambda: StubService())
    client = TestClient(create_app())

    response = client.post("/pipeline/run", json={"parcel_geometry": {"type": "Point", "coordinates": [0, 0]}, "jurisdiction": "Test City"})

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_parcel_input"


def test_pipeline_api_unsupported_zoning_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = PipelineExecutionResult(
        run_id="run-unsupported-404-replaced",
        status="unsupported",
        feasibility_result=None,
    )

    class StubService:
        def __init__(self) -> None:
            self.run_store = type(
                "StubRunStore",
                (),
                {
                    "load_run": lambda self, run_id: _pipeline_run_payload(
                        run_id=run_id,
                        status="unsupported",
                        layout=None,
                        feasibility=None,
                        zoning_bypassed=True,
                        bypass_reason="unsupported_jurisdiction",
                    )
                },
            )()

        def run(self, **kwargs):
            return expected

    monkeypatch.setattr("bedrock.api.pipeline_api.PipelineService", StubService)
    client = TestClient(create_app())

    response = client.post("/pipeline/run", json={"parcel_geometry": _geojson_polygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]), "jurisdiction": "Test City"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unsupported"
    assert payload["layout_result"] is None
    assert payload["feasibility_result"] is None
    assert payload["zoning_bypassed"] is True
    assert payload["bypass_reason"] == "unsupported_jurisdiction"


def test_pipeline_api_zoning_ambiguity(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubService:
        def run(self, **kwargs):
            raise AmbiguousZoningMatchError("Parcel geometry overlaps multiple district candidates equally.")

    monkeypatch.setattr("bedrock.api.pipeline_api.PipelineService", lambda: StubService())
    client = TestClient(create_app())

    response = client.post("/pipeline/run", json={"parcel_geometry": _geojson_polygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]), "jurisdiction": "Test City"})

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "ambiguous_district_match"


def test_pipeline_api_layout_solver_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubService:
        def run(self, **kwargs):
            raise PipelineStageError(
                stage="layout.search",
                error="layout_solver_failure",
                message="No viable layouts generated for parcel parcel-001",
                status_code=500,
            )

    monkeypatch.setattr("bedrock.api.pipeline_api.PipelineService", lambda: StubService())
    client = TestClient(create_app())

    response = client.post("/pipeline/run", json={"parcel_geometry": _geojson_polygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]), "jurisdiction": "Test City"})

    assert response.status_code == 500
    assert response.json()["detail"]["error"] == "layout_solver_failure"
    assert response.json()["detail"]["stage"] == "layout.search"


def test_pipeline_api_incomplete_zoning_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubService:
        def run(self, **kwargs):
            raise PipelineStageError(
                stage="zoning.lookup",
                error="incomplete_zoning_rules",
                message="Zoning rules are incomplete for layout execution",
                status_code=422,
                details={"district": "R-1", "missing_fields": ["min_lot_size_sqft"]},
            )

    monkeypatch.setattr("bedrock.api.pipeline_api.PipelineService", lambda: StubService())
    client = TestClient(create_app())

    response = client.post("/pipeline/run", json={"parcel_geometry": _geojson_polygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]), "jurisdiction": "Test City"})

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "incomplete_zoning_rules"
    assert response.json()["detail"]["stage"] == "zoning.lookup"


def test_pipeline_api_invalid_zoning_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubService:
        def run(self, **kwargs):
            raise PipelineStageError(
                stage="zoning.lookup",
                error="invalid_zoning_rules",
                message="Zoning rules are invalid for layout execution",
                status_code=422,
                details={"district": "R3", "violations": ["max_units_per_acre must be <= 80.0"]},
            )

    monkeypatch.setattr("bedrock.api.pipeline_api.PipelineService", lambda: StubService())
    client = TestClient(create_app())

    response = client.post("/pipeline/run", json={"parcel_geometry": _geojson_polygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]), "jurisdiction": "Test City"})

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "invalid_zoning_rules"
    assert response.json()["detail"]["stage"] == "zoning.lookup"


def test_pipeline_api_accepts_market_context(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    expected = PipelineExecutionResult(
        run_id="run-001",
        status="completed",
        feasibility=_feasibility_result("parcel-001", "layout-001"),
    )

    class StubService:
        def __init__(self) -> None:
            self.run_store = type(
                "StubRunStore",
                (),
                {"load_run": lambda self, run_id: _pipeline_run_payload(run_id=run_id)},
            )()

        def run(self, **kwargs):
            captured.update(kwargs)
            return expected

    monkeypatch.setattr("bedrock.api.pipeline_api.PipelineService", StubService)
    client = TestClient(create_app())

    response = client.post(
        "/pipeline/run",
        json={
            "parcel_geometry": _geojson_polygon([(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]),
            "jurisdiction": "Test City",
            "market_context": {
                "estimated_home_price": 490000,
                "cost_per_home": 255000,
                "road_cost_per_ft": 310,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["feasibility_result"]["layout_id"] == "layout-001"
    assert isinstance(captured["market_data"], MarketData)
    assert captured["market_data"].construction_cost_per_home == 255000


def test_pipeline_api_requires_jurisdiction_for_raw_geometry() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/pipeline/run",
        json={"parcel_geometry": _geojson_polygon([(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)])},
    )

    assert response.status_code == 422
