CREATE EXTENSION IF NOT EXISTS postgis;

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS jurisdictions (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_engine TEXT NOT NULL,
    name TEXT NOT NULL,
    state TEXT NOT NULL,
    county TEXT NOT NULL,
    planning_authority TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zoning_districts (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_engine TEXT NOT NULL,
    jurisdiction_id TEXT NOT NULL REFERENCES jurisdictions(id),
    code TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parcels (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_engine TEXT NOT NULL,
    parcel_id TEXT NOT NULL UNIQUE,
    geometry GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
    area DOUBLE PRECISION NOT NULL,
    jurisdiction_id TEXT REFERENCES jurisdictions(id),
    zoning_district_id TEXT REFERENCES zoning_districts(id),
    utilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    access_points JSONB NOT NULL DEFAULT '[]'::jsonb,
    topography JSONB NOT NULL DEFAULT '{}'::jsonb,
    existing_structures JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS development_standards (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_engine TEXT NOT NULL,
    district_id TEXT NOT NULL REFERENCES zoning_districts(id),
    standard_type TEXT NOT NULL,
    value JSONB NOT NULL,
    units TEXT,
    conditions JSONB NOT NULL DEFAULT '[]'::jsonb,
    citation TEXT
);

CREATE TABLE IF NOT EXISTS plan_documents (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_engine TEXT NOT NULL,
    external_document_id TEXT NOT NULL,
    title TEXT,
    file_uri TEXT,
    jurisdiction_id TEXT REFERENCES jurisdictions(id),
    extracted_geometry GEOMETRY(GEOMETRY, 4326)
);

CREATE TABLE IF NOT EXISTS layouts (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_engine TEXT NOT NULL,
    layout_id TEXT NOT NULL UNIQUE,
    parcel_id TEXT NOT NULL REFERENCES parcels(id),
    street_network JSONB NOT NULL DEFAULT '[]'::jsonb,
    lot_geometries JSONB NOT NULL DEFAULT '[]'::jsonb,
    lot_count INTEGER NOT NULL,
    open_space_area DOUBLE PRECISION NOT NULL,
    road_length DOUBLE PRECISION NOT NULL,
    utility_length DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS feasibility_scenarios (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_engine TEXT NOT NULL,
    scenario_id TEXT NOT NULL UNIQUE,
    parcel_id TEXT NOT NULL REFERENCES parcels(id),
    requested_units INTEGER NOT NULL,
    assumptions JSONB NOT NULL DEFAULT '{}'::jsonb,
    constraints JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS feasibility_results (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_engine TEXT NOT NULL,
    scenario_id TEXT NOT NULL REFERENCES feasibility_scenarios(scenario_id),
    parcel_id TEXT REFERENCES parcels(parcel_id),
    layout_id TEXT NOT NULL REFERENCES layouts(layout_id),
    units INTEGER NOT NULL,
    estimated_home_price DOUBLE PRECISION NOT NULL DEFAULT 0,
    construction_cost_per_home DOUBLE PRECISION NOT NULL DEFAULT 0,
    development_cost_total DOUBLE PRECISION NOT NULL DEFAULT 0,
    projected_revenue DOUBLE PRECISION NOT NULL DEFAULT 0,
    projected_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
    projected_profit DOUBLE PRECISION NOT NULL DEFAULT 0,
    ROI DOUBLE PRECISION,
    risk_score DOUBLE PRECISION NOT NULL,
    constraint_violations JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence DOUBLE PRECISION NOT NULL
);

-- Idempotent migration for legacy feasibility_results deployments.
ALTER TABLE feasibility_results ADD COLUMN IF NOT EXISTS parcel_id TEXT REFERENCES parcels(parcel_id);
ALTER TABLE feasibility_results ADD COLUMN IF NOT EXISTS units INTEGER;
ALTER TABLE feasibility_results ADD COLUMN IF NOT EXISTS estimated_home_price DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE feasibility_results ADD COLUMN IF NOT EXISTS construction_cost_per_home DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE feasibility_results ADD COLUMN IF NOT EXISTS development_cost_total DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE feasibility_results ADD COLUMN IF NOT EXISTS projected_revenue DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE feasibility_results ADD COLUMN IF NOT EXISTS projected_cost DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE feasibility_results ADD COLUMN IF NOT EXISTS projected_profit DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE feasibility_results ADD COLUMN IF NOT EXISTS ROI DOUBLE PRECISION;

UPDATE feasibility_results
SET units = COALESCE(units, max_units)
WHERE units IS NULL;

ALTER TABLE feasibility_results
    ALTER COLUMN units SET NOT NULL;

ALTER TABLE feasibility_results
    DROP COLUMN IF EXISTS max_units;

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_engine TEXT NOT NULL,
    evidence_id TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    document_id TEXT NOT NULL,
    section TEXT,
    page INTEGER,
    text TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_parcels_geometry ON parcels USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_plan_documents_geometry ON plan_documents USING GIST (extracted_geometry);
CREATE INDEX IF NOT EXISTS idx_parcels_parcel_id ON parcels (parcel_id);
CREATE INDEX IF NOT EXISTS idx_layouts_layout_id ON layouts (layout_id);
CREATE INDEX IF NOT EXISTS idx_feasibility_scenarios_scenario_id ON feasibility_scenarios (scenario_id);
CREATE INDEX IF NOT EXISTS idx_evidence_document_id ON evidence (document_id);

CREATE TRIGGER set_jurisdictions_updated_at
BEFORE UPDATE ON jurisdictions
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER set_zoning_districts_updated_at
BEFORE UPDATE ON zoning_districts
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER set_parcels_updated_at
BEFORE UPDATE ON parcels
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER set_development_standards_updated_at
BEFORE UPDATE ON development_standards
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER set_plan_documents_updated_at
BEFORE UPDATE ON plan_documents
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER set_layouts_updated_at
BEFORE UPDATE ON layouts
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER set_feasibility_scenarios_updated_at
BEFORE UPDATE ON feasibility_scenarios
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER set_feasibility_results_updated_at
BEFORE UPDATE ON feasibility_results
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER set_evidence_updated_at
BEFORE UPDATE ON evidence
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
