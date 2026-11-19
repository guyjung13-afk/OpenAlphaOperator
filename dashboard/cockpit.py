"""
Real Time Desk.

Latest public tags, source health, and an optional local Ollama query.
A missing feed stays empty. Synthetic samples are labeled and stay off the model path.
"""

from __future__ import annotations

import html
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from spire_reactor.ingest.desk_pull import (
    collect_public_desk,
    load_synthetic_desk,
    summarize,
)
from spire_reactor.ingest.public_feeds import DEFAULT_LAT, DEFAULT_LON, fetch_radar_overlay
from spire_reactor.model.ollama_desk import ask_desk, ollama_model, probe_ollama
from spire_reactor.tags.derived import houston_north_spread, implied_heat_rate
from spire_reactor.tags.schema import (
    QUALITY_SYNTHETIC,
    desk_timezone,
    desk_timezone_name,
    format_local,
    format_period_date,
    key_tags,
    utc_now,
    weather_site,
)
from spire_reactor.tags.staleness import evaluate_all
from spire_reactor.tags.store import get_store

from dashboard.setup_stage import render_integration_status_strip

_UNIT_LABEL = {"degC": "°C"}


def _shown_unit(unit: str) -> str:
    return _UNIT_LABEL.get(unit, unit)


def _clock(moment: Any) -> str:
    return moment.astimezone(desk_timezone()).strftime("%H:%M %Z")


def _tile_label(spec: Any) -> str:
    """Short operator label; the full tag stays in the caption."""
    parts = str(spec.tag).split(".")
    family = parts[0] if parts else ""
    entity = parts[1] if len(parts) > 1 else ""
    variable = parts[-1] if parts else str(spec.tag)
    if variable == "TEMP_C":
        return "Temperature"
    if variable == "WIND_KMH":
        return "Wind"
    if variable == "RH_PCT":
        return "Humidity"
    if variable == "RNGWHHD" or (family == "EIA" and entity == "NG"):
        return "Henry Hub"
    if family == "ERCOT" and variable == "LMP":
        name = entity.replace("HB_", "").replace("LZ_", "LZ ").replace("_", " ").title()
        return f"{name} LMP"
    if family == "PJM" and variable == "LMP":
        return f"{entity.title()} LMP"
    return str(spec.tag)


def _format_value(value: float, unit: str) -> str:
    if unit == "%":
        text = f"{value:.0f}"
    elif unit == "degC":
        text = f"{value:.1f}"
    else:
        text = f"{value:.2f}"
    return f"{text} {_shown_unit(unit)}"


def _freshness_kind(freshness: str) -> str:
    return {
        "live": "badge-green",
        "amber": "badge-amber",
        "red": "badge-red",
        "synthetic": "badge-amber",
        "not_connected": "badge-muted",
    }.get(freshness, "badge-red")


def _freshness_word(row: dict[str, Any]) -> str:
    freshness = str(row.get("freshness") or "unavailable")
    if freshness == "live":
        return "live"
    if freshness == "synthetic":
        return "synthetic"
    if freshness == "not_connected":
        return "not connected"
    if freshness in {"amber", "red"} and row.get("age_label"):
        return str(row["age_label"])
    return "unavailable"


