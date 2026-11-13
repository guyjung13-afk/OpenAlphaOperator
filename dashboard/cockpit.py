"""
Real Time Desk — operator view on public feeds.

- Five Truths UI (illustrative envelopes from the operator form)
- Public-feed prefill (Open-Meteo, optional EIA, or an offline synthetic tick)
- Local ritual compute (no private data store)
- Append-only session audit + operator acknowledgment
- Integration status strip → re-open Setup stage
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st
from spire_reactor.ingest.public_feeds import fetch_demo_snapshot, synthetic_tick
from spire_reactor.main import trigger_ritual

from dashboard.setup_stage import render_integration_status_strip

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
    st.session_state.truth_source = "demo"  # "public" | "demo" | "ritual"


def truth_tile(label: str, value: str, sub: str, color: str) -> None:
    """Compact single-tile commercial truth — dense desk, not marketing card."""
    st.markdown(
        f"""
        <div class="truth-tile" style="border-left-color:{color};">
          <div class="t-label">{label}</div>
          <div class="t-value">{value}</div>
          <div class="t-sub">{sub}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _source_badge(src: str) -> str:
    s = (src or "demo").lower()
    cls = {"public": "badge-green", "ritual": "badge-ritual"}.get(s, "badge-demo")
    return f'<span class="desk-badge {cls}">{s}</span>'


def _pci_badge(pci: str) -> str:
    p = (pci or "—").upper()
    cls = {"GREEN": "badge-green", "AMBER": "badge-amber", "RED": "badge-red"}.get(
        p, "badge-demo"
    )
    return f'<span class="desk-badge {cls}">PCI {p}</span>'


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
    Illustrative envelopes from the operator form.
    Explicitly not SCADA or bid guidance.
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


def load_public_snapshot(*, force: bool = False) -> dict[str, Any]:
    """Open-Meteo (+ optional EIA), with an offline synthetic tick if the feed fails."""
    cached = st.session_state.feed_meta
    if not force and isinstance(cached, dict):
        return cached
    snap = fetch_demo_snapshot()
    weather = snap.get("weather") or {}
    if not weather.get("ok"):
        offline = synthetic_tick()
        offline["fallback"] = weather.get("error") or "public feed unavailable"
        snap = offline
    st.session_state.feed_meta = snap
    return snap


def apply_public_payload(payload: dict[str, Any]) -> None:
    """Copy a public-feed operator payload into the desk form."""
    if payload.get("heat_rate") is not None:
        st.session_state.heat_rate = float(payload["heat_rate"])
    if payload.get("award_mw") is not None:
        st.session_state.award_mw = float(payload["award_mw"])
    if payload.get("actual_burn_mmbtu") is not None:
        st.session_state.actual_burn = float(payload["actual_burn_mmbtu"])
    if payload.get("hours") is not None:
        st.session_state.hours = float(payload["hours"])
    if payload.get("notes"):
        st.session_state.notes = str(payload["notes"])
    st.session_state.truth_source = "public"


def resolve_truth_envelopes() -> dict[str, Any]:
    """Envelopes follow the operator form. Source tag records what last filled it."""
    src = st.session_state.get("truth_source") or "demo"
    env = demo_envelopes(
        st.session_state.award_mw,
        st.session_state.heat_rate,
        st.session_state.pci_status,
        st.session_state.deviation_pct,
    )
    if src in ("public", "ritual"):
        env["source"] = src
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
    st.session_state.truth_source = "ritual"

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


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None or value == "":
        return "—"
    return f"{value}{suffix}"


