"""
AlphaGen Commercial Truth Cockpit — Desk Operator View (Hybrid v1)

- Governance banner + Five Truths UI (commercial desk framing)
- Lake-grounded envelopes from V_CALCULATED_GAS_BURN (read-only ingest)
- Unit picker from Snowflake lake + local ritual compute
- Optional public-feeds prefill (Open-Meteo / synthetic)
- Append-only session audit + operator acknowledgment
- Integration status strip → re-open Setup stage
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import plotly.express as px
import streamlit as st
from spire_reactor.main import trigger_ritual  # noqa: E402
from spire_reactor.store.lake import (  # noqa: E402
    fetch_latest_lake_gas_burn,
    list_lake_units,
    session_fields_from_desk,
    truth_envelopes_from_desk,
)

from dashboard.setup_stage import render_integration_status_strip

# ── defaults / identity ──────────────────────────────────────────────
DEFAULT_PLANT = os.getenv("DEMO_PLANT_ID", "Linda 1 (Gas)")
DEFAULT_OPERATOR = os.getenv("DEMO_OPERATOR", "Desk Operator")
DEFAULT_SHIFT = os.getenv("DEMO_SHIFT", "Day")
LAKE_LABEL = os.getenv("SNOWFLAKE_LAKE_SOURCE", "V_CALCULATED_GAS_BURN")
MANUAL_UNIT = "— Manual plant id —"


def _init_state() -> None:
    if "ready" in st.session_state:
        return
    st.session_state.ready = True
    st.session_state.plant_id = DEFAULT_PLANT
    st.session_state.operator = DEFAULT_OPERATOR
    st.session_state.shift = DEFAULT_SHIFT
    st.session_state.heat_rate = 7.2
    st.session_state.award_mw = 480.0
    st.session_state.actual_burn = 3450.0
    st.session_state.notes = "Exhaust spread widening slightly"
    st.session_state.hours = 1.0
    st.session_state.pci_status = "GREEN"
    st.session_state.etrm_status = "COMPLIANT"
    st.session_state.etrm_action = "NONE"
    st.session_state.deviation_pct = 0.0
    st.session_state.estimated_burn = 3456.0
    st.session_state.last_ritual = None
    st.session_state.last_update = None
    st.session_state.prev_update = None
    st.session_state.pending_ack = None
    st.session_state.history = []
    st.session_state.audit = []
    st.session_state.feed_meta = None
    st.session_state.last_snowflake = None
    st.session_state.sor_cache = None
    # Lake-driven desk
    st.session_state.lake_units = None  # list[str] | None = not loaded yet
    st.session_state.lake_units_msg = ""
    st.session_state.lake_truth_row = None  # latest desk row for selected unit
    st.session_state.truth_source = "demo"  # "lake" | "demo" | "ritual"
    st.session_state.unit_picker = MANUAL_UNIT
    st.session_state._last_loaded_unit = None


def truth_card(
    title: str,
    value: str,
    confidence: str,
    trend: str,
    explanation: str,
    color: str,
) -> None:
    st.markdown(
        f"""
    <div style="background-color:#0f172a; border-left:6px solid {color}; padding:14px;
                border-radius:8px; margin-bottom:12px; height:100%;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <div><b>{title}</b></div>
            <div style="font-size:0.85em; color:#94a3b8;">{confidence}</div>
        </div>
        <div style="font-size:1.55em; font-weight:600; margin:6px 0;">{value}</div>
        <div style="font-size:0.9em; color:#64748b;">{trend}</div>
        <div style="margin-top:8px; font-size:0.82em; color:#cbd5e1; line-height:1.35;">{explanation}</div>
    </div>
    """,
        unsafe_allow_html=True,
    )


def band_color(pci_status: str) -> str:
    return {"GREEN": "#22c55e", "AMBER": "#eab308", "RED": "#ef4444"}.get(
        pci_status, "#64748b"
    )


def demo_envelopes(
    award_mw: float,
    heat_rate: float,
    pci_status: str,
    deviation_pct: float,
) -> dict[str, Any]:
    """
    Fallback envelopes when lake is unavailable.
    Explicitly illustrative — not SCADA or bid guidance.
    """
    derate = {"GREEN": 0.0, "AMBER": 0.03, "RED": 0.08}.get(pci_status, 0.02)
    stress = min(abs(deviation_pct) / 100.0, 0.12)
    p50 = round(award_mw * (0.86 - derate), 0)
    p90 = round(award_mw * (0.83 - derate - stress * 0.5), 0)
    p99 = round(award_mw * (0.80 - derate - stress), 0)
    ramp_full = 7.5
    ramp_desk = round(ramp_full * (1.0 - derate - stress * 0.4), 1)
    start_12 = max(80, round(96 - derate * 100 - stress * 40, 0))
    start_36 = max(75, round(start_12 - 5, 0))
    min_nom = 185
    min_p95 = round(min_nom + 10 + derate * 40 + stress * 20, 0)
    rel_full = max(70, round(94 - derate * 80 - stress * 50, 0))
    prob_derate = min(25, round(6 + derate * 100 + stress * 80, 0))
    return {
        "source": "demo",
        "p50": int(p50),
        "p90": int(p90),
        "p99": int(p99),
        "ramp_full": ramp_full,
        "ramp_desk": ramp_desk,
        "start_12": int(start_12),
        "start_36": int(start_36),
        "min_nom": min_nom,
        "min_p95": int(min_p95),
        "rel_full": int(rel_full),
        "prob_derate": int(prob_derate),
        "heat_rate": heat_rate,
        "dam_mw": award_mw,
        "rt_mw": 0.0,
    }


def ensure_lake_units(*, force: bool = False) -> list[str]:
    """Load distinct lake units once per session (or on force refresh)."""
    if not force and st.session_state.lake_units is not None:
        return list(st.session_state.lake_units or [])
    try:
        result = list_lake_units(limit=200)
        if result.get("ok"):
            st.session_state.lake_units = list(result.get("units") or [])
            st.session_state.lake_units_msg = result.get("message") or "ok"
        else:
            st.session_state.lake_units = []
            st.session_state.lake_units_msg = result.get("message") or "unavailable"
    except Exception as exc:  # noqa: BLE001
        st.session_state.lake_units = []
        st.session_state.lake_units_msg = str(exc)[:160]
    return list(st.session_state.lake_units or [])


def apply_lake_desk_to_session(desk: dict[str, Any], *, as_truth: bool = True) -> None:
    """Push lake desk row into operator form + optional truth source flag."""
    fields = session_fields_from_desk(desk)
    if not fields:
        return
    if fields.get("plant_id"):
        st.session_state.plant_id = fields["plant_id"]
    st.session_state.heat_rate = float(fields.get("heat_rate") or st.session_state.heat_rate)
    st.session_state.award_mw = float(fields.get("award_mw") or 0.0)
    st.session_state.actual_burn = float(fields.get("actual_burn") or 0.0)
    st.session_state.estimated_burn = float(fields.get("estimated_burn") or 0.0)
    st.session_state.pci_status = str(fields.get("pci_status") or "GREEN")
    st.session_state.deviation_pct = float(fields.get("deviation_pct") or 0.0)
    st.session_state.hours = float(fields.get("hours") or 1.0)
    # Derived ETRM band from PCI (desk convention)
    pci = st.session_state.pci_status
    if pci == "GREEN":
        st.session_state.etrm_status = "COMPLIANT"
        st.session_state.etrm_action = "NONE"
    elif pci == "AMBER":
        st.session_state.etrm_status = "REVIEW"
        st.session_state.etrm_action = "PROPAGATE_ALERT"
    else:
        st.session_state.etrm_status = "BREACH"
        st.session_state.etrm_action = "PROPAGATE_CRITICAL"
    note_bits = [
        f"Lake {LAKE_LABEL}",
        f"HE={fields.get('he')}",
        f"date={fields.get('operating_date')}",
    ]
    if fields.get("pipeline"):
        note_bits.append(f"pipe={fields.get('pipeline')}")
    if fields.get("heat_rate_config"):
        note_bits.append(str(fields.get("heat_rate_config")))
    st.session_state.notes = " · ".join(str(b) for b in note_bits if b)
    st.session_state.lake_truth_row = desk
    if as_truth:
        st.session_state.truth_source = "lake"


def load_lake_truth_for_unit(unit: str, *, force: bool = False) -> Optional[dict[str, Any]]:
    """Fetch latest lake row for unit and apply to session."""
    unit = (unit or "").strip()
    if not unit or unit == MANUAL_UNIT:
        return None
    if (
        not force
        and st.session_state.get("_last_loaded_unit") == unit
        and st.session_state.lake_truth_row
    ):
        return st.session_state.lake_truth_row
    try:
        result = fetch_latest_lake_gas_burn(unit_name=unit)
    except Exception:  # noqa: BLE001
        return None
    if not result.get("ok") or not result.get("row"):
        st.session_state.lake_truth_row = None
        return None
    desk = result["row"]
    apply_lake_desk_to_session(desk, as_truth=True)
    st.session_state._last_loaded_unit = unit
    st.session_state.sor_cache = None  # force lake table refresh later
    return desk


def resolve_truth_envelopes() -> dict[str, Any]:
    """
    Prefer lake envelopes when a desk row is loaded.
    After an operator ritual, keep session-driven envelopes until ↻ Truths reloads lake.
    """
    src = st.session_state.get("truth_source") or "demo"
    if src == "ritual":
        env = demo_envelopes(
            st.session_state.award_mw,
            st.session_state.heat_rate,
            st.session_state.pci_status,
            st.session_state.deviation_pct,
        )
        env["source"] = "ritual"
        return env

    desk = st.session_state.lake_truth_row
    if isinstance(desk, dict) and desk:
        env = truth_envelopes_from_desk(desk)
        if env.get("source") == "lake":
            st.session_state.truth_source = "lake"
            return env

    env = demo_envelopes(
        st.session_state.award_mw,
        st.session_state.heat_rate,
        st.session_state.pci_status,
        st.session_state.deviation_pct,
    )
    st.session_state.truth_source = "demo"
    return env


def apply_ritual_result(result: dict[str, Any], inputs: dict[str, Any]) -> None:
    burn = result.get("result") or {}
    st.session_state.pci_status = result.get("pci_status") or burn.get("pci_status", "GREEN")
    st.session_state.etrm_status = result.get("etrm_status", "COMPLIANT")
    st.session_state.etrm_action = result.get("etrm_action", "NONE")
    st.session_state.deviation_pct = float(result.get("deviation_pct") or 0.0)
    st.session_state.estimated_burn = float(
        result.get("estimated_burn_mmbtu") or burn.get("estimated_burn_mmbtu") or 0.0
    )
    st.session_state.last_ritual = datetime.now()
    st.session_state.heat_rate = float(inputs["heat_rate"])
    st.session_state.award_mw = float(inputs["award_mw"])
    st.session_state.actual_burn = float(inputs["actual_burn"])
    st.session_state.notes = str(inputs.get("notes") or "")
    st.session_state.last_snowflake = result.get("snowflake")
    # Ritual judgment layers on top of lake facts for this session
    st.session_state.truth_source = "ritual"
    # Invalidate lake table cache (local audit only; no SF write)
    st.session_state.sor_cache = None

    snapshot = {
        "ts": datetime.now(),
        "heat_rate": inputs["heat_rate"],
        "award_mw": inputs["award_mw"],
        "actual_burn": inputs["actual_burn"],
        "notes": inputs.get("notes") or "",
        "pci_status": st.session_state.pci_status,
        "etrm_status": st.session_state.etrm_status,
        "etrm_action": st.session_state.etrm_action,
        "deviation_pct": st.session_state.deviation_pct,
        "estimated_burn": st.session_state.estimated_burn,
        "outcome": result.get("outcome"),
        "mode": result.get("mode"),
        "plant_id": st.session_state.plant_id,
        "operator": st.session_state.operator,
        "snowflake_ok": (result.get("snowflake") or {}).get("ok"),
        "snowflake_load_id": (result.get("snowflake") or {}).get("load_id"),
    }
    if st.session_state.last_update is not None:
        st.session_state.prev_update = dict(st.session_state.last_update)
    st.session_state.last_update = snapshot
    st.session_state.pending_ack = snapshot

    st.session_state.history.append(
        {
            "time": datetime.now(),
            "pci": float(inputs["heat_rate"]),
            "status": st.session_state.etrm_status,
            "deviation": st.session_state.deviation_pct,
            "pci_band": st.session_state.pci_status,
        }
    )


def render_cockpit() -> None:
    """Main desk operator view (called from app.py after Setup gate)."""
    _init_state()

    # ── sidebar ──────────────────────────────────────────────────────────
    with st.sidebar:
        st.header("Shift context")

        units = ensure_lake_units()
        unit_options = [MANUAL_UNIT] + units
        # Keep picker aligned with plant_id when plant is a known unit
        if st.session_state.plant_id in units:
            st.session_state.unit_picker = st.session_state.plant_id
        elif st.session_state.unit_picker not in unit_options:
            st.session_state.unit_picker = MANUAL_UNIT

        picker_idx = (
            unit_options.index(st.session_state.unit_picker)
            if st.session_state.unit_picker in unit_options
            else 0
        )
        chosen = st.selectbox(
            "Unit (lake)",
            unit_options,
            index=picker_idx,
            help=f"Units from {LAKE_LABEL}. Selecting a unit loads latest lake HE into Five Truths.",
        )
        st.session_state.unit_picker = chosen

        col_u1, col_u2 = st.columns(2)
        with col_u1:
            refresh_units = st.button("↻ Units", use_container_width=True)
        with col_u2:
            reload_truth = st.button("↻ Truths", use_container_width=True)

        if refresh_units:
            ensure_lake_units(force=True)
            st.rerun()

        if chosen != MANUAL_UNIT:
            st.session_state.plant_id = chosen
            if (
                reload_truth
                or st.session_state.get("_last_loaded_unit") != chosen
                or st.session_state.lake_truth_row is None
            ):
                load_lake_truth_for_unit(chosen, force=reload_truth)
            st.caption(
                f"{len(units)} lake unit(s). "
                f"{st.session_state.lake_units_msg or ''}".strip()
            )
        else:
            st.session_state.plant_id = st.text_input(
                "Plant (manual)",
                st.session_state.plant_id
                if st.session_state.plant_id != MANUAL_UNIT
                else DEFAULT_PLANT,
            )
            st.caption("Manual mode — Five Truths fall back to demo envelopes unless you prefill lake.")

        st.session_state.operator = st.text_input("Operator", st.session_state.operator)
        st.session_state.shift = st.selectbox(
            "Shift",
            ["Day", "Night", "Swing"],
            index=["Day", "Night", "Swing"].index(st.session_state.shift)
            if st.session_state.shift in ("Day", "Night", "Swing")
            else 0,
        )
        st.markdown("---")
        render_integration_status_strip()
        st.markdown("---")
        src = st.session_state.get("truth_source") or "demo"
        st.caption(
            f"Truth source: **{src}**. Lake ingest read-only (`{LAKE_LABEL}`). "
            "Rituals compute locally + session audit. No Snowflake writes."
        )

    # ── Auto-pick first lake unit on first successful unit load ──────────
    if (
        st.session_state.lake_units
        and st.session_state.unit_picker == MANUAL_UNIT
        and st.session_state.plant_id == DEFAULT_PLANT
        and st.session_state.lake_truth_row is None
    ):
        first = st.session_state.lake_units[0]
        st.session_state.unit_picker = first
        st.session_state.plant_id = first
        load_lake_truth_for_unit(first, force=True)
        st.rerun()

    # ── governance header ────────────────────────────────────────────────
    st.markdown(
        """
