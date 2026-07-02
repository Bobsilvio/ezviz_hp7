"""Config flow for EZVIZ HP7 integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .api import Hp7Api
from .const import (
    DOMAIN,
    CONF_REGION,
    CONF_SERIAL,
    CONF_MONITOR_SERIAL,
    CONF_RELAY_PORT,
    CONF_AGGRESSIVE_MPEGTS,
    CONF_VIDEO_CODEC,
    VIDEO_CODEC_AUTO,
    VIDEO_CODECS,
    CONF_STREAM_SOURCE,
    STREAM_SOURCE_CLOUD,
    STREAM_SOURCES,
    CONF_STREAM_MODE,
    STREAM_MODE_AUTO,
    STREAM_MODES,
)
from .pylocalapi.exceptions import EzvizAuthVerificationCode

CONF_SMS_CODE = "sms_code"
SMS_SCHEMA = vol.Schema({vol.Required(CONF_SMS_CODE): str})

_LOGGER = logging.getLogger(__name__)

# Schema for initial username/password entry
DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
        vol.Required(CONF_REGION, default="eu"): vol.In(
            ["eu", "us", "cn", "as", "sa", "ru"]
        ),
    }
)

# Schema for manual serial entry
SERIAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SERIAL): str,
    }
)


def _looks_like_long_serial(serial: str) -> bool:
    """Check if serial looks like a long/stable identifier.
    
    Args:
        serial: Serial string to check.
        
    Returns:
        True if serial appears to be a long identifier.
    """
    # Heuristic: long serials usually contain dashes or are quite long
    return ("-" in serial) or (len(serial) >= 12)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for EZVIZ HP7 integration."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "OptionsFlow":
        """Return the options flow.

        Home Assistant 2024.12+ injects `config_entry` automatically as a
        base-class property — we must not pass it to the constructor or
        assign it from a subclass, or the UI gets a 500.
        """
        return OptionsFlow()

    def __init__(self) -> None:
        """Initialize config flow."""
        self._cached_creds: dict[str, Any] | None = None
        self._device_options: dict[str, str] | None = None
        self._serial_to_unique: dict[str, str] | None = None
        self._pending_api: Hp7Api | None = None  # set during 2FA flow

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle user data entry step.
        
        Args:
            user_input: User provided data.
            
        Returns:
            Form config or next step.
        """
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA)

        # Try to authenticate and list devices
        try:
            api = Hp7Api(
                user_input["username"],
                user_input["password"],
                user_input[CONF_REGION],
            )
            ok = await self.hass.async_add_executor_job(api.login)
            if not ok:
                raise ValueError("Login returned False")
        except EzvizAuthVerificationCode:
            # Cloud already pushed the SMS code; hand off to async_step_sms.
            self._cached_creds = user_input
            self._pending_api = api
            return await self.async_step_sms()
        except ValueError as exc:
            _LOGGER.error("EZVIZ authentication failed: %s", exc)
            return self.async_show_form(
                step_id="user",
                data_schema=DATA_SCHEMA,
                errors={"base": "auth"},
            )
        except Exception as exc:
            _LOGGER.error("EZVIZ API error: %s", exc)
            return self.async_show_form(
                step_id="user",
                data_schema=DATA_SCHEMA,
                errors={"base": "cannot_connect"},
            )

        return await self._post_login(api, user_input)

    async def _post_login(
        self, api: Hp7Api, user_input: dict[str, Any]
    ) -> FlowResult:
        """Branch to pick-serial or enter-serial after a successful login."""
        if api.token:
            user_input["token"] = api.token

        devices: dict[str, dict[str, Any]] = {}
        try:
            if hasattr(api, "list_devices"):
                devices = await self.hass.async_add_executor_job(api.list_devices)
        except Exception as exc:
            _LOGGER.error("EZVIZ device list error: %s", exc)
            return self.async_show_form(
                step_id="user",
                data_schema=DATA_SCHEMA,
                errors={"base": "cannot_connect"},
            )

        options: dict[str, str] = {}
        serial_to_unique: dict[str, str] = {}

        for serial_key, info in (devices or {}).items():
            name = (info.get("name") or info.get("device_name") or "Device").strip()
            api_unique = (
                info.get("device_id")
                or info.get("uuid")
                or info.get("serial_long")
                or info.get("full_serial")
                or None
            )
            if _looks_like_long_serial(serial_key):
                shown_serial = serial_key
            else:
                shown_serial = (
                    info.get("serial_long") or info.get("full_serial") or None
                )
            if not shown_serial:
                continue
            unique_id = api_unique or shown_serial
            if shown_serial in options or unique_id in serial_to_unique.values():
                continue
            options[shown_serial] = f"{name} ({shown_serial})"
            serial_to_unique[shown_serial] = unique_id

        self._cached_creds = user_input

        if options:
            self._device_options = options
            self._serial_to_unique = serial_to_unique
            return await self.async_step_pick_serial()
        return await self.async_step_enter_serial()

    async def async_step_sms(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for the 2FA SMS code and re-attempt login with it.

        Reached from async_step_user when the EZVIZ cloud answers with code
        6002 (MFA enabled). pylocalapi has already triggered the SMS push, so
        we just need to collect the code from the user.
        """
        if self._pending_api is None or self._cached_creds is None:
            # State lost (e.g. flow resumed in a weird way): bounce back to the
            # credentials step rather than crash.
            return await self.async_step_user()

        if user_input is None:
            return self.async_show_form(step_id="sms", data_schema=SMS_SCHEMA)

        raw = str(user_input.get(CONF_SMS_CODE, "")).strip()
        try:
            code_int = int(raw)
        except ValueError:
            return self.async_show_form(
                step_id="sms",
                data_schema=SMS_SCHEMA,
                errors={"base": "invalid_sms"},
            )

        api = self._pending_api
        try:
            await self.hass.async_add_executor_job(api.login, code_int)
        except EzvizAuthVerificationCode:
            return self.async_show_form(
                step_id="sms",
                data_schema=SMS_SCHEMA,
                errors={"base": "invalid_sms"},
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("EZVIZ SMS auth failed: %s", exc)
            return self.async_show_form(
                step_id="sms",
                data_schema=SMS_SCHEMA,
                errors={"base": "cannot_connect"},
            )

        self._pending_api = None
        return await self._post_login(api, self._cached_creds)

    async def async_step_pick_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle device selection from list.
        
        Args:
            user_input: User selected device.
            
        Returns:
            Form config or config entry.
        """
        assert self._device_options is not None, "Device list not prepared"

        schema = vol.Schema(
            {vol.Required(CONF_SERIAL): vol.In(list(self._device_options.keys()))}
        )

        if user_input is None:
            return self.async_show_form(
                step_id="pick_serial",
                data_schema=schema,
                description_placeholders={
                    "devices": ", ".join(self._device_options.values())
                },
            )

        serial = user_input[CONF_SERIAL]

        # Use stable unique ID if available
        unique_id = None
        if self._serial_to_unique:
            unique_id = self._serial_to_unique.get(serial)

        await self.async_set_unique_id(unique_id or serial)
        self._abort_if_unique_id_configured()

        data = {**(self._cached_creds or {}), CONF_SERIAL: serial}
        title = f"EZVIZ HP7 ({serial})"
        return self.async_create_entry(title=title, data=data)

    async def async_step_enter_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual serial entry.
        
        Args:
            user_input: User provided serial number.
            
        Returns:
            Form config or config entry.
        """
        if user_input is None:
            return self.async_show_form(
                step_id="enter_serial",
                data_schema=SERIAL_SCHEMA,
            )

        serial = user_input[CONF_SERIAL].strip()

        # Normalize serial
        await self.async_set_unique_id(serial)
        self._abort_if_unique_id_configured()

        data = {**(self._cached_creds or {}), CONF_SERIAL: serial}
        title = f"EZVIZ HP7 ({serial})"
        return self.async_create_entry(title=title, data=data)


class OptionsFlow(config_entries.OptionsFlow):
    """Options flow to configure optional indoor monitor serial.

    Note: do NOT assign `self.config_entry` in __init__ — Home Assistant
    2024.12+ makes that a base-class property and assigning to it from a
    subclass raises and surfaces in the UI as 500 "Server got itself in
    trouble".
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options."""
        if user_input is not None:
            monitor = (user_input.get(CONF_MONITOR_SERIAL) or "").strip()
            try:
                relay_port = int(user_input.get(CONF_RELAY_PORT) or 0)
            except (TypeError, ValueError):
                relay_port = 0
            if relay_port < 0 or relay_port > 65535:
                relay_port = 0
            aggressive = bool(user_input.get(CONF_AGGRESSIVE_MPEGTS, False))
            codec = str(
                user_input.get(CONF_VIDEO_CODEC) or VIDEO_CODEC_AUTO
            ).lower()
            if codec not in VIDEO_CODECS:
                codec = VIDEO_CODEC_AUTO
            source = str(
                user_input.get(CONF_STREAM_SOURCE) or STREAM_SOURCE_CLOUD
            ).lower()
            if source not in STREAM_SOURCES:
                source = STREAM_SOURCE_CLOUD
            mode = str(
                user_input.get(CONF_STREAM_MODE) or STREAM_MODE_AUTO
            ).lower()
            if mode not in STREAM_MODES:
                mode = STREAM_MODE_AUTO
            return self.async_create_entry(
                title="",
                data={
                    CONF_MONITOR_SERIAL: monitor,
                    CONF_RELAY_PORT: relay_port,
                    CONF_AGGRESSIVE_MPEGTS: aggressive,
                    CONF_VIDEO_CODEC: codec,
                    CONF_STREAM_SOURCE: source,
                    CONF_STREAM_MODE: mode,
                },
            )

        # No auto-suggest. CP5/CP7 (single-piece doorbells) report a
        # composite serial too — `BE9259083-BE9140879` — but the part before
        # the dash is NOT a real monitor; querying ChimeMusic on it returns
        # 403 and spawns a phantom device in HA. Users with HP7 / HP7
        # bifamigliare can type the real monitor serial(s) here manually.
        current = self.config_entry.options.get(
            CONF_MONITOR_SERIAL,
            self.config_entry.data.get(CONF_MONITOR_SERIAL, ""),
        )
        if isinstance(current, (list, tuple)):
            current = ", ".join(str(s) for s in current if s)

        current_port = self.config_entry.options.get(
            CONF_RELAY_PORT,
            self.config_entry.data.get(CONF_RELAY_PORT, 0),
        )
        try:
            current_port_int = int(current_port or 0)
        except (TypeError, ValueError):
            current_port_int = 0

        current_aggressive = bool(
            self.config_entry.options.get(
                CONF_AGGRESSIVE_MPEGTS,
                self.config_entry.data.get(CONF_AGGRESSIVE_MPEGTS, False),
            )
        )

        current_codec = str(
            self.config_entry.options.get(
                CONF_VIDEO_CODEC,
                self.config_entry.data.get(CONF_VIDEO_CODEC, VIDEO_CODEC_AUTO),
            )
            or VIDEO_CODEC_AUTO
        ).lower()
        if current_codec not in VIDEO_CODECS:
            current_codec = VIDEO_CODEC_AUTO

        current_source = str(
            self.config_entry.options.get(
                CONF_STREAM_SOURCE,
                self.config_entry.data.get(CONF_STREAM_SOURCE, STREAM_SOURCE_CLOUD),
            )
            or STREAM_SOURCE_CLOUD
        ).lower()
        if current_source not in STREAM_SOURCES:
            current_source = STREAM_SOURCE_CLOUD

        current_mode = str(
            self.config_entry.options.get(
                CONF_STREAM_MODE,
                self.config_entry.data.get(CONF_STREAM_MODE, STREAM_MODE_AUTO),
            )
            or STREAM_MODE_AUTO
        ).lower()
        if current_mode not in STREAM_MODES:
            current_mode = STREAM_MODE_AUTO

        schema = vol.Schema(
            {
                vol.Optional(CONF_MONITOR_SERIAL, default=current or ""): str,
                vol.Optional(
                    CONF_RELAY_PORT, default=current_port_int
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
                vol.Optional(
                    CONF_AGGRESSIVE_MPEGTS, default=current_aggressive
                ): bool,
                vol.Optional(
                    CONF_VIDEO_CODEC, default=current_codec
                ): vol.In(VIDEO_CODECS),
                vol.Optional(
                    CONF_STREAM_SOURCE, default=current_source
                ): vol.In(STREAM_SOURCES),
                vol.Optional(
                    CONF_STREAM_MODE, default=current_mode
                ): vol.In(STREAM_MODES),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
