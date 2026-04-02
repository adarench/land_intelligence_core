"""Write LLM-extracted zoning rules to normalized_rules directory.

Takes JSON extraction output and writes it in the format expected by
zoning_code_rules.py's lookup_normalized_rule().

Usage:
    python scripts/write_extracted_rules.py <jurisdiction_slug> '<json_string>'

    Or pipe JSON:
    echo '{"jurisdiction": "Herriman", "districts": {...}}' | python scripts/write_extracted_rules.py herriman
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "zoning_data_scraper" / "data" / "normalized_rules"


def normalize_district_entry(d: dict) -> dict:
    """Ensure a district entry has all expected fields."""
    return {
        "district": d.get("district", ""),
        "aliases": d.get("aliases", []),
        "min_lot_size_sqft": d.get("min_lot_size_sqft"),
        "max_units_per_acre": d.get("max_units_per_acre"),
        "setbacks": d.get("setbacks", {"front": None, "side": None, "rear": None}),
        "max_building_height_ft": d.get("max_building_height_ft"),
        "max_lot_coverage": d.get("max_lot_coverage"),
        "min_lot_width_ft": d.get("min_lot_width_ft") or d.get("min_frontage_ft"),
        "min_lot_depth_ft": d.get("min_lot_depth_ft"),
        "allowed_use_types": d.get("allowed_use_types") or d.get("allowed_uses"),
        "source_documents": d.get("source_documents", ["llm_extraction"]),
        "district_name": d.get("district_name"),
    }


def write_rules(slug: str, data: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{slug}.json"

    districts = data.get("districts", {})
    normalized = {}
    for code, d in districts.items():
        entry = normalize_district_entry(d)
        if entry["district"]:
            normalized[entry["district"]] = entry

    # Merge with existing if present
    if path.exists():
        existing = json.loads(path.read_text())
        existing_districts = existing.get("districts", {})
        for code, entry in normalized.items():
            if code not in existing_districts:
                existing_districts[code] = entry
            else:
                # Fill nulls from new extraction
                ex = existing_districts[code]
                for key in ["min_lot_size_sqft", "max_units_per_acre", "max_building_height_ft", "max_lot_coverage"]:
                    if ex.get(key) is None and entry.get(key) is not None:
                        ex[key] = entry[key]
                for edge in ["front", "side", "rear"]:
                    if ex.get("setbacks", {}).get(edge) is None and entry.get("setbacks", {}).get(edge) is not None:
                        ex.setdefault("setbacks", {})[edge] = entry["setbacks"][edge]
        output = existing
        output["districts"] = existing_districts
    else:
        output = {
            "jurisdiction": data.get("jurisdiction", slug.replace("-", " ").title()),
            "jurisdiction_slug": slug,
            "extraction_method": "llm_claude",
            "districts": normalized,
        }

    path.write_text(json.dumps(output, indent=2, default=str))
    print(f"Written {len(normalized)} districts to {path}")
    print(f"  Districts: {sorted(normalized.keys())}")
    return path


if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 else None
    if not slug:
        print("Usage: python write_extracted_rules.py <slug> [json_string]")
        sys.exit(1)

    if len(sys.argv) > 2:
        data = json.loads(sys.argv[2])
    else:
        data = json.loads(sys.stdin.read())

    write_rules(slug, data)
