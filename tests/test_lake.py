"""Unit tests for read-only lake mapping helpers."""

from __future__ import annotations

import pytest
from spire_reactor.store.lake import (
    map_lake_row_to_desk,
    map_lake_row_to_ritual_payload,
    pci_band_from_variance,
    variance_pct_from_lake,
)


@pytest.mark.unit
def test_variance_pct_from_da_and_burn_var():
    pct = variance_pct_from_lake(
        {"da_burn_mmbtu": 1000.0, "burn_variance_mmbtu": -50.0, "rt_burn_mmbtu": 950.0}
    )
    assert abs(pct - (-5.0)) < 1e-6


@pytest.mark.unit
def test_pci_bands():
    assert pci_band_from_variance(0.0) == "GREEN"
    assert pci_band_from_variance(4.9) == "GREEN"
    assert pci_band_from_variance(7.0) == "AMBER"
    assert pci_band_from_variance(12.0) == "RED"


@pytest.mark.unit
def test_map_lake_row_to_desk():
    row = {
        "UNIT_NAME": "LINDEN 18 KV 2001 GEN GEN",
        "FLEET_NAME": "PS LINDEN 2 CC",
        "PIPELINE": "TEXAS_EASTERN",
        "OPERATING_DATE": "2026-07-29",
        "HE": 18,
        "DAM_MW": 509.0,
        "RT_MW": -16.7,
        "HEAT_RATE": 6.6171,
        "DA_BURN_MMBTU": 3368.1,
        "RT_BURN_MMBTU": -110.51,
        "BURN_VARIANCE_MMBTU": -3478.61,
    }
    desk = map_lake_row_to_desk(row)
    assert desk["plant_id"] == "LINDEN 18 KV 2001 GEN GEN"
    assert desk["award_mw"] == 509.0
    assert desk["heat_rate"] == 6.6171
    assert desk["source_system"] == "snowflake_lake"
    assert desk["pci_status"] in ("GREEN", "AMBER", "RED")
    payload = map_lake_row_to_ritual_payload(desk)
    assert payload["plant_id"] == desk["plant_id"]
    assert payload["award_mw"] == 509.0


@pytest.mark.unit
def test_truth_envelopes_from_desk_lake_sourced():
    from spire_reactor.store.lake import (
        session_fields_from_desk,
        truth_envelopes_from_desk,
    )

    desk = map_lake_row_to_desk(
        {
            "UNIT_NAME": "PS BERGEN 2CC F",
            "FLEET_NAME": "BERGEN",
            "PIPELINE": "TRANSCO",
            "OPERATING_DATE": "2026-07-29",
            "HE": 10,
            "DAM_MW": 400.0,
            "RT_MW": 380.0,
            "HEAT_RATE": 7.1,
            "HEAT_RATE_CONFIG": "2x1",
            "DA_BURN_MMBTU": 2800.0,
            "RT_BURN_MMBTU": 2750.0,
            "BURN_VARIANCE_MMBTU": -50.0,
            "CONFIG_MW": 450.0,
            "ECO_MAX_RT_MW": 500.0,
        }
    )
    # attach raw for eco/config
    desk["_lake"] = {
        "config_mw": 450.0,
        "eco_max_rt_mw": 500.0,
    }
    env = truth_envelopes_from_desk(desk)
    assert env["source"] == "lake"
    assert env["p50"] > 0
    assert env["p90"] <= env["p50"] + 50  # stressed ≤ roughly base
    assert env["dam_mw"] == 400.0
    assert env["nameplate_mw"] == 500.0
    assert env["unit_name"] == "PS BERGEN 2CC F"
    fields = session_fields_from_desk(desk)
    assert fields["plant_id"] == "PS BERGEN 2CC F"
    assert fields["award_mw"] == 400.0
    assert fields["heat_rate"] == 7.1
