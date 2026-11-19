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
from spire_reactor.ingest.ercot_grid import load_grid_snapshot
from spire_reactor.ingest.public_feeds import DEFAULT_LAT, DEFAULT_LON, fetch_radar_overlay
from spire_reactor.model.ollama_desk import ask_desk, ollama_model, probe_ollama
from spire_reactor.tags.derived import (
    houston_north_spread,
    houston_north_spread_at,
    implied_heat_rate,
    implied_heat_rate_at,
)
from spire_reactor.tags.schema import (
    QUALITY_SYNTHETIC,
    desk_timezone,
    desk_timezone_name,
    eia_price_tag,
    ercot_lmp_tag,
    format_local,
    format_period_date,
    key_tags,
    utc_now,
    weather_site,
)
from spire_reactor.tags.staleness import evaluate_all
from spire_reactor.tags.store import get_store

from dashboard.setup_stage import render_integration_status_strip

_UNIT_LABEL = {"degC": "°F", "km/h": "mph"}
LMP_THRESHOLD_USD = 100.0
LMP_CHART_HOURS = 24
DESK_FOOTER = "Public data only. No recommendations or trading signals."


def _shown_unit(unit: str, tag: str = "") -> str:
    if unit == "degC" or tag.endswith(".TEMP_C"):
        return "°F"
    if unit == "km/h" or tag.endswith(".WIND_KMH"):
        return "mph"
    return _UNIT_LABEL.get(unit, unit)


def _to_display(value: float, unit: str, tag: str = "") -> float:
    if unit == "degC" or tag.endswith(".TEMP_C"):
        return value * 9.0 / 5.0 + 32.0
    if unit == "km/h" or tag.endswith(".WIND_KMH"):
        return value / 1.609344
    return value


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


def _format_value(value: float, unit: str, tag: str = "") -> str:
    shown = _to_display(value, unit, tag)
    shown_unit = _shown_unit(unit, tag)
    if shown_unit == "%":
        text = f"{shown:.0f}"
    elif shown_unit in {"°F", "°C"}:
        text = f"{shown:.1f}"
    elif shown_unit == "mph":
        text = f"{shown:.1f}"
    else:
        text = f"{shown:.2f}"
    return f"{text} {shown_unit}"


def _format_delta(delta: float, unit: str, tag: str = "") -> str:
    shown_unit = _shown_unit(unit, tag)
    if shown_unit == "%":
        return f"{delta:+.0f}"
    if shown_unit in {"°F", "°C", "mph"}:
        return f"{delta:+.1f}"
    return f"{delta:+.2f}"


def _prior_and_ago(store: Any, tag: str, sample: Any, *, quality: str, cadence: str) -> tuple[Any, Any]:
    """Previous stored interval, and the sample from ~24h earlier. EIA uses the prior trading day only."""
    if cadence == "daily":
        history = store.series(tag, sample.ts_utc - timedelta(days=21), sample.ts_utc - timedelta(seconds=1), quality=quality)
        prior = history[-1] if history else None
        return prior, None
    history = store.series(
        tag,
        sample.ts_utc - timedelta(hours=6),
        sample.ts_utc - timedelta(seconds=1),
        quality=quality,
    )
    prior = history[-1] if history else None
    ago = store.latest_at(tag, sample.ts_utc - timedelta(hours=24), quality=quality)
    if ago is not None and ago.ts_utc == sample.ts_utc:
        ago = None
    return prior, ago


def _delta_caption(sample: Any, prior: Any, ago: Any, unit: str, tag: str, *, daily: bool) -> str:
    parts: list[str] = []
    current = _to_display(float(sample.value), unit, tag)
    if prior is not None and prior.value is not None:
        previous = _to_display(float(prior.value), unit, tag)
        label = "vs prior day" if daily else "vs prior"
        parts.append(f"{_format_delta(current - previous, unit, tag)} {label}")
    if not daily and ago is not None and ago.value is not None:
        previous = _to_display(float(ago.value), unit, tag)
        parts.append(f"{_format_delta(current - previous, unit, tag)} vs 24h")
    return " · ".join(parts)


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


