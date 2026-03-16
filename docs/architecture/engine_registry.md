# Engine Registry

## bedrock

### Responsibility

- host canonical contracts
- expose platform APIs
- orchestrate parcel, zoning, layout, and feasibility stages

## GIS_lot_layout_optimizer

### Responsibility

- Retrieve parcel inputs for optimization workflows.
- Generate subdivision layouts from parcel and zoning constraints.

### Bedrock Integration

- Adapter: `engines/parcel_engine.py`
- Expected functions:
  - `get_parcel(parcel_id)`
  - `generate_layout(parcel, zoning_constraints)`

## model_lab

### Responsibility

- offline research
- training and evaluation
- experimental ranking and prior work

### Platform status

- research support only
- not part of the active production runtime boundary

## zoning_data_scraper

### Responsibility

- Retrieve parcel zoning context.
- Extract development standards and regulatory metadata.

### Bedrock Integration

- Adapter: `engines/zoning_engine.py`
- Expected functions:
  - `get_zoning(parcel)`
  - `get_development_standards(parcel)`

## Registry Rules

- Engines remain independent repositories.
- Bedrock must not duplicate engine logic.
- Engine changes must be mediated through stable adapter and contract updates.

Boundary note:

`takeoff_archive` is frozen legacy research code and is not part of the active Land Feasibility Platform.
