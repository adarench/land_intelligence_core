"""Compatibility facade for zoning contracts."""

from __future__ import annotations

from typing import Optional

from .base import BedrockModel, EngineMetadata
from .zoning_rules import DevelopmentStandard, ZoningDistrict, ZoningRules


class Jurisdiction(BedrockModel):
    id: str
    name: str
    state: str
    county: str
    planning_authority: str
    metadata: Optional[EngineMetadata] = None


__all__ = ["DevelopmentStandard", "Jurisdiction", "ZoningDistrict", "ZoningRules"]