def _tile(spec: Any, *, show_sparkline: bool = False) -> None:
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
    st.metric(_tile_label(spec), prefix + _format_value(float(sample.value), spec.unit, spec.tag))
    window = timedelta(days=90) if spec.cadence == "daily" else timedelta(hours=36)
    points = store.series(spec.tag, utc_now() - window, utc_now(), quality=quality)
    if show_sparkline and len(points) >= 2:
        frame = pd.DataFrame(
            {"value": [_to_display(float(point.value), spec.unit, spec.tag) for point in points if point.value is not None]},
            index=pd.to_datetime([point.ts_utc for point in points if point.value is not None], utc=True),
        )
        st.line_chart(frame, height=72)
    when = format_period_date(sample.ts_utc) if spec.cadence == "daily" else format_local(sample.ts_utc)
    prior, ago = _prior_and_ago(store, spec.tag, sample, quality=quality, cadence=spec.cadence)
    deltas = _delta_caption(sample, prior, ago, spec.unit, spec.tag, daily=spec.cadence == "daily")
    st.caption(f"{when}" + (f" · {deltas}" if deltas else ""))


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


def _weather_radar_map(*, height: int = 260) -> None:
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
        height=height,
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


def _derived_delta_line(current: float, prior: float | None, ago: float | None, unit: str, *, daily: bool = False) -> str:
    parts: list[str] = []
    if prior is not None:
        label = "vs prior day" if daily else "vs prior"
        parts.append(f"{current - prior:+.2f} {label}")
    if ago is not None:
        parts.append(f"{current - ago:+.2f} vs 24h")
    if unit and parts:
        return " · ".join(parts) + f" {unit}"
    return " · ".join(parts)


def _heat_rate_and_spread_row() -> None:
    store = get_store()
    quality = QUALITY_SYNTHETIC if store.mode() == "synthetic" else "good"
    heat = implied_heat_rate(store=store, quality=quality)
    spread = houston_north_spread(store=store, quality=quality)
    left, mid, right = st.columns(3)
    henry_spec = next((spec for spec in key_tags() if spec.tag == eia_price_tag()), None)
    with left:
        if henry_spec is not None:
            _tile(henry_spec, show_sparkline=False)
        else:
            st.metric("Henry Hub", "—")
            st.caption("source unavailable")
    with mid:
        if heat["ok"]:
            history = store.series(
                heat["houston"].tag,
                heat["houston"].ts_utc - timedelta(hours=6),
                heat["houston"].ts_utc - timedelta(seconds=1),
                quality=quality,
            )
            prior = implied_heat_rate_at(history[-1].ts_utc, store=store, quality=quality) if history else None
            ago = implied_heat_rate_at(heat["houston"].ts_utc - timedelta(hours=24), store=store, quality=quality)
            when = format_local(heat["houston"].ts_utc)
            deltas = _derived_delta_line(float(heat["value"]), prior, ago, heat["unit"])
            _derived_metric(
                heat["label"],
                f"{heat['value']:.2f} {heat['unit']}",
                f"{when}" + (f" · {deltas}" if deltas else ""),
            )
        else:
            missing = ", ".join(heat["missing"]) or "inputs unavailable"
            _derived_metric(heat["label"], "—", missing)
    with right:
        if spread["ok"]:
            history = store.series(
                spread["houston"].tag,
                spread["houston"].ts_utc - timedelta(hours=6),
                spread["houston"].ts_utc - timedelta(seconds=1),
                quality=quality,
            )
            prior = houston_north_spread_at(history[-1].ts_utc, store=store, quality=quality) if history else None
            ago = houston_north_spread_at(spread["houston"].ts_utc - timedelta(hours=24), store=store, quality=quality)
            houston_when = format_local(spread["houston"].ts_utc)
            north_when = format_local(spread["north"].ts_utc)
            deltas = _derived_delta_line(float(spread["value"]), prior, ago, spread["unit"])
            _derived_metric(
                spread["label"],
                f"{spread['value']:.2f} {spread['unit']}",
                f"HB_HOUSTON {houston_when} · HB_NORTH {north_when}" + (f" · {deltas}" if deltas else ""),
            )
        else:
            missing = ", ".join(spread["missing"]) or "inputs unavailable"
            _derived_metric(spread["label"], "—", missing)


def _count_threshold_crossings(values: list[float], threshold: float = LMP_THRESHOLD_USD) -> int:
    count = 0
    for left, right in zip(values, values[1:], strict=False):
        if (left - threshold) * (right - threshold) < 0:
            count += 1
        elif left != threshold and right == threshold:
            count += 1
    return count


