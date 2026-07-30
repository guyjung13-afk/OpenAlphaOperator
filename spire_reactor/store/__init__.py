"""Store adapters: read-only lake ingest + optional landing writes."""

from spire_reactor.store.lake import (
    fetch_lake_gas_burn,
    fetch_latest_lake_gas_burn,
    list_lake_units,
    map_lake_row_to_desk,
    map_lake_row_to_ritual_payload,
    session_fields_from_desk,
    truth_envelopes_from_desk,
)
from spire_reactor.store.landing import (
    fetch_latest_operator_burn,
    fetch_recent_operator_burns,
    insert_operator_burn_update,
)
from spire_reactor.store.snowflake_client import (
    is_snowflake_configured,
    snowflake_read_enabled,
    snowflake_write_enabled,
)

__all__ = [
    "fetch_lake_gas_burn",
    "fetch_latest_lake_gas_burn",
    "fetch_latest_operator_burn",
    "fetch_recent_operator_burns",
    "insert_operator_burn_update",
    "is_snowflake_configured",
    "list_lake_units",
    "map_lake_row_to_desk",
    "map_lake_row_to_ritual_payload",
    "session_fields_from_desk",
    "snowflake_read_enabled",
    "snowflake_write_enabled",
    "truth_envelopes_from_desk",
]
