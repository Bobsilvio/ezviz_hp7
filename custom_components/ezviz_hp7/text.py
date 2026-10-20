"""EZVIZ HP7 text entities (peephole viewer OSD)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.text import TextEntity, TextMode

from .cateye import CatEyeSettingEntity
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    if not data.get("cateye"):
        return
    async_add_entities(
        [
            CatEyeOsdText(
                data["coordinator"], data["api"], data["serial"],
                data.get("model") or "CP2", "osd_text", "mdi:format-text",
            )
        ]
    )


class CatEyeOsdText(CatEyeSettingEntity, TextEntity):
    """Text drawn on the video. Any text works (the app only offers the
    device name); empty turns the overlay off."""

    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = 32

    @property
    def available(self) -> bool:
        # An empty OSD is a valid state ("off"), only a missing key isn't.
        return self.coordinator.last_update_success and "osd_text" in (self.coordinator.data or {})

    @property
    def native_value(self) -> str | None:
        value = self._current()
        return "" if value is None else str(value)

    async def async_set_value(self, value: str) -> None:
        await self._async_write(value.strip())
