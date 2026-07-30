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
