"""Shared base entity for the HESCOM integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import HescomCoordinator


class HescomEntity(CoordinatorEntity[HescomCoordinator]):
    """An entity belonging to one HESCOM consumer account."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HescomCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.account_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        data = self.coordinator.data
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.account_id)},
            name=f"{NAME} {self.coordinator.account_id}",
            manufacturer=f"{NAME} (Karnataka)",
            model="Consumer account",
            serial_number=self.coordinator.account_id,
            configuration_url="https://hescom.co.in/hescom/",
            suggested_area=data.subdivision if data and data.subdivision else None,
        )


__all__ = ["HescomEntity"]
