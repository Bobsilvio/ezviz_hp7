"""Shared base for peephole viewer (CatEye, e.g. CP2) setting entities.

Writing to a battery peephole is slow and lossy: the first command often finds
it asleep. Hp7Api.cateye_write retries with backoff, re-sends once and waits
for the cloud to report the new value. While that runs (up to about a minute)
the entity shows the requested value; afterwards it shows whatever the cloud
reports, so a change the device didn't take visibly reverts, and the action
raises an error, like the EZVIZ app's "command failed". Trying again usually
works, exactly as in the app.
"""
from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_UNSET = object()


class CatEyeSettingEntity(CoordinatorEntity):
    """A writable CP2 setting backed by one key of the CatEye status dict."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator,
        api,
        serial: str,
        model: str,
        key: str,
        icon: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._serial = serial
        self._model = model
        self._key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{DOMAIN}_{serial}_cateye_{key}"
        if icon:
            self._attr_icon = icon
        self._pending: Any = _UNSET

    @property
    def device_info(self) -> DeviceInfo:
        from .device_info import make_device_info

        return make_device_info(self._serial, self._model)

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return super().available and data.get(self._key) is not None

    def _current(self) -> Any:
        if self._pending is not _UNSET:
            return self._pending
        return (self.coordinator.data or {}).get(self._key)

    async def _async_write(self, value: Any) -> None:
        self._pending = value
        self.async_write_ha_state()
        try:
            ok, status = await self.hass.async_add_executor_job(
                self._api.cateye_write, self._serial, self._key, value
            )
        finally:
            self._pending = _UNSET
        if status:
            self.coordinator.async_set_updated_data(status)
        else:
            self.async_write_ha_state()
        if not ok:
            raise HomeAssistantError(
                "The peephole didn't apply this change (it was probably asleep). "
                "Try again; it usually works on the second attempt."
            )
