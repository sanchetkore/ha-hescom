"""Set up the HESCOM integration.

The integration is a thin, read-only window onto the HESCOM consumer portal.
Everything interesting happens in :class:`~.coordinator.HescomCoordinator`;
this module only wires it into Home Assistant and exposes the one service.
"""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .api import HescomApi
from .const import (
    ATTR_ENTRY_ID,
    CONF_ACCOUNT_ID,
    DOMAIN,
    PLATFORMS,
    SERVICE_REFRESH,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .coordinator import HescomCoordinator

_LOGGER = logging.getLogger(__name__)

HescomConfigEntry = ConfigEntry[HescomCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_REFRESH_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): cv.string})

ENTRIES = "entries"


def _coordinator_map(hass: HomeAssistant) -> dict[str, HescomCoordinator]:
    """The live coordinators, keyed by config entry id."""
    return hass.data.setdefault(DOMAIN, {}).setdefault(ENTRIES, {})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the ``hescom.refresh`` service.

    The service is registered even with no entries configured, so an automation
    restored before the integration still validates.
    """

    async def _handle_refresh(call: ServiceCall) -> None:
        entry_id = call.data.get(ATTR_ENTRY_ID)
        coordinators = _coordinator_map(hass)

        if entry_id:
            coordinator = coordinators.get(entry_id)
            if coordinator is None:
                raise ValueError(f"No HESCOM entry with id {entry_id}")
            targets = [coordinator]
        else:
            targets = list(coordinators.values())

        if not targets:
            _LOGGER.warning("HESCOM refresh requested but no accounts are configured")
            return

        results = await asyncio.gather(
            *(coordinator.async_force_refresh() for coordinator in targets),
            return_exceptions=True,
        )
        for coordinator, result in zip(targets, results):
            if isinstance(result, Exception):
                _LOGGER.error(
                    "Manual HESCOM refresh failed for %s: %s",
                    coordinator.account_id,
                    result,
                )

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH,
        _handle_refresh,
        schema=SERVICE_REFRESH_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HescomConfigEntry) -> bool:
    """Set up one HESCOM account."""
    account_id = entry.data[CONF_ACCOUNT_ID]
    api = HescomApi(hass, account_id)
    coordinator = HescomCoordinator(hass, entry, api)

    _coordinator_map(hass)[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await coordinator.async_setup()
    # Raises ConfigEntryNotReady on a transient failure and
    # ConfigEntryAuthFailed when the service key is rejected, both of which
    # Home Assistant handles for us.
    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HescomConfigEntry) -> bool:
    """Unload one HESCOM account."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    coordinators = _coordinator_map(hass)
    coordinators.pop(entry.entry_id, None)
    if not coordinators:
        hass.data.get(DOMAIN, {}).pop(ENTRIES, None)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: HescomConfigEntry) -> None:
    """Forget the stored bill baseline when the account is deleted."""
    store: Store[dict[str, str]] = Store(
        hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}"
    )
    await store.async_remove()


async def _async_reload_entry(hass: HomeAssistant, entry: HescomConfigEntry) -> None:
    """Apply changed options by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


__all__ = [
    "async_remove_entry",
    "async_setup",
    "async_setup_entry",
    "async_unload_entry",
]
