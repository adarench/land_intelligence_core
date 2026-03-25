from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
BEDROCK_ROOT = REPO_ROOT / "bedrock"
if str(BEDROCK_ROOT) not in sys.path:
    sys.path.insert(0, str(BEDROCK_ROOT))

from api.parcel_api import create_app
from services.jurisdiction_resolver import get_default_resolver
from services.jurisdiction_resolver import resolve_jurisdiction
from services.parcel_service import ParcelService
from services.parcel_store import ParcelStore


@pytest.fixture
def parcel_service(tmp_path: Path) -> ParcelService:
    store = ParcelStore(tmp_path / "parcels.db")
    return ParcelService(store=store)


@pytest.fixture
def client(parcel_service: ParcelService) -> TestClient:
    return TestClient(create_app(parcel_service))


def test_parcel_store_save_and_get_parcel(tmp_path: Path):
    store = ParcelStore(tmp_path / "parcels.db")
    service = ParcelService(store=store)
    parcel = service.load_parcel(
        parcel_id="store-alpha",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [0, 660], [660, 660], [660, 0]]],
        },
        jurisdiction="SampleCounty_CA",
    )

    assert store.parcel_exists("store-alpha") is True
    stored = store.get_parcel("store-alpha")
    assert stored is not None
    assert stored.parcel_id == parcel.parcel_id
    assert stored.crs == "BEDROCK:LOCAL_FEET"
    assert stored.jurisdiction == "SampleCounty_CA"
    assert stored.area_sqft == pytest.approx(435600.0)
    assert stored.bounding_box == pytest.approx([0.0, 0.0, 660.0, 660.0])


def test_parcel_store_rejects_duplicate_ids(parcel_service: ParcelService):
    payload = {
        "parcel_id": "dup-alpha",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [0, 660], [660, 660], [660, 0]]],
        },
        "jurisdiction": "SampleCounty_CA",
    }

    parcel_service.load_parcel(**payload)
    duplicate = parcel_service.load_parcel(**payload)
    assert duplicate.parcel_id == "dup-alpha"
    assert duplicate.area_sqft == pytest.approx(435600.0)


def test_parcel_store_rejects_conflicting_duplicate_ids(tmp_path: Path):
    store = ParcelStore(tmp_path / "parcels.db")
    service = ParcelService(store=store)

    service.load_parcel(
        parcel_id="dup-conflict",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [0, 100], [100, 100], [100, 0]]],
        },
        jurisdiction="SampleCounty_CA",
    )

    conflicting = store.get_parcel("dup-conflict").model_copy(
        update={
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.0, 0.0], [0.0, 120.0], [120.0, 120.0], [120.0, 0.0], [0.0, 0.0]]],
            },
            "area_sqft": 14400.0,
            "centroid": [60.0, 60.0],
            "bounding_box": [0.0, 0.0, 120.0, 120.0],
        }
    )

    with pytest.raises(ValueError, match="Parcel already exists with different data: dup-conflict"):
        store.save_parcel(conflicting)


def test_jurisdiction_resolver_returns_stub_match():
    resolver = get_default_resolver()
    assert "Lehi" in resolver.jurisdiction_names()
    assert "Draper" in resolver.jurisdiction_names()

    lehi_point = resolver.representative_point("Lehi")
    draper_point = resolver.representative_point("Draper")

    assert lehi_point is not None
    assert draper_point is not None
    assert resolve_jurisdiction(lehi_point) == "Lehi"
    assert resolve_jurisdiction(draper_point) == "Draper"
    assert resolve_jurisdiction([0.0, 0.0]) is None


