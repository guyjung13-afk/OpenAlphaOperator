-- Auto-propagation task + alert (solves the core "everyone auto-adjusts" problem)
CREATE OR REPLACE TASK TASK_AUTO_PROPAGATE_PCI_ETRM
  WAREHOUSE = COMPUTE_WH
  SCHEDULE = '5 minutes'
  WHEN SYSTEM$STREAM_HAS_DATA('STREAM_BASE_INGEST')
AS
  BEGIN
    CALL SYSTEM$SEND_WEBHOOK('https://your-spire/reactor', 
      OBJECT_CONSTRUCT('event', 'operator_update', 'pci_delta', (SELECT pci_index FROM DT_PCI_ADJUSTED LIMIT 1)));
    
    -- Trigger downstream refresh
    ALTER DYNAMIC TABLE DT_PCI_ADJUSTED REFRESH;
    ALTER VIEW VW_ETRM_BURN_READY REFRESH;
  END;

-- Enable the task
ALTER TASK TASK_AUTO_PROPAGATE_PCI_ETRM RESUME;

-- Optional alert for you
CREATE ALERT ALERT_OPERATOR_CHANGE
  WAREHOUSE = COMPUTE_WH
  SCHEDULE = '1 minute'
  IF (SELECT COUNT(*) FROM STREAM_BASE_INGEST) > 0
  THEN CALL SYSTEM$NOTIFY('Operator updated data - propagation complete', 'Teams');
