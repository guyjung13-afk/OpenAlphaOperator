"""
AlphaGen Commercial Truth Cockpit — Desk Operator View (Hybrid v1)

- Governance banner + Five Truths UI (commercial desk framing)
- Live burn/PCI/ETRM via spire_reactor.trigger_ritual / calculate_gas_burn
- Optional public-feeds prefill (Open-Meteo / synthetic)
- Append-only session audit + operator acknowledgment
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

# Repo root on path when launched as `streamlit run dashboard/app.py`
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from spire_reactor.main import calculate_gas_burn, pci_band_to_etrm, trigger_ritual  # noqa: E402

# ── page config ──────────────────────────────────────────────────────
st.set_page_config(
    page_title="AlphaGen • Commercial Truth Cockpit",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
    .main { background-color: #0b1220; }
    .stMetric { background-color: #1e2937; border-radius: 8px; padding: 12px; }
</style>
""",
    unsafe_allow_html=True,
)

# ── defaults / identity ──────────────────────────────────────────────
DEFAULT_PLANT = os.getenv("DEMO_PLANT_ID", "Linda 1 (Gas)")
DEFAULT_OPERATOR = os.getenv("DEMO_OPERATOR", "Desk Operator")
DEFAULT_SHIFT = os.getenv("DEMO_SHIFT", "Day")


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


_init_state()


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
    Light demo coupling: envelopes move with award / PCI band.
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
    }


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
        "plant_id": st.session_state.plant_id,
        "operator": st.session_state.operator,
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


# ── sidebar ──────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Shift context")
    st.session_state.plant_id = st.text_input("Plant", st.session_state.plant_id)
    st.session_state.operator = st.text_input("Operator", st.session_state.operator)
    st.session_state.shift = st.selectbox(
        "Shift",
        ["Day", "Night", "Swing"],
        index=["Day", "Night", "Swing"].index(st.session_state.shift)
        if st.session_state.shift in ("Day", "Night", "Swing")
        else 0,
    )
    st.markdown("---")
    st.caption(
        "Hybrid v1: live burn/PCI/ETRM from Spire Reactor. "
        "Five Truths envelopes are **demo-coupled** (not SCADA). "
        "Public feeds optional. Snowflake audit = Phase 2."
    )

# ── governance header ────────────────────────────────────────────────
st.markdown(
    """
<div style="background-color:#1a1a2e; padding:12px; border-radius:8px; margin-bottom:16px;">
<b>⚖️ GOVERNED DECISION SUPPORT — GENERATION / ASSET MANAGEMENT</b><br>
Advisory only. No bids generated. No bid prices recommended.
Operator acknowledgment required. Full immutable audit (session; Snowflake Phase 2).
</div>
""",
    unsafe_allow_html=True,
)

st.title("Commercial Truth Cockpit — Desk Operator View")
st.caption(
    f"Last refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
    f"Plant: {st.session_state.plant_id} | Shift: {st.session_state.shift} | "
    f"Operator: {st.session_state.operator}"
)

