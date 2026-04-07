"""Service for enriching parcels with slope data from USGS 3DEP elevation models."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from bedrock.contracts.parcel import Parcel
from bedrock.models.slope_models import assess_slope


class SlopeEnrichmentService:
    """Computes mean slope for a parcel from cached DEM tiles.

    When no DEM data is available, the service returns None (triggering
    fallback to the default grading factor in the cost model).

    A production implementation would:
    1. Determine which USGS 3DEP tile(s) cover the parcel centroid
    2. Fetch/cache the elevation raster
    3. Clip the raster to parcel geometry
    4. Compute slope from the elevation grid (rise/run)
    5. Return mean slope percentage
    """

    def __init__(self, dem_cache_dir: Optional[Path] = None) -> None:
        self._cache_dir = dem_cache_dir

    def compute_slope(self, parcel: Parcel) -> float | None:
        """Compute mean slope for a parcel.

        Returns slope_percent or None if DEM data is unavailable.
        If the parcel already has slope_percent set, returns it directly.
        """
        if parcel.slope_percent is not None:
            return float(parcel.slope_percent)

        # Check topography dict
        if parcel.topography and "slope_percent" in parcel.topography:
            return float(parcel.topography["slope_percent"])

        # Placeholder: DEM-based computation would go here
        # For now, return None to trigger default grading factor
        return None
