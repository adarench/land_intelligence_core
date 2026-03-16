Parcel Foundation Roadmap
# Parcel Foundation Roadmap

This roadmap defines the system required to reliably ingest, normalize, and serve parcel data for the Land Feasibility Platform.

Parcel data is the **first dependency** for the entire feasibility pipeline.

All downstream services depend on this layer:

- zoning intelligence
- layout feasibility
- economic feasibility
- acquisition intelligence

This roadmap implements **Milestone 1 from the master roadmap**.

Master roadmap reference:

bedrock/docs/land_feasibility_roadmap.md

---

# Purpose

Provide a stable parcel data layer that allows the system to accept real parcels and transform them into normalized geometry suitable for zoning analysis and subdivision layout simulation.

The parcel foundation must support:

- parcel ingestion
- geometry validation
- coordinate normalization
- parcel metadata extraction
- parcel caching and retrieval

---

# Scope

This roadmap includes:

- parcel ingestion pipelines
- parcel geometry normalization
- parcel metadata extraction
- parcel API service
- parcel storage and caching

This roadmap does NOT include:

- zoning rules
- layout optimization
- financial modeling

Those belong to other roadmaps.

---

# System Role in Architecture

Parcel Service sits at the start of the pipeline.

Pipeline order:

parcel
→ zoning
→ layout
→ feasibility

Architecture position:

User Interface
      │
      ▼
Feasibility API
      │
      ▼
Parcel Service
      │
      ▼
Zoning Service
      │
      ▼
Layout Engine

---

# Core Data Model

Normalized parcel object:


Parcel
parcel_id
geometry
area_sqft
centroid
bounding_box
jurisdiction


Geometry format:

GeoJSON Polygon

Projection:

EPSG:4326 or normalized internal coordinate system.

---

# Milestone PF-1 — Parcel Geometry Ingestion

Goal

Accept parcel geometries from external sources.

Sources may include:

- county GIS APIs
- shapefiles
- GeoJSON uploads
- manual polygon input

Required capabilities

- accept polygon input
- validate polygon topology
- repair simple geometry errors
- compute parcel area

API

POST /parcel/load

Input


{
"parcel_id": "...",
"geometry": GeoJSON
}


Output


normalized parcel geometry
parcel area
bounding box


Definition of Done

- system accepts parcel geometry
- geometry validated and normalized
- area computed correctly
- invalid polygons rejected

Validation

Test dataset:

50 parcels

Success criteria:

0 geometry failures

---

# Milestone PF-2 — Parcel Metadata Extraction

Goal

Extract useful spatial metadata from parcel geometry.

Required metadata

- parcel area
- centroid
- bounding box
- jurisdiction inference

Output model


ParcelMetadata
area_sqft
centroid
bounding_box
jurisdiction


Definition of Done

Every parcel object returned by the API includes:

- computed area
- centroid coordinates
- bounding box

Validation

Compare calculated parcel areas with known parcel areas.

Accuracy target:

±2%

---

# Milestone PF-3 — Parcel Storage and Retrieval

Goal

Enable persistent parcel storage and lookup.

Required capabilities

- parcel database
- parcel lookup by ID
- caching for previously analyzed parcels

API

GET /parcel/{parcel_id}

Output

Normalized parcel object.

Definition of Done

Previously loaded parcels can be retrieved instantly without recomputation.

Validation

Load and retrieve 100 parcels.

Average lookup latency:

<100ms

---

# Milestone PF-4 — Parcel Normalization for Layout Engine

Goal

Ensure parcel geometry is usable by the layout engine.

Normalization tasks

- coordinate system normalization
- polygon simplification if needed
- removal of duplicate vertices
- consistent winding order

Output

Geometry compatible with layout solver.

Definition of Done

All parcels used in layout experiments successfully run through layout search without geometry errors.

Validation

20 parcels run through layout engine with zero geometry failures.

---

# Milestone PF-5 — Parcel API Stabilization

Goal

Provide a stable interface for the rest of the platform.

Required endpoints

POST /parcel/load

GET /parcel/{id}

Output contract


Parcel
parcel_id
geometry
area_sqft
centroid
bounding_box
jurisdiction


Definition of Done

The parcel API is stable and usable by:

- zoning service
- layout service
- feasibility service

---

# Success Metrics

The parcel layer is considered complete when:

- 100 parcels can be ingested
- all parcels normalized successfully
- parcel metadata computed correctly
- parcel retrieval latency <100ms

---

# Agent Ownership

Primary Agent

parcel_agent

Supporting Agents

data_governance_agent
orchestrator_agent

---

# Dependencies

None.

Parcel ingestion is the first layer of the pipeline.

---

# Next Roadmap

After parcel foundation is complete, the system advances to:

zoning_intelligence_roadmap.md
