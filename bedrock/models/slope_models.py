"""Pure functions for slope-dependent grading cost multipliers."""

from __future__ import annotations

from bedrock.models.cost_models import DEFAULT_GRADING_COST_FACTOR

# Grading cost multiplier by slope band (mean slope percentage).
# Higher slopes require more earthwork, retaining walls, and erosion control.
SLOPE_GRADING_MULTIPLIERS: list[tuple[float, float, float]] = [
    # (min_slope_pct, max_slope_pct, grading_factor)
    (0.0, 5.0, 0.08),    # flat/gentle — minimal grading
    (5.0, 10.0, 0.15),   # moderate — standard grading
    (10.0, 15.0, 0.22),  # significant — cut/fill + erosion control
    (15.0, 25.0, 0.32),  # steep — retaining walls likely
    (25.0, 100.0, 0.45), # very steep — major earthwork
]


def compute_grading_multiplier(*, mean_slope_pct: float) -> float:
    """Return grading cost factor based on average slope percentage.

    The factor is applied as: grading_cost = (roads_cost + utilities_cost) * factor.
    """
    slope = max(0.0, float(mean_slope_pct))
    for min_slope, max_slope, factor in SLOPE_GRADING_MULTIPLIERS:
        if min_slope <= slope < max_slope:
            return factor
    # Beyond the highest band
    return SLOPE_GRADING_MULTIPLIERS[-1][2]


def assess_slope(*, slope_percent: float | None) -> float:
    """Return grading factor from parcel slope data.

    Falls back to DEFAULT_GRADING_COST_FACTOR when no slope data is available.
    """
    if slope_percent is None:
        return DEFAULT_GRADING_COST_FACTOR
    return compute_grading_multiplier(mean_slope_pct=slope_percent)
