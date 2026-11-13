"""Pull public feeds into the tag store for the Real Time Desk."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

from spire_reactor.ingest.ercot_lmp import fetch_ercot_lmp
from spire_reactor.ingest.pjm_lmp import fetch_pjm_lmp
from spire_reactor.ingest.public_feeds import (
    DEFAULT_LAT,
    DEFAULT_LON,
    OPEN_METEO_URL,
    fetch_eia_natural_gas_price,
    fetch_open_meteo,
)
from spire_reactor.tags.schema import (
    QUALITY_GOOD,
    QUALITY_SYNTHETIC,
    catalog,
    desk_timezone_name,
    eia_price_tag,
    key_tags,
    utc_now,
    weather_tag,
)
from spire_reactor.tags.store import TagSample, get_store

_PULL_LOCK = threading.Lock()
_WEATHER_FIELDS = (
    ("TEMP_C", "temperature_c", "degC"),
    ("WIND_KMH", "wind_speed_kmh", "km/h"),
    ("RH_PCT", "humidity_pct", "%"),
)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _period_utc(period: str) -> datetime | None:
    text = period.strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            day = datetime.strptime(text, "%Y-%m-%d")
            return day.replace(tzinfo=timezone.utc)
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _weather_feed(lat: float, lon: float) -> dict[str, Any]:
    weather = fetch_open_meteo(lat=lat, lon=lon)
    samples: list[dict[str, Any]] = []
    if weather.get("ok"):
        for point in weather.get("series") or []:
            moment = _period_utc(str(point.get("time") or ""))
            if moment is None:
                continue
            for variable, field, unit in _WEATHER_FIELDS:
                number = _num(point.get(field))
                if number is None:
                    continue
                samples.append(
                    {
                        "tag": weather_tag(variable),
                        "ts": moment,
                        "value": number,
                        "unit": unit,
                        "quality": QUALITY_GOOD,
                    }
                )
    endpoint = weather.get("endpoint")
    if weather.get("ok"):
        message = str(weather.get("message") or "Open-Meteo")
        if endpoint and endpoint != OPEN_METEO_URL:
            message = f"{message} (primary host unreachable)"
        state = "ok"
    else:
        message = f"source unavailable: {weather.get('error') or 'Open-Meteo failed'}"
        state = "unavailable"
    return {
        "source": "open-meteo",
        "ok": bool(weather.get("ok")),
        "state": state,
        "message": message,
        "samples": samples,
    }


def _saved_secret(integration_id: str, field: str, env_name: str) -> str:
    """Read a key on the caller thread. Streamlit secrets are not touched from workers."""
    try:
        from spire_reactor.config.integrations import get_credential

        found = get_credential(integration_id, field)
        if found:
            return found
    except Exception:  # noqa: BLE001
        pass
    return os.getenv(env_name, "").strip()


def _eia_feed(api_key: str) -> dict[str, Any]:
    if not api_key:
        gas: dict[str, Any] = {
            "ok": False,
            "skipped": True,
            "reason": "EIA_API_KEY not set",
            "history": [],
        }
    else:
        gas = fetch_eia_natural_gas_price(api_key=api_key)
    samples: list[dict[str, Any]] = []
    if gas.get("ok"):
        for point in gas.get("history") or []:
            moment = _period_utc(str(point.get("period") or ""))
            number = _num(point.get("value"))
            if moment is None or number is None:
                continue
            samples.append(
                {
                    "tag": eia_price_tag(),
                    "ts": moment,
                    "value": number,
                    "unit": "USD/MMBtu",
                    "quality": QUALITY_GOOD,
                }
            )
    if gas.get("ok") and samples:
        message = str(gas.get("message") or "EIA")
        state = "ok"
        ok = True
    else:
        message = f"source unavailable: {gas.get('reason') or gas.get('error') or 'EIA failed'}"
        state = "unavailable"
        ok = False
        samples = []
    return {"source": "eia", "ok": ok, "state": state, "message": message, "samples": samples}


def _samples_from(feed: dict[str, Any]) -> list[TagSample]:
    if not feed.get("ok") and feed.get("state") != "synthetic":
        return []
    out: list[TagSample] = []
    for row in feed.get("samples") or []:
        moment = row.get("ts")
        number = _num(row.get("value"))
        if not isinstance(moment, datetime) or number is None:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        out.append(
            TagSample(
                tag=str(row["tag"]),
                ts_utc=moment.astimezone(timezone.utc),
                value=number,
                unit=str(row.get("unit") or ""),
                quality=str(row.get("quality") or QUALITY_GOOD),
                source=str(feed.get("source") or ""),
            )
        )
    return out


def _apply(feeds: list[dict[str, Any]], *, mode: str) -> dict[str, Any]:
    store = get_store()
    fetched = utc_now()
    health = [
        {
            "source": feed["source"],
            "ok": bool(feed.get("ok")),
            "state": feed.get("state") or "unavailable",
            "message": feed.get("message") or "",
            "fetched_at": fetched.isoformat(),
        }
        for feed in feeds
    ]
    samples: list[TagSample] = []
    for feed in feeds:
        samples.extend(_samples_from(feed))
    if mode == "synthetic":
        store.replace_quality(QUALITY_SYNTHETIC, samples)
    else:
        store.upsert(samples)
    store.set_health(health)
    store.set_mode(mode)
    store.mark_fetched(fetched)
    return summarize()


def summarize() -> dict[str, Any]:
    store = get_store()
    mode = store.mode()
    quality = QUALITY_SYNTHETIC if mode == "synthetic" else QUALITY_GOOD
    latest: list[dict[str, Any]] = []
    for spec in catalog().values():
        health = store.health_for(spec.source) or {}
        state = str(health.get("state") or "unavailable")
        sample = store.latest(spec.tag, quality=quality) if state in {"ok", "synthetic"} else None
        latest.append(
            {
                "tag": spec.tag,
                "unit": spec.unit,
                "source": spec.source,
                "state": state,
                "value": None if sample is None else sample.value,
                "ts_utc": None if sample is None else sample.ts_utc.isoformat(),
                "quality": None if sample is None else sample.quality,
                "key": spec.key,
                "cadence": spec.cadence,
            }
        )
    fetched = store.fetched_at()
    return {
        "fetched_at": None if fetched is None else fetched.isoformat(),
        "timezone": desk_timezone_name(),
        "mode": mode,
        "health": store.health(),
        "latest": latest,
    }


def _pjm_feed(api_key: str) -> dict[str, Any]:
    if not api_key:
        return {
            "source": "pjm",
            "ok": False,
            "state": "unavailable",
            "message": "source unavailable: PJM_API_KEY not set",
            "samples": [],
        }
    return fetch_pjm_lmp(api_key=api_key)


def _pull_feeds() -> list[dict[str, Any]]:
    eia_key = _saved_secret("eia", "api_key", "EIA_API_KEY")
    pjm_key = _saved_secret("pjm", "api_key", "PJM_API_KEY")
    jobs = {
        "open-meteo": lambda: _weather_feed(DEFAULT_LAT, DEFAULT_LON),
        "eia": lambda: _eia_feed(eia_key),
        "ercot": fetch_ercot_lmp,
        "pjm": lambda: _pjm_feed(pjm_key),
    }
    feeds: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        future_map = {pool.submit(fn): name for name, fn in jobs.items()}
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                feeds[name] = future.result()
            except Exception as exc:  # noqa: BLE001
                feeds[name] = {
                    "source": name,
                    "ok": False,
                    "state": "unavailable",
                    "message": f"source unavailable: {exc}",
                    "samples": [],
                }
    return [feeds[name] for name in ("open-meteo", "eia", "ercot", "pjm")]


def collect_public_desk() -> dict[str, Any]:
    """Fetch every public feed. Failed sources contribute no numbers."""
    with _PULL_LOCK:
        return _apply(_pull_feeds(), mode="live")


def synthetic_samples() -> list[TagSample]:
    """Offline sparkline. Values are arbitrary and must be shown as synthetic."""
    now = utc_now().replace(second=0, microsecond=0)
    samples: list[TagSample] = []
    for index, spec in enumerate(key_tags()):
        for step in range(8):
            moment = now - timedelta(minutes=15 * (7 - step))
            samples.append(
                TagSample(
                    tag=spec.tag,
                    ts_utc=moment,
                    value=round(20 + index + step * 0.25, 2),
                    unit=spec.unit,
                    quality=QUALITY_SYNTHETIC,
                    source="synthetic",
                )
            )
    return samples


def load_synthetic_desk() -> dict[str, Any]:
    """Replace the on-screen samples with a labeled synthetic set. Does not call feeds."""
    samples = synthetic_samples()
    feeds = []
    for source in ("open-meteo", "eia", "ercot", "pjm"):
        feeds.append(
            {
                "source": source,
                "ok": False,
                "state": "synthetic",
                "message": "synthetic offline sample — not a measurement",
                "samples": [
                    {
                        "tag": sample.tag,
                        "ts": sample.ts_utc,
                        "value": sample.value,
                        "unit": sample.unit,
                        "quality": sample.quality,
                    }
                    for sample in samples
                    if catalog()[sample.tag].source == source
                ],
            }
        )
    with _PULL_LOCK:
        return _apply(feeds, mode="synthetic")
