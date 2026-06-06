"""Number entities (chime volume) for EZVIZ HP7 / CP7."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up EZVIZ HP7/CP7 number entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    serial = data["serial"]
    monitor_serial = data.get("monitor_serial")
    model = data.get("model") or "HP7"
    coordinator = data["coordinator"]

    entities: list[NumberEntity] = []
    entities.append(EzvizHp7ChimeVolume(coordinator, api, serial, model=model))
    entities.append(EzvizHp7ChimeRingtone(coordinator, api, serial, model=model))
    entities.append(
        EzvizHp7ChimePirRingtone(coordinator, api, serial, model=model)
    )

    monitors: list[str] = []
    if isinstance(monitor_serial, str) and monitor_serial.strip():
        monitors = [monitor_serial.strip()]
    elif isinstance(monitor_serial, (list, tuple)):
        monitors = [s for s in monitor_serial if isinstance(s, str) and s.strip()]

    for ms in monitors:
        entities.append(
            EzvizHp7ChimeVolume(
                coordinator,
                api,
                ms,
                model=f"{model} Monitor",
                translation_key="chime_volume_monitor",
                value_key="chime_volume_monitors",
            )
        )
        entities.append(
            EzvizHp7ChimeRingtone(
                coordinator,
                api,
                ms,
                model=f"{model} Monitor",
                translation_key="chime_ringtone_monitor",
                value_key="chime_ringtone_monitors",
            )
        )
        entities.append(
            EzvizHp7ChimePirRingtone(
                coordinator,
                api,
                ms,
                model=f"{model} Monitor",
                translation_key="chime_pir_ringtone_monitor",
                value_key="chime_pir_ringtone_monitors",
            )
        )

    async_add_entities(entities)


class EzvizHp7ChimeVolume(CoordinatorEntity, NumberEntity):
    """Slider 0-7 for the ChimeMusic ``volume`` field."""

    _attr_has_entity_name = True
    _attr_native_min_value = 0
    _attr_native_max_value = 7
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator,
        api,
        serial: str,
        *,
        model: str = "HP7",
        translation_key: str = "chime_volume",
        value_key: str = "chime_volume",
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._serial = serial
        self._model = model
        self._value_key = value_key
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{serial}_{translation_key}"

    @property
    def device_info(self) -> DeviceInfo:
        from .device_info import make_device_info
        return make_device_info(self._serial, self._model)

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        if self._value_key == "chime_volume":
            val = data.get("chime_volume")
        else:
            mapping = data.get(self._value_key) or {}
            val = mapping.get(self._serial) if isinstance(mapping, dict) else None
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        target = max(0, min(7, int(round(value))))
        ok = await self.hass.async_add_executor_job(
            self._api.set_chime_volume, self._serial, target
        )
        if ok:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(
                "EZVIZ HP7: set_chime_volume(%s, %d) failed",
                self._serial,
                target,
            )


class _RingtoneBase(CoordinatorEntity, NumberEntity):
    """Shared scaffolding for the ringtone selectors.

    The HP7 firmware reports the active ringtone as an integer index. The
    exact upper bound varies by firmware, so we expose a generous slider
    (0-15) and rely on the device to silently accept the highest supported
    value. If your firmware ignores values above N, just stay within range.
    """

    _attr_has_entity_name = True
    _attr_native_min_value = 0
    _attr_native_max_value = 15
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator,
        api,
        serial: str,
        *,
        model: str,
        translation_key: str,
        value_key: str,
        camera_state_key: str,
        setter_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._serial = serial
        self._model = model
        self._value_key = value_key
        self._camera_state_key = camera_state_key
        self._setter_name = setter_name
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{serial}_{translation_key}"

    @property
    def device_info(self) -> DeviceInfo:
        from .device_info import make_device_info
        return make_device_info(self._serial, self._model)

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        if self._value_key == self._camera_state_key:
            val = data.get(self._camera_state_key)
        else:
            mapping = data.get(self._value_key) or {}
            val = mapping.get(self._serial) if isinstance(mapping, dict) else None
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        target = max(0, int(round(value)))
        setter = getattr(self._api, self._setter_name)
        ok = await self.hass.async_add_executor_job(setter, self._serial, target)
        if ok:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(
                "EZVIZ HP7: %s(%s, %d) failed",
                self._setter_name,
                self._serial,
                target,
            )


class EzvizHp7ChimeRingtone(_RingtoneBase):
    def __init__(
        self,
        coordinator,
        api,
        serial: str,
        *,
        model: str = "HP7",
        translation_key: str = "chime_ringtone",
        value_key: str = "chime_ringtone",
    ) -> None:
        super().__init__(
            coordinator,
            api,
            serial,
            model=model,
            translation_key=translation_key,
            value_key=value_key,
            camera_state_key="chime_ringtone",
            setter_name="set_chime_ringtone",
        )


class EzvizHp7ChimePirRingtone(_RingtoneBase):
    def __init__(
        self,
        coordinator,
        api,
        serial: str,
        *,
        model: str = "HP7",
        translation_key: str = "chime_pir_ringtone",
        value_key: str = "chime_pir_ringtone",
    ) -> None:
        super().__init__(
            coordinator,
            api,
            serial,
            model=model,
            translation_key=translation_key,
            value_key=value_key,
            camera_state_key="chime_pir_ringtone",
            setter_name="set_chime_pir_ringtone",
        )
