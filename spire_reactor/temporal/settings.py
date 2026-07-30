"""
Temporal connection settings from Setup secrets / env.

Keys (integrations catalog id=temporal):
  host, namespace, api_key
Optional env:
  TEMPORAL_TASK_QUEUE (default spire-reactor)
  TEMPORAL_USE=true|false  force enable/disable dispatch
  TEMPORAL_TLS=true|false  (default true when api_key set)
"""

from __future__ import annotations

import os
from typing import Any, Optional


def temporal_settings(
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, str]:
    if creds is None:
        from spire_reactor.config.integrations import load_credentials

        creds = load_credentials()
    t = dict(creds.get("temporal") or {})
    host = (t.get("host") or os.getenv("TEMPORAL_HOST") or "").strip()
    namespace = (
        t.get("namespace") or os.getenv("TEMPORAL_NAMESPACE") or "default"
    ).strip() or "default"
    api_key = (t.get("api_key") or os.getenv("TEMPORAL_API_KEY") or "").strip()
    task_queue = (os.getenv("TEMPORAL_TASK_QUEUE") or "spire-reactor").strip() or "spire-reactor"
    return {
        "host": host,
        "namespace": namespace,
        "api_key": api_key,
        "task_queue": task_queue,
    }


def is_temporal_configured(
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> bool:
    s = temporal_settings(creds)
    host = s["host"]
    if not host:
        return False
    if host.lower() in ("your_host", "placeholder", "localhost:0000"):
        return False
    return True


def temporal_dispatch_enabled(
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> bool:
    """
    Whether trigger_ritual should start a Temporal workflow for gas_burn_update.

    - Requires TEMPORAL_HOST (Setup/env).
    - TEMPORAL_USE=false always disables.
    - TEMPORAL_USE=true forces when configured.
    - Otherwise: enabled when not demo_mode (same gate as Snowflake writes).
    """
    force = (os.getenv("TEMPORAL_USE") or "").strip().lower()
    if force in ("0", "false", "no", "off"):
        return False
    if not is_temporal_configured(creds):
        return False
    if force in ("1", "true", "yes", "on"):
        return True
    from spire_reactor.config.integrations import is_demo_mode

    return not is_demo_mode(creds)


def temporal_tls_enabled(settings: Optional[dict[str, str]] = None) -> bool:
    """TLS default: on when api_key present (Temporal Cloud); off for bare localhost."""
    explicit = (os.getenv("TEMPORAL_TLS") or "").strip().lower()
    if explicit in ("1", "true", "yes", "on"):
        return True
    if explicit in ("0", "false", "no", "off"):
        return False
    s = settings or temporal_settings()
    if s.get("api_key"):
        return True
    host = (s.get("host") or "").lower()
    if host.startswith("localhost") or host.startswith("127.0.0.1"):
        return False
    # Cloud-style hosts usually need TLS
    if ".tmprl.cloud" in host or "temporal.io" in host:
        return True
    return False


def connect_kwargs(creds: Optional[dict[str, dict[str, str]]] = None) -> dict[str, Any]:
    """Args for temporalio.client.Client.connect (excluding event loop)."""
    s = temporal_settings(creds)
    if not s["host"]:
        raise ValueError("TEMPORAL_HOST / temporal.host not configured")
    kwargs: dict[str, Any] = {
        "target_host": s["host"],
        "namespace": s["namespace"],
    }
    if s["api_key"]:
        kwargs["api_key"] = s["api_key"]
    if temporal_tls_enabled(s):
        kwargs["tls"] = True
    return kwargs
