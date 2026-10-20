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


DEVICE_CATEGORY_CATEYE = "CatEye"


def is_cateye(category: str | None) -> bool:
    """True for EZVIZ peephole viewers (deviceCategory "CatEye", e.g. CP2).

    Battery cameras with a lens and an indoor screen: no doorbell button,
    lock relays, chime or PIR. They get their own entity set (no doorbell
    entities), cloud-only polling and a live view that only opens a cloud
    session while someone is watching.
    """
    return bool(category) and str(category).strip().lower() == "cateye"


def _model_from_device_infos(device_infos: dict[str, Any]) -> str:
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


def detect_model_and_category(api: Any, serial: str) -> tuple[str, str | None]:
    """Best-effort lookup of the model label and the cloud deviceCategory.

    Reads the cloud pagelist via the EzvizClient. Falls back to
    (DEFAULT_MODEL, None) on any error so setup never blocks on detection.
    """
    try:
        api.ensure_client()
        client = getattr(api, "_client", None)
        if client is None:
            return DEFAULT_MODEL, None
        info = client.get_device_infos(serial)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Model detection skipped (%s): %s", serial, exc)
        return DEFAULT_MODEL, None

    device_infos = (info or {}).get("deviceInfos") or {}
    category = device_infos.get("deviceCategory")
    return (
        _model_from_device_infos(device_infos),
        category if isinstance(category, str) else None,
    )


def detect_model(api: Any, serial: str) -> str:
    """Best-effort lookup of the device model label (e.g. "HP7", "CP7")."""
    return detect_model_and_category(api, serial)[0]


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
