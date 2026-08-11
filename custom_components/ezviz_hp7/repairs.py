"""Repair flows for the EZVIZ HP7 integration.

The only fixable issue so far is the encrypted-stream one (#47). On firmware
that keeps Image/Video Encryption on with no toggle in the app, the cloud
refuses to hand the decryption key to an un-elevated session and answers
20002 — and that refusal is itself what makes EZVIZ e-mail a 4-digit code
(subject "[Device Encryption] Security Code"). The code dies after about 30
minutes, so the user needs a form in front of them, not a log line.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from .const import CONF_ENCRYPTION_KEY, DOMAIN

_LOGGER = logging.getLogger(__name__)


class EncryptionOtpRepairFlow(RepairsFlow):
    """Ask for the code EZVIZ mailed, then store the key it unlocks."""

    def __init__(self, serial: str) -> None:
        """Remember which doorbell this flow is fixing."""
        self._serial = serial

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        """Entry point — go straight to the code form."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        """Take the code, exchange it for the key, store it in the options.

        An empty code is not an error: it means the previous one expired, so
        we ask the cloud again (which sends a fresh mail) and re-show the
        form.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            code = str(user_input.get("code", "")).strip()
            entry, api = _resolve_entry(self.hass, self._serial)
            if api is None or entry is None:
                return self.async_abort(reason="no_device")

            if not code:
                # Re-trigger the 20002 so EZVIZ mails a fresh code.
                await self.hass.async_add_executor_job(
                    api.get_camera_encryption_key, self._serial
                )
                errors["code"] = "resent"
            else:
                try:
                    key = await self.hass.async_add_executor_job(
                        api.fetch_camera_key_with_code, self._serial, code
                    )
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning(
                        "EZVIZ HP7: key exchange failed for %s: %s",
                        self._serial, exc,
                    )
                    key = None
                if key:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        options={**entry.options, CONF_ENCRYPTION_KEY: key},
                    )
                    _LOGGER.warning(
                        "EZVIZ HP7: stored the camera encryption key for %s "
                        "— the entry will reload and decrypt the stream.",
                        self._serial,
                    )
                    return self.async_create_entry(title="", data={})
                errors["code"] = "invalid_code"

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Optional("code", default=""): str}),
            errors=errors,
            description_placeholders={"serial": self._serial},
        )


def _resolve_entry(hass: HomeAssistant, serial: str) -> tuple[Any, Any]:
    """Find the config entry and API client that own this serial."""
    for entry_id, data in hass.data.get(DOMAIN, {}).items():
        if data.get("serial") != serial:
            continue
        return hass.config_entries.async_get_entry(entry_id), data.get("api")
    return None, None


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Build the flow for a fixable issue."""
    serial = str((data or {}).get("serial") or "")
    if not serial and issue_id.startswith("encryption_otp_"):
        serial = issue_id[len("encryption_otp_"):]
    return EncryptionOtpRepairFlow(serial)
