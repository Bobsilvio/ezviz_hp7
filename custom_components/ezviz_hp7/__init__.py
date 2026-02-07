"""EZVIZ HP7 integration for Home Assistant."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, PLATFORMS
from .api import Hp7Api
from .coordinator import Hp7Coordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EZVIZ HP7 from a config entry.
    
    Args:
        hass: Home Assistant instance.
        entry: Config entry with credentials and device info.
        
    Returns:
        True if setup was successful, False otherwise.
        
    Raises:
        ConfigEntryNotReady: If API is not reachable.
    """
    username: str = entry.data["username"]
    password: str = entry.data["password"]
    region: str = entry.data["region"]
    serial: str = entry.data["serial"]
    token: dict[str, Any] | None = entry.data.get("token")

    try:
        api = Hp7Api(username, password, region, token=token)
        await hass.async_add_executor_job(api.login)
        await hass.async_add_executor_job(api.detect_capabilities, serial)
    except Exception as exc:
        _LOGGER.error("Failed to connect to EZVIZ HP7 API: %s", exc)
        raise ConfigEntryNotReady(f"Cannot connect to EZVIZ HP7: {exc}") from exc

    coordinator = Hp7Coordinator(hass, api, serial)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as exc:
        _LOGGER.error("Failed to fetch initial data from coordinator: %s", exc)
        raise ConfigEntryNotReady(f"Failed to fetch EZVIZ HP7 data: {exc}") from exc

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "serial": serial,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.
    
    Args:
        hass: Home Assistant instance.
        entry: Config entry to unload.
        
    Returns:
        True if unload was successful.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, {})
        api: Hp7Api | None = data.get("api")
        if api:
            api.close()
        _LOGGER.debug("EZVIZ HP7 integration unloaded for entry %s", entry.entry_id)
    
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry.
    
    Args:
        hass: Home Assistant instance.
        entry: Config entry to reload.
    """
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
