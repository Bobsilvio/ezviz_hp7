"""Number entities (chime volume) for EZVIZ HP7 / CP7."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_info import (
    HP5_RINGTONE_MAX,
    HP5_RINGTONE_MIN,
    HP5_RINGTONE_OFFSET,
    is_hp5_family,
)

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
    hp5 = is_hp5_family(model)
    ring_kwargs: dict[str, Any] = (
        {
            "min_value": HP5_RINGTONE_MIN,
            "max_value": HP5_RINGTONE_MAX,
            "offset": HP5_RINGTONE_OFFSET,
        }
        if hp5
        else {}
    )
    if not hp5:
        # On HP5/HPD5 the composite serial's ChimeMusic is inert (volume is a
        # 0-100 value the app can't change, doorbell_enable stuck at 0); the
        # real chime config is on the monitor serial and handled below.
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
                **ring_kwargs,
            )
        )
        if not hp5:
            # HP5 monitors have no PIR/motion ringtone in the app; the field
            # exists in ChimeMusic but is inert.
            entities.append(
                EzvizHp7ChimePirRingtone(
                    coordinator,
                    api,
                    ms,
                    model=f"{model} Monitor",
                    translation_key="chime_pir_ringtone_monitor",
                    value_key="chime_pir_ringtone_monitors",
                    **ring_kwargs,
                )
            )

    async_add_entities(entities)


class _OptimisticValueMixin:
    """Hold a just-written slider value for a short grace window.

    Same idea as the switches' _OptimisticStateMixin: the EZVIZ cloud takes a
    few seconds to reflect a devconfig write, so the poll right after a set
    would snap the slider back to the old value.
    """

    _OPTIMISTIC_GRACE = 20.0
    _opt_value: float | None = None
    _opt_until: float = 0.0

    def _mark_optimistic(self, value: float) -> None:
        self._opt_value = value
        self._opt_until = time.monotonic() + self._OPTIMISTIC_GRACE
        self.async_write_ha_state()

    def _shield_stale(self, coordinator_value: float | None) -> float | None:
        if time.monotonic() < self._opt_until:
            return self._opt_value
        return coordinator_value


class EzvizHp7ChimeVolume(_OptimisticValueMixin, CoordinatorEntity, NumberEntity):
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
            return self._shield_stale(None)
        try:
            return self._shield_stale(float(val))
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        target = max(0, min(7, int(round(value))))
        ok = await self.hass.async_add_executor_job(
            self._api.set_chime_volume, self._serial, target
        )
        if ok:
            self._mark_optimistic(float(target))
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(
                "EZVIZ HP7: set_chime_volume(%s, %d) failed",
                self._serial,
                target,
            )


class _RingtoneBase(_OptimisticValueMixin, CoordinatorEntity, NumberEntity):
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
        min_value: int | None = None,
        max_value: int | None = None,
        offset: int = 0,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._serial = serial
        self._model = model
        self._value_key = value_key
        self._camera_state_key = camera_state_key
        self._setter_name = setter_name
        # Displayed value = stored field + offset (HP5 monitor: app shows
        # 1..20, ChimeMusic stores 0..19).
        self._offset = offset
        if min_value is not None:
            self._attr_native_min_value = min_value
        if max_value is not None:
            self._attr_native_max_value = max_value
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
            return self._shield_stale(None)
        try:
            return self._shield_stale(float(val) + self._offset)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        target = max(0, int(round(value)) - self._offset)
        setter = getattr(self._api, self._setter_name)
        ok = await self.hass.async_add_executor_job(setter, self._serial, target)
        if ok:
            self._mark_optimistic(float(target + self._offset))
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
        **kwargs: Any,
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
            **kwargs,
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
        **kwargs: Any,
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
            **kwargs,
        )
