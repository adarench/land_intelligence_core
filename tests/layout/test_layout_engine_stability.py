from __future__ import annotations

import importlib
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from shapely.geometry import Polygon

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
LAYOUT_ENGINE_ROOT = WORKSPACE_ROOT / "GIS_lot_layout_optimizer" / "apps" / "python-api" / "services" / "layout_engine"
LAYOUT_ENGINE_PACKAGE = "layout_engine_test_runtime"

if LAYOUT_ENGINE_PACKAGE not in sys.modules:
    package = types.ModuleType(LAYOUT_ENGINE_PACKAGE)
    package.__path__ = [str(LAYOUT_ENGINE_ROOT)]  # type: ignore[attr-defined]
    sys.modules[LAYOUT_ENGINE_PACKAGE] = package

run_layout_search = importlib.import_module(f"{LAYOUT_ENGINE_PACKAGE}.layout_search").run_layout_search  # type: ignore[attr-defined]
generate_candidates_multi_strategy = importlib.import_module(
    f"{LAYOUT_ENGINE_PACKAGE}.graph_generator"
).generate_candidates_multi_strategy
from layout_engine_test_runtime.lot_subdivision import (  # type: ignore  # noqa: E402
    SubdivisionResult,
    ValidationSummary,
    score_subdivision,
)


