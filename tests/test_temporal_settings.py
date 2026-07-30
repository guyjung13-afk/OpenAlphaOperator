"""Unit tests for Temporal settings / dispatch gates."""

from __future__ import annotations

import pytest
from spire_reactor.temporal.settings import (
    connect_kwargs,
    is_temporal_configured,
    temporal_dispatch_enabled,
    temporal_settings,
    temporal_tls_enabled,
)


@pytest.mark.unit
def test_not_configured_without_host():
    assert is_temporal_configured({"temporal": {}}) is False


@pytest.mark.unit
def test_not_configured_placeholder_host():
    assert is_temporal_configured({"temporal": {"host": "placeholder"}}) is False
    assert is_temporal_configured({"temporal": {"host": "localhost:0000"}}) is False


@pytest.mark.unit
def test_configured_local_host():
    assert is_temporal_configured({"temporal": {"host": "localhost:7233"}}) is True


@pytest.mark.unit
def test_settings_from_creds():
    s = temporal_settings(
        {
            "temporal": {
                "host": "us-central1.gcp.api.temporal.io:7233",
                "namespace": "prod.abc",
                "api_key": "tk_test",
            }
        }
    )
    assert s["host"].startswith("us-central1")
    assert s["namespace"] == "prod.abc"
    assert s["api_key"] == "tk_test"
    assert s["task_queue"] == "spire-reactor"


@pytest.mark.unit
def test_dispatch_disabled_by_use_false(monkeypatch):
    monkeypatch.setenv("TEMPORAL_USE", "false")
    assert (
        temporal_dispatch_enabled({"temporal": {"host": "localhost:7233"}}) is False
    )


@pytest.mark.unit
def test_dispatch_force_true(monkeypatch):
    monkeypatch.setenv("TEMPORAL_USE", "true")
    assert (
        temporal_dispatch_enabled({"temporal": {"host": "localhost:7233"}}) is True
    )


@pytest.mark.unit
def test_dispatch_follows_demo_mode(monkeypatch):
    monkeypatch.delenv("TEMPORAL_USE", raising=False)
    live = {
        "temporal": {"host": "localhost:7233"},
        "app": {"demo_mode": "false"},
    }
    demo = {
        "temporal": {"host": "localhost:7233"},
        "app": {"demo_mode": "true"},
    }
    assert temporal_dispatch_enabled(live) is True
    assert temporal_dispatch_enabled(demo) is False


@pytest.mark.unit
def test_tls_off_for_localhost():
    assert temporal_tls_enabled({"host": "localhost:7233", "api_key": ""}) is False


@pytest.mark.unit
def test_tls_on_with_api_key():
    assert (
        temporal_tls_enabled(
            {
                "host": "us-central1.gcp.api.temporal.io:7233",
                "api_key": "tk_x",
            }
        )
        is True
    )


@pytest.mark.unit
def test_tls_on_for_cloud_host_without_key():
    assert (
        temporal_tls_enabled(
            {"host": "namespace.tmprl.cloud:7233", "api_key": ""}
        )
        is True
    )


@pytest.mark.unit
def test_connect_kwargs_local(monkeypatch):
    monkeypatch.setenv("TEMPORAL_TLS", "false")
    kwargs = connect_kwargs({"temporal": {"host": "localhost:7233", "namespace": "default"}})
    assert kwargs["target_host"] == "localhost:7233"
    assert kwargs["namespace"] == "default"
    assert "api_key" not in kwargs
    assert kwargs.get("tls") is not True


@pytest.mark.unit
def test_connect_kwargs_cloud():
    kwargs = connect_kwargs(
        {
            "temporal": {
                "host": "us-central1.gcp.api.temporal.io:7233",
                "namespace": "ns",
                "api_key": "tk_abc",
            }
        }
    )
    assert kwargs["api_key"] == "tk_abc"
    assert kwargs["tls"] is True


@pytest.mark.unit
def test_connect_kwargs_requires_host():
    with pytest.raises(ValueError, match="not configured"):
        connect_kwargs({"temporal": {}})
