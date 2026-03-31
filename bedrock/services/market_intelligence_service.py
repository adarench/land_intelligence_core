"""Traceable market and cost proxies for Utah feasibility evaluation."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from bedrock.contracts.market_data import MarketData
from bedrock.contracts.parcel import Parcel
from bedrock.models.financial_models import DEFAULT_HOME_SIZE_SQFT, calculate_estimated_home_size_sqft


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "market" / "utah_market_reference_20260326.json"


@lru_cache(maxsize=1)
def _load_reference() -> dict[str, Any]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


class MarketIntelligenceService:
    def __init__(self, data_path: Path | None = None) -> None:
        self._data_path = data_path or DATA_PATH

    @property
    def reference(self) -> dict[str, Any]:
        return _load_reference() if self._data_path == DATA_PATH else json.loads(self._data_path.read_text(encoding="utf-8"))

    def resolve_profile(self, parcel: Parcel) -> dict[str, Any]:
        reference = self.reference
        jurisdiction = (parcel.jurisdiction or "").strip()
        profile = dict((reference.get("jurisdictions") or {}).get(jurisdiction) or {})
        county_name = profile.get("county_name")
        county_profile = dict((reference.get("counties") or {}).get(county_name) or {})
        assumptions = dict(reference.get("assumptions") or {})

        median_home_value = float(
            profile.get("median_home_value")
            or county_profile.get("median_home_value")
            or 480000.0
        )
        rpp = float(profile.get("rpp_all_items_2024") or 100.0)
        reference_home_size_sqft = float(assumptions.get("reference_home_size_sqft") or DEFAULT_HOME_SIZE_SQFT)
        baseline_cost_per_sqft = float(assumptions.get("national_baseline_construction_cost_per_sqft") or 130.0)
        baseline_road_cost_per_ft = float(assumptions.get("national_baseline_road_cost_per_ft") or 300.0)
        land_value_share = float(assumptions.get("land_value_share_of_home_value") or 0.18)

        return {
            "jurisdiction": jurisdiction,
            "county_name": county_name,
            "median_home_value": median_home_value,
            "rpp_all_items": rpp,
            "reference_home_size_sqft": reference_home_size_sqft,
            "baseline_cost_per_sqft": baseline_cost_per_sqft,
            "baseline_road_cost_per_ft": baseline_road_cost_per_ft,
            "land_value_share_of_home_value": land_value_share,
            "price_reference_date": profile.get("price_reference_date"),
            "msa_name": profile.get("msa_name"),
            "sources": dict(reference.get("sources") or {}),
            "used_county_fallback": not bool(profile.get("median_home_value")),
        }

    def resolve_market_data(self, *, parcel: Parcel, units: int) -> tuple[MarketData, dict[str, Any]]:
        profile = self.resolve_profile(parcel)
        estimated_home_size_sqft = calculate_estimated_home_size_sqft(parcel_area_sqft=float(parcel.area_sqft), units=max(units, 1))
        price_per_sqft = float(profile["median_home_value"]) / max(float(profile["reference_home_size_sqft"]), 1.0)
        estimated_home_price = price_per_sqft * estimated_home_size_sqft
        regional_factor = float(profile["rpp_all_items"]) / 100.0
        construction_cost_per_sqft = float(profile["baseline_cost_per_sqft"]) * regional_factor
        construction_cost_per_home = construction_cost_per_sqft * estimated_home_size_sqft
        road_cost_per_ft = float(profile["baseline_road_cost_per_ft"]) * regional_factor
        parcel_area_acres = float(parcel.area_sqft) / 43560.0
        land_price = parcel_area_acres * (estimated_home_price * float(profile["land_value_share_of_home_value"]))
        market_data = MarketData(
            estimated_home_price=estimated_home_price,
            construction_cost_per_home=construction_cost_per_home,
            road_cost_per_ft=road_cost_per_ft,
            land_price=land_price,
            soft_cost_factor=max(0.04, min(abs(regional_factor - 1.0) + 0.05, 0.18)),
        )
        return market_data, {
            **profile,
            "estimated_home_size_sqft": estimated_home_size_sqft,
            "price_per_sqft": price_per_sqft,
            "construction_cost_per_sqft": construction_cost_per_sqft,
            "road_cost_per_ft": road_cost_per_ft,
            "land_price_estimate": land_price,
            "pricing_proxy": "acs_median_home_value_per_reference_home_size",
            "cost_proxy": "bea_rpp_scaled_baseline_costs",
        }
