"""In-process history for public tags. Query results come only from recorded samples."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from spire_reactor.tags.schema import (
    QUALITY_GOOD,
    TagSpec,
    catalog,
    format_utc,
    spec_for,
    utc_now,
)

_MAX_POINTS = 800
_LAST = re.compile(
    r"^last\s+(\d+(?:\.\d+)?)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$",
    re.IGNORECASE,
)
_REL = re.compile(r"^-(\d+(?:\.\d+)?)(m|min|h|hr|d)$", re.IGNORECASE)
_AGGS = frozenset({"raw", "avg", "min", "max", "last"})


@dataclass
class TagSample:
    tag: str
    ts_utc: datetime
    value: float | None
    unit: str
    quality: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        moment = self.ts_utc.astimezone(timezone.utc)
        return {
            "tag": self.tag,
            "ts_utc": moment.isoformat(),
            "value": self.value,
            "unit": self.unit,
            "quality": self.quality,
            "source": self.source,
        }


def _delta(amount: str, unit: str) -> timedelta:
    n = float(amount)
    u = unit.lower()
    if u.startswith("m"):
        return timedelta(minutes=n)
    if u.startswith("h"):
        return timedelta(hours=n)
    return timedelta(days=n)


def parse_time(text: str | None, *, now: datetime, default: datetime) -> datetime:
    """
    Parse a tool timestamp.

    Accepts UTC ISO-8601 (Z or offset), 'now', '-1h' / '-15m' / '-1d',
    and 'last 1 hour'. Naive values are UTC. Relative forms use ``now``.
    """
    if text is None or str(text).strip() == "":
        return default
    raw = str(text).strip()
    low = raw.lower()
    if low in {"now", "current"}:
        return now
    matched = _LAST.match(low)
    if matched:
        return now - _delta(matched.group(1), matched.group(2))
    matched = _REL.match(low)
    if matched:
        return now - _delta(matched.group(1), matched.group(2))
    cleaned = raw.replace("Z", "+00:00")
    if len(cleaned) >= 5 and cleaned[-5] in "+-" and cleaned[-3] != ":":
        cleaned = cleaned[:-2] + ":" + cleaned[-2:]
    if " " in cleaned and "T" not in cleaned:
        cleaned = cleaned.replace(" ", "T", 1)
    try:
        moment = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(f"unrecognized time {raw!r}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


class TagStore:
    """Process-local samples and source health. Safe to call from the desk and tools."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._points: dict[str, dict[str, TagSample]] = {}
        self._health: list[dict[str, Any]] = []
        self._mode = "live"
        self._fetched_at: datetime | None = None

    def mode(self) -> str:
        with self._lock:
            return self._mode

    def set_mode(self, mode: str) -> None:
        with self._lock:
            self._mode = mode

    def fetched_at(self) -> datetime | None:
        with self._lock:
            return self._fetched_at

    def mark_fetched(self, moment: datetime | None = None) -> None:
        with self._lock:
            self._fetched_at = moment or utc_now()

    def set_health(self, rows: list[dict[str, Any]]) -> None:
        with self._lock:
            self._health = list(rows)

    def health(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._health)

    def health_for(self, source: str) -> dict[str, Any] | None:
        with self._lock:
            for row in self._health:
                if row.get("source") == source:
                    return dict(row)
        return None

    def upsert(self, samples: list[TagSample]) -> None:
        with self._lock:
            for sample in samples:
                if sample.value is None:
                    continue
                bucket = self._points.setdefault(sample.tag, {})
                key = sample.ts_utc.astimezone(timezone.utc).isoformat()
                bucket[key] = sample
                if len(bucket) > _MAX_POINTS:
                    for old in sorted(bucket)[: len(bucket) - _MAX_POINTS]:
                        del bucket[old]

    def replace_quality(self, quality: str, samples: list[TagSample]) -> None:
        with self._lock:
            for bucket in self._points.values():
                for key in [k for k, sample in bucket.items() if sample.quality == quality]:
                    del bucket[key]
        self.upsert(samples)

    def series(
        self,
        tag: str,
        start: datetime,
        end: datetime,
        *,
        quality: str = QUALITY_GOOD,
    ) -> list[TagSample]:
        spec = spec_for(tag)
        canonical = spec.tag if spec else tag
        with self._lock:
            bucket = list(self._points.get(canonical, {}).values())
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        picked = [
            sample
            for sample in bucket
            if sample.quality == quality
            and start_utc <= sample.ts_utc.astimezone(timezone.utc) <= end_utc
        ]
        picked.sort(key=lambda sample: sample.ts_utc)
        return picked

    def latest(self, tag: str, *, quality: str = QUALITY_GOOD) -> TagSample | None:
        spec = spec_for(tag)
        canonical = spec.tag if spec else tag
        with self._lock:
            bucket = list(self._points.get(canonical, {}).values())
        picked = [sample for sample in bucket if sample.quality == quality and sample.value is not None]
        if not picked:
            return None
        return max(picked, key=lambda sample: sample.ts_utc)

    def list_tags(self, filter_text: str | None = None) -> list[dict[str, Any]]:
        needle = (filter_text or "").strip().lower()
        rows: list[dict[str, Any]] = []
        for spec in catalog().values():
            blob = " ".join((spec.tag, spec.source, spec.unit, spec.description)).lower()
            if needle and needle not in blob:
                continue
            rows.append(self._tag_row(spec))
        return rows

    def _tag_row(self, spec: TagSpec) -> dict[str, Any]:
        health = self.health_for(spec.source) or {}
        state = str(health.get("state") or "unavailable")
        latest = self.latest(spec.tag, quality=QUALITY_GOOD)
        value = latest.value if latest and state == "ok" else None
        ts = format_utc(latest.ts_utc) if latest and value is not None else None
        return {
            "tag": spec.tag,
            "source": spec.source,
            "unit": spec.unit,
            "description": spec.description,
            "cadence": spec.cadence,
            "key": spec.key,
            "source_state": state,
            "latest_value": value,
            "latest_ts_utc": None if latest is None or value is None else latest.ts_utc.isoformat(),
            "latest_display": ts,
            "quality": QUALITY_GOOD if value is not None else None,
        }

    def query_pi_tag(
        self,
        tag: str,
        start: str | None = None,
        end: str | None = None,
        agg: str | None = None,
        *,
        quality: str = QUALITY_GOOD,
    ) -> dict[str, Any]:
        """
        Read stored samples for one catalog tag.

        Missing tags and failed feeds return an empty point list. Values are
        never filled in.
        """
        spec = spec_for(tag)
        if spec is None:
            return {
                "tag": tag,
                "error": "unknown tag",
                "points": [],
                "source_state": "unavailable",
            }
        health = self.health_for(spec.source) or {}
        state = str(health.get("state") or "unavailable")
        now = utc_now()
        try:
            end_dt = parse_time(end, now=now, default=now)
            start_dt = parse_time(start, now=now, default=end_dt - timedelta(hours=6))
        except ValueError as exc:
            return {
                "tag": spec.tag,
                "unit": spec.unit,
                "error": str(exc),
                "points": [],
                "source_state": state,
            }
        if start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt
        agg_name = (agg or "raw").strip().lower() or "raw"
        if agg_name not in _AGGS:
            return {
                "tag": spec.tag,
                "unit": spec.unit,
                "error": f"unknown agg {agg_name!r}",
                "points": [],
                "source_state": state,
            }
        if state == "unavailable":
            return {
                "tag": spec.tag,
                "unit": spec.unit,
                "source": spec.source,
                "source_state": "unavailable",
                "agg": agg_name,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "aggregate_value": None,
                "points": [],
                "error": "source unavailable",
                "message": health.get("message") or "source unavailable",
            }
        samples = self.series(spec.tag, start_dt, end_dt, quality=quality)
        values = [sample.value for sample in samples if sample.value is not None]
        aggregate: float | None = None
        if values and agg_name == "avg":
            aggregate = sum(values) / len(values)
        elif values and agg_name == "min":
            aggregate = min(values)
        elif values and agg_name == "max":
            aggregate = max(values)
        elif values and agg_name == "last":
            aggregate = values[-1]
        return {
            "tag": spec.tag,
            "unit": spec.unit,
            "source": spec.source,
            "source_state": state,
            "cadence": spec.cadence,
            "agg": agg_name,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "aggregate_value": None if aggregate is None else round(float(aggregate), 4),
            "points": [sample.to_dict() for sample in samples],
            "error": None,
        }


_STORE = TagStore()


def get_store() -> TagStore:
    return _STORE
