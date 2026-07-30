"""Unit tests for integration catalog and demo/setup flags."""

from __future__ import annotations

import pytest
from spire_reactor.config.integrations import (
    INTEGRATIONS,
    is_demo_mode,
    is_setup_complete,
)


@pytest.mark.unit
def test_catalog_has_core_live_ids():
    ids = {i["id"] for i in INTEGRATIONS}
    for expected in ("snowflake", "eia", "redis", "temporal", "open_meteo"):
        assert expected in ids


@pytest.mark.unit
def test_snowflake_fields_present():
    sf = next(i for i in INTEGRATIONS if i["id"] == "snowflake")
    keys = {f["key"] for f in sf["fields"]}
    assert {"account", "user", "password", "warehouse", "database", "schema"} <= keys
    assert sf["phase"] == "live"
    assert sf["auth_required"] is True


@pytest.mark.unit
def test_is_demo_mode_default_true():
    assert is_demo_mode({"app": {}}) is True
    assert is_demo_mode({"app": {"demo_mode": "true"}}) is True


@pytest.mark.unit
def test_is_demo_mode_false():
    assert is_demo_mode({"app": {"demo_mode": "false"}}) is False
    assert is_demo_mode({"app": {"demo_mode": "0"}}) is False


@pytest.mark.unit
def test_is_setup_complete():
    assert is_setup_complete({"app": {"setup_complete": "true"}}) is True
    assert is_setup_complete({"app": {"setup_complete": "false"}}) is False
    assert is_setup_complete({"app": {}}) is False