def _feed_chip_html(row: dict[str, Any]) -> str:
    name = html.escape(str(row.get("name") or row.get("source") or ""))
    word = html.escape(_freshness_word(row))
    kind = _freshness_kind(str(row.get("freshness") or "unavailable"))
    as_of = str(row.get("as_of_label") or "").strip()
    age = str(row.get("age_label") or "").strip()
    detail = ""
    if as_of and row.get("freshness") not in {"not_connected", "unavailable"}:
        # Age is already the status word when stale; still show it next to as-of when live.
        if row.get("freshness") == "live" and age:
            detail = f'<span class="chip-asof">as of {html.escape(as_of)} · {html.escape(age)}</span>'
        else:
            detail = f'<span class="chip-asof">as of {html.escape(as_of)}</span>'
    return (
        f'<span class="desk-chip desk-badge {kind}">'
        f'<span class="chip-name">{name}</span> '
        f'<span class="chip-state">{word}</span>'
        f"{detail}"
        "</span>"
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


def _tile(spec: Any, *, show_sparkline: bool = True) -> None:
    store = get_store()
    health = store.health_for(spec.source) or {}
    state = str(health.get("state") or "unavailable")
    quality = QUALITY_SYNTHETIC if state == "synthetic" else "good"
    sample = None if state in {"unavailable", "not_connected"} else store.latest(spec.tag, quality=quality)
    if sample is None or sample.value is None:
        st.metric(_tile_label(spec), "—")
        st.caption("source unavailable")
        return
    prefix = "SYNTHETIC " if sample.quality == QUALITY_SYNTHETIC else ""
    st.metric(_tile_label(spec), prefix + _format_value(float(sample.value), spec.unit))
    window = timedelta(days=90) if spec.cadence == "daily" else timedelta(hours=36)
    points = store.series(spec.tag, utc_now() - window, utc_now(), quality=quality)
    if show_sparkline and len(points) >= 2:
        frame = pd.DataFrame(
            {"value": [point.value for point in points]},
            index=pd.to_datetime([point.ts_utc for point in points], utc=True),
        )
        st.line_chart(frame, height=72)
    when = format_period_date(sample.ts_utc) if spec.cadence == "daily" else format_local(sample.ts_utc)
    st.caption(when)


def _radar_as_of_caption(radar: dict[str, Any]) -> str:
    raw = radar.get("observed_at")
    moment = None
    if raw:
        try:
            moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            moment = None
    if moment is not None:
        local = moment.astimezone(desk_timezone())
        return f"radar as of {local.strftime('%H:%M')}"
    if radar.get("source") == "iem-nexrad":
        return "radar as of latest IEM NEXRAD"
    return "radar as of unknown time"


def _weather_radar_map() -> None:
    """Houston/ERCOT weather map with a live public radar overlay. Hidden on fetch failure."""
    show = st.checkbox("Radar overlay", value=True, key="wx_radar_overlay")
    radar: dict[str, Any] = fetch_radar_overlay() if show else {}
    layers: list[dict[str, Any]] = []
    if show and radar.get("ok") and radar.get("tile_url"):
        layers.append(
            {
                "sourcetype": "raster",
                "source": [str(radar["tile_url"])],
                "below": "traces",
                "opacity": 0.65,
            }
        )
    site = weather_site()
    fig = go.Figure(
        go.Scattermapbox(
            lat=[DEFAULT_LAT],
            lon=[DEFAULT_LON],
            mode="markers",
            marker={"size": 11, "color": "#0a0a0a"},
            text=[site],
            hoverinfo="text",
            showlegend=False,
        )
    )
    fig.update_layout(
        mapbox={
            "style": "open-street-map",
            "center": {"lat": DEFAULT_LAT, "lon": DEFAULT_LON},
            "zoom": 6,
            "layers": layers,
        },
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        height=300,
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    if not show:
        return
    if radar.get("ok") and layers:
        st.caption(_radar_as_of_caption(radar))
    else:
        st.caption(f"Radar unavailable: {radar.get('error') or 'source unavailable'}")


def _eia_history_chart(spec: Any) -> None:
    store = get_store()
    health = store.health_for(spec.source) or {}
    state = str(health.get("state") or "unavailable")
    if state in {"unavailable", "not_connected"}:
        return
    quality = QUALITY_SYNTHETIC if state == "synthetic" else "good"
    points = store.series(spec.tag, utc_now() - timedelta(days=90), utc_now(), quality=quality)
    if len(points) < 2:
        st.caption("Not enough EIA history to chart yet.")
        return
    frame = pd.DataFrame(
        {
            "period": pd.to_datetime([point.ts_utc for point in points], utc=True),
            "USD/MMBtu": [point.value for point in points],
        }
    )
    chart = px.line(frame, x="period", y="USD/MMBtu")
    chart.update_traces(line={"width": 2, "color": "#0f172a"}, hovertemplate="%{x|%b %d, %Y}<br>%{y:.2f} USD/MMBtu<extra></extra>")
    chart.update_xaxes(tickformat="%b %d", nticks=8, title="", showgrid=False, ticks="outside")
    chart.update_yaxes(title="USD/MMBtu", gridcolor="#e5e7eb", zeroline=False, ticks="outside")
    chart.update_layout(
        height=260,
        margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        showlegend=False,
    )
    st.plotly_chart(chart, width="stretch", config={"displayModeBar": False})
    first = format_period_date(points[0].ts_utc)
    last = format_period_date(points[-1].ts_utc)
    st.caption(f"{spec.tag} · daily · {first} to {last} · {len(points)} points")



def _derived_metric(title: str, value: str, caption: str) -> None:
    st.metric(title, value)
    st.caption(caption)


def _heat_rate_and_spread_row() -> None:
    store = get_store()
    heat = implied_heat_rate(store=store)
    spread = houston_north_spread(store=store)
    left, right = st.columns(2)
    with left:
        if heat["ok"]:
            _derived_metric(
                heat["label"],
                f"{heat['value']:.2f} {heat['unit']}",
                f"Houston LMP as of {format_local(heat['houston'].ts_utc)}",
            )
        else:
            missing = ", ".join(heat["missing"]) or "inputs unavailable"
            _derived_metric(heat["label"], "—", missing)
    with right:
        if spread["ok"]:
            houston_when = format_local(spread["houston"].ts_utc)
            north_when = format_local(spread["north"].ts_utc)
            _derived_metric(
                spread["label"],
                f"{spread['value']:.2f} {spread['unit']}",
                f"HB_HOUSTON as of {houston_when} · HB_NORTH as of {north_when}",
            )
        else:
            missing = ", ".join(spread["missing"]) or "inputs unavailable"
            _derived_metric(spread["label"], "—", missing)


@st.fragment(run_every=timedelta(seconds=60))
def _desk_fragment() -> None:
    _ensure_feeds()
    store = get_store()
    if store.mode() == "synthetic":
        st.warning("SYNTHETIC — offline sample, not a measurement. Refresh live feeds to clear it.")

    chips = " ".join(_feed_chip_html(row) for row in evaluate_all(store=store))
    st.markdown(f'<div class="desk-bar">{chips}</div>', unsafe_allow_html=True)

    fetched = store.fetched_at()
    clock = _clock(utc_now())
    pulled = _clock(fetched) if fetched else "not yet"
    st.caption(f"{clock} · last pull {pulled}")

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
        if source == "pjm":
            continue
        st.subheader(titles.get(source, source))
        if source == "open-meteo":
            _weather_radar_map()
        if source == "eia":
            columns = st.columns(4)
            for index, spec in enumerate(specs):
                with columns[index % 4]:
                    _tile(spec, show_sparkline=False)
            _eia_history_chart(specs[0])
            continue
        columns = st.columns(min(4, len(specs)))
        for index, spec in enumerate(specs):
            with columns[index % len(columns)]:
                _tile(spec)
        if source == "ercot":
            _heat_rate_and_spread_row()

    with st.expander("All public tags", expanded=False):
        rows = summarize().get("latest") or []
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.caption("No pull yet.")


def _render_query_chart(frame: pd.DataFrame, unit: str) -> None:
    """Chart tag samples in the desk timezone. A single point is not drawn on a zero-width axis."""
    if "chart_time" not in frame.columns or frame.empty:
        return
    if len(frame) == 1:
        return
    chart = px.line(frame, x="chart_time", y="value", color="tag", markers=True)
    span = frame["chart_time"].max() - frame["chart_time"].min()
    if pd.isna(span):
        tickformat = "%H:%M"
    elif span <= timedelta(hours=18):
        tickformat = "%H:%M"
    elif span <= timedelta(days=2):
        tickformat = "%b %d %H:%M"
    else:
        tickformat = "%b %d"
    chart.update_xaxes(tickformat=tickformat, nticks=6, title="", showgrid=False, ticks="outside")
    chart.update_yaxes(title=_shown_unit(unit) or "value", gridcolor="#e5e7eb", zeroline=False, ticks="outside")
    chart.update_layout(
        height=280,
        margin=dict(l=8, r=8, t=28, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", y=1.12),
        hovermode="x unified",
    )
    st.plotly_chart(chart, width="stretch", config={"displayModeBar": False})


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
        if name not in {"query_pi_tag", "latest_pi_tag"}:
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
        if payload.get("message"):
            st.caption(str(payload["message"]))
        frames.extend(payload.get("points") or [])
    if frames:
        frame = pd.DataFrame(frames)
        stamp = pd.to_datetime(frame["ts_utc"], utc=True).dt.tz_convert(desk_timezone())
        frame["time"] = stamp.dt.strftime("%Y-%m-%d %H:%M %Z")
        show = [column for column in ("tag", "time", "value", "unit", "quality") if column in frame.columns]
        _render_query_chart(frame.assign(chart_time=stamp), str(frames[0].get("unit") or ""))
        with st.expander(f"Samples ({len(frame)})", expanded=False):
            st.dataframe(frame[show], width="stretch", hide_index=True)
    elif calls and not any(call.get("name") == "list_tags" for call in calls):
        st.info("No tag samples in that range.")
    note = (result.get("message") or "").strip()
    # The chart/table already come from the tag store. Skip the model's own recap.
    if note and not frames:
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
    _ensure_feeds()
    st.title("Real Time Desk")
    st.caption("Public feeds · times in CT.")
    with st.sidebar:
        st.markdown("### Desk")
        st.checkbox("Auto refresh (60s)", value=True, key="auto_refresh")
        if st.button("Refresh live feeds", width="stretch"):
            st.session_state["force_pull"] = True
            st.session_state.pop("ollama_probe", None)
            st.rerun()
        render_integration_status_strip()
        st.caption(f"Times in {desk_timezone_name()}.")
        if st.button("Load synthetic sample", width="stretch"):
            load_synthetic_desk()
            st.rerun()

    _desk_fragment()
    _model_panel()
    _calculator()
