"""Adapter for takeoff_archive."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import yaml

from contracts.parcel import Parcel

workspace_root = Path(__file__).resolve().parents[2]
sys.path.append(str(workspace_root / "GIS_lot_layout_optimizer"))
sys.path.append(str(workspace_root / "zoning_data_scraper"))
sys.path.append(str(workspace_root / "takeoff_archive"))

CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "system_config.yaml"


@lru_cache(maxsize=1)
def _engine_config() -> Dict[str, Any]:
    data = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return data.get("engines", {}).get("plan_engine", {})


def health_check() -> bool:
    _engine_config()
    return True


def _not_available(capability: str) -> RuntimeError:
    return RuntimeError(
        f"takeoff_archive does not expose a stable Python entrypoint for {capability}. "
        "The current repository appears to provide TypeScript-first plan intelligence surfaces."
    )


def extract_plan_geometry(document: Dict[str, Any]) -> Dict[str, Any]:
    raise _not_available("extract_plan_geometry")


def reconstruct_parcels(plan_document: Dict[str, Any]) -> List[Parcel]:
    raise _not_available("reconstruct_parcels")


def infer_infrastructure(parcel: Parcel) -> Dict[str, Any]:
    raise _not_available("infer_infrastructure")
