"""Data update coordinator for EZVIZ HP7."""
from __future__ import annotations

import inspect
import logging
from datetime import timedelta
from typing import Any, TYPE_CHECKING

from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import UPDATE_INTERVAL_SEC

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from .api import Hp7Api

_LOGGER = logging.getLogger(__name__)


class Hp7Coordinator(DataUpdateCoordinator):
    """Manage periodic data updates from EZVIZ HP7 API.
    
    This coordinator handles fetching device status and sensor data
    at regular intervals and distributing updates to all entities.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api: Hp7Api,
        serial: str,
        monitor_serial: str | None = None,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize coordinator.

        Args:
            hass: Home Assistant instance.
            api: EZVIZ HP7 API instance.
            serial: Camera serial number.
            monitor_serial: Optional indoor monitor serial.
            config_entry: HA config entry. Required by recent HA versions
                (2024.12+) to use async_config_entry_first_refresh; older
                releases accept it as a no-op kwarg too.
        """
        # Newer HA (2024.10+) requires DataUpdateCoordinator subclasses to
        # carry their owning ConfigEntry before async_config_entry_first_refresh
        # can be used. The kwarg name is `config_entry` — pass it through when
        # the base class actually declares it, fall back to plain init on
        # older builds.
        kwargs: dict[str, Any] = {
            "name": "EZVIZ HP7",
            "update_interval": timedelta(seconds=UPDATE_INTERVAL_SEC),
        }
        params = inspect.signature(DataUpdateCoordinator.__init__).parameters
        if "config_entry" in params and config_entry is not None:
            kwargs["config_entry"] = config_entry
        super().__init__(hass, _LOGGER, **kwargs)

        # Some HA builds accept the kwarg but don't set the attribute, or
        # reload paths arrive here without a current_entry context: assign
        # explicitly so async_config_entry_first_refresh's check passes.
        if config_entry is not None and getattr(self, "config_entry", None) is None:
            try:
                self.config_entry = config_entry  # type: ignore[assignment]
            except AttributeError:
                pass

        self.api = api
        self.serial = serial
        self.monitor_serial = monitor_serial
        # The EZVIZ cloud (pagelist) hiccups often — 504 / euauth timeouts /
        # brief rate-limits — usually recovering within a cycle or two. Rather
        # than blanking every cloud-backed entity to "unknown" on the first
        # blip, tolerate a short run of failures by holding the last-good data,
        # and only surface UpdateFailed (entities unavailable) once the outage
        # is sustained. 4 cycles * 15 s ≈ 1 min of grace (#36 andresako).
        self._fail_streak: int = 0
        self._last_alarm_time: str | None = None
        self._last_alarm_detail: dict[str, Any] | None = None
        # Key list cache (RFID cards / face / palm enrolments) — refresh
        # every 5 min to keep KeyMgr API load minimal while still picking up
        # new enrolments / renames within a polling cycle or two.
        self._key_list: list[dict[str, Any]] | None = None
        self._key_list_age: int = 10**6  # force first fetch

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest device status from API.

        Called periodically to update all coordinator data. When the
        ``last_alarm_time`` field changes between polls we also call the
        EZVIZ alarminfo endpoint once to fetch the detailed alarm record
        (RFID card id, picture id, etc.) and embed it under
        ``latest_alarm_detail``.
        """
        # A blip is either an exception OR an empty dict (get_status swallows
        # RequestException and returns {}). Treat both the same: hold the last
        # good data for a short grace window before degrading to unavailable.
        try:
            data = await self.hass.async_add_executor_job(
                self.api.get_status, self.serial, self.monitor_serial
            )
            failure: str | None = None if data else "empty status from cloud"
        except Exception as exc:  # noqa: BLE001
            data = None
            failure = str(exc)

        if failure is not None:
            self._fail_streak += 1
            # Keep serving the previous cycle's values while the outage is
            # short and we actually have something to serve.
            if self._fail_streak <= 4 and self.data:
                _LOGGER.warning(
                    "EZVIZ cloud fetch failed (%d in a row), keeping last "
                    "known values: %s",
                    self._fail_streak,
                    failure,
                )
                return self.data
            # Right past the grace window, try re-authenticating once: an
            # expired session would otherwise fail every poll until restart,
            # which matches "unknown for hours" reports (#36). Do it a single
            # time so we don't hammer login (and trip rate-limits) during a
            # genuine cloud outage.
            if self._fail_streak == 5:
                _LOGGER.warning(
                    "EZVIZ cloud failing for %d cycles — forcing re-login",
                    self._fail_streak,
                )
                await self.hass.async_add_executor_job(self.api.force_relogin)
            # Sustained outage (or no prior data): mark entities unavailable.
            raise UpdateFailed(f"EZVIZ cloud fetch failed: {failure}")

        # Success — clear the streak and continue with fresh data.
        self._fail_streak = 0

        alarm_time = data.get("last_alarm_time")
        if alarm_time and alarm_time != self._last_alarm_time:
            detail = await self.hass.async_add_executor_job(
                self.api.get_latest_alarm_detail, self.serial
            )
            if detail:
                self._last_alarm_detail = detail
            self._last_alarm_time = alarm_time
        if self._last_alarm_detail is not None:
            data["latest_alarm_detail"] = self._last_alarm_detail

        # Refresh key list every ~5 min (with UPDATE_INTERVAL_SEC = 15 that's
        # one fetch every 20 polling cycles).
        self._key_list_age += 1
        if self._key_list_age >= 20 or self._key_list is None:
            keys = await self.hass.async_add_executor_job(
                self.api.get_key_list, self.serial
            )
            if keys is not None:
                self._key_list = keys
            self._key_list_age = 0
        if self._key_list is not None:
            data["keys"] = self._key_list

        return data
