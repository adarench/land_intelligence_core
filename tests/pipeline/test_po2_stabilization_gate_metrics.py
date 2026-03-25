from __future__ import annotations

from fastapi import FastAPI

from bedrock.scripts import po2_stabilization_gate as gate


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)

    def post(self, _path: str, json=None):  # noqa: A002 - mirror client signature
        if not self._responses:
            raise AssertionError("No more fake responses available for client")
        return self._responses.pop(0)


def _mk_case(
    *,
    matrix: str,
    jurisdiction: str,
    parcel_id: str,
    expected_district: str | None = None,
) -> gate.MatrixCase:
    geometry = {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]],
    }
    return gate.MatrixCase(
        matrix=matrix,
        jurisdiction=jurisdiction,
        parcel_id=parcel_id,
        geometry=geometry,
        expected_district=expected_district,
        area_sqft=1000.0,
    )


def test_run_case_extracts_pipeline_failed_stage_and_error_from_detail_payload() -> None:
    case = _mk_case(
        matrix="m1",
        jurisdiction="Draper",
        parcel_id="case-1",
        expected_district="R-1",
    )
    parcel_client = _FakeClient([_FakeResponse(200, {"parcel_id": "case-1"})])
    zoning_client = _FakeClient(
        [
            _FakeResponse(
                200,
                {
                    "district": "R-1",
                    "min_lot_size_sqft": 7000,
                    "max_units_per_acre": 6.2,
                    "setbacks": {"front": 25, "side": 8, "rear": 20},
                },
            )
        ]
    )
    layout_client = _FakeClient(
        [
            _FakeResponse(
                200,
                {
                    "units": 4,
                },
            )
        ]
    )
    pipeline_client = _FakeClient(
        [
            _FakeResponse(
                422,
                {
                    "detail": {
                        "stage": "zoning.lookup",
                        "error": "incomplete_zoning_rules",
                        "message": "missing fields",
                    }
                },
            )
        ]
    )

    row = gate._run_case(case, parcel_client, zoning_client, layout_client, pipeline_client)

    assert row["pipeline"]["success"] is False
    assert row["pipeline"]["failed_stage"] == "zoning.lookup"
    assert row["pipeline"]["failed_error"] == "incomplete_zoning_rules"


