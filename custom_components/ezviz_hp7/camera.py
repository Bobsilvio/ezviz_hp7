"""EZVIZ HP7/CP7 camera entity for alarm snapshots and live stream."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

from homeassistant.components.camera import Camera, CameraEntityFeature
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
    """Set up EZVIZ HP7/CP7 camera entities.

    Args:
        hass: Home Assistant instance.
        entry: Config entry.
        async_add_entities: Callback to add entities.
    """
    data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    coordinator: Hp7Coordinator = data["coordinator"]
    serial: str = data["serial"]

    async_add_entities(
        [Hp7Camera(hass, coordinator, serial)]
    )


class Hp7Camera(Camera, CoordinatorEntity):
    """Camera entity for EZVIZ HP7/CP7 device.

    Provides:
      - Latest alarm snapshot from cloud API
      - Live stream via local RTSP (when available) or Hikvision protocol
    """

    _attr_has_entity_name = True
    _attr_translation_key = "camera"

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
        model = getattr(self.coordinator.api, "model", "HP7")
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=f"EZVIZ {model} ({self._serial})",
            manufacturer="EZVIZ",
            model=model,
        )

    @property
    def supported_features(self) -> CameraEntityFeature:
        """Return supported features based on device model and local IP."""
        data = self.coordinator.data or {}
        ip = data.get("local_ip")
        model = getattr(self.coordinator.api, "model", "HP7")

        if model == "CP7":
            # CP7: needs Hikvision protocol for live stream
            # Feature flag reserved for future implementation
            return CameraEntityFeature(0)

        # HP7: RTSP if local IP is available
        if ip and ip != "0.0.0.0":
            return CameraEntityFeature.STREAM
        return CameraEntityFeature(0)

    async def stream_source(self) -> str | None:
        """Return RTSP stream URL for live video.

        For HP7: uses devAuthCode from cloud API on port 554.
        For CP7: returns None (Hikvision protocol relay not yet implemented).
        """
        model = getattr(self.coordinator.api, "model", "HP7")

        if model == "CP7":
            # CP7 doesn't expose RTSP natively.
            # Future: Hikvision protocol relay via ffmpeg subprocess.
            return None

        # HP7: RTSP with devAuthCode
        data = self.coordinator.data or {}
        ip = data.get("local_ip")
        port = data.get("local_rtsp_port") or "554"
        if not ip or ip == "0.0.0.0":
            return None
        try:
            password = await self.hass.async_add_executor_job(
                self.coordinator.api.get_rtsp_password, self._serial
            )
            if not password:
                return None
            return f"rtsp://admin:{password}@{ip}:{port}/Streaming/Channels/101/"
        except Exception as exc:
            _LOGGER.debug("stream_source failed for %s: %s", self._serial, exc)
            return None

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
        url = (self.coordinator.data or {}).get("last_alarm_pic")
        if not url:
            _LOGGER.debug("No snapshot URL available for %s", self._serial)
            return None

        try:
            token = self.coordinator.api.token
            if not token:
                _LOGGER.warning("No authentication token available")
                return None

            session: ClientSession = async_get_clientsession(self.hass)
            access_token = token.get("access_token")

            headers = {
                "User-Agent": "EZVIZ/5.0",
            }

            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"

            async with session.get(url, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    return await resp.read()

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
        """Return WebRTC provider (not supported)."""
        return None

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator data update."""
        self.async_write_ha_state()
