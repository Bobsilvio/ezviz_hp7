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
    monitor_serial = data.get("monitor_serial")
    coordinator = data["coordinator"]

    entities = [EzvizHp7ChimeSwitch(coordinator, api, serial)]
    if monitor_serial:
        entities.append(
            EzvizHp7ChimeSwitch(
                coordinator,
                api,
                monitor_serial,
                state_key="chime_is_on_monitor",
                translation_key="chime_sound_monitor",
                model="HP7 Monitor",
            )
        )
    async_add_entities(entities)


class EzvizHp7ChimeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity to enable/disable chime sound on camera or monitor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        api,
        serial: str,
        state_key: str = "chime_is_on",
        translation_key: str = "chime_sound",
        model: str = "HP7",
    ):
        super().__init__(coordinator)
        self._api = api
        self._serial = serial
        self._state_key = state_key
        self._model = model
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{serial}_{translation_key}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=f"EZVIZ {self._model} ({self._serial})",
            manufacturer="EZVIZ",
            model=self._model,
        )

    @property
    def is_on(self) -> bool | None:
        """Return current chime state from coordinator data."""
        data = self.coordinator.data or {}
        return data.get(self._state_key)

    async def async_turn_on(self, **kwargs) -> None:
        """Enable chime sound."""
        ok = await self.hass.async_add_executor_job(
            self._api.enable_chime, self._serial
        )
        if ok:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("EZVIZ HP7: enable_chime failed (serial=%s)", self._serial)

    async def async_turn_off(self, **kwargs) -> None:
        """Disable chime sound."""
        ok = await self.hass.async_add_executor_job(
            self._api.disable_chime, self._serial
        )
        if ok:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("EZVIZ HP7: disable_chime failed (serial=%s)", self._serial)
