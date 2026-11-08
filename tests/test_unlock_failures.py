"""Unlock failures must be visible: logged with a reason, and raised to the caller (#59, #63)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.ezviz_hp7.api import (
    DEFAULT_DOOR_LOCK_NO,
    DEFAULT_GATE_LOCK_NO,
    Hp7Api,
)
from custom_components.ezviz_hp7.button import EzvizHp7Button

SERIAL = "BW0000000-BW0000001"
COMPONENT = Path(__file__).parents[1] / "custom_components" / "ezviz_hp7"
_LOGGED_IN = object()


def _api(token: dict | None | object = _LOGGED_IN) -> Hp7Api:
    if token is _LOGGED_IN:
        token = {"username": "user-id"}
    api = Hp7Api(username="someone@example.com", password="secret", token=token)
    api._client = MagicMock()
    return api


def _skipped(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "Remote unlock skipped" in r.getMessage()]


@pytest.mark.parametrize("method", ["unlock_gate", "unlock_door"])
def test_unlock_without_token_logs_why_once_and_skips_the_cloud(caplog, method):
    """The state #59/#63 left behind: a client, but no token, and logins still failing."""
    api = _api(token=None)
    # Upstream PR #80 retries the login for a tokenless client; keep it failing.
    api._client.login.side_effect = ValueError("cloud unreachable")

    with caplog.at_level(logging.WARNING):
        assert getattr(api, method)(SERIAL) is False

    assert len(_skipped(caplog)) == 1  # not once per lock number tried
    assert "no EZVIZ cloud token" in _skipped(caplog)[0]
    api._client.remote_unlock.assert_not_called()


def test_unlock_when_the_client_cannot_be_created_logs_and_returns_false(caplog, monkeypatch):
    api = _api(token=None)
    api._client = None

    def fail():
        raise RuntimeError("Failed to initialize EZVIZ client: DNS failure")

    monkeypatch.setattr(api, "ensure_client", fail)
    with caplog.at_level(logging.WARNING):
        assert api.unlock_door(SERIAL) is False

    assert len(_skipped(caplog)) == 1
    assert "no EZVIZ cloud client" in caplog.text
    assert "DNS failure" in caplog.text


def test_unlock_returns_false_if_the_client_is_dropped_mid_press(monkeypatch):
    """force_relogin on the coordinator can clear the client after the session check."""
    api = _api()
    calls = 0

    def ensure_client():
        nonlocal calls
        calls += 1
        if calls > 1:  # the session check passed; the next call finds no client
            raise RuntimeError("Failed to initialize EZVIZ client: relogin in progress")

    monkeypatch.setattr(api, "ensure_client", ensure_client)

    assert api.unlock_gate(SERIAL) is False
    api._client.remote_unlock.assert_not_called()


@pytest.mark.parametrize(
    ("method", "first", "second"),
    [
        ("unlock_gate", DEFAULT_GATE_LOCK_NO, DEFAULT_DOOR_LOCK_NO),
        ("unlock_door", DEFAULT_DOOR_LOCK_NO, DEFAULT_GATE_LOCK_NO),
    ],
)
def test_unlock_falls_back_to_the_other_lock_number(method, first, second):
    """Existing behaviour, kept: the second lock number is tried if the first fails."""
    api = _api()
    api._client.remote_unlock.side_effect = [ValueError("wrong lock"), None]

    assert getattr(api, method)(SERIAL) is True

    assert [c.args[2] for c in api._client.remote_unlock.call_args_list] == [first, second]


def test_unlock_success_calls_the_cloud_with_the_token_user():
    api = _api()

    assert api._try_unlock(SERIAL, DEFAULT_GATE_LOCK_NO) is True

    api._client.remote_unlock.assert_called_once_with(SERIAL, "user-id", DEFAULT_GATE_LOCK_NO)


def test_unlock_cloud_error_returns_false(caplog):
    api = _api()
    api._client.remote_unlock.side_effect = ValueError("device offline")

    with caplog.at_level(logging.WARNING):
        assert api._try_unlock(SERIAL, DEFAULT_GATE_LOCK_NO) is False

    assert "Remote unlock failed" in caplog.text


@pytest.mark.parametrize(
    ("action", "api_method"),
    [("unlock_gate", "unlock_gate"), ("unlock_door", "unlock_door")],
)
async def test_failed_press_raises_a_translated_error(hass, action, api_method):
    api = MagicMock()
    getattr(api, api_method).return_value = False
    button = EzvizHp7Button(hass, api, SERIAL, "HP7", action)

    with pytest.raises(HomeAssistantError) as err:
        await button.async_press()

    assert err.value.translation_domain == "ezviz_hp7"
    assert err.value.translation_key == f"{action}_failed"


@pytest.mark.parametrize("action", ["unlock_gate", "unlock_door"])
async def test_successful_press_does_not_raise(hass, action):
    api = MagicMock()
    getattr(api, action).return_value = True
    button = EzvizHp7Button(hass, api, SERIAL, "HP7", action)

    await button.async_press()

    getattr(api, action).assert_called_once_with(SERIAL)


@pytest.mark.parametrize(
    "path",
    ["strings.json", *[f"translations/{lang}.json" for lang in ("en", "it", "es", "fr", "pl")]],
)
def test_every_language_has_the_unlock_error_messages(path):
    exceptions = json.loads((COMPONENT / path).read_text(encoding="utf-8"))["exceptions"]

    for key in ("unlock_gate_failed", "unlock_door_failed"):
        assert exceptions[key]["message"].strip()
