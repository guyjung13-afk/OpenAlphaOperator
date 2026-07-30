"""
Temporal client helpers: connect and run PCI_ETRM_Operator_Update workflows.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

from spire_reactor.temporal.settings import (
    connect_kwargs,
    temporal_settings,
)

WORKFLOW_NAME = "PCI_ETRM_Operator_Update"


async def connect_client(creds: Optional[dict[str, dict[str, str]]] = None) -> Any:
    from temporalio.client import Client

    kwargs = connect_kwargs(creds)
    host = kwargs.pop("target_host")
    return await Client.connect(host, **kwargs)


async def execute_pci_etrm_workflow(
    payload: dict[str, Any],
    *,
    creds: Optional[dict[str, dict[str, str]]] = None,
    workflow_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Start PCI_ETRM_Operator_Update and wait for result.

    Returns the workflow result dict (same shape as local ritual envelope + fusion).
    """
    from spire_reactor.workflows.pci_etrm_ritual import PCIEtrmRitual

    s = temporal_settings(creds)
    client = await connect_client(creds)
    plant = str((payload or {}).get("plant_id") or "plant")
    wid = workflow_id or f"pci-etrm-{plant}-{uuid.uuid4().hex[:12]}"
    handle = await client.start_workflow(
        PCIEtrmRitual.run,
        dict(payload or {}),
        id=wid,
        task_queue=s["task_queue"],
    )
    result = await handle.result()
    if not isinstance(result, dict):
        return {
            "status": "error",
            "message": f"Unexpected workflow result type: {type(result)}",
            "workflow_id": wid,
        }
    out = dict(result)
    out.setdefault("workflow_id", wid)
    out.setdefault("orchestrator", "temporal")
    out.setdefault("task_queue", s["task_queue"])
    return out


def execute_pci_etrm_workflow_sync(
    payload: dict[str, Any],
    *,
    creds: Optional[dict[str, dict[str, str]]] = None,
    workflow_id: Optional[str] = None,
) -> dict[str, Any]:
    """Sync wrapper for FastAPI / Streamlit / CLI (runs its own event loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            execute_pci_etrm_workflow(payload, creds=creds, workflow_id=workflow_id)
        )
    # Already inside an event loop (rare for Streamlit) — use a new thread loop
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(
            lambda: asyncio.run(
                execute_pci_etrm_workflow(
                    payload, creds=creds, workflow_id=workflow_id
                )
            )
        )
        return fut.result(timeout=float(
            __import__("os").getenv("TEMPORAL_WORKFLOW_TIMEOUT", "180")
        ))
