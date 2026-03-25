"""Adapter for GIS_lot_layout_optimizer."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import json
import logging
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

import yaml

from contracts.layout import SubdivisionLayout
from contracts.parcel import Parcel
from contracts.zoning import DevelopmentStandard, ZoningDistrict
from engines._runtime_bootstrap import ensure_runtime_paths
from services.layout_service import ZoningRules, search_subdivision_layout

logger = logging.getLogger(__name__)

ensure_runtime_paths(include_gis_python_api=True)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "system_config.yaml"


@lru_cache(maxsize=1)
def _engine_config() -> Dict[str, Any]:
    data = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return data.get("engines", {}).get("parcel_engine", {})


def _repo_root() -> Path:
    repo_path = _engine_config().get("repo_path", "../GIS_lot_layout_optimizer")
    return (Path(__file__).resolve().parents[1] / repo_path).resolve()


@lru_cache(maxsize=1)
def _system_config() -> Dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text()) or {}


def _parcel_data_source_config() -> Dict[str, Any]:
    return _system_config().get("parcel_data_source", {})


@lru_cache(maxsize=1)
def _load_geojson_data() -> Dict[str, Any]:
    config = _parcel_data_source_config()
    path_value = config.get("path", "data/test_parcels.geojson")
    data_path = (Path(__file__).resolve().parents[1] / path_value).resolve()
    return json.loads(data_path.read_text())


def _find_geojson_feature(parcel_id: str) -> Optional[Dict[str, Any]]:
    data = _load_geojson_data()
    for feature in data.get("features", []):
        properties = feature.get("properties", {})
        if properties.get("parcel_id") == parcel_id:
            return feature
    return None


def _load_module(module_name: str) -> ModuleType:
    repo_root = _repo_root()
    if module_name.endswith(".py") or "/" in module_name:
        module_path = (repo_root / module_name).resolve()
        spec = importlib.util.spec_from_file_location(f"bedrock_ext_{module_path.stem}", module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load module from {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return importlib.import_module(module_name)


def _resolve_callable(attribute_path: str):
    module_name, _, attr_path = attribute_path.partition(":")
    module = _load_module(module_name)
    target = module
    for part in attr_path.split("."):
        if not part:
            continue
        target = getattr(target, part)
    return target


def _call(target, *args, **kwargs):
    if inspect.isclass(target):
        raise TypeError("Target must be a callable attribute, not a class.")

    result = target(*args, **kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _coerce_parcel(record: Any) -> Parcel:
    payload = record.model_dump() if hasattr(record, "model_dump") else dict(record)
    geometry = payload.get("geometryGeoJSON") or payload.get("geometry") or {}
    area_sqft = payload.get("areaSqft")
    area_acres = payload.get("areaAcres")
    area = float(area_sqft) if area_sqft is not None else float(area_acres or 0.0) * 43560.0

    return Parcel(
        parcel_id=payload["id"],
        geometry=geometry,
        area=area,
        jurisdiction=str(payload.get("county") or payload.get("state") or "unknown"),
        zoning_district=payload.get("zoningCode"),
        utilities=[],
        access_points=[],
        topography={},
        existing_structures=[],
        metadata={
            "source_engine": "GIS_lot_layout_optimizer",
            "source_run_id": None,
        },
    )


def _extract_constraint_value(
    zoning_constraints: List[Dict[str, object]],
    standard_types: Set[str],
    default: float,
) -> float:
    for item in zoning_constraints:
        standard_type = str(item.get("standard_type", "")).lower()
        if standard_type not in standard_types:
            continue
        value = item.get("value")
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return default


def _standards_to_layout_parameters(
    zoning: ZoningDistrict,
    standards: List[DevelopmentStandard],
) -> Dict[str, float]:
    parameters: Dict[str, float] = {
        "min_lot_size_sqft": 6000.0,
        "front_setback_ft": 0.0,
        "side_setback_ft": 0.0,
        "rear_setback_ft": 0.0,
        "road_width_ft": 32.0,
        "min_frontage_ft": 50.0,
        "lot_depth_ft": 110.0,
    }

    for standard in standards:
        key = standard.standard_type.lower()
        value = standard.value
        if isinstance(value, bool):
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if key in parameters:
            parameters[key] = numeric_value

    parameters["effective_buildable_depth_ft"] = max(
        40.0,
        parameters["lot_depth_ft"] - parameters["front_setback_ft"] - parameters["rear_setback_ft"],
    )
    parameters["effective_buildable_width_ft"] = max(
        30.0,
        parameters["min_frontage_ft"] - (2.0 * parameters["side_setback_ft"]),
    )
    if parameters["min_lot_size_sqft"] > 0:
        derived_frontage = parameters["min_lot_size_sqft"] / max(parameters["effective_buildable_depth_ft"], 40.0)
        parameters["min_frontage_ft"] = max(35.0, min(80.0, derived_frontage))
    parameters["zoning_density_hint"] = 1.0 if zoning.code else 0.0
    return parameters


def _coerce_layout(parcel: Parcel, response: Any) -> SubdivisionLayout:
    payload = response.model_dump() if hasattr(response, "model_dump") else dict(response)
    results = payload.get("results") or []
    top_summary = results[0] if results else {}
    if hasattr(top_summary, "model_dump"):
        top_summary = top_summary.model_dump()

    top_geojson = payload.get("topResultGeoJSON") or {"type": "FeatureCollection", "features": []}
    features = top_geojson.get("features", [])
    lot_geometries = [feature["geometry"] for feature in features if feature.get("properties", {}).get("layer") == "lots"]
    street_network = [feature["geometry"] for feature in features if feature.get("properties", {}).get("layer") == "road"]
    lot_area = sum(
        float(feature.get("properties", {}).get("area_sqft", 0.0))
        for feature in features
        if feature.get("properties", {}).get("layer") == "lots"
    )

    return SubdivisionLayout(
        layout_id=f"layout-{uuid4()}",
        parcel_id=parcel.parcel_id,
        street_network=street_network,
        lot_geometries=lot_geometries,
        lot_count=int(top_summary.get("lotCount", len(lot_geometries))),
        open_space_area=max(parcel.area - lot_area, 0.0),
        road_length=float(top_summary.get("totalRoadFt", 0.0)),
        utility_length=0.0,
        metadata={
            "source_engine": "GIS_lot_layout_optimizer",
            "source_run_id": None,
        },
    )


def health_check() -> bool:
    _engine_config()
    return True


def _stub_parcel(parcel_id: str) -> Parcel:
    return Parcel(
        parcel_id=parcel_id,
        geometry={
            "type": "Polygon",
            "coordinates": [[
                [0.0, 0.0],
                [0.0, 0.0028],
                [0.0028, 0.0028],
                [0.0028, 0.0],
                [0.0, 0.0],
            ]],
        },
        area=100000.0,
        jurisdiction="test_county",
        zoning_district="R-1",
        utilities=["water", "sewer"],
        access_points=[{"type": "Point", "coordinates": [0.0, 0.0014]}],
        topography={"slope_percent": 2.0},
        existing_structures=[],
        metadata={"source_engine": "bedrock.stub.parcel_engine", "source_run_id": "stub"},
    )


def _parcel_from_feature(feature: Dict[str, Any]) -> Parcel:
    properties = feature.get("properties", {})
    area = float(properties.get("area_sqft", 0.0))
    return Parcel(
        parcel_id=str(properties["parcel_id"]),
        geometry=feature.get("geometry", {}),
        area=area,
        jurisdiction=str(properties.get("jurisdiction", "test_county")),
        zoning_district=properties.get("zoning_district", "R-1"),
        utilities=list(properties.get("utilities", ["water", "sewer"])),
        access_points=list(properties.get("access_points", [])),
        topography=dict(properties.get("topography", {"slope_percent": 2.0})),
        existing_structures=list(properties.get("existing_structures", [])),
        metadata={"source_engine": "bedrock.parcel_data_source", "source_run_id": "geojson"},
    )


def _stub_layout(parcel: Parcel) -> SubdivisionLayout:
    return SubdivisionLayout(
        layout_id="stub-layout-test-parcel-001",
        parcel_id=parcel.parcel_id,
        street_network=[
            {"type": "LineString", "coordinates": [[0.0, 0.0014], [0.0028, 0.0014]]}
        ],
        lot_geometries=[
            {
                "type": "Polygon",
                "coordinates": [[
                    [index * 0.00028, 0.0],
                    [index * 0.00028, 0.001],
                    [(index + 1) * 0.00028, 0.001],
                    [(index + 1) * 0.00028, 0.0],
                    [index * 0.00028, 0.0],
                ]],
            }
            for index in range(10)
        ],
        lot_count=10,
        open_space_area=10000.0,
        road_length=1.0,
        utility_length=1.0,
        metadata={"source_engine": "bedrock.stub.parcel_engine", "source_run_id": "stub"},
    )


def get_parcel(parcel_id: str) -> Parcel:
    feature = _find_geojson_feature(parcel_id)
    if feature is not None:
        return _parcel_from_feature(feature)

    logger.warning("parcel_engine.get_parcel could not find parcel_id=%s in configured data source", parcel_id)
    try:
        config = _engine_config()
        module_name = config.get("module", "services.parcel_service")
        attribute = config.get("function", "ParcelService.get_parcel")
        target = _resolve_callable(f"{module_name}:{attribute}")
        if ".get_parcel" in attribute:
            service_cls = _resolve_callable(f"{module_name}:{attribute.rsplit('.', 1)[0]}")
            service = service_cls()
            record = _call(getattr(service, "get_parcel"), parcel_id)
        else:
            record = _call(target, parcel_id)
        if record is None:
            raise ValueError(f"Parcel not found: {parcel_id}")
        return _coerce_parcel(record)
    except Exception as exc:
        logger.warning("parcel_engine.get_parcel failed, using stub parcel: %s", exc)
        return _stub_parcel(parcel_id)


def generate_layout(
    parcel: Parcel,
    zoning: ZoningDistrict,
    standards: List[DevelopmentStandard],
) -> SubdivisionLayout:
    try:
        layout_parameters = _standards_to_layout_parameters(zoning, standards)
        zoning_rules = ZoningRules(
            district=zoning.code or zoning.description or zoning.id,
            min_lot_size_sqft=layout_parameters["min_lot_size_sqft"],
            max_units_per_acre=max(float(parcel.area) / max(layout_parameters["min_lot_size_sqft"], 1.0) / 43560.0, 1.0),
            setbacks={
                "front": layout_parameters["front_setback_ft"],
                "side": layout_parameters["side_setback_ft"],
                "rear": layout_parameters["rear_setback_ft"],
            },
        )
        return search_subdivision_layout(parcel, zoning_rules, max_candidates=24)
    except Exception as exc:
        logger.warning("parcel_engine.generate_layout failed, using stub layout: %s", exc)
        return _stub_layout(parcel)
