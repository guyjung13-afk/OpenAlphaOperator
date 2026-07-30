"""
Durable PCI + ETRM operator-update workflow.

Steps:
  1. compute_and_land_gas_burn — math + Snowflake landing + redis publish
  2. fuse_pci_etrm — fusion insight, redis, optional webhook / xAI
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn(name="PCI_ETRM_Operator_Update")
class PCIEtrmRitual:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload or {})

        public = await workflow.execute_activity(
            "compute_and_land_gas_burn",
            payload,
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        if not isinstance(public, dict):
            return {
                "status": "error",
                "message": "compute_and_land_gas_burn returned non-dict",
                "orchestrator": "temporal",
            }

        fusion = await workflow.execute_activity(
            "fuse_pci_etrm",
            public,
            start_to_close_timeout=timedelta(seconds=90),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )
        if not isinstance(fusion, dict):
            fusion = {"ok": False, "message": "fusion returned non-dict"}

        out = dict(public)
        out["fusion"] = fusion
        out["orchestrator"] = "temporal"
        out["status"] = public.get("status") or "success"
        # Prefer fusion outcome when present
        if fusion.get("outcome"):
            out["outcome"] = fusion["outcome"]
        if fusion.get("action"):
            out["etrm_action"] = fusion["action"]
        consumers = list(out.get("consumers") or [])
        for c in ("Temporal:PCI_ETRM_Operator_Update", "fusion:redis", "fusion:webhook"):
            if c not in consumers:
                consumers.append(c)
        out["consumers"] = consumers
        if out.get("mode") != "live" and (public.get("snowflake") or {}).get("ok"):
            out["mode"] = "live"
        if fusion.get("ok") and out.get("mode") == "stub":
            # Durable path ran even without SF land
            out["mode"] = out.get("mode") or "stub"
        return out
