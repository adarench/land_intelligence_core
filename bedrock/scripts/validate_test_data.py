from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DATA_DIR = REPO_ROOT / "test_data"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def require_fields(name: str, data: dict, fields: list[str]) -> None:
    missing = [field for field in fields if field not in data]
    if missing:
        raise ValueError(f"{name} is missing required fields: {', '.join(missing)}")


def validate_parcel(parcel: dict) -> None:
    require_fields(
        "parcel",
        parcel,
        [
            "parcel_id",
            "jurisdiction",
            "area_sqft",
            "geometry",
            "centroid",
            "bounding_box",
            "land_use",
            "slope_percent",
            "flood_zone",
        ],
    )
    geometry = parcel["geometry"]
    if geometry.get("type") != "Polygon":
        raise ValueError("parcel geometry must be a GeoJSON Polygon")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates or not coordinates[0]:
        raise ValueError("parcel geometry must contain polygon coordinates")


def validate_zoning(zoning: dict, parcel_id: str) -> None:
    require_fields(
        "zoning",
        zoning,
        [
            "parcel_id",
            "district",
            "min_lot_size_sqft",
            "max_units_per_acre",
            "setbacks",
            "height_limit_ft",
            "lot_coverage_max",
            "min_frontage_ft",
            "road_right_of_way_ft",
        ],
    )
    if zoning["parcel_id"] != parcel_id:
        raise ValueError("zoning parcel_id does not match parcel data")
    setbacks = zoning["setbacks"]
    require_fields("zoning.setbacks", setbacks, ["front", "side", "rear"])


def validate_feasibility_assumptions(assumptions: dict) -> None:
    require_fields(
        "feasibility assumptions",
        assumptions,
        [
            "construction_cost_per_home",
            "average_home_sale_price",
            "road_cost_per_linear_ft",
            "utility_cost_per_lot",
            "soft_cost_percent",
            "developer_margin_target",
        ],
    )


def validate_dataset(dataset_id: str) -> None:
    parcel = load_json(TEST_DATA_DIR / f"parcel_test_{dataset_id}.json")
    zoning = load_json(TEST_DATA_DIR / f"zoning_test_{dataset_id}.json")
    assumptions = load_json(TEST_DATA_DIR / f"feasibility_assumptions_{dataset_id}.json")

    validate_parcel(parcel)
    validate_zoning(zoning, parcel["parcel_id"])
    validate_feasibility_assumptions(assumptions)

    print(f"Parcel ID: {parcel['parcel_id']}")
    print(f"Area: {parcel['area_sqft']} sqft")
    print(f"Zoning: {zoning['district']}")
    print(f"Min Lot Size: {zoning['min_lot_size_sqft']} sqft")
    print()


def main() -> None:
    for dataset_id in ["001", "002"]:
        validate_dataset(dataset_id)


if __name__ == "__main__":
    main()
