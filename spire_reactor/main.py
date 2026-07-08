#!/usr/bin/env python3
"""
Spire Reactor — Sovereign Gas Burn PCI + ETRM Ritual Engine

Entry point for docker compose and local ops.

Modes:
  api     (default CLI) FastAPI on :8000 — /health, /ritual/*
  worker  Redis pub/sub (Compose: --profile full)
  trigger one-shot CLI ritual for scripts / shift checks

Image default CMD is Streamlit; Compose overrides worker to --mode worker.

Operator rituals (dispatcher + stubs):
  - gas_burn_update / operator_update  (Hourly Burn Update)
  - morning_check
  - shift_handover_package
  - propagation_verify

All actions logged. Pure burn math is isolated for testability.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import redis
import uvicorn

load_dotenv()

# ── logging (structlog if installed, else stdlib) ────────────────────
try:
    import structlog

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
        ),
    )
    log = structlog.get_logger("spire_reactor")
    _USE_STRUCTLOG = True
except ImportError:  # pragma: no cover
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    log = logging.getLogger("spire_reactor")
    _USE_STRUCTLOG = False


def _info(event: str, **kwargs: Any) -> None:
    if _USE_STRUCTLOG:
        log.info(event, **kwargs)
    else:
        extra = " ".join(f"{k}={v!r}" for k, v in kwargs.items())
        log.info("%s %s", event, extra)


def _error(event: str, **kwargs: Any) -> None:
    if _USE_STRUCTLOG:
        log.error(event, **kwargs)
    else:
        extra = " ".join(f"{k}={v!r}" for k, v in kwargs.items())
        log.error("%s %s", event, extra)


# ── config ───────────────────────────────────────────────────────────
# Local default is localhost; Compose overrides REDIS_URL=redis://redis:6379
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
APP_ENV = os.getenv("APP_ENV", "local")

# Ritual type aliases → canonical names
_RITUAL_ALIASES = {
    "operator_update": "gas_burn_update",
    "hourly_burn_update": "gas_burn_update",
    "gas_burn_update": "gas_burn_update",
    "morning_check": "morning_check",
    "shift_handover_package": "shift_handover_package",
    "shift_handover": "shift_handover_package",
    "propagation_verify": "propagation_verify",
    "propagation_verification": "propagation_verify",
}


# ── pure domain math ─────────────────────────────────────────────────
def calculate_gas_burn(
    heat_rate: float,
    award_mw: float,
    actual_burn_mmbtu: float,
    prev_accum_mmbtu: float = 0.0,
    hours: float = 1.0,
    threshold_pct: float = 5.0,
) -> dict[str, Any]:
    """
    Deterministic gas burn model (whiteboard-aligned kernel).
    estimated = award_mw * heat_rate * hours
    variance vs actual → GREEN / AMBER / RED PCI band.
    """
    estimated_burn = award_mw * heat_rate * hours
    variance_pct = (
        ((actual_burn_mmbtu - estimated_burn) / estimated_burn * 100)
        if estimated_burn > 0
        else 0.0
    )
    new_accum = prev_accum_mmbtu + actual_burn_mmbtu

    if abs(variance_pct) <= threshold_pct:
        pci_status = "GREEN"
    elif abs(variance_pct) < 15:
        pci_status = "AMBER"
    else:
        pci_status = "RED"

    return {
        "estimated_burn_mmbtu": round(estimated_burn, 2),
        "variance_pct": round(variance_pct, 2),
        "new_accum_mmbtu": round(new_accum, 2),
        "pci_status": pci_status,
        "alert": pci_status != "GREEN",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def pci_band_to_etrm(pci_status: str) -> tuple[str, str]:
    """Map GREEN/AMBER/RED → public ETRM status + action (dashboard contract)."""
    if pci_status == "GREEN":
        return "COMPLIANT", "NONE"
    if pci_status == "AMBER":
        return "ALERT - REQUIRES RITUAL", "PROPAGATE_ALERT"
    return "ALERT - REQUIRES RITUAL", "PROPAGATE_CRITICAL"


# ── redis ────────────────────────────────────────────────────────────
def get_redis() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def publish_ritual_result(channel: str, payload: dict[str, Any]) -> None:
    """Best-effort Redis publish for live dashboard (no-op if Redis down)."""
    try:
        r = get_redis()
        r.publish(channel, json.dumps(payload))
        r.setex("latest_pci_etrm", 7200, json.dumps(payload))
    except Exception as exc:  # noqa: BLE001 — demo/local may lack Redis
        _info("redis_publish_skipped", error=str(exc), demo=DEMO_MODE)


# ── ritual dispatcher ────────────────────────────────────────────────
def trigger_ritual(
    ritual_type: str,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Core ritual dispatcher — local + deterministic for speed & sovereignty.
    Later: Temporal client for durable execution + compensation.
    """
    payload = dict(payload or {})
    canonical = _RITUAL_ALIASES.get(ritual_type, ritual_type)
    _info("ritual_triggered", ritual_type=canonical, raw=ritual_type, demo=DEMO_MODE)

    if canonical == "gas_burn_update":
        heat_rate = float(payload.get("heat_rate") or 7.5)
        # Prefer explicit award_mw; else derive loosely from award_mmbtu / heat_rate
        if "award_mw" in payload and payload["award_mw"] is not None:
            award_mw = float(payload["award_mw"])
        elif payload.get("award_mmbtu"):
            award_mw = float(payload["award_mmbtu"]) / heat_rate if heat_rate else 0.0
        else:
            award_mw = 500.0

        actual = float(
            payload.get("actual_burn_mmbtu")
            if payload.get("actual_burn_mmbtu") is not None
            else 3750.0
        )
        burn = calculate_gas_burn(
            heat_rate=heat_rate,
            award_mw=award_mw,
            actual_burn_mmbtu=actual,
            prev_accum_mmbtu=float(payload.get("prev_accum_mmbtu") or 0.0),
            hours=float(payload.get("hours") or 1.0),
            threshold_pct=float(payload.get("threshold_pct") or 5.0),
        )
        etrm_status, etrm_action = pci_band_to_etrm(burn["pci_status"])
        plant_id = str(payload.get("plant_id") or "DEMO-1")
        notes = str(payload.get("notes") or "")

        # Envelope: status=success|error; outcome=pipeline result for clients
        outcome = (
            "ALL_DOWNSTREAM_UPDATED"
            if burn["pci_status"] == "GREEN"
            else "RITUAL_QUEUED"
        )
        public = {
            "status": "success",
            "outcome": outcome,
            "pci": heat_rate,
            "pci_status": burn["pci_status"],
            "etrm_status": etrm_status,
            "etrm_action": etrm_action,
            "deviation_pct": burn["variance_pct"],
            "estimated_burn_mmbtu": burn["estimated_burn_mmbtu"],
            "new_accum_mmbtu": burn["new_accum_mmbtu"],
            "plant_id": plant_id,
            "notes": notes,
            "consumers": [
                "Power BI",
                "Provider Exports",
                "Compliance Ledger",
                "Redis cache:latest_pci_etrm",
            ],
            "ritual_at": burn["timestamp"],
            "mode": "stub" if DEMO_MODE else "live",
            "ritual": canonical,
            "result": burn,
        }
        # TODO Phase 3: Snowflake LANDING_OPERATOR_BURN_UPDATE (Data Vault)
        publish_ritual_result("ritual_results", public)
        return public

    if canonical == "morning_check":
        package = {
            "status": "success",
            "ritual": canonical,
            "check_id": f"MORNING-{int(time.time())}",
            "items": [
                "Redis reachable",
                "Last PCI ritual present",
                "Open alerts scan (stub)",
            ],
            "demo": DEMO_MODE,
            "ritual_at": datetime.now(timezone.utc).isoformat(),
        }
        return package

    if canonical == "shift_handover_package":
        # Phase 4: collect audit trail, open alerts, recommendations
        return {
            "status": "success",
            "package_id": f"HANDOVER-{int(time.time())}",
            "ritual": canonical,
            "demo": DEMO_MODE,
            "ritual_at": datetime.now(timezone.utc).isoformat(),
        }

    if canonical == "propagation_verify":
        return {
            "status": "success",
            "ritual": canonical,
            "consumers_ok": ["Power BI", "Provider Exports", "Compliance Ledger"],
            "demo": DEMO_MODE,
            "ritual_at": datetime.now(timezone.utc).isoformat(),
        }

    return {"status": "error", "message": f"Unknown ritual: {ritual_type}"}


