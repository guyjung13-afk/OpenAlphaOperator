"""
Append operator gas_burn_update results to Snowflake landing (and optional staging).

Primary SoR: LANDING_OPERATOR_BURN_UPDATE
Optional dual-write: STAGING_GAS_BURN (feeds STREAM_BASE_INGEST when present)

Staging dual-write defaults OFF until gas_m3 unit mapping is real (SNOWFLAKE_STAGING_WRITE=true).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from spire_reactor.store.snowflake_client import (
    connection,
    fq_table,
    is_snowflake_configured,
    redact_secrets,
    snowflake_read_enabled,
    snowflake_settings,
    snowflake_write_enabled,
)

LANDING_TABLE = "LANDING_OPERATOR_BURN_UPDATE"
STAGING_TABLE = "STAGING_GAS_BURN"

_SECRET_KEYS = frozenset(
    {
        "password",
        "api_key",
        "token",
        "secret",
        "authorization",
        "private_key",
        "bearer_secret",
        "api_key_or_cert",
    }
)


def _parse_ritual_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            s = value.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _as_utc_naive(dt: datetime) -> datetime:
    """UTC wall time as naive datetime for TIMESTAMP_NTZ inserts."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _hour_floor(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.replace(minute=0, second=0, microsecond=0)


def _first_num(*candidates: Any, default: float = 0.0) -> float:
    for c in candidates:
        if c is None:
            continue
        try:
            return float(c)
        except (TypeError, ValueError):
            continue
    return default


def _row_from_ritual(
    public: dict[str, Any],
    payload: dict[str, Any],
    burn: dict[str, Any],
) -> dict[str, Any]:
    """
    Build a normalized landing row from ritual public envelope + inputs.

    Prefer fields already resolved by trigger_ritual (on ``public``):
    award_mw, actual_burn_mmbtu, heat_rate, hours — so SoR matches PCI math.
    """
    load_id = str(uuid.uuid4())
    heat_rate = _first_num(public.get("heat_rate"), payload.get("heat_rate"), public.get("pci"), default=7.5)
    hours = _first_num(public.get("hours"), payload.get("hours"), default=1.0)

    award_mw = _first_num(public.get("award_mw"), payload.get("award_mw"), default=0.0)
    if not award_mw and payload.get("award_mmbtu") and heat_rate:
        award_mw = float(payload["award_mmbtu"]) / heat_rate
    if not award_mw:
        # Mirror main.trigger_ritual default when award omitted
        award_mw = 500.0

    # Prefer public (threaded from ritual) — never substitute estimate for actual
    if public.get("actual_burn_mmbtu") is not None:
        actual = float(public["actual_burn_mmbtu"])
    elif payload.get("actual_burn_mmbtu") is not None:
        actual = float(payload["actual_burn_mmbtu"])
    else:
        actual = 3750.0  # same default as trigger_ritual

    if payload.get("award_mmbtu") is not None:
        award_mmbtu = float(payload["award_mmbtu"])
    elif public.get("award_mmbtu") is not None:
        award_mmbtu = float(public["award_mmbtu"])
    else:
        award_mmbtu = award_mw * heat_rate * hours

    ritual_at = _parse_ritual_at(public.get("ritual_at") or burn.get("timestamp"))

    safe_payload = {
        k: v for k, v in payload.items() if str(k).lower() not in _SECRET_KEYS
    }

    return {
        "load_id": load_id,
        "ritual_at": ritual_at,
        "plant_id": str(public.get("plant_id") or payload.get("plant_id") or "DEMO-1"),
        "heat_rate": heat_rate,
        "award_mw": award_mw,
        "award_mmbtu": award_mmbtu,
        "actual_burn_mmbtu": actual,
        "estimated_burn_mmbtu": float(
            public.get("estimated_burn_mmbtu")
            or burn.get("estimated_burn_mmbtu")
            or 0.0
        ),
        "variance_pct": float(public.get("deviation_pct") or burn.get("variance_pct") or 0.0),
        "new_accum_mmbtu": float(
            public.get("new_accum_mmbtu") or burn.get("new_accum_mmbtu") or 0.0
        ),
        "hours": hours,
        "pci_status": str(public.get("pci_status") or burn.get("pci_status") or ""),
        "etrm_status": str(public.get("etrm_status") or ""),
        "etrm_action": str(public.get("etrm_action") or ""),
        "outcome": str(public.get("outcome") or ""),
        "notes": str(public.get("notes") or payload.get("notes") or ""),
        "ritual_name": str(public.get("ritual") or "gas_burn_update"),
        "source_system": str(payload.get("source_system") or "spire_reactor"),
        "operator_id": str(payload.get("operator") or payload.get("operator_id") or ""),
        "raw_payload": {
            "payload": safe_payload,
            "result": burn,
            "public": {
                k: public.get(k)
                for k in (
                    "status",
                    "outcome",
                    "pci_status",
                    "etrm_status",
                    "etrm_action",
                    "deviation_pct",
                    "mode",
                    "ritual",
                    "award_mw",
                    "actual_burn_mmbtu",
                    "heat_rate",
                    "hours",
                )
            },
        },
    }


