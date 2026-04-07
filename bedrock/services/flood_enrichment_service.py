"""Service for enriching parcels with FEMA NFHL flood zone data via spatial intersection."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from bedrock.contracts.parcel import Parcel
from bedrock.models.flood_models import FloodAssessment, assess_flood_risk

_DEFAULT_NFHL_DIR = Path(__file__).resolve().parent.parent / "data" / "flood"


class FloodEnrichmentService:
    """Intersects parcel geometry with cached FEMA NFHL data to produce a FloodAssessment.

    When no NFHL data is available for the parcel's location, returns a no-risk assessment
    with a warning that data was unavailable.
    """

    def __init__(self, nfhl_data_dir: Optional[Path] = None) -> None:
        self._data_dir = nfhl_data_dir or _DEFAULT_NFHL_DIR

    def assess(self, parcel: Parcel) -> FloodAssessment:
        """Assess flood risk for a parcel.

        If the parcel already has a flood_zone set, use it directly.
        Otherwise, attempt spatial intersection with NFHL data.
        """
        # If parcel already has flood zone data, use it
        if parcel.flood_zone is not None:
            # Use a conservative flood_area_ratio estimate when not computed spatially
            flood_area_ratio = 0.5 if parcel.flood_zone.strip().upper() != "X" else 0.0
            return assess_flood_risk(
                flood_zone=parcel.flood_zone,
                flood_area_ratio=flood_area_ratio,
                parcel_area_sqft=float(parcel.area_sqft),
            )

        # Attempt spatial lookup from cached NFHL data
        nfhl_result = self._spatial_lookup(parcel)
        if nfhl_result is not None:
            return assess_flood_risk(
                flood_zone=nfhl_result["flood_zone"],
                flood_area_ratio=nfhl_result["flood_area_ratio"],
                parcel_area_sqft=float(parcel.area_sqft),
            )

        # No data available — return no-risk assessment
        return assess_flood_risk(
            flood_zone=None,
            flood_area_ratio=0.0,
            parcel_area_sqft=float(parcel.area_sqft),
        )

    def _spatial_lookup(self, parcel: Parcel) -> dict | None:
        """Look up flood zone from cached NFHL GeoJSON files.

        This is a placeholder for full spatial intersection.
        A production implementation would:
        1. Load NFHL GeoJSON polygons for the parcel's county
        2. Intersect parcel geometry with flood zone polygons
        3. Compute flood_area_ratio from intersection area / parcel area
        """
        index = self._load_index()
        if not index:
            return None

        # Simple centroid-based lookup from pre-indexed data
        if parcel.centroid is None:
            return None

        cx, cy = parcel.centroid
        for entry in index:
            bbox = entry.get("bbox")
            if bbox and bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]:
                return {
                    "flood_zone": entry.get("flood_zone", "X"),
                    "flood_area_ratio": entry.get("flood_area_ratio", 0.0),
                }

        return None

    @lru_cache(maxsize=1)
    def _load_index(self) -> list:
        """Load pre-computed flood zone index if available."""
        index_path = self._data_dir / "flood_index.json"
        if not index_path.exists():
            return []
        with open(index_path) as f:
            return json.load(f)