# ── FastAPI (Docker health + HTTP rituals) ───────────────────────────
app = FastAPI(
    title="OpenAlphaOperator Spire Reactor",
    description="Sovereign Gas Burn PCI + ETRM ritual engine",
    version="0.2.0",
)


class OperatorUpdate(BaseModel):
    plant_id: str = "DEMO-1"
    heat_rate: float | None = Field(default=None, description="HR (mmbtu/MWh) used as PCI kernel input")
    award_mw: float | None = Field(default=None, gt=0)
    award_mmbtu: float | None = Field(default=None, gt=0)
    actual_burn_mmbtu: float = Field(ge=0)
    prev_accum_mmbtu: float = 0.0
    hours: float = Field(default=1.0, gt=0)
    threshold_pct: float = Field(default=5.0, ge=0)
    notes: str = ""


class RitualRequest(BaseModel):
    type: str = Field(description="Ritual type, e.g. gas_burn_update")
    payload: dict[str, Any] = Field(default_factory=dict)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "spire-reactor",
        "env": APP_ENV,
        "demo": DEMO_MODE,
        "redis_url": REDIS_URL,
    }


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "OpenAlphaOperator Spire Reactor",
        "docs": "/docs",
        "health": "/health",
        "ritual": "POST /ritual/operator-update",
        "dispatch": "POST /ritual",
        "ingest_snapshot": "GET /ingest/snapshot",
        "ingest_ritual": "POST /ingest/demo-ritual",
    }


