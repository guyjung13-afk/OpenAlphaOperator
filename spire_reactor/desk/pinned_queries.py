"""
Pinned desk queries, stored locally on the operator machine.

The JSON file lives outside git-tracked secrets, defaulting to
~/.config/openalpha-desk/pins.json (override with OPENALPHA_PINS_PATH).
It holds operator name, pin title, and question text only.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT = Path.home() / ".config" / "openalpha-desk" / "pins.json"


def pins_path() -> Path:
    raw = (os.getenv("OPENALPHA_PINS_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT


def _empty() -> dict[str, Any]:
    return {"pins": []}


def load_document() -> dict[str, Any]:
    path = pins_path()
    if not path.is_file():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    pins = data.get("pins")
    if not isinstance(pins, list):
        return _empty()
    cleaned: list[dict[str, Any]] = []
    for row in pins:
        if not isinstance(row, dict):
            continue
        question = str(row.get("question") or "").strip()
        if not question:
            continue
        cleaned.append(
            {
                "id": str(row.get("id") or uuid.uuid4()),
                "operator": str(row.get("operator") or "").strip(),
                "name": str(row.get("name") or "").strip() or question,
                "question": question,
                "created_at": str(row.get("created_at") or ""),
            }
        )
    return {"pins": cleaned}


def load_pins(*, operator: str | None = None) -> list[dict[str, Any]]:
    pins = list(load_document().get("pins") or [])
    wanted = (operator or "").strip()
    if not wanted:
        return pins
    return [row for row in pins if str(row.get("operator") or "").strip() == wanted]


def _write(document: dict[str, Any]) -> None:
    path = pins_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
        if path.parent.exists():
            os.chmod(path.parent, 0o700)
    except OSError:
        return


def add_pin(*, operator: str, name: str, question: str) -> dict[str, Any]:
    question = (question or "").strip()
    if not question:
        raise ValueError("A question is required to pin a panel.")
    operator = (operator or "").strip() or "operator"
    name = (name or "").strip() or question
    pin = {
        "id": uuid.uuid4().hex,
        "operator": operator,
        "name": name,
        "question": question,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    document = load_document()
    document["pins"].append(pin)
    _write(document)
    return pin


def remove_pin(pin_id: str) -> bool:
    wanted = (pin_id or "").strip()
    if not wanted:
        return False
    document = load_document()
    pins = [row for row in document["pins"] if str(row.get("id")) != wanted]
    if len(pins) == len(document["pins"]):
        return False
    document["pins"] = pins
    _write(document)
    return True
