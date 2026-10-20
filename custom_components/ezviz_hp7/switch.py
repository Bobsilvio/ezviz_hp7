from __future__ import annotations

import logging
import time
from typing import Any, Callable

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up EZVIZ HP7/CP7 switches."""
    data = hass.data[DOMAIN][entry.entry_id]
    if data.get("cateye"):
        # Peephole viewer: only its own settings, no doorbell switches.
        from .cateye import CatEyeSettingEntity

        class CatEyeSwitch(CatEyeSettingEntity, SwitchEntity):
            @property
            def is_on(self) -> bool | None:
                value = self._current()
                return None if value is None else bool(value)

            async def async_turn_on(self, **kwargs) -> None:
                await self._async_write(True)

            async def async_turn_off(self, **kwargs) -> None:
                await self._async_write(False)

        args = (data["coordinator"], data["api"], data["serial"], data.get("model") or "CP2")
        async_add_entities(
            [
                CatEyeSwitch(*args, "offline_notify", "mdi:bell-alert-outline"),
                CatEyeSwitch(*args, "logo_watermark", "mdi:watermark"),
                CatEyeSwitch(*args, "peak_protection", "mdi:battery-heart-outline"),
                CatEyeSwitch(*args, "distortion_correction", "mdi:panorama-variant-outline"),
            ]
        )
        return
    api = data["api"]
    serial = data["serial"]
    monitor_serial = data.get("monitor_serial")
    model = data.get("model") or "HP7"
    coordinator = data["coordinator"]

    entities: list[SwitchEntity] = []

    # Camera-side switches.
    entities.append(EzvizHp7ChimeSwitch(coordinator, api, serial, model=model))
    entities.append(EzvizHp7DndSwitch(coordinator, api, serial, model))
    entities.append(EzvizHp7PrivacySwitch(coordinator, api, serial, model))
    entities.append(EzvizHp7DefenceSwitch(coordinator, api, serial, model))
    entities.append(EzvizHp7LabelLightSwitch(coordinator, api, serial, model))
    entities.append(EzvizHp7ChimePirSwitch(coordinator, api, serial, model=model))

    # Per-monitor switches (multi-monitor for HP7 bifamigliare).
    monitors: list[str] = []
    if isinstance(monitor_serial, str) and monitor_serial.strip():
        monitors = [monitor_serial.strip()]
    elif isinstance(monitor_serial, (list, tuple)):
        monitors = [s for s in monitor_serial if isinstance(s, str) and s.strip()]

    for ms in monitors:
        entities.append(
            EzvizHp7ChimeSwitch(
                coordinator,
                api,
                ms,
                state_lookup=(
                    lambda data, s=ms: (
                        data.get("chime_is_on_monitors", {}).get(s)
                        if isinstance(data.get("chime_is_on_monitors"), dict)
                        else data.get("chime_is_on_monitor")
                    )
                ),
                translation_key="chime_sound_monitor",
                model=f"{model} Monitor",
            )
        )
        entities.append(
            EzvizHp7ChimePirSwitch(
                coordinator,
                api,
                ms,
                model=f"{model} Monitor",
                translation_key="chime_pir_monitor",
                state_lookup=(
                    lambda data, s=ms: (
                        data.get("chime_pir_is_on_monitors", {}).get(s)
                        if isinstance(data.get("chime_pir_is_on_monitors"), dict)
                        else None
                    )
                ),
            )
        )

    async_add_entities(entities)


class _OptimisticStateMixin:
    """Hold a just-written switch state for a short grace window (#40).

    The EZVIZ cloud takes a few seconds to propagate a write to its status
    endpoint, so the refresh right after a toggle reads the OLD state and HA
    flips the switch back — then forward again a poll later ("spurious state
    transitions"). After a successful write, trust the value we sent for
    _OPTIMISTIC_GRACE seconds and shield is_on from the coordinator's stale
    reading; by the time the window expires the cloud has caught up.
    """

    _OPTIMISTIC_GRACE = 20.0
    _opt_value: bool | None = None
    _opt_until: float = 0.0

    def _mark_optimistic(self, value: bool) -> None:
        self._opt_value = value
        self._opt_until = time.monotonic() + self._OPTIMISTIC_GRACE
        self.async_write_ha_state()

    def _shield_stale(self, coordinator_value: Any) -> Any:
        if time.monotonic() < self._opt_until:
            return self._opt_value
        return coordinator_value


class EzvizHp7ChimeSwitch(_OptimisticStateMixin, CoordinatorEntity, SwitchEntity):
    """Switch entity to enable/disable chime sound on camera or monitor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        api,
        serial: str,
        state_lookup: Callable[[dict], Any] | None = None,
        translation_key: str = "chime_sound",
        model: str = "HP7",
    ):
        super().__init__(coordinator)
        self._api = api
        self._serial = serial
        self._state_lookup = state_lookup
        self._model = model
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{serial}_{translation_key}"

    @property
    def device_info(self) -> DeviceInfo:
        from .device_info import make_device_info
        return make_device_info(self._serial, self._model)

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        if self._state_lookup is not None:
            return self._shield_stale(self._state_lookup(data))
        return self._shield_stale(data.get("chime_is_on"))

    async def async_turn_on(self, **kwargs) -> None:
        ok = await self.hass.async_add_executor_job(
            self._api.enable_chime, self._serial
        )
        if ok:
            self._mark_optimistic(True)
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("EZVIZ HP7: enable_chime failed (%s)", self._serial)

    async def async_turn_off(self, **kwargs) -> None:
        ok = await self.hass.async_add_executor_job(
            self._api.disable_chime, self._serial
        )
        if ok:
            self._mark_optimistic(False)
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("EZVIZ HP7: disable_chime failed (%s)", self._serial)


class _BaseHp7Switch(_OptimisticStateMixin, CoordinatorEntity, SwitchEntity):
    """Common scaffolding for boolean device-level switches."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        api,
        serial: str,
        model: str,
        translation_key: str,
        data_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._serial = serial
        self._model = model
        self._data_key = data_key
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{serial}_{translation_key}"

    @property
    def device_info(self) -> DeviceInfo:
        from .device_info import make_device_info
        return make_device_info(self._serial, self._model)

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        return self._shield_stale(data.get(self._data_key))

    async def _async_apply(self, setter: Callable[..., bool], value: bool) -> None:
        """Run the API write and hold the optimistic state on success."""
        ok = await self.hass.async_add_executor_job(setter, self._serial, value)
        if ok:
            self._mark_optimistic(value)
            await self.coordinator.async_request_refresh()


class EzvizHp7DndSwitch(_BaseHp7Switch):
    """Do-Not-Disturb."""

    def __init__(self, coordinator, api, serial: str, model: str) -> None:
        super().__init__(coordinator, api, serial, model, "dnd", "dnd_on")

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_apply(self._api.set_dnd, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_apply(self._api.set_dnd, False)


class EzvizHp7PrivacySwitch(_BaseHp7Switch):
    """Privacy (camera blackout)."""

    def __init__(self, coordinator, api, serial: str, model: str) -> None:
        super().__init__(coordinator, api, serial, model, "privacy", "privacy_on")

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_apply(self._api.set_privacy, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_apply(self._api.set_privacy, False)


class EzvizHp7DefenceSwitch(_BaseHp7Switch):
    """Armed / disarmed."""

    def __init__(self, coordinator, api, serial: str, model: str) -> None:
        super().__init__(coordinator, api, serial, model, "defence", "defence_on")

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_apply(self._api.set_defence, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_apply(self._api.set_defence, False)


class EzvizHp7LabelLightSwitch(_BaseHp7Switch):
    """Doorbell name-tag LED (CHIME_INDICATOR_LIGHT, switch type 611)."""

    def __init__(self, coordinator, api, serial: str, model: str) -> None:
        super().__init__(
            coordinator, api, serial, model, "label_light", "label_light_on"
        )

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_apply(self._api.set_label_light, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_apply(self._api.set_label_light, False)


class EzvizHp7ChimePirSwitch(_OptimisticStateMixin, CoordinatorEntity, SwitchEntity):
    """Toggle ChimeMusic.pir_enable (PIR motion sound notification)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        api,
        serial: str,
        *,
        model: str = "HP7",
        translation_key: str = "chime_pir",
        state_lookup: Callable[[dict], Any] | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._serial = serial
        self._model = model
        self._state_lookup = state_lookup
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{serial}_{translation_key}"

    @property
    def device_info(self) -> DeviceInfo:
        from .device_info import make_device_info
        return make_device_info(self._serial, self._model)

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        if self._state_lookup is not None:
            return self._shield_stale(self._state_lookup(data))
        return self._shield_stale(data.get("chime_pir_is_on"))

    async def async_turn_on(self, **kwargs) -> None:
        ok = await self.hass.async_add_executor_job(
            self._api.set_chime_pir_enable, self._serial, True
        )
        if ok:
            self._mark_optimistic(True)
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        ok = await self.hass.async_add_executor_job(
            self._api.set_chime_pir_enable, self._serial, False
        )
        if ok:
            self._mark_optimistic(False)
            await self.coordinator.async_request_refresh()
