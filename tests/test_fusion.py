"""Unit tests for PCI/ETRM rule fusion (no network)."""

from __future__ import annotations

import pytest
from spire_reactor.activities.fusion import _rule_fusion, fuse_pci_etrm_sync


@pytest.mark.unit
def test_rule_fusion_green():
    out = _rule_fusion(
        {
            "pci_status": "GREEN",
            "etrm_status": "COMPLIANT",
            "etrm_action": "NONE",
            "deviation_pct": 0.5,
        }
    )
    assert out["action"] == "NONE"
    assert out["severity"] == "info"
    assert out["source"] == "rules"
    assert "GREEN" in out["provider_msg"]


@pytest.mark.unit
def test_rule_fusion_amber_defaults_propagate():
    out = _rule_fusion(
        {
            "pci_status": "AMBER",
            "etrm_status": "REVIEW",
            "etrm_action": "NONE",
            "deviation_pct": 3.2,
        }
    )
    assert out["action"] == "PROPAGATE_ALERT"
    assert out["severity"] == "warning"


@pytest.mark.unit
def test_rule_fusion_amber_keeps_etrm_action():
    out = _rule_fusion(
        {
            "pci_status": "AMBER",
            "etrm_status": "REVIEW",
            "etrm_action": "HOLD_DISPATCH",
            "deviation_pct": 4.0,
        }
    )
    assert out["action"] == "HOLD_DISPATCH"
    assert out["severity"] == "warning"


@pytest.mark.unit
def test_rule_fusion_red_critical():
    out = _rule_fusion(
        {
            "pci_status": "RED",
            "etrm_status": "BREACH",
            "etrm_action": "NONE",
            "deviation_pct": -12.0,
        }
    )
    assert out["action"] == "PROPAGATE_CRITICAL"
    assert out["severity"] == "critical"
    assert "RED" in out["provider_msg"]


@pytest.mark.unit
def test_fuse_sync_no_redis_no_webhook(monkeypatch, ritual_public):
    """Without Redis/webhook, fusion still returns ok with skip/fail status."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")  # refuse connection
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    out = fuse_pci_etrm_sync(ritual_public)
    assert out["ok"] is True
    assert out["action"] == "NONE"
    assert out["severity"] == "info"
    # webhook not configured
    assert out["webhook"]["skipped"] is True
    # redis may fail (ok False) — still non-fatal
    assert "redis" in out
