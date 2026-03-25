from __future__ import annotations

import sys
from pathlib import Path

import pytest
from shapely.geometry import shape
from zoning_data_scraper.services.zoning_overlay import lookup_zoning_district


ROOT = Path(__file__).resolve().parents[2]
BEDROCK_ROOT = ROOT / "bedrock"

for candidate in (ROOT, BEDROCK_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from bedrock.services.jurisdiction_resolver import get_default_resolver, resolve_jurisdiction
from bedrock.services.parcel_service import ParcelService
from bedrock.services.parcel_store import ParcelStore
from tests.runtime_validation_utils import RUNTIME_CASES


@pytest.fixture
def parcel_service(tmp_path: Path) -> ParcelService:
    return ParcelService(store=ParcelStore(tmp_path / "zoning_ready.db"))


def test_normalized_parcel_geometry_is_accepted_by_zoning_lookup(parcel_service: ParcelService) -> None:
    parcel = parcel_service.load_parcel(
        parcel_id="draper-zoning-ready",
        geometry={
            "type": "Polygon",
            "coordinates": [[
                [-111.871, 40.499],
                [-111.871, 40.501],
                [-111.869, 40.501],
                [-111.869, 40.499],
                [-111.871, 40.499],
            ]],
        },
        jurisdiction="Draper",
    )

    result = lookup_zoning_district(
        shape(parcel.geometry),
        parcel_jurisdiction=parcel.jurisdiction,
        dataset_root=ROOT / "zoning_data_scraper",
    )

    assert parcel.geometry["type"] == "Polygon"
    assert result.jurisdiction == "Draper"
    assert result.district


def test_centroid_matches_normalized_geometry_centroid(parcel_service: ParcelService) -> None:
    parcel = parcel_service.load_parcel(
        parcel_id="lehi-centroid",
        geometry={
            "type": "Polygon",
            "coordinates": [[
                [-111.881, 40.389],
                [-111.881, 40.391],
                [-111.879, 40.391],
                [-111.879, 40.389],
            ]],
        },
        jurisdiction="Lehi",
    )

    geom_centroid = shape(parcel.geometry).centroid

    assert parcel.centroid == pytest.approx([geom_centroid.x, geom_centroid.y], abs=1e-6)


def test_bounding_box_matches_normalized_geometry_bounds(parcel_service: ParcelService) -> None:
    parcel = parcel_service.load_parcel(
        parcel_id="draper-bbox",
        geometry={
            "type": "Polygon",
            "coordinates": [[
                [-111.871, 40.499],
                [-111.871, 40.501],
                [-111.869, 40.501],
                [-111.869, 40.499],
                [-111.871, 40.499],
            ]],
        },
        jurisdiction="Draper",
    )

    assert parcel.bounding_box == pytest.approx(list(shape(parcel.geometry).bounds), abs=1e-6)


def test_jurisdiction_resolution_is_reliable_for_supported_overlay_cities() -> None:
    resolver = get_default_resolver()

    for jurisdiction in ("Salt Lake City", "Lehi", "Draper"):
        point = resolver.representative_point(jurisdiction)
        assert point is not None
        assert resolve_jurisdiction(point) == jurisdiction


def test_runtime_reference_parcels_resolve_to_expected_districts() -> None:
    for runtime_case in RUNTIME_CASES:
        result = lookup_zoning_district(
            shape(runtime_case.parcel_payload()["geometry"]),
            parcel_jurisdiction=runtime_case.jurisdiction,
            dataset_root=ROOT / "zoning_data_scraper",
        )
        assert result.district == runtime_case.expected_district


def test_invalid_multipolygon_does_not_raise_topology_exception() -> None:
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [
            [[
                [-111.880, 40.490],
                [-111.870, 40.500],
                [-111.880, 40.500],
                [-111.870, 40.490],
                [-111.880, 40.490],
            ]],
            [[
                [-111.8795, 40.4905],
                [-111.8695, 40.5005],
                [-111.8795, 40.5005],
                [-111.8695, 40.4905],
                [-111.8795, 40.4905],
            ]],
        ],
    }

    result = lookup_zoning_district(
        shape(geometry),
        parcel_jurisdiction="Draper",
        dataset_root=ROOT / "zoning_data_scraper",
    )

    assert result.district
