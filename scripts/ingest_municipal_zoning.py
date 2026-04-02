"""Ingest municipal zoning layers from known ArcGIS feature services.

Downloads zoning polygons for each city, normalizes to the standard
normalized_zoning.json schema, and writes to zoning_dataset_v10/.

Usage:
    python scripts/ingest_municipal_zoning.py
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "zoning_data_scraper" / "zoning_dataset_v10"
MAX_FEATURES_PER_REQUEST = 1000

# Registry of known ArcGIS feature service endpoints for Utah cities.
# Each entry: (jurisdiction_name, city_slug, service_url, layer_index, zone_field, desc_field)
MUNICIPAL_ZONING_SOURCES: list[tuple[str, str, str, int, str, str]] = [
    (
        "Herriman",
        "herriman",
        "https://services2.arcgis.com/XBmqwOHlPh25M7aJ/arcgis/rest/services/HerrimanCityZoning/FeatureServer",
        0,
        "ZONE_",
        "ZONEDESCRIPTION",
    ),
    (
        "Lehi",
        "lehi",
        "https://services5.arcgis.com/rObWD7PYeLl9jJPT/arcgis/rest/services/Lehi_Zoning/FeatureServer",
        0,
        "ZONE_CODE",  # will be auto-detected
        "ZONE_NAME",
    ),
    (
        "Saratoga Springs",
        "saratoga-springs",
        "https://services.arcgis.com/M7jfYoTaLM0yE75d/arcgis/rest/services/Zoning_Districts_for_City_of_Saratoga_Springs/FeatureServer",
        0,
        "ZONE_CODE",
        "ZONE_NAME",
    ),
]

# Existing datasets that are already working — preserve by copying their paths
EXISTING_OVERLAY_CITIES = {
    "salt-lake-city": "zoning_dataset_v8/salt-lake-city",
    "draper": "zoning_data_priority_v9b/draper",
    "south-jordan": "zoning_data_priority_v9b/south-jordan",
}


def fetch_json(url: str) -> dict[str, Any]:
    """Fetch JSON from a URL with retry."""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "land-intelligence-zoning-ingest/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            if attempt == 2:
                raise
            print(f"  Retry {attempt + 1} for {url[:80]}... ({exc})")
            time.sleep(2)
    return {}


def detect_fields(service_url: str, layer: int) -> tuple[str, str, list[str]]:
    """Auto-detect the zone code and description fields."""
    info = fetch_json(f"{service_url}/{layer}?f=json")
    fields = info.get("fields", [])
    field_names = [f["name"] for f in fields]

    zone_field = None
    desc_field = None

    zone_candidates = ["ZONE_", "ZONE", "ZONECLASS", "ZONE_CODE", "ZoneCode", "ZONEID", "Zone_ID", "ZONING", "ZoningCode"]
    desc_candidates = ["ZONEDESCRIPTION", "ZONEDESC", "ZONE_DESC", "ZoneDesc", "ZONE_NAME", "ZoneName", "Zone_Name", "Description"]

    for candidate in zone_candidates:
        if candidate in field_names:
            zone_field = candidate
            break
    for candidate in desc_candidates:
        if candidate in field_names:
            desc_field = candidate
            break

    if not zone_field:
        # Fall back to first text field that isn't OBJECTID or shape
        for f in fields:
            if f.get("type") == "esriFieldTypeString" and f["name"] not in ("OBJECTID", "GlobalID", "GLOBALID"):
                zone_field = f["name"]
                break

    return zone_field or "ZONE_", desc_field or "", field_names


def fetch_all_features(service_url: str, layer: int, zone_field: str, desc_field: str) -> list[dict]:
    """Download all features from an ArcGIS feature service, handling pagination."""
    features = []
    offset = 0

    out_fields = f"{zone_field},{desc_field}" if desc_field else zone_field

    while True:
        url = (
            f"{service_url}/{layer}/query?"
            f"where=1%3D1&outFields={out_fields}&outSR=4326"
            f"&resultOffset={offset}&resultRecordCount={MAX_FEATURES_PER_REQUEST}"
            f"&f=geojson"
        )
        data = fetch_json(url)
        batch = data.get("features", [])
        if not batch:
            break
        features.extend(batch)
        offset += len(batch)
        if len(batch) < MAX_FEATURES_PER_REQUEST:
            break

    return features


def normalize_features(
    features: list[dict],
    city: str,
    zone_field: str,
    desc_field: str,
    source_url: str,
) -> list[dict]:
    """Convert ArcGIS GeoJSON features to the normalized_zoning.json schema."""
    normalized = []
    valid_code_pattern = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9()/_ .-]{1,48}$")

    for feature in features:
        props = feature.get("properties", {})
        geometry = feature.get("geometry")

        if not geometry or not geometry.get("coordinates"):
            continue

        zone_code = str(props.get(zone_field) or "").strip()
        zone_name = str(props.get(desc_field) or "").strip() if desc_field else ""

        if not zone_code or not valid_code_pattern.match(zone_code):
            continue

        normalized.append({
            "city": city,
            "zoning_code": zone_code,
            "zoning_name": zone_name,
            "density": None,
            "overlay": None,
            "source_layer": source_url,
            "geometry": geometry,
        })

    return normalized


def write_dataset(city_slug: str, city: str, features: list[dict], source_url: str) -> Path:
    """Write normalized features and metadata to zoning_dataset_v10/{city_slug}/."""
    city_dir = OUTPUT_DIR / city_slug
    city_dir.mkdir(parents=True, exist_ok=True)

    # Write normalized_zoning.json
    zoning_path = city_dir / "normalized_zoning.json"
    zoning_path.write_text(json.dumps(features, indent=2), encoding="utf-8")

    # Write metadata.json
    zone_codes = sorted(set(f["zoning_code"] for f in features))
    metadata = {
        "city": city,
        "city_slug": city_slug,
        "source": source_url,
        "source_type": "arcgis_feature_service",
        "feature_count": len(features),
        "coded_feature_count": len(features),
        "distinct_zones": len(zone_codes),
        "zone_codes": zone_codes,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "coordinate_system": "EPSG:4326",
        "legal_reliability": True,
    }
    metadata_path = city_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return city_dir


def create_symlinks_for_existing() -> None:
    """Create symlink-style references for cities that already have good data in older datasets."""
    for city_slug, existing_path in EXISTING_OVERLAY_CITIES.items():
        city_dir = OUTPUT_DIR / city_slug
        if city_dir.exists():
            continue
        source = REPO_ROOT / "zoning_data_scraper" / existing_path
        if not (source / "normalized_zoning.json").exists():
            continue
        # Copy the data rather than symlink for portability
        city_dir.mkdir(parents=True, exist_ok=True)
        for filename in ("normalized_zoning.json", "metadata.json"):
            src = source / filename
            if src.exists():
                (city_dir / filename).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  Copied existing {city_slug} from {existing_path}")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {OUTPUT_DIR}")
    print()

    # Copy existing working datasets
    print("=== Preserving existing overlay datasets ===")
    create_symlinks_for_existing()
    print()

    # Ingest from municipal ArcGIS services
    print("=== Ingesting from municipal ArcGIS services ===")
    results = []

    for city, slug, service_url, layer, zone_hint, desc_hint in MUNICIPAL_ZONING_SOURCES:
        print(f"\n--- {city} ---")
        try:
            zone_field, desc_field, all_fields = detect_fields(service_url, layer)
            if zone_hint and zone_hint in all_fields:
                zone_field = zone_hint
            if desc_hint and desc_hint in all_fields:
                desc_field = desc_hint
            print(f"  Fields detected: zone={zone_field}, desc={desc_field}")

            features = fetch_all_features(service_url, layer, zone_field, desc_field)
            print(f"  Downloaded {len(features)} features")

            normalized = normalize_features(features, city, zone_field, desc_field, service_url)
            print(f"  Normalized {len(normalized)} valid features")

            if normalized:
                path = write_dataset(slug, city, normalized, service_url)
                zone_codes = sorted(set(f["zoning_code"] for f in normalized))
                print(f"  Written to {path}")
                print(f"  Zones: {zone_codes[:10]}{'...' if len(zone_codes) > 10 else ''}")
                results.append({"city": city, "slug": slug, "features": len(normalized), "zones": len(zone_codes), "status": "ok"})
            else:
                print(f"  WARNING: No valid features after normalization")
                results.append({"city": city, "slug": slug, "features": 0, "zones": 0, "status": "empty"})

        except Exception as exc:
            print(f"  ERROR: {exc}")
            results.append({"city": city, "slug": slug, "features": 0, "zones": 0, "status": f"error: {exc}"})

    # Summary
    print("\n" + "=" * 60)
    print("INGEST SUMMARY")
    print("=" * 60)
    total_cities = len([d for d in OUTPUT_DIR.iterdir() if (d / "normalized_zoning.json").exists()])
    total_features = sum(
        len(json.loads((d / "normalized_zoning.json").read_text()))
        for d in OUTPUT_DIR.iterdir()
        if (d / "normalized_zoning.json").exists()
    )
    print(f"Cities with data: {total_cities}")
    print(f"Total features: {total_features}")
    for r in results:
        status = "OK" if r["status"] == "ok" else r["status"]
        print(f"  {r['city']:<25} {r['features']:>5} features, {r['zones']:>3} zones  [{status}]")

    # List all cities in output
    print(f"\nAll cities in {OUTPUT_DIR.name}:")
    for d in sorted(OUTPUT_DIR.iterdir()):
        if (d / "normalized_zoning.json").exists():
            meta = json.loads((d / "metadata.json").read_text()) if (d / "metadata.json").exists() else {}
            print(f"  {d.name:<25} {meta.get('feature_count', '?'):>5} features  source={meta.get('source_type', '?')}")


if __name__ == "__main__":
    main()
