"""Centralized DeviceInfo factory.

All entities share one DeviceInfo identity per serial. The model label is
detected from the EZVIZ cloud at setup (so HP7 vs CP7 vs newer siblings show
up correctly) and cached in hass.data; entities just read it back.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_MODEL = "HP7"

# Friendly labels for known deviceSubCategory / deviceType prefixes.
# Extend as more siblings appear.
_KNOWN_MODELS = ("HP7", "CP7", "HP4", "DP1", "DP2")


def detect_model(api: Any, serial: str) -> str:
    """Best-effort lookup of the device model label (e.g. "HP7", "CP7").

    Reads the cloud pagelist via the EzvizClient and walks the deviceInfos
    fields. Falls back to DEFAULT_MODEL on any error so setup never blocks
    on model detection.
    """
    try:
        api.ensure_client()
        client = getattr(api, "_client", None)
        if client is None:
            return DEFAULT_MODEL
        info = client.get_device_infos(serial)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Model detection skipped (%s): %s", serial, exc)
        return DEFAULT_MODEL

    device_infos = (info or {}).get("deviceInfos") or {}
    candidates = [
        device_infos.get("deviceSubCategory"),
        device_infos.get("deviceCategory"),
        device_infos.get("deviceType"),
        device_infos.get("model"),
    ]
    for cand in candidates:
        if not cand or not isinstance(cand, str):
            continue
        upper = cand.upper()
        for known in _KNOWN_MODELS:
            if known in upper:
                return known
        # Heuristic: take last alphanumeric token if it looks like a code.
        token = upper.replace("-", " ").split()[-1]
        if 2 <= len(token) <= 8 and any(c.isdigit() for c in token):
            return token
    return DEFAULT_MODEL


def make_device_info(serial: str, model: Optional[str]) -> DeviceInfo:
    """Build the shared DeviceInfo for this device serial."""
    label = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return DeviceInfo(
        identifiers={(DOMAIN, serial)},
        name=f"EZVIZ {label} ({serial})",
        manufacturer="EZVIZ",
        model=label,
    )


def get_model(hass, entry_id: str) -> str:
    """Return the cached model for an entry, or DEFAULT_MODEL."""
    data = hass.data.get(DOMAIN, {}).get(entry_id) or {}
    return data.get("model") or DEFAULT_MODEL
