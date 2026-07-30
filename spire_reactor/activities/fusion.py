"""
Temporal activity: PCI + ETRM fusion / downstream propagation.

Steps (best-effort, never raises unless totally broken):
  1. Load latest SoR context (Snowflake landing when available)
  2. Rule-based fusion action from PCI/ETRM band
  3. Optional xAI insight text (skipped if no key)
  4. Redis cache latest_pci_etrm + publish fusion channel
  5. Optional downstream webhook
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from temporalio import activity


def _rule_fusion(public: dict[str, Any]) -> dict[str, Any]:
    """Deterministic fusion insight from ritual envelope (no LLM required)."""
    pci = str(public.get("pci_status") or "GREEN")
    etrm_status = str(public.get("etrm_status") or "COMPLIANT")
    etrm_action = str(public.get("etrm_action") or "NONE")
    variance = float(public.get("deviation_pct") or 0.0)

    if pci == "GREEN":
        action = "NONE"
        provider_msg = (
            f"PCI GREEN · variance {variance:+.2f}% · ETRM {etrm_status}. "
            "No propagation required; consumers remain on last compliant envelope."
        )
        severity = "info"
    elif pci == "AMBER":
        action = etrm_action if etrm_action != "NONE" else "PROPAGATE_ALERT"
        provider_msg = (
            f"PCI AMBER · variance {variance:+.2f}% · ETRM {etrm_status}. "
            "Queue consumer refresh and desk review within the hour."
        )
        severity = "warning"
    else:
        action = etrm_action if etrm_action != "NONE" else "PROPAGATE_CRITICAL"
        provider_msg = (
            f"PCI RED · variance {variance:+.2f}% · ETRM {etrm_status}. "
            "Immediate operator acknowledgment and downstream alert path."
        )
        severity = "critical"

    return {
        "action": action,
        "provider_msg": provider_msg,
        "severity": severity,
        "pci_status": pci,
        "etrm_status": etrm_status,
        "source": "rules",
    }


def _optional_xai_insight(public: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """Best-effort xAI chat completion; falls back to rule text on any failure."""
    try:
        from spire_reactor.config.integrations import get_credential

        api_key = get_credential("xai", "api_key") or (os.getenv("XAI_API_KEY") or "").strip()
    except Exception:  # noqa: BLE001
        api_key = (os.getenv("XAI_API_KEY") or "").strip()

    if not api_key:
        return {**base, "xai": {"ok": False, "skipped": True, "reason": "not_configured"}}

    prompt = (
        "You are a gas-plant commercial desk assistant. In 2 short sentences, "
        "summarize operator burn update for downstream consumers. No bid prices.\n"
        f"plant={public.get('plant_id')} pci={public.get('pci_status')} "
        f"etrm={public.get('etrm_status')} variance_pct={public.get('deviation_pct')} "
        f"award_mw={public.get('award_mw')} actual_burn={public.get('actual_burn_mmbtu')} "
        f"notes={public.get('notes')}"
    )
    try:
        import httpx

        resp = httpx.post(
            os.getenv("XAI_API_URL", "https://api.x.ai/v1/chat/completions"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": os.getenv("XAI_MODEL", "grok-2-latest"),
                "messages": [
                    {"role": "system", "content": "Be concise. Advisory only."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 120,
            },
            timeout=20.0,
        )
        if resp.status_code >= 400:
            return {
                **base,
                "xai": {
                    "ok": False,
                    "skipped": False,
                    "message": f"HTTP {resp.status_code}",
                },
            }
        data = resp.json()
        text = (
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        ).strip()
        if text:
            return {
                **base,
                "provider_msg": text,
                "source": "xai+rules",
                "xai": {"ok": True, "skipped": False},
            }
        return {**base, "xai": {"ok": False, "skipped": False, "message": "empty response"}}
    except Exception as exc:  # noqa: BLE001
        return {
            **base,
            "xai": {"ok": False, "skipped": False, "message": str(exc)[:200]},
        }


def _redis_publish(fusion_payload: dict[str, Any]) -> dict[str, Any]:
    try:
        import redis

        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        # Prefer Setup redis credential if present
        try:
            from spire_reactor.config.integrations import get_credential

            url = get_credential("redis", "url") or url
        except Exception:  # noqa: BLE001
            pass
        r = redis.from_url(url, decode_responses=True, socket_connect_timeout=3)
        body = json.dumps(fusion_payload, default=str)
        r.setex("latest_pci_etrm", 7200, body)
        r.publish("ritual_results", body)
        r.publish("fusion_results", body)
        return {"ok": True, "skipped": False, "message": "Redis updated"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "skipped": False, "message": str(exc)[:200]}


def _webhook_post(fusion_payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from spire_reactor.config.integrations import get_credential

        url = get_credential("webhooks", "url") or (os.getenv("WEBHOOK_URL") or "").strip()
        secret = get_credential("webhooks", "bearer_secret") or (
            os.getenv("WEBHOOK_SECRET") or ""
        ).strip()
    except Exception:  # noqa: BLE001
        url = (os.getenv("WEBHOOK_URL") or "").strip()
        secret = (os.getenv("WEBHOOK_SECRET") or "").strip()

    if not url:
        return {"ok": False, "skipped": True, "reason": "not_configured"}

    try:
        import httpx

        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        resp = httpx.post(url, json=fusion_payload, headers=headers, timeout=15.0)
        if resp.status_code >= 400:
            return {
                "ok": False,
                "skipped": False,
                "message": f"HTTP {resp.status_code}",
            }
        return {"ok": True, "skipped": False, "message": f"HTTP {resp.status_code}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "skipped": False, "message": str(exc)[:200]}


def _sor_context(public: dict[str, Any]) -> dict[str, Any]:
    """Optional latest landing row for the plant (read path)."""
    plant = str(public.get("plant_id") or "").strip() or None
    try:
        from spire_reactor.store.landing import fetch_latest_operator_burn

        latest = fetch_latest_operator_burn(plant_id=plant)
        if latest.get("ok") and latest.get("row"):
            return {
                "ok": True,
                "skipped": False,
                "row": latest["row"],
                "message": latest.get("message"),
            }
        return {
            "ok": False,
            "skipped": bool(latest.get("skipped")),
            "message": latest.get("message") or "no row",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "skipped": False, "message": str(exc)[:200]}


@activity.defn(name="fuse_pci_etrm")
async def fuse_pci_etrm(public: dict[str, Any]) -> dict[str, Any]:
    """
    Fuse operator update into downstream signals.

    Input: public ritual envelope from compute_and_land_gas_burn.
    """
    public = dict(public or {})
    activity.logger.info(
        "fuse_pci_etrm plant=%s pci=%s",
        public.get("plant_id"),
        public.get("pci_status"),
    )

    base = _rule_fusion(public)
    insight = _optional_xai_insight(public, base)
    sor = _sor_context(public)

    fusion_payload = {
        "event": "operator_update_fusion",
        "fused_at": datetime.now(timezone.utc).isoformat(),
        "plant_id": public.get("plant_id"),
        "pci_status": public.get("pci_status"),
        "etrm_status": public.get("etrm_status"),
        "etrm_action": insight.get("action") or public.get("etrm_action"),
        "deviation_pct": public.get("deviation_pct"),
        "estimated_burn_mmbtu": public.get("estimated_burn_mmbtu"),
        "actual_burn_mmbtu": public.get("actual_burn_mmbtu"),
        "award_mw": public.get("award_mw"),
        "provider_msg": insight.get("provider_msg"),
        "severity": insight.get("severity"),
        "source": insight.get("source"),
        "ritual_at": public.get("ritual_at"),
        "snowflake": public.get("snowflake"),
        "mode": public.get("mode"),
        "workflow_step": "fuse_pci_etrm",
    }

    redis_status = _redis_publish(fusion_payload)
    webhook_status = _webhook_post(fusion_payload)

    outcome = (
        "ALL_DOWNSTREAM_UPDATED"
        if str(public.get("pci_status")) == "GREEN"
        else "RITUAL_QUEUED"
    )

    result = {
        "ok": True,
        "pci": float(public.get("heat_rate") or public.get("pci") or 0.0),
        "pci_status": public.get("pci_status"),
        "action": insight.get("action"),
        "provider_msg": insight.get("provider_msg"),
        "severity": insight.get("severity"),
        "outcome": outcome,
        "redis": redis_status,
        "webhook": webhook_status,
        "xai": insight.get("xai"),
        "sor": {
            "ok": sor.get("ok"),
            "skipped": sor.get("skipped"),
            "message": sor.get("message"),
            # do not dump full row into every consumer — summary only
            "load_id": (sor.get("row") or {}).get("load_id") if sor.get("row") else None,
            "pci_status": (sor.get("row") or {}).get("pci_status") if sor.get("row") else None,
        },
        "fused_at": fusion_payload["fused_at"],
    }
    activity.logger.info(
        "fuse done action=%s redis_ok=%s webhook_skipped=%s",
        result.get("action"),
        (redis_status or {}).get("ok"),
        (webhook_status or {}).get("skipped"),
    )
    return result


# Sync helper for non-Temporal local fusion pass
def fuse_pci_etrm_sync(public: dict[str, Any]) -> dict[str, Any]:
    """Run fusion without Temporal activity context (local path optional)."""
    public = dict(public or {})
    base = _rule_fusion(public)
    insight = _optional_xai_insight(public, base)
    fusion_payload = {
        "event": "operator_update_fusion",
        "fused_at": datetime.now(timezone.utc).isoformat(),
        "plant_id": public.get("plant_id"),
        "pci_status": public.get("pci_status"),
        "etrm_status": public.get("etrm_status"),
        "etrm_action": insight.get("action") or public.get("etrm_action"),
        "deviation_pct": public.get("deviation_pct"),
        "provider_msg": insight.get("provider_msg"),
        "severity": insight.get("severity"),
        "source": insight.get("source"),
        "mode": public.get("mode"),
        "workflow_step": "fuse_pci_etrm_local",
    }
    redis_status = _redis_publish(fusion_payload)
    webhook_status = _webhook_post(fusion_payload)
    return {
        "ok": True,
        "pci": float(public.get("heat_rate") or public.get("pci") or 0.0),
        "action": insight.get("action"),
        "provider_msg": insight.get("provider_msg"),
        "severity": insight.get("severity"),
        "redis": redis_status,
        "webhook": webhook_status,
        "xai": insight.get("xai"),
        "fused_at": fusion_payload["fused_at"],
    }
