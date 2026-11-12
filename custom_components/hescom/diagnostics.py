"""Diagnostics support for the HESCOM integration."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import HescomCoordinator

#: Fields that identify the person rather than the connection.
TO_REDACT = {
    "name",
    "address",
    "phone",
    "email",
}

_SENSITIVE_LEDGER_KEYS = {"transaction_id"}


def _jsonable(value: Any) -> Any:
    """Recursively convert dates and dataclasses into JSON-friendly types."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Describe what the integration currently knows, minus personal detail."""
    coordinator: HescomCoordinator = hass.data[DOMAIN]["entries"][entry.entry_id]
    data = coordinator.data

    payload: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "options": dict(entry.options),
        },
        "polling": {
            "interval_minutes": (
                coordinator.update_interval.total_seconds() / 60
                if coordinator.update_interval
                else None
            ),
            "last_update_success": coordinator.last_update_success,
        },
        "data": asdict(data) if data is not None else None,
    }

    payload = _jsonable(payload)
    for payment in (payload.get("data") or {}).get("payments") or []:
        for key in _SENSITIVE_LEDGER_KEYS:
            if isinstance(payment, dict) and payment.get(key):
                payment[key] = "**REDACTED**"

    return async_redact_data(payload, TO_REDACT)


__all__ = ["async_get_config_entry_diagnostics"]
