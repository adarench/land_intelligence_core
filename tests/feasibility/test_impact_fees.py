from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (REPO_ROOT, REPO_ROOT / "bedrock"):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from bedrock.models.impact_fee_models import (
    calculate_impact_fees,
    resolve_impact_fee_per_unit,
)
from bedrock.models.cost_models import DEFAULT_PERMITTING_COST_PER_UNIT


class ImpactFeeModelsTest(unittest.TestCase):
    def test_known_jurisdiction_returns_specific_fee(self) -> None:
        fee = resolve_impact_fee_per_unit(jurisdiction="Lehi")
        self.assertGreater(fee, DEFAULT_PERMITTING_COST_PER_UNIT)

    def test_unknown_jurisdiction_returns_default(self) -> None:
        fee = resolve_impact_fee_per_unit(jurisdiction="Nonexistent City")
        self.assertEqual(fee, DEFAULT_PERMITTING_COST_PER_UNIT)

    def test_none_jurisdiction_returns_default(self) -> None:
        fee = resolve_impact_fee_per_unit(jurisdiction=None)
        self.assertEqual(fee, DEFAULT_PERMITTING_COST_PER_UNIT)

    def test_case_insensitive_lookup(self) -> None:
        fee_lower = resolve_impact_fee_per_unit(jurisdiction="lehi")
        fee_upper = resolve_impact_fee_per_unit(jurisdiction="LEHI")
        fee_exact = resolve_impact_fee_per_unit(jurisdiction="Lehi")
        self.assertEqual(fee_lower, fee_exact)
        self.assertEqual(fee_upper, fee_exact)

    def test_calculate_impact_fees_multiplies_by_units(self) -> None:
        per_unit = resolve_impact_fee_per_unit(jurisdiction="Herriman")
        total = calculate_impact_fees(units=10, jurisdiction="Herriman")
        self.assertEqual(total, per_unit * 10)

    def test_calculate_impact_fees_zero_units(self) -> None:
        total = calculate_impact_fees(units=0, jurisdiction="Lehi")
        self.assertEqual(total, 0.0)

    def test_jurisdictions_have_higher_fees_than_default(self) -> None:
        """Most covered jurisdictions should have fees above the old flat default."""
        for jurisdiction in ["Lehi", "Draper", "Herriman", "South Jordan"]:
            fee = resolve_impact_fee_per_unit(jurisdiction=jurisdiction)
            self.assertGreater(
                fee,
                DEFAULT_PERMITTING_COST_PER_UNIT,
                f"{jurisdiction} fee ({fee}) should exceed default ({DEFAULT_PERMITTING_COST_PER_UNIT})",
            )


if __name__ == "__main__":
    unittest.main()
