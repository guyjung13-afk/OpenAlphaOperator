"""
Snowflake connection helpers for **read-only lake ingest** (and optional writes).

Product default: the desk **does not write** to Snowflake — it only reads existing
lake views/tables (see store/lake.py). Writes require explicit SNOWFLAKE_WRITE=true.

Credentials: same load order as dashboard Setup
  st.secrets → .streamlit/secrets.toml → env → defaults
  (via spire_reactor.config.integrations.load_credentials)
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Any, Generator, Optional

# Placeholder values that must never open a real session
_PLACEHOLDER_ACCOUNTS = frozenset({"", "your_account", "placeholder"})
_PLACEHOLDER_USERS = frozenset({"", "your_user", "test_user"})
_PLACEHOLDER_PASSWORDS = frozenset({"", "your_password", "dummy", "placeholder"})

# Unquoted Snowflake identifiers (simple form)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

# AlphaGen lake defaults (read-only desk account)
_DEFAULT_WAREHOUSE = "ALPHAGEN_WH"
_DEFAULT_DATABASE = "ALPHAGEN"
_DEFAULT_SCHEMA = "DBO"


def snowflake_settings(
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, str]:
    """Resolve Snowflake connect kwargs from Setup/env credentials."""
    if creds is None:
        from spire_reactor.config.integrations import load_credentials

        creds = load_credentials()
    sf = dict(creds.get("snowflake") or {})
    role = (sf.get("role") or os.getenv("SNOWFLAKE_ROLE") or "").strip()
    return {
        "account": (sf.get("account") or "").strip(),
        "user": (sf.get("user") or "").strip(),
        "password": (sf.get("password") or "").strip(),
        "warehouse": (sf.get("warehouse") or _DEFAULT_WAREHOUSE).strip() or _DEFAULT_WAREHOUSE,
        "database": (sf.get("database") or _DEFAULT_DATABASE).strip() or _DEFAULT_DATABASE,
        "schema": (sf.get("schema") or _DEFAULT_SCHEMA).strip() or _DEFAULT_SCHEMA,
        "role": role,
    }


def is_snowflake_configured(
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> bool:
    """True when account/user/password look real enough to attempt a connect."""
    s = snowflake_settings(creds)
    if s["account"].lower() in _PLACEHOLDER_ACCOUNTS:
        return False
    if s["user"].lower() in _PLACEHOLDER_USERS:
        return False
    if s["password"].lower() in _PLACEHOLDER_PASSWORDS:
        return False
    return True


def snowflake_write_enabled(
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> bool:
    """
    Whether the ritual should attempt a Snowflake landing **write**.

    Default **OFF** — AlphaGen desk is read-only against the data lake.
    Only enables when SNOWFLAKE_WRITE=true|1|yes|on **and** credentials look real.
    (Creating tables / INSERT is out of scope for lake-ingest mode.)
    """
    force = (os.getenv("SNOWFLAKE_WRITE") or "").strip().lower()
    if force not in ("1", "true", "yes", "on"):
        return False
    return is_snowflake_configured(creds)


def snowflake_read_enabled(
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> bool:
    """
    Whether cockpit / API should attempt Snowflake **reads** (lake ingest).

    - Requires configured credentials.
    - Allowed even in demo_mode so operators can browse the lake.
    - SNOWFLAKE_READ=false disables; default on when configured.
    """
    force = (os.getenv("SNOWFLAKE_READ") or "").strip().lower()
    if force in ("0", "false", "no", "off"):
        return False
    if not is_snowflake_configured(creds):
        return False
    return True


def connect(creds: Optional[dict[str, dict[str, str]]] = None) -> Any:
    """
    Open a Snowflake connection. Caller must close().

    Raises ImportError if connector missing, ValueError if not configured,
    or snowflake connector errors on auth/network failure.
    """
    if not is_snowflake_configured(creds):
        raise ValueError("Snowflake credentials not configured (or still placeholders)")

    import snowflake.connector

    s = snowflake_settings(creds)
    kwargs: dict[str, Any] = {
        "account": s["account"],
        "user": s["user"],
        "password": s["password"],
        "warehouse": s["warehouse"] or None,
        "database": s["database"] or None,
        "schema": s["schema"] or None,
        "login_timeout": int(os.getenv("SNOWFLAKE_LOGIN_TIMEOUT", "15")),
        "network_timeout": int(os.getenv("SNOWFLAKE_NETWORK_TIMEOUT", "30")),
    }
    if s["role"]:
        kwargs["role"] = s["role"]
    return snowflake.connector.connect(**kwargs)


@contextmanager
def connection(
    creds: Optional[dict[str, dict[str, str]]] = None,
) -> Generator[Any, None, None]:
    """Context manager that always closes the connection."""
    conn = connect(creds)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _quote_ident(name: str, *, kind: str) -> str:
    """Validate and double-quote a Snowflake identifier (fail closed)."""
    n = (name or "").strip()
    if not n or not _IDENT_RE.match(n):
        raise ValueError(f"Invalid Snowflake {kind} identifier: {name!r}")
    return f'"{n}"'


def fq_table(table: str, creds: Optional[dict[str, dict[str, str]]] = None) -> str:
    """Return quoted DATABASE.SCHEMA.TABLE for SELECT (or optional INSERT) targets."""
    s = snowflake_settings(creds)
    db = _quote_ident(s["database"] or _DEFAULT_DATABASE, kind="database")
    sch = _quote_ident(s["schema"] or _DEFAULT_SCHEMA, kind="schema")
    tbl = _quote_ident(table, kind="table")
    return f"{db}.{sch}.{tbl}"


def redact_secrets(message: str, creds: Optional[dict[str, dict[str, str]]] = None) -> str:
    """Strip password (and similar) from error strings for public envelopes."""
    msg = str(message or "")
    s = snowflake_settings(creds)
    pw = s.get("password") or ""
    if pw and pw in msg:
        msg = msg.replace(pw, "••••")
    return msg
