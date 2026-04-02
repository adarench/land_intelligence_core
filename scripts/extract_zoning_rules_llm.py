"""Extract district-level zoning development standards using Claude API.

Processes zoning code HTML/PDF documents and extracts structured rules
for each residential zoning district.

Usage:
    python scripts/extract_zoning_rules_llm.py [--city SLUG] [--all]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRAPER_ROOT = REPO_ROOT / "zoning_data_scraper"
OUTPUT_DIR = SCRAPER_ROOT / "data" / "normalized_rules"

EXTRACTION_SCHEMA = {
    "district": "string — the zoning district code exactly as written",
    "district_name": "string — the full name of the district",
    "min_lot_size_sqft": "number — minimum lot area in square feet (convert acres × 43560)",
    "max_units_per_acre": "number — maximum dwelling units per acre",
    "front_setback_ft": "number — minimum front yard setback in feet",
    "side_setback_ft": "number — minimum side yard setback in feet",
    "rear_setback_ft": "number — minimum rear yard setback in feet",
    "max_building_height_ft": "number — maximum building height in feet",
    "max_lot_coverage": "number — maximum lot coverage as decimal (e.g., 0.40 for 40%)",
    "min_frontage_ft": "number — minimum lot frontage/width in feet",
    "allowed_uses": "list of strings — permitted use categories",
}

SYSTEM_PROMPT = """You are a zoning code analyst. Extract development standards from municipal zoning code text.

For each residential or development zoning district found in the text, extract:
- district: the exact district code (e.g., R-1-10, RM, A-1)
- district_name: the full name
- min_lot_size_sqft: minimum lot area in square feet. Convert acres to sqft (1 acre = 43,560 sqft).
- max_units_per_acre: maximum dwelling units per acre
- front_setback_ft, side_setback_ft, rear_setback_ft: minimum yard setbacks in feet
- max_building_height_ft: maximum building height in feet
- max_lot_coverage: as a decimal (40% = 0.40)
- min_frontage_ft: minimum lot width/frontage in feet
- allowed_uses: list of use categories (e.g., ["single_family_residential", "accessory_dwelling"])

Return a JSON array of district objects. Use null for values not found in the text.
Only extract districts that have at least one numeric standard specified.
Do NOT invent values — only extract what is explicitly stated in the text."""

SANITY_BOUNDS = {
    "min_lot_size_sqft": (500, 2_000_000),
    "max_units_per_acre": (0.1, 80),
    "front_setback_ft": (1, 200),
    "side_setback_ft": (1, 100),
    "rear_setback_ft": (1, 200),
    "max_building_height_ft": (8, 300),
    "max_lot_coverage": (0.05, 1.0),
    "min_frontage_ft": (10, 400),
}


def extract_text_from_html(path: Path) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
        return soup.get_text("\n", strip=True)
    except Exception:
        return path.read_text(encoding="utf-8", errors="replace")


def extract_text_from_pdf(path: Path) -> str:
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
        return "\n\n".join(pages)
    except Exception:
        return ""


def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return extract_text_from_pdf(path)
    return extract_text_from_html(path)


def chunk_text(text: str, max_chars: int = 12000) -> list[str]:
    """Split text into chunks, preferring breaks at section boundaries."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    lines = text.split("\n")
    current = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > max_chars and current:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks


