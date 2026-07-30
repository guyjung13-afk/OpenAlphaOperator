"""
Connection tests for dashboard Setup stage.

Each test returns: {ok, latency_ms, message, integration}
Never include secrets in message.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse, urlunparse


def _result(
    integration: str,
    ok: bool,
    message: str,
    latency_ms: float,
    **extra: Any,
) -> dict[str, Any]:
    out = {
        "integration": integration,
        "ok": ok,
        "message": message,
        "latency_ms": round(latency_ms, 1),
    }
    out.update(extra)
    return out


def test_open_meteo(
    lat: float | str = 29.76,
    lon: float | str = -95.37,
    **_: Any,
) -> dict[str, Any]:
    """Ping Open-Meteo (no auth)."""
    t0 = time.perf_counter()
    try:
        from spire_reactor.ingest.public_feeds import fetch_open_meteo

        weather = fetch_open_meteo(lat=float(lat), lon=float(lon))
        ms = (time.perf_counter() - t0) * 1000
        if weather.get("ok"):
            temp = weather.get("temperature_c")
            return _result(
                "open_meteo",
                True,
                f"OK — temp {temp}°C at {lat},{lon}",
                ms,
            )
        return _result(
            "open_meteo",
            False,
            f"Unreachable: {weather.get('error') or 'unknown'}",
            ms,
        )
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        return _result("open_meteo", False, f"Error: {exc}", ms)


def test_eia(api_key: str = "", **_: Any) -> dict[str, Any]:
    """Validate EIA API key (optional integration)."""
    t0 = time.perf_counter()
    key = (api_key or "").strip()
    if not key:
        ms = (time.perf_counter() - t0) * 1000
        return _result(
            "eia",
            False,
            "No API key — optional; desk can run without EIA",
            ms,
            skipped=True,
        )
    try:
        from spire_reactor.ingest.public_feeds import fetch_eia_natural_gas_price

        gas = fetch_eia_natural_gas_price(api_key=key)
        ms = (time.perf_counter() - t0) * 1000
        if gas.get("ok"):
            price = gas.get("price_usd_mmbtu")
            return _result("eia", True, f"OK — NG price ${price}/MMBtu", ms)
        if gas.get("skipped"):
            return _result("eia", False, gas.get("reason") or "skipped", ms, skipped=True)
        return _result("eia", False, f"Failed: {gas.get('error') or 'empty response'}", ms)
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        return _result("eia", False, f"Error: {exc}", ms)


def _redis_url_with_password(url: str, password: str = "") -> str:
    """If password provided separately, inject into redis URL."""
    url = (url or "redis://localhost:6379/0").strip()
    password = (password or "").strip()
    if not password:
        return url
    parsed = urlparse(url)
    # If password already in netloc, keep as-is
    if parsed.password:
        return url
    host = parsed.hostname or "localhost"
    port = f":{parsed.port}" if parsed.port else ""
    user = parsed.username or ""
    auth = f":{password}@" if not user else f"{user}:{password}@"
    netloc = f"{auth}{host}{port}"
    return urlunparse(
        (parsed.scheme or "redis", netloc, parsed.path or "/0", "", "", "")
    )


def test_redis(url: str = "redis://localhost:6379/0", password: str = "", **_: Any) -> dict[str, Any]:
    """PING Redis."""
    t0 = time.perf_counter()
    target = _redis_url_with_password(url, password)
    try:
        import redis

        r = redis.from_url(target, socket_connect_timeout=3, socket_timeout=3)
        pong = r.ping()
        ms = (time.perf_counter() - t0) * 1000
        if pong:
            # Do not echo full URL if it might embed password
            host = urlparse(target).hostname or "redis"
            return _result("redis", True, f"OK — PING {host}", ms)
        return _result("redis", False, "PING returned false", ms)
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        return _result(
            "redis",
            False,
            f"Unreachable: {exc}. Start with: docker compose up -d redis",
            ms,
        )


def test_snowflake(
    account: str = "",
    user: str = "",
    password: str = "",
    warehouse: str = "COMPUTE_WH",
    database: str = "ALPHAGEN_ETRM",
    schema: str = "GOLD",
    **_: Any,
) -> dict[str, Any]:
    """Connect to Snowflake and run SELECT 1."""
    t0 = time.perf_counter()
    account = (account or "").strip()
    user = (user or "").strip()
    password = (password or "").strip()
    if not account or not user or not password:
        ms = (time.perf_counter() - t0) * 1000
        return _result(
            "snowflake",
            False,
            "Missing account, user, or password",
            ms,
        )
    # Placeholder-ish values from examples
    if account in ("your_account", "placeholder") or user in ("your_user", "test_user"):
        ms = (time.perf_counter() - t0) * 1000
        return _result(
            "snowflake",
            False,
            "Placeholder credentials — replace with real Snowflake login",
            ms,
        )
    try:
        import snowflake.connector

        conn = snowflake.connector.connect(
            account=account,
            user=user,
            password=password,
            warehouse=warehouse or None,
            database=database or None,
            schema=schema or None,
            login_timeout=15,
            network_timeout=15,
        )
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
        finally:
            conn.close()
        ms = (time.perf_counter() - t0) * 1000
        return _result(
            "snowflake",
            True,
            f"OK — connected ({database}.{schema})",
            ms,
        )
    except ImportError:
        ms = (time.perf_counter() - t0) * 1000
        return _result(
            "snowflake",
            False,
            "snowflake-connector-python not installed",
            ms,
        )
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        # Strip any accidental credential echo from exception text
        msg = str(exc)
        if password and password in msg:
            msg = msg.replace(password, "••••")
        return _result("snowflake", False, f"Auth/connect failed: {msg}", ms)


def test_temporal(**_: Any) -> dict[str, Any]:
    return _result(
        "temporal",
        False,
        "Phase 2 — Temporal worker not wired yet",
        0.0,
        phase2=True,
    )


def test_webhooks(**_: Any) -> dict[str, Any]:
    return _result(
        "webhooks",
        False,
        "Phase 2 — downstream webhooks not wired yet",
        0.0,
        phase2=True,
    )


def test_xai(**_: Any) -> dict[str, Any]:
    return _result(
        "xai",
        False,
        "Phase 2 — xAI fusion insights not wired yet",
        0.0,
        phase2=True,
    )


_TESTERS: dict[str, Callable[..., dict[str, Any]]] = {
    "test_open_meteo": test_open_meteo,
    "test_eia": test_eia,
    "test_redis": test_redis,
    "test_snowflake": test_snowflake,
    "test_temporal": test_temporal,
    "test_webhooks": test_webhooks,
    "test_xai": test_xai,
}


def run_test(test_name: str, fields: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Dispatch by catalog `test` field name."""
    fn = _TESTERS.get(test_name)
    if not fn:
        return _result(test_name, False, f"Unknown test: {test_name}", 0.0)
    return fn(**(fields or {}))


def test_live_integrations(creds: dict[str, dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Run all live-phase tests using a credentials map from load_credentials()."""
    from spire_reactor.config.integrations import live_integrations

    results: dict[str, dict[str, Any]] = {}
    for integ in live_integrations():
        iid = str(integ["id"])
        fields = dict(creds.get(iid) or {})
        results[iid] = run_test(str(integ["test"]), fields)
    return results
