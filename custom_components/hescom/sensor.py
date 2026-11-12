"""Sensors exposing the HESCOM consumer account."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NAME
from .coordinator import HescomCoordinator, HescomData
from .entity import HescomEntity

_LOGGER = logging.getLogger(__name__)

#: Complaint states that still need action from the utility.
_OPEN_COMPLAINT_STATES = {
    "OPEN",
    "PENDING",
    "REGISTERED",
    "INPROGRESS",
    "IN PROGRESS",
    "ASSIGNED",
}


def _aware(value: datetime | None) -> datetime | None:
    """Attach the Home Assistant timezone to a naive timestamp."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return value


def _payment_date(data: HescomData) -> date | None:
    payment = data.last_payment
    if not payment:
        return None
    value = payment.get("date")
    return value.date() if isinstance(value, datetime) else value


def _open_complaints(data: HescomData) -> int | None:
    if data.complaints is None:
        return None
    count = 0
    for row in data.complaints:
        for key in ("user", "Status", "status", "requestStatus", "complaintStatus"):
            value = row.get(key)
            if isinstance(value, str) and value.strip().upper() in _OPEN_COMPLAINT_STATES:
                count += 1
                break
    return count


def _due_attributes(data: HescomData) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    if data.bill_id:
        attributes["bill_id"] = data.bill_id
    if data.bill_due_date:
        attributes["bill_due_date"] = data.bill_due_date.isoformat()
    if data.alert_info:
        attributes["alert"] = data.alert_info
    if data.pdf_url:
        attributes["pdf_url"] = data.pdf_url
    return attributes


@dataclass(frozen=True, kw_only=True)
class HescomSensorDescription(SensorEntityDescription):
    """A sensor backed by one field of :class:`HescomData`."""

    value_fn: Callable[[HescomData], Any]
    attrs_fn: Callable[[HescomData], dict[str, Any]] | None = None


SENSORS: tuple[HescomSensorDescription, ...] = (
    HescomSensorDescription(
        key="due_amount",
        name="Due amount",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="INR",
        state_class=SensorStateClass.TOTAL,
        icon="mdi:currency-inr",
        value_fn=lambda data: data.due_amount,
        attrs_fn=_due_attributes,
    ),
    HescomSensorDescription(
        key="payoff_balance",
        name="Payoff balance",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="INR",
        state_class=SensorStateClass.TOTAL,
        icon="mdi:cash-check",
        value_fn=lambda data: data.payoff_balance,
    ),
    HescomSensorDescription(
        key="bill_due_date",
        name="Bill due date",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-alert",
        value_fn=lambda data: data.bill_due_date,
    ),
    HescomSensorDescription(
        key="last_payment_amount",
        name="Last payment amount",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="INR",
        icon="mdi:cash",
        value_fn=lambda data: (
            data.last_payment.get("amount") if data.last_payment else None
        ),
    ),
    HescomSensorDescription(
        key="last_payment_date",
        name="Last payment date",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-check",
        value_fn=_payment_date,
    ),
    HescomSensorDescription(
        key="account_status",
        name="Account status",
        icon="mdi:account-check",
        value_fn=lambda data: data.account_status,
        attrs_fn=lambda data: (
            {"service_agreement_id": data.service_agreement_id}
            if data.service_agreement_id
            else {}
        ),
    ),
    HescomSensorDescription(
        key="connection_type",
        name="Connection type",
        icon="mdi:transmission-tower",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.connection_type,
    ),
    HescomSensorDescription(
        key="tariff",
        name="Tariff",
        icon="mdi:tag-text",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.tariff,
    ),
    HescomSensorDescription(
        key="load_kw",
        name="Sanctioned load",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        value_fn=lambda data: data.load_kw,
    ),
    HescomSensorDescription(
        key="meter_serial",
        name="Meter serial",
        icon="mdi:meter-electric",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.meter_serial,
    ),
    HescomSensorDescription(
        key="bill_id",
        name="Bill number",
        icon="mdi:file-document-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.bill_id,
    ),
    HescomSensorDescription(
        key="current_reading",
        name="Current reading",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:gauge",
        value_fn=lambda data: data.current_reading,
    ),
    HescomSensorDescription(
        key="previous_reading",
        name="Previous reading",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:gauge-empty",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.previous_reading,
    ),
    HescomSensorDescription(
        key="usage",
        name="Billed usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:lightning-bolt",
        value_fn=lambda data: data.usage,
    ),
    HescomSensorDescription(
        key="reading_start",
        name="Reading period start",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-start",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _aware(data.reading_start),
    ),
    HescomSensorDescription(
        key="reading_end",
        name="Reading period end",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-end",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _aware(data.reading_end),
    ),
    HescomSensorDescription(
        key="complaint_count",
        name="Complaints",
        icon="mdi:message-alert-outline",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: len(data.complaints),
        attrs_fn=lambda data: {"latest": data.complaints[0]} if data.complaints else {},
    ),
    HescomSensorDescription(
        key="open_complaints",
        name="Open complaints",
        icon="mdi:message-alert",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_open_complaints,
    ),
    HescomSensorDescription(
        key="bill_pdf",
        name="Bill PDF",
        icon="mdi:file-pdf-box",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.pdf_url,
        attrs_fn=lambda data: (
            {"local_path": data.pdf_path} if data.pdf_path else {}
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the sensors for one account."""
    coordinator: HescomCoordinator = hass.data[DOMAIN]["entries"][entry.entry_id]
    async_add_entities(
        HescomSensor(coordinator, description) for description in SENSORS
    )


class HescomSensor(HescomEntity, SensorEntity):
    """One HESCOM field."""

    entity_description: HescomSensorDescription

    def __init__(
        self,
        coordinator: HescomCoordinator,
        description: HescomSensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data) or None


__all__ = ["HescomSensor", "async_setup_entry"]
