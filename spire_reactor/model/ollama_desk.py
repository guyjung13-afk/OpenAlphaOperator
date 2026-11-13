"""
Turn a desk question into tag-store reads via a local Ollama model.

Default server: http://127.0.0.1:11434
Default model:  qwen3.5:9b  (OLLAMA_MODEL)

The model may only call list_tags and query_pi_tag. Charts are drawn from
those tool results. If the server is down, callers get ok=False and the
dashboard hides the query panel.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from spire_reactor.tags.schema import catalog, desk_timezone, desk_timezone_name
from spire_reactor.tags.store import TagStore, get_store

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.5:9b"

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_tags",
            "description": "List public desk tags. filter is an optional substring of the tag, source, unit, or description.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "Optional substring. Empty lists every public tag.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_pi_tag",
            "description": (
                "Read stored public samples for one tag. "
                "start and end are UTC ISO-8601, now, -1h, or 'last 1 hour'. "
                "agg is raw, avg, min, max, or last. Does not invent values."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "agg": {
                        "type": "string",
                        "enum": ["raw", "avg", "min", "max", "last"],
                    },
                },
                "required": ["tag"],
            },
        },
    },
]


def ollama_host() -> str:
    return (os.getenv("OLLAMA_HOST") or DEFAULT_HOST).strip().rstrip("/") or DEFAULT_HOST


def ollama_model() -> str:
    return (os.getenv("OLLAMA_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def system_prompt(now: datetime | None = None) -> str:
    """Clock is injected on every call so 'last hour' has a real anchor."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    tz_name = desk_timezone_name()
    local = moment.astimezone(desk_timezone())
    names = ", ".join(spec.tag for spec in catalog().values())
    return (
        "You translate one desk question into tag tool calls. "
        "You do not invent measurements, prices, or timestamps.\n"
        f"Current time UTC: {moment.isoformat()}\n"
        f"Desk timezone: {tz_name}\n"
        f"Current time in {tz_name}: {local.isoformat()}\n"
        "Resolve ranges such as 'last hour' from this clock into absolute UTC "
        "before calling query_pi_tag. Use list_tags to discover tags and "
        "query_pi_tag to read them. One query_pi_tag call per tag.\n"
        f"Known tags: {names}\n"
        "If a tool result says source unavailable, report that and do not supply a number."
    )


def probe_ollama(host: str | None = None) -> dict[str, Any]:
    base = (host or ollama_host()).rstrip("/")
    try:
        response = httpx.get(f"{base}/api/tags", timeout=httpx.Timeout(2.0, connect=1.0))
        response.raise_for_status()
        return {"ok": True, "host": base, "message": "Ollama reachable"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "host": base, "message": f"source unavailable: {exc}"}


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _execute(name: str, arguments: dict[str, Any], store: TagStore) -> dict[str, Any]:
    if name == "list_tags":
        return {"tags": store.list_tags(str(arguments.get("filter") or ""))}
    if name == "query_pi_tag":
        return store.query_pi_tag(
            str(arguments.get("tag") or ""),
            None if arguments.get("start") is None else str(arguments.get("start")),
            None if arguments.get("end") is None else str(arguments.get("end")),
            None if arguments.get("agg") is None else str(arguments.get("agg")),
        )
    return {"error": f"unknown tool {name}", "points": []}


def ask_desk(
    question: str,
    store: TagStore | None = None,
    *,
    host: str | None = None,
    model: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Ask the local model to call tag tools.

    The returned ``calls`` list is what the dashboard charts. Assistant prose
    is kept as ``message`` and is not treated as a measurement.
    """
    text = (question or "").strip()
    base = (host or ollama_host()).rstrip("/")
    chosen = (model or ollama_model()).strip()
    tag_store = store or get_store()
    if not text:
        return {"ok": False, "host": base, "model": chosen, "calls": [], "error": "empty question"}

    probe = probe_ollama(base)
    if not probe.get("ok"):
        return {
            "ok": False,
            "host": base,
            "model": chosen,
            "calls": [],
            "message": "",
            "error": probe.get("message") or "source unavailable",
        }

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(now)},
        {"role": "user", "content": text},
    ]
    calls: list[dict[str, Any]] = []
    note = ""
    try:
        with httpx.Client(timeout=httpx.Timeout(90.0, connect=2.0)) as client:
            for _round in range(3):
                body: dict[str, Any] = {
                    "model": chosen,
                    "messages": messages,
                    "tools": TOOLS,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0},
                }
                response = client.post(f"{base}/api/chat", json=body)
                if response.status_code == 400 and "think" in response.text.lower():
                    body.pop("think", None)
                    response = client.post(f"{base}/api/chat", json=body)
                response.raise_for_status()
                message = (response.json() or {}).get("message") or {}
                tool_calls = message.get("tool_calls") or []
                note = str(message.get("content") or "")
                messages.append(
                    {
                        "role": "assistant",
                        "content": note,
                        "tool_calls": tool_calls,
                    }
                )
                if not tool_calls:
                    break
                for call in tool_calls:
                    fn = call.get("function") if isinstance(call, dict) else None
                    if not isinstance(fn, dict):
                        continue
                    name = str(fn.get("name") or "")
                    arguments = _arguments(fn.get("arguments"))
                    result = _execute(name, arguments, tag_store)
                    calls.append({"name": name, "arguments": arguments, "result": result})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_name": name,
                            "content": json.dumps(result, default=str),
                        }
                    )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "host": base,
            "model": chosen,
            "calls": calls,
            "message": note,
            "error": f"source unavailable: {exc}",
        }

    return {
        "ok": True,
        "host": base,
        "model": chosen,
        "calls": calls,
        "message": note.strip(),
        "error": None,
    }
