"""
Read-only Snowflake data-lake ingest for the Commercial Truth Cockpit.

This desk does **not** write to Snowflake. It ingests existing lake objects
(default: V_CALCULATED_GAS_BURN) for live commercial truth context.

Override source with env:
  SNOWFLAKE_LAKE_SOURCE=V_CALCULATED_GAS_BURN
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from typing import Any, Optional

from spire_reactor.store.snowflake_client import (
    connection,
    fq_table,
    is_snowflake_configured,
    redact_secrets,
    snowflake_read_enabled,
    snowflake_settings,
)

# Safe simple identifiers only (no schema injection)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

DEFAULT_LAKE_SOURCE = "V_CALCULATED_GAS_BURN"

# Columns we project when present (source may vary slightly)
_PREFERRED_COLS = (
    "UNIT_NAME",
    "FLEET_NAME",
    "PIPELINE",
    "OPERATING_DATE",
    "HE",
    "GAS_DAY_DATE",
    "DAM_MW",
    "RT_MW",
    "HEAT_RATE",
    "HEAT_RATE_CONFIG",
    "DA_BURN_MMBTU",
    "RT_BURN_MMBTU",
    "BURN_VARIANCE_MMBTU",
    "NET_REVENUE",
    "DAM_LMP",
    "RT_LMP",
    "CONFIG_MW",
    "ECO_MAX_RT_MW",
)


def lake_source_table() -> str:
    """Configured lake object name (table/view) within session DB.SCHEMA."""
    name = (os.getenv("SNOWFLAKE_LAKE_SOURCE") or DEFAULT_LAKE_SOURCE).strip()
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid SNOWFLAKE_LAKE_SOURCE identifier: {name!r}")
    return name


def _serialize_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:  # noqa: BLE001
            pass
    if type(value).__name__ == "Decimal":
        return float(value)
    return value


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def variance_pct_from_lake(row: dict[str, Any]) -> float:
    """
    Prefer explicit variance vs DA burn when available.
    BURN_VARIANCE_MMBTU / DA_BURN_MMBTU * 100.
    """
    da = _as_float(row.get("da_burn_mmbtu") or row.get("DA_BURN_MMBTU"), 0.0)
    var = row.get("burn_variance_mmbtu")
    if var is None:
        var = row.get("BURN_VARIANCE_MMBTU")
    if var is not None and abs(da) > 1e-9:
        return (_as_float(var) / da) * 100.0
    # fallback: RT vs DA
    rt = _as_float(row.get("rt_burn_mmbtu") or row.get("RT_BURN_MMBTU"), 0.0)
    if abs(da) > 1e-9:
        return ((rt - da) / da) * 100.0
    return 0.0


def pci_band_from_variance(variance_pct: float, threshold_pct: float = 5.0) -> str:
    mag = abs(float(variance_pct))
    if mag <= threshold_pct:
        return "GREEN"
    if mag <= threshold_pct * 2:
        return "AMBER"
    return "RED"


def map_lake_row_to_desk(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a lake row into desk / ritual-friendly fields (lowercase keys)."""
    # accept either upper or lower keys from fetch
    def g(*keys: str, default: Any = None) -> Any:
        for k in keys:
            if k in row and row[k] is not None:
                return row[k]
            lk = k.lower()
            if lk in row and row[lk] is not None:
                return row[lk]
        return default

    unit = str(g("UNIT_NAME", "unit_name", "PLANT", "plant_id") or "")
    da_burn = _as_float(g("DA_BURN_MMBTU", "da_burn_mmbtu"), 0.0)
    rt_burn = _as_float(g("RT_BURN_MMBTU", "rt_burn_mmbtu"), 0.0)
    # Prefer RT as "actual" when present; DA as award/estimate basis
    actual = rt_burn if g("RT_BURN_MMBTU", "rt_burn_mmbtu") is not None else da_burn
    award_mw = _as_float(g("DAM_MW", "dam_mw", "AWARD_MW"), 0.0)
    heat_rate = _as_float(g("HEAT_RATE", "heat_rate"), 0.0) or 7.5
    variance_pct = variance_pct_from_lake(
        {
            "da_burn_mmbtu": da_burn,
            "rt_burn_mmbtu": rt_burn,
            "burn_variance_mmbtu": g("BURN_VARIANCE_MMBTU", "burn_variance_mmbtu"),
        }
    )
    pci = pci_band_from_variance(variance_pct)
    op_date = g("OPERATING_DATE", "operating_date")
    he = g("HE", "he")
    return {
        "plant_id": unit,
        "unit_name": unit,
        "fleet_name": g("FLEET_NAME", "fleet_name"),
        "pipeline": g("PIPELINE", "pipeline"),
        "operating_date": _serialize_cell(op_date),
        "he": he,
        "award_mw": award_mw,
        "rt_mw": _as_float(g("RT_MW", "rt_mw"), 0.0),
        "heat_rate": heat_rate,
        "heat_rate_config": g("HEAT_RATE_CONFIG", "heat_rate_config"),
        "estimated_burn_mmbtu": da_burn,
        "actual_burn_mmbtu": actual,
        "da_burn_mmbtu": da_burn,
        "rt_burn_mmbtu": rt_burn,
        "burn_variance_mmbtu": _as_float(
            g("BURN_VARIANCE_MMBTU", "burn_variance_mmbtu"), 0.0
        ),
        "variance_pct": round(variance_pct, 4),
        "pci_status": pci,
        "net_revenue": g("NET_REVENUE", "net_revenue"),
        "dam_lmp": g("DAM_LMP", "dam_lmp"),
        "rt_lmp": g("RT_LMP", "rt_lmp"),
        "source_system": "snowflake_lake",
        "ritual_name": "lake_ingest",
        "load_ts": _serialize_cell(op_date),
        "notes": f"Ingest {g('HEAT_RATE_CONFIG', 'heat_rate_config') or ''} HE={he}".strip(),
    }


