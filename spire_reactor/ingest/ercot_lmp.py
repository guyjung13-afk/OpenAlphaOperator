"""
ERCOT real-time hub and load-zone prices. No API key.

Primary: the public dashboard JSON (interval-ending settlement point prices).
Fallback: public MIS report 12301 (NP6-905-CD), hubs and load zones only.
Resource-node rows in that report are skipped and never stored.
"""

from __future__ import annotations

import csv
import io
import os
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from spire_reactor.tags.schema import (
    ERCOT_DASHBOARD_FIELDS,
    QUALITY_GOOD,
    ercot_lmp_tag,
)

ERCOT_PRICES_URL = os.getenv(
    "ERCOT_PRICES_URL",
    "https://www.ercot.com/api/1/services/read/dashboards/system-wide-prices.json",
)
ERCOT_DOC_LIST_URL = "https://www.ercot.com/misapp/servlets/IceDocListJsonWS"
ERCOT_DOWNLOAD_URL = "https://www.ercot.com/misdownload/servlets/mirDownload"
ERCOT_REPORT_TYPE_ID = os.getenv("ERCOT_REPORT_TYPE_ID", "12301")
_CT = ZoneInfo("America/Chicago")
_ALLOWED = set(ERCOT_DASHBOARD_FIELDS.values())
_HEADERS = {
    "User-Agent": "OpenAlphaOperator/0.1 (public ERCOT price desk)",
    "Accept": "application/json, text/csv, application/zip, */*",
}


def _sample(entity: str, moment: datetime, value: Any) -> dict[str, Any] | None:
    if entity not in _ALLOWED or value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return {
        "tag": ercot_lmp_tag(entity),
        "ts": moment.astimezone(timezone.utc),
        "value": number,
        "unit": "USD/MWh",
        "quality": QUALITY_GOOD,
    }


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


def _samples_from_dashboard(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rtSppData") or []
    samples: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return samples
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_ts = row.get("timestamp")
        if not raw_ts:
            continue
        try:
            moment = _parse_offset_ts(str(raw_ts))
        except ValueError:
            continue
        for field, entity in ERCOT_DASHBOARD_FIELDS.items():
            sample = _sample(entity, moment, row.get(field))
            if sample:
                samples.append(sample)
    return samples


def _mis_interval_end(date_s: str, hour_ending: str, interval: str, dst_flag: str) -> datetime:
    day = datetime.strptime(date_s.strip(), "%m/%d/%Y")
    start = day + timedelta(hours=int(hour_ending) - 1, minutes=int(interval) * 15)
    fold = 1 if str(dst_flag).strip().upper() == "Y" else 0
    local = datetime(
        start.year,
        start.month,
        start.day,
        start.hour,
        start.minute,
        tzinfo=_CT,
        fold=fold,
    )
    return local.astimezone(timezone.utc)


def _samples_from_csv(text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    samples: list[dict[str, Any]] = []
    for row in reader:
        name = (row.get("SettlementPointName") or "").strip()
        if name not in _ALLOWED:
            continue
        try:
            moment = _mis_interval_end(
                row.get("DeliveryDate") or "",
                row.get("DeliveryHour") or "",
                row.get("DeliveryInterval") or "",
                row.get("DSTFlag") or "N",
            )
        except (TypeError, ValueError):
            continue
        sample = _sample(name, moment, row.get("SettlementPointPrice"))
        if sample:
            samples.append(sample)
    return samples


def _csv_from_download(content: bytes) -> str:
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as bundle:
            names = [name for name in bundle.namelist() if name.lower().endswith(".csv")]
            if not names:
                raise ValueError("MIS zip has no csv")
            return bundle.read(names[0]).decode("utf-8", "replace")
    return content.decode("utf-8", "replace")


def _latest_mis_docs(client: httpx.Client, limit: int) -> list[str]:
    response = client.get(
        ERCOT_DOC_LIST_URL,
        params={"reportTypeId": ERCOT_REPORT_TYPE_ID},
        headers=_HEADERS,
    )
    response.raise_for_status()
    payload = response.json()
    docs = (payload.get("ListDocsByRptTypeRes") or {}).get("DocumentList") or []
    if isinstance(docs, dict):
        docs = [docs]
    rows: list[tuple[str, str]] = []
    for item in docs:
        doc = item.get("Document") if isinstance(item, dict) else None
        if not isinstance(doc, dict):
            continue
        ext = str(doc.get("Extension") or "").lower()
        friendly = str(doc.get("FriendlyName") or "")
        if ext not in {"zip", "csv"} and not friendly.lower().endswith("csv"):
            continue
        doc_id = str(doc.get("DocID") or "").strip()
        published = str(doc.get("PublishDate") or "")
        if doc_id:
            rows.append((published, doc_id))
    rows.sort(reverse=True)
    return [doc_id for _published, doc_id in rows[:limit]]


def _samples_from_mis(client: httpx.Client) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for doc_id in _latest_mis_docs(client, limit=6):
        response = client.get(
            ERCOT_DOWNLOAD_URL,
            params={"doclookupId": doc_id},
            headers=_HEADERS,
        )
        response.raise_for_status()
        samples.extend(_samples_from_csv(_csv_from_download(response.content)))
    return samples


def fetch_ercot_lmp(client: httpx.Client | None = None) -> dict[str, Any]:
    """Return hub/load-zone samples, or an unavailable result. Never fills prices."""
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=True)
    try:
        return _fetch_ercot_with_client(client)
    finally:
        if own:
            client.close()


def _fetch_ercot_with_client(client: httpx.Client) -> dict[str, Any]:
    dashboard_error = "not tried"
    try:
        response = client.get(ERCOT_PRICES_URL, headers=_HEADERS)
        response.raise_for_status()
        body = response.content.lstrip()
        if body[:1] in {b"<", b""}:
            raise ValueError("dashboard response was not JSON")
        samples = _samples_from_dashboard(response.json())
        if samples:
            last = max(sample["ts"] for sample in samples)
            return {
                "source": "ercot",
                "ok": True,
                "state": "ok",
                "message": f"ERCOT public prices · {len(samples)} samples · last {last.isoformat()}",
                "samples": samples,
            }
        dashboard_error = "dashboard JSON had no hub prices"
    except Exception as exc:  # noqa: BLE001 — fall through to the public MIS report
        dashboard_error = str(exc)
    try:
        samples = _samples_from_mis(client)
        if samples:
            last = max(sample["ts"] for sample in samples)
            return {
                "source": "ercot",
                "ok": True,
                "state": "ok",
                "message": (
                    f"ERCOT MIS report {ERCOT_REPORT_TYPE_ID} · "
                    f"{len(samples)} samples · last {last.isoformat()}"
                ),
                "samples": samples,
            }
        message = f"source unavailable: {dashboard_error}; MIS report had no hub prices"
    except Exception as exc:  # noqa: BLE001
        message = f"source unavailable: {dashboard_error}; MIS {exc}"
    return {
        "source": "ercot",
        "ok": False,
        "state": "unavailable",
        "message": message,
        "samples": [],
    }
