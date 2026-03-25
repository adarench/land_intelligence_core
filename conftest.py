from __future__ import annotations

import sys
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parent

for candidate in (
    WORKSPACE_ROOT,
    WORKSPACE_ROOT / "bedrock",
    WORKSPACE_ROOT / "GIS_lot_layout_optimizer",
    WORKSPACE_ROOT / "GIS_lot_layout_optimizer" / "apps" / "python-api",
    WORKSPACE_ROOT / "zoning_data_scraper" / "src",
):
    candidate_str = str(candidate)
    if candidate.exists() and candidate_str not in sys.path:
        sys.path.append(candidate_str)
