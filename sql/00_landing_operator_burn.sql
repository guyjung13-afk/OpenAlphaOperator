-- OpenAlphaOperator — landing + staging tables (run first in Snowsight)
-- Database/schema: set via session context or qualify (default ALPHAGEN_ETRM.GOLD)
--
-- Ritual path writes LANDING_OPERATOR_BURN_UPDATE (full operator burn SoR).
-- Optional dual-write maps a row into STAGING_GAS_BURN for STREAM_BASE_INGEST / DT_PCI_ADJUSTED.

CREATE TABLE IF NOT EXISTS LANDING_OPERATOR_BURN_UPDATE (
  load_id               VARCHAR             DEFAULT UUID_STRING(),
  load_ts               TIMESTAMP_NTZ       DEFAULT CURRENT_TIMESTAMP(),
  ritual_at             TIMESTAMP_NTZ,
  plant_id              VARCHAR,
  heat_rate             FLOAT,
  award_mw              FLOAT,
  award_mmbtu           FLOAT,
  actual_burn_mmbtu     FLOAT,
  estimated_burn_mmbtu  FLOAT,
  variance_pct          FLOAT,
  new_accum_mmbtu       FLOAT,
  hours                 FLOAT,
  pci_status            VARCHAR,
  etrm_status           VARCHAR,
  etrm_action           VARCHAR,
  outcome               VARCHAR,
  notes                 VARCHAR,
  ritual_name           VARCHAR,
  source_system         VARCHAR,
  operator_id           VARCHAR,
  raw_payload           VARIANT
);

-- Staging table required by STREAM_BASE_INGEST (sql/01_base_ingestion_stream.sql)
CREATE TABLE IF NOT EXISTS STAGING_GAS_BURN (
  plant_id                VARCHAR,
  hour_ts                 TIMESTAMP_NTZ,
  energy_mwh              FLOAT,
  gas_m3                  FLOAT,   -- mapped from actual_burn_mmbtu (energy proxy until SCADA units land)
  heat_rate_factor        FLOAT,
  etrm_compliance_ratio   FLOAT,
  award_mmbtu             FLOAT,
  effective_hr            FLOAT,
  ms_o_cutback            FLOAT,
  actual_burn             FLOAT,
  load_id                 VARCHAR,
  load_ts                 TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

COMMENT ON TABLE LANDING_OPERATOR_BURN_UPDATE IS
  'Operator gas_burn_update ritual landing (append-only SoR). Written by spire_reactor.';

COMMENT ON TABLE STAGING_GAS_BURN IS
  'Mapped burn rows for STREAM_BASE_INGEST → DT_PCI_ADJUSTED. Dual-written from ritual when enabled.';
