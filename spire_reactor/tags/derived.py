"""Display-only derived desk values. Never invent a number when an input is missing."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from spire_reactor.tags.schema import QUALITY_GOOD, eia_price_tag, ercot_lmp_tag, format_period_date
from spire_reactor.tags.store import TagSample, get_store

HOUSTON_ENTITY = "HB_HOUSTON"
NORTH_ENTITY = "HB_NORTH"


def houston_lmp_tag() -> str:
    return ercot_lmp_tag(HOUSTON_ENTITY)


def north_lmp_tag() -> str:
    return ercot_lmp_tag(NORTH_ENTITY)


def gas_period_label(moment: datetime | None) -> str:
    """Short visible EIA period for the heat-rate label (month, day, year)."""
    if moment is None:
        return "no period"
    # Use the UTC calendar date of the EIA period, not a zoned clock time.
    iso = format_period_date(moment)
    year, month, day = iso.split("-")
    months = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )
    return f"{months[int(month) - 1]} {int(day)}, {year}"


def _sample(store: Any, tag: str, *, quality: str = QUALITY_GOOD) -> TagSample | None:
    sample = store.latest(tag, quality=quality)
    if sample is None or sample.value is None:
        return None
    return sample


def implied_heat_rate(*, store: Any | None = None, quality: str = QUALITY_GOOD) -> dict[str, Any]:
    """Houston LMP (USD/MWh) / Henry Hub (USD/MMBtu) → MMBtu/MWh."""
    store = store or get_store()
    houston = _sample(store, houston_lmp_tag(), quality=quality)
    henry = _sample(store, eia_price_tag(), quality=quality)
    gas_date = gas_period_label(henry.ts_utc if henry else None)
    label = f"Houston LMP ÷ Henry Hub (EIA, {gas_date})"
    missing: list[str] = []
    if houston is None:
        missing.append("Houston LMP")
    if henry is None:
        missing.append("Henry Hub")
    if missing:
        return {
            "ok": False,
            "value": None,
            "unit": "MMBtu/MWh",
            "label": label,
            "gas_date": gas_date,
            "houston": houston,
            "henry": henry,
            "missing": missing,
        }
    henry_value = float(henry.value)
    if henry_value == 0:
        return {
            "ok": False,
            "value": None,
            "unit": "MMBtu/MWh",
            "label": label,
            "gas_date": gas_date,
            "houston": houston,
            "henry": henry,
            "missing": ["Henry Hub is zero"],
        }
    return {
        "ok": True,
        "value": float(houston.value) / henry_value,
        "unit": "MMBtu/MWh",
        "label": label,
        "gas_date": gas_date,
        "houston": houston,
        "henry": henry,
        "missing": [],
    }


def houston_north_spread(*, store: Any | None = None, quality: str = QUALITY_GOOD) -> dict[str, Any]:
    """HB_HOUSTON minus HB_NORTH, USD/MWh."""
    store = store or get_store()
    houston = _sample(store, houston_lmp_tag(), quality=quality)
    north = _sample(store, north_lmp_tag(), quality=quality)
    missing: list[str] = []
    if houston is None:
        missing.append("Houston LMP")
    if north is None:
        missing.append("North LMP")
    label = "Houston–North hub spread"
    if missing:
        return {
            "ok": False,
            "value": None,
            "unit": "USD/MWh",
            "label": label,
            "houston": houston,
            "north": north,
            "missing": missing,
        }
    return {
        "ok": True,
        "value": float(houston.value) - float(north.value),
        "unit": "USD/MWh",
        "label": label,
        "houston": houston,
        "north": north,
        "missing": [],
    }
