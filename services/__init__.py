from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
__path__ = [
    str(_ROOT / "bedrock" / "services"),
    str(_ROOT / "GIS_lot_layout_optimizer" / "services"),
]