def test_load_parcel_persists_and_returns_normalized_contract(client: TestClient):
    response = client.post(
        "/parcel/load",
        json={
            "parcel_id": "parcel-alpha",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [0, 660], [660, 660], [660, 0]]],
            },
            "jurisdiction": "SampleCounty_CA",
            "zoning_district": "R-1",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["parcel_id"] == "parcel-alpha"
    assert payload["jurisdiction"] == "SampleCounty_CA"
    assert payload["geometry"]["type"] in {"Polygon", "MultiPolygon"}
    assert payload["geometry"]["coordinates"][0][0] == [0.0, 0.0]
    assert payload["geometry"]["coordinates"][0][-1] == [0.0, 0.0]
    assert payload["area_sqft"] == pytest.approx(435600.0)
    assert payload["centroid"] == pytest.approx([330.0, 330.0])
    assert payload["bounding_box"] == pytest.approx([0.0, 0.0, 660.0, 660.0])
    assert payload["crs"] == "BEDROCK:LOCAL_FEET"
    assert payload["zoning_district"] == "R-1"


def test_get_parcel_returns_persisted_record(client: TestClient):
    load_response = client.post(
        "/parcel/load",
        json={
            "parcel_id": "parcel-retrieve",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-111.90, 40.75],
                    [-111.90, 40.7505],
                    [-111.8995, 40.7505],
                    [-111.8995, 40.75],
                    [-111.90, 40.75],
                ]],
            },
            "jurisdiction": "Salt Lake City",
        },
    )
    assert load_response.status_code == 200

    response = client.get("/parcel/parcel-retrieve")

    assert response.status_code == 200
    payload = response.json()
    assert payload["parcel_id"] == "parcel-retrieve"
    assert payload["jurisdiction"] == "Salt Lake City"
    assert payload["crs"] == "EPSG:4326"
    assert payload["area_sqft"] > 20000
    assert payload["centroid"] == pytest.approx([-111.89975, 40.75025], abs=1e-6)


def test_load_parcel_preserves_zoning_district_through_store(parcel_service: ParcelService):
    parcel = parcel_service.load_parcel(
        parcel_id="parcel-zoning-hint",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [0, 660], [660, 660], [660, 0]]],
        },
        jurisdiction="Murray",
        zoning_district="M-G",
    )

    assert parcel.zoning_district == "M-G"

    stored = parcel_service.get_parcel("parcel-zoning-hint")
    assert stored is not None
    assert stored.zoning_district == "M-G"


def test_load_parcel_updates_existing_missing_zoning_district(parcel_service: ParcelService):
    parcel_service.load_parcel(
        parcel_id="parcel-zoning-upgrade",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [0, 660], [660, 660], [660, 0]]],
        },
        jurisdiction="Murray",
    )

    upgraded = parcel_service.load_parcel(
        parcel_id="parcel-zoning-upgrade",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [0, 660], [660, 660], [660, 0]]],
        },
        jurisdiction="Murray",
        zoning_district="M-G",
    )

    assert upgraded.zoning_district == "M-G"


def test_get_parcel_returns_404_when_missing(client: TestClient):
    response = client.get("/parcel/missing-parcel")

    assert response.status_code == 404
    assert response.json()["detail"] == "Parcel not found"


def test_load_parcel_repairs_simple_self_intersection(client: TestClient):
    response = client.post(
        "/parcel/load",
        json={
            "parcel_id": "parcel-bad",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [2, 2], [0, 2], [2, 0], [0, 0]]],
            },
            "jurisdiction": "SampleCounty_CA",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["geometry"]["type"] in {"Polygon", "MultiPolygon"}
    assert payload["crs"] == "EPSG:4326"
    assert payload["area_sqft"] > 0
    if payload["geometry"]["type"] == "Polygon":
        assert len(payload["geometry"]["coordinates"][0]) >= 4
    else:
        assert all(len(polygon[0]) >= 4 for polygon in payload["geometry"]["coordinates"])


def test_load_parcel_repair_preserves_bowtie_area(parcel_service: ParcelService):
    parcel = parcel_service.load_parcel(
        parcel_id="parcel-bowtie-preserve",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [200, 200], [0, 200], [200, 0], [0, 0]]],
        },
        jurisdiction="SampleCounty_CA",
    )

    assert parcel.geometry["type"] == "MultiPolygon"
    assert parcel.area_sqft == pytest.approx(20000.0)


