"""
Public feed adapters for OpenAlphaOperator demos.

Sources:
  - Open-Meteo (no API key) — weather tags. Falls back to other public Open-Meteo hosts.
  - EIA Open Data (optional key) — series RNGWHHD when configured. No stand-in price.
  - Derived burn payload for the local ritual only (not a measured tag).
  - synthetic_tick — offline ritual sample, labeled synthetic.

Usage:
  from spire_reactor.ingest.public_feeds import fetch_demo_snapshot, build_operator_payload
  snap = fetch_demo_snapshot()
  payload = build_operator_payload(snap)
  # → trigger_ritual("gas_burn_update", payload)
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from spire_reactor.tags.schema import eia_series_id

# ── defaults (public Houston weather site, not a plant) ──────────────
DEFAULT_LAT = float(os.getenv("DEMO_LAT", "29.76"))
DEFAULT_LON = float(os.getenv("DEMO_LON", "-95.37"))
DEFAULT_PLANT_ID = os.getenv("DEMO_PLANT_ID", "DEMO-PUBLIC-1")
DEFAULT_HEAT_RATE = float(os.getenv("DEMO_HEAT_RATE", "7.5"))
DEFAULT_NAMEPLATE_MW = float(os.getenv("DEMO_NAMEPLATE_MW", "500.0"))
OPEN_METEO_URL = os.getenv(
    "OPEN_METEO_URL",
    "https://api.open-meteo.com/v1/forecast",
)
_METEO_FALLBACKS = (
    "https://historical-forecast-api.open-meteo.com/v1/forecast",
    "https://previous-runs-api.open-meteo.com/v1/forecast",
)
_HTTP_HEADERS = {
    "User-Agent": "OpenAlphaOperator/0.1 (public data desk)",
    "Accept": "application/json",
}
# Prefer Setup-stage secrets / shared loader; fall back to env at call time.
def _resolve_eia_api_key(override: Optional[str] = None) -> str:
    if override is not None and str(override).strip():
        return str(override).strip()
    try:
        from spire_reactor.config.integrations import get_eia_api_key

        key = get_eia_api_key()
        if key:
            return key
    except Exception:  # noqa: BLE001 — keep ingest usable without config package
        pass
    return os.getenv("EIA_API_KEY", "").strip()


EIA_API_KEY = os.getenv("EIA_API_KEY", "").strip()  # legacy module default; prefer _resolve_eia_api_key
EIA_BASE = "https://api.eia.gov/v2"
# Henry Hub daily spot (RNGWHHD) on the public natural-gas price route.
EIA_SERIES_ROUTE = os.getenv("EIA_SERIES_ROUTE", "natural-gas/pri/sum/data").strip("/")
HTTP_TIMEOUT = float(os.getenv("INGEST_HTTP_TIMEOUT", "12"))


def _http_timeout() -> httpx.Timeout:
    return httpx.Timeout(HTTP_TIMEOUT, connect=min(4.0, HTTP_TIMEOUT))


def _redact(message: str, secret: str) -> str:
    if secret and secret in message:
        return message.replace(secret, "••••")
    return message


def _meteo_urls() -> list[str]:
    extra = [part.strip() for part in os.getenv("OPEN_METEO_FALLBACK_URLS", "").split(",") if part.strip()]
    urls: list[str] = []
    for url in (OPEN_METEO_URL, *extra, *_METEO_FALLBACKS):
        if url and url not in urls:
            urls.append(url)
    return urls


def _parse_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    if not text:
        return None
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _meteo_series(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Hourly points at or before now, plus the current observation."""
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    winds = hourly.get("wind_speed_10m") or []
    hums = hourly.get("relative_humidity_2m") or []
    cutoff = datetime.now(timezone.utc) + timedelta(minutes=20)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(times):
        moment = _parse_utc(raw)
        if moment is None or moment > cutoff:
            continue
        key = moment.isoformat()
        seen.add(key)
        rows.append(
            {
                "time": key,
                "temperature_c": temps[index] if index < len(temps) else None,
                "wind_speed_kmh": winds[index] if index < len(winds) else None,
                "humidity_pct": hums[index] if index < len(hums) else None,
            }
        )
    current = data.get("current") or {}
    moment = _parse_utc(current.get("time"))
    if moment is not None and moment <= cutoff and moment.isoformat() not in seen:
        rows.append(
            {
                "time": moment.isoformat(),
                "temperature_c": current.get("temperature_2m"),
                "wind_speed_kmh": current.get("wind_speed_10m"),
                "humidity_pct": current.get("relative_humidity_2m"),
            }
        )
    rows.sort(key=lambda row: str(row["time"]))
    return rows


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_open_meteo(
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
    client: Optional[httpx.Client] = None,
) -> dict[str, Any]:
    """
    Current weather via Open-Meteo (no key).

    Tries OPEN_METEO_URL, then other public Open-Meteo hosts that speak the
    same forecast API. Never raises. A total failure leaves temperatures null.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m",
        "past_hours": 24,
        "forecast_hours": 1,
        "timezone": "UTC",
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
    }
    own = client is None
    client = client or httpx.Client(timeout=_http_timeout(), follow_redirects=True)
    errors: list[str] = []
    try:
        for url in _meteo_urls():
            try:
                resp = client.get(url, params=params, headers=_HTTP_HEADERS)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:  # noqa: BLE001 — try the next public host
                errors.append(f"{url}: {exc}")
                continue
            cur = data.get("current") or {}
            if cur.get("temperature_2m") is None:
                errors.append(f"{url}: no current temperature")
                continue
            observed = _parse_utc(cur.get("time"))
            return {
                "source": "open-meteo",
                "ok": True,
                "state": "ok",
                "endpoint": url,
                "lat": lat,
                "lon": lon,
                "temperature_c": cur.get("temperature_2m"),
                "humidity_pct": cur.get("relative_humidity_2m"),
                "wind_speed_kmh": cur.get("wind_speed_10m"),
                "weather_code": cur.get("weather_code"),
                "observed_at": observed.isoformat() if observed else None,
                "series": _meteo_series(data),
                "raw_current": cur,
                "message": f"Open-Meteo via {url}",
            }
        return {
            "source": "open-meteo",
            "ok": False,
            "state": "unavailable",
            "error": "; ".join(errors) or "source unavailable",
            "lat": lat,
            "lon": lon,
            "temperature_c": None,
            "humidity_pct": None,
            "wind_speed_kmh": None,
            "series": [],
            "observed_at": None,
        }
    finally:
        if own:
            client.close()


def _eia_unavailable(message: str, *, skipped: bool = False) -> dict[str, Any]:
    return {
        "source": "eia",
        "ok": False,
        "skipped": skipped,
        "state": "unavailable",
        "reason": message,
        "error": None if skipped else message,
        "price_usd_mmbtu": None,
        "observed_at": None,
        "history": [],
    }


def fetch_eia_natural_gas_price(
    api_key: Optional[str] = None,
    client: Optional[httpx.Client] = None,
) -> dict[str, Any]:
    """
    EIA daily series (default RNGWHHD, Henry Hub spot).

    No key, an error, or an empty body yields a null price. No stand-in number.
    """
    key = _resolve_eia_api_key(api_key if api_key is not None else None)
    series_id = eia_series_id()
    if not key:
        return _eia_unavailable("EIA_API_KEY not set", skipped=True)

    url = f"{EIA_BASE}/{EIA_SERIES_ROUTE}"
    params = {
        "api_key": key,
        "frequency": "daily",
        "data[0]": "value",
        "facets[series][]": series_id,
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": 30,
    }
    own = client is None
    client = client or httpx.Client(timeout=_http_timeout(), follow_redirects=True)
    try:
        resp = client.get(url, params=params, headers=_HTTP_HEADERS)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and body.get("error"):
            err = body["error"]
            text = err.get("message") if isinstance(err, dict) else str(err)
            return _eia_unavailable(_redact(str(text), key))
        rows = (body.get("response") or {}).get("data") or []
        history: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            reported = str(row.get("series") or "").upper()
            if reported and reported not in {series_id, f"NG.{series_id}.D"}:
                continue
            raw_value = row.get("value")
            period = row.get("period")
            if raw_value is None or not period:
                continue
            try:
                number = float(raw_value)
            except (TypeError, ValueError):
                continue
            history.append({"period": str(period), "value": number})
        if not history:
            return _eia_unavailable("empty EIA response")
        latest = history[0]
        return {
            "source": "eia",
            "ok": True,
            "state": "ok",
            "series_id": series_id,
            "price_usd_mmbtu": latest["value"],
            "period": latest["period"],
            "observed_at": latest["period"],
            "history": history,
            "message": f"EIA {series_id} · {latest['period']}",
        }
    except Exception as exc:  # noqa: BLE001
        return _eia_unavailable(_redact(str(exc), key))
    finally:
        if own:
            client.close()


def _load_factor_from_weather(temperature_c: Optional[float]) -> Optional[float]:
    """
    Map outdoor temp → relative load factor for the local burn ritual.

    Missing temperature returns None. The ritual must not invent a load.
    """
    if temperature_c is None:
        return None
    # Comfort band ~18–24 C
    deviation = abs(float(temperature_c) - 21.0)
    # 0 C or 42 C → factor approaches ~1.0; mild → ~0.82
    factor = 0.82 + min(deviation, 20.0) / 20.0 * 0.18
    return round(min(1.0, max(0.55, factor)), 4)


def _burn_noise_from_wind(wind_kmh: Optional[float]) -> float:
    """Small actual-vs-award noise from wind (0–3%)."""
    if wind_kmh is None:
        return 0.0
    # Cap influence
    return round(min(float(wind_kmh), 40.0) / 40.0 * 0.03, 4)


def build_operator_payload(
    snapshot: dict[str, Any],
    *,
    plant_id: str = DEFAULT_PLANT_ID,
    heat_rate: float = DEFAULT_HEAT_RATE,
    nameplate_mw: float = DEFAULT_NAMEPLATE_MW,
    hours: float = 1.0,
) -> dict[str, Any]:
    """
    Map a public-feed snapshot into gas_burn_update ritual fields.
    Pure function over snapshot dict (testable without network).
    """
    weather = snapshot.get("weather") or {}
    gas = snapshot.get("natural_gas") or {}
    temp = weather.get("temperature_c")
    wind = weather.get("wind_speed_kmh")

    load_factor = _load_factor_from_weather(temp)
    synthetic = (weather.get("source") == "synthetic") or (weather.get("quality") == "synthetic")
    if load_factor is None:
        award_mw = None
        estimated = None
        actual = None
    else:
        award_mw = round(nameplate_mw * load_factor, 2)
        estimated = award_mw * heat_rate * hours
        noise = _burn_noise_from_wind(wind)
        # Slight under-burn when mild wind; over-burn signal when hot (temp-driven)
        temp_bias = 0.0
        if temp is not None and float(temp) > 32:
            temp_bias = 0.02
        elif temp is not None and float(temp) < 5:
            temp_bias = 0.015
        actual = round(estimated * (1.0 + noise + temp_bias), 2)

    gas_price = gas.get("price_usd_mmbtu") if gas.get("ok") else None
    notes_bits = [
        "SYNTHETIC" if synthetic else "derived-from-weather",
        "not a measured tag",
    ]
    if load_factor is not None:
        notes_bits.append(f"load_factor={load_factor}")
    if weather.get("ok"):
        notes_bits.append(f"temp_c={temp}")
    if gas_price is not None:
        notes_bits.append(f"ng_usd_mmbtu={gas_price}")

    return {
        "plant_id": plant_id,
        "heat_rate": heat_rate,
        "award_mw": award_mw,
        "actual_burn_mmbtu": actual,
        "hours": hours,
        "notes": "; ".join(notes_bits),
        # extras for dashboard / audit (ignored by ritual if unused)
        "nameplate_mw": nameplate_mw,
        "load_factor": load_factor,
        "estimated_burn_mmbtu": None if estimated is None else round(estimated, 2),
        "gas_price_usd_mmbtu": gas_price,
        "measurement": False,
        "quality": "synthetic" if synthetic else "derived",
        "ingest_at": snapshot.get("fetched_at") or _utc_now(),
        "sources": {
            "weather": weather.get("source"),
            "weather_ok": weather.get("ok"),
            "natural_gas": gas.get("source"),
            "natural_gas_ok": gas.get("ok"),
        },
    }


def fetch_demo_snapshot(
    *,
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
    eia_api_key: Optional[str] = None,
) -> dict[str, Any]:
    """
    Pull public feeds and return a single snapshot for the demo plant.
    Network errors are embedded; snapshot always returns a structure.
    """
    with httpx.Client(timeout=_http_timeout(), follow_redirects=True) as client:
        weather = fetch_open_meteo(lat=lat, lon=lon, client=client)
        gas = fetch_eia_natural_gas_price(api_key=eia_api_key, client=client)

    return {
        "fetched_at": _utc_now(),
        "demo": True,
        "plant_id": DEFAULT_PLANT_ID,
        "location": {"lat": lat, "lon": lon},
        "weather": weather,
        "natural_gas": gas,
        # Convenience: pre-built ritual payload
        "operator_payload": build_operator_payload(
            {"weather": weather, "natural_gas": gas, "fetched_at": _utc_now()}
        ),
    }


def synthetic_tick(seed: Optional[float] = None) -> dict[str, Any]:
    """
    Offline fallback: no network. Deterministic-ish payload for CI / air-gapped demos.
    """
    t = seed if seed is not None else datetime.now(timezone.utc).timetuple().tm_yday
    # Smooth seasonal-ish oscillation
    temp = 21.0 + 12.0 * math.sin(t / 365.0 * 2 * math.pi)
    weather = {
        "source": "synthetic",
        "quality": "synthetic",
        "ok": True,
        "state": "synthetic",
        "temperature_c": round(temp, 1),
        "humidity_pct": 55,
        "wind_speed_kmh": 10.0 + (t % 7),
        "observed_at": _utc_now(),
        "label": "synthetic",
    }
    gas = {
        "source": "synthetic",
        "quality": "synthetic",
        "ok": True,
        "state": "synthetic",
        "price_usd_mmbtu": round(2.5 + (t % 10) * 0.05, 2),
        "observed_at": _utc_now(),
        "label": "synthetic",
    }
    snap = {
        "fetched_at": _utc_now(),
        "demo": True,
        "synthetic": True,
        "label": "synthetic",
        "plant_id": DEFAULT_PLANT_ID,
        "weather": weather,
        "natural_gas": gas,
    }
    snap["operator_payload"] = build_operator_payload(snap)
    return snap
