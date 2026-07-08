#!/bin/bash
snowsql -q "ALTER VIEW OLD_BURN_VIEW RENAME TO OLD_BURN_VIEW_LEGACY;"
snowsql -q "CREATE VIEW OLD_BURN_VIEW AS SELECT * FROM VW_ETRM_CONSUMERS;"
echo "All consumers now point to fresh dynamic data ✅"
