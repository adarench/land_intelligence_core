#!/usr/bin/env python3
"""Run a reproducible real-parcel Bedrock pipeline batch and persist raw artifacts."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shapely.geometry import shape


WEB_BASE = "http://127.0.0.1:3000"
BEDROCK_BASE = "http://127.0.0.1:8000"
HEADERS = {"content-type": "application/json"}
OUTPUT_DIR = Path("artifacts/real_parcel_batches")

SEARCH_CONFIGS = (
    {
        "county": "Utah",
        "label": "Draper",
        "jurisdiction": "Draper",
        "minLng": -111.95,
        "minLat": 40.47,
        "maxLng": -111.79,
        "maxLat": 40.59,
        "limit": 200,
        "select": 15,
        "target_area_deg2": 0.0000015,
    },
    {
        "county": "Utah",
        "label": "Provo",
        "jurisdiction": "Provo",
        "minLng": -111.72,
        "minLat": 40.19,
        "maxLng": -111.58,
        "maxLat": 40.29,
        "limit": 200,
        "select": 15,
        "target_area_deg2": 0.0000015,
    },
    {
        "county": "Salt Lake",
        "label": "SaltLakeCity",
        "jurisdiction": "Salt Lake City",
        "minLng": -112.04,
        "minLat": 40.66,
        "maxLng": -111.83,
        "maxLat": 40.82,
        "limit": 220,
        "select": 15,
        "target_area_deg2": 0.0000012,
    },
    {
        "county": "Salt Lake",
        "label": "WestValleyCity",
        "jurisdiction": "West Valley City",
        "minLng": -111.95,
        "minLat": 40.62,
        "maxLng": -111.84,
        "maxLat": 40.69,
        "limit": 220,
        "select": 15,
        "target_area_deg2": 0.0000012,
    },
)


def get_run_summary(feasibility_result: dict[str, Any] | None) -> dict[str, Any]:
    feasibility = feasibility_result or {}
    return {
        "status": feasibility.get("status"),
        "units": feasibility.get("units"),
        "projected_revenue": feasibility.get("projected_revenue"),
        "projected_cost": feasibility.get("projected_cost"),
        "ROI": feasibility.get("ROI"),
        "projected_profit": feasibility.get("projected_profit"),
    }


def get_near_feasible_summary(near_feasible_result: dict[str, Any] | None) -> dict[str, Any]:
    near_feasible = near_feasible_result or {}
    financial_upside = near_feasible.get("financial_upside") or {}
    return {
        "status": near_feasible.get("status"),
        "units": financial_upside.get("relaxed_units"),
        "projected_revenue": None,
        "projected_cost": None,
        "ROI": financial_upside.get("ROI"),
        "projected_profit": financial_upside.get("projected_profit"),
    }


def get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=120) as response:
        return json.load(response)


def post_json(url: str, payload: dict[str, Any]) -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


def infer_jurisdiction(parcel: dict[str, Any]) -> str | None:
    raw = parcel.get("rawAttributes") or {}
    for key in ("PARCEL_CITY", "CITY", "MUNICIPALITY", "JURISDICTION", "SITUS_CITY"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    county = parcel.get("county")
    return county if isinstance(county, str) and county.strip() else None


def area_score(geometry: dict[str, Any], target_area_deg2: float) -> float:
    return abs(abs(shape(geometry).area) - target_area_deg2)


def collect_selected_parcels() -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for config in SEARCH_CONFIGS:
        query = urllib.parse.urlencode(
            {
                "county": config["county"],
                "minLng": config["minLng"],
                "minLat": config["minLat"],
                "maxLng": config["maxLng"],
                "maxLat": config["maxLat"],
                "limit": config["limit"],
            }
        )
        url = f"{WEB_BASE}/api/parcels/in-bounds?{query}"
        parcels = get_json(url)
        filtered: list[dict[str, Any]] = []
        for parcel in parcels:
            if parcel.get("sourceProvider") != "Utah ArcGIS":
                continue
            geometry = parcel.get("geometryGeoJSON")
            if not isinstance(geometry, dict):
                continue
            jurisdiction = infer_jurisdiction(parcel)
            if jurisdiction != config["jurisdiction"]:
                continue
            try:
                score = area_score(geometry, float(config["target_area_deg2"]))
            except Exception:
                continue
            filtered.append(
                {
                    "parcel_id": parcel["id"],
                    "county": config["county"],
                    "jurisdiction": jurisdiction,
                    "source_bucket": config["label"],
                    "selection_score": score,
                    "geometry": geometry,
                }
            )
        filtered.sort(key=lambda item: (item["selection_score"], item["parcel_id"]))
        selected.extend(filtered[: int(config["select"])])
    return selected


def classify_response(parcel: dict[str, Any], status_code: int, payload: Any) -> dict[str, Any]:
    record = {
        "parcel_id": parcel["parcel_id"],
        "jurisdiction": parcel["jurisdiction"],
        "run_id": payload.get("run_id") if isinstance(payload, dict) else None,
        "status_code": status_code,
        "status": None,
        "reason_category": None,
        "units": None,
    }
    if status_code == 200 and isinstance(payload, dict):
        record["status"] = payload.get("status")
        feasibility = payload.get("feasibility_result") or {}
        summary = get_run_summary(feasibility)
        record["units"] = ((payload.get("layout_result") or {}).get("unit_count")) or summary.get("units")
        if feasibility:
            record["ROI"] = summary.get("ROI")
            record["projected_profit"] = summary.get("projected_profit")
            record["projected_revenue"] = summary.get("projected_revenue")
            record["projected_cost"] = summary.get("projected_cost")
            record["estimated_home_price"] = feasibility.get("estimated_home_price")
            record["price_per_sqft"] = feasibility.get("price_per_sqft")
            record["ROI_best_case"] = feasibility.get("ROI_best_case")
            record["ROI_worst_case"] = feasibility.get("ROI_worst_case")
            record["break_even_price"] = feasibility.get("break_even_price")
            record["confidence"] = feasibility.get("confidence")
            record["confidence_score"] = feasibility.get("confidence_score", feasibility.get("confidence"))
            record["key_risk_factors"] = feasibility.get("key_risk_factors")
        if record["status"] == "non_buildable":
            record["reason_category"] = "ZONING_CONSTRAINT_FAIL"
        elif record["status"] == "near_feasible":
            near = payload.get("near_feasible_result") or {}
            near_summary = get_near_feasible_summary(near)
            record["reason_category"] = near.get("reason_category")
            record["near_feasible_result"] = near
            record["units"] = near_summary.get("units")
            record["ROI"] = near_summary.get("ROI")
            record["projected_profit"] = near_summary.get("projected_profit")
            record["projected_revenue"] = near_summary.get("projected_revenue")
            record["projected_cost"] = near_summary.get("projected_cost")
            upside = near.get("financial_upside") or {}
            record["upside_ROI"] = upside.get("ROI")
            record["upside_projected_profit"] = upside.get("projected_profit")
            record["upside_relaxed_units"] = upside.get("relaxed_units")
        elif record["status"] == "unsupported":
            record["reason_category"] = "SOLVER_FAIL"
        return record

    detail = payload.get("detail", {}) if isinstance(payload, dict) else {}
    record["status"] = "failed"
    record["reason_category"] = detail.get("reason_category")
    if record["reason_category"] is None:
        error = detail.get("error")
        if error == "invalid_geometry":
            record["reason_category"] = "GEOMETRY_INVALID"
        elif error == "frontage_fail":
            record["reason_category"] = "FRONTAGE_FAIL"
        elif error == "zoning_constraint_fail":
            record["reason_category"] = "ZONING_CONSTRAINT_FAIL"
        elif error == "solver_fail" or error == "layout_solver_failure":
            record["reason_category"] = "SOLVER_FAIL"
        elif error == "too_small":
            record["reason_category"] = "DENSITY_LIMIT"
        elif error == "no_buildable_units":
            record["reason_category"] = "DENSITY_LIMIT"
    record["error"] = detail.get("error")
    record["stage"] = detail.get("stage")
    if "min_lot_area_sqft" in detail:
        record["min_lot_area_sqft"] = detail.get("min_lot_area_sqft")
    if "approx_frontage_ft" in detail:
        record["approx_frontage_ft"] = detail.get("approx_frontage_ft")
    if "required_frontage_ft" in detail:
        record["required_frontage_ft"] = detail.get("required_frontage_ft")
    return record


def validate_actionable_runs(raw_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for item in raw_results:
        if item.get("status") not in {"completed", "near_feasible"}:
            continue
        missing = [
            field
            for field in ("units", "ROI", "projected_profit")
            if item.get(field) is None
        ]
        if missing:
            violation = {
                "parcel_id": item.get("parcel_id"),
                "run_id": item.get("run_id"),
                "status": item.get("status"),
                "missing_fields": missing,
            }
            violations.append(violation)
            print(f"WARN actionable run missing economics: {json.dumps(violation, sort_keys=True)}", file=sys.stderr)
    return violations


def build_batch_run(raw_results: list[dict[str, Any]], *, timestamp: str) -> dict[str, Any]:
    roi_values = [float(item["ROI"]) for item in raw_results if item.get("ROI") is not None]
    profit_values = [float(item["projected_profit"]) for item in raw_results if item.get("projected_profit") is not None]
    pass_count = sum(1 for item in raw_results if item.get("status") == "completed")
    near_feasible_count = sum(1 for item in raw_results if item.get("status") == "near_feasible")
    batch_id = f"real-parcel-batch-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    runs = [
        {
            "parcel_id": item["parcel_id"],
            "run_id": item.get("run_id"),
            "status": item.get("status"),
            "units": item.get("units"),
            "projected_revenue": item.get("projected_revenue"),
            "projected_cost": item.get("projected_cost"),
            "roi": item.get("ROI"),
            "profit": item.get("projected_profit"),
        }
        for item in raw_results
    ]
    return {
        "batch_id": batch_id,
        "timestamp": timestamp,
        "parcel_count": len(raw_results),
        "runs": runs,
        "metrics": {
            "avg_roi": (sum(roi_values) / len(roi_values)) if roi_values else None,
            "avg_profit": (sum(profit_values) / len(profit_values)) if profit_values else None,
            "pass_rate": pass_count / max(len(raw_results), 1),
            "near_feasible_rate": near_feasible_count / max(len(raw_results), 1),
        },
    }


def run_batch() -> dict[str, Any]:
    selected = collect_selected_parcels()
    raw_results: list[dict[str, Any]] = []
    for parcel in selected:
        status_code, payload = post_json(
            f"{BEDROCK_BASE}/pipeline/run",
            {
                "parcel_geometry": parcel["geometry"],
                "parcel_id": parcel["parcel_id"],
                "jurisdiction": parcel["jurisdiction"],
                "max_candidates": 30,
            },
        )
        raw_results.append(classify_response(parcel, status_code, payload))

    generated_at = datetime.now(timezone.utc).isoformat()
    validation_violations = validate_actionable_runs(raw_results)
    overall_success_rate = sum(1 for item in raw_results if item["status"] == "completed") / max(len(raw_results), 1)
    actionable_rate = sum(1 for item in raw_results if item["status"] in {"completed", "near_feasible"}) / max(len(raw_results), 1)
    roi_values = [float(item["ROI"]) for item in raw_results if item.get("ROI") is not None]
    profitable_share = sum(1 for item in raw_results if (item.get("ROI") or -1) > 0) / max(len(raw_results), 1)
    near_feasible_profitable_share = sum(
        1 for item in raw_results if item.get("status") == "near_feasible" and (item.get("upside_ROI") or -1) > 0
    ) / max(sum(1 for item in raw_results if item.get("status") == "near_feasible"), 1)
    by_jurisdiction: dict[str, dict[str, Any]] = {}
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in raw_results:
        grouped[str(item["jurisdiction"])].append(item)
    for jurisdiction, items in grouped.items():
        successes = sum(1 for item in items if item["status"] == "completed")
        actionable = sum(1 for item in items if item["status"] in {"completed", "near_feasible"})
        profitable = sum(1 for item in items if (item.get("ROI") or -1) > 0)
        by_jurisdiction[jurisdiction] = {
            "count": len(items),
            "success_rate": successes / max(len(items), 1),
            "actionable_rate": actionable / max(len(items), 1),
            "profitable_share": profitable / max(len(items), 1),
        }
    failure_distribution = Counter(
        item["reason_category"] for item in raw_results if item["status"] == "failed" and item["reason_category"]
    )
    near_feasible_distribution = Counter(
        item["reason_category"] for item in raw_results if item["status"] == "near_feasible" and item["reason_category"]
    )
    top_value = sorted(
        raw_results,
        key=lambda item: float(item.get("projected_profit") or item.get("upside_projected_profit") or float("-inf")),
        reverse=True,
    )[:5]

    return {
        "generated_at": generated_at,
        "selected_count": len(selected),
        "results": raw_results,
        "batch_run": build_batch_run(raw_results, timestamp=generated_at),
        "validation": {
            "actionable_run_economics_violations": validation_violations,
        },
        "metrics": {
            "overall_success_rate": overall_success_rate,
            "overall_actionable_rate": actionable_rate,
            "profitable_share": profitable_share,
            "near_feasible_profitable_share": near_feasible_profitable_share,
            "success_rate_by_jurisdiction": by_jurisdiction,
            "failure_distribution": dict(failure_distribution),
            "near_feasible_distribution": dict(near_feasible_distribution),
            "roi_distribution": {
                "count": len(roi_values),
                "min": min(roi_values) if roi_values else None,
                "max": max(roi_values) if roi_values else None,
                "avg": (sum(roi_values) / len(roi_values)) if roi_values else None,
            },
            "top_5_highest_value_parcels": [
                {
                    "parcel_id": item["parcel_id"],
                    "jurisdiction": item["jurisdiction"],
                    "status": item["status"],
                    "projected_profit": item.get("projected_profit"),
                    "upside_projected_profit": item.get("upside_projected_profit"),
                    "ROI": item.get("ROI"),
                    "upside_ROI": item.get("upside_ROI"),
                }
                for item in top_value
            ],
        },
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = run_batch()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_path = OUTPUT_DIR / f"utah_real_parcel_batch_{timestamp}.json"
    batch_path = OUTPUT_DIR / f"utah_real_parcel_batch_{timestamp}_batch.json"
    metrics_path = OUTPUT_DIR / f"utah_real_parcel_batch_{timestamp}_metrics.json"
    mapping_path = OUTPUT_DIR / f"utah_real_parcel_batch_{timestamp}_run_ids.json"

    results_payload = payload["results"]
    batch_payload = payload["batch_run"]
    metrics_payload = {
        "generated_at": payload["generated_at"],
        "metrics": payload["metrics"],
    }
    mapping_payload = {item["parcel_id"]: item.get("run_id") for item in batch_payload["runs"]}
    results_path.write_text(json.dumps(results_payload, indent=2))
    batch_path.write_text(json.dumps(batch_payload, indent=2))
    metrics_path.write_text(json.dumps(metrics_payload, indent=2))
    mapping_path.write_text(json.dumps(mapping_payload, indent=2, sort_keys=True))
    print(
        json.dumps(
            {
                "results_path": str(results_path),
                "batch_path": str(batch_path),
                "metrics_path": str(metrics_path),
                "mapping_path": str(mapping_path),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
