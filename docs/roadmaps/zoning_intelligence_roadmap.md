Zoning Intelligence Roadmap
# Zoning Intelligence Roadmap

This roadmap defines the system responsible for extracting, normalizing, and serving zoning rules for the Land Feasibility Platform.

Zoning intelligence converts parcel location into legally enforceable development constraints.

Without zoning intelligence, layout feasibility and deal feasibility cannot be computed.

This roadmap implements **Milestone 2 from the master roadmap**.

Master roadmap reference:

bedrock/docs/land_feasibility_roadmap.md

---

# Purpose

Provide a reliable system for determining the zoning constraints that apply to a parcel.

The zoning layer must produce structured rules that can be consumed by the layout engine and feasibility models.

The zoning system must support:

- zoning district identification
- rule extraction from municipal codes
- development standards normalization
- jurisdiction coverage expansion

---

# Scope

This roadmap includes:

- zoning district detection
- zoning rule extraction
- development standard normalization
- zoning APIs
- jurisdiction coverage expansion

This roadmap does NOT include:

- parcel ingestion
- layout optimization
- financial modeling

Those belong to other roadmaps.

---

# Role in the Feasibility Pipeline

Pipeline order:

parcel
→ zoning
→ layout
→ feasibility

Architecture position:

Parcel Service
      │
      ▼
Zoning Service
      │
      ▼
Layout Engine

The zoning service transforms parcel location into a structured development ruleset.

---

# Core Data Model

Normalized zoning rules object:


ZoningRules
district
min_lot_size_sqft
max_units_per_acre
setbacks
front
side
rear
max_height
max_lot_coverage


Additional fields may include:


DevelopmentStandards
road_width
sidewalk_requirements
open_space_ratio


---

# Milestone ZI-1 — Zoning District Identification

Goal

Determine which zoning district applies to a parcel.

Required capabilities

- jurisdiction detection
- zoning district lookup
- spatial overlay with zoning maps

API

POST /zoning/lookup

Input


{
"parcel_geometry": GeoJSON
}


Output


{
"jurisdiction": "...",
"zoning_district": "..."
}


Definition of Done

Given a parcel location, the system returns the correct zoning district.

Validation

Test dataset:

20 parcels across known jurisdictions.

Accuracy target:

≥95% district identification accuracy.

---

# Milestone ZI-2 — Development Rule Extraction

Goal

Extract zoning development rules from municipal sources.

Sources may include:

- municipal zoning codes
- development standards PDFs
- planning department documents

Required extracted rules

- minimum lot size
- setbacks
- density limits
- height limits
- lot coverage limits

Extraction pipeline

document
→ zoning text parsing
→ rule extraction
→ structured zoning rules

Definition of Done

For a given zoning district, the system returns a complete development rule set.

Validation

Compare extracted rules with official municipal documentation.

Accuracy target:

≥90% rule correctness.

---

# Milestone ZI-3 — Rule Normalization

Goal

Normalize extracted zoning rules into a consistent internal format.

Problem

Municipal zoning codes express rules inconsistently.

Examples


minimum lot size
minimum parcel area
lot area minimum


All must map to:


min_lot_size_sqft


Normalization tasks

- field mapping
- unit normalization
- constraint validation

Definition of Done

All zoning rules conform to the internal ZoningRules schema.

---

# Milestone ZI-4 — Zoning API Stabilization

Goal

Expose zoning intelligence through a stable API.

Endpoint

POST /zoning/lookup

Input


parcel geometry


Output


ZoningRules object


Example output


{
"district": "R-1",
"min_lot_size_sqft": 6000,
"max_units_per_acre": 5,
"setbacks": {
"front": 25,
"side": 8,
"rear": 20
}
}


Definition of Done

The zoning API consistently returns normalized zoning rules usable by the layout engine.

---

# Milestone ZI-5 — Jurisdiction Coverage Expansion

Goal

Expand zoning intelligence to multiple municipalities.

Initial coverage targets

- 3 municipalities
- 10 zoning districts
- complete rule extraction for each district

Coverage expansion strategy

- prioritize high-growth development markets
- expand by jurisdiction clusters

Definition of Done

The system supports at least 3 jurisdictions with reliable zoning rule extraction.

---

# Success Metrics

The zoning system is considered operational when:

- zoning districts correctly identified
- zoning rules extracted with ≥90% accuracy
- rules normalized into internal schema
- zoning API returns valid rules for layout engine consumption

---

# Agent Ownership

Primary Agent

zoning_agent

Supporting Agents

data_governance_agent  
orchestrator_agent  
evaluation_agent

---

# Dependencies

Parcel foundation roadmap must be completed before zoning intelligence can operate reliably.

Dependency reference:

bedrock/docs/roadmaps/parcel_foundation_roadmap.md

---

# Next Roadmap

After zoning intelligence is implemented, the next pipeline layer is:

layout_intelligence_roadmap.md
