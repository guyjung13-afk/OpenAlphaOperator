"""Unit tests for landing row normalization and write/read skip paths."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from spire_reactor.store.landing import (
    _first_num,
    _hour_floor,
    _parse_ritual_at,
    _row_from_ritual,
    fetch_recent_operator_burns,
    insert_operator_burn_update,
)


@pytest.mark.unit
def test_first_num_prefers_first_valid():
    assert _first_num(None, "x", 7.5, default=1.0) == 7.5
    assert _first_num(None, None, default=3.0) == 3.0


@pytest.mark.unit
def test_parse_ritual_at_iso():
    dt = _parse_ritual_at("2026-07-29T15:30:00Z")
    assert dt.tzinfo is not None
    assert dt.year == 2026
    assert dt.hour == 15


@pytest.mark.unit
def test_hour_floor():
    dt = datetime(2026, 7, 29, 15, 45, 30, tzinfo=timezone.utc)
    floored = _hour_floor(dt)
    assert floored.minute == 0
    assert floored.second == 0
    assert floored.hour == 15


@pytest.mark.unit
def test_row_from_ritual_uses_public_values(
    ritual_public, ritual_payload, ritual_burn
):
    row = _row_from_ritual(ritual_public, ritual_payload, ritual_burn)
    assert row["plant_id"] == "DEMO-1"
    assert row["award_mw"] == 500.0
    assert row["actual_burn_mmbtu"] == 3750.0
    assert row["heat_rate"] == 7.5
    assert row["award_mmbtu"] == 500.0 * 7.5 * 1.0
    assert row["operator_id"] == "test-op"
    assert row["source_system"] == "pytest"
    # Secrets stripped from raw payload
    assert "password" not in row["raw_payload"]["payload"]
    assert row["raw_payload"]["payload"].get("notes") == "payload-note"
    assert row["load_id"]  # uuid


@pytest.mark.unit
def test_row_does_not_substitute_estimate_for_actual(
    ritual_public, ritual_payload, ritual_burn
):
    ritual_public = {**ritual_public, "actual_burn_mmbtu": 3600.0}
    ritual_burn = {**ritual_burn, "estimated_burn_mmbtu": 9999.0}
    row = _row_from_ritual(ritual_public, ritual_payload, ritual_burn)
    assert row["actual_burn_mmbtu"] == 3600.0
    assert row["estimated_burn_mmbtu"] == 3750.0  # from public first


@pytest.mark.unit
def test_row_default_award_when_omitted(ritual_burn):
    public = {"plant_id": "P1", "heat_rate": 7.5, "hours": 1.0}
    payload: dict = {}
    row = _row_from_ritual(public, payload, ritual_burn)
    assert row["award_mw"] == 500.0
    assert row["actual_burn_mmbtu"] == 3750.0


@pytest.mark.unit
def test_insert_skipped_not_configured(ritual_public, ritual_payload, ritual_burn):
    out = insert_operator_burn_update(
        ritual_public,
        ritual_payload,
        ritual_burn,
        creds={"snowflake": {}, "app": {"demo_mode": "false"}},
    )
    assert out["ok"] is False
    assert out["skipped"] is True
    assert out["reason"] == "not_configured"


@pytest.mark.unit
def test_insert_skipped_demo_mode(sf_creds, ritual_public, ritual_payload, ritual_burn, monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_WRITE", raising=False)
    demo = {**sf_creds, "app": {"demo_mode": "true"}}
    out = insert_operator_burn_update(
        ritual_public, ritual_payload, ritual_burn, creds=demo
    )
    assert out["ok"] is False
    assert out["skipped"] is True
    assert out["reason"] == "demo_mode"


@pytest.mark.unit
def test_insert_success_mocked_connection(
    sf_creds, ritual_public, ritual_payload, ritual_burn, monkeypatch
):
    monkeypatch.setenv("SNOWFLAKE_WRITE", "true")
    monkeypatch.setenv("SNOWFLAKE_STAGING_WRITE", "false")

    mock_cur = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_conn
    mock_cm.__exit__.return_value = False

    with patch("spire_reactor.store.landing.connection", return_value=mock_cm):
        out = insert_operator_burn_update(
            ritual_public,
            ritual_payload,
            ritual_burn,
            creds=sf_creds,
            dual_write_staging=False,
        )

    assert out["ok"] is True
    assert out["skipped"] is False
    assert out["load_id"]
    assert out["staging_written"] is False
    assert "LANDING_OPERATOR_BURN_UPDATE" in out["landing_table"]
    mock_cur.execute.assert_called()
    mock_conn.commit.assert_called_once()


@pytest.mark.unit
def test_fetch_skipped_not_configured():
    out = fetch_recent_operator_burns(creds={"snowflake": {}})
    assert out["ok"] is False
    assert out["skipped"] is True
    assert out["rows"] == []
    assert out["reason"] == "not_configured"


@pytest.mark.unit
def test_fetch_success_mocked(sf_creds, monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_READ", raising=False)

    mock_cur = MagicMock()
    mock_cur.description = [
        ("LOAD_ID",),
        ("LOAD_TS",),
        ("RITUAL_AT",),
        ("PLANT_ID",),
        ("HEAT_RATE",),
        ("AWARD_MW",),
        ("AWARD_MMBTU",),
        ("ACTUAL_BURN_MMBTU",),
        ("ESTIMATED_BURN_MMBTU",),
        ("VARIANCE_PCT",),
        ("NEW_ACCUM_MMBTU",),
        ("HOURS",),
        ("PCI_STATUS",),
        ("ETRM_STATUS",),
        ("ETRM_ACTION",),
        ("OUTCOME",),
        ("NOTES",),
        ("RITUAL_NAME",),
        ("SOURCE_SYSTEM",),
        ("OPERATOR_ID",),
    ]
    mock_cur.fetchall.return_value = [
        (
            "abc-123",
            datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc),
            "DEMO-1",
            7.5,
            500.0,
            3750.0,
            3750.0,
            3750.0,
            0.0,
            3750.0,
            1.0,
            "GREEN",
            "COMPLIANT",
            "NONE",
            "OK",
            "note",
            "gas_burn_update",
            "pytest",
            "op1",
        )
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_conn
    mock_cm.__exit__.return_value = False

    with patch("spire_reactor.store.landing.connection", return_value=mock_cm):
        out = fetch_recent_operator_burns(limit=10, plant_id="DEMO-1", creds=sf_creds)

    assert out["ok"] is True
    assert out["count"] == 1
    assert out["rows"][0]["plant_id"] == "DEMO-1"
    assert out["rows"][0]["pci_status"] == "GREEN"
    assert "load_id" in out["rows"][0]