# ── live PCI / ETRM strip (Spire-backed) ──────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("PCI band", st.session_state.pci_status)
c2.metric("ETRM", st.session_state.etrm_status)
c3.metric("Variance %", f"{st.session_state.deviation_pct:+.2f}%")
c4.metric("Est. burn (MMBtu)", f"{st.session_state.estimated_burn:,.0f}")
c5.metric(
    "Last ritual",
    st.session_state.last_ritual.strftime("%H:%M:%S")
    if st.session_state.last_ritual
    else "—",
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
            st.session_state.feed_meta = {"mode": "synthetic", "fetched_at": snap.get("fetched_at")}
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
            st.success(
                f"Ritual complete — PCI {st.session_state.pci_status}, "
                f"ETRM {st.session_state.etrm_status}, "
                f"variance {st.session_state.deviation_pct:+.2f}%. "
                "Acknowledge below to append immutable audit."
            )
            st.rerun()

# ── five commercial truths (demo envelopes, input-coupled) ───────────
st.divider()
st.subheader("Five Commercial Truths — Confidence-Weighted Operating Envelopes")
st.caption(
    "Demo envelopes derived from award / PCI band / variance — **illustrative only**. "
    "Not SCADA, not failure prediction, not bid prices. Desk guidance bands for PoC."
)

env = demo_envelopes(
    st.session_state.award_mw,
    st.session_state.heat_rate,
    st.session_state.pci_status,
    st.session_state.deviation_pct,
)
accent = band_color(st.session_state.pci_status)

r1c1, r1c2 = st.columns(2)
with r1c1:
    truth_card(
        "1. Available MW (Operating Envelope) · Demo",
        f"P50: {env['p50']} MW | P90: {env['p90']} MW | P99: {env['p99']} MW",
        f"Band {st.session_state.pci_status}",
        f"Tied to award {st.session_state.award_mw:.0f} MW + PCI stress",
        "Probability-informed envelope vs static nameplate. Desk still declares a single number — now band-aware. Illustrative PoC coupling only.",
        accent if st.session_state.pci_status == "GREEN" else "#22c55e",
    )
with r1c2:
    truth_card(
        "2. Ramp Capability (Next 6h) · Demo",
        f"Normal: {env['ramp_full']} MW/min → Desk guidance band: {env['ramp_desk']} MW/min",
        "High" if st.session_state.pci_status == "GREEN" else "Watch",
        "Tightens when PCI AMBER/RED or variance rises",
        "Advisory envelope for commitment planning — not a control setpoint and not a bid. Operator retains full capability judgment.",
        "#eab308" if st.session_state.pci_status != "GREEN" else "#22c55e",
    )

r2c1, r2c2 = st.columns(2)
with r2c1:
    truth_card(
        "3. Start Capability (Probability) · Demo",
        f"Hot start (next 12h): {env['start_12']}% | Next 36h: {env['start_36']}%",
        "Medium-High",
        "Demo: penalized when burn variance elevated",
        "Commercial start confidence stub. Helps decide how hard to lean on starts vs keeping unit warm. Not a failure prediction model.",
        "#3b82f6",
    )
with r2c2:
    truth_card(
        "4. Minimum Commercial Load · Demo",
        f"Nominal: {env['min_nom']} MW → Desk guidance (P95-style): {env['min_p95']} MW",
        "High",
        "Rises with PCI stress (demo rule)",
        "Turndown confidence band for negative-price hours. Advisory only — no automatic offer or price recommendation.",
        "#a855f7",
    )

truth_card(
    "5. Expected Reliability (7-day horizon) · Demo · Differentiator",
    f"Run hours at full capability: {env['rel_full']}% | Prob >25 MW derate: {env['prob_derate']}%",
    "Elevated but manageable"
    if st.session_state.pci_status != "RED"
    else "Stressed — review notes",
    "NOT failure prediction · Commercial reliability framing",
    "Probability of forced derate, run-hour loss, or start shortfall as a **commercial** signal. "
    "Operators may shade with traders, pull maintenance, or align outage timing. "
    "Fleet-scale inference is roadmap — this card is a desk PoC envelope only.",
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
            # Production: Snowflake LANDING + Redis stream
            st.session_state.audit.append(entry)
            st.session_state.pending_ack = None
            st.success(
                "Acknowledgment recorded. Immutable session entry created. "
                "Ready for shift handover package."
            )
            st.rerun()
else:
    st.warning("No operator update yet this shift. Submit inputs above to seed the truths.")

if st.session_state.audit:
    st.markdown("**Audit trail (append-only, this session)**")
    audit_df = pd.DataFrame(st.session_state.audit)
    # newest first
    st.dataframe(audit_df.iloc[::-1], use_container_width=True, hide_index=True)
else:
    st.caption("Audit is empty until an update is submitted and acknowledged.")

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
                "Status": "Session log active",
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
                "Mode": "DEMO_MODE",
            },
        ]
    )
    st.dataframe(consumers, hide_index=True, use_container_width=True)

# ── footer ───────────────────────────────────────────────────────────
st.caption(
    "Owned by Generation / Asset Management • Co-developed with Ops + Commercial • "
    "Trading is consumer, not owner • Full audit logging enforced (session; Snowflake Phase 2) • "
    "OpenAlphaOperator hybrid v1 • Spire Reactor gas_burn_update"
)
