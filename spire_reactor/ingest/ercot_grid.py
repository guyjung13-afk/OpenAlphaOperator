"""
ERCOT operating reserves (PRC) and Energy Emergency Alert status.

Keyless public dashboard: daily-prc.json. No API key.
If the dashboard cannot be read, the desk shows the condition as unavailable
and keeps the last successful snapshot only for as-of/age of stored tags.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from spire_reactor.tags.schema import QUALITY_GOOD, ercot_eea_tag, ercot_prc_tag

ERCOT_DAILY_PRC_URL = os.getenv(
    "ERCOT_DAILY_PRC_URL",
    "https://www.ercot.com/api/1/services/read/dashboards/daily-prc.json",
)
_CT = ZoneInfo("America/Chicago")
_HEADERS = {
    "User-Agent": "OpenAlphaOperator/0.1 (public ERCOT grid desk)",
    "Accept": "application/json, */*",
}
_SNAPSHOT = Path(__file__).resolve().parents[2] / "logs" / "ercot-grid.json"


def _parse_offset_ts(text: str) -> datetime:
    cleaned = text.strip().replace("Z", "+00:00")
    if len(cleaned) >= 5 and cleaned[-5] in "+-" and cleaned[-3] != ":":
        cleaned = cleaned[:-2] + ":" + cleaned[-2:]
    if " " in cleaned and "T" not in cleaned:
        cleaned = cleaned.replace(" ", "T", 1)
    moment = datetime.fromisoformat(cleaned)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_CT)
    return moment.astimezone(timezone.utc)


def _observed_at(payload: dict[str, Any], condition: dict[str, Any]) -> datetime:
    raw_updated = payload.get("lastUpdated")
    if raw_updated:
        try:
            return _parse_offset_ts(str(raw_updated))
        except ValueError:
            pass
    raw_epoch = condition.get("datetime")
    if raw_epoch is not None:
        try:
            return datetime.fromtimestamp(float(raw_epoch), tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            pass
    return datetime.now(timezone.utc)


def _parse_prc(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _parse_eea(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _write_snapshot(payload: dict[str, Any]) -> None:
    try:
        _SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        serializable = dict(payload)
        observed = serializable.get("observed_at")
        if isinstance(observed, datetime):
            serializable["observed_at"] = observed.astimezone(timezone.utc).isoformat()
        _SNAPSHOT.write_text(json.dumps(serializable, indent=2) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001 — snapshot must not take the desk down
        return


def load_grid_snapshot() -> dict[str, Any] | None:
    """Last PRC/EEA fetch result (no secrets). Used by the status strip."""
    if not _SNAPSHOT.is_file():
        return None
    try:
        data = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("observed_at")
    if isinstance(raw, str):
        try:
            data["observed_at"] = _parse_offset_ts(raw)
        except ValueError:
            data["observed_at"] = None
    return data


def fetch_ercot_grid() -> dict[str, Any]:
    """Read PRC (operating reserves) and EEA level from the public daily PRC dashboard."""
    try:
        response = httpx.get(
            ERCOT_DAILY_PRC_URL,
            headers=_HEADERS,
            timeout=20.0,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        result = {
            "ok": False,
            "source": "ercot-grid",
            "state": "unavailable",
            "message": f"daily-prc.json unavailable: {exc}",
            "reason": f"request failed: {exc}",
            "samples": [],
        }
        _write_snapshot(result)
        return result
    if response.status_code != 200:
        result = {
            "ok": False,
            "source": "ercot-grid",
            "state": "unavailable",
            "message": f"daily-prc.json HTTP {response.status_code}",
            "reason": (
                "https://www.ercot.com/api/1/services/read/dashboards/daily-prc.json "
                f"returned HTTP {response.status_code}"
            ),
            "samples": [],
        }
        _write_snapshot(result)
        return result
    try:
        payload = response.json()
    except ValueError:
        result = {
            "ok": False,
            "source": "ercot-grid",
            "state": "unavailable",
            "message": "daily-prc.json was not JSON",
            "reason": "daily-prc.json body was not JSON",
            "samples": [],
        }
        _write_snapshot(result)
        return result
    if not isinstance(payload, dict):
        result = {
            "ok": False,
            "source": "ercot-grid",
            "state": "unavailable",
            "message": "daily-prc.json missing current_condition",
            "reason": "daily-prc.json payload was not an object",
            "samples": [],
        }
        _write_snapshot(result)
        return result
    condition = payload.get("current_condition") or {}
    if not isinstance(condition, dict) or not condition:
        result = {
            "ok": False,
            "source": "ercot-grid",
            "state": "unavailable",
            "message": "daily-prc.json missing current_condition",
            "reason": "daily-prc.json had no current_condition object",
            "samples": [],
        }
        _write_snapshot(result)
        return result
    prc = _parse_prc(condition.get("prc_value"))
    eea = _parse_eea(condition.get("eea_level"))
    if prc is None and eea is None:
        result = {
            "ok": False,
            "source": "ercot-grid",
            "state": "unavailable",
            "message": "daily-prc.json had no PRC or EEA values",
            "reason": "current_condition had neither prc_value nor eea_level",
            "samples": [],
        }
        _write_snapshot(result)
        return result
    observed = _observed_at(payload, condition)
    samples: list[dict[str, Any]] = []
    if prc is not None:
        samples.append(
            {
                "tag": ercot_prc_tag(),
                "ts": observed,
                "value": prc,
                "unit": "MW",
                "quality": QUALITY_GOOD,
            }
        )
    if eea is not None:
        samples.append(
            {
                "tag": ercot_eea_tag(),
                "ts": observed,
                "value": float(eea),
                "unit": "level",
                "quality": QUALITY_GOOD,
            }
        )
    title = str(condition.get("title") or "").strip()
    note = str(condition.get("condition_note") or "").strip()
    state_name = str(condition.get("state") or "").strip()
    parts: list[str] = []
    if prc is not None:
        parts.append(f"PRC {prc:,.0f} MW")
    parts.append(title or state_name or (f"EEA {eea}" if eea is not None else "grid"))
    result = {
        "ok": True,
        "source": "ercot-grid",
        "state": "ok",
        "message": "; ".join(parts),
        "reason": "",
        "samples": samples,
        "prc_mw": prc,
        "eea_level": eea,
        "title": title,
        "condition_note": note,
        "state_name": state_name,
        "observed_at": observed,
    }
    _write_snapshot(result)
    return result
