"""Binary sensors describing the state of a HESCOM connection."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import HescomCoordinator, HescomData
from .entity import HescomEntity

_LOGGER = logging.getLogger(__name__)

BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="connection_active",
        name="Connection active",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
    ),
    BinarySensorEntityDescription(
        key="bill_overdue",
        name="Bill overdue",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the binary sensors for one account."""
    coordinator: HescomCoordinator = hass.data[DOMAIN]["entries"][entry.entry_id]
    async_add_entities(
        HescomBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
    )


class HescomBinarySensor(HescomEntity, BinarySensorEntity):
    """A boolean view over the account snapshot."""

    entity_description: BinarySensorEntityDescription

    def __init__(
        self,
        coordinator: HescomCoordinator,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        if self.entity_description.key == "connection_active":
            return data.is_active
        return _is_overdue(data)


def _is_overdue(data: HescomData) -> bool | None:
    """Whether money is owed past the printed due date."""
    if data.due_amount is None:
        return None
    if data.due_amount <= 0:
        return False
    if data.bill_due_date is None:
        # Owed, but the portal has not printed a due date: assume overdue
        # rather than silently hiding a debt.
        return True
    return dt_util.now().date() > data.bill_due_date


__all__ = ["HescomBinarySensor", "async_setup_entry"]