def test_load_parcel_accepts_multipolygon_geometry(parcel_service: ParcelService):
    parcel = parcel_service.load_parcel(
        parcel_id="parcel-multipolygon",
        geometry={
            "type": "MultiPolygon",
            "coordinates": [
                [[[0, 0], [0, 200], [200, 200], [200, 0], [0, 0]]],
                [[[400, 0], [400, 100], [500, 100], [500, 0], [400, 0]]],
            ],
        },
        jurisdiction="SampleCounty_CA",
    )

    assert parcel.geometry["type"] == "MultiPolygon"
    assert parcel.area_sqft == pytest.approx(50000.0)
    assert parcel.bounding_box == pytest.approx([0.0, 0.0, 500.0, 200.0])


def test_geometry_repair_handles_duplicate_vertices(parcel_service: ParcelService):
    parcel = parcel_service.load_parcel(
        parcel_id="repair-duplicate",
        geometry={
            "type": "Polygon",
            "coordinates": [[
                [0, 0],
                [0, 0],
                [0, 660],
                [660, 660],
                [660, 660],
                [660, 0],
                [0, 0],
            ]],
        },
        jurisdiction="SampleCounty_CA",
    )

    ring = parcel.geometry["coordinates"][0]
    assert ring[0] == ring[-1]
    assert len(ring) == 5
    assert {tuple(point) for point in ring[:-1]} == {
        (0.0, 0.0),
        (0.0, 660.0),
        (660.0, 660.0),
        (660.0, 0.0),
    }


def test_geometry_rejects_invalid_coordinate_ranges(client: TestClient):
    response = client.post(
        "/parcel/load",
        json={
            "parcel_id": "bad-range",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-200.0, 40.75],
                    [-200.0, 40.76],
                    [-199.9, 40.76],
                    [-199.9, 40.75],
                    [-200.0, 40.75],
                ]],
            },
            "jurisdiction": "Salt Lake City",
        },
    )

    assert response.status_code == 400
    assert "longitude" in response.json()["detail"]


def test_spatial_index_returns_bbox_matches(parcel_service: ParcelService):
    parcel_service.load_parcel(
        parcel_id="bbox-alpha",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [0, 100], [100, 100], [100, 0]]],
        },
        jurisdiction="SampleCounty_CA",
    )
    parcel_service.load_parcel(
        parcel_id="bbox-beta",
        geometry={
            "type": "Polygon",
            "coordinates": [[[500, 500], [500, 600], [600, 600], [600, 500]]],
        },
        jurisdiction="SampleCounty_CA",
    )

    hits = parcel_service.store.search_by_bbox(50, 50, 150, 150)

    assert [parcel.parcel_id for parcel in hits] == ["bbox-alpha"]


def test_retrieval_latency_is_minimal(parcel_service: ParcelService):
    parcel_service.load_parcel(
        parcel_id="latency-alpha",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [0, 50], [50, 50], [50, 0]]],
        },
        jurisdiction="SampleCounty_CA",
    )

    started = time.perf_counter()
    parcel = parcel_service.get_parcel("latency-alpha")
    elapsed = time.perf_counter() - started

    assert parcel is not None
    assert elapsed < 0.05


def test_repeated_load_is_deterministic_and_idempotent(parcel_service: ParcelService):
    payload = {
        "parcel_id": "stable-alpha",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [-111.90, 40.75],
                [-111.90, 40.7505],
                [-111.8995, 40.7505],
                [-111.8995, 40.75],
                [-111.90, 40.75],
            ]],
        },
        "jurisdiction": "Salt Lake City",
    }

    first = parcel_service.load_parcel(**payload)
    second = parcel_service.load_parcel(**payload)

    assert second.model_dump() == first.model_dump()