def test_build_report_metric_rates_and_trends_are_computed_correctly(monkeypatch) -> None:
    cases = [
        _mk_case(matrix="alpha", jurisdiction="Draper", parcel_id="c1", expected_district="R1"),
        _mk_case(matrix="alpha", jurisdiction="Draper", parcel_id="c2", expected_district="R1"),
        _mk_case(matrix="beta", jurisdiction="Lehi", parcel_id="c3", expected_district="TH-5"),
        _mk_case(matrix="beta", jurisdiction="Lehi", parcel_id="c4", expected_district="TH-5"),
    ]
    rows_by_id = {
        "c1": {
            "matrix": "alpha",
            "jurisdiction": "Draper",
            "parcel_id": "c1",
            "expected_district": "R1",
            "parcel": {"success": True, "runtime_seconds": 0.01, "status_code": 200, "body": {}},
            "zoning": {
                "success": True,
                "runtime_seconds": 0.02,
                "status_code": 200,
                "district": "R1",
                "district_resolution_ok": True,
                "rule_complete": True,
                "missing_layout_fields": [],
                "stub_zoning": False,
                "fallback_usage": False,
                "body": {},
            },
            "layout": {"success": True, "runtime_seconds": 0.01, "status_code": 200, "units": 5, "body": {}},
            "pipeline": {
                "success": True,
                "runtime_seconds": 0.10,
                "status_code": 200,
                "failed_stage": None,
                "failed_error": None,
                "body": {},
            },
            "matrix_scope": "production",
            "synthetic_dataset": False,
            "parcel_usable": True,
            "partial_rule_usable": False,
        },
        "c2": {
            "matrix": "alpha",
            "jurisdiction": "Draper",
            "parcel_id": "c2",
            "expected_district": "R1",
            "parcel": {"success": True, "runtime_seconds": 0.01, "status_code": 200, "body": {}},
            "zoning": {
                "success": True,
                "runtime_seconds": 0.02,
                "status_code": 200,
                "district": "R1",
                "district_resolution_ok": True,
                "rule_complete": True,
                "missing_layout_fields": [],
                "stub_zoning": False,
                "fallback_usage": False,
                "body": {},
            },
            "layout": {"success": False, "runtime_seconds": 0.01, "status_code": 422, "units": 0, "body": {}},
            "pipeline": {
                "success": False,
                "runtime_seconds": 0.20,
                "status_code": 422,
                "failed_stage": "layout.search",
                "failed_error": "no_viable_layout",
                "body": {"detail": {"stage": "layout.search", "error": "no_viable_layout"}},
            },
            "matrix_scope": "production",
            "synthetic_dataset": False,
            "parcel_usable": False,
            "partial_rule_usable": False,
        },
        "c3": {
            "matrix": "beta",
            "jurisdiction": "Lehi",
            "parcel_id": "c3",
            "expected_district": "TH-5",
            "parcel": {"success": False, "runtime_seconds": 0.01, "status_code": 400, "body": {}},
            "zoning": {
                "success": False,
                "runtime_seconds": 0.02,
                "status_code": 422,
                "district": None,
                "district_resolution_ok": False,
                "rule_complete": False,
                "missing_layout_fields": list(gate.REQUIRED_LAYOUT_FIELDS),
                "stub_zoning": False,
                "fallback_usage": False,
                "body": {},
            },
            "layout": {"success": False, "runtime_seconds": 0.0, "status_code": 0, "units": 0, "body": {}},
            "pipeline": {
                "success": False,
                "runtime_seconds": 0.30,
                "status_code": 400,
                "failed_stage": "parcel.load",
                "failed_error": "invalid_parcel_input",
                "body": {"detail": {"stage": "parcel.load", "error": "invalid_parcel_input"}},
            },
            "matrix_scope": "production",
            "synthetic_dataset": False,
            "parcel_usable": False,
            "partial_rule_usable": False,
        },
        "c4": {
            "matrix": "beta",
            "jurisdiction": "Lehi",
            "parcel_id": "c4",
            "expected_district": "TH-5",
            "parcel": {"success": True, "runtime_seconds": 0.01, "status_code": 200, "body": {}},
            "zoning": {
                "success": True,
                "runtime_seconds": 0.02,
                "status_code": 200,
                "district": "TH-8",
                "district_resolution_ok": False,
                "rule_complete": False,
                "missing_layout_fields": ["setbacks.rear"],
                "stub_zoning": True,
                "fallback_usage": True,
                "body": {},
            },
            "layout": {"success": True, "runtime_seconds": 0.01, "status_code": 200, "units": 2, "body": {}},
            "pipeline": {
                "success": False,
                "runtime_seconds": 0.40,
                "status_code": 404,
                "failed_stage": "zoning.lookup",
                "failed_error": "no_district_match",
                "body": {"detail": {"stage": "zoning.lookup", "error": "no_district_match"}},
            },
            "matrix_scope": "production",
            "synthetic_dataset": True,
            "parcel_usable": True,
            "partial_rule_usable": True,
        },
    }

    monkeypatch.setattr(gate, "_load_matrix_cases", lambda: cases)
    monkeypatch.setattr(
        gate,
        "_run_case",
        lambda case, _parcel_client, _zoning_client, _layout_client, _pipeline_client: rows_by_id[case.parcel_id],
    )
    monkeypatch.setattr(gate, "create_parcel_app", lambda: FastAPI())
    monkeypatch.setattr(gate, "create_zoning_app", lambda: FastAPI())
    monkeypatch.setattr(gate, "create_pipeline_app", lambda: FastAPI())
    monkeypatch.setattr(gate, "_load_previous_metrics", lambda: {"metrics": {
        "pipeline_success_rate": 0.5,
        "zoning_success_rate": 0.5,
        "district_accuracy": 0.25,
        "rule_completeness_rate": 0.75,
        "geometry_stability_rate": 1.0,
        "stub_zoning_rate": 0.0,
        "fallback_usage_rate": 0.0,
        "synthetic_dataset_rate": 0.0,
        "parcel_usability_rate": 0.25,
        "partial_rule_usable_rate": 0.0,
    }})

    report = gate.build_report()
    metrics = report["metrics"]
    trend = report["trend_since_last_run"]

    assert metrics["pipeline_success_rate"] == 0.25
    assert metrics["zoning_success_rate"] == 0.75
    assert metrics["district_accuracy"] == 0.5
    assert metrics["rule_completeness_rate"] == 0.5
    assert metrics["geometry_stability_rate"] == 0.75
    assert metrics["stub_zoning_rate"] == 1 / 3
    assert metrics["fallback_usage_rate"] == 1 / 3
    assert metrics["synthetic_dataset_rate"] == 0.25
    assert metrics["parcel_usability_rate"] == 0.5
    assert metrics["partial_rule_usable_rate"] == 0.25
    assert metrics["failed_stage_counts"] == {
        "layout.search": 1,
        "parcel.load": 1,
        "zoning.lookup": 1,
    }
    assert metrics["failed_error_counts"] == {
        "no_viable_layout": 1,
        "invalid_parcel_input": 1,
        "no_district_match": 1,
    }

    assert metrics["matrix_breakdown"]["alpha"]["pipeline_success_rate"] == 0.5
    assert metrics["matrix_breakdown"]["beta"]["pipeline_success_rate"] == 0.0
    assert metrics["matrix_breakdown"]["beta"]["district_accuracy"] == 0.0

    assert trend["pipeline_success_rate_delta"] == -0.25
    assert trend["zoning_success_rate_delta"] == 0.25
    assert trend["district_accuracy_delta"] == 0.25
    assert trend["rule_completeness_rate_delta"] == -0.25
    assert trend["geometry_stability_rate_delta"] == -0.25
    assert trend["stub_zoning_rate_delta"] == 1 / 3
    assert trend["fallback_usage_rate_delta"] == 1 / 3
    assert trend["synthetic_dataset_rate_delta"] == 0.25
    assert trend["parcel_usability_rate_delta"] == 0.25
    assert trend["partial_rule_usable_rate_delta"] == 0.25
    assert report["gate"]["stub_zoning_passed"] is False
    assert report["gate"]["fallback_usage_passed"] is False
    assert report["gate"]["synthetic_dataset_passed"] is False
    assert report["evaluation_summary"]["status"] in {"warning", "poor"}
    assert isinstance(report["evaluation_summary"]["issues"], list)


