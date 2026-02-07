"""Data update coordinator for EZVIZ HP7."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, TYPE_CHECKING

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import UPDATE_INTERVAL_SEC

if TYPE_CHECKING:
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
    ) -> None:
        """Initialize coordinator.
        
        Args:
            hass: Home Assistant instance.
            api: EZVIZ HP7 API instance.
            serial: Device serial number.
        """
        super().__init__(
            hass,
            _LOGGER,
            name="EZVIZ HP7",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SEC),
        )
        self.api = api
        self.serial = serial

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest device status from API.
        
        Called periodically to update all coordinator data.
        
        Returns:
            Device status dictionary with sensor values.
        """
        return await self.hass.async_add_executor_job(
            self.api.get_status, self.serial
        )
