"""
Real Time Desk.

Latest public tags, source health, and an optional local Ollama query.
A missing feed stays empty. Synthetic samples are labeled and stay off the model path.
"""

from __future__ import annotations

import html
import time
from datetime import timedelta
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st
from spire_reactor.ingest.desk_pull import (
    collect_public_desk,
    load_synthetic_desk,
    summarize,
)
from spire_reactor.model.ollama_desk import ask_desk, ollama_model, probe_ollama
from spire_reactor.tags.schema import (
    QUALITY_SYNTHETIC,
    desk_timezone_name,
    format_local,
    format_period_date,
    format_utc,
    key_tags,
    utc_now,
)
from spire_reactor.tags.store import get_store

from dashboard.setup_stage import render_integration_status_strip

_UNIT_LABEL = {"degC": "°C"}


def _shown_unit(unit: str) -> str:
    return _UNIT_LABEL.get(unit, unit)


def _format_value(value: float, unit: str) -> str:
    if unit == "%":
        text = f"{value:.0f}"
    elif unit == "degC":
        text = f"{value:.1f}"
    else:
        text = f"{value:.2f}"
    return f"{text} {_shown_unit(unit)}"


def _badge(source: str, state: str) -> str:
    kind = {"ok": "badge-green", "synthetic": "badge-amber"}.get(state, "badge-red")
    label = {"ok": "ok", "synthetic": "synthetic"}.get(state, "unavailable")
    return (
        f'<span class="desk-badge {kind}">{html.escape(source)} {label}</span>'
    )


def _ensure_feeds() -> None:
    force = bool(st.session_state.pop("force_pull", False))
    store = get_store()
    if store.mode() == "synthetic" and not force:
        return
    fetched = store.fetched_at()
    age = None if fetched is None else (utc_now() - fetched).total_seconds()
    auto = bool(st.session_state.get("auto_refresh", True))
    if force or age is None or (auto and age > 50):
        collect_public_desk()


def _ollama_status() -> dict[str, Any]:
    cached = st.session_state.get("ollama_probe")
    now = time.time()
    if isinstance(cached, dict) and now - float(cached.get("t") or 0) < 30:
        return cached
    probed = probe_ollama()
    cached = {"t": now, "ok": bool(probed.get("ok")), "message": probed.get("message") or ""}
    st.session_state["ollama_probe"] = cached
    return cached


def _tile(spec: Any) -> None:
    store = get_store()
    health = store.health_for(spec.source) or {}
    state = str(health.get("state") or "unavailable")
    quality = QUALITY_SYNTHETIC if state == "synthetic" else "good"
    sample = None if state == "unavailable" else store.latest(spec.tag, quality=quality)
    if sample is None or sample.value is None:
        st.metric(spec.tag, "—")
        st.caption("source unavailable")
        return
    prefix = "SYNTHETIC " if sample.quality == QUALITY_SYNTHETIC else ""
    st.metric(spec.tag, prefix + _format_value(float(sample.value), spec.unit))
    window = timedelta(days=40) if spec.cadence == "daily" else timedelta(hours=36)
    points = store.series(spec.tag, utc_now() - window, utc_now(), quality=quality)
    if len(points) >= 2:
        frame = pd.DataFrame(
            {"value": [point.value for point in points]},
            index=pd.to_datetime([point.ts_utc for point in points], utc=True),
        )
        st.line_chart(frame, height=88)
    when = format_period_date(sample.ts_utc) if spec.cadence == "daily" else format_local(sample.ts_utc)
    st.caption(when)


@st.fragment(run_every=timedelta(seconds=60))
def _desk_fragment() -> None:
    _ensure_feeds()
    store = get_store()
    if store.mode() == "synthetic":
        st.warning("SYNTHETIC — offline sample, not a measurement. Refresh live feeds to clear it.")

    health = store.health()
    if health:
        chips = " ".join(_badge(str(row.get("source") or ""), str(row.get("state") or "")) for row in health)
        st.markdown(f'<div class="desk-bar">{chips}</div>', unsafe_allow_html=True)
        for row in health:
            st.caption(f"{row.get('source')}: {row.get('message') or 'source unavailable'}")

    fetched = store.fetched_at()
    clock = format_local(utc_now())
    pulled = format_utc(fetched) if fetched else "not yet"
    st.caption(f"{clock} · stored UTC · last pull {pulled}")

    grouped: dict[str, list[Any]] = {}
    for spec in key_tags():
        grouped.setdefault(spec.source, []).append(spec)
    titles = {
        "open-meteo": "Weather",
        "eia": "EIA",
        "ercot": "ERCOT",
        "pjm": "PJM",
    }
    for source, specs in grouped.items():
        st.subheader(titles.get(source, source))
        columns = st.columns(min(4, len(specs)))
        for index, spec in enumerate(specs):
            with columns[index % len(columns)]:
                _tile(spec)

    with st.expander("All public tags", expanded=False):
        rows = summarize().get("latest") or []
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.caption("No pull yet.")


