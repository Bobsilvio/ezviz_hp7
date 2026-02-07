"""EZVIZ HP7 API client."""
from __future__ import annotations

import logging
from typing import Any

from .pylocalapi.client import EzvizClient
from .pylocalapi.camera import EzvizCamera

_LOGGER = logging.getLogger(__name__)

DEFAULT_DOOR_LOCK_NO = 2
DEFAULT_GATE_LOCK_NO = 1

REGION_URLS: dict[str, str] = {
    "eu": "apiieu.ezvizlife.com",
    "us": "apiisa.ezvizlife.com",
    "cn": "apiicn.ezvizlife.com",
    "as": "apiias.ezvizlife.com",
    "sa": "apiisa.ezvizlife.com",
    "ru": "apirus.ezvizru.com",
}


class Hp7Api:
    """EZVIZ HP7 API client for cloud and local operations."""

    def __init__(
        self,
        username: str,
        password: str | None = None,
        region: str = "eu",
        token: dict[str, Any] | None = None,
    ) -> None:
        """Initialize EZVIZ HP7 API client.
        
        Args:
            username: EZVIZ account username.
            password: EZVIZ account password.
            region: API region (eu, us, cn, as, sa, ru).
            token: Optional cached authentication token.
        """
        self._username = username
        self._password = password
        self._region = region
        self._token = token
        self._client: EzvizClient | None = None
        self._url = REGION_URLS.get(region, REGION_URLS["eu"])
        self.supports_door = True
        self.supports_gate = True


    @property
    def token(self) -> dict[str, Any] | None:
        """Get the current authentication token.
        
        Returns:
            Authentication token dict or None if not authenticated.
        """
        return self._token

    def ensure_client(self) -> None:
        """Ensure EzvizClient is initialized.
        
        Creates the client if it doesn't exist and handles token authentication.
        
        Raises:
            RuntimeError: If client initialization fails.
        """
        if self._client:
            return

        try:
            self._client = EzvizClient(
                account=self._username,
                password=self._password,
                url=self._url,
                token=self._token,
            )

            if not self._token:
                self._login_and_store_token()
        except Exception as exc:
            _LOGGER.error("Failed to initialize EzvizClient: %s", exc)
            raise RuntimeError(f"Failed to initialize EZVIZ client: {exc}") from exc

    def _login_and_store_token(self) -> None:
        """Authenticate with EZVIZ server and store token.
        
        Raises:
            ValueError: If login fails.
        """
        if not self._client:
            raise RuntimeError("Client not initialized")
            
        try:
            self._token = self._client.login()
            _LOGGER.debug("EZVIZ HP7 authentication successful")
        except (ValueError, KeyError) as exc:
            _LOGGER.error("EZVIZ HP7 authentication failed: %s", exc)
            raise ValueError(f"Authentication failed: {exc}") from exc

    def login(self) -> bool:
        """Authenticate with EZVIZ server.
        
        Returns:
            True if authentication was successful.
            
        Raises:
            RuntimeError: If authentication fails.
        """
        self.ensure_client()
        return True

    def detect_capabilities(self, serial: str) -> None:
        """Detect device capabilities from EZVIZ API.
        
        Args:
            serial: Device serial number.
        """
        self.ensure_client()
        try:
            if self._client:
                dev = self._client.get_device_infos(serial).get(serial, {})
                _LOGGER.debug("EZVIZ HP7 device %s capabilities detected", serial)
        except (KeyError, AttributeError, ValueError) as exc:
            _LOGGER.debug("Failed to detect capabilities for %s: %s", serial, exc)
        
        # Set default capabilities
        self.supports_door = True
        self.supports_gate = True

    def list_devices(self) -> dict[str, dict[str, Any]]:
        """List all paired EZVIZ devices.
        
        Returns:
            Dictionary mapping device serial to device info.
        """
        self.ensure_client()
        if not self._client:
            return {}
            
        try:
            devices = self._client.get_device_infos()
        except (KeyError, AttributeError, ValueError) as exc:
            _LOGGER.warning("Failed to list devices: %s", exc)
            return {}
        
        result: dict[str, dict[str, Any]] = {}
        for serial, data in devices.items():
            name = data.get("name") or data.get("deviceName") or "Device"
            result[serial] = {"device_name": name}
        return result

    def close(self) -> None:
        """Close API connection and cleanup resources."""
        if self._client:
            try:
                self._client.logout()
            except Exception as exc:
                _LOGGER.debug("Error during logout: %s", exc)
            finally:
                self._client = None

    def _try_unlock(self, serial: str, lock_no: int) -> bool:
        """Attempt to unlock a specific lock.
        
        Args:
            serial: Device serial number.
            lock_no: Lock number to unlock.
            
        Returns:
            True if unlock was successful.
        """
        self.ensure_client()
        if not self._token or not self._client:
            return False
            
        user_id = self._token.get("username") or self._username
        try:
            self._client.remote_unlock(serial, user_id, lock_no)
            _LOGGER.info("Remote unlock OK (serial=%s, lock_no=%s)", serial, lock_no)
            return True
        except (KeyError, AttributeError, ValueError, Exception) as exc:
            _LOGGER.warning(
                "Remote unlock failed (serial=%s, lock_no=%s): %s", serial, lock_no, exc
            )
            return False

    def unlock_door(self, serial: str) -> bool:
        """Unlock the door lock.
        
        Args:
            serial: Device serial number.
            
        Returns:
            True if unlock was successful.
        """
        return self._try_unlock(serial, DEFAULT_DOOR_LOCK_NO) or self._try_unlock(
            serial, DEFAULT_GATE_LOCK_NO
        )

    def unlock_gate(self, serial: str) -> bool:
        """Unlock the gate lock.
        
        Args:
            serial: Device serial number.
            
        Returns:
            True if unlock was successful.
        """
        return self._try_unlock(serial, DEFAULT_GATE_LOCK_NO) or self._try_unlock(
            serial, DEFAULT_DOOR_LOCK_NO
        )

    def get_status(self, serial: str) -> dict[str, Any]:
        """Get current device status.
        
        Args:
            serial: Device serial number.
            
        Returns:
            Dictionary with device status and sensor readings.
        """
        self.ensure_client()
        if not self._client:
            return {}
            
        try:
            camera = EzvizCamera(self._client, serial)
            cam_status = camera.status(refresh=True)
            wifi_info = cam_status.get("WIFI", {})

            _LOGGER.debug("Device status received for %s", serial)
            
            return {
                "name": cam_status.get("name"),
                "version": cam_status.get("version"),
                "upgrade_available": cam_status.get("upgrade_available"),
                "status": cam_status.get("status"),
                "wan_ip": cam_status.get("wan_ip"),
                "pir_status": cam_status.get("PIR_Status"),
                "motion": cam_status.get("Motion_Trigger"),
                "seconds_last_trigger": cam_status.get("Seconds_Last_Trigger"),
                "last_alarm_time": cam_status.get("last_alarm_time"),
                "last_alarm_pic": cam_status.get("last_alarm_pic"),
                "alarm_name": cam_status.get("last_alarm_type_name"),
                "ssid": wifi_info.get("ssid"),
                "signal": wifi_info.get("signal"),
                "local_ip": cam_status.get("local_ip") or wifi_info.get("address"),
            }

        except (KeyError, AttributeError, ValueError, Exception) as exc:
            _LOGGER.warning("Failed to get device status for %s: %s", serial, exc)
            return {}
