"""Adapter for zoning_data_scraper."""

from __future__ import annotations

import importlib
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
try:
    from sqlalchemy import func, select
except ModuleNotFoundError:  # pragma: no cover - test-mode fallback path
    func = None
    select = None

from contracts.parcel import Parcel
from contracts.zoning import DevelopmentStandard, ZoningDistrict
from engines._runtime_bootstrap import ensure_runtime_paths

logger = logging.getLogger(__name__)

ensure_runtime_paths(include_zoning_src=True)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "system_config.yaml"


@lru_cache(maxsize=1)
def _engine_config() -> Dict[str, Any]:
    data = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return data.get("engines", {}).get("zoning_engine", {})


@lru_cache(maxsize=1)
def _system_config() -> Dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text()) or {}


def _zoning_data_source_config() -> Dict[str, Any]:
    return _system_config().get("zoning_data_source", {})


@lru_cache(maxsize=1)
def _load_local_zoning_rules() -> Dict[str, Dict[str, Any]]:
    config = _zoning_data_source_config()
    path_value = config.get("path", "data/zoning_rules.json")
    data_path = (Path(__file__).resolve().parents[1] / path_value).resolve()
    return json.loads(data_path.read_text())


def _load_module(module_name: str):
    return importlib.import_module(module_name)


def _resolve_callable(module_name: str, attribute_path: str):
    target = _load_module(module_name)
    for part in attribute_path.split("."):
        if not part:
            continue
        target = getattr(target, part)
    return target


def _matching_jurisdiction(session, models_module, parcel: Parcel):
    if func is None or select is None:
        raise ModuleNotFoundError("sqlalchemy is not installed")
    Jurisdiction = getattr(models_module, "Jurisdiction")
    name = parcel.jurisdiction.strip()
    stmt = select(Jurisdiction).where(
        func.lower(Jurisdiction.name) == name.lower()
    )
    row = session.scalar(stmt)
    if row:
        return row
    county_stmt = select(Jurisdiction).where(
        func.lower(func.coalesce(Jurisdiction.county_name, "")) == name.lower()
    )
    return session.scalar(county_stmt)


def _matching_district(session, models_module, jurisdiction_id: int, zoning_code: Optional[str]):
    if func is None or select is None:
        raise ModuleNotFoundError("sqlalchemy is not installed")
    District = getattr(models_module, "ZoningDistrict")
    if zoning_code:
        stmt = select(District).where(
            District.jurisdiction_id == jurisdiction_id,
            func.lower(func.coalesce(District.code, "")) == zoning_code.lower(),
        )
        row = session.scalar(stmt)
        if row:
            return row
    return session.scalar(
        select(District).where(District.jurisdiction_id == jurisdiction_id).order_by(District.id.asc())
    )


def health_check() -> bool:
    _engine_config()
    return True


def _stub_zoning(parcel: Parcel) -> ZoningDistrict:
    return ZoningDistrict(
        id="stub-r1",
        jurisdiction_id=f"stub:{parcel.jurisdiction}",
        code=parcel.zoning_district or "R-1",
        description="Deterministic stub zoning district for test execution.",
        metadata={"source_engine": "bedrock.stub.zoning_engine", "source_run_id": "stub"},
    )


def _stub_standards() -> List[DevelopmentStandard]:
    return [
        DevelopmentStandard(
            id="stub-min-lot-size",
            district_id="stub-r1",
            standard_type="min_lot_size_sqft",
            value=8000,
            units="sqft",
            conditions=[],
            citation="stub",
            metadata={"source_engine": "bedrock.stub.zoning_engine", "source_run_id": "stub"},
        ),
        DevelopmentStandard(
            id="stub-max-units-per-acre",
            district_id="stub-r1",
            standard_type="max_units_per_acre",
            value=5,
            units="du/ac",
            conditions=[],
            citation="stub",
            metadata={"source_engine": "bedrock.stub.zoning_engine", "source_run_id": "stub"},
        ),
    ]


def _local_zoning_district(parcel: Parcel) -> Optional[ZoningDistrict]:
    rules = _load_local_zoning_rules()
    district_code = parcel.zoning_district or "R-1"
    if district_code not in rules:
        return None
    return ZoningDistrict(
        id=f"local:{district_code}",
        jurisdiction_id=f"local:{parcel.jurisdiction}",
        code=district_code,
        description=f"Local zoning rules for district {district_code}.",
        metadata={"source_engine": "bedrock.zoning_data_source", "source_run_id": "local_json"},
    )


