"""Helper utilities for EZVIZ HP7 integration."""
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

if TYPE_CHECKING:
    pass


def get_device_info(serial: str) -> DeviceInfo:
    """Get DeviceInfo for EZVIZ HP7 device.
    
    Args:
        serial: Device serial number.
        
    Returns:
        DeviceInfo object with device identifiers and metadata.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, serial)},
        name=f"EZVIZ HP7 ({serial})",
        manufacturer="EZVIZ",
        model="HP7",
    )