def _insert_landing(cur: Any, table: str, row: dict[str, Any]) -> None:
    sql = f"""
        INSERT INTO {table} (
            load_id, ritual_at, plant_id, heat_rate, award_mw, award_mmbtu,
            actual_burn_mmbtu, estimated_burn_mmbtu, variance_pct, new_accum_mmbtu,
            hours, pci_status, etrm_status, etrm_action, outcome, notes,
            ritual_name, source_system, operator_id, raw_payload
        )
        SELECT
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, PARSE_JSON(%s)
    """
    cur.execute(
        sql,
        (
            row["load_id"],
            _as_utc_naive(row["ritual_at"]),
            row["plant_id"],
            row["heat_rate"],
            row["award_mw"],
            row["award_mmbtu"],
            row["actual_burn_mmbtu"],
            row["estimated_burn_mmbtu"],
            row["variance_pct"],
            row["new_accum_mmbtu"],
            row["hours"],
            row["pci_status"],
            row["etrm_status"],
            row["etrm_action"],
            row["outcome"],
            row["notes"],
            row["ritual_name"],
            row["source_system"],
            row["operator_id"],
            json.dumps(row["raw_payload"], default=str),
        ),
    )


def _insert_staging(cur: Any, table: str, row: dict[str, Any]) -> None:
    """Map operator burn into STAGING_GAS_BURN columns expected by DT_PCI_ADJUSTED."""
    hour_ts = _as_utc_naive(_hour_floor(row["ritual_at"]))
    energy_mwh = row["award_mw"] * row["hours"]
    # gas_m3: energy proxy until SCADA units arrive (documented in SQL comments)
    gas_m3 = row["actual_burn_mmbtu"]
    variance = abs(float(row["variance_pct"] or 0.0))
    compliance = max(0.0, min(1.0, 1.0 - (variance / 100.0)))
    ms_o = 0.0

    sql = f"""
        INSERT INTO {table} (
            plant_id, hour_ts, energy_mwh, gas_m3, heat_rate_factor,
            etrm_compliance_ratio, award_mmbtu, effective_hr, ms_o_cutback,
            actual_burn, load_id
        )
        SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    """
    cur.execute(
        sql,
        (
            row["plant_id"],
            hour_ts,
            energy_mwh,
            gas_m3,
            row["heat_rate"],
            compliance,
            row["award_mmbtu"],
            row["heat_rate"],
            ms_o,
            row["actual_burn_mmbtu"],
            row["load_id"],
        ),
    )


def insert_operator_burn_update(
    public: dict[str, Any],
    payload: dict[str, Any],
    burn: dict[str, Any],
    *,
    creds: Optional[dict[str, dict[str, str]]] = None,
    dual_write_staging: Optional[bool] = None,
) -> dict[str, Any]:
    """
    Persist a gas_burn_update ritual to Snowflake.

    Returns a safe (no-secret) status dict for the ritual response:
      {ok, skipped, load_id, landing_table, staging_written, message, database, schema}
    """
    if dual_write_staging is None:
        # Default OFF until gas_m3 mapping is real; enable explicitly for DT path
        dual_write_staging = (
            os.getenv("SNOWFLAKE_STAGING_WRITE", "false").lower()
            in ("1", "true", "yes", "on")
        )

    if not snowflake_write_enabled(creds):
        configured = is_snowflake_configured(creds)
        if not configured:
            return {
                "ok": False,
                "skipped": True,
                "reason": "not_configured",
                "message": "Snowflake write skipped — credentials not configured",
            }
        return {
            "ok": False,
            "skipped": True,
            "reason": "demo_mode",
            "message": "Snowflake write skipped — demo_mode (set DEMO_MODE=false or SNOWFLAKE_WRITE=true)",
        }

    row = _row_from_ritual(public, payload, burn)
    settings = snowflake_settings(creds)
    try:
        landing_fq = fq_table(LANDING_TABLE, creds)
        staging_fq = fq_table(STAGING_TABLE, creds) if dual_write_staging else ""
    except ValueError as exc:
        return {
            "ok": False,
            "skipped": False,
            "load_id": row["load_id"],
            "message": f"Invalid Snowflake identifier: {exc}",
            "database": settings["database"],
            "schema": settings["schema"],
        }

    staging_written = False
    staging_error: Optional[str] = None

    try:
        with connection(creds) as conn:
            cur = conn.cursor()
            try:
                _insert_landing(cur, landing_fq, row)
                if dual_write_staging:
                    try:
                        _insert_staging(cur, staging_fq, row)
                        staging_written = True
                    except Exception as exc:  # noqa: BLE001 — landing is primary
                        staging_error = redact_secrets(str(exc), creds)
                conn.commit()
            finally:
                cur.close()
    except Exception as exc:  # noqa: BLE001
        msg = redact_secrets(str(exc), creds)
        return {
            "ok": False,
            "skipped": False,
            "load_id": row["load_id"],
            "landing_table": landing_fq,
            "staging_written": False,
            "message": f"Snowflake landing failed: {msg}",
            "database": settings["database"],
            "schema": settings["schema"],
        }

    out: dict[str, Any] = {
        "ok": True,
        "skipped": False,
        "load_id": row["load_id"],
        "landing_table": landing_fq,
        "staging_written": staging_written,
        "message": f"Landed load_id={row['load_id'][:8]}… → {LANDING_TABLE}",
        "database": settings["database"],
        "schema": settings["schema"],
    }
    if staging_error:
        out["staging_error"] = staging_error
        out["message"] += " (staging dual-write failed; landing OK)"
    return out


