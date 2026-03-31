"""FastAPI router for decision record persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from bedrock.contracts.base import BedrockModel
from bedrock.contracts.decision import DecisionRecord
from bedrock.services.decision_store import DecisionStore

router = APIRouter(prefix="/decisions", tags=["decisions"])
store = DecisionStore()


class CreateDecisionRequest(BedrockModel):
    parcel_id: str
    optimization_run_id: Optional[str] = None
    pipeline_run_id: Optional[str] = None
    system_recommendation: Optional[str] = None
    user_action: Optional[str] = None
    target_price: Optional[float] = None
    notes: Optional[str] = None


class UpdateDecisionRequest(BedrockModel):
    user_action: Optional[str] = None
    status: Optional[str] = None
    target_price: Optional[float] = None
    notes: Optional[str] = None


@router.post("", response_model=DecisionRecord)
def create_decision(request: CreateDecisionRequest) -> DecisionRecord:
    now = datetime.now(timezone.utc).isoformat()
    record = DecisionRecord(
        decision_id=str(uuid4()),
        parcel_id=request.parcel_id,
        optimization_run_id=request.optimization_run_id,
        pipeline_run_id=request.pipeline_run_id,
        system_recommendation=request.system_recommendation,
        user_action=request.user_action,
        user_action_at=now if request.user_action else None,
        status="decided" if request.user_action else "new",
        target_price=request.target_price,
        notes=request.notes,
        created_at=now,
        updated_at=now,
    )
    store.save(record)
    return record


@router.get("", response_model=list[DecisionRecord])
def list_decisions(
    parcel_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
) -> list[DecisionRecord]:
    return store.list_decisions(parcel_id=parcel_id, status=status, limit=limit, offset=offset)


@router.get("/{decision_id}", response_model=DecisionRecord)
def get_decision(decision_id: str) -> DecisionRecord:
    try:
        return store.load(decision_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "decision_not_found"}) from exc


@router.patch("/{decision_id}", response_model=DecisionRecord)
def update_decision(decision_id: str, request: UpdateDecisionRequest) -> DecisionRecord:
    try:
        now = datetime.now(timezone.utc).isoformat()
        fields: dict = {"updated_at": now}
        if request.user_action is not None:
            fields["user_action"] = request.user_action
            fields["user_action_at"] = now
        if request.status is not None:
            fields["status"] = request.status
        if request.target_price is not None:
            fields["target_price"] = request.target_price
        if request.notes is not None:
            fields["notes"] = request.notes
        return store.update(decision_id, **fields)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "decision_not_found"}) from exc