def test_load_parcel_requires_supported_jurisdiction(client: TestClient):
    response = client.post(
        "/parcel/load",
        json={
            "parcel_id": "bad-jurisdiction",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]],
            },
            "jurisdiction": "Unknown City",
        },
    )

    assert response.status_code == 400
    assert "Unsupported jurisdiction" in response.json()["detail"]


def test_load_parcel_rejects_mixed_crs_input(client: TestClient):
    response = client.post(
        "/parcel/load",
        json={
            "parcel_id": "mixed-crs",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0, 120], [120, 120], [120, 0], [0, 0]]],
                },
                "jurisdiction": "SampleCounty_CA",
                "crs": "EPSG:4326",
        },
    )

    assert response.status_code == 400
    assert "does not match declared CRS" in response.json()["detail"]


def test_load_parcel_requires_jurisdiction_input(client: TestClient):
    response = client.post(
        "/parcel/load",
        json={
            "parcel_id": "missing-jurisdiction",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]],
            },
        },
    )

    assert response.status_code == 422


def test_repeated_load_rejects_conflicting_geometry_for_same_parcel_id(parcel_service: ParcelService):
    parcel_service.load_parcel(
        parcel_id="conflict-alpha",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [0, 100], [100, 100], [100, 0]]],
        },
        jurisdiction="SampleCounty_CA",
    )

    with pytest.raises(ValueError, match="Parcel already exists with different data: conflict-alpha"):
        parcel_service.load_parcel(
            parcel_id="conflict-alpha",
            geometry={
                "type": "Polygon",
                "coordinates": [[[0, 0], [0, 120], [120, 120], [120, 0]]],
            },
            jurisdiction="SampleCounty_CA",
        )


def test_can_load_and_retrieve_100_parcels_under_latency_budget(parcel_service: ParcelService):
    parcel_ids: list[str] = []

    for index in range(100):
        x0 = float(index * 20)
        y0 = float(index * 10)
        parcel_id = f"bulk-{index:03d}"
        parcel_ids.append(parcel_id)
        parcel_service.load_parcel(
            parcel_id=parcel_id,
            geometry={
                "type": "Polygon",
                "coordinates": [[
                    [x0, y0],
                    [x0, y0 + 10.0],
                    [x0 + 10.0, y0 + 10.0],
                    [x0 + 10.0, y0],
                    [x0, y0],
                ]],
            },
            jurisdiction="SampleCounty_CA",
        )

    started = time.perf_counter()
    loaded = [parcel_service.get_parcel(parcel_id) for parcel_id in parcel_ids]
    elapsed = time.perf_counter() - started
    average_latency_ms = (elapsed / len(parcel_ids)) * 1000.0

    assert all(parcel is not None for parcel in loaded)
    assert average_latency_ms < 100.0


def test_phase1_stabilization_loads_20_parcels_with_zero_geometry_failures(parcel_service: ParcelService):
    failures: list[str] = []
    loaded_ids: list[str] = []

    for index in range(20):
        x0 = float(index * 15)
        y0 = float(index * 7)
        parcel_id = f"phase1-{index:02d}"
        geometry = {
            "type": "Polygon",
            "coordinates": [[
                [x0, y0],
                [x0, y0],
                [x0, y0 + 12.0],
                [x0 + 12.0, y0 + 12.0],
                [x0 + 12.0, y0 + 12.0],
                [x0 + 12.0, y0],
                [x0, y0],
            ]],
        }
        try:
            parcel_service.load_parcel(
                parcel_id=parcel_id,
                geometry=geometry,
                jurisdiction="SampleCounty_CA",
            )
            loaded_ids.append(parcel_id)
        except ValueError as exc:
            failures.append(f"{parcel_id}: {exc}")

    assert failures == []
    assert len(loaded_ids) == 20
    assert all(parcel_service.parcel_exists(parcel_id) for parcel_id in loaded_ids)