def test_run_case_maps_validation_detail_list_to_request_validation_bucket() -> None:
    case = _mk_case(matrix="alpha", jurisdiction="Draper", parcel_id="c1", expected_district="R1")
    parcel_client = _FakeClient([_FakeResponse(200, {"parcel_id": "c1"})])
    zoning_client = _FakeClient(
        [
            _FakeResponse(
                200,
                {
                    "district": "R1",
                    "min_lot_size_sqft": 7000,
                    "max_units_per_acre": 6.2,
                    "setbacks": {"front": 25, "side": 8, "rear": 20},
                },
            )
        ]
    )
    layout_client = _FakeClient(
        [
            _FakeResponse(
                200,
                {
                    "units": 4,
                },
            )
        ]
    )
    pipeline_client = _FakeClient(
        [_FakeResponse(422, {"detail": [{"loc": ["body", "parcel"], "msg": "bad input"}]})]
    )

    row = gate._run_case(case, parcel_client, zoning_client, layout_client, pipeline_client)

    assert row["pipeline"]["failed_stage"] == "request.validation"
    assert row["pipeline"]["failed_error"] == "request_validation_error"


def test_extract_pipeline_failure_maps_text_500_to_internal_error() -> None:
    failed_stage, failed_error = gate._extract_pipeline_failure("Internal Server Error", 500)
    assert failed_stage == "internal"
    assert failed_error == "internal_error"
