"""Shared import bootstrap for active external runtimes.

This keeps active engine adapters aligned on the same import path setup
without coupling them to archival repositories.
"""

from __future__ import annotations

import sys
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def ensure_runtime_paths(*, include_gis_python_api: bool = False, include_zoning_src: bool = False) -> None:
    """Register active adjacent runtime roots exactly once."""

    candidates = [
        WORKSPACE_ROOT / "GIS_lot_layout_optimizer",
        WORKSPACE_ROOT / "zoning_data_scraper",
    ]
    if include_gis_python_api:
        candidates.append(WORKSPACE_ROOT / "GIS_lot_layout_optimizer" / "apps" / "python-api")
    if include_zoning_src:
        candidates.append(WORKSPACE_ROOT / "zoning_data_scraper" / "src")

    for candidate in candidates:
        candidate_str = str(candidate)
        if candidate_str not in sys.path:
            sys.path.append(candidate_str)
