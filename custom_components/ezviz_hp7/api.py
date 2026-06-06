"""EZVIZ HP7 API client."""
from __future__ import annotations

import json
import logging
from typing import Any

from requests.exceptions import RequestException

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
                self._client.get_device_infos(serial)
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
            device_info = data.get("deviceInfos", {})
            name = device_info.get("name") or device_info.get("deviceName") or "Device"
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

    _CHIME_DEFAULTS = {
        "doorbell": 10,
        "pir": 0,
        "volume": 7,
        "doorbell_enable": 1,
        "pir_enable": 0,
    }

    def _get_chime_config(self, serial: str) -> dict[str, Any] | None:
        """Fetch full ChimeMusic config dict, or None on error."""
        self.ensure_client()
        if not self._client:
            return None
        try:
            result = self._client.get_dev_config(serial, 1, "ChimeMusic")
        except (KeyError, AttributeError, ValueError, RequestException) as exc:
            _LOGGER.warning("EZVIZ HP7: get_dev_config(ChimeMusic) failed: %s", exc)
            return None
        value = result.get("valueInfo") or result.get("value")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as exc:
                _LOGGER.warning("EZVIZ HP7: ChimeMusic value parse failed: %s", exc)
                return None
        return value if isinstance(value, dict) else None

    def _set_chime(self, serial: str, doorbell_enable: int) -> bool:
        """Set ChimeMusic doorbell_enable, preserving other fields."""
        self.ensure_client()
        if not self._client:
            return False
        current = self._get_chime_config(serial) or {}
        config = {**self._CHIME_DEFAULTS, **current, "doorbell_enable": doorbell_enable}
        value = json.dumps(config, separators=(",", ":"))
        url = (
            f"https://{self._url}/v3/devconfig/v1/keyValue"
            f"/{serial}/1/op"
        )
        params = {"key": "ChimeMusic", "value": value}
        try:
            resp = self._client._session.put(url, params=params, timeout=15)
            resp.raise_for_status()
            return True
        except (RequestException, ValueError) as exc:
            _LOGGER.error("EZVIZ HP7: _set_chime failed: %s", exc)
            return False

    def enable_chime(self, serial: str) -> bool:
        """Enable monitor chime sound (doorbell_enable=1)."""
        return self._set_chime(serial, doorbell_enable=1)

    def disable_chime(self, serial: str) -> bool:
        """Disable monitor chime sound (doorbell_enable=0)."""
        return self._set_chime(serial, doorbell_enable=0)

    def get_chime_state(self, serial: str) -> bool | None:
        """Return doorbell_enable state (True/False), or None on error."""
        config = self._get_chime_config(serial)
        if config is None:
            return None
        return config.get("doorbell_enable") in (1, "1", True)

    def get_status(
        self, serial: str, monitor_serial: str | None = None
    ) -> dict[str, Any]:
        """Get current device status.

        Args:
            serial: Camera serial number.
            monitor_serial: Optional indoor monitor serial for chime state.

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

            status_data = {
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

            # Read chime state (won't break polling if it fails)
            chime_on = self.get_chime_state(serial)
            if chime_on is not None:
                status_data["chime_is_on"] = chime_on

            if monitor_serial:
                monitor_chime = self.get_chime_state(monitor_serial)
                if monitor_chime is not None:
                    status_data["chime_is_on_monitor"] = monitor_chime

            return status_data

        except (KeyError, AttributeError, ValueError, TypeError, RequestException) as exc:
            _LOGGER.warning("Failed to get device status for %s: %s", serial, exc)
            return {}
