"""Traceable market and cost proxies for Utah feasibility evaluation.

Uses a two-tier data strategy:
1. Internal calibration data (flagship_calibration.json) — actual sale prices and
   margin-derived costs from Flagship Homes closed deals.  Highest confidence.
2. Public market reference (utah_market_reference.json) — Census ACS median home
   values and FRED RPP regional cost factors.  Used as fallback.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from bedrock.contracts.market_data import MarketData
from bedrock.contracts.parcel import Parcel
from bedrock.models.financial_models import DEFAULT_HOME_SIZE_SQFT


_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "market"
PUBLIC_REFERENCE_PATH = _DATA_DIR / "utah_market_reference_20260326.json"
CALIBRATION_PATH = _DATA_DIR / "flagship_calibration.json"

# Minimum sample size to trust jurisdiction-level calibration
_MIN_PRICE_SAMPLE = 10
_MIN_COST_SAMPLE = 5


@lru_cache(maxsize=1)
def _load_public_reference() -> dict[str, Any]:
    return json.loads(PUBLIC_REFERENCE_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _load_calibration() -> dict[str, Any]:
    if not CALIBRATION_PATH.exists():
        return {}
    return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))


class MarketIntelligenceService:
    def __init__(self, data_path: Path | None = None) -> None:
        self._data_path = data_path or PUBLIC_REFERENCE_PATH

    @property
    def reference(self) -> dict[str, Any]:
        return _load_public_reference() if self._data_path == PUBLIC_REFERENCE_PATH else json.loads(self._data_path.read_text(encoding="utf-8"))

    @property
    def calibration(self) -> dict[str, Any]:
        return _load_calibration()

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
        cal = self._resolve_calibration(parcel)
        reference_home_size = float(profile["reference_home_size_sqft"])
        regional_factor = float(profile["rpp_all_items"]) / 100.0

        # --- Revenue: prefer internal calibration, then public median ---
        if cal["has_price"]:
            estimated_home_price = cal["median_sale_price"]
            pricing_proxy = "internal_calibration_median"
            pricing_sample = cal["n_price"]
        else:
            estimated_home_price = float(profile["median_home_value"])
            pricing_proxy = "acs_median_home_value_direct"
            pricing_sample = 0

        price_per_sqft = estimated_home_price / max(reference_home_size, 1.0)

        # --- Cost: prefer margin-derived from price (consistent), then public baseline ---
        # Using margin × price is more robust than raw cost medians because price and
        # cost medians can come from different product mixes within a jurisdiction.
        if cal["has_cost"] and cal.get("median_final_margin_pct") is not None:
            margin_pct = cal["median_final_margin_pct"]
            construction_cost_per_home = estimated_home_price * (1 - margin_pct / 100)
            construction_cost_per_sqft = construction_cost_per_home / max(reference_home_size, 1.0)
            cost_proxy = "internal_jurisdiction_margin_derived"
            cost_sample = cal["n_cost"]
        elif cal["has_global_margin"]:
            margin_pct = cal["global_median_margin_pct"]
            construction_cost_per_home = estimated_home_price * (1 - margin_pct / 100)
            construction_cost_per_sqft = construction_cost_per_home / max(reference_home_size, 1.0)
            cost_proxy = "internal_margin_derived"
            cost_sample = cal["global_cost_sample"]
        else:
            construction_cost_per_sqft = float(profile["baseline_cost_per_sqft"]) * regional_factor
            construction_cost_per_home = construction_cost_per_sqft * reference_home_size
            cost_proxy = "bea_rpp_scaled_baseline"
            cost_sample = 0

        road_cost_per_ft = float(profile["baseline_road_cost_per_ft"]) * regional_factor

        parcel_area_acres = float(parcel.area_sqft) / 43560.0
        land_price = parcel_area_acres * (estimated_home_price * float(profile["land_value_share_of_home_value"]))

        soft_cost_factor = max(0.04, min(abs(regional_factor - 1.0) + 0.05, 0.18))

        market_data = MarketData(
            estimated_home_price=estimated_home_price,
            construction_cost_per_home=construction_cost_per_home,
            road_cost_per_ft=road_cost_per_ft,
            land_price=land_price,
            soft_cost_factor=soft_cost_factor,
        )
        return market_data, {
            **profile,
            "estimated_home_price": estimated_home_price,
            "estimated_home_size_sqft": reference_home_size,
            "price_per_sqft": price_per_sqft,
            "construction_cost_per_sqft": construction_cost_per_sqft,
            "construction_cost_per_home": construction_cost_per_home,
            "road_cost_per_ft": road_cost_per_ft,
            "land_price_estimate": land_price,
            "pricing_proxy": pricing_proxy,
            "pricing_sample_size": pricing_sample,
            "cost_proxy": cost_proxy,
            "cost_sample_size": cost_sample,
            "calibration_source": cal["source"],
            "calibration_jurisdiction_match": cal["jurisdiction_match"],
        }

    def _resolve_calibration(self, parcel: Parcel) -> dict[str, Any]:
        """Resolve internal calibration data for a parcel's jurisdiction."""
        cal = self.calibration
        if not cal:
            return self._empty_calibration()

        jurisdiction = (parcel.jurisdiction or "").strip()
        jurisdictions = cal.get("jurisdictions") or {}
        global_data = cal.get("global") or {}

        jur_data = jurisdictions.get(jurisdiction)
        if jur_data and jur_data.get("n_total", 0) >= _MIN_PRICE_SAMPLE:
            has_cost = jur_data.get("n_with_cost", 0) >= _MIN_COST_SAMPLE
            return {
                "source": "internal_calibration",
                "jurisdiction_match": jurisdiction,
                "has_price": True,
                "median_sale_price": float(jur_data["median_sale_price"]),
                "p25_sale_price": float(jur_data.get("p25_sale_price", 0)),
                "p75_sale_price": float(jur_data.get("p75_sale_price", 0)),
                "n_price": jur_data["n_total"],
                "has_cost": has_cost,
                "median_total_cost_per_home": float(jur_data["median_total_cost_per_home"]) if has_cost else None,
                "median_final_margin_pct": float(jur_data.get("median_final_margin_pct", 0)) if has_cost else None,
                "n_cost": jur_data.get("n_with_cost", 0),
                "has_global_margin": bool(global_data.get("median_final_margin_pct")),
                "global_median_margin_pct": float(global_data.get("median_final_margin_pct", 14.4)),
                "global_cost_sample": int(global_data.get("total_cost_records", 0)),
            }

        # No jurisdiction match — check if we have global calibration
        has_global = bool(global_data.get("total_calibration_records", 0))
        return {
            "source": "internal_calibration_global_fallback" if has_global else "none",
            "jurisdiction_match": None,
            "has_price": False,
            "median_sale_price": None,
            "n_price": 0,
            "has_cost": False,
            "median_total_cost_per_home": None,
            "n_cost": 0,
            "has_global_margin": has_global,
            "global_median_margin_pct": float(global_data.get("median_final_margin_pct", 14.4)) if has_global else None,
            "global_cost_sample": int(global_data.get("total_cost_records", 0)) if has_global else 0,
        }

    @staticmethod
    def _empty_calibration() -> dict[str, Any]:
        return {
            "source": "none",
            "jurisdiction_match": None,
            "has_price": False,
            "median_sale_price": None,
            "n_price": 0,
            "has_cost": False,
            "median_total_cost_per_home": None,
            "n_cost": 0,
            "has_global_margin": False,
            "global_median_margin_pct": None,
            "global_cost_sample": 0,
        }
