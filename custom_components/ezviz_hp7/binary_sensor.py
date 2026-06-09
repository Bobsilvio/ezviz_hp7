"""EZVIZ HP7 binary sensor entities."""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.helpers.event import CALLBACK_TYPE
    from .coordinator import Hp7Coordinator

_LOGGER = logging.getLogger(__name__)

ALARM_FIELD = "alarm_name"
ALARM_TIME_FIELD = "last_alarm_time"
PULSE_SECONDS = 3

# Simple binary sensors mapped directly to coordinator data keys
SIMPLE_MAP: list[tuple[str, str, BinarySensorDeviceClass]] = [
    ("motion", "motion_trigger", BinarySensorDeviceClass.MOTION),
]

# Alarm sensors that trigger for PULSE_SECONDS when specific alarm names appear
ALARM_MAP: list[tuple[list[str], str, str, BinarySensorDeviceClass | None, str]] = [
    (
        ["Smart Detection Alarm"],
        "smart_detection_alarm",
        None,
        "mdi:run",
    ),
    (
        ["Intelligent Detection Alarm"],
        "intelligent_detection_alarm",
        None,
        "mdi:account-search",
    ),
    (
        ["Your doorbell is ringing"],
        "doorbell_ringing",
        None,
        "mdi:doorbell",
    ),
    (
        ["EZVIZ app open the gate", "Monitor open the gate"],
        "gate_open",
        None,
        "mdi:gate-open",
    ),
    (
        ["EZVIZ app unlock the lock", "Monitor unlock the lock"],
        "unlock_lock",
        None,
        "mdi:lock-open-variant",
    ),
    # HP7 Pro: tap-to-unlock variants. Strings observed in app event log;
    # different firmwares may localise them — _matches_any() compares case-
    # insensitively below.
    (
        ["RFID card unlock", "Unlock with RFID", "Card unlock"],
        "unlock_rfid",
        None,
        "mdi:card-account-details",
    ),
    (
        ["Face recognition unlock", "Face unlock", "Unlock with face"],
        "unlock_face",
        None,
        "mdi:face-recognition",
    ),
    (
        ["Palm vein unlock", "Palm unlock", "Unlock with palm"],
        "unlock_palm",
        None,
        "mdi:hand-front-right",
    ),
    (
        ["Passcode unlock", "Code unlock", "Password unlock"],
        "unlock_code",
        None,
        "mdi:dialpad",
    ),
    (
        ["EZVIZ app unlock", "App unlock"],
        "unlock_app",
        None,
        "mdi:cellphone",
    ),
]


# HA event fired on every recognised unlock. Drives automations that want
# the unlock category and the underlying alarm name.
HP7_UNLOCK_EVENT = "ezviz_hp7_unlock"


def _resolve_card_name(
    keys: list[dict[str, Any]], card_id: str | None
) -> str | None:
    """Best-effort: map card_id to the user-assigned keyName.

    If card_id matches a keyID in the list, return the matching keyName.
    Otherwise, if exactly one key is enabled, assume that one (works for
    households with a single RFID card).
    """
    enabled = [
        k for k in keys
        if isinstance(k, dict) and k.get("enabled") in (1, "1", True)
    ]
    if card_id is not None:
        for k in keys:
            kid = k.get("keyID")
            if str(kid) == str(card_id) and isinstance(k.get("keyName"), str):
                return k["keyName"]
    if len(enabled) == 1:
        name = enabled[0].get("keyName")
        if isinstance(name, str) and name.strip():
            return name
    return None


def _extract_card_id(detail: Any) -> str | None:
    """Best-effort hunt for a card identifier inside an EZVIZ alarm record.

    The HP7 Pro EZVIZ payload for RFID unlocks tends to put the card number
    in one of a handful of fields; firmware revisions move it around. Try
    common names (and `relationInfo`, which is sometimes a JSON-encoded
    string) and return the first non-empty match.
    """
    if not isinstance(detail, dict):
        return None

    candidates = (
        "cardNo", "cardID", "cardId", "rfidCardNo", "rfidId",
        "userNo", "tagId", "doorlockCardNo",
    )
    for key in candidates:
        val = detail.get(key)
        if isinstance(val, (str, int)) and str(val).strip() not in ("", "0"):
            return str(val).strip()

    # `relationInfo` / `recExtraInfo` are typically JSON strings.
    for key in ("relationInfo", "recExtraInfo", "remark", "alarmExtInfo"):
        raw = detail.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            import json as _json
            parsed = _json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            for k in candidates:
                v = parsed.get(k)
                if isinstance(v, (str, int)) and str(v).strip() not in ("", "0"):
                    return str(v).strip()
    return None

