"""
Public tag schema for the Real Time Desk.

Naming (three uppercase segments, dots between them)::

    {FAMILY}.{ENTITY}.{VARIABLE}

    WX.{SITE}.{VAR}          weather at a public site label
    EIA.{SERIES}.{ID}        EIA Open Data series
    {ISO}.{NODE}.LMP         public ISO hub or load-zone price

Timestamps are stored in UTC. The desk displays them in DESK_TIMEZONE
(default America/Chicago). Daily EIA periods stay on the UTC period date.

This map is original. It uses public settlement-point names, public series
ids, and a site label for the configured coordinates. It is not a copy of
any private PI catalog, plant list, or warehouse schema.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

QUALITY_GOOD = "good"
QUALITY_UNAVAILABLE = "unavailable"
QUALITY_SYNTHETIC = "synthetic"

_TOKEN = re.compile(r"[^A-Z0-9]+")


def sanitize_token(value: str) -> str:
    """Uppercase token safe for a single tag segment."""
    token = _TOKEN.sub("_", (value or "").strip().upper()).strip("_")
    if not token:
        raise ValueError("empty tag segment")
    return token


def tag_name(family: str, entity: str, variable: str) -> str:
    return f"{sanitize_token(family)}.{sanitize_token(entity)}.{sanitize_token(variable)}"


def weather_site() -> str:
    return sanitize_token(os.getenv("WX_SITE", "HOUSTON"))


def desk_timezone() -> ZoneInfo:
    name = (os.getenv("DESK_TIMEZONE") or "America/Chicago").strip() or "America/Chicago"
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — bad env must not take the desk down
        return ZoneInfo("UTC")


def desk_timezone_name() -> str:
    tz = desk_timezone()
    return getattr(tz, "key", None) or "UTC"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_local(moment: datetime) -> str:
    return moment.astimezone(desk_timezone()).strftime("%Y-%m-%d %H:%M %Z")


def format_period_date(moment: datetime) -> str:
    """EIA daily periods are the UTC calendar date, not a clock time."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d")


# Public ERCOT hubs and load zones from the public price dashboard.
# Resource nodes are intentionally absent.
ERCOT_DASHBOARD_FIELDS: dict[str, str] = {
    "hbHouston": "HB_HOUSTON",
    "hbNorth": "HB_NORTH",
    "hbSouth": "HB_SOUTH",
    "hbWest": "HB_WEST",
    "hbPan": "HB_PAN",
    "hbBusAvg": "HB_BUSAVG",
    "hbHubAvg": "HB_HUBAVG",
    "lzHouston": "LZ_HOUSTON",
    "lzNorth": "LZ_NORTH",
    "lzSouth": "LZ_SOUTH",
    "lzWest": "LZ_WEST",
    "lzAen": "LZ_AEN",
    "lzCps": "LZ_CPS",
    "lzLcra": "LZ_LCRA",
    "lzRaybn": "LZ_RAYBN",
}

ERCOT_KEY_ENTITIES = ("HB_HOUSTON", "HB_NORTH", "HB_SOUTH", "HB_WEST")

# Public PJM trading hubs. Bus-level nodes are intentionally absent.
PJM_HUBS: tuple[str, ...] = (
    "WESTERN HUB",
    "EASTERN HUB",
    "AEP-DAYTON HUB",
    "DOMINION HUB",
)

EIA_SERIES_FAMILY = "NG"
EIA_SERIES_ID = "RNGWHHD"


def eia_series_id() -> str:
    raw = (os.getenv("EIA_SERIES_ID") or EIA_SERIES_ID).strip() or EIA_SERIES_ID
    return sanitize_token(raw)


def ercot_lmp_tag(entity: str) -> str:
    return tag_name("ERCOT", entity, "LMP")


def pjm_lmp_tag(hub_name: str) -> str:
    return tag_name("PJM", hub_name, "LMP")


def eia_price_tag() -> str:
    return tag_name("EIA", EIA_SERIES_FAMILY, eia_series_id())


def weather_tag(variable: str, site: str | None = None) -> str:
    return tag_name("WX", site or weather_site(), variable)


@dataclass(frozen=True)
class TagSpec:
    tag: str
    source: str
    unit: str
    description: str
    cadence: str  # "instant" or "daily"
    key: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "tag": self.tag,
            "source": self.source,
            "unit": self.unit,
            "description": self.description,
            "cadence": self.cadence,
            "key": self.key,
        }


def _weather_specs() -> list[TagSpec]:
    site = weather_site()
    return [
        TagSpec(
            tag=weather_tag("TEMP_C", site),
            source="open-meteo",
            unit="degC",
            description=f"Open-Meteo 2 m temperature at {site}",
            cadence="instant",
            key=True,
        ),
        TagSpec(
            tag=weather_tag("WIND_KMH", site),
            source="open-meteo",
            unit="km/h",
            description=f"Open-Meteo 10 m wind speed at {site}",
            cadence="instant",
            key=True,
        ),
        TagSpec(
            tag=weather_tag("RH_PCT", site),
            source="open-meteo",
            unit="%",
            description=f"Open-Meteo 2 m relative humidity at {site}",
            cadence="instant",
            key=True,
        ),
    ]


def _eia_specs() -> list[TagSpec]:
    series_id = eia_series_id()
    return [
        TagSpec(
            tag=eia_price_tag(),
            source="eia",
            unit="USD/MMBtu",
            description=f"EIA natural gas series {series_id} (Henry Hub daily spot when id is RNGWHHD)",
            cadence="daily",
            key=True,
        )
    ]


def _ercot_specs() -> list[TagSpec]:
    specs: list[TagSpec] = []
    for entity in ERCOT_DASHBOARD_FIELDS.values():
        specs.append(
            TagSpec(
                tag=ercot_lmp_tag(entity),
                source="ercot",
                unit="USD/MWh",
                description=f"ERCOT real-time settlement point price {entity}",
                cadence="instant",
                key=entity in ERCOT_KEY_ENTITIES,
            )
        )
    return specs


def _pjm_specs() -> list[TagSpec]:
    return [
        TagSpec(
            tag=pjm_lmp_tag(hub),
            source="pjm",
            unit="USD/MWh",
            description=f"PJM real-time unverified five-minute LMP at {hub}",
            cadence="instant",
            key=True,
        )
        for hub in PJM_HUBS
    ]


def catalog() -> dict[str, TagSpec]:
    """All public tags the desk will query. Built from env (site, EIA id)."""
    specs: Iterable[TagSpec] = (
        *_weather_specs(),
        *_eia_specs(),
        *_ercot_specs(),
        *_pjm_specs(),
    )
    return {spec.tag: spec for spec in specs}


def key_tags() -> list[TagSpec]:
    return [spec for spec in catalog().values() if spec.key]


def spec_for(tag: str) -> TagSpec | None:
    wanted = (tag or "").strip().upper()
    items = catalog()
    if wanted in items:
        return items[wanted]
    for spec in items.values():
        if spec.tag.upper() == wanted:
            return spec
    return None
