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

    def _login_and_store_token(self, sms_code: int | None = None) -> None:
        """Authenticate with EZVIZ server and store token.

        Raises:
            ValueError: If login fails.
            EzvizAuthVerificationCode: If the account requires SMS-based 2FA
                (the cloud has already pushed the code).
        """
        if not self._client:
            raise RuntimeError("Client not initialized")

        try:
            self._token = self._client.login(sms_code=sms_code)
            _LOGGER.debug("EZVIZ HP7 authentication successful")
        except (ValueError, KeyError) as exc:
            _LOGGER.error("EZVIZ HP7 authentication failed: %s", exc)
            raise ValueError(f"Authentication failed: {exc}") from exc

    def login(self, sms_code: int | None = None) -> bool:
        """Authenticate with EZVIZ server.

        Args:
            sms_code: Optional 2FA code (requested via config_flow after the
                cloud answers with code 6002).

        Returns:
            True if authentication was successful.

        Raises:
            RuntimeError: If authentication fails.
            EzvizAuthVerificationCode: If MFA is required.
        """
        if sms_code is not None:
            # Re-entering with the SMS code: build a fresh client and force
            # login through the code-aware path.
            from .pylocalapi.client import EzvizClient

            self._client = EzvizClient(
                account=self._username,
                password=self._password,
                url=self._url,
                token=self._token,
            )
            self._login_and_store_token(sms_code=sms_code)
            return True
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

    def _set_chime_fields(self, serial: str, **fields: int) -> bool:
        """Patch one or more ChimeMusic fields, preserving the rest."""
        self.ensure_client()
        if not self._client:
            return False
        current = self._get_chime_config(serial) or {}
        config = {**self._CHIME_DEFAULTS, **current, **fields}
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
            _LOGGER.error("EZVIZ HP7: _set_chime_fields failed: %s", exc)
            return False

    def _set_chime(self, serial: str, doorbell_enable: int) -> bool:
        """Legacy wrapper kept for backwards-compatible call sites."""
        return self._set_chime_fields(serial, doorbell_enable=doorbell_enable)

    def enable_chime(self, serial: str) -> bool:
        """Enable monitor chime sound (doorbell_enable=1)."""
        return self._set_chime_fields(serial, doorbell_enable=1)

    def disable_chime(self, serial: str) -> bool:
        """Disable monitor chime sound (doorbell_enable=0)."""
        return self._set_chime_fields(serial, doorbell_enable=0)

    # PIR sound notification (motion alert chime).

    def set_chime_pir_enable(self, serial: str, enable: bool) -> bool:
        return self._set_chime_fields(serial, pir_enable=int(bool(enable)))

    def get_chime_pir_state(self, serial: str) -> bool | None:
        cfg = self._get_chime_config(serial)
        if cfg is None:
            return None
        return cfg.get("pir_enable") in (1, "1", True)

    # Ringtone selection (0-N, exact range depends on firmware).

    def set_chime_ringtone(self, serial: str, ringtone: int) -> bool:
        return self._set_chime_fields(serial, doorbell=max(0, int(ringtone)))

    def get_chime_ringtone(self, serial: str) -> int | None:
        cfg = self._get_chime_config(serial)
        if cfg is None:
            return None
        try:
            return int(cfg.get("doorbell", 0))
        except (TypeError, ValueError):
            return None

    def set_chime_pir_ringtone(self, serial: str, ringtone: int) -> bool:
        return self._set_chime_fields(serial, pir=max(0, int(ringtone)))

    def get_chime_pir_ringtone(self, serial: str) -> int | None:
        cfg = self._get_chime_config(serial)
        if cfg is None:
            return None
        try:
            return int(cfg.get("pir", 0))
        except (TypeError, ValueError):
            return None

    def get_chime_state(self, serial: str) -> bool | None:
        """Return doorbell_enable state (True/False), or None on error."""
        config = self._get_chime_config(serial)
        if config is None:
            return None
        return config.get("doorbell_enable") in (1, "1", True)

    def get_chime_volume(self, serial: str) -> int | None:
        """Return chime volume (0-7), or None on error."""
        config = self._get_chime_config(serial)
        if config is None:
            return None
        try:
            return int(config.get("volume", 7))
        except (TypeError, ValueError):
            return None

    def set_chime_volume(self, serial: str, volume: int) -> bool:
        """Set ChimeMusic volume (0-7), preserving other fields."""
        self.ensure_client()
        if not self._client:
            return False
        volume = max(0, min(7, int(volume)))
        current = self._get_chime_config(serial) or {}
        config = {**self._CHIME_DEFAULTS, **current, "volume": volume}
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
            _LOGGER.error("EZVIZ HP7: set_chime_volume failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # DND / Privacy / Defence — wrappers around pylocalapi calls
    # ------------------------------------------------------------------

    # SwitchType.PRIVACY (camera blackout) = 7.
    _PRIVACY_SWITCH_TYPE = 7
    # SwitchType.CHIME_INDICATOR_LIGHT (HP7 doorbell label/name LED) = 611.
    _LABEL_LIGHT_SWITCH_TYPE = 611

    def set_dnd(self, serial: str, enable: bool) -> bool:
        """Toggle Do-Not-Disturb on the device."""
        self.ensure_client()
        if not self._client:
            return False
        try:
            self._client.do_not_disturb(serial, enable=int(bool(enable)))
            return True
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("EZVIZ HP7: do_not_disturb failed: %s", exc)
            return False

    def set_privacy(self, serial: str, enable: bool) -> bool:
        """Toggle privacy (camera blackout) via switch_status."""
        self.ensure_client()
        if not self._client:
            return False
        try:
            self._client.switch_status(
                serial,
                self._PRIVACY_SWITCH_TYPE,
                bool(enable),
            )
            return True
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("EZVIZ HP7: privacy switch_status failed: %s", exc)
            return False

    def set_defence(self, serial: str, enable: bool) -> bool:
        """Arm (enable=True) or disarm (False) motion detection."""
        self.ensure_client()
        if not self._client:
            return False
        try:
            self._client.set_camera_defence(serial, int(bool(enable)))
            return True
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("EZVIZ HP7: set_camera_defence failed: %s", exc)
            return False

    def get_latest_alarm_detail(self, serial: str) -> dict[str, Any] | None:
        """Fetch the most recent detailed alarm record for ``serial``.

        Used to recover information the basic `last_alarm_type_name` field
        does not carry (e.g. which RFID card unlocked the door, face/palm
        recognition metadata, EZVIZ message id for picture lookup).

        Returns the raw first alarm dict, or None on error / no data.
        """
        self.ensure_client()
        if not self._client:
            return None
        try:
            payload = self._client.get_alarminfo(serial, limit=1)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("EZVIZ HP7: get_alarminfo failed: %s", exc)
            return None

        # Response shape: { "alarms": [ {...} ], "meta": {...}, "page": {...} }
        # (key name observed as "alarms" or "alarmLogs" depending on firmware).
        for key in ("alarms", "alarmLogs", "alarmList", "alarmInfos"):
            value = payload.get(key)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value[0]
        return None

    def set_label_light(self, serial: str, enable: bool) -> bool:
        """Toggle the doorbell name/label LED (CHIME_INDICATOR_LIGHT switch).

        Issue #24 — controls the LED that illuminates the name tag plate on
        the HP7 doorbell button. Switch type 611 per pylocalapi constants.
        """
        self.ensure_client()
        if not self._client:
            return False
        try:
            self._client.switch_status(
                serial,
                self._LABEL_LIGHT_SWITCH_TYPE,
                bool(enable),
            )
            return True
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("EZVIZ HP7: label light switch failed: %s", exc)
            return False

    @staticmethod
    def _coerce_bool(value: Any) -> bool | None:
        """Loose bool coercion for cloud status fields (int/str/bool)."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("1", "true", "on", "yes", "y", "enable", "enabled"):
                return True
            if v in ("0", "false", "off", "no", "n", "disable", "disabled"):
                return False
        return None

    def _read_extra_states(self, serial: str) -> dict[str, Any]:
        """Read DND / privacy / defence state from a fresh get_device_infos."""
        out: dict[str, Any] = {}
        if not self._client:
            return out
        try:
            info = self._client.get_device_infos(serial) or {}
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("EZVIZ HP7: extra states fetch failed: %s", exc)
            return out

        nodisturb = info.get("NODISTURB") or {}
        if isinstance(nodisturb, dict):
            enable = nodisturb.get("enable")
            coerced = self._coerce_bool(enable)
            if coerced is not None:
                out["dnd_on"] = coerced

        switches = info.get("SWITCH") or {}
        if isinstance(switches, dict):
            # `SWITCH` may map switchType → {enable: bool} OR be a list.
            sw_list: list[dict[str, Any]] = []
            if isinstance(switches, dict) and "switchStatusInfos" in switches:
                sw_list = switches.get("switchStatusInfos") or []
            elif isinstance(switches, list):
                sw_list = switches
            for sw in sw_list:
                try:
                    s_type = int(sw.get("type", -1))
                except (TypeError, ValueError):
                    continue
                if s_type == self._PRIVACY_SWITCH_TYPE:
                    coerced = self._coerce_bool(sw.get("enable"))
                    if coerced is not None:
                        out["privacy_on"] = coerced
                elif s_type == self._LABEL_LIGHT_SWITCH_TYPE:
                    coerced = self._coerce_bool(sw.get("enable"))
                    if coerced is not None:
                        out["label_light_on"] = coerced

        status = info.get("STATUS") or {}
        if isinstance(status, dict):
            global_status = status.get("globalStatus")
            coerced = self._coerce_bool(global_status)
            if coerced is not None:
                out["defence_on"] = coerced
        return out

    def get_status(
        self,
        serial: str,
        monitor_serial: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """Get current device status.

        Args:
            serial: Camera serial number.
            monitor_serial: Optional indoor monitor serial(s). May be a single
                string (legacy single-monitor setup) or a list of strings for
                multi-monitor (e.g. HP7 bifamigliare).

        Returns:
            Dictionary with device status and sensor readings. Extra keys for
            multi-monitor: ``chime_is_on_monitors`` and
            ``chime_volume_monitors`` are dicts keyed by monitor serial.
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

            # Read chime state of the camera itself (best-effort).
            chime_on = self.get_chime_state(serial)
            if chime_on is not None:
                status_data["chime_is_on"] = chime_on
            chime_vol = self.get_chime_volume(serial)
            if chime_vol is not None:
                status_data["chime_volume"] = chime_vol
            pir_on = self.get_chime_pir_state(serial)
            if pir_on is not None:
                status_data["chime_pir_is_on"] = pir_on
            ring = self.get_chime_ringtone(serial)
            if ring is not None:
                status_data["chime_ringtone"] = ring
            pir_ring = self.get_chime_pir_ringtone(serial)
            if pir_ring is not None:
                status_data["chime_pir_ringtone"] = pir_ring

            # Multi-monitor support: accept str or list and produce per-serial
            # dicts so the entity layer can iterate.
            monitors: list[str] = []
            if isinstance(monitor_serial, str) and monitor_serial.strip():
                monitors = [monitor_serial.strip()]
            elif isinstance(monitor_serial, (list, tuple)):
                monitors = [s.strip() for s in monitor_serial if isinstance(s, str) and s.strip()]
            if monitors:
                monitor_chimes: dict[str, bool] = {}
                monitor_vols: dict[str, int] = {}
                monitor_pir: dict[str, bool] = {}
                monitor_ring: dict[str, int] = {}
                monitor_pir_ring: dict[str, int] = {}
                for ms in monitors:
                    mc = self.get_chime_state(ms)
                    if mc is not None:
                        monitor_chimes[ms] = mc
                    mv = self.get_chime_volume(ms)
                    if mv is not None:
                        monitor_vols[ms] = mv
                    mp = self.get_chime_pir_state(ms)
                    if mp is not None:
                        monitor_pir[ms] = mp
                    mr = self.get_chime_ringtone(ms)
                    if mr is not None:
                        monitor_ring[ms] = mr
                    mpr = self.get_chime_pir_ringtone(ms)
                    if mpr is not None:
                        monitor_pir_ring[ms] = mpr
                if monitor_chimes:
                    status_data["chime_is_on_monitors"] = monitor_chimes
                    if len(monitor_chimes) == 1:
                        status_data["chime_is_on_monitor"] = next(
                            iter(monitor_chimes.values())
                        )
                if monitor_vols:
                    status_data["chime_volume_monitors"] = monitor_vols
                if monitor_pir:
                    status_data["chime_pir_is_on_monitors"] = monitor_pir
                if monitor_ring:
                    status_data["chime_ringtone_monitors"] = monitor_ring
                if monitor_pir_ring:
                    status_data["chime_pir_ringtone_monitors"] = monitor_pir_ring

            # Extra states best-effort (DND / privacy / defence).
            extra = self._read_extra_states(serial)
            status_data.update(extra)

            return status_data

        except (KeyError, AttributeError, ValueError, TypeError, RequestException) as exc:
            _LOGGER.warning("Failed to get device status for %s: %s", serial, exc)
            return {}
