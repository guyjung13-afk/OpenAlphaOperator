-- ETRM Modeling Fusion (directly from ETRM_Modeling_Spec.pdf)
CREATE OR REPLACE VIEW VW_ETRM_BURN_READY AS
SELECT 
  *,
  -- ETRM formulas from your PDF
  award_mmbtu * effective_heat_rate * (1 - ms_o_cutback_factor) AS projected_burn_mmbtu,
  CASE 
    WHEN actual_burn > award_mmbtu * 1.05 THEN 'OVERRUN_ALERT'
    WHEN actual_burn < award_mmbtu * 0.95 THEN 'UNDERRUN_ALERT'
    ELSE 'IN_COMPLIANCE' 
  END AS etrm_status,
  CURRENT_TIMESTAMP() AS last_propagated
FROM DT_PCI_ADJUSTED;

-- Consumer view (all dashboards point here)
CREATE OR REPLACE VIEW VW_FINAL_CONSUMERS AS SELECT * FROM VW_ETRM_BURN_READY;