<div style="background-color:#1a1a2e; padding:12px; border-radius:8px; margin-bottom:16px;">
<b>⚖️ GOVERNED DECISION SUPPORT — GENERATION / ASSET MANAGEMENT</b><br>
Advisory only. No bids generated. No bid prices recommended.
Operator acknowledgment required. Session audit always; Snowflake lake <b>read-only ingest</b>.
</div>
""",
        unsafe_allow_html=True,
    )

    st.title("Commercial Truth Cockpit — Desk Operator View")
    truth_src = st.session_state.get("truth_source") or "demo"
    lake_meta = st.session_state.lake_truth_row or {}
    he_note = ""
    if lake_meta:
        he_note = f" | Lake HE {lake_meta.get('he')} @ {lake_meta.get('operating_date')}"
    st.caption(
        f"Last refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"Unit: {st.session_state.plant_id} | Shift: {st.session_state.shift} | "
        f"Operator: {st.session_state.operator} | Truths: **{truth_src}**{he_note}"
    )

    # ── live PCI / ETRM strip ─────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("PCI band", st.session_state.pci_status)
    c2.metric("ETRM", st.session_state.etrm_status)
    c3.metric("Variance %", f"{st.session_state.deviation_pct:+.2f}%")
    c4.metric("DA / est. burn (MMBtu)", f"{st.session_state.estimated_burn:,.0f}")
    c5.metric(
        "Last ritual",
        st.session_state.last_ritual.strftime("%H:%M:%S")
        if st.session_state.last_ritual
        else ("lake" if truth_src == "lake" else "—"),
    )

    # ── operator inputs ──────────────────────────────────────────────────
    with st.expander("Operator Inputs (Manual + Live public feeds)", expanded=True):
        st.caption(
            "Hourly burn update. Recalc uses Spire `gas_burn_update` (same math as the reactor API). "
            "Live feeds prefill from Open-Meteo (± EIA if keyed) — not plant SCADA."
        )

        b1, b2 = st.columns(2)
        with b1:
            if st.button("Prefill from public feeds", use_container_width=True):
                try:
                    from spire_reactor.ingest.public_feeds import fetch_demo_snapshot

                    snap = fetch_demo_snapshot()
                    payload = snap.get("operator_payload") or {}
                    st.session_state.heat_rate = float(payload.get("heat_rate") or 7.5)
                    st.session_state.award_mw = float(payload.get("award_mw") or 480.0)
                    st.session_state.actual_burn = float(
                        payload.get("actual_burn_mmbtu") or 3450.0
                    )
                    st.session_state.notes = str(payload.get("notes") or "")
                    st.session_state.feed_meta = {
                        "weather_ok": (snap.get("weather") or {}).get("ok"),
                        "temp_c": (snap.get("weather") or {}).get("temperature_c"),
                        "ng_ok": (snap.get("natural_gas") or {}).get("ok"),
                        "ng_price": (snap.get("natural_gas") or {}).get("price_usd_mmbtu"),
                        "fetched_at": snap.get("fetched_at"),
                    }
                    st.success("Inputs prefilled from public feeds. Review, then submit.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Public feed prefill failed: {exc}")
        with b2:
            if st.button("Prefill synthetic (offline)", use_container_width=True):
                from spire_reactor.ingest.public_feeds import synthetic_tick

                snap = synthetic_tick()
                payload = snap.get("operator_payload") or {}
                st.session_state.heat_rate = float(payload.get("heat_rate") or 7.5)
                st.session_state.award_mw = float(payload.get("award_mw") or 480.0)
                st.session_state.actual_burn = float(
                    payload.get("actual_burn_mmbtu") or 3450.0
                )
                st.session_state.notes = str(payload.get("notes") or "synthetic tick")
                st.session_state.feed_meta = {
                    "mode": "synthetic",
                    "fetched_at": snap.get("fetched_at"),
                }
                st.success("Synthetic prefill applied.")
                st.rerun()

        if st.session_state.feed_meta:
            st.caption(f"Last feed meta: {st.session_state.feed_meta}")

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            heat_rate = st.number_input(
                "Heat Rate (MMBtu/MWh)",
                value=float(st.session_state.heat_rate),
                step=0.1,
                format="%.1f",
                key="in_hr",
            )
        with col2:
            award_mw = st.number_input(
                "Day-Ahead Award (MW)",
                value=float(st.session_state.award_mw),
                step=10.0,
                key="in_award",
            )
        with col3:
            actual_burn = st.number_input(
                "Actual Burn (MMBtu / period)",
                value=float(st.session_state.actual_burn),
                step=50.0,
                key="in_burn",
                help="For hourly ritual, this is MMBtu for the period (hours=1).",
            )
        with col4:
            hours = st.number_input(
                "Hours in period",
                value=float(st.session_state.hours),
                min_value=0.25,
                step=0.25,
                key="in_hours",
            )

        notes = st.text_area(
            "Notes / Near-miss flags",
            value=st.session_state.notes,
            height=68,
            key="in_notes",
        )

        if st.button(
            "Submit Update → Recalculate Truths",
            type="primary",
            use_container_width=True,
        ):
            inputs = {
                "heat_rate": float(heat_rate),
                "award_mw": float(award_mw),
                "actual_burn": float(actual_burn),
                "hours": float(hours),
                "notes": notes,
            }
            payload = {
                "plant_id": st.session_state.plant_id,
                "heat_rate": inputs["heat_rate"],
                "award_mw": inputs["award_mw"],
                "actual_burn_mmbtu": inputs["actual_burn"],
                "hours": inputs["hours"],
                "notes": inputs["notes"],
            }
            result = trigger_ritual("gas_burn_update", payload)
            if result.get("status") != "success":
                st.error(result.get("message") or "Ritual failed")
            else:
                apply_ritual_result(result, inputs)
                st.session_state.hours = inputs["hours"]
                sf = result.get("snowflake") or {}
                if sf.get("ok"):
                    sf_note = f" Optional SF write OK ({str(sf.get('load_id') or '')[:8]}…)."
                elif sf.get("skipped"):
                    reason = sf.get("reason") or "n/a"
                    if reason == "read_only_lake":
                        sf_note = " Lake mode: no SF write (ingest-only)."
                    else:
                        sf_note = f" SF write skipped ({reason})."
                else:
                    sf_note = f" SF write issue: {sf.get('message') or 'see logs'}."
                orch = result.get("orchestrator") or "local"
                fusion = result.get("fusion") or {}
                if orch == "temporal" or result.get("workflow_id"):
                    orch_note = f" Temporal workflow `{result.get('workflow_id') or 'ok'}`."
                elif result.get("temporal_fallback"):
                    orch_note = " Temporal failed → local fallback."
                else:
                    orch_note = " Local ritual."
                if fusion.get("provider_msg"):
                    orch_note += f" Fusion: {fusion.get('action') or '—'}"
                st.success(
                    f"Ritual complete — PCI {st.session_state.pci_status}, "
                    f"ETRM {st.session_state.etrm_status}, "
                    f"variance {st.session_state.deviation_pct:+.2f}%."
                    f"{sf_note}{orch_note} Acknowledge below for session audit."
                )
                st.rerun()

    # ── five commercial truths (lake-grounded when available) ────────────
    st.divider()
    st.subheader("Five Commercial Truths — Confidence-Weighted Operating Envelopes")

    env = resolve_truth_envelopes()
    env_src = str(env.get("source") or st.session_state.get("truth_source") or "demo")
    tag = "Lake" if env_src == "lake" else ("Ritual" if env_src == "ritual" else "Demo")
    if env_src == "lake":
        st.caption(
            f"Grounded in **{LAKE_LABEL}** for unit `{env.get('unit_name') or st.session_state.plant_id}` "
            f"(HE={env.get('he')}, date={env.get('operating_date')}, "
            f"DAM={env.get('dam_mw')}, RT={env.get('rt_mw')}, HR={env.get('heat_rate')}). "
            "Advisory envelopes only — not SCADA setpoints, not bids."
        )
    elif env_src == "ritual":
        st.caption(
            "Envelopes follow the **last local ritual** (operator judgment on form inputs). "
            "Re-select a lake unit or hit ↻ Truths to re-anchor from the data lake."
        )
    else:
        st.caption(
            "Demo envelopes (lake unavailable or manual plant). "
            "Configure Snowflake in Setup and pick a lake unit for live commercial truth."
        )

    accent = band_color(st.session_state.pci_status)
    dam_lbl = env.get("dam_mw", st.session_state.award_mw)
    nameplate = env.get("nameplate_mw") or env.get("eco_max_mw") or dam_lbl

    r1c1, r1c2 = st.columns(2)
    with r1c1:
        truth_card(
            f"1. Available MW (Operating Envelope) · {tag}",
            f"P50: {env['p50']} MW | P90: {env['p90']} MW | P99: {env['p99']} MW",
            f"Band {st.session_state.pci_status}",
            f"DAM {float(dam_lbl or 0):.0f} MW · eco/config ~{float(nameplate or 0):.0f} MW · PCI stress",
            "Envelope from lake DAM award / eco max / config when available, stressed by burn variance. "
            "Desk still declares a single number — now lake-aware. Not a bid.",
            accent if st.session_state.pci_status == "GREEN" else "#22c55e",
        )
    with r1c2:
        truth_card(
            f"2. Ramp Capability (Next 6h) · {tag}",
            f"Normal: {env['ramp_full']} MW/min → Desk guidance band: {env['ramp_desk']} MW/min",
            "High" if st.session_state.pci_status == "GREEN" else "Watch",
            f"RT {float(env.get('rt_mw') or 0):.1f} vs DAM {float(dam_lbl or 0):.0f}; tightens on variance",
            "Advisory envelope for commitment planning — not a control setpoint and not a bid. "
            "Stress from lake RT gap and PCI/variance when lake-sourced.",
            "#eab308" if st.session_state.pci_status != "GREEN" else "#22c55e",
        )

    r2c1, r2c2 = st.columns(2)
    with r2c1:
        truth_card(
            f"3. Start Capability (Probability) · {tag}",
            f"Hot start (next 12h): {env['start_12']}% | Next 36h: {env['start_36']}%",
            "Medium-High" if st.session_state.pci_status == "GREEN" else "Watch",
            "Penalized when lake burn variance / PCI elevated",
            "Commercial start confidence. Helps decide starts vs warm. Not a failure prediction model.",
            "#3b82f6",
        )
    with r2c2:
        truth_card(
            f"4. Minimum Commercial Load · {tag}",
            f"Nominal: {env['min_nom']} MW → Desk guidance (P95-style): {env['min_p95']} MW",
            "High",
            f"~28% of config/nameplate ({float(env.get('config_mw') or nameplate or 0):.0f} MW) + PCI stress",
            "Turndown confidence band for negative-price hours. Advisory only — no automatic offer.",
            "#a855f7",
        )

    truth_card(
        f"5. Expected Reliability (horizon) · {tag} · Differentiator",
        f"Run hours at full capability: {env['rel_full']}% | Prob >25 MW derate: {env['prob_derate']}%",
        "Elevated but manageable"
        if st.session_state.pci_status != "RED"
        else "Stressed — review lake variance / notes",
        "NOT failure prediction · Commercial reliability framing from lake PCI/variance",
        "Probability of forced derate or run-hour loss as a **commercial** signal from live burn variance. "
        "Operators may shade with traders or pull maintenance. Fleet-scale inference remains roadmap.",
        "#ef4444" if st.session_state.pci_status == "RED" else "#f97316",
    )

    # ── what changed + audit ─────────────────────────────────────────────
    st.divider()
    st.subheader("What Changed Since Last Update + Immutable Audit")

    if st.session_state.last_update:
        last = st.session_state.last_update
        st.info(
            f"**Update at {last['ts'].strftime('%H:%M:%S')}** by {last.get('operator', 'operator')}  \n"
            f"Heat Rate: {last['heat_rate']} | Award: {last['award_mw']} MW | "
            f"Actual Burn: {last['actual_burn']} MMBtu | "
            f"PCI: **{last['pci_status']}** | ETRM: **{last['etrm_status']}** "
            f"({last['deviation_pct']:+.2f}%)  \n"
            f"Notes: {last['notes']}"
        )
        prev = st.session_state.prev_update
        if prev:
            st.write(
                f"**Delta:** HR {prev['heat_rate']} → {last['heat_rate']} · "
                f"Award {prev['award_mw']} → {last['award_mw']} MW · "
                f"Burn {prev['actual_burn']} → {last['actual_burn']} · "
                f"PCI {prev.get('pci_status', '—')} → {last['pci_status']} · "
                f"Var {prev.get('deviation_pct', 0):+.2f}% → {last['deviation_pct']:+.2f}%"
            )

        if st.session_state.pending_ack is not None:
            st.warning("Acknowledgment required to append this update to the immutable audit trail.")
            if st.button(
                "✅ Acknowledge & Log to Audit Trail (Required)",
                type="secondary",
                use_container_width=True,
            ):
                entry = dict(st.session_state.pending_ack)
                entry["acked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                entry["ts"] = entry["ts"].strftime("%Y-%m-%d %H:%M:%S")
                st.session_state.audit.append(entry)
                st.session_state.pending_ack = None
                st.success(
                    "Acknowledgment recorded. Immutable session entry created. "
                    "Ready for shift handover package."
                )
                st.rerun()
    else:
        if st.session_state.lake_truth_row:
            st.info(
                "Lake truths are loaded for this unit. Submit a ritual only if you want an "
                "operator-acknowledged judgment on top of the lake facts."
            )
        else:
            st.warning(
                "No lake unit or operator update yet. Pick a unit in the sidebar or submit inputs."
            )

    if st.session_state.audit:
        st.markdown("**Audit trail (append-only, this session)**")
        audit_df = pd.DataFrame(st.session_state.audit)
        st.dataframe(audit_df.iloc[::-1], use_container_width=True, hide_index=True)
    else:
        st.caption("Audit is empty until an update is submitted and acknowledged.")

    # ── Snowflake lake (read-only ingest) ────────────────────────────────
    st.divider()
    st.subheader(f"Snowflake lake (read-only) — {LAKE_LABEL}")
    st.caption(
        "Ingest-only commercial truth from the AlphaGen data lake. "
        "This desk never writes to Snowflake. Operator rituals compute locally "
        "and log to the session audit; lake rows refresh from existing views."
    )

    if st.session_state.last_snowflake:
        sf = st.session_state.last_snowflake
        if sf.get("ok"):
            st.info(
                f"Optional SF write OK — load_id `{str(sf.get('load_id') or '')[:13]}…` "
                f"(not used in lake mode)"
            )
        elif sf.get("skipped"):
            st.caption(
                f"SF write skipped ({sf.get('reason') or 'n/a'}) — expected in read-only lake mode."
            )
        else:
            st.caption(f"SF write not used: {sf.get('message') or 'n/a'}")

    lake_c1, lake_c2, lake_c3, lake_c4 = st.columns([1, 1, 1, 1])
    with lake_c1:
        refresh_sor = st.button("Refresh lake", use_container_width=True)
    with lake_c2:
        filter_plant = st.checkbox(
            "Filter unit",
            value=st.session_state.unit_picker != MANUAL_UNIT,
            help="When on, only rows for the sidebar unit are loaded.",
        )
    with lake_c3:
        prefill_from_lake = st.button("Prefill from latest", use_container_width=True)
    with lake_c4:
        sor_limit = st.select_slider("Rows", options=[10, 25, 50, 100], value=25)

    plant = st.session_state.plant_id if filter_plant else None
    sor_key = (bool(filter_plant), str(plant or ""), int(sor_limit), "lake")
    if (
        refresh_sor
        or st.session_state.sor_cache is None
        or st.session_state.get("sor_key") != sor_key
    ):
        try:
            from spire_reactor.store.lake import fetch_lake_gas_burn

            st.session_state.sor_cache = fetch_lake_gas_burn(
                limit=int(sor_limit),
                unit_name=plant,
            )
            st.session_state.sor_key = sor_key
        except Exception as exc:  # noqa: BLE001
            st.session_state.sor_cache = {
                "ok": False,
                "skipped": False,
                "rows": [],
                "count": 0,
                "message": f"Lake read error: {exc}",
            }
            st.session_state.sor_key = sor_key

    sor = st.session_state.sor_cache or {}
    if prefill_from_lake and sor.get("ok") and (sor.get("rows") or []):
        latest_pre = (sor.get("rows") or [None])[0] or {}
        apply_lake_desk_to_session(latest_pre, as_truth=True)
        if latest_pre.get("plant_id") and latest_pre["plant_id"] in (
            st.session_state.lake_units or []
        ):
            st.session_state.unit_picker = str(latest_pre["plant_id"])
        st.session_state._last_loaded_unit = str(latest_pre.get("plant_id") or "")
        st.success(
            f"Five Truths + form prefilled from lake unit `{latest_pre.get('plant_id')}` "
            f"(HE={latest_pre.get('he')}, date={latest_pre.get('operating_date')})."
        )
        st.rerun()

    if sor.get("ok"):
        rows = sor.get("rows") or []
        st.success(
            f"{sor.get('message') or 'OK'} · `{sor.get('landing_table') or sor.get('source') or LAKE_LABEL}`"
        )
        if rows:
            display_cols = [
                c
                for c in (
                    "operating_date",
                    "he",
                    "plant_id",
                    "fleet_name",
                    "pipeline",
                    "pci_status",
                    "variance_pct",
                    "award_mw",
                    "rt_mw",
                    "actual_burn_mmbtu",
                    "estimated_burn_mmbtu",
                    "da_burn_mmbtu",
                    "rt_burn_mmbtu",
                    "burn_variance_mmbtu",
                    "heat_rate",
                    "heat_rate_config",
                    "net_revenue",
                )
                if rows and c in rows[0]
            ]
            # Drop nested raw for table display
            flat = [{k: v for k, v in r.items() if k != "_lake"} for r in rows]
            sor_df = pd.DataFrame(flat)
            if display_cols:
                sor_df = sor_df[[c for c in display_cols if c in sor_df.columns]]
            st.dataframe(sor_df, use_container_width=True, hide_index=True)
            latest = rows[0]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Latest PCI (derived)", str(latest.get("pci_status") or "—"))
            m2.metric("Unit", str(latest.get("plant_id") or "—")[:28])
            try:
                m3.metric("Burn variance %", f"{float(latest.get('variance_pct') or 0):+.2f}%")
            except (TypeError, ValueError):
                m3.metric("Burn variance %", "—")
            try:
                m4.metric("DA burn MMBtu", f"{float(latest.get('da_burn_mmbtu') or 0):,.1f}")
            except (TypeError, ValueError):
                m4.metric("DA burn MMBtu", "—")
        else:
            st.caption("Lake source returned no rows for this filter.")
    elif sor.get("skipped"):
        st.caption(sor.get("message") or "Snowflake lake not available (configure in Setup).")
    else:
        st.error(sor.get("message") or "Snowflake lake read failed")

    # ── trend ────────────────────────────────────────────────────────────
    if st.session_state.history:
        st.subheader("Burn variance trend (this session)")
        hist_df = pd.DataFrame(st.session_state.history)
        hist_df["time"] = pd.to_datetime(hist_df["time"])
        fig = px.line(
            hist_df,
            x="time",
            y="deviation",
            markers=True,
            color="pci_band" if "pci_band" in hist_df.columns else None,
            title="Variance % after each ritual",
            labels={"deviation": "Variance %", "time": "Time"},
        )
        fig.update_layout(height=300, margin=dict(l=20, r=20, t=40, b=20))
        st.plotly_chart(fig, use_container_width=True)

    # ── downstream ───────────────────────────────────────────────────────
    with st.expander("Downstream Consumers (Read-Only View)", expanded=False):
        st.write(
            "Power BI (Gold views) • Provider Exports (MS-D) • Compliance • "
            "Trading Desk (envelopes only) • Outage Planning"
        )
        st.caption(
            "All outputs are advisory envelopes. No automatic bid submission or price recommendations."
        )
        consumers = pd.DataFrame(
            [
                {
                    "Consumer": "Power BI — Operations",
                    "Status": "Ready (demo)",
                    "Mode": "Advisory",
                },
                {
                    "Consumer": "Provider Export Portal",
                    "Status": "Ready (demo)",
                    "Mode": "Advisory",
                },
                {
                    "Consumer": "Compliance / Audit",
                    "Status": "Session log + Snowflake lake ingest (read-only)",
                    "Mode": "Append-only",
                },
                {
                    "Consumer": "Trading Desk",
                    "Status": "Envelopes only",
                    "Mode": "No bids",
                },
                {
                    "Consumer": "Spire Reactor / Redis",
                    "Status": "Ritual publish best-effort",
                    "Mode": "demo or live",
                },
                {
                    "Consumer": f"Snowflake {LAKE_LABEL}",
                    "Status": "Read-only lake ingest → Five Truths",
                    "Mode": "Ingest",
                },
            ]
        )
        st.dataframe(consumers, hide_index=True, use_container_width=True)

    st.caption(
        "Owned by Generation / Asset Management • Co-developed with Ops + Commercial • "
        "Trading is consumer, not owner • Session audit + Snowflake lake ingest (read-only) • "
        "OpenAlphaOperator hybrid v1 • Spire Reactor gas_burn_update"
    )