# Translation keys recognised as unlock categories.
_UNLOCK_KEYS = {
    "unlock_lock",
    "unlock_rfid",
    "unlock_face",
    "unlock_palm",
    "unlock_code",
    "unlock_app",
    "gate_open",
}


def _to_bool(value: Any) -> bool:
    """Convert various types to boolean.
    
    Args:
        value: Value to convert.
        
    Returns:
        Boolean representation of the value.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes", "y")
    return False


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up EZVIZ HP7 binary sensor entities.
    
    Args:
        hass: Home Assistant instance.
        entry: Config entry.
        async_add_entities: Callback to add entities.
    """
    data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    coordinator: Hp7Coordinator = data["coordinator"]
    serial: str = data["serial"]
    model: str = data.get("model") or "HP7"

    entities: list[BinarySensorEntity] = []

    for key, translation_key, device_class in SIMPLE_MAP:
        entities.append(
            Hp7BinarySimple(coordinator, serial, model, key, translation_key, device_class)
        )

    for match_values, translation_key, device_class, icon in ALARM_MAP:
        entities.append(
            Hp7BinaryAlarm(
                coordinator,
                serial,
                model,
                match_values,
                translation_key,
                device_class,
                icon,
            )
        )

    async_add_entities(entities)


class Hp7BinarySimple(CoordinatorEntity, BinarySensorEntity):
    """Simple binary sensor that directly maps to coordinator data."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: Hp7Coordinator,
        serial: str,
        model: str,
        key: str,
        translation_key: str,
        device_class: BinarySensorDeviceClass,
    ) -> None:
        """Initialize binary sensor entity.

        Args:
            coordinator: Data coordinator.
            serial: Device serial number.
            model: Device model label (HP7 / CP7 / ...).
            key: Key in coordinator data.
            translation_key: i18n translation key.
            device_class: Device class for sensor.
        """
        super().__init__(coordinator)
        self._serial = serial
        self._model = model
        self._key = key
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{serial}_binary_{key}"
        self._attr_device_class = device_class

    @property
    def is_on(self) -> bool:
        """Return True if sensor is on."""
        data = self.coordinator.data or {}
        val = data.get(self._key)
        return _to_bool(val)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        from .device_info import make_device_info
        return make_device_info(self._serial, self._model)


class Hp7BinaryAlarm(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor that pulses briefly when alarm is triggered.
    
    This sensor stays ON for PULSE_SECONDS after detecting a matching alarm,
    then returns to OFF. This is useful for automations that react to events.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: Hp7Coordinator,
        serial: str,
        model: str,
        match_values: list[str],
        translation_key: str,
        device_class: BinarySensorDeviceClass | None,
        icon: str,
    ) -> None:
        """Initialize alarm binary sensor entity.

        Args:
            coordinator: Data coordinator.
            serial: Device serial number.
            model: Device model label (HP7 / CP7 / ...).
            match_values: List of alarm names to trigger on.
            translation_key: i18n translation key.
            device_class: Device class for sensor.
            icon: Icon to display.
        """
        super().__init__(coordinator)
        self._serial = serial
        self._model = model
        self._match_values = match_values
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{serial}_alarm_{translation_key}"
        self._attr_device_class = device_class
        self._attr_icon = icon
        self._last_trigger: dt_util.datetime | None = None
        self._prev_alarm_time: str | None = None
        self._off_unsub: CALLBACK_TYPE | None = None

    @property
    def is_on(self) -> bool:
        """Return True if recently triggered (within PULSE_SECONDS)."""
        if self._last_trigger is None:
            return False
        delta = (dt_util.utcnow() - self._last_trigger).total_seconds()
        return delta < PULSE_SECONDS

    def _schedule_state_update(self) -> None:
        """Schedule state update to turn off after PULSE_SECONDS."""
        if self._off_unsub:
            self._off_unsub()

        def _cb(_now: dt_util.datetime) -> None:
            self._off_unsub = None
            self.hass.add_job(self.async_write_ha_state)

        self._off_unsub = async_call_later(self.hass, PULSE_SECONDS, _cb)

    def _alarm_matches(self, current_alarm: Any) -> bool:
        """Case-insensitive substring match against the configured names.

        EZVIZ alarm strings vary between firmwares and translations; an exact
        match misses too many real events. Treat the configured names as
        substrings, lowercase on both sides.
        """
        if not isinstance(current_alarm, str):
            return False
        cur = current_alarm.lower()
        for name in self._match_values:
            if not isinstance(name, str):
                continue
            if name.lower() in cur:
                return True
        return False

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle coordinator data update event.

        Detects new alarms, pulses the sensor, and fires the
        ``ezviz_hp7_unlock`` HA event for unlock-category alarms so users
        can react to RFID / face / palm / code / app unlocks in automations.
        """
        data = self.coordinator.data or {}
        current_alarm = data.get(ALARM_FIELD)
        current_alarm_time = data.get(ALARM_TIME_FIELD)

        if (
            self._alarm_matches(current_alarm)
            and current_alarm_time is not None
            and current_alarm_time != self._prev_alarm_time
        ):
            self._prev_alarm_time = current_alarm_time
            self._last_trigger = dt_util.utcnow()
            self._schedule_state_update()
            _LOGGER.debug(
                "Alarm triggered for %s: %s (%s)",
                self._attr_translation_key,
                current_alarm,
                self._serial,
            )
            # Pre-warm the live stream when something interesting happens at
            # the door — covers the canonical "ring → open the dashboard"
            # workflow with a sub-second first frame instead of paying the
            # cold VTM handshake.
            if (
                self._attr_translation_key in ("doorbell_ringing",)
                or self._attr_translation_key in _UNLOCK_KEYS
            ) and self.hass is not None:
                relay = (
                    self.hass.data.get(DOMAIN, {})
                    .get(self._entry_id_for_relay(), {})
                    .get("live_relay")
                    if hasattr(self, "_entry_id_for_relay")
                    else None
                )
                # Simpler: scan all entries for one carrying our serial.
                if relay is None:
                    for data in (self.hass.data.get(DOMAIN, {}) or {}).values():
                        if (
                            isinstance(data, dict)
                            and data.get("serial") == self._serial
                            and data.get("live_relay") is not None
                        ):
                            relay = data["live_relay"]
                            break
                if relay is not None:
                    self.hass.async_create_task(relay.prewarm())
            # Fire HA event for unlock categories so automations can branch
            # on the kind of unlock without polling state.
            if self._attr_translation_key in _UNLOCK_KEYS and self.hass is not None:
                detail = data.get("latest_alarm_detail") if isinstance(data, dict) else None
                keys = data.get("keys") if isinstance(data, dict) else None
                card_id = _extract_card_id(detail) if detail else None
                payload: dict[str, Any] = {
                    "category": self._attr_translation_key,
                    "alarm_name": current_alarm,
                    "alarm_time": current_alarm_time,
                    "serial": self._serial,
                }
                if detail is not None:
                    payload["details"] = detail
                if card_id is not None:
                    payload["card_id"] = card_id
                if isinstance(keys, list) and keys:
                    payload["keys"] = keys
                    # Heuristic: if card_id is known and matches a keyID
                    # in the list, surface card_name. Otherwise if there
                    # is exactly one enabled key, assume that one.
                    card_name = _resolve_card_name(keys, card_id)
                    if card_name:
                        payload["card_name"] = card_name
                self.hass.bus.async_fire(HP7_UNLOCK_EVENT, payload)

        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        from .device_info import make_device_info
        return make_device_info(self._serial, self._model)