def call_claude(text_chunk: str, city: str) -> list[dict]:
    """Call Claude API to extract zoning rules from a text chunk."""
    try:
        import anthropic
    except ImportError:
        print("  ERROR: anthropic package not installed. Run: pip install anthropic")
        return []

    client = anthropic.Anthropic()

    user_prompt = f"""Extract zoning development standards for {city}, Utah from the following municipal code text.

Return a JSON array of district objects with these fields:
{json.dumps(EXTRACTION_SCHEMA, indent=2)}

TEXT:
{text_chunk[:15000]}

Return ONLY the JSON array, no other text."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        content = response.content[0].text.strip()
        # Extract JSON from response
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\n?", "", content)
            content = re.sub(r"\n?```$", "", content)

        districts = json.loads(content)
        if not isinstance(districts, list):
            districts = [districts]
        return districts
    except json.JSONDecodeError:
        return []
    except Exception as e:
        print(f"  API error: {e}")
        return []


def validate_district(d: dict) -> dict | None:
    """Validate and normalize an extracted district."""
    code = (d.get("district") or "").strip()
    if not code or len(code) > 24:
        return None

    result = {
        "district": code,
        "district_name": (d.get("district_name") or "").strip() or None,
        "min_lot_size_sqft": None,
        "max_units_per_acre": None,
        "setbacks": {"front": None, "side": None, "rear": None},
        "max_building_height_ft": None,
        "max_lot_coverage": None,
        "min_frontage_ft": None,
        "allowed_use_types": None,
    }

    # Extract and validate numeric fields
    field_map = {
        "min_lot_size_sqft": "min_lot_size_sqft",
        "max_units_per_acre": "max_units_per_acre",
        "max_building_height_ft": "max_building_height_ft",
        "max_lot_coverage": "max_lot_coverage",
        "min_frontage_ft": "min_frontage_ft",
    }

    for src, dest in field_map.items():
        val = d.get(src)
        if val is not None:
            try:
                val = float(val)
                lo, hi = SANITY_BOUNDS.get(dest, (0, 1e9))
                if lo <= val <= hi:
                    result[dest] = val
            except (ValueError, TypeError):
                pass

    # Setbacks
    for edge, src in [("front", "front_setback_ft"), ("side", "side_setback_ft"), ("rear", "rear_setback_ft")]:
        val = d.get(src)
        if val is not None:
            try:
                val = float(val)
                lo, hi = SANITY_BOUNDS.get(src, (0, 200))
                if lo <= val <= hi:
                    result["setbacks"][edge] = val
            except (ValueError, TypeError):
                pass

    # Lot coverage normalization
    if result["max_lot_coverage"] is not None and result["max_lot_coverage"] > 1:
        result["max_lot_coverage"] = result["max_lot_coverage"] / 100.0

    # Allowed uses
    uses = d.get("allowed_uses")
    if isinstance(uses, list) and uses:
        result["allowed_use_types"] = [str(u).strip().lower().replace(" ", "_") for u in uses if u]

    # Must have at least one numeric standard
    has_value = any([
        result["min_lot_size_sqft"],
        result["max_units_per_acre"],
        result["setbacks"]["front"],
        result["max_building_height_ft"],
    ])

    return result if has_value else None


def find_documents(city_slug: str) -> list[Path]:
    """Find zoning code documents for a city."""
    docs = []
    for base in ["zoning_data_priority_v4", "zoning_data_priority_v9b"]:
        doc_dir = SCRAPER_ROOT / base / city_slug / "documents"
        if doc_dir.exists():
            docs.extend(doc_dir.glob("*.html"))
            docs.extend(doc_dir.glob("*.pdf"))
    # Deduplicate by filename
    seen = set()
    unique = []
    for d in docs:
        if d.name not in seen:
            seen.add(d.name)
            unique.append(d)
    return sorted(unique)


def extract_city(city_slug: str, city_name: str, dry_run: bool = False) -> dict:
    """Extract all zoning rules for a city."""
    docs = find_documents(city_slug)
    if not docs:
        return {"jurisdiction": city_name, "districts": {}, "status": "no_documents"}

    print(f"\n{'='*60}")
    print(f"  {city_name} ({city_slug}): {len(docs)} documents")
    print(f"{'='*60}")

    # Extract text from all documents
    all_text = []
    for doc in docs:
        text = extract_text(doc)
        if len(text) > 100:
            all_text.append((doc.name, text))

    if not all_text:
        return {"jurisdiction": city_name, "districts": {}, "status": "no_text"}

    # Concatenate and chunk — focus on zoning/development standards sections
    combined = "\n\n".join(f"--- {name} ---\n{text}" for name, text in all_text)

    # Filter to zoning-relevant text
    zoning_keywords = ["zone", "district", "setback", "lot size", "dwelling", "density", "height", "coverage", "frontage", "residential"]
    relevant_lines = []
    for line in combined.split("\n"):
        if any(k in line.lower() for k in zoning_keywords):
            relevant_lines.append(line)
        elif relevant_lines and len(relevant_lines[-1]) < 200:
            relevant_lines.append(line)  # Include context lines

    filtered = "\n".join(relevant_lines)
    if len(filtered) < 200:
        filtered = combined  # Fall back to full text

    chunks = chunk_text(filtered, max_chars=12000)
    print(f"  Text: {len(combined):,} chars → {len(filtered):,} filtered → {len(chunks)} chunks")

    if dry_run:
        return {"jurisdiction": city_name, "districts": {}, "status": "dry_run", "chunks": len(chunks)}

    # Extract from each chunk
    all_districts: dict[str, dict] = {}
    for i, chunk in enumerate(chunks):
        print(f"  Chunk {i+1}/{len(chunks)} ({len(chunk):,} chars)...", end=" ", flush=True)
        raw = call_claude(chunk, city_name)
        valid = 0
        for d in raw:
            validated = validate_district(d)
            if validated:
                code = validated["district"]
                if code not in all_districts:
                    all_districts[code] = validated
                    valid += 1
                else:
                    # Merge: fill nulls from new extraction
                    existing = all_districts[code]
                    for key in ["min_lot_size_sqft", "max_units_per_acre", "max_building_height_ft", "max_lot_coverage", "min_frontage_ft"]:
                        if existing[key] is None and validated[key] is not None:
                            existing[key] = validated[key]
                    for edge in ["front", "side", "rear"]:
                        if existing["setbacks"][edge] is None and validated["setbacks"][edge] is not None:
                            existing["setbacks"][edge] = validated["setbacks"][edge]
        print(f"{len(raw)} extracted, {valid} new valid")
        time.sleep(0.5)  # Rate limiting

    print(f"  RESULT: {len(all_districts)} districts extracted")
    for code, d in sorted(all_districts.items()):
        lot = f"lot={d['min_lot_size_sqft']}" if d['min_lot_size_sqft'] else "lot=?"
        density = f"du/ac={d['max_units_per_acre']}" if d['max_units_per_acre'] else "du/ac=?"
        setbacks = f"F{d['setbacks']['front'] or '?'}/S{d['setbacks']['side'] or '?'}/R{d['setbacks']['rear'] or '?'}"
        print(f"    {code:<15} {lot:<15} {density:<12} {setbacks}")

    return {
        "jurisdiction": city_name,
        "jurisdiction_slug": city_slug,
        "districts": all_districts,
        "status": "extracted",
        "extraction_method": "llm_claude",
        "source_documents": [str(d) for d in docs],
    }


def write_rules(city_slug: str, data: dict) -> Path:
    """Write extracted rules to normalized_rules directory."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{city_slug}.json"

    # If file exists, merge — don't overwrite existing curated data
    if path.exists():
        existing = json.loads(path.read_text())
        existing_districts = existing.get("districts", {})
        new_districts = data.get("districts", {})

        for code, new_d in new_districts.items():
            if code not in existing_districts:
                existing_districts[code] = new_d
            # Don't overwrite existing curated rules

        existing["districts"] = existing_districts
        data = existing

    path.write_text(json.dumps(data, indent=2, default=str))
    return path


