"""Compatibility proxy for GIS layout models."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[2] / "GIS_lot_layout_optimizer" / "services" / "layout_models.py"
_SPEC = importlib.util.spec_from_file_location("gis_layout_models_proxy", _MODULE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Unable to load layout models from {_MODULE_PATH}")

_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

LayoutResult = _MODULE.LayoutResult
ParcelInput = _MODULE.ParcelInput
ZoningInput = _MODULE.ZoningInput

__all__ = ["LayoutResult", "ParcelInput", "ZoningInput"]