def map_lake_row_to_ritual_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Prefill gas_burn_update ritual from a lake desk row."""
    desk = map_lake_row_to_desk(row) if "plant_id" not in row else row
    if "da_burn_mmbtu" not in desk and any(k.isupper() for k in row):
        desk = map_lake_row_to_desk(row)
    return {
        "plant_id": desk.get("plant_id") or desk.get("unit_name"),
        "heat_rate": desk.get("heat_rate") or 7.5,
        "award_mw": desk.get("award_mw") or 0.0,
        "actual_burn_mmbtu": desk.get("actual_burn_mmbtu")
        if desk.get("actual_burn_mmbtu") is not None
        else desk.get("da_burn_mmbtu"),
        "hours": 1.0,
        "source_system": "snowflake_lake",
        "notes": desk.get("notes") or "Prefill from V_CALCULATED_GAS_BURN",
    }


def fetch_lake_gas_burn(
    limit: int = 50,
    unit_name: Optional[str] = None,
    *,
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, Any]:
    """
    Read recent calculated gas burn rows from the data lake (read-only).

    Returns safe status:
      {ok, skipped, reason?, rows, count, source, message, database, schema, mode}
    """
    limit = max(1, min(int(limit or 50), 500))
    unit_filter = (unit_name or "").strip() or None

    if not snowflake_read_enabled(creds):
        if not is_snowflake_configured(creds):
            return {
                "ok": False,
                "skipped": True,
                "reason": "not_configured",
                "rows": [],
                "count": 0,
                "mode": "lake_read",
                "message": "Snowflake lake read skipped — credentials not configured",
            }
        return {
            "ok": False,
            "skipped": True,
            "reason": "disabled",
            "rows": [],
            "count": 0,
            "mode": "lake_read",
            "message": "Snowflake lake read skipped — SNOWFLAKE_READ=false",
        }

    settings = snowflake_settings(creds)
    try:
        source = lake_source_table()
        table = fq_table(source, creds)
    except ValueError as exc:
        return {
            "ok": False,
            "skipped": False,
            "rows": [],
            "count": 0,
            "mode": "lake_read",
            "message": f"Invalid lake source: {exc}",
            "database": settings["database"],
            "schema": settings["schema"],
        }

    # SELECT * is fine for views; we normalize client-side
    if unit_filter:
        sql = f"""
            SELECT *
            FROM {table}
            WHERE UNIT_NAME = %s
            ORDER BY OPERATING_DATE DESC NULLS LAST, HE DESC NULLS LAST
            LIMIT %s
        """
        params: tuple[Any, ...] = (unit_filter, limit)
    else:
        sql = f"""
            SELECT *
            FROM {table}
            ORDER BY OPERATING_DATE DESC NULLS LAST, HE DESC NULLS LAST
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
        # Fallback: some sources use PLANT instead of UNIT_NAME
        if unit_filter:
            try:
                sql_alt = f"""
                    SELECT *
                    FROM {table}
                    WHERE PLANT = %s
                    ORDER BY DATE DESC NULLS LAST, HE DESC NULLS LAST
                    LIMIT %s
                """
                with connection(creds) as conn:
                    cur = conn.cursor()
                    try:
                        cur.execute(sql_alt, (unit_filter, limit))
                        raw_rows = cur.fetchall()
                        colnames = [d[0].lower() for d in (cur.description or [])]
                    finally:
                        cur.close()
            except Exception as exc2:  # noqa: BLE001
                msg = redact_secrets(str(exc2), creds)
                return {
                    "ok": False,
                    "skipped": False,
                    "rows": [],
                    "count": 0,
                    "source": table,
                    "mode": "lake_read",
                    "message": f"Snowflake lake read failed: {msg}",
                    "database": settings["database"],
                    "schema": settings["schema"],
                }
        else:
            msg = redact_secrets(str(exc), creds)
            return {
                "ok": False,
                "skipped": False,
                "rows": [],
                "count": 0,
                "source": table,
                "mode": "lake_read",
                "message": f"Snowflake lake read failed: {msg}",
                "database": settings["database"],
                "schema": settings["schema"],
            }

    raw_dicts: list[dict[str, Any]] = []
    for raw in raw_rows:
        item = {
            colnames[i] if i < len(colnames) else f"c{i}": _serialize_cell(raw[i])
            for i in range(len(raw))
        }
        raw_dicts.append(item)

    desk_rows = [map_lake_row_to_desk(r) for r in raw_dicts]
    # Keep raw lake columns too for power users (prefix)
    for desk, raw in zip(desk_rows, raw_dicts):
        desk["_lake"] = raw

    return {
        "ok": True,
        "skipped": False,
        "rows": desk_rows,
        "count": len(desk_rows),
        "source": table,
        "landing_table": table,  # cockpit reuses this key
        "mode": "lake_read",
        "write": False,
        "message": f"Ingested {len(desk_rows)} row(s) from {source} (read-only)",
        "database": settings["database"],
        "schema": settings["schema"],
        "unit_name": unit_filter,
        "limit": limit,
    }


