"""Action buttons for the HESCOM integration."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HescomCoordinator
from .entity import HescomEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the buttons for one account."""
    coordinator: HescomCoordinator = hass.data[DOMAIN]["entries"][entry.entry_id]
    async_add_entities(
        [
            HescomRefreshButton(coordinator),
            HescomArchiveButton(coordinator),
        ]
    )


class HescomRefreshButton(HescomEntity, ButtonEntity):
    """Poll the portal immediately."""

    _attr_name = "Refresh"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: HescomCoordinator) -> None:
        super().__init__(coordinator, "refresh")

    async def async_press(self) -> None:
        await self.coordinator.async_force_refresh()


class HescomArchiveButton(HescomEntity, ButtonEntity):
    """Download the current bill PDF, even if an earlier attempt failed."""

    _attr_name = "Download bill PDF"
    _attr_icon = "mdi:file-download"

    def __init__(self, coordinator: HescomCoordinator) -> None:
        super().__init__(coordinator, "archive_pdf")

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and bool(data and data.bill_id)

    async def async_press(self) -> None:
        await self.coordinator.async_archive_now(force=True)


__all__ = [
    "HescomArchiveButton",
    "HescomRefreshButton",
    "async_setup_entry",
]
