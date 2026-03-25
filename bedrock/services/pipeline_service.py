"""Pipeline orchestration for parcel -> zoning -> layout -> feasibility execution."""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from shapely.geometry import shape

from bedrock.contracts.base import BedrockModel
from bedrock.contracts.base import EngineMetadata
from bedrock.contracts.feasibility import FeasibilityResult
from bedrock.contracts.layout_result import LayoutResult
from bedrock.contracts.market_data import MarketData
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.pipeline_execution_result import PipelineExecutionResult
from bedrock.contracts.zoning_rules import ZoningRules
from bedrock.contracts.validators import (
    validate_feasibility_pipeline_contracts,
    validate_feasibility_result_output,
    validate_layout_result_output,
    validate_parcel_output,
    validate_zoning_rules_for_layout,
)
from bedrock.services.feasibility_service import FeasibilityService
from bedrock.services.layout_service import LayoutSearchError, search_subdivision_layout
from bedrock.services.parcel_service import ParcelService
from bedrock.services.pipeline_run_store import PipelineRunStore
from bedrock.services.zoning_rule_normalizer import normalize_rules as normalize_zoning_rules
from bedrock.services.zoning_service import IncompleteZoningRulesError, InvalidZoningRulesError, ZoningService
from zoning_data_scraper.services.zoning_overlay import (
    AmbiguousJurisdictionMatchError,
    AmbiguousZoningMatchError,
    NoJurisdictionMatchError,
    NoZoningMatchError,
)

logger = logging.getLogger(__name__)

NON_BUILDABLE_DISTRICTS_ENABLED = True
NON_BUILDABLE_DISTRICTS = (
    {"jurisdiction": "provo", "district": "RC", "reason": "historical_constraint"},
    {"jurisdiction": "murray", "district": "M-G", "reason": "non_residential"},
)


class PipelineStageError(RuntimeError):
    """Structured stage failure surfaced by pipeline orchestration."""

    def __init__(
        self,
        *,
        stage: str,
        error: str,
        message: str,
        status_code: int,
        details: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.error = error
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "error": self.error,
            "stage": self.stage,
            "message": self.message,
            **self.details,
        }


class PipelineRunLog(BedrockModel):
    run_id: str
    status: str = "completed"
    parcel_id: str
    zoning_district: str
    zoning_source: str
    zoning_geometry_match_success: bool
    zoning_bypassed: bool = False
    bypass_reason: Optional[str] = None
    layout_units: Optional[int] = None
    layout_score: Optional[float] = None
    feasibility_roi: Optional[float] = None
    timestamp: datetime


class PipelineRunRecord(BedrockModel):
    run_id: str
    timestamp: datetime
    status: str = "completed"
    parcel: Parcel
    zoning: ZoningRules
    layout: Optional[LayoutResult] = None
    feasibility: Optional[FeasibilityResult] = None
    git_commit: Optional[str] = None
    input_hash: Optional[str] = None
    stage_runtimes: dict[str, float] = {}
    zoning_bypassed: bool = False
    bypass_reason: Optional[str] = None


class ZoningStageResult(BedrockModel):
    rules: ZoningRules
    status: str = "completed"
    bypass_reason: Optional[str] = None

    @property
    def is_bypassed(self) -> bool:
        return self.status != "completed"


