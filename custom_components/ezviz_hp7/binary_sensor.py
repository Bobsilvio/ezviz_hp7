"""EZVIZ HP7 binary sensor entities."""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.helpers.event import CALLBACK_TYPE
    from .coordinator import Hp7Coordinator

_LOGGER = logging.getLogger(__name__)

ALARM_FIELD = "alarm_name"
ALARM_TIME_FIELD = "last_alarm_time"
PULSE_SECONDS = 3

# Simple binary sensors mapped directly to coordinator data keys
SIMPLE_MAP: list[tuple[str, str, BinarySensorDeviceClass]] = [
    ("Motion_Trigger", "motion_trigger", BinarySensorDeviceClass.MOTION),
]

# Alarm sensors that trigger for PULSE_SECONDS when specific alarm names appear
ALARM_MAP: list[tuple[list[str], str, str, BinarySensorDeviceClass | None, str]] = [
    (
        ["Smart Detection Alarm"],
        "smart_detection_alarm",
        None,
        "mdi:run",
    ),
    (
        ["Intelligent Detection Alarm"],
        "intelligent_detection_alarm",
        None,
        "mdi:account-search",
    ),
    (
        ["Your doorbell is ringing"],
        "doorbell_ringing",
        None,
        "mdi:doorbell",
    ),
    (
        ["EZVIZ app open the gate", "Monitor open the gate"],
        "gate_open",
        None,
        "mdi:gate-open",
    ),
    (
        ["EZVIZ app unlock the lock", "Monitor unlock the lock"],
        "unlock_lock",
        None,
        "mdi:lock-open-variant",
    ),
]


def _to_bool(value: Any) -> bool:
    """Convert various types to boolean.
    
    Args:
        value: Value to convert.
        
    Returns:
        Boolean representation of the value.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes", "y")
    return False


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up EZVIZ HP7 binary sensor entities.
    
    Args:
        hass: Home Assistant instance.
        entry: Config entry.
        async_add_entities: Callback to add entities.
    """
    data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    coordinator: Hp7Coordinator = data["coordinator"]
    serial: str = data["serial"]

    entities: list[BinarySensorEntity] = []

    for key, translation_key, device_class in SIMPLE_MAP:
        entities.append(
            Hp7BinarySimple(coordinator, serial, key, translation_key, device_class)
        )

    for match_values, translation_key, device_class, icon in ALARM_MAP:
        entities.append(
            Hp7BinaryAlarm(
                coordinator,
                serial,
                match_values,
                translation_key,
                device_class,
                icon,
            )
        )

    async_add_entities(entities)


class Hp7BinarySimple(CoordinatorEntity, BinarySensorEntity):
    """Simple binary sensor that directly maps to coordinator data."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: Hp7Coordinator,
        serial: str,
        key: str,
        translation_key: str,
        device_class: BinarySensorDeviceClass,
    ) -> None:
        """Initialize binary sensor entity.
        
        Args:
            coordinator: Data coordinator.
            serial: Device serial number.
            key: Key in coordinator data.
            translation_key: i18n translation key.
            device_class: Device class for sensor.
        """
        super().__init__(coordinator)
        self._serial = serial
        self._key = key
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{serial}_binary_{key}"
        self._attr_device_class = device_class

    @property
    def is_on(self) -> bool:
        """Return True if sensor is on."""
        data = self.coordinator.data or {}
        val = data.get(self._key)
        return _to_bool(val)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=f"EZVIZ HP7 ({self._serial})",
            manufacturer="EZVIZ",
            model="HP7",
        )


class Hp7BinaryAlarm(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor that pulses briefly when alarm is triggered.
    
    This sensor stays ON for PULSE_SECONDS after detecting a matching alarm,
    then returns to OFF. This is useful for automations that react to events.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: Hp7Coordinator,
        serial: str,
        match_values: list[str],
        translation_key: str,
        device_class: BinarySensorDeviceClass | None,
        icon: str,
    ) -> None:
        """Initialize alarm binary sensor entity.
        
        Args:
            coordinator: Data coordinator.
            serial: Device serial number.
            match_values: List of alarm names to trigger on.
            translation_key: i18n translation key.
            device_class: Device class for sensor.
            icon: Icon to display.
        """
        super().__init__(coordinator)
        self._serial = serial
        self._match_values = match_values
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{serial}_alarm_{translation_key}"
        self._attr_device_class = device_class
        self._attr_icon = icon
        self._last_trigger: dt_util.datetime | None = None
        self._prev_alarm_time: str | None = None
        self._off_unsub: CALLBACK_TYPE | None = None

    @property
    def is_on(self) -> bool:
        """Return True if recently triggered (within PULSE_SECONDS)."""
        if self._last_trigger is None:
            return False
        delta = (dt_util.utcnow() - self._last_trigger).total_seconds()
        return delta < PULSE_SECONDS

    def _schedule_state_update(self) -> None:
        """Schedule state update to turn off after PULSE_SECONDS."""
        if self._off_unsub:
            self._off_unsub()

        def _cb(_now: dt_util.datetime) -> None:
            self._off_unsub = None
            self.hass.add_job(self.async_write_ha_state)

        self._off_unsub = async_call_later(self.hass, PULSE_SECONDS, _cb)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle coordinator data update event.
        
        Detects new alarms and triggers pulse on matching sensor.
        """
        data = self.coordinator.data or {}
        current_alarm = data.get(ALARM_FIELD)
        current_alarm_time = data.get(ALARM_TIME_FIELD)

        # Check if new alarm matches this sensor and is different from last
        if (
            current_alarm in self._match_values
            and current_alarm_time is not None
            and current_alarm_time != self._prev_alarm_time
        ):
            self._prev_alarm_time = current_alarm_time
            self._last_trigger = dt_util.utcnow()
            self._schedule_state_update()
            _LOGGER.debug(
                "Alarm triggered for %s: %s (%s)", 
                self._attr_translation_key, 
                current_alarm,
                self._serial,
            )

        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=f"EZVIZ HP7 ({self._serial})",
            manufacturer="EZVIZ",
            model="HP7",
        )
