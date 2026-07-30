"""
Temporal worker process — polls task queue and runs workflows + activities.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from spire_reactor.temporal.settings import temporal_settings

log = logging.getLogger("spire_reactor.temporal.worker")


async def run_temporal_worker(
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> None:
    from temporalio.worker import Worker

    from spire_reactor.activities.burn import compute_and_land_gas_burn
    from spire_reactor.activities.fusion import fuse_pci_etrm
    from spire_reactor.temporal.client import connect_client
    from spire_reactor.workflows.pci_etrm_ritual import PCIEtrmRitual

    s = temporal_settings(creds)
    if not s["host"]:
        raise SystemExit(
            "Temporal worker requires TEMPORAL_HOST (or Setup temporal.host)"
        )

    client = await connect_client(creds)
    worker = Worker(
        client,
        task_queue=s["task_queue"],
        workflows=[PCIEtrmRitual],
        activities=[compute_and_land_gas_burn, fuse_pci_etrm],
    )
    log.info(
        "Temporal worker starting host=%s namespace=%s queue=%s",
        s["host"],
        s["namespace"],
        s["task_queue"],
    )
    await worker.run()


def run_temporal_worker_sync(
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> None:
    asyncio.run(run_temporal_worker(creds))
