"""Pure functions for carry cost and absorption-based project duration modeling."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "absorption_rates.json"

DEFAULT_ABSORPTION_RATE = 3.0   # homes/month
DEFAULT_INTEREST_RATE = 0.075   # annual (7.5%)
DEFAULT_AVG_DRAW_FACTOR = 0.6  # average capital outstanding as fraction of total


@lru_cache(maxsize=1)
def _load_absorption_data() -> dict:
    if not _DATA_PATH.exists():
        return {"default_rate_per_month": DEFAULT_ABSORPTION_RATE, "default_interest_rate": DEFAULT_INTEREST_RATE, "jurisdictions": {}}
    with open(_DATA_PATH) as f:
        return json.load(f)


def resolve_absorption_rate(*, jurisdiction: str | None) -> float:
    """Return jurisdiction-specific absorption rate (homes/month), or default."""
    data = _load_absorption_data()
    default = float(data.get("default_rate_per_month", DEFAULT_ABSORPTION_RATE))
    if not jurisdiction:
        return default
    jurisdictions = data.get("jurisdictions", {})
    key = jurisdiction.strip()
    if key in jurisdictions:
        return float(jurisdictions[key].get("rate_per_month", default))
    key_lower = key.lower()
    for name, entry in jurisdictions.items():
        if name.lower() == key_lower:
            return float(entry.get("rate_per_month", default))
    return default


def resolve_interest_rate() -> float:
    """Return configured interest rate for carry cost calculation."""
    data = _load_absorption_data()
    return float(data.get("default_interest_rate", DEFAULT_INTEREST_RATE))


@dataclass(frozen=True)
class CarryCostAssessment:
    """Result of carry cost calculation."""

    absorption_rate: float          # homes/month
    project_duration_months: float  # total project duration
    interest_rate: float            # annual rate
    carry_cost: float               # total financing/carry cost


def calculate_carry_cost(
    *,
    units: int,
    total_capital: float,
    absorption_rate: float = DEFAULT_ABSORPTION_RATE,
    interest_rate: float = DEFAULT_INTEREST_RATE,
) -> CarryCostAssessment:
    """Calculate financing carry cost based on project duration and capital deployed.

    Uses a simple average-draw model: assumes ~60% of total capital is outstanding
    on average over the project duration (capital ramps up during construction,
    then draws down as homes sell).
    """
    if units <= 0 or total_capital <= 0:
        return CarryCostAssessment(
            absorption_rate=absorption_rate,
            project_duration_months=0.0,
            interest_rate=interest_rate,
            carry_cost=0.0,
        )

    duration_months = math.ceil(units / max(absorption_rate, 0.1))
    duration_years = duration_months / 12.0
    avg_outstanding = total_capital * DEFAULT_AVG_DRAW_FACTOR
    carry_cost = avg_outstanding * interest_rate * duration_years

    return CarryCostAssessment(
        absorption_rate=absorption_rate,
        project_duration_months=float(duration_months),
        interest_rate=interest_rate,
        carry_cost=carry_cost,
    )