@app.post("/ritual/operator-update")
def operator_update(body: OperatorUpdate) -> dict[str, Any]:
    payload = body.model_dump(exclude_none=True)
    # If only award_mmbtu given, gas_burn path derives award_mw
    result = trigger_ritual("gas_burn_update", payload)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@app.post("/ritual")
def ritual_dispatch(body: RitualRequest) -> dict[str, Any]:
    result = trigger_ritual(body.type, body.payload)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@app.get("/ingest/snapshot")
def ingest_snapshot(synthetic: bool = False) -> dict[str, Any]:
    """Pull public feeds (Open-Meteo ± EIA) or offline synthetic tick."""
    from spire_reactor.ingest.public_feeds import fetch_demo_snapshot, synthetic_tick

    if synthetic:
        return synthetic_tick()
    return fetch_demo_snapshot()


@app.post("/ingest/demo-ritual")
def ingest_demo_ritual(synthetic: bool = False) -> dict[str, Any]:
    """Fetch public/synthetic feeds and run gas_burn_update ritual."""
    from spire_reactor.ingest.public_feeds import fetch_demo_snapshot, synthetic_tick

    snap = synthetic_tick() if synthetic else fetch_demo_snapshot()
    payload = snap.get("operator_payload") or {}
    result = trigger_ritual("gas_burn_update", payload)
    return {"snapshot": snap, "ritual": result}


# ── Redis worker (pre-Temporal) ──────────────────────────────────────
def run_worker() -> None:
    """Subscribe to ritual_requests; JSON payloads only (never eval)."""
    _info("spire_reactor_worker_starting", redis=REDIS_URL, demo=DEMO_MODE)
    r = get_redis()
    pubsub = r.pubsub()
    pubsub.subscribe("ritual_requests")

    def shutdown_handler(signum: int, frame: Any) -> None:  # noqa: ARG001
        _info("graceful_shutdown_initiated", signal=signum)
        try:
            pubsub.close()
        finally:
            sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    while True:
        message = pubsub.get_message(timeout=1.0)
        if message and message.get("type") == "message":
            raw = message.get("data")
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(data, dict):
                    raise ValueError("ritual message must be a JSON object")
                trigger_ritual(str(data.get("type") or "gas_burn_update"), data.get("payload") or {})
            except Exception as exc:  # noqa: BLE001
                _error("ritual_failed", error=str(exc), raw=str(raw)[:200])
        time.sleep(0.05)


def run_api() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("REACTOR_PORT", os.getenv("PORT", "8000")))
    _info("starting_spire_reactor_api", host=host, port=port, demo=DEMO_MODE)
    uvicorn.run(
        "spire_reactor.main:app",
        host=host,
        port=port,
        reload=os.getenv("RELOAD", "0") == "1",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Spire Reactor — PCI + ETRM")
    parser.add_argument(
        "--mode",
        choices=["api", "worker", "trigger", "ingest"],
        default=os.getenv("SPIRE_MODE", "api"),
        help="api | worker | trigger | ingest (public feeds → ritual)",
    )
    parser.add_argument(
        "--ritual",
        default="gas_burn_update",
        help="Ritual type when --mode trigger",
    )
    parser.add_argument(
        "--payload",
        default="{}",
        help="JSON payload for --mode trigger",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="With --mode ingest: skip network, use offline synthetic tick",
    )
    parser.add_argument(
        "--no-ritual",
        action="store_true",
        help="With --mode ingest: print snapshot only (do not run ritual)",
    )
    args = parser.parse_args()

    if args.mode == "api":
        run_api()
    elif args.mode == "worker":
        run_worker()
    elif args.mode == "trigger":
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as exc:
            print(f"invalid --payload JSON: {exc}", file=sys.stderr)
            sys.exit(2)
        result = trigger_ritual(args.ritual, payload)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("status") == "success" else 1)
    elif args.mode == "ingest":
        from spire_reactor.ingest.public_feeds import fetch_demo_snapshot, synthetic_tick

        snap = synthetic_tick() if args.synthetic else fetch_demo_snapshot()
        if args.no_ritual:
            print(json.dumps(snap, indent=2))
            sys.exit(0)
        result = trigger_ritual("gas_burn_update", snap.get("operator_payload") or {})
        print(json.dumps({"snapshot": snap, "ritual": result}, indent=2))
        sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
