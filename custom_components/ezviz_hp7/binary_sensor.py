from __future__ import annotations
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "on", "yes", "y"):
            return True
        return False
    return False

MAP = [
    ("Motion_Trigger", "motion_trigger", BinarySensorDeviceClass.MOTION),
]

async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    serial = data["serial"]
    ents = [Hp7Binary(coordinator, serial, key, translation_key, dc) for key, translation_key, dc in MAP]
    async_add_entities(ents)

class Hp7Binary(CoordinatorEntity, BinarySensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, coordinator, serial, key, translation_key, device_class):
        super().__init__(coordinator)
        self._serial = serial
        self._key = key
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{serial}_bin_{key}"
        self._attr_device_class = device_class

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data or {}
        raw = data.get(self._key)
        return _to_bool(raw)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=f"EZVIZ HP7 ({self._serial})",
            manufacturer="EZVIZ",
            model="HP7",
        )