# ── reads (cockpit SoR path) ─────────────────────────────────────────

_READ_COLUMNS = (
    "load_id",
    "load_ts",
    "ritual_at",
    "plant_id",
    "heat_rate",
    "award_mw",
    "award_mmbtu",
    "actual_burn_mmbtu",
    "estimated_burn_mmbtu",
    "variance_pct",
    "new_accum_mmbtu",
    "hours",
    "pci_status",
    "etrm_status",
    "etrm_action",
    "outcome",
    "notes",
    "ritual_name",
    "source_system",
    "operator_id",
)


def _serialize_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    # Snowflake VARIANT / Decimal etc.
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:  # noqa: BLE001
            pass
    if type(value).__name__ == "Decimal":
        return float(value)
    return value


def fetch_recent_operator_burns(
    limit: int = 25,
    plant_id: Optional[str] = None,
    *,
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, Any]:
    """
    Read recent rows from LANDING_OPERATOR_BURN_UPDATE (system of record).

    Returns safe status:
      {ok, skipped, reason?, rows, count, table, message, database, schema}
    Never includes credentials. raw_payload VARIANT is omitted from default select.
    """
    limit = max(1, min(int(limit or 25), 200))
    plant_filter = (plant_id or "").strip() or None

    if not snowflake_read_enabled(creds):
        if not is_snowflake_configured(creds):
            return {
                "ok": False,
                "skipped": True,
                "reason": "not_configured",
                "rows": [],
                "count": 0,
                "message": "Snowflake read skipped — credentials not configured",
            }
        return {
            "ok": False,
            "skipped": True,
            "reason": "disabled",
            "rows": [],
            "count": 0,
            "message": "Snowflake read skipped — SNOWFLAKE_READ=false",
        }

    settings = snowflake_settings(creds)
    try:
        table = fq_table(LANDING_TABLE, creds)
    except ValueError as exc:
        return {
            "ok": False,
            "skipped": False,
            "rows": [],
            "count": 0,
            "message": f"Invalid Snowflake identifier: {exc}",
            "database": settings["database"],
            "schema": settings["schema"],
        }

    cols = ", ".join(_READ_COLUMNS)
    # ORDER BY load_ts then ritual_at for stable newest-first
    if plant_filter:
        sql = f"""
            SELECT {cols}
            FROM {table}
            WHERE plant_id = %s
            ORDER BY load_ts DESC NULLS LAST, ritual_at DESC NULLS LAST
            LIMIT %s
        """
        params: tuple[Any, ...] = (plant_filter, limit)
    else:
        sql = f"""
            SELECT {cols}
            FROM {table}
            ORDER BY load_ts DESC NULLS LAST, ritual_at DESC NULLS LAST
            LIMIT %s
        """
        params = (limit,)

    try:
        with connection(creds) as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, params)
                raw_rows = cur.fetchall()
                colnames = [d[0].lower() for d in (cur.description or [])]
            finally:
                cur.close()
    except Exception as exc:  # noqa: BLE001
        msg = redact_secrets(str(exc), creds)
        return {
            "ok": False,
            "skipped": False,
            "rows": [],
            "count": 0,
            "landing_table": table,
            "message": f"Snowflake read failed: {msg}",
            "database": settings["database"],
            "schema": settings["schema"],
        }

    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        item = {
            colnames[i] if i < len(colnames) else f"c{i}": _serialize_cell(raw[i])
            for i in range(len(raw))
        }
        rows.append(item)

    return {
        "ok": True,
        "skipped": False,
        "rows": rows,
        "count": len(rows),
        "landing_table": table,
        "message": f"Loaded {len(rows)} row(s) from {LANDING_TABLE}",
        "database": settings["database"],
        "schema": settings["schema"],
        "plant_id": plant_filter,
        "limit": limit,
    }


def fetch_latest_operator_burn(
    plant_id: Optional[str] = None,
    *,
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, Any]:
    """Convenience: single latest landing row (or empty)."""
    result = fetch_recent_operator_burns(limit=1, plant_id=plant_id, creds=creds)
    row = (result.get("rows") or [None])[0]
    result["row"] = row
    return result
