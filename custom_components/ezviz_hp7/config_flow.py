"""Config flow for EZVIZ HP7 integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .api import Hp7Api
from .const import DOMAIN, CONF_REGION, CONF_SERIAL

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

    def __init__(self) -> None:
        """Initialize config flow."""
        self._cached_creds: dict[str, Any] | None = None
        self._device_options: dict[str, str] | None = None
        self._serial_to_unique: dict[str, str] | None = None

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

            # Store token for later use
            if api.token:
                user_input["token"] = api.token

            # List available devices
            devices: dict[str, dict[str, Any]] = {}
            if hasattr(api, "list_devices"):
                devices = await self.hass.async_add_executor_job(api.list_devices)
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

        # Build device selection options, filtering short/duplicate serials
        options: dict[str, str] = {}
        serial_to_unique: dict[str, str] = {}

        for serial_key, info in (devices or {}).items():
            # Get device name
            name = (info.get("name") or info.get("device_name") or "Device").strip()

            # Try to get a stable unique ID from API
            api_unique = (
                info.get("device_id")
                or info.get("uuid")
                or info.get("serial_long")
                or info.get("full_serial")
                or None
            )

            # Choose which serial to show user (prefer long serials)
            if _looks_like_long_serial(serial_key):
                shown_serial = serial_key
            else:
                shown_serial = (
                    info.get("serial_long")
                    or info.get("full_serial")
                    or None
                )

            # Skip empty serials to avoid duplicates
            if not shown_serial:
                continue

            # Unique ID: prefer API unique ID, otherwise use shown serial
            unique_id = api_unique or shown_serial

            # Avoid duplicates if multiple records point to same device
            if shown_serial in options or unique_id in serial_to_unique.values():
                continue

            options[shown_serial] = f"{name} ({shown_serial})"
            serial_to_unique[shown_serial] = unique_id

        self._cached_creds = user_input

        if options:
            self._device_options = options
            self._serial_to_unique = serial_to_unique
            return await self.async_step_pick_serial()

        # No devices found, ask user to enter manually
        return await self.async_step_enter_serial()

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
