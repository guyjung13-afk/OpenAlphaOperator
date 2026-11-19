"""
Per-feed freshness for the Real Time Desk.

Age is measured from the newest stored sample, not from the last pull.
'Live' is earned only while the sample is inside the feed's amber cutoff.

Cutoffs (from WAL-2026-11-19-02):
  ERCOT      amber after 15 minutes, red after 60 minutes
  Open-Meteo amber after 90 minutes, red after 6 hours
  EIA daily  amber if the period is older than 3 business days, red after 7 calendar days
  PJM        stays not connected unless a key is later attached
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from spire_reactor.tags.schema import (
    QUALITY_GOOD,
    QUALITY_SYNTHETIC,
    catalog,
    desk_timezone,
    format_period_date,
)
from spire_reactor.tags.store import get_store

# Instant feeds, seconds.
ERCOT_AMBER_SEC = 15 * 60
ERCOT_RED_SEC = 60 * 60
WEATHER_AMBER_SEC = 90 * 60
WEATHER_RED_SEC = 6 * 60 * 60

# Daily EIA: business-day and calendar-day cutoffs.
EIA_AMBER_BUSINESS_DAYS = 3
EIA_RED_CALENDAR_DAYS = 7

FEED_ORDER: tuple[tuple[str, str], ...] = (
    ("Weather", "open-meteo"),
    ("ERCOT", "ercot"),
    ("EIA", "eia"),
    ("PJM", "pjm"),
)


def business_days_after(start: datetime, end: datetime) -> int:
    """Count weekdays in (start.date, end.date]. Weekends only; no holiday calendar."""
    if end < start:
        return 0
    day = start.date()
    last = end.date()
    counted = 0
    while day < last:
        day = day + timedelta(days=1)
        if day.weekday() < 5:
            counted += 1
    return counted


def format_age(seconds: float | None, *, daily: bool = False) -> str:
    if seconds is None:
        return ""
    sec = max(0, int(seconds))
    if daily or sec >= 36 * 3600:
        days = sec // 86400
        if days <= 0:
            return f"{sec // 3600}h"
        return f"{days}d"
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60} min"
    hours = sec // 3600
    minutes = (sec % 3600) // 60
    if minutes and hours < 6:
        return f"{hours}h {minutes:02d}m"
    return f"{hours}h"


def _as_of_label(moment: datetime, *, daily: bool) -> str:
    if daily:
        return format_period_date(moment)
    local = moment.astimezone(desk_timezone())
    return local.strftime("%H:%M %Z")


def _cutoffs_for(source: str, as_of: datetime, now: datetime) -> tuple[str, float]:
    """Return (freshness, age_seconds) for a connected feed with a sample."""
    age = (now - as_of.astimezone(timezone.utc)).total_seconds()
    if source == "ercot":
        if age > ERCOT_RED_SEC:
            return "red", age
        if age > ERCOT_AMBER_SEC:
            return "amber", age
        return "live", age
    if source == "open-meteo":
        if age > WEATHER_RED_SEC:
            return "red", age
        if age > WEATHER_AMBER_SEC:
            return "amber", age
        return "live", age
    if source == "eia":
        calendar_days = (now.date() - as_of.astimezone(timezone.utc).date()).days
        biz = business_days_after(as_of.astimezone(timezone.utc), now)
        if calendar_days > EIA_RED_CALENDAR_DAYS:
            return "red", age
        if biz > EIA_AMBER_BUSINESS_DAYS:
            return "amber", age
        return "live", age
    # Default: treat like weather.
    if age > WEATHER_RED_SEC:
        return "red", age
    if age > WEATHER_AMBER_SEC:
        return "amber", age
    return "live", age


def as_of_for_source(store: Any, source: str, *, quality: str = QUALITY_GOOD) -> datetime | None:
    times: list[datetime] = []
    for spec in catalog().values():
        if spec.source != source or not spec.key:
            continue
        sample = store.latest(spec.tag, quality=quality)
        if sample is not None and sample.ts_utc is not None:
            times.append(sample.ts_utc)
    if not times:
        return None
    return max(times)


def evaluate_source(
    source: str,
    *,
    store: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Freshness for one public feed, computed at display time."""
    store = store or get_store()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    health = store.health_for(source) or {}
    raw_state = str(health.get("state") or "unavailable")
    names = {src: name for name, src in FEED_ORDER}
    name = names.get(source, source)

    if source == "pjm" and raw_state not in {"ok", "synthetic"}:
        return {
            "source": source,
            "name": name,
            "freshness": "not_connected",
            "as_of": None,
            "as_of_label": "",
            "age_seconds": None,
            "age_label": "",
            "daily": False,
        }
    if raw_state == "unavailable":
        return {
            "source": source,
            "name": name,
            "freshness": "unavailable",
            "as_of": None,
            "as_of_label": "",
            "age_seconds": None,
            "age_label": "",
            "daily": source == "eia",
        }
    quality = QUALITY_SYNTHETIC if raw_state == "synthetic" else QUALITY_GOOD
    as_of = as_of_for_source(store, source, quality=quality)
    daily = source == "eia"
    if as_of is None:
        return {
            "source": source,
            "name": name,
            "freshness": "unavailable",
            "as_of": None,
            "as_of_label": "",
            "age_seconds": None,
            "age_label": "",
            "daily": daily,
        }
    if raw_state == "synthetic":
        freshness, age = "synthetic", (now - as_of.astimezone(timezone.utc)).total_seconds()
    else:
        freshness, age = _cutoffs_for(source, as_of, now)
    return {
        "source": source,
        "name": name,
        "freshness": freshness,
        "as_of": as_of,
        "as_of_label": _as_of_label(as_of, daily=daily),
        "age_seconds": age,
        "age_label": format_age(age, daily=daily),
        "daily": daily,
    }


def evaluate_all(*, store: Any | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    store = store or get_store()
    return [evaluate_source(source, store=store, now=now) for _label, source in FEED_ORDER]
