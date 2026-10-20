"""EZVIZ HP7 select entities (peephole viewer settings)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity

from .cateye import CatEyeSettingEntity
from .const import CATEYE_SCREEN_TIMEOUTS, CATEYE_WIFI_MODES, DOMAIN


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    if not data.get("cateye"):
        return
    args = (data["coordinator"], data["api"], data["serial"], data.get("model") or "CP2")
    async_add_entities(
        [
            CatEyeScreenTimeout(*args, "screen_timeout", "mdi:monitor-off"),
            CatEyeWifiSleepMode(*args, "wifi_sleep_mode", "mdi:wifi-cog"),
        ]
    )


class CatEyeScreenTimeout(CatEyeSettingEntity, SelectEntity):
    """Panel auto-lock time (the app offers 10-30 s in 5 s steps)."""

    _attr_options = [str(v) for v in CATEYE_SCREEN_TIMEOUTS]

    @property
    def current_option(self) -> str | None:
        value = self._current()
        return str(value) if value is not None and str(value) in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        await self._async_write(int(option))


class CatEyeWifiSleepMode(CatEyeSettingEntity, SelectEntity):
    """Wi-Fi sleep mode. Ultra low power saves battery but makes the CP2 slow
    to answer commands: switching back often needs a second attempt."""

    _attr_options = list(CATEYE_WIFI_MODES)

    @property
    def current_option(self) -> str | None:
        value = self._current()
        for option, raw in CATEYE_WIFI_MODES.items():
            if value == raw:
                return option
        return None

    async def async_select_option(self, option: str) -> None:
        await self._async_write(CATEYE_WIFI_MODES[option])
