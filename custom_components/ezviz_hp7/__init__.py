"""EZVIZ HP7 integration for Home Assistant."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, PLATFORMS, CONF_MONITOR_SERIAL, CONF_RELAY_PORT
from .api import Hp7Api
from .coordinator import Hp7Coordinator
from .device_info import DEFAULT_MODEL, detect_model

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
    monitor_serial_raw = entry.options.get(
        CONF_MONITOR_SERIAL, entry.data.get(CONF_MONITOR_SERIAL)
    )
    # Accept legacy single-string OR comma-separated list (HP7 bifamigliare =
    # 1 camera + 2 monitors in two separate apartments).
    monitor_serials: list[str] = []
    if isinstance(monitor_serial_raw, str):
        for chunk in monitor_serial_raw.split(","):
            chunk = chunk.strip()
            if chunk:
                monitor_serials.append(chunk)
    elif isinstance(monitor_serial_raw, (list, tuple)):
        for chunk in monitor_serial_raw:
            if isinstance(chunk, str) and chunk.strip():
                monitor_serials.append(chunk.strip())
    monitor_serial = monitor_serials or None
    # Live-relay fixed TCP port (0 = pick a free one at start). Lets external
    # consumers (go2rtc, mediamtx, Frigate) keep a stable URL across HA
    # restarts.
    try:
        relay_port = int(
            entry.options.get(
                CONF_RELAY_PORT, entry.data.get(CONF_RELAY_PORT, 0)
            )
            or 0
        )
    except (TypeError, ValueError):
        relay_port = 0
    if relay_port < 0 or relay_port > 65535:
        relay_port = 0

    try:
        api = Hp7Api(username, password, region, token=token)
        await hass.async_add_executor_job(api.login)
        await hass.async_add_executor_job(api.detect_capabilities, serial)
    except Exception as exc:
        _LOGGER.error("Failed to connect to EZVIZ HP7 API: %s", exc)
        raise ConfigEntryNotReady(f"Cannot connect to EZVIZ HP7: {exc}") from exc

    # Detect device model (HP7 / CP7 / ...) from the cloud so DeviceInfo
    # shows the right label. Falls back to DEFAULT_MODEL on any error.
    try:
        model = await hass.async_add_executor_job(detect_model, api, serial)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Model detection failed (%s): %s", serial, exc)
        model = DEFAULT_MODEL

    coordinator = Hp7Coordinator(
        hass, api, serial, monitor_serial, config_entry=entry
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as exc:
        _LOGGER.error("Failed to fetch initial data from coordinator: %s", exc)
        raise ConfigEntryNotReady(f"Failed to fetch EZVIZ HP7 data: {exc}") from exc

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "serial": serial,
        "monitor_serial": monitor_serial,
        "model": model,
        "relay_port": relay_port,
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
    # Stop the per-entry live stream relay (if any) before tearing down platforms.
    try:
        from .live_camera import async_unload_live_entities

        await async_unload_live_entities(hass, entry)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Live camera teardown ignored: %s", exc)

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
