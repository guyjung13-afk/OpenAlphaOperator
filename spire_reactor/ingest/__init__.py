"""Demo/PoC data ingestion adapters (public APIs + synthetic plant mapping)."""

from spire_reactor.ingest.public_feeds import (
    build_operator_payload,
    fetch_demo_snapshot,
    fetch_open_meteo,
    fetch_eia_natural_gas_price,
)

__all__ = [
    "build_operator_payload",
    "fetch_demo_snapshot",
    "fetch_open_meteo",
    "fetch_eia_natural_gas_price",
]
