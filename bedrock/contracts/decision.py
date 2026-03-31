"""Canonical decision record contract for parcel acquisition decisions."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from .base import BedrockModel

DecisionRecommendation = Literal["acquire", "renegotiate_price", "pursue_rezoning", "abandon"]
UserAction = Literal["acquire", "pass", "hold", "renegotiate", "rezoning_in_progress"]
DecisionStatus = Literal["new", "in_review", "decided", "in_progress", "closed", "abandoned"]


class DecisionRecord(BedrockModel):
    """Tracks a user's acquisition decision for a parcel."""

    schema_name: str = Field(default="DecisionRecord", frozen=True)
    schema_version: str = Field(default="1.0.0", frozen=True)
    decision_id: str
    parcel_id: str
    optimization_run_id: Optional[str] = None
    pipeline_run_id: Optional[str] = None
    system_recommendation: Optional[DecisionRecommendation] = None
    user_action: Optional[UserAction] = None
    user_action_at: Optional[str] = None
    status: DecisionStatus = "new"
    target_price: Optional[float] = None
    notes: Optional[str] = None
    created_at: str
    updated_at: str
