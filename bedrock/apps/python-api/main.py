"""Bedrock API entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI

BEDROCK_ROOT = Path(__file__).resolve().parents[2]
if str(BEDROCK_ROOT) not in sys.path:
    sys.path.append(str(BEDROCK_ROOT))

from api.decision_api import router as decision_router  # noqa: E402
from api.feasibility_api import router as feasibility_router  # noqa: E402
from api.evaluation_api import router as evaluation_router  # noqa: E402
from api.experiments_api import router as experiments_router  # noqa: E402
from api.layout_api import app as layout_app  # noqa: E402
from api.optimization_api import router as optimization_router  # noqa: E402
from api.parcel_api import create_router as create_parcel_router  # noqa: E402
from api.pipeline_api import router as pipeline_router  # noqa: E402
from api.runs_api import router as runs_router  # noqa: E402
from api.shortlist_api import router as shortlist_router  # noqa: E402
from api.zoning_api import router as zoning_router  # noqa: E402
from services.parcel_service import ParcelService  # noqa: E402


def create_app() -> FastAPI:
    app = FastAPI(title="Bedrock API", version="0.1.0")
    app.include_router(create_parcel_router(ParcelService()))
    app.include_router(zoning_router)
    app.include_router(layout_app.router)
    app.include_router(optimization_router)
    app.include_router(decision_router)
    app.include_router(feasibility_router)
    app.include_router(evaluation_router)
    app.include_router(pipeline_router)
    app.include_router(runs_router)
    app.include_router(shortlist_router)
    app.include_router(experiments_router)
    return app


app = create_app()