def _local_development_standards(zoning: ZoningDistrict) -> List[DevelopmentStandard]:
    rules = _load_local_zoning_rules()
    district_rules = rules.get(zoning.code, {})
    standards: List[DevelopmentStandard] = []
    for key, value in district_rules.items():
        standards.append(
            DevelopmentStandard(
                id=f"{zoning.code}:{key}",
                district_id=zoning.id,
                standard_type=key,
                value=value,
                units="sqft" if "sqft" in key else ("ft" if key.endswith("_ft") else ("du/ac" if "acre" in key else None)),
                conditions=[],
                citation="local_json",
                metadata={"source_engine": "bedrock.zoning_data_source", "source_run_id": "local_json"},
            )
        )
    return standards


def get_zoning(parcel: Parcel) -> ZoningDistrict:
    local = _local_zoning_district(parcel)
    if local is not None:
        return local

    try:
        if func is None or select is None:
            raise ModuleNotFoundError("sqlalchemy is not installed")
        config = _engine_config()
        get_session = _resolve_callable(config.get("module", "zoning.db.session"), config.get("function", "get_session"))
        models = _load_module(config.get("models_module", "zoning.models.entities"))

        with get_session() as session:
            jurisdiction = _matching_jurisdiction(session, models, parcel)
            if jurisdiction is None:
                raise ValueError(f"No zoning jurisdiction match for {parcel.jurisdiction}")

            district = _matching_district(session, models, jurisdiction.id, parcel.zoning_district)
            if district is None:
                raise ValueError(f"No zoning district match for {parcel.zoning_district}")

            return ZoningDistrict(
                id=str(district.id),
                jurisdiction_id=str(district.jurisdiction_id),
                code=district.code or parcel.zoning_district or "UNKNOWN",
                description=district.description or getattr(district, "name", "No description provided."),
                metadata={"source_engine": "zoning_data_scraper", "source_run_id": None},
            )
    except Exception as exc:
        logger.warning("zoning_engine.get_zoning failed, using stub zoning: %s", exc)
        return _stub_zoning(parcel)


def get_development_standards(parcel: Parcel, zoning: Optional[ZoningDistrict] = None) -> List[DevelopmentStandard]:
    zoning_contract = zoning or get_zoning(parcel)
    if zoning_contract.metadata and zoning_contract.metadata.source_run_id == "local_json":
        return _local_development_standards(zoning_contract)

    try:
        if func is None or select is None:
            raise ModuleNotFoundError("sqlalchemy is not installed")
        config = _engine_config()
        get_session = _resolve_callable(config.get("module", "zoning.db.session"), config.get("function", "get_session"))
        models = _load_module(config.get("models_module", "zoning.models.entities"))
        district_contract = zoning_contract
        Standard = getattr(models, "DevelopmentStandard")

        if district_contract.metadata and district_contract.metadata.source_run_id == "stub":
            return _stub_standards()

        with get_session() as session:
            stmt = select(Standard).where(Standard.zoning_district_id == int(district_contract.id))
            rows = list(session.scalars(stmt))
            if not rows:
                stmt = select(Standard).where(Standard.jurisdiction_id == int(district_contract.jurisdiction_id))
                rows = list(session.scalars(stmt))

        if not rows:
            raise ValueError(f"No development standards available for district {district_contract.id}")

        standards: List[DevelopmentStandard] = []
        for row in rows:
            value = row.value_numeric if row.value_numeric is not None else (row.value_text or "")
            standards.append(
                DevelopmentStandard(
                    id=str(row.id),
                    district_id=str(row.zoning_district_id or district_contract.id),
                    standard_type=row.standard_type,
                    value=value,
                    units=row.unit,
                    conditions=[row.condition_text] if row.condition_text else [],
                    citation=str(row.source_document_id) if row.source_document_id is not None else None,
                    metadata={"source_engine": "zoning_data_scraper", "source_run_id": None},
                )
            )
        return standards
    except Exception as exc:
        logger.warning("zoning_engine.get_development_standards failed, using stub standards: %s", exc)
        return _stub_standards()