def render_cockpit() -> None:
    """Dense operator desk — primary facts visible when maximized."""
    _init_state()
    snap = load_public_snapshot()
    weather = snap.get("weather") or {}
    gas = snap.get("natural_gas") or {}
    payload = snap.get("operator_payload") or {}

    with st.sidebar:
        st.markdown("### Desk controls")
        st.session_state.plant_id = st.text_input("Plant", st.session_state.plant_id)
        st.session_state.operator = st.text_input("Operator", st.session_state.operator)
        st.session_state.shift = st.selectbox(
            "Shift",
            ["Day", "Night", "Swing"],
            index=["Day", "Night", "Swing"].index(st.session_state.shift)
            if st.session_state.shift in ("Day", "Night", "Swing")
            else 0,
        )
        st.divider()
        render_integration_status_strip()
        feed_name = weather.get("source") or "public"
        st.caption(f"Public feeds · {feed_name} · optional EIA")

    env = resolve_truth_envelopes()
    truth_src = str(env.get("source") or st.session_state.get("truth_source") or "demo")
    dam = float(env.get("dam_mw") if env.get("dam_mw") is not None else st.session_state.award_mw)
    rt_mw = float(env.get("rt_mw") or 0.0)
    hr = float(env.get("heat_rate") or st.session_state.heat_rate)
    da_burn = float(st.session_state.estimated_burn)
    rt_burn = float(st.session_state.actual_burn)
    temp = weather.get("temperature_c")
    wind = weather.get("wind_speed_kmh")
    price = gas.get("price_usd_mmbtu")

    st.title("Real Time Desk")

    unit_short = str(st.session_state.plant_id)
    if len(unit_short) > 42:
        unit_short = unit_short[:40] + "…"
    st.markdown(
        f"""
        <div class="desk-bar">
          {_source_badge(truth_src)}
          {_pci_badge(st.session_state.pci_status)}
          <span><span class="muted">Unit</span> <strong>{unit_short}</strong></span>
          <span><span class="muted">Temp</span> <strong>{_fmt(temp, "°C")}</strong></span>
          <span><span class="muted">Wind</span> <strong>{_fmt(wind, " km/h")}</strong></span>
          <span><span class="muted">NG</span> <strong>{_fmt(price, " $/MMBtu")}</strong></span>
          <span><span class="muted">Shift</span> <strong>{st.session_state.shift}</strong> · {st.session_state.operator}</span>
          <span class="muted">{datetime.now().strftime("%H:%M:%S")}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("Advisory only · public feeds · no bids · session audit on acknowledge")

    r1 = st.columns(4)
    r2 = st.columns(4)
    r1[0].metric("PCI", st.session_state.pci_status)
    r1[1].metric("ETRM", st.session_state.etrm_status)
    r1[2].metric("Var %", f"{st.session_state.deviation_pct:+.1f}%")
    r1[3].metric("DAM MW", f"{dam:,.0f}")
    r2[0].metric("RT MW", f"{rt_mw:,.1f}")
    r2[1].metric("HR", f"{hr:.2f}")
    r2[2].metric("DA burn", f"{da_burn:,.0f}")
    r2[3].metric("RT burn", f"{rt_burn:,.0f}")

    st.markdown("")
    st.subheader("Commercial truths")
    t1, t2, t3, t4, t5 = st.columns(5)
    nameplate = float(env.get("nameplate_mw") or payload.get("nameplate_mw") or dam or 0)
    with t1:
        truth_tile(
            "1 · Available MW",
            f"P50 {env['p50']} · P90 {env['p90']} · P99 {env['p99']}",
            f"DAM {dam:.0f} · eco/cfg ~{nameplate:.0f}",
            band_color(st.session_state.pci_status),
        )
    with t2:
        truth_tile(
            "2 · Ramp (6h)",
            f"{env['ramp_desk']} MW/min desk",
            f"Base {env['ramp_full']} · RT vs DAM stress",
            "#eab308" if st.session_state.pci_status != "GREEN" else "#22c55e",
        )
    with t3:
        truth_tile(
            "3 · Start conf.",
            f"12h {env['start_12']}% · 36h {env['start_36']}%",
            "Commercial start — not failure model",
            "#3b82f6",
        )
    with t4:
        truth_tile(
            "4 · Min load",
            f"{env['min_nom']} → P95 {env['min_p95']} MW",
            "Turndown band · advisory",
            "#a855f7",
        )
    with t5:
        truth_tile(
            "5 · Reliability",
            f"Full {env['rel_full']}% · >25MW derate {env['prob_derate']}%",
            "Commercial signal from variance",
            "#ef4444" if st.session_state.pci_status == "RED" else "#f97316",
        )

    st.markdown("")
    left, right = st.columns([1, 1.35], gap="large")

    with left:
        st.subheader("Operator judgment")
        c_a, c_b, c_c, c_d = st.columns(4)
        with c_a:
            heat_rate = st.number_input(
                "HR",
                value=float(st.session_state.heat_rate),
                step=0.1,
                format="%.2f",
                key="in_hr",
            )
        with c_b:
            award_mw = st.number_input(
                "Award MW",
                value=float(st.session_state.award_mw),
                step=10.0,
                key="in_award",
            )
        with c_c:
            actual_burn = st.number_input(
                "Actual burn",
                value=float(st.session_state.actual_burn),
                step=50.0,
                key="in_burn",
            )
        with c_d:
            hours = st.number_input(
                "Hours",
                value=float(st.session_state.hours),
                min_value=0.25,
                step=0.25,
                key="in_hours",
            )
        notes = st.text_input("Notes", value=st.session_state.notes, key="in_notes")

        a1, a2, a3 = st.columns(3)
        with a1:
            submit = st.button("Submit ritual", type="primary", use_container_width=True)
        with a2:
            if st.button("Public feeds", use_container_width=True):
                fresh = load_public_snapshot(force=True)
                apply_public_payload(fresh.get("operator_payload") or {})
                st.rerun()
        with a3:
            if st.button("Synthetic", use_container_width=True):
                tick = synthetic_tick()
                st.session_state.feed_meta = tick
                apply_public_payload(tick.get("operator_payload") or {})
                st.session_state.truth_source = "demo"
                st.rerun()

        if submit:
            inputs = {
                "heat_rate": float(heat_rate),
                "award_mw": float(award_mw),
                "actual_burn": float(actual_burn),
                "hours": float(hours),
                "notes": notes,
            }
            body = {
                "plant_id": st.session_state.plant_id,
                "heat_rate": inputs["heat_rate"],
                "award_mw": inputs["award_mw"],
                "actual_burn_mmbtu": inputs["actual_burn"],
                "hours": inputs["hours"],
                "notes": inputs["notes"],
            }
            result = trigger_ritual("gas_burn_update", body)
            if result.get("status") != "success":
                st.error(result.get("message") or "Ritual failed")
            else:
                apply_ritual_result(result, inputs)
                st.session_state.hours = inputs["hours"]
                st.success(
                    f"PCI {st.session_state.pci_status} · ETRM {st.session_state.etrm_status} · "
                    f"{st.session_state.deviation_pct:+.2f}% — acknowledge to audit"
                )
                st.rerun()

        if st.session_state.pending_ack is not None:
            last = st.session_state.pending_ack
            st.warning(
                f"Ack required · {last.get('pci_status')} · "
                f"award {last.get('award_mw')} · burn {last.get('actual_burn')} · "
                f"{last.get('deviation_pct', 0):+.2f}%"
            )
            if st.button("Acknowledge → audit", type="secondary", use_container_width=True):
                entry = dict(st.session_state.pending_ack)
                entry["acked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ts = entry.get("ts")
                entry["ts"] = (
                    ts.strftime("%Y-%m-%d %H:%M:%S")
                    if hasattr(ts, "strftime")
                    else str(ts)
                )
                st.session_state.audit.append(entry)
                st.session_state.pending_ack = None
                st.rerun()
        elif st.session_state.last_update:
            last = st.session_state.last_update
            ts = last.get("ts")
            ts_s = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts)
            st.caption(
                f"Last update {ts_s} · PCI {last.get('pci_status')} · "
                f"var {last.get('deviation_pct', 0):+.2f}%"
            )

    with right:
        st.subheader("Public feeds")
        rc1, rc2 = st.columns([1, 3])
        with rc1:
            if st.button("Refresh", use_container_width=True, key="feed_ref"):
                load_public_snapshot(force=True)
                st.rerun()
        with rc2:
            observed = weather.get("observed_at") or snap.get("fetched_at") or "—"
            st.caption(f"Observed {observed}")

        def _cell(value: Any) -> str:
            if value is None or value == "":
                return "—"
            return str(value)

        rows = [
            {"field": "weather", "value": _cell(weather.get("source")), "ok": bool(weather.get("ok"))},
            {"field": "temperature_c", "value": _cell(weather.get("temperature_c")), "ok": bool(weather.get("ok"))},
            {"field": "wind_kmh", "value": _cell(weather.get("wind_speed_kmh")), "ok": bool(weather.get("ok"))},
            {"field": "humidity_pct", "value": _cell(weather.get("humidity_pct")), "ok": bool(weather.get("ok"))},
            {"field": "natural_gas", "value": _cell(gas.get("source")), "ok": bool(gas.get("ok"))},
            {"field": "price_usd_mmbtu", "value": _cell(gas.get("price_usd_mmbtu")), "ok": bool(gas.get("ok"))},
            {"field": "award_mw", "value": _cell(payload.get("award_mw")), "ok": True},
            {"field": "actual_burn_mmbtu", "value": _cell(payload.get("actual_burn_mmbtu")), "ok": True},
            {"field": "heat_rate", "value": _cell(payload.get("heat_rate")), "ok": True},
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=260)
        if snap.get("fallback"):
            st.caption(f"Offline tick · {snap.get('fallback')}")
        else:
            st.caption("Open-Meteo needs no key. EIA price fills in when EIA_API_KEY is set.")

    with st.expander("Session audit & trend", expanded=bool(st.session_state.audit)):
        if st.session_state.audit:
            st.dataframe(
                pd.DataFrame(st.session_state.audit).iloc[::-1],
                use_container_width=True,
                hide_index=True,
                height=160,
            )
        else:
            st.caption("Empty until a ritual is acknowledged.")
        if st.session_state.history:
            hist_df = pd.DataFrame(st.session_state.history)
            hist_df["time"] = pd.to_datetime(hist_df["time"])
            fig = px.line(
                hist_df,
                x="time",
                y="deviation",
                markers=True,
                color="pci_band" if "pci_band" in hist_df.columns else None,
                labels={"deviation": "Var %", "time": ""},
            )
            fig.update_layout(
                height=180,
                margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", y=1.1),
            )
            st.plotly_chart(fig, use_container_width=True)
