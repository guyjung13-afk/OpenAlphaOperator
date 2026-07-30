"""Unit tests for Snowflake client gates and helpers."""

from __future__ import annotations

import pytest
from spire_reactor.store.snowflake_client import (
    fq_table,
    is_snowflake_configured,
    redact_secrets,
    snowflake_read_enabled,
    snowflake_settings,
    snowflake_write_enabled,
)


@pytest.mark.unit
def test_not_configured_when_empty():
    assert is_snowflake_configured({"snowflake": {}}) is False
    assert is_snowflake_configured({"snowflake": {"account": "x", "user": "u", "password": ""}}) is False


@pytest.mark.unit
def test_not_configured_placeholders():
    assert (
        is_snowflake_configured(
            {
                "snowflake": {
                    "account": "your_account",
                    "user": "your_user",
                    "password": "your_password",
                }
            }
        )
        is False
    )


@pytest.mark.unit
def test_configured_with_real_looking_creds(sf_creds):
    assert is_snowflake_configured(sf_creds) is True


@pytest.mark.unit
def test_settings_defaults(sf_creds):
    s = snowflake_settings(sf_creds)
    assert s["database"] == "ALPHAGEN_ETRM"
    assert s["schema"] == "GOLD"
    assert s["warehouse"] == "COMPUTE_WH"
    assert s["password"] == "s3cret-not-real"


@pytest.mark.unit
def test_write_disabled_when_not_configured():
    assert snowflake_write_enabled({"snowflake": {}, "app": {"demo_mode": "false"}}) is False


@pytest.mark.unit
def test_write_disabled_in_demo_mode(sf_creds, monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_WRITE", raising=False)
    demo = {**sf_creds, "app": {"demo_mode": "true", "setup_complete": "true"}}
    assert snowflake_write_enabled(demo) is False


@pytest.mark.unit
def test_write_enabled_live_mode(sf_creds, monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_WRITE", raising=False)
    assert snowflake_write_enabled(sf_creds) is True


@pytest.mark.unit
def test_write_force_true_overrides_demo(sf_creds, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_WRITE", "true")
    demo = {**sf_creds, "app": {"demo_mode": "true"}}
    assert snowflake_write_enabled(demo) is True


@pytest.mark.unit
def test_write_force_false_always_off(sf_creds, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_WRITE", "false")
    assert snowflake_write_enabled(sf_creds) is False


@pytest.mark.unit
def test_read_enabled_even_in_demo(sf_creds, monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_READ", raising=False)
    demo = {**sf_creds, "app": {"demo_mode": "true"}}
    assert snowflake_read_enabled(demo) is True


@pytest.mark.unit
def test_read_disabled_by_env(sf_creds, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_READ", "false")
    assert snowflake_read_enabled(sf_creds) is False


@pytest.mark.unit
def test_fq_table_quoted(sf_creds):
    assert fq_table("LANDING_OPERATOR_BURN_UPDATE", sf_creds) == (
        '"ALPHAGEN_ETRM"."GOLD"."LANDING_OPERATOR_BURN_UPDATE"'
    )


@pytest.mark.unit
def test_fq_table_rejects_injection(sf_creds):
    with pytest.raises(ValueError, match="Invalid Snowflake table"):
        fq_table("LANDING; DROP TABLE X", sf_creds)


@pytest.mark.unit
def test_redact_password_in_message(sf_creds):
    msg = "Login failed for password=s3cret-not-real host=xyz"
    out = redact_secrets(msg, sf_creds)
    assert "s3cret-not-real" not in out
    assert "••••" in out
