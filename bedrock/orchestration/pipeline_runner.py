"""Pipeline execution and telemetry tracking."""

from __future__ import annotations

import logging
import time
import inspect
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class EngineInteraction:
    pipeline_stage: str
    engine_called: str
    execution_time: float
    status: str
    validation_result: str
    stub_used: bool = False
    error: Optional[str] = None


@dataclass
class PipelineTelemetryRun:
    run_id: str
    pipeline_name: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str = "running"
    interactions: List[EngineInteraction] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


class PipelineRunner:
    """Execute pipelines with lightweight telemetry for Bedrock."""

    def __init__(self) -> None:
        self.runs: Dict[str, PipelineTelemetryRun] = {}

    def run_pipeline(
        self,
        pipeline_name: str,
        pipeline_fn: Callable[..., Any],
        inputs: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        run_id = str(uuid4())
        run = PipelineTelemetryRun(
            run_id=run_id,
            pipeline_name=pipeline_name,
            started_at=datetime.now(timezone.utc),
        )
        self.runs[run_id] = run
        logger.info("Starting pipeline run", extra={"run_id": run_id, "pipeline_name": pipeline_name})
        self._check_pipeline_health(pipeline_fn)

        started = time.perf_counter()
        try:
            call_kwargs = dict(inputs or {})
            call_kwargs.update(kwargs)
            signature = inspect.signature(pipeline_fn)
            if "runner" in signature.parameters:
                call_kwargs["runner"] = self
            if "run_id" in signature.parameters:
                call_kwargs["run_id"] = run_id
            result = pipeline_fn(**call_kwargs)
            run.status = "succeeded"
            return result
        except Exception:
            run.status = "failed"
            logger.exception("Pipeline run failed", extra={"run_id": run_id, "pipeline_name": pipeline_name})
            raise
        finally:
            finished = time.perf_counter()
            run.finished_at = datetime.now(timezone.utc)
            run.metrics["execution_time"] = finished - started
            logger.info(
                "Finished pipeline run",
                extra={
                    "run_id": run_id,
                    "pipeline_name": pipeline_name,
                    "status": run.status,
                    "execution_time": run.metrics["execution_time"],
                },
            )

    def log_engine_interaction(
        self,
        run_id: str,
        pipeline_stage: str,
        engine_called: str,
        execution_time: float,
        status: str,
        validation_result: str,
        stub_used: bool = False,
        error: Optional[str] = None,
    ) -> None:
        run = self.runs[run_id]
        run.interactions.append(
            EngineInteraction(
                pipeline_stage=pipeline_stage,
                engine_called=engine_called,
                execution_time=execution_time,
                status=status,
                validation_result=validation_result,
                stub_used=stub_used,
                error=error,
            )
        )

    def get_run(self, run_id: str) -> PipelineTelemetryRun:
        return self.runs[run_id]

    def _check_pipeline_health(self, pipeline_fn: Callable[..., Any]) -> None:
        pipeline_obj = getattr(pipeline_fn, "__self__", None)
        if pipeline_obj is None:
            return
        for engine in getattr(pipeline_obj, "required_engines", ()):
            if hasattr(engine, "health_check"):
                engine.health_check()

    @contextmanager
    def track_engine_call(
        self,
        run_id: str,
        pipeline_stage: str,
        engine_called: str,
    ) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            execution_time = time.perf_counter() - started
            self.log_engine_interaction(
                run_id,
                pipeline_stage,
                engine_called,
                execution_time,
                status="success",
                validation_result="unknown",
            )
