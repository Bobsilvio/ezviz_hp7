"""EZVIZ HP7 camera entity for displaying alarm snapshots."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN

if TYPE_CHECKING:
    from aiohttp import ClientSession
    from .coordinator import Hp7Coordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EZVIZ HP7 camera entities.
    
    Args:
        hass: Home Assistant instance.
        entry: Config entry.
        async_add_entities: Callback to add entities.
    """
    data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    coordinator: Hp7Coordinator = data["coordinator"]
    serial: str = data["serial"]
    
    async_add_entities(
        [Hp7LastSnapshotCamera(hass, coordinator, serial)]
    )


class Hp7LastSnapshotCamera(Camera, CoordinatorEntity):
    """Camera entity for latest EZVIZ device alarm snapshot.
    
    This camera entity displays the most recent alarm snapshot captured
    by the EZVIZ HP7 device. The image is fetched from the cloud API
    each time it's requested.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "last_snapshot"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: Hp7Coordinator,
        serial: str,
    ) -> None:
        """Initialize camera entity.
        
        Args:
            hass: Home Assistant instance.
            coordinator: Data coordinator.
            serial: Device serial number.
        """
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self.hass = hass
        self._serial = serial
        self._attr_unique_id = f"{DOMAIN}_{serial}_last_snapshot"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=f"EZVIZ HP7 ({self._serial})",
            manufacturer="EZVIZ",
            model="HP7",
        )

    @property
    def supported_features(self) -> int:
        """Return supported features (none for this camera)."""
        return 0

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Fetch and return latest alarm snapshot.
        
        Args:
            width: Desired width (not used).
            height: Desired height (not used).
            
        Returns:
            JPEG image bytes or None if not available.
        """
        # Get snapshot URL from coordinator data
        url = (self.coordinator.data or {}).get("last_alarm_pic")
        if not url:
            _LOGGER.debug("No snapshot URL available for %s", self._serial)
            return None

        try:
            # Get authentication token from API
            token = self.coordinator.api.token
            if not token:
                _LOGGER.warning("No authentication token available")
                return None

            # Fetch image from URL
            session: ClientSession = async_get_clientsession(self.hass)
            access_token = token.get("access_token")
            
            headers = {
                "User-Agent": "EZVIZ/5.0",
            }
            
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"

            async with session.get(url, headers=headers, timeout=15) as resp:
                # Check response status
                if resp.status == 200:
                    return await resp.read()
                
                # Log error for debugging
                try:
                    error_text = await resp.text()
                except Exception:
                    error_text = "Unknown error"
                    
                _LOGGER.warning(
                    "Failed to fetch snapshot for %s: HTTP %s - %s",
                    self._serial,
                    resp.status,
                    error_text,
                )
                return None

        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout fetching snapshot for %s", self._serial)
            return None
        except Exception as exc:
            _LOGGER.warning(
                "Error fetching snapshot for %s: %s",
                self._serial,
                exc,
            )
            return None

    async def _async_get_supported_webrtc_provider(self, *args, **kwargs) -> None:
        """Return WebRTC provider (not supported).
        
        Returns:
            None as WebRTC streaming is not supported.
        """
        return None

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator data update.
        
        Called when coordinator data is refreshed.
        """
        self.async_write_ha_state()