class PipelineService:
    """Compose the canonical PO-2 execution chain."""

    def __init__(
        self,
        dataset_root: Optional[Path] = None,
        run_store: Optional[PipelineRunStore] = None,
    ) -> None:
        self.parcel_service = ParcelService()
        self.zoning_service = ZoningService(dataset_root=dataset_root)
        self.feasibility_service = FeasibilityService()
        self.run_store = run_store or PipelineRunStore()

    def run(
        self,
        *,
        parcel: Optional[Parcel] = None,
        parcel_geometry: Optional[dict] = None,
        max_candidates: int = 50,
        parcel_id: Optional[str] = None,
        jurisdiction: Optional[str] = None,
        market_data: Optional[MarketData] = None,
    ) -> PipelineExecutionResult:
        stage_runtimes: dict[str, float] = {}
        input_hash = self._build_input_hash(
            parcel=parcel,
            parcel_geometry=parcel_geometry,
            max_candidates=max_candidates,
            parcel_id=parcel_id,
            jurisdiction=jurisdiction,
            market_data=market_data,
        )

        stage_started = time.perf_counter()
        parcel_contract = self._load_parcel_stage(
            parcel=parcel,
            parcel_geometry=parcel_geometry,
            parcel_id=parcel_id,
            jurisdiction=jurisdiction,
        )
        stage_runtimes["parcel.load"] = round(time.perf_counter() - stage_started, 6)

        stage_started = time.perf_counter()
        zoning_stage = self._lookup_zoning_stage(parcel_contract)
        zoning_rules = zoning_stage.rules
        stage_runtimes["zoning.lookup"] = round(time.perf_counter() - stage_started, 6)

        status = zoning_stage.status
        zoning_bypassed = zoning_stage.is_bypassed
        bypass_reason = zoning_stage.bypass_reason
        layout_result: Optional[LayoutResult] = None
        feasibility_result: Optional[FeasibilityResult] = None
        if not zoning_bypassed:
            stage_started = time.perf_counter()
            layout_result = self._search_layout_stage(
                parcel_contract,
                zoning_rules,
                max_candidates=max_candidates,
            )
            stage_runtimes["layout.search"] = round(time.perf_counter() - stage_started, 6)

            stage_started = time.perf_counter()
            feasibility_result = self._evaluate_feasibility_stage(
                parcel_contract,
                zoning_rules,
                layout_result,
                market_data=market_data,
            )
            stage_runtimes["feasibility.evaluate"] = round(time.perf_counter() - stage_started, 6)
        run_id = str(uuid4())
        timestamp = datetime.now(timezone.utc)
        run_record = PipelineRunRecord(
            run_id=run_id,
            timestamp=timestamp,
            status=status,
            parcel=parcel_contract,
            zoning=zoning_rules,
            layout=layout_result,
            feasibility=feasibility_result,
            git_commit=self._resolve_git_commit(),
            input_hash=input_hash,
            stage_runtimes=stage_runtimes,
            zoning_bypassed=zoning_bypassed,
            bypass_reason=bypass_reason,
        )
        self.run_store.save_run(run_id, run_record)

        run_log = PipelineRunLog(
            run_id=run_id,
            status=status,
            parcel_id=parcel_contract.parcel_id,
            zoning_district=zoning_rules.district,
            zoning_source=str(zoning_rules.metadata.source_run_id if zoning_rules.metadata is not None else "unknown"),
            zoning_geometry_match_success=True,
            zoning_bypassed=zoning_bypassed,
            bypass_reason=bypass_reason,
            layout_units=layout_result.units if layout_result is not None else None,
            layout_score=layout_result.score if layout_result is not None else None,
            feasibility_roi=feasibility_result.ROI if feasibility_result is not None else None,
            timestamp=timestamp,
        )
        self.run_store.save(run_log)
        return PipelineExecutionResult(
            run_id=run_id,
            status=status,
            feasibility_result=feasibility_result,
        )

    @staticmethod
    def _build_input_hash(
        *,
        parcel: Optional[Parcel],
        parcel_geometry: Optional[dict],
        max_candidates: int,
        parcel_id: Optional[str],
        jurisdiction: Optional[str],
        market_data: Optional[MarketData],
    ) -> str:
        payload = {
            "parcel": parcel.model_dump(mode="json") if parcel is not None else None,
            "parcel_geometry": parcel_geometry,
            "max_candidates": max_candidates,
            "parcel_id": parcel_id,
            "jurisdiction": jurisdiction,
            "market_data": market_data.model_dump(mode="json") if market_data is not None else None,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _resolve_git_commit() -> Optional[str]:
        try:
            workspace_root = Path(__file__).resolve().parents[2]
            return (
                subprocess.check_output(
                    ["git", "-C", str(workspace_root), "rev-parse", "HEAD"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
                or None
            )
        except Exception:
            return None

    def _load_parcel_stage(
        self,
        *,
        parcel: Optional[Parcel],
        parcel_geometry: Optional[dict],
        parcel_id: Optional[str],
        jurisdiction: Optional[str],
    ) -> Parcel:
        try:
            if parcel is not None:
                return validate_parcel_output(self.parcel_service.normalize_parcel_contract(parcel))
            if parcel_geometry is None:
                raise ValueError("pipeline input must include either 'parcel' or 'parcel_geometry'")
            if not (jurisdiction or "").strip():
                raise ValueError("pipeline parcel_geometry input requires jurisdiction")
            loaded = self.parcel_service.load_parcel(
                geometry=parcel_geometry,
                parcel_id=parcel_id,
                jurisdiction=jurisdiction,
            )
            return validate_parcel_output(loaded)
        except ValueError as exc:
            raise PipelineStageError(
                stage="parcel.load",
                error="invalid_parcel_input",
                message=str(exc),
                status_code=400,
            ) from exc

    def _lookup_zoning_stage(self, parcel: Parcel) -> ZoningStageResult:
        try:
            bypassed = self._lookup_non_buildable_zoning_stage(parcel)
            if bypassed is not None:
                return bypassed
            zoning = self.zoning_service.lookup(parcel)
            zoning_rules = validate_zoning_rules_for_layout(zoning.rules)
            self._validate_real_overlay_zoning_source(parcel, zoning_rules)
            return ZoningStageResult(rules=zoning_rules)
        except (NoJurisdictionMatchError, NoZoningMatchError) as exc:
            hinted = self._lookup_non_buildable_from_parcel_hint(parcel)
            if hinted is not None:
                return hinted
            return self._build_unsupported_zoning_stage(parcel, reason="unsupported_jurisdiction")
        except (AmbiguousJurisdictionMatchError, AmbiguousZoningMatchError) as exc:
            raise PipelineStageError(
                stage="zoning.lookup",
                error="ambiguous_district_match",
                message=str(exc),
                status_code=409,
            ) from exc
        except IncompleteZoningRulesError as exc:
            hinted = self._lookup_non_buildable_from_parcel_hint(parcel)
            if hinted is not None:
                return hinted
            raise PipelineStageError(
                stage="zoning.lookup",
                error="incomplete_zoning_rules",
                message="Zoning rules are incomplete for layout execution",
                status_code=422,
                details={"district": exc.district, "missing_fields": exc.missing_fields},
            ) from exc
        except InvalidZoningRulesError as exc:
            hinted = self._lookup_non_buildable_from_parcel_hint(parcel)
            if hinted is not None:
                return hinted
            raise PipelineStageError(
                stage="zoning.lookup",
                error="invalid_zoning_rules",
                message="Zoning rules are invalid for layout execution",
                status_code=422,
                details={"district": exc.district, "violations": exc.violations},
            ) from exc
        except ValueError as exc:
            message = str(exc)
            if "ZoningRules is incomplete for layout compatibility:" in message:
                hinted = self._lookup_non_buildable_from_parcel_hint(parcel)
                if hinted is not None:
                    return hinted
                trailing = message.split(":", 1)[1] if ":" in message else ""
                missing_fields = [field.strip() for field in trailing.split(",") if field.strip()]
                raise PipelineStageError(
                    stage="zoning.lookup",
                    error="incomplete_zoning_rules",
                    message="Zoning rules are incomplete for layout execution",
                    status_code=422,
                    details={"district": parcel.jurisdiction or "unknown", "missing_fields": missing_fields},
                ) from exc
            raise PipelineStageError(
                stage="zoning.lookup",
                error="invalid_zoning_rules",
                message="Zoning rules are invalid for layout execution",
                status_code=422,
                details={"district": parcel.jurisdiction or "unknown", "violations": [message]},
            ) from exc

    def _lookup_non_buildable_from_parcel_hint(self, parcel: Parcel) -> Optional[ZoningStageResult]:
        district = (parcel.zoning_district or "").strip()
        if not district:
            return None

        bypass_reason = self._match_non_buildable_district(
            jurisdiction=parcel.jurisdiction,
            district=district,
        )
        if bypass_reason is None:
            return None

        zoning_rules = ZoningRules(
            parcel_id=parcel.parcel_id,
            jurisdiction=parcel.jurisdiction,
            district=district,
            metadata=EngineMetadata(
                source_engine="bedrock.pipeline_service",
                source_run_id="parcel_non_buildable_hint",
            ),
        )
        logger.info(
            "pipeline_non_buildable_parcel_hint_bypassed",
            extra={
                "parcel_id": parcel.parcel_id,
                "jurisdiction": parcel.jurisdiction,
                "district": district,
                "bypass_reason": bypass_reason,
            },
        )
        return ZoningStageResult(rules=zoning_rules, status="non_buildable", bypass_reason=bypass_reason)

    def _lookup_non_buildable_zoning_stage(self, parcel: Parcel) -> Optional[ZoningStageResult]:
        if not NON_BUILDABLE_DISTRICTS_ENABLED:
            return None

        parcel_geometry = shape(parcel.geometry)
        raw_rules = self.zoning_service._resolve_raw_rules(parcel, parcel_geometry)
        normalized_raw = self.zoning_service._normalize_raw_input(parcel_geometry, raw_rules)
        bypass_reason = self._match_non_buildable_district(
            jurisdiction=normalized_raw.get("jurisdiction"),
            district=normalized_raw.get("district"),
        )
        if bypass_reason is None:
            return None

        zoning_rules = normalize_zoning_rules(
            normalized_raw,
            parcel=parcel,
            jurisdiction=normalized_raw["jurisdiction"],
            district=normalized_raw["district"],
        )
        self._validate_real_overlay_zoning_source(parcel, zoning_rules)
        logger.info(
            "pipeline_non_buildable_zoning_bypassed",
            extra={
                "parcel_id": parcel.parcel_id,
                "jurisdiction": zoning_rules.jurisdiction,
                "district": zoning_rules.district,
                "zoning_source": zoning_rules.metadata.source_run_id if zoning_rules.metadata else "unknown",
                "bypass_reason": bypass_reason,
            },
        )
        return ZoningStageResult(rules=zoning_rules, status="non_buildable", bypass_reason=bypass_reason)

    def _build_unsupported_zoning_stage(self, parcel: Parcel, *, reason: str) -> ZoningStageResult:
        district = (parcel.zoning_district or "").strip() or "UNSUPPORTED"
        zoning_rules = ZoningRules(
            parcel_id=parcel.parcel_id,
            jurisdiction=parcel.jurisdiction,
            district=district,
            metadata=EngineMetadata(
                source_engine="bedrock.pipeline_service",
                source_run_id="unsupported_jurisdiction",
            ),
        )
        logger.info(
            "pipeline_unsupported_zoning_bypassed",
            extra={
                "parcel_id": parcel.parcel_id,
                "jurisdiction": parcel.jurisdiction,
                "district": district,
                "bypass_reason": reason,
            },
        )
        return ZoningStageResult(rules=zoning_rules, status="unsupported", bypass_reason=reason)

    @staticmethod
    def _match_non_buildable_district(*, jurisdiction: Optional[str], district: Optional[str]) -> Optional[str]:
        normalized_jurisdiction = (jurisdiction or "").strip().lower()
        normalized_district = (district or "").strip().upper()
        for candidate in NON_BUILDABLE_DISTRICTS:
            if normalized_jurisdiction == candidate["jurisdiction"] and normalized_district == candidate["district"].upper():
                return str(candidate["reason"])
        return None

    @staticmethod
    def _validate_real_overlay_zoning_source(parcel: Parcel, zoning_rules: ZoningRules) -> None:
        source = str(zoning_rules.metadata.source_run_id if zoning_rules.metadata is not None else "unknown")
        invalid_sources = {
            "jurisdiction_fallback",
            "safe_minimum_viable",
            "precomputed_district_index",
            "unknown",
        }
        if source in invalid_sources or "precomputed_district_index" in source:
            logger.error(
                "pipeline_zoning_source_rejected",
                extra={
                    "parcel_id": parcel.parcel_id,
                    "jurisdiction": parcel.jurisdiction,
                    "district": zoning_rules.district,
                    "zoning_source": source,
                },
            )
            raise PipelineStageError(
                stage="zoning.lookup",
                error="invalid_zoning_source",
                message="Pipeline zoning must use real overlay-backed district resolution",
                status_code=422,
                details={
                    "district": zoning_rules.district,
                    "zoning_source": source,
                    "geometry_match_success": False,
                },
            )

        logger.info(
            "pipeline_zoning_source_validated",
            extra={
                "parcel_id": parcel.parcel_id,
                "jurisdiction": parcel.jurisdiction,
                "district": zoning_rules.district,
                "zoning_source": source,
                "geometry_match_success": True,
            },
        )

    def _search_layout_stage(self, parcel: Parcel, zoning_rules, *, max_candidates: int):
        try:
            layout = search_subdivision_layout(parcel, zoning_rules, max_candidates=max_candidates)
            return validate_layout_result_output(layout)
        except LayoutSearchError as exc:
            raise PipelineStageError(
                stage="layout.search",
                error=exc.code,
                message=exc.message,
                status_code=422,
            ) from exc
        except RuntimeError as exc:
            raise PipelineStageError(
                stage="layout.search",
                error="layout_solver_failure",
                message=str(exc),
                status_code=500,
            ) from exc

    def _evaluate_feasibility_stage(
        self,
        parcel: Parcel,
        zoning_rules,
        layout_result,
        *,
        market_data: Optional[MarketData],
    ) -> FeasibilityResult:
        try:
            feasibility = self.feasibility_service.evaluate(
                parcel=parcel,
                layout=layout_result,
                market_data=market_data,
            )
            feasibility = validate_feasibility_result_output(feasibility)
            validate_feasibility_pipeline_contracts(
                parcel=parcel,
                zoning_rules=zoning_rules,
                layout_result=layout_result,
                feasibility_result=feasibility,
            )
            return feasibility
        except ValueError as exc:
            raise PipelineStageError(
                stage="feasibility.evaluate",
                error="invalid_feasibility_output",
                message=str(exc),
                status_code=500,
            ) from exc
        except RuntimeError as exc:
            raise PipelineStageError(
                stage="feasibility.evaluate",
                error="feasibility_evaluation_failure",
                message=str(exc),
                status_code=500,
            ) from exc
