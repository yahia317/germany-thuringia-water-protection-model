
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS iris_staging;

CREATE TABLE IF NOT EXISTS iris_staging.water_protection_zones (
    source_zone_id DOUBLE PRECISION NOT NULL,
    state_code VARCHAR(2) NOT NULL,
    zone_code TEXT,
    protection_type TEXT,
    designation_status TEXT,
    restriction_category TEXT,
    extraction_rate DOUBLE PRECISION,
    source_url TEXT,
    source_updated_at DATE,
    geom geometry(Geometry, 25832) NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT water_protection_zones_pk
        PRIMARY KEY (source_zone_id, state_code)
);

CREATE INDEX IF NOT EXISTS water_protection_zones_geom_idx
    ON iris_staging.water_protection_zones
    USING GIST (geom);
