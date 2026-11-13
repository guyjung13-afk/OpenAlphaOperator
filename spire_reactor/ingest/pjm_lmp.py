"""
PJM real-time unverified five-minute LMPs for public trading hubs.

Data Miner 2 requires a free subscription key in PJM_API_KEY
(header Ocp-Apim-Subscription-Key). With no key, or on any error, the result
is empty and marked source unavailable. Bus-level nodes are not stored.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from spire_reactor.tags.schema import PJM_HUBS, QUALITY_GOOD, pjm_lmp_tag

PJM_LMP_URL = os.getenv(
    "PJM_API_URL",
    "https://api.pjm.com/api/v1/rt_unverified_fivemin_lmps",
)
_HUB_SET = {hub.upper(): hub for hub in PJM_HUBS}


def _resolve_key(override: str | None = None) -> str:
    if override is not None and str(override).strip():
        return str(override).strip()
    try:
        from spire_reactor.config.integrations import get_credential

        key = get_credential("pjm", "api_key")
        if key:
            return key
    except Exception:  # noqa: BLE001 — ingest must run without the config package
        pass
    return os.getenv("PJM_API_KEY", "").strip()


def _redact(message: str, secret: str) -> str:
    if secret and secret in message:
        return message.replace(secret, "••••")
    return message


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    if not text:
        return None
    if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
        text = text[:-2] + ":" + text[-2:]
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("items", "data", "rows"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _unavailable(message: str) -> dict[str, Any]:
    return {
        "source": "pjm",
        "ok": False,
        "state": "unavailable",
        "message": message,
        "samples": [],
    }


def fetch_pjm_lmp(
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    key = _resolve_key(api_key if api_key is not None else None)
    if not key:
        return _unavailable("source unavailable: PJM_API_KEY not set")

    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=True)
    samples: list[dict[str, Any]] = []
    errors: list[str] = []
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Accept": "application/json",
        "User-Agent": "OpenAlphaOperator/0.1 (public PJM hub desk)",
    }
    try:
        for hub in PJM_HUBS:
            try:
                response = client.get(
                    PJM_LMP_URL,
                    headers=headers,
                    params={
                        "rowCount": "48",
                        "startRow": "1",
                        "pnode_name": hub,
                        "fields": "datetime_beginning_utc,pnode_name,total_lmp_rt",
                    },
                )
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001 — one hub must not invent the others
                errors.append(_redact(str(exc), key))
                continue
            for row in _items(response.json()):
                name = str(row.get("pnode_name") or "").strip().upper()
                if name not in _HUB_SET:
                    continue
                moment = _parse_ts(row.get("datetime_beginning_utc"))
                raw_price = row.get("total_lmp_rt")
                if moment is None or raw_price is None or raw_price == "":
                    continue
                try:
                    price = float(raw_price)
                except (TypeError, ValueError):
                    continue
                samples.append(
                    {
                        "tag": pjm_lmp_tag(_HUB_SET[name]),
                        "ts": moment,
                        "value": price,
                        "unit": "USD/MWh",
                        "quality": QUALITY_GOOD,
                    }
                )
    finally:
        if own:
            client.close()

    if samples:
        last = max(sample["ts"] for sample in samples)
        note = f"PJM five-minute hub LMP · {len(samples)} samples · last {last.isoformat()}"
        if errors:
            note = f"{note} · some hubs unavailable"
        return {
            "source": "pjm",
            "ok": True,
            "state": "ok",
            "message": note,
            "samples": samples,
        }
    detail = errors[0] if errors else "empty response"
    return _unavailable(f"source unavailable: {detail}")