# City registry — map slugs to display names
CITY_REGISTRY = {
    "salt-lake-city": "Salt Lake City",
    "draper": "Draper",
    "lehi": "Lehi",
    "provo": "Provo",
    "herriman": "Herriman",
    "eagle-mountain": "Eagle Mountain",
    "saratoga-springs": "Saratoga Springs",
    "south-jordan": "South Jordan",
    "riverton": "Riverton",
    "st-george": "St. George",
    "heber-city": "Heber City",
    "west-valley-city": "West Valley City",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", help="Process a single city slug")
    parser.add_argument("--all", action="store_true", help="Process all cities with documents")
    parser.add_argument("--dry-run", action="store_true", help="Count chunks without calling API")
    args = parser.parse_args()

    if args.city:
        cities = [(args.city, CITY_REGISTRY.get(args.city, args.city.replace("-", " ").title()))]
    elif args.all:
        cities = list(CITY_REGISTRY.items())
    else:
        print("Usage: --city SLUG or --all")
        print(f"Available: {', '.join(CITY_REGISTRY.keys())}")
        sys.exit(1)

    results = []
    for slug, name in cities:
        result = extract_city(slug, name, dry_run=args.dry_run)
        results.append(result)

        if not args.dry_run and result.get("districts"):
            path = write_rules(slug, result)
            print(f"  Written to {path}")

    # Summary
    print(f"\n{'='*60}")
    print("EXTRACTION SUMMARY")
    print(f"{'='*60}")
    total_districts = 0
    for r in results:
        n = len(r.get("districts", {}))
        total_districts += n
        status = r.get("status", "?")
        print(f"  {r['jurisdiction']:<25} {n:>3} districts  [{status}]")
    print(f"\nTotal: {total_districts} districts across {len(results)} cities")


if __name__ == "__main__":
    main()