class LayoutEngineStabilityTest(unittest.TestCase):
    def _stability_parcels(self) -> list[Polygon]:
        return [
            Polygon([(0, 0), (0, 500), (600, 500), (600, 0), (0, 0)]),
            Polygon([(0, 0), (0, 450), (550, 450), (550, 0), (0, 0)]),
            Polygon([(0, 0), (0, 600), (500, 600), (500, 0), (0, 0)]),
            Polygon([(0, 0), (0, 520), (700, 520), (700, 0), (0, 0)]),
            Polygon([(0, 0), (0, 480), (680, 480), (680, 0), (0, 0)]),
            Polygon([(0, 0), (0, 560), (640, 560), (640, 0), (0, 0)]),
            Polygon([(0, 0), (0, 700), (240, 700), (240, 0), (0, 0)]),
            Polygon([(0, 0), (0, 900), (220, 900), (220, 0), (0, 0)]),
            Polygon([(0, 0), (0, 800), (260, 800), (260, 0), (0, 0)]),
            Polygon([(0, 0), (0, 420), (780, 420), (780, 0), (0, 0)]),
            Polygon([(0, 0), (0, 560), (240, 560), (240, 340), (560, 340), (560, 0), (0, 0)]),
            Polygon([(0, 0), (0, 620), (220, 620), (220, 380), (620, 380), (620, 0), (0, 0)]),
            Polygon([(0, 0), (0, 700), (210, 700), (210, 430), (700, 430), (700, 0), (0, 0)]),
            Polygon([(0, 0), (0, 540), (180, 540), (180, 320), (580, 320), (580, 0), (0, 0)]),
            Polygon([(0, 0), (0, 500), (120, 500), (120, 500), (520, 500), (520, 0), (520, 0), (0, 0)]),
            Polygon([(0, 0), (0, 560), (160, 560), (160, 560), (640, 560), (640, 0), (0, 0)]),
            Polygon([(0, 0), (0, 620), (200, 620), (200, 350), (200, 350), (700, 350), (700, 0), (0, 0)]),
            Polygon([(0, 0), (0, 510), (300, 510), (300, 260), (620, 260), (620, 260), (620, 0), (0, 0)]),
            Polygon([(0, 0), (0, 640), (210, 640), (210, 390), (210, 390), (640, 390), (640, 0), (0, 0)]),
            Polygon([(0, 0), (0, 720), (240, 720), (240, 430), (680, 430), (680, 430), (680, 0), (0, 0)]),
        ]

    def test_concave_parcel_generates_viable_layouts(self) -> None:
        parcel = Polygon(
            [
                (0, 0),
                (0, 500),
                (180, 500),
                (180, 300),
                (420, 300),
                (420, 500),
                (700, 500),
                (700, 0),
                (0, 0),
            ]
        )

        candidates = run_layout_search(
            parcel_polygon=parcel,
            area_sqft=parcel.area,
            to_lnglat=lambda x, y: [x, y],
            n_candidates=16,
            n_top=2,
            seed=7,
            road_width_ft=32.0,
            lot_depth=110.0,
            min_frontage_ft=50.0,
            use_prior=False,
        )

        self.assertTrue(candidates)
        self.assertGreater(candidates[0].result.metrics["lot_count"], 0)
        self.assertGreater(candidates[0].result.metrics["compliance_rate"], 0.0)

    def test_narrow_parcel_uses_fallback_parameters_without_failure(self) -> None:
        parcel = Polygon(
            [
                (0, 0),
                (0, 1400),
                (160, 1400),
                (160, 0),
                (0, 0),
            ]
        )

        candidates = run_layout_search(
            parcel_polygon=parcel,
            area_sqft=parcel.area,
            to_lnglat=lambda x, y: [x, y],
            n_candidates=12,
            n_top=1,
            seed=3,
            road_width_ft=32.0,
            lot_depth=120.0,
            min_frontage_ft=60.0,
            use_prior=False,
        )

        self.assertTrue(candidates)
        self.assertIn(
            candidates[0].network.generator_type,
            {"spine", "herringbone", "t_junction", "loop_custom", "cul_de_sac"},
        )

    def test_multi_strategy_generation_includes_production_strategies(self) -> None:
        parcel = Polygon([(0, 0), (0, 520), (620, 520), (620, 0), (0, 0)])
        candidates = generate_candidates_multi_strategy(
            parcel_polygon=parcel,
            area_sqft=parcel.area,
            n=15,
            seed=19,
            strategies=["grid", "spine-road", "cul-de-sac"],
        )
        generator_types = {candidate.generator_type for candidate in candidates}
        self.assertIn("grid", generator_types)
        self.assertIn("spine", generator_types)
        self.assertIn("cul_de_sac", generator_types)

    def test_multi_strategy_generation_is_deterministic_for_same_seed(self) -> None:
        parcel = Polygon([(0, 0), (0, 520), (620, 520), (620, 0), (0, 0)])
        first = generate_candidates_multi_strategy(
            parcel_polygon=parcel,
            area_sqft=parcel.area,
            n=15,
            seed=19,
            strategies=["grid", "spine-road", "cul-de-sac"],
        )
        second = generate_candidates_multi_strategy(
            parcel_polygon=parcel,
            area_sqft=parcel.area,
            n=15,
            seed=19,
            strategies=["grid", "spine-road", "cul-de-sac"],
        )

        def _signature(items):
            return [
                (
                    candidate.generator_type,
                    [
                        [(round(x, 3), round(y, 3)) for x, y in line.coords]
                        for line in candidate.centerlines
                    ],
                )
                for candidate in items
            ]

        self.assertEqual(_signature(first), _signature(second))

    def test_li2_stability_handles_twenty_parcels_under_runtime_budget(self) -> None:
        parcels = self._stability_parcels()
        self.assertEqual(len(parcels), 20)
        total_started = time.perf_counter()
        processed = 0

        for idx, parcel in enumerate(parcels):
            started = time.perf_counter()
            candidates = run_layout_search(
                parcel_polygon=parcel,
                area_sqft=parcel.area,
                to_lnglat=lambda x, y: [x, y],
                n_candidates=10,
                n_top=1,
                seed=idx + 31,
                road_width_ft=32.0,
                lot_depth=105.0,
                min_frontage_ft=45.0,
                min_lot_area_sqft=3200.0,
                side_setback_ft=5.0,
                min_buildable_width_ft=30.0,
                solver_constraints={
                    "min_lot_area_sqft": 3200.0,
                    "min_frontage_ft": 45.0,
                    "side_setback_ft": 5.0,
                    "required_buildable_width_ft": 30.0,
                    "max_buildable_depth_ft": 105.0,
                    "max_units": 80,
                },
                max_units=80,
                use_prior=False,
                max_runtime_seconds=50.0,
            )
            runtime_seconds = time.perf_counter() - started
            self.assertLess(runtime_seconds, 60.0)
            self.assertTrue(candidates, f"Expected viable layout for parcel index={idx}")
            self.assertTrue(all(lot.polygon.is_valid for lot in candidates[0].result.lots))
            for lot in candidates[0].result.lots:
                self.assertGreaterEqual(lot.area_sqft + 1e-6, 3200.0)
                self.assertLessEqual(lot.depth_ft, 105.0 * 1.02)
                self.assertGreaterEqual(lot.frontage_ft + 1e-6, 30.0 + 10.0)
                self.assertTrue(lot.polygon.within(parcel.buffer(1e-6)))
            road_segments = [segment.line for segment in candidates[0].result.segments]
            self.assertTrue(all(segment.is_valid for segment in road_segments))
            processed += 1

        self.assertEqual(processed, 20)
        self.assertLess(time.perf_counter() - total_started, 60.0)

    def test_li2_solver_exception_is_handled_without_crash(self) -> None:
        parcel = Polygon([(0, 0), (0, 520), (620, 520), (620, 0), (0, 0)])
        layout_search_module = importlib.import_module(f"{LAYOUT_ENGINE_PACKAGE}.layout_search")

        real_run_subdivision = layout_search_module.run_subdivision
        state = {"calls": 0}

        def flaky_subdivision(*args, **kwargs):
            state["calls"] += 1
            if state["calls"] <= 3:
                raise RuntimeError("synthetic subdivision failure")
            return real_run_subdivision(*args, **kwargs)

        with patch.object(layout_search_module, "run_subdivision", side_effect=flaky_subdivision):
            candidates = run_layout_search(
                parcel_polygon=parcel,
                area_sqft=parcel.area,
                to_lnglat=lambda x, y: [x, y],
                n_candidates=10,
                n_top=1,
                seed=13,
                road_width_ft=32.0,
                lot_depth=110.0,
                min_frontage_ft=50.0,
                min_lot_area_sqft=3500.0,
                solver_constraints={"min_lot_area_sqft": 3500.0, "max_buildable_depth_ft": 110.0},
                use_prior=False,
            )

        self.assertGreaterEqual(state["calls"], 4)
        self.assertTrue(isinstance(candidates, list))

    def test_scoring_prefers_better_secondary_feasibility_when_yield_equal(self) -> None:
        strong = SubdivisionResult(
            lots=[],
            segments=[],
            strips=[],
            validation=ValidationSummary(0, 0, 0, 0, 0, 0),
            metrics={
                "lot_count": 14,
                "max_units": 16,
                "total_road_ft": 900.0,
                "dev_area_ratio": 0.62,
                "avg_lot_area_sqft": 8200.0,
                "avg_lot_compactness": 0.72,
                "compliance_rate": 0.95,
            },
        )
        weak = SubdivisionResult(
            lots=[],
            segments=[],
            strips=[],
            validation=ValidationSummary(0, 0, 0, 0, 0, 0),
            metrics={
                "lot_count": 14,
                "max_units": 16,
                "total_road_ft": 900.0,
                "dev_area_ratio": 0.62,
                "avg_lot_area_sqft": 8200.0,
                "avg_lot_compactness": 0.08,
                "compliance_rate": 0.45,
            },
        )

        self.assertGreater(score_subdivision(strong), score_subdivision(weak))

    def test_scoring_is_deterministic_for_identical_metrics(self) -> None:
        result = SubdivisionResult(
            lots=[],
            segments=[],
            strips=[],
            validation=ValidationSummary(0, 0, 0, 0, 0, 0),
            metrics={
                "lot_count": 10,
                "max_units": 12,
                "total_road_ft": 1200.0,
                "dev_area_ratio": 0.48,
                "avg_lot_compactness": 0.66,
                "irregular_lot_share": 0.12,
                "lot_depth_cv": 0.14,
                "lot_frontage_cv": 0.11,
                "dead_end_ratio": 0.18,
                "disconnected_segment_ratio": 0.02,
                "awkward_leftover_ratio": 0.06,
                "compliance_rate": 0.93,
                "rejected_ratio": 0.07,
            },
        )
        scores = [score_subdivision(result) for _ in range(10)]
        self.assertEqual(len(set(scores)), 1)

    def test_scoring_is_monotonic_with_units_when_other_terms_fixed(self) -> None:
        low = SubdivisionResult(
            lots=[],
            segments=[],
            strips=[],
            validation=ValidationSummary(0, 0, 0, 0, 0, 0),
            metrics={
                "lot_count": 8,
                "max_units": 12,
                "total_road_ft": 1000.0,
                "dev_area_ratio": 0.42,
                "avg_lot_compactness": 0.60,
                "compliance_rate": 0.90,
            },
        )
        high = SubdivisionResult(
            lots=[],
            segments=[],
            strips=[],
            validation=ValidationSummary(0, 0, 0, 0, 0, 0),
            metrics={
                "lot_count": 10,
                "max_units": 12,
                "total_road_ft": 1000.0,
                "dev_area_ratio": 0.42,
                "avg_lot_compactness": 0.60,
                "compliance_rate": 0.90,
            },
        )

        self.assertGreater(score_subdivision(high), score_subdivision(low))

    def test_scoring_is_monotonic_with_efficiency_when_yield_fixed(self) -> None:
        inefficient = SubdivisionResult(
            lots=[],
            segments=[],
            strips=[],
            validation=ValidationSummary(0, 0, 0, 0, 0, 0),
            metrics={
                "lot_count": 10,
                "max_units": 12,
                "total_road_ft": 1400.0,
                "dev_area_ratio": 0.42,
                "avg_lot_compactness": 0.60,
                "compliance_rate": 0.90,
            },
        )
        efficient = SubdivisionResult(
            lots=[],
            segments=[],
            strips=[],
            validation=ValidationSummary(0, 0, 0, 0, 0, 0),
            metrics={
                "lot_count": 10,
                "max_units": 12,
                "total_road_ft": 900.0,
                "dev_area_ratio": 0.42,
                "avg_lot_compactness": 0.60,
                "compliance_rate": 0.90,
            },
        )

        self.assertGreater(score_subdivision(efficient), score_subdivision(inefficient))

    def test_scoring_dominance_guard_keeps_higher_yield_layout_ahead(self) -> None:
        dominant = SubdivisionResult(
            lots=[],
            segments=[],
            strips=[],
            validation=ValidationSummary(0, 0, 0, 0, 0, 0),
            metrics={
                "lot_count": 12,
                "max_units": 12,
                "total_road_ft": 1000.0,
                "dev_area_ratio": 0.38,
                "avg_lot_compactness": 0.45,
                "compliance_rate": 0.75,
            },
        )
        weaker = SubdivisionResult(
            lots=[],
            segments=[],
            strips=[],
            validation=ValidationSummary(0, 0, 0, 0, 0, 0),
            metrics={
                "lot_count": 10,
                "max_units": 12,
                "total_road_ft": 1000.0,
                "dev_area_ratio": 0.60,
                "avg_lot_compactness": 0.90,
                "compliance_rate": 1.0,
            },
        )

        self.assertGreater(score_subdivision(dominant), score_subdivision(weaker))

    def test_frontage_change_produces_geometry_change(self) -> None:
        parcel = Polygon([(0, 0), (0, 520), (700, 520), (700, 0), (0, 0)])
        base_kwargs = dict(
            parcel_polygon=parcel,
            area_sqft=parcel.area,
            to_lnglat=lambda x, y: [x, y],
            n_candidates=12,
            n_top=1,
            seed=7,
            road_width_ft=32.0,
            min_lot_area_sqft=4000.0,
            side_setback_ft=8.0,
            min_buildable_width_ft=36.0,
            lot_depth=110.0,
            max_units=24,
            use_prior=False,
        )

        narrow = run_layout_search(
            **base_kwargs,
            min_frontage_ft=45.0,
            solver_constraints={
                "min_lot_area_sqft": 4000.0,
                "min_frontage_ft": 45.0,
                "side_setback_ft": 8.0,
                "required_buildable_width_ft": 36.0,
                "max_buildable_depth_ft": 110.0,
                "max_units": 24,
            },
        )[0]
        wide = run_layout_search(
            **base_kwargs,
            min_frontage_ft=70.0,
            solver_constraints={
                "min_lot_area_sqft": 4000.0,
                "min_frontage_ft": 70.0,
                "side_setback_ft": 8.0,
                "required_buildable_width_ft": 36.0,
                "max_buildable_depth_ft": 110.0,
                "max_units": 24,
            },
        )[0]

        self.assertNotEqual(narrow.result.metrics["avg_frontage_ft"], wide.result.metrics["avg_frontage_ft"])
        self.assertNotEqual(narrow.geojson["features"], wide.geojson["features"])

    def test_block_depth_change_produces_geometry_change(self) -> None:
        parcel = Polygon([(0, 0), (0, 520), (700, 520), (700, 0), (0, 0)])
        base_kwargs = dict(
            parcel_polygon=parcel,
            area_sqft=parcel.area,
            to_lnglat=lambda x, y: [x, y],
            n_candidates=12,
            n_top=1,
            seed=7,
            road_width_ft=32.0,
            min_lot_area_sqft=4000.0,
            side_setback_ft=8.0,
            min_buildable_width_ft=36.0,
            min_frontage_ft=55.0,
            max_units=24,
            use_prior=False,
        )

        shallow = run_layout_search(
            **base_kwargs,
            lot_depth=90.0,
            solver_constraints={
                "min_lot_area_sqft": 4000.0,
                "min_frontage_ft": 55.0,
                "side_setback_ft": 8.0,
                "required_buildable_width_ft": 36.0,
                "max_buildable_depth_ft": 90.0,
                "max_units": 24,
            },
        )[0]
        deep = run_layout_search(
            **base_kwargs,
            lot_depth=130.0,
            solver_constraints={
                "min_lot_area_sqft": 4000.0,
                "min_frontage_ft": 55.0,
                "side_setback_ft": 8.0,
                "required_buildable_width_ft": 36.0,
                "max_buildable_depth_ft": 130.0,
                "max_units": 24,
            },
        )[0]

        self.assertNotEqual(shallow.result.metrics["avg_depth_ft"], deep.result.metrics["avg_depth_ft"])
        self.assertNotEqual(shallow.geojson["features"], deep.geojson["features"])


if __name__ == "__main__":
    unittest.main()
