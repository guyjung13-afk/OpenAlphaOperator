-- PCI Dynamic Table (clean version matching PCI_Pipelines_v2.pdf)
CREATE OR REPLACE DYNAMIC TABLE DT_PCI_ADJUSTED
  TARGET_LAG = '5 seconds'
  WAREHOUSE = COMPUTE_WH
AS
SELECT 
  plant_id, 
  hour_ts,
  SUM(energy_mwh) / NULLIF(SUM(gas_m3 * heat_rate_factor), 0) AS pci_index,
  AVG(etrm_compliance_ratio) AS etrm_pct,
  etrm_calc_projected(award_mmbtu, effective_hr, ms_o_cutback) AS projected_burn
FROM STREAM_BASE_INGEST
GROUP BY plant_id, hour_ts;
