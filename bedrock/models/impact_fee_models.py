"""Jurisdiction-aware impact fee lookup replacing flat per-unit permitting cost."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from bedrock.models.cost_models import DEFAULT_PERMITTING_COST_PER_UNIT

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "impact_fees.json"


@lru_cache(maxsize=1)
def _load_impact_fees() -> dict:
    if not _DATA_PATH.exists():
        return {"default_per_unit": DEFAULT_PERMITTING_COST_PER_UNIT, "jurisdictions": {}}
    with open(_DATA_PATH) as f:
        return json.load(f)


def resolve_impact_fee_per_unit(*, jurisdiction: str | None) -> float:
    """Return jurisdiction-specific impact fee per unit, or default if not found."""
    data = _load_impact_fees()
    default = float(data.get("default_per_unit", DEFAULT_PERMITTING_COST_PER_UNIT))
    if not jurisdiction:
        return default
    jurisdictions = data.get("jurisdictions", {})
    key = jurisdiction.strip()
    # Try exact match first, then case-insensitive
    if key in jurisdictions:
        return float(jurisdictions[key].get("impact_fee_per_unit", default))
    key_lower = key.lower()
    for name, entry in jurisdictions.items():
        if name.lower() == key_lower:
            return float(entry.get("impact_fee_per_unit", default))
    return default


def calculate_impact_fees(*, units: int, jurisdiction: str | None) -> float:
    """Total impact fees for a development based on jurisdiction-specific rates."""
    return float(units) * resolve_impact_fee_per_unit(jurisdiction=jurisdiction)
