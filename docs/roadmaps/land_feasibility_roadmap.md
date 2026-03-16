This document defines the canonical product roadmap for the Land Feasibility Platform. All Bedrock agents must reference this roadmap when planning or executing work.

# Land Feasibility Platform  
## North Star Execution Roadmap

## Core Business Outcome

The system must allow a developer to:

Input: parcel location or polygon  
Output: feasibility report in under 60 seconds

The report includes:

- maximum buildable units
- optimal subdivision layout
- estimated home price
- estimated construction cost
- projected project profit
- risk factors

If the system reliably produces this output, the product is commercially viable.

---

# North Star Architecture

The system architecture target:

User Interface
      │
      ▼
Feasibility API
      │
      ▼
Pipeline Orchestrator
      │
 ┌────┼─────────────┬───────────────┐
 ▼    ▼             ▼               ▼
Parcel  Zoning   Layout Engine   Market Data
Service Service      Service       Service
      │               │
      ▼               ▼
 Development Intelligence Database

---

# Milestone 1 — Real Parcel Inputs

Business Value  
The system can analyze real parcels rather than demo geometry.

Required Capabilities

Parcel ingestion
- parcel id
- geometry
- area
- location

Parcel normalization
- geometry validation
- projection normalization
- area calculation

Parcel API
POST /parcel/load  
GET /parcel/{id}

Definition of Done

The system accepts:

- parcel polygon  
or  
- parcel id

and produces:

- normalized geometry
- parcel area
- bounding box

Validation

50 parcels processed with zero geometry failures.

---

# Milestone 2 — Zoning Intelligence

Business Value  
The system understands legal build constraints.

Required Capabilities

Zoning extraction

- zoning district
- minimum lot size
- setbacks
- density limits

Rule normalization

ZoningRules model

Zoning API

POST /zoning/lookup

Definition of Done

Given a parcel location the system returns:

- zoning district
- min_lot_size
- setbacks
- max_units_per_acre

Validation

Compare zoning extraction with 10 known jurisdictions.

Accuracy target:  
90% rule correctness.

---

# Milestone 3 — Layout Feasibility

Business Value  
The system predicts how many homes can be built.

Pipeline

parcel geometry  
+ zoning rules  
→ layout search  
→ optimal subdivision

Layout Engine Components

- strategy generator
- candidate ranking
- graph generator
- subdivision solver
- layout scoring

Layout Service API

POST /layout/search

Output

- units
- lot geometries
- road network
- layout score

Definition of Done

For a given parcel the system:

- generates a subdivision layout
- returns a unit count

Performance targets

- runtime under 60 seconds
- constraint compliance above 90%

Validation dataset

20 real parcels.

---

# Milestone 4 — Economic Feasibility

Business Value  
The system determines whether a land deal is profitable.

Required Capabilities

Revenue Model

- home size
- price per sqft
- market comps

Construction Cost Model

- build cost per sqft
- development cost

Financial Model

revenue = units × home price  
cost = construction + development + land  
profit = revenue − cost

Feasibility API

POST /feasibility/evaluate

Output

- project profit
- ROI
- risk score

Definition of Done

The feasibility report includes:

- unit count
- expected home price
- expected build cost
- projected profit

Validation

Compare outputs with 5 historical development deals.

Accuracy target: ±15%.

---

# Milestone 5 — Acquisition Intelligence

Business Value  
The system becomes a land acquisition decision tool.

Required Capabilities

Parcel Search

- zoning filters
- size filters
- geography filters

Batch Feasibility

Evaluate 100+ parcels automatically.

Deal Ranking

- ROI score
- risk score

Deal Database

Store:

- parcel data
- layouts
- financial models

Definition of Done

A user can:

- search parcels
- run feasibility analysis
- rank the best opportunities

Performance target

Evaluate 100 parcels in under 30 minutes.

---

# True MVP

The minimum sellable product is achieved when the pipeline works end-to-end:

parcel  
→ zoning  
→ layout  
→ feasibility

Output:

a development feasibility report.