def fetch_latest_lake_gas_burn(
    unit_name: Optional[str] = None,
    *,
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, Any]:
    result = fetch_lake_gas_burn(limit=1, unit_name=unit_name, creds=creds)
    result["row"] = (result.get("rows") or [None])[0]
    return result


def list_lake_units(
    limit: int = 100,
    *,
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, Any]:
    """Distinct unit names from the lake source for desk plant picker."""
    limit = max(1, min(int(limit or 100), 500))
    if not snowflake_read_enabled(creds) or not is_snowflake_configured(creds):
        return {"ok": False, "units": [], "message": "not available"}
    settings = snowflake_settings(creds)
    try:
        table = fq_table(lake_source_table(), creds)
    except ValueError as exc:
        return {"ok": False, "units": [], "message": str(exc)}
    sql = f"""
        SELECT DISTINCT UNIT_NAME
        FROM {table}
        WHERE UNIT_NAME IS NOT NULL
        ORDER BY UNIT_NAME
        LIMIT %s
    """
    try:
        with connection(creds) as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (limit,))
                units = [str(r[0]) for r in cur.fetchall() if r and r[0]]
            finally:
                cur.close()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "units": [],
            "message": redact_secrets(str(exc), creds)[:200],
            "database": settings["database"],
        }
    return {
        "ok": True,
        "units": units,
        "count": len(units),
        "source": table,
        "message": f"{len(units)} unit(s)",
    }
