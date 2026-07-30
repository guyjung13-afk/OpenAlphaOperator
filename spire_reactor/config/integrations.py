"""
Declarative integration catalog + credential loading for OpenAlphaOperator.

Load order (highest wins per field):
  Streamlit st.secrets → .streamlit/secrets.toml (direct file read) → env → defaults.

Never log secret values. Dashboard Setup stage persists to .streamlit/secrets.toml.
Docker / headless API continue to use .env (same key names). Direct TOML parse covers
headless unit tests and Streamlit secrets watcher lag after save.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

# Repo root: spire_reactor/config/integrations.py → parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SECRETS_PATH = _REPO_ROOT / ".streamlit" / "secrets.toml"

# Field names treated as secrets when masking / writing
_SECRET_FIELD_NAMES = frozenset(
    {
        "password",
        "api_key",
        "api_key_or_cert",
        "bearer_secret",
        "token",
        "secret",
    }
)

# ── catalog ──────────────────────────────────────────────────────────
# phase: "live" = testable today | "phase2" = UI slot only
INTEGRATIONS: list[dict[str, Any]] = [
    {
        "id": "open_meteo",
        "label": "Open-Meteo (weather)",
        "category": "ingest",
        "phase": "live",
        "auth_required": False,
        "description": "Free weather forecast — no login. Used for public-feed plant load prefill.",
        "help_url": "https://open-meteo.com/",
        "fields": [
            {"key": "lat", "label": "Latitude", "secret": False, "env": "DEMO_LAT", "default": "29.76"},
            {"key": "lon", "label": "Longitude", "secret": False, "env": "DEMO_LON", "default": "-95.37"},
        ],
        "secrets_section": "open_meteo",
        "test": "test_open_meteo",
    },
    {
        "id": "eia",
        "label": "EIA Open Data (natural gas)",
        "category": "ingest",
        "phase": "live",
        "auth_required": True,
        "optional": True,
        "description": "Free API key for natural-gas price series. Optional — desk works without it.",
        "help_url": "https://www.eia.gov/opendata/",
        "fields": [
            {"key": "api_key", "label": "API key", "secret": True, "env": "EIA_API_KEY", "default": ""},
        ],
        "secrets_section": "eia",
        "test": "test_eia",
    },
    {
        "id": "redis",
        "label": "Redis (ritual bus / cache)",
        "category": "bus",
        "phase": "live",
        "auth_required": False,
        "optional": True,
        "description": "Pub/sub + latest PCI cache. Local Docker has no password; embed password in URL if needed.",
        "help_url": None,
        "fields": [
            {
                "key": "url",
                "label": "Redis URL",
                "secret": False,
                "env": "REDIS_URL",
                "default": "redis://localhost:6379/0",
            },
        ],
        "secrets_section": "redis",
        "test": "test_redis",
    },
    {
        "id": "snowflake",
        "label": "Snowflake (system of record)",
        "category": "store",
        "phase": "live",
        "auth_required": True,
        "optional": False,
        "description": "Account login for streams, dynamic tables, and consumer views. Required for live (non-demo) desk path.",
        "help_url": "https://docs.snowflake.com/",
        "fields": [
            {"key": "account", "label": "Account", "secret": False, "env": "SNOWFLAKE_ACCOUNT", "default": ""},
            {"key": "user", "label": "User", "secret": False, "env": "SNOWFLAKE_USER", "default": ""},
            {
                "key": "password",
                "label": "Password",
                "secret": True,
                "env": "SNOWFLAKE_PASSWORD",
                "default": "",
            },
            {
                "key": "warehouse",
                "label": "Warehouse",
                "secret": False,
                "env": "SNOWFLAKE_WAREHOUSE",
                "default": "COMPUTE_WH",
            },
            {
                "key": "database",
                "label": "Database",
                "secret": False,
                "env": "SNOWFLAKE_DATABASE",
                "default": "ALPHAGEN_ETRM",
            },
            {
                "key": "schema",
                "label": "Schema",
                "secret": False,
                "env": "SNOWFLAKE_SCHEMA",
                "default": "GOLD",
            },
        ],
        "secrets_section": "snowflake",
        "test": "test_snowflake",
    },
    {
        "id": "temporal",
        "label": "Temporal (durable rituals)",
        "category": "orchestrate",
        "phase": "live",
        "auth_required": True,
        "optional": True,
        "description": "Durable PCI_ETRM_Operator_Update workflow. Local: host localhost:7233; Cloud: host + API key + TLS.",
        "help_url": "https://docs.temporal.io/",
        "fields": [
            {
                "key": "host",
                "label": "Host",
                "secret": False,
                "env": "TEMPORAL_HOST",
                "default": "",
            },
            {
                "key": "namespace",
                "label": "Namespace",
                "secret": False,
                "env": "TEMPORAL_NAMESPACE",
                "default": "default",
            },
            {
                "key": "api_key",
                "label": "API key (Cloud)",
                "secret": True,
                "env": "TEMPORAL_API_KEY",
                "default": "",
            },
        ],
        "secrets_section": "temporal",
        "test": "test_temporal",
    },
    {
        "id": "webhooks",
        "label": "Downstream webhooks (BI / Teams / Spire)",
        "category": "notify",
        "phase": "live",
        "auth_required": True,
        "optional": True,
        "description": "Optional fusion notify URL (POST JSON). Used by fuse_pci_etrm activity.",
        "help_url": None,
        "fields": [
            {
                "key": "url",
                "label": "Webhook URL",
                "secret": False,
                "env": "WEBHOOK_URL",
                "default": "",
            },
            {
                "key": "bearer_secret",
                "label": "Bearer / shared secret",
                "secret": True,
                "env": "WEBHOOK_SECRET",
                "default": "",
            },
        ],
        "secrets_section": "webhooks",
        "test": "test_webhooks",
    },
    {
        "id": "xai",
        "label": "xAI / Grok (fusion insights)",
        "category": "ai",
        "phase": "live",
        "auth_required": True,
        "optional": True,
        "description": "Optional AI text for fusion. Without a key, rule-based insights are used.",
        "help_url": "https://x.ai/",
        "fields": [
            {
                "key": "api_key",
                "label": "API key",
                "secret": True,
                "env": "XAI_API_KEY",
                "default": "",
            },
        ],
        "secrets_section": "xai",
        "test": "test_xai",
    },
]


def secrets_path() -> Path:
    """Absolute path to host secrets file (gitignored)."""
    return _SECRETS_PATH


def mask_secret(value: Optional[str], show: int = 0) -> str:
    """Mask a secret for UI display. Empty → empty string."""
    if not value:
        return ""
    if show <= 0:
        return "••••••••"
    if len(value) <= show:
        return "•" * len(value)
    return value[:show] + "…" + ("•" * 4)


def _streamlit_section(section: str) -> Optional[dict[str, Any]]:
    """Best-effort read of st.secrets[section] when running under Streamlit."""
    try:
        import streamlit as st

        if section in st.secrets:
            raw = st.secrets[section]
            # AttrDict / Mapping → plain dict
            return {k: raw[k] for k in raw}
    except Exception:  # noqa: BLE001 — no Streamlit, no secrets file, etc.
        return None
    return None


def _read_secrets_file(path: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """
    Parse .streamlit/secrets.toml directly (tomllib).

    Used when st.secrets is unavailable (headless) or stale after Setup save
    before Streamlit's file watcher reloads. Never logs contents.
    """
    target = path or _SECRETS_PATH
    if not target.is_file():
        return {}
    try:
        import tomllib
    except ImportError:  # pragma: no cover — Python < 3.11
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return {}
    try:
        with target.open("rb") as fh:
            raw = tomllib.load(fh)
    except Exception:  # noqa: BLE001 — corrupt/partial write
        return {}
    out: dict[str, dict[str, Any]] = {}
    for section, body in raw.items():
        if isinstance(body, dict):
            out[str(section)] = dict(body)
    return out


def _section_value(
    key: str,
    *,
    streamlit_map: dict[str, Any],
    file_map: dict[str, Any],
    env_key: str,
    default: str = "",
) -> str:
    """Resolve one field: st.secrets → file TOML → env → default."""
    if key in streamlit_map and streamlit_map[key] is not None and str(streamlit_map[key]).strip() != "":
        return str(streamlit_map[key]).strip()
    if key in file_map and file_map[key] is not None and str(file_map[key]).strip() != "":
        return str(file_map[key]).strip()
    return _env_value(env_key, default)


def _env_value(env_key: str, default: str = "") -> str:
    return (os.getenv(env_key) or default).strip()


def load_credentials() -> dict[str, dict[str, str]]:
    """
    Merge credentials for every catalog integration.

    Returns: { integration_id: { field_key: value, ... }, "app": {...} }
    Source priority per field: Streamlit secrets → secrets.toml file → env → default.
    """
    out: dict[str, dict[str, str]] = {}
    file_all = _read_secrets_file()

    for integ in INTEGRATIONS:
        section = str(integ["secrets_section"])
        iid = str(integ["id"])
        secret_map = _streamlit_section(section) or {}
        file_map = file_all.get(section) or {}
        fields: dict[str, str] = {}
        for f in integ["fields"]:
            key = str(f["key"])
            env_key = str(f["env"])
            default = str(f.get("default") or "")
            fields[key] = _section_value(
                key,
                streamlit_map=secret_map,
                file_map=file_map,
                env_key=env_key,
                default=default,
            )
        out[iid] = fields

    # App flags
    app_st = _streamlit_section("app") or {}
    app_file = file_all.get("app") or {}
    out["app"] = {
        "demo_mode": _section_value(
            "demo_mode",
            streamlit_map=app_st,
            file_map=app_file,
            env_key="DEMO_MODE",
            default="true",
        ).lower(),
        "setup_complete": _section_value(
            "setup_complete",
            streamlit_map=app_st,
            file_map=app_file,
            env_key="SETUP_COMPLETE",
            default="false",
        ).lower(),
    }
    return out


def get_credential(integration_id: str, field: str, default: str = "") -> str:
    """Single-field helper used by ingest / connectors."""
    creds = load_credentials()
    return str((creds.get(integration_id) or {}).get(field) or default).strip()


def get_eia_api_key() -> str:
    return get_credential("eia", "api_key")


def is_setup_complete(creds: Optional[dict[str, dict[str, str]]] = None) -> bool:
    c = creds if creds is not None else load_credentials()
    return str((c.get("app") or {}).get("setup_complete", "false")).lower() in (
        "1",
        "true",
        "yes",
    )


def is_demo_mode(creds: Optional[dict[str, dict[str, str]]] = None) -> bool:
    c = creds if creds is not None else load_credentials()
    return str((c.get("app") or {}).get("demo_mode", "true")).lower() in (
        "1",
        "true",
        "yes",
    )


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def write_secrets(
    sections: dict[str, dict[str, Any]],
    *,
    path: Optional[Path] = None,
) -> Path:
    """
    Write / overwrite .streamlit/secrets.toml from a sections dict.

    sections example:
      {
        "app": {"demo_mode": True, "setup_complete": True},
        "eia": {"api_key": "..."},
        "snowflake": {"account": "...", ...},
      }
    Empty secret fields are omitted so operators can leave blanks to keep env-only.
    """
    target = path or _SECRETS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [
        "# Generated by OpenAlphaOperator Dashboard Setup stage",
        "# DO NOT COMMIT — gitignored via .streamlit/secrets.toml",
        "",
    ]

    # Stable order: app first, then catalog order, then any extras
    order = ["app"] + [str(i["secrets_section"]) for i in INTEGRATIONS]
    seen: set[str] = set()
    for section_name in order + [s for s in sections if s not in order]:
        if section_name in seen or section_name not in sections:
            continue
        seen.add(section_name)
        body = sections[section_name] or {}
        lines.append(f"[{section_name}]")
        for k, v in body.items():
            if v is None:
                continue
            if isinstance(v, bool):
                lines.append(f"{k} = {_toml_bool(v)}")
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                lines.append(f"{k} = {v}")
            else:
                s = str(v).strip()
                if s == "" and k in _SECRET_FIELD_NAMES:
                    continue
                lines.append(f'{k} = "{_toml_escape(s)}"')
        lines.append("")

    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def live_integrations() -> list[dict[str, Any]]:
    return [i for i in INTEGRATIONS if i.get("phase") == "live"]


def phase2_integrations() -> list[dict[str, Any]]:
    return [i for i in INTEGRATIONS if i.get("phase") == "phase2"]
