"""
Public / free feed adapters for OpenAlphaOperator demos (no Snowflake required).

Sources:
  - Open-Meteo  (no API key) — weather as demand / heat-rate proxy
  - EIA Open Data (optional key) — Henry Hub-ish natural gas series when configured
  - Synthetic plant mapper — maps feeds → award_mw / actual_burn_mmbtu for rituals

Usage:
  from spire_reactor.ingest.public_feeds import fetch_demo_snapshot, build_operator_payload
  snap = fetch_demo_snapshot()
  payload = build_operator_payload(snap)
  # → trigger_ritual("gas_burn_update", payload)
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

# ── defaults (Houston / ERCOT-ish demo plant) ────────────────────────
DEFAULT_LAT = float(os.getenv("DEMO_LAT", "29.76"))
DEFAULT_LON = float(os.getenv("DEMO_LON", "-95.37"))
DEFAULT_PLANT_ID = os.getenv("DEMO_PLANT_ID", "DEMO-PUBLIC-1")
DEFAULT_HEAT_RATE = float(os.getenv("DEMO_HEAT_RATE", "7.5"))
DEFAULT_NAMEPLATE_MW = float(os.getenv("DEMO_NAMEPLATE_MW", "500.0"))
OPEN_METEO_URL = os.getenv(
    "OPEN_METEO_URL",
    "https://api.open-meteo.com/v1/forecast",
)
EIA_API_KEY = os.getenv("EIA_API_KEY", "").strip()
EIA_BASE = "https://api.eia.gov/v2"
# Henry Hub natural gas spot price series (daily); requires free EIA key
EIA_NG_SERIES = os.getenv("EIA_NG_SERIES", "natural-gas/pri/fut/data")
HTTP_TIMEOUT = float(os.getenv("INGEST_HTTP_TIMEOUT", "12"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_open_meteo(
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
    client: Optional[httpx.Client] = None,
) -> dict[str, Any]:
    """
    Current weather via Open-Meteo (no key).
    Returns normalized fields + raw subset; never raises (error in payload).
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
        "timezone": "UTC",
    }
    own = client is None
    client = client or httpx.Client(timeout=HTTP_TIMEOUT)
    try:
        resp = client.get(OPEN_METEO_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        cur = data.get("current") or {}
        return {
            "source": "open-meteo",
            "ok": True,
            "lat": lat,
            "lon": lon,
            "temperature_c": cur.get("temperature_2m"),
            "humidity_pct": cur.get("relative_humidity_2m"),
            "wind_speed_kmh": cur.get("wind_speed_10m"),
            "weather_code": cur.get("weather_code"),
            "observed_at": cur.get("time") or _utc_now(),
            "raw_current": cur,
        }
    except Exception as exc:  # noqa: BLE001 — demo must degrade gracefully
        return {
            "source": "open-meteo",
            "ok": False,
            "error": str(exc),
            "lat": lat,
            "lon": lon,
            "observed_at": _utc_now(),
        }
    finally:
        if own:
            client.close()


def fetch_eia_natural_gas_price(
    api_key: Optional[str] = None,
    client: Optional[httpx.Client] = None,
) -> dict[str, Any]:
    """
    Latest EIA natural-gas related price point when EIA_API_KEY is set.
    Without a key, returns ok=False and a fixed demo price so rituals still run.
    """
    key = (api_key if api_key is not None else EIA_API_KEY).strip()
    if not key:
        return {
            "source": "eia",
            "ok": False,
            "skipped": True,
            "reason": "EIA_API_KEY not set",
            "price_usd_mmbtu": 2.75,  # demo fallback
            "observed_at": _utc_now(),
        }

    # v2 facets endpoint for NG futures / spot — keep thin; series path configurable
    url = f"{EIA_BASE}/{EIA_NG_SERIES}"
    params = {
        "api_key": key,
        "frequency": "daily",
        "data[0]": "value",
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": 1,
    }
    own = client is None
    client = client or httpx.Client(timeout=HTTP_TIMEOUT)
    try:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        body = resp.json()
        rows = (body.get("response") or {}).get("data") or []
        if not rows:
            return {
                "source": "eia",
                "ok": False,
                "error": "empty EIA response",
                "price_usd_mmbtu": 2.75,
                "observed_at": _utc_now(),
            }
        row = rows[0]
        value = row.get("value")
        return {
            "source": "eia",
            "ok": True,
            "price_usd_mmbtu": float(value) if value is not None else None,
            "period": row.get("period"),
            "observed_at": row.get("period") or _utc_now(),
            "raw": row,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "source": "eia",
            "ok": False,
            "error": str(exc),
            "price_usd_mmbtu": 2.75,
            "observed_at": _utc_now(),
        }
    finally:
        if own:
            client.close()


def _load_factor_from_weather(temperature_c: Optional[float]) -> float:
    """
    Map outdoor temp → relative plant load factor.
    Hot/cold extremes → higher load; mild ~0.85 of nameplate.
    """
    if temperature_c is None:
        return 0.90
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

    gas_price = gas.get("price_usd_mmbtu")
    notes_bits = [
        f"public-feeds demo",
        f"load_factor={load_factor}",
    ]
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
        "estimated_burn_mmbtu": round(estimated, 2),
        "gas_price_usd_mmbtu": gas_price,
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
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
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
        "ok": True,
        "temperature_c": round(temp, 1),
        "humidity_pct": 55,
        "wind_speed_kmh": 10.0 + (t % 7),
        "observed_at": _utc_now(),
    }
    gas = {
        "source": "synthetic",
        "ok": True,
        "price_usd_mmbtu": round(2.5 + (t % 10) * 0.05, 2),
        "observed_at": _utc_now(),
    }
    snap = {
        "fetched_at": _utc_now(),
        "demo": True,
        "plant_id": DEFAULT_PLANT_ID,
        "weather": weather,
        "natural_gas": gas,
    }
    snap["operator_payload"] = build_operator_payload(snap)
    return snap