def _render_query_result(result: dict[str, Any]) -> None:
    if not result.get("ok"):
        st.warning(str(result.get("error") or "source unavailable"))
        return
    calls = result.get("calls") or []
    if not calls:
        st.info("No tag query was run. Nothing to chart.")
    frames: list[dict[str, Any]] = []
    for call in calls:
        name = call.get("name")
        payload = call.get("result") or {}
        if name == "list_tags":
            tags = payload.get("tags") or []
            if tags:
                st.dataframe(pd.DataFrame(tags), width="stretch", hide_index=True)
            continue
        if name != "query_pi_tag":
            continue
        if payload.get("source_state") == "unavailable" or payload.get("error") == "source unavailable":
            st.warning(f"{payload.get('tag')}: source unavailable")
            continue
        if payload.get("error") and not payload.get("points"):
            st.warning(f"{payload.get('tag')}: {payload.get('error')}")
            continue
        if payload.get("agg") not in (None, "raw") and payload.get("aggregate_value") is not None:
            st.metric(
                f"{payload.get('tag')} {payload.get('agg')}",
                _format_value(float(payload["aggregate_value"]), str(payload.get("unit") or "")),
            )
        frames.extend(payload.get("points") or [])
    if frames:
        frame = pd.DataFrame(frames)
        frame["time"] = pd.to_datetime(frame["ts_utc"], utc=True)
        show = [column for column in ("tag", "time", "value", "unit", "quality") if column in frame.columns]
        st.dataframe(frame[show], width="stretch", hide_index=True)
        chart = px.line(frame, x="time", y="value", color="tag", markers=True)
        chart.update_layout(
            height=280,
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", y=1.12),
        )
        st.plotly_chart(chart, width="stretch")
    elif calls and not any(call.get("name") == "list_tags" for call in calls):
        st.info("No tag samples in that range.")
    note = (result.get("message") or "").strip()
    if note:
        st.caption(note)


def _model_panel() -> None:
    status = _ollama_status()
    if not status.get("ok"):
        return
    st.subheader("Desk query")
    st.caption(
        f"Local model {ollama_model()} turns the question into tag reads. "
        "The chart is the tag store, not the model's own numbers. "
        "Synthetic samples are excluded."
    )
    question = st.text_input(
        "Question",
        placeholder="ERCOT Houston hub LMP for the last 6 hours",
        key="desk_question",
    )
    if st.button("Ask", type="primary", key="desk_ask"):
        with st.spinner("Querying tags"):
            st.session_state["desk_query"] = ask_desk(question)
    result = st.session_state.get("desk_query")
    if isinstance(result, dict):
        _render_query_result(result)


def _calculator() -> None:
    with st.expander("Local burn calculator — derived, not a measurement", expanded=False):
        st.caption(
            "Typed inputs only. The result is a local variance check, not an LMP or a meter. "
            "Temporal and Redis are optional; the check still runs on this machine."
        )
        heat_rate = st.number_input("Heat rate", min_value=0.0, value=7.5, step=0.1, format="%.2f")
        award_mw = st.number_input("Award MW", min_value=0.0, value=0.0, step=10.0)
        actual_burn = st.number_input("Actual burn (MMBtu)", min_value=0.0, value=0.0, step=10.0)
        hours = st.number_input("Hours", min_value=0.25, value=1.0, step=0.25)
        notes = st.text_input("Notes", value="")
        if st.button("Run local check"):
            try:
                from spire_reactor.main import trigger_ritual
            except Exception as exc:  # noqa: BLE001
                st.error(f"Calculator unavailable: {exc}")
                return
            result = trigger_ritual(
                "gas_burn_update",
                {
                    "plant_id": "DESK-LOCAL",
                    "heat_rate": float(heat_rate),
                    "award_mw": float(award_mw),
                    "actual_burn_mmbtu": float(actual_burn),
                    "hours": float(hours),
                    "notes": notes or "local calculator",
                },
            )
            if result.get("status") != "success":
                st.error(result.get("message") or "Check failed")
            else:
                st.write(
                    {
                        "scheduling_status": result.get("scheduling_status"),
                        "deviation_pct": result.get("deviation_pct"),
                        "estimated_burn_mmbtu": result.get("estimated_burn_mmbtu"),
                        "quality": "derived",
                    }
                )


def render_cockpit() -> None:
    """Public-tag desk. Works with no model, no Temporal, and no Docker."""
    st.title("Real Time Desk")
    st.caption(
        "Public feeds only. A failed source stays empty. "
        "Times are stored in UTC and shown in the desk timezone."
    )
    with st.sidebar:
        st.markdown("### Desk")
        st.checkbox("Auto refresh (60s)", value=True, key="auto_refresh")
        if st.button("Refresh live feeds", width="stretch"):
            st.session_state["force_pull"] = True
            st.session_state.pop("ollama_probe", None)
            st.rerun()
        if st.button("Load synthetic sample", width="stretch"):
            load_synthetic_desk()
            st.rerun()
        st.caption(f"Display timezone {desk_timezone_name()}.")
        st.caption("Temporal is not required for this desk.")
        render_integration_status_strip()

    _desk_fragment()
    _model_panel()
    _calculator()
