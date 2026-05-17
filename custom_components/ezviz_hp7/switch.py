from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up EZVIZ HP7 switches."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    serial = data["serial"]
    coordinator = data["coordinator"]

    async_add_entities([EzvizHp7ChimeSwitch(coordinator, api, serial)])


class EzvizHp7ChimeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity to enable/disable monitor chime sound."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, api, serial: str):
        super().__init__(coordinator)
        self._api = api
        self._serial = serial
        self._attr_translation_key = "chime_sound"
        self._attr_unique_id = f"{DOMAIN}_{serial}_chime_sound"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=f"EZVIZ HP7 ({self._serial})",
            manufacturer="EZVIZ",
            model="HP7",
        )

    @property
    def is_on(self) -> bool | None:
        """Return current chime state from coordinator data."""
        data = self.coordinator.data or {}
        return data.get("chime_is_on", True)

    async def async_turn_on(self, **kwargs) -> None:
        """Enable monitor chime sound."""
        ok = await self.hass.async_add_executor_job(
            self._api.enable_chime, self._serial
        )
        if ok:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("EZVIZ HP7: enable_chime failed")

    async def async_turn_off(self, **kwargs) -> None:
        """Disable monitor chime sound."""
        ok = await self.hass.async_add_executor_job(
            self._api.disable_chime, self._serial
        )
        if ok:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("EZVIZ HP7: disable_chime failed")
