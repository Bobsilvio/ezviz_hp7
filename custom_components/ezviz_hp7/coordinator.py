"""Data update coordinator for EZVIZ HP7."""
from __future__ import annotations

import inspect
import logging
from datetime import timedelta
from typing import Any, TYPE_CHECKING

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest device status from API.

        Called periodically to update all coordinator data.

        Returns:
            Device status dictionary with sensor values.
        """
        return await self.hass.async_add_executor_job(
            self.api.get_status, self.serial, self.monitor_serial
        )
