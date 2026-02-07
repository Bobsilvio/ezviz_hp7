"""EZVIZ HP7 button entities."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN

if TYPE_CHECKING:
    from .api import Hp7Api

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EZVIZ HP7 button entities.
    
    Args:
        hass: Home Assistant instance.
        entry: Config entry.
        async_add_entities: Callback to add entities.
    """
    data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    api: Hp7Api = data["api"]
    serial: str = data["serial"]

    entities: list[EzvizHp7Button] = []
    if getattr(api, "supports_gate", False):
        entities.append(EzvizHp7Button(hass, api, serial, "unlock_gate"))
    if getattr(api, "supports_door", False):
        entities.append(EzvizHp7Button(hass, api, serial, "unlock_door"))
    
    async_add_entities(entities)


class EzvizHp7Button(ButtonEntity):
    """Button entity to unlock door or gate."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        api: Hp7Api,
        serial: str,
        action: str,
    ) -> None:
        """Initialize button entity.
        
        Args:
            hass: Home Assistant instance.
            api: EZVIZ HP7 API instance.
            serial: Device serial number.
            action: Button action ("unlock_door" or "unlock_gate").
        """
        self.hass = hass
        self._api = api
        self._serial = serial
        self._action = action
        self._attr_translation_key = action
        self._attr_unique_id = f"{DOMAIN}_{serial}_{action}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=f"EZVIZ HP7 ({self._serial})",
            manufacturer="EZVIZ",
            model="HP7",
        )

    async def async_press(self) -> None:
        """Handle button press.
        
        Sends unlock command to EZVIZ device.
        """
        _LOGGER.debug("EZVIZ HP7 button pressed: %s (%s)", self._action, self._serial)
        
        success = False
        if self._action == "unlock_gate":
            success = await self.hass.async_add_executor_job(
                self._api.unlock_gate, self._serial
            )
            log_msg = "Unlock Gate"
        elif self._action == "unlock_door":
            success = await self.hass.async_add_executor_job(
                self._api.unlock_door, self._serial
            )
            log_msg = "Unlock Door"
        else:
            return
        
        if success:
            _LOGGER.info("EZVIZ HP7: %s successful", log_msg)
        else:
            _LOGGER.error("EZVIZ HP7: %s failed", log_msg)
