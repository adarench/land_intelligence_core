from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bedrock") not in sys.path:
    sys.path.insert(0, str(ROOT / "bedrock"))

from zoning_data_scraper.services import rule_normalization as rule_normalization_module
from zoning_data_scraper.services import zoning_code_rules as zoning_code_rules_module
from zoning_data_scraper.services.rule_normalization import normalize_zoning_rules
from zoning_data_scraper.services.zoning_code_rules import (
    extract_rules_from_documents,
    extract_text_from_document,
    lookup_normalized_rule,
    write_normalized_rules,
)
from zoning_data_scraper.services.zoning_overlay import OverlayMatch


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_extract_text_from_html_document(tmp_path: Path) -> None:
    html_path = tmp_path / "districts.html"
    _write(
        html_path,
        """
        <html><body>
        <h1>R-1 Residential District</h1>
        <p>Minimum Lot Size 6000 sq ft</p>
        <p>Front Yard Setback 25 ft</p>
        </body></html>
        """,
    )

    text = extract_text_from_document(html_path)

    assert "R-1 Residential District" in text
    assert "Minimum Lot Size 6000 sq ft" in text


def test_extract_text_from_pdf_document(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "districts.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "R-1 Residential District\nMinimum Lot Size 6000 sq ft\nFront Yard Setback 25 ft")
    doc.save(pdf_path)
    doc.close()

    text = extract_text_from_document(pdf_path)

    assert "R-1 Residential District" in text
    assert "Front Yard Setback 25 ft" in text


def test_extract_rules_from_documents_parses_standards_text(tmp_path: Path) -> None:
    priority_root = tmp_path / "priority"
    package_root = tmp_path / "package"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(zoning_code_rules_module, "PACKAGE_ROOT", package_root)
    _write_json(
        priority_root / "draper" / "datasets.json",
        {
            "datasets": [
                {
                    "format": "HTML",
                    "name": "Development Code",
                    "source_url": "https://example.gov/development-code",
                    "path": "documents/development-code.html",
                }
            ]
        },
    )
    html_path = package_root / "documents" / "development-code.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(
        """
        <html><body>
        <h1>R-1 Residential District</h1>
        <p>Minimum Lot Area: 0.25 acres</p>
        <p>Maximum Density: 4 units per acre</p>
        <p>Front Yard Setback: 25 ft</p>
        <p>Side Yard Setback: 8 ft</p>
        <p>Rear Yard Setback: 20 ft</p>
        <p>Maximum Building Height: 35 ft</p>
        <p>Maximum Lot Coverage: 45%</p>
        </body></html>
        """,
    )
    try:
        rules = extract_rules_from_documents("draper", priority_root=priority_root)
    finally:
        monkeypatch.undo()

    assert rules["R-1"]["min_lot_size_sqft"] == 10890.0
    assert rules["R-1"]["max_units_per_acre"] == 4.0
    assert rules["R-1"]["setbacks"] == {"front": 25.0, "side": 8.0, "rear": 20.0}
    assert rules["R-1"]["max_building_height_ft"] == 35.0
    assert rules["R-1"]["max_lot_coverage"] == 0.45


def test_normalized_rules_store_is_lookupable_and_prioritized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    normalized_root = tmp_path / "normalized_rules"
    write_normalized_rules(
        "draper",
        {
            "R3": {
                "district": "R3",
                "aliases": ["Residential"],
                "min_lot_size_sqft": 13000,
                "max_units_per_acre": 4,
                "setbacks": {"front": 25, "side": 10, "rear": 20},
                "max_building_height_ft": 35,
                "max_lot_coverage": 0.45,
                "allowed_use_types": ["single_family_residential"],
            }
        },
        normalized_rules_root=normalized_root,
        jurisdiction_name="Draper",
    )
    monkeypatch.setattr(zoning_code_rules_module, "NORMALIZED_RULES_ROOT", normalized_root)
    zoning_code_rules_module._load_normalized_rule_index.cache_clear()
    zoning_code_rules_module._load_normalized_rules_document.cache_clear()

    record, source = lookup_normalized_rule("Draper", "Residential", normalized_rules_root=normalized_root)

    assert source == "normalized_rules"
    assert record["district"] == "R3"

    dataset_dir = tmp_path / "zoning_dataset_sample" / "draper"
    _write_json(
        dataset_dir / "district_rules.json",
        {
            "R3": {
                "district": "R3",
                "min_lot_size_sqft": 1000,
                "max_units_per_acre": 1,
                "setbacks": {"front": 1, "side": 1, "rear": 1},
            }
        },
    )
    match = OverlayMatch(
        jurisdiction="Draper",
        jurisdiction_slug="draper",
        county_name="Salt Lake",
        dataset_dir=dataset_dir,
        district="Residential",
        district_name="Residential",
        overlays=(),
        density=None,
        source_layer="draper-zoning",
        intersection_area=1.0,
    )

    normalized = normalize_zoning_rules(match)

    assert normalized["rule_source"] == "normalized_rules"
    assert normalized["district"] == "Residential"
    assert normalized["min_lot_size_sqft"] == 13000.0
    assert normalized["setbacks"] == {"front": 25.0, "side": 10.0, "rear": 20.0}
