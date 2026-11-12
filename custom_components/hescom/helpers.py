"""Small parsing helpers shared by the HESCOM platforms."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y")
_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d-%m-%Y %H:%M:%S",
)


def as_float(value: Any) -> float | None:
    """Best-effort float conversion; returns None for empty/invalid input."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def as_int(value: Any) -> int | None:
    """Best-effort int conversion."""
    number = as_float(value)
    return None if number is None else int(number)


def as_text(value: Any) -> str | None:
    """Trimmed string, or None when the value carries no information."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_date(value: Any) -> date | None:
    """Parse the several date spellings HESCOM uses.

    Seen in the wild: ``2026-11-20``, ``2025-03-03-00.00.00``, ``10-09-2026``.
    """
    text = as_text(value)
    if text is None:
        return None

    # "2025-03-03-00.00.00" -> "2025-03-03"
    if len(text) > 10 and text[4] == "-" and text[7] == "-" and text[10] == "-":
        text = text[:10]

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    parsed = parse_datetime(value)
    return parsed.date() if parsed else None


def parse_datetime(value: Any) -> datetime | None:
    """Parse a timestamp, falling back to a date at midnight."""
    text = as_text(value)
    if text is None:
        return None

    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    # ISO-ish values and "2025-03-03-00.00.00"
    candidate = text
    if len(candidate) > 10 and candidate[10] == "-":
        candidate = f"{candidate[:10]} {candidate[11:].replace('-', ':')}"
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        pass

    day = parse_date(text)
    return datetime.combine(day, datetime.min.time()) if day else None


def dig(data: Any, *keys: str) -> Any:
    """Walk nested dicts, returning None as soon as a level is missing."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def find_key(data: Any, key: str, *, max_depth: int = 6) -> Any:
    """Depth-first search for the first matching key in nested structures.

    Some HESCOM payloads move fields between nesting levels depending on the
    backend service that answered, so a tolerant lookup beats a rigid path.
    """
    stack: list[tuple[Any, int]] = [(data, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            continue
        if isinstance(node, dict):
            if key in node and node[key] not in (None, "", [], {}):
                return node[key]
            stack.extend((value, depth + 1) for value in node.values())
        elif isinstance(node, list):
            stack.extend((item, depth + 1) for item in node)
    return None