def _lmp_threshold_chart() -> None:
    store = get_store()
    quality = QUALITY_SYNTHETIC if store.mode() == "synthetic" else "good"
    tag = ercot_lmp_tag("HB_HOUSTON")
    end = utc_now()
    start = end - timedelta(hours=LMP_CHART_HOURS)
    points = [row for row in store.series(tag, start, end, quality=quality) if row.value is not None]
    st.markdown("**Houston LMP**")
    if len(points) < 2:
        st.caption("Not enough Houston LMP samples in the last 24 hours for a chart.")
        return
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime([point.ts_utc for point in points], utc=True).tz_convert(desk_timezone()),
            "USD/MWh": [float(point.value) for point in points],
        }
    )
    chart = px.line(frame, x="time", y="USD/MWh")
    chart.update_traces(line={"width": 2, "color": "#0f172a"}, hovertemplate="%{x|%H:%M}<br>%{y:.2f} USD/MWh<extra></extra>")
    chart.add_hline(
        y=LMP_THRESHOLD_USD,
        line_dash="dot",
        line_color="#b45309",
        line_width=1.5,
        annotation_text="$100/MWh threshold",
        annotation_position="top left",
        annotation_font_size=11,
        annotation_font_color="#b45309",
    )
    chart.update_xaxes(tickformat="%H:%M", nticks=8, title="", showgrid=False, ticks="outside")
    chart.update_yaxes(title="USD/MWh", gridcolor="#e5e7eb", zeroline=False, ticks="outside")
    chart.update_layout(
        height=220,
        margin=dict(l=8, r=8, t=16, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        showlegend=False,
    )
    st.plotly_chart(chart, width="stretch", config={"displayModeBar": False})
    crossings = _count_threshold_crossings([float(point.value) for point in points])
    first = _clock(points[0].ts_utc)
    last = _clock(points[-1].ts_utc)
    st.caption(
        f"$100/MWh threshold line · {crossings} crossing"
        f"{'' if crossings == 1 else 's'} in this window · {first}–{last}"
    )


def _grid_pills_html() -> str:
    snapshot = load_grid_snapshot() or {}
    if not snapshot.get("ok"):
        reason = str(snapshot.get("reason") or snapshot.get("message") or "no public PRC/EEA reading yet").strip()
        return (
            '<span class="desk-chip desk-badge badge-muted grid-pill">'
            '<span class="chip-name">Grid</span> '
            '<span class="chip-state">unavailable</span>'
            f'<span class="chip-asof">{html.escape(reason)}</span>'
            "</span>"
        )
    prc = snapshot.get("prc_mw")
    eea = snapshot.get("eea_level")
    title = str(snapshot.get("title") or snapshot.get("state_name") or "").strip()
    observed = snapshot.get("observed_at")
    as_of = _clock(observed) if observed is not None else ""
    prc_text = f"{float(prc):,.0f} MW" if isinstance(prc, (int, float)) else "—"
    try:
        level = int(eea) if eea is not None else None
    except (TypeError, ValueError):
        level = None
    if level is None:
        eea_kind = "badge-muted"
        eea_text = title or "unavailable"
    elif level <= 0:
        eea_kind = "badge-green"
        eea_text = title or "Normal Conditions"
    elif level == 1:
        eea_kind = "badge-amber"
        eea_text = title or "EEA 1"
    else:
        eea_kind = "badge-red"
        eea_text = title or f"EEA {level}"
    eea_detail = f"EEA {level}" if level is not None else ""
    as_of_html = f'<span class="chip-asof">as of {html.escape(as_of)}</span>' if as_of else ""
    eea_extra = f'<span class="chip-asof">{html.escape(eea_detail)}</span>' if eea_detail else ""
    prc_pill = (
        '<span class="desk-chip desk-badge badge-green grid-pill">'
        '<span class="chip-name">PRC</span> '
        f'<span class="chip-state">{html.escape(prc_text)}</span>'
        f"{as_of_html}"
        "</span>"
    )
    eea_pill = (
        f'<span class="desk-chip desk-badge {eea_kind} grid-pill">'
        '<span class="chip-name">EEA</span> '
        f'<span class="chip-state">{html.escape(eea_text)}</span>'
        f"{eea_extra}"
        "</span>"
    )
    return prc_pill + eea_pill


def _desk_footer() -> None:
    st.markdown(f'<div class="desk-footer">{html.escape(DESK_FOOTER)}</div>', unsafe_allow_html=True)


@st.fragment(run_every=timedelta(seconds=60))
def _desk_fragment() -> None:
    _ensure_feeds()
    store = get_store()
    if store.mode() == "synthetic":
        st.warning("SYNTHETIC — offline sample, not a measurement. Refresh live feeds to clear it.")

    chips = " ".join(_feed_chip_html(row) for row in evaluate_all(store=store))
    st.markdown(f'<div class="desk-bar">{chips} {_grid_pills_html()}</div>', unsafe_allow_html=True)

    fetched = store.fetched_at()
    clock = _clock(utc_now())
    pulled = _clock(fetched) if fetched else "not yet"
    st.caption(f"{clock} · last pull {pulled}")

    grouped: dict[str, list[Any]] = {}
    for spec in key_tags():
        grouped.setdefault(spec.source, []).append(spec)

    weather = grouped.get("open-meteo") or []
    if weather:
        columns = st.columns(len(weather))
        for index, spec in enumerate(weather):
            with columns[index]:
                _tile(spec, show_sparkline=False)

    hubs = grouped.get("ercot") or []
    if hubs:
        columns = st.columns(min(4, len(hubs)))
        for index, spec in enumerate(hubs):
            with columns[index % len(columns)]:
                _tile(spec, show_sparkline=False)

    left, right = st.columns([1.55, 1], gap="medium")
    with left:
        _heat_rate_and_spread_row()
        _lmp_threshold_chart()
    with right:
        st.markdown("**Radar**")
        _weather_radar_map(height=300)
    _desk_footer()

    eia_specs = grouped.get("eia") or []
    if eia_specs:
        with st.expander("Henry Hub history (90 days)", expanded=False):
            _eia_history_chart(eia_specs[0])

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


def _operator_name() -> str:
    return str(st.session_state.get("desk_operator") or "").strip() or "operator"


def _model_panel() -> None:
    status = _ollama_status()
    st.subheader("Desk query")
    if not status.get("ok"):
        st.caption(str(status.get("message") or "Local model unavailable. Pinned panels stay on this machine."))
    else:
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
    pin_name = st.text_input("Pin name", placeholder="Optional title for a pinned panel", key="desk_pin_name")
    ask_col, pin_col = st.columns([1, 1])
    with ask_col:
        asked = st.button("Ask", type="primary", key="desk_ask", width="stretch")
    with pin_col:
        pinned = st.button("Pin this query", key="desk_pin", width="stretch")
    if asked:
        if not status.get("ok"):
            st.warning("Local model unavailable.")
        else:
            with st.spinner("Querying tags"):
                st.session_state["desk_query"] = ask_desk(question)
    if pinned:
        from spire_reactor.desk.pinned_queries import add_pin

        try:
            pin = add_pin(operator=_operator_name(), name=pin_name, question=question)
            st.session_state["desk_pin_flash"] = f"Pinned “{pin['name']}” for {_operator_name()}."
        except ValueError as exc:
            st.session_state["desk_pin_flash"] = str(exc)
        st.rerun()
    flash = st.session_state.pop("desk_pin_flash", None)
    if flash:
        st.caption(str(flash))
    result = st.session_state.get("desk_query")
    if isinstance(result, dict):
        _render_query_result(result)


def _pinned_queries_panel() -> None:
    from spire_reactor.desk.pinned_queries import load_pins, pins_path, remove_pin

    operator = _operator_name()
    pins = load_pins(operator=operator)
    st.subheader("Pinned queries")
    st.caption(
        f"Stored on this machine for operator “{operator}” · {pins_path()}"
    )
    if not pins:
        st.caption("No pinned queries yet.")
        return
    status = _ollama_status()
    for pin in pins:
        pin_id = str(pin["id"])
        with st.container(border=True):
            st.markdown(f"**{pin['name']}**")
            st.caption(pin["question"])
            run_col, unpin_col = st.columns([1, 1])
            with run_col:
                run = st.button("Run", key=f"pin_run_{pin_id}", width="stretch")
            with unpin_col:
                unpin = st.button("Unpin", key=f"pin_unpin_{pin_id}", width="stretch")
            if unpin:
                remove_pin(pin_id)
                st.session_state.pop(f"desk_query_pin_{pin_id}", None)
                st.rerun()
            if run:
                if not status.get("ok"):
                    st.warning("Local model unavailable.")
                else:
                    with st.spinner("Querying tags"):
                        st.session_state[f"desk_query_pin_{pin_id}"] = ask_desk(pin["question"])
            result = st.session_state.get(f"desk_query_pin_{pin_id}")
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
    st.caption("Public feeds · times in CT. Public data only.")
    with st.sidebar:
        st.markdown("### Desk")
        st.checkbox("Auto refresh (60s)", value=True, key="auto_refresh")
        st.text_input("Operator name", value="operator", key="desk_operator")
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
    _pinned_queries_panel()
    _calculator()
