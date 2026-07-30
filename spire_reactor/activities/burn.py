"""
Temporal activity: compute gas burn PCI/ETRM and land to Snowflake SoR.
"""

from __future__ import annotations

from typing import Any

from temporalio import activity


@activity.defn(name="compute_and_land_gas_burn")
async def compute_and_land_gas_burn(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministic burn math + optional Snowflake landing + Redis publish.

    Delegates to spire_reactor.main.execute_gas_burn_local so local CLI and
    Temporal share one implementation (no Temporal re-entry).
    """
    activity.logger.info(
        "compute_and_land_gas_burn plant=%s",
        (payload or {}).get("plant_id"),
    )
    from spire_reactor.main import execute_gas_burn_local

    result = execute_gas_burn_local(dict(payload or {}), via_temporal=True)
    activity.logger.info(
        "compute_and_land done mode=%s pci=%s snowflake_ok=%s",
        result.get("mode"),
        result.get("pci_status"),
        (result.get("snowflake") or {}).get("ok"),
    )
    return result
