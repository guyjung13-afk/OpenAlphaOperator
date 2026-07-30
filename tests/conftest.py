"""Shared fixtures — env isolation and sample envelopes."""

from __future__ import annotations

from typing import Any, Iterator

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """
    Keep unit tests free of host secrets and accidental live writes.

    Clears common integration env keys so load_credentials / gates are deterministic.
    """
    keys = [
        "DEMO_MODE",
        "SETUP_COMPLETE",
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_SCHEMA",
        "SNOWFLAKE_ROLE",
        "SNOWFLAKE_WRITE",
        "SNOWFLAKE_READ",
        "SNOWFLAKE_STAGING_WRITE",
        "TEMPORAL_HOST",
        "TEMPORAL_NAMESPACE",
        "TEMPORAL_API_KEY",
        "TEMPORAL_TASK_QUEUE",
        "TEMPORAL_USE",
        "TEMPORAL_TLS",
        "REDIS_URL",
        "EIA_API_KEY",
        "WEBHOOK_URL",
        "WEBHOOK_SECRET",
        "XAI_API_KEY",
    ]
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    # Default demo on for tests unless a case overrides
    monkeypatch.setenv("DEMO_MODE", "true")
    yield


@pytest.fixture
def sf_creds() -> dict[str, dict[str, str]]:
    """Realistic-looking (fake) Snowflake credentials for gate tests."""
    return {
        "snowflake": {
            "account": "xy12345.us-east-1",
            "user": "operator_bot",
            "password": "s3cret-not-real",
            "warehouse": "COMPUTE_WH",
            "database": "ALPHAGEN_ETRM",
            "schema": "GOLD",
        },
        "app": {"demo_mode": "false", "setup_complete": "true"},
    }


@pytest.fixture
def ritual_public() -> dict[str, Any]:
    return {
        "status": "ok",
        "ritual": "gas_burn_update",
        "plant_id": "DEMO-1",
        "heat_rate": 7.5,
        "hours": 1.0,
        "award_mw": 500.0,
        "actual_burn_mmbtu": 3750.0,
        "estimated_burn_mmbtu": 3750.0,
        "deviation_pct": 0.0,
        "new_accum_mmbtu": 3750.0,
        "pci_status": "GREEN",
        "etrm_status": "COMPLIANT",
        "etrm_action": "NONE",
        "outcome": "ALL_DOWNSTREAM_UPDATED",
        "notes": "unit-test",
        "mode": "live",
        "ritual_at": "2026-07-29T15:00:00+00:00",
    }


@pytest.fixture
def ritual_payload() -> dict[str, Any]:
    return {
        "plant_id": "DEMO-1",
        "award_mw": 500.0,
        "actual_burn_mmbtu": 3750.0,
        "heat_rate": 7.5,
        "hours": 1.0,
        "operator": "test-op",
        "source_system": "pytest",
        "password": "should-be-stripped",
        "notes": "payload-note",
    }


@pytest.fixture
def ritual_burn() -> dict[str, Any]:
    return {
        "estimated_burn_mmbtu": 3750.0,
        "variance_pct": 0.0,
        "new_accum_mmbtu": 3750.0,
        "pci_status": "GREEN",
        "timestamp": "2026-07-29T15:00:00+00:00",
    }
