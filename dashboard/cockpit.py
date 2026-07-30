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
    cls = {"lake": "badge-lake", "ritual": "badge-ritual"}.get(s, "badge-demo")
    return f'<span class="desk-badge {cls}">{s}</span>'


def _pci_badge(pci: str) -> str:
    p = (pci or "—").upper()
    cls = {"GREEN": "badge-green", "AMBER": "badge-amber", "RED": "badge-red"}.get(
        p, "badge-demo"
    )
    return f'<span class="desk-badge {cls}">PCI {p}</span>'


def _is_stale_op_date(op_date: Any) -> bool:
    if not op_date:
        return False
    try:
        from datetime import date as date_cls

        s = str(op_date)[:10]
        d = date_cls.fromisoformat(s)
        return d != date_cls.today()
    except Exception:  # noqa: BLE001
        return False


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
    """Dense operator desk — primary facts visible when maximized."""
    _init_state()

    # ── sidebar (controls only) ──────────────────────────────────────────
    with st.sidebar:
        st.markdown("### Desk controls")
        units = ensure_lake_units()
        unit_options = [MANUAL_UNIT] + units
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
            "Unit",
            unit_options,
            index=picker_idx,
            help=f"From {LAKE_LABEL}",
        )
        st.session_state.unit_picker = chosen

        b_u, b_t = st.columns(2)
        with b_u:
            refresh_units = st.button("Units", use_container_width=True)
        with b_t:
            reload_truth = st.button("Reload", use_container_width=True)
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
            st.caption(f"{len(units)} units · {st.session_state.lake_units_msg or 'lake'}")
        else:
            st.session_state.plant_id = st.text_input(
                "Plant",
                st.session_state.plant_id
                if st.session_state.plant_id != MANUAL_UNIT
                else DEFAULT_PLANT,
            )

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
        st.caption(f"Read-only · `{LAKE_LABEL}` · no SF writes")

    # Auto-pick first lake unit once
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

    env = resolve_truth_envelopes()
    truth_src = str(env.get("source") or st.session_state.get("truth_source") or "demo")
    lake_meta = st.session_state.lake_truth_row or {}
    op_date = lake_meta.get("operating_date") or env.get("operating_date")
    he = lake_meta.get("he") if lake_meta.get("he") is not None else env.get("he")
    fleet = lake_meta.get("fleet_name") or env.get("fleet_name") or "—"
    pipe = lake_meta.get("pipeline") or env.get("pipeline") or "—"
    stale = _is_stale_op_date(op_date)
    dam = float(env.get("dam_mw") if env.get("dam_mw") is not None else st.session_state.award_mw)
    rt_mw = float(env.get("rt_mw") or lake_meta.get("rt_mw") or 0.0)
    hr = float(env.get("heat_rate") or st.session_state.heat_rate)
    da_burn = float(
        env.get("da_burn_mmbtu")
        if env.get("da_burn_mmbtu") is not None
        else st.session_state.estimated_burn
    )
    rt_burn = float(
        env.get("rt_burn_mmbtu")
        if env.get("rt_burn_mmbtu") is not None
        else st.session_state.actual_burn
    )

    # ── title (native Streamlit — always visible; custom HTML titles can be stripped) ──
    st.title("Real Time Desk")

    # ── status bar ───────────────────────────────────────────────────────
    stale_html = (
        '<span class="desk-badge badge-stale">STALE HE</span>' if stale else ""
    )
    unit_short = str(st.session_state.plant_id)
    if len(unit_short) > 42:
        unit_short = unit_short[:40] + "…"
    st.markdown(
        f"""
        <div class="desk-bar">
          {_source_badge(truth_src)}
          {_pci_badge(st.session_state.pci_status)}
          {stale_html}
          <span><span class="muted">Unit</span> <strong>{unit_short}</strong></span>
          <span><span class="muted">Fleet</span> <strong>{fleet}</strong></span>
          <span><span class="muted">Pipe</span> <strong>{pipe}</strong></span>
          <span><span class="muted">HE</span> <strong>{he if he is not None else "—"}</strong></span>
          <span><span class="muted">Gas day</span> <strong>{op_date or "—"}</strong></span>
          <span><span class="muted">Shift</span> <strong>{st.session_state.shift}</strong> · {st.session_state.operator}</span>
          <span class="muted">{datetime.now().strftime("%H:%M:%S")}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("Advisory only · no bids · lake read-only · session audit on acknowledge")

    # ── KPI strip (2×4 — readable when maximized or mid-width) ───────────
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

    st.markdown("")  # small vertical break before truths
    # ── Five Truths — single row when maximized ──────────────────────────
    st.subheader("Commercial truths")
    t1, t2, t3, t4, t5 = st.columns(5)
    nameplate = float(env.get("nameplate_mw") or env.get("eco_max_mw") or dam or 0)
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

    # ── Work row: judgment form | lake table ─────────────────────────────
    st.markdown("")  # space between truths and work row
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
            if st.button("Prefill lake", use_container_width=True):
                if st.session_state.plant_id and st.session_state.plant_id != MANUAL_UNIT:
                    load_lake_truth_for_unit(st.session_state.plant_id, force=True)
                    st.rerun()
                st.warning("Select a lake unit first")
        with a3:
            if st.button("Synthetic", use_container_width=True):
                from spire_reactor.ingest.public_feeds import synthetic_tick

                snap = synthetic_tick()
                payload = snap.get("operator_payload") or {}
                st.session_state.heat_rate = float(payload.get("heat_rate") or 7.5)
                st.session_state.award_mw = float(payload.get("award_mw") or 480.0)
                st.session_state.actual_burn = float(
                    payload.get("actual_burn_mmbtu") or 3450.0
                )
                st.session_state.notes = str(payload.get("notes") or "synthetic")
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
                    f"PCI {st.session_state.pci_status} · ETRM {st.session_state.etrm_status} · "
                    f"{st.session_state.deviation_pct:+.2f}% — acknowledge to audit"
                )
                st.rerun()

        # Pending ack (critical path — always visible)
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
        st.subheader(f"Lake feed · {LAKE_LABEL}")
        lc1, lc2, lc3 = st.columns([1, 1, 2])
        with lc1:
            refresh_sor = st.button("Refresh", use_container_width=True, key="lake_ref")
        with lc2:
            filter_plant = st.checkbox(
                "This unit",
                value=st.session_state.unit_picker != MANUAL_UNIT,
                key="lake_filter",
            )
        with lc3:
            sor_limit = st.select_slider(
                "Rows", options=[10, 25, 50], value=10, key="lake_lim"
            )

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
                    "rows": [],
                    "count": 0,
                    "message": str(exc)[:160],
                }
                st.session_state.sor_key = sor_key

        sor = st.session_state.sor_cache or {}
        if sor.get("ok") and (sor.get("rows") or []):
            rows = sor["rows"]
            display_cols = [
                c
                for c in (
                    "operating_date",
                    "he",
                    "plant_id",
                    "pipeline",
                    "pci_status",
                    "variance_pct",
                    "award_mw",
                    "rt_mw",
                    "da_burn_mmbtu",
                    "rt_burn_mmbtu",
                    "heat_rate",
                )
                if c in rows[0]
            ]
            flat = [{k: v for k, v in r.items() if k != "_lake"} for r in rows]
            sor_df = pd.DataFrame(flat)
            if display_cols:
                sor_df = sor_df[[c for c in display_cols if c in sor_df.columns]]
            st.dataframe(
                sor_df,
                use_container_width=True,
                hide_index=True,
                height=260,
            )
            st.caption(sor.get("message") or f"{len(rows)} row(s)")
        elif sor.get("skipped"):
            st.caption(sor.get("message") or "Lake not configured")
        else:
            st.caption(sor.get("message") or "No lake rows")

    # ── secondary (collapsed) ────────────────────────────────────────────
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

    with st.expander("Advanced / offline feeds", expanded=False):
        if st.button("Prefill public feeds (Open-Meteo / EIA)"):
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
                st.session_state.truth_source = "demo"
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc)[:200])
        st.caption(
            "Public feeds are not plant SCADA. Lake is system of commercial truth for this desk."
        )
