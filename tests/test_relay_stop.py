"""Hp7StreamRelay.stop() must not wait for clients to hang up by themselves (#60)."""
from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import MagicMock

import pytest

from custom_components.ezviz_hp7 import live_camera
from custom_components.ezviz_hp7.live_camera import Hp7StreamRelay

# The relay is a real TCP server; these tests connect to it over loopback.
pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.fixture(autouse=True)
def _short_grace(monkeypatch):
    """Clients that ignore the socket close would otherwise sit out the real 3 s grace."""
    monkeypatch.setattr(live_camera, "RELAY_CLOSE_GRACE", 0.1)


def _relay() -> Hp7StreamRelay:
    return Hp7StreamRelay(api=MagicMock(), serial="BW0000000-BW0000001")


async def _connect(relay: Hp7StreamRelay):
    return await asyncio.open_connection("127.0.0.1", relay._port)


async def _wait_for_clients(relay: Hp7StreamRelay, count: int) -> None:
    for _ in range(100):
        if len(relay._client_tasks) == count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {count} tracked clients, got {len(relay._client_tasks)}")


@pytest.mark.asyncio
async def test_stop_closes_a_client_that_never_disconnects(monkeypatch):
    """A long-lived consumer (HA stream worker, go2rtc, Frigate) kept unload hanging."""
    serving_forever = asyncio.Event()

    async def serve_forever(self, reader, writer):
        serving_forever.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Hp7StreamRelay, "_serve_client", serve_forever)
    relay = _relay()
    await relay.start()
    reader, writer = await _connect(relay)
    await asyncio.wait_for(serving_forever.wait(), 2)
    await _wait_for_clients(relay, 1)

    await asyncio.wait_for(relay.stop(), 5)

    assert relay._client_tasks == set()
    assert relay._client_writers == set()
    assert await asyncio.wait_for(reader.read(), 2) == b""  # the client saw EOF
    writer.close()


@pytest.mark.asyncio
async def test_stop_closes_every_client(monkeypatch):
    async def serve_forever(self, reader, writer):
        await asyncio.Event().wait()

    monkeypatch.setattr(Hp7StreamRelay, "_serve_client", serve_forever)
    relay = _relay()
    await relay.start()
    clients = [await _connect(relay) for _ in range(3)]
    await _wait_for_clients(relay, 3)

    await asyncio.wait_for(relay.stop(), 5)

    assert relay._client_tasks == set()
    for reader, writer in clients:
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()


@pytest.mark.asyncio
async def test_stop_gives_up_on_a_client_that_ignores_cancellation(monkeypatch, caplog):
    """A wedged client must not hold the unload hostage: stop() logs and moves on."""
    release = asyncio.Event()

    async def ignore_cancellation(self, reader, writer):
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setattr(Hp7StreamRelay, "_serve_client", ignore_cancellation)
    monkeypatch.setattr(live_camera, "RELAY_STOP_TIMEOUT", 0.2)
    relay = _relay()
    await relay.start()
    _reader, writer = await _connect(relay)
    await _wait_for_clients(relay, 1)

    with caplog.at_level(logging.WARNING):
        await asyncio.wait_for(relay.stop(), 5)

    assert "did not finish within" in caplog.text
    release.set()
    await _wait_for_clients(relay, 0)
    writer.close()


@pytest.mark.asyncio
async def test_finished_clients_are_untracked(monkeypatch):
    async def serve_once(self, reader, writer):
        writer.close()

    monkeypatch.setattr(Hp7StreamRelay, "_serve_client", serve_once)
    relay = _relay()
    await relay.start()
    reader, writer = await _connect(relay)
    assert await asyncio.wait_for(reader.read(), 2) == b""
    await _wait_for_clients(relay, 0)
    assert relay._client_writers == set()
    writer.close()
    await relay.stop()


@pytest.mark.asyncio
async def test_a_client_that_ends_on_socket_close_is_not_cancelled(monkeypatch):
    """Closing the socket lets the client run its own cleanup; cancel is the fallback."""
    cancelled = False
    finished = asyncio.Event()

    async def serve_until_eof(self, reader, writer):
        nonlocal cancelled
        try:
            await reader.read()  # returns b"" once stop() closes the socket
            finished.set()
        except asyncio.CancelledError:
            cancelled = True
            raise

    monkeypatch.setattr(Hp7StreamRelay, "_serve_client", serve_until_eof)
    relay = _relay()
    await relay.start()
    _reader, writer = await _connect(relay)
    await _wait_for_clients(relay, 1)

    await asyncio.wait_for(relay.stop(), 5)

    assert finished.is_set()
    assert not cancelled
    writer.close()


@pytest.mark.asyncio
async def test_a_connection_accepted_during_stop_is_turned_away(monkeypatch):
    """A client accepted just before the listener closed must not open a new session."""

    async def must_not_serve(self, reader, writer):
        raise AssertionError("a client was served after stop() began")

    monkeypatch.setattr(Hp7StreamRelay, "_serve_client", must_not_serve)
    relay = _relay()
    relay._stopping = True
    writer = MagicMock()

    await relay._handle_client(MagicMock(), writer)

    writer.close.assert_called_once()
    assert relay._client_tasks == set()


@pytest.mark.asyncio
async def test_a_cancelled_stop_still_tears_down_the_shared_session(monkeypatch):
    """If HA cancels the unload mid-stop, the session must not be left running."""
    async def serve_forever(self, reader, writer):
        await asyncio.Event().wait()

    monkeypatch.setattr(Hp7StreamRelay, "_serve_client", serve_forever)
    monkeypatch.setattr(live_camera, "RELAY_CLOSE_GRACE", 5)  # stop() parks here
    relay = _relay()
    shutdowns = []
    original = relay._shutdown_shared

    async def recording_shutdown():
        shutdowns.append(True)
        await original()

    monkeypatch.setattr(relay, "_shutdown_shared", recording_shutdown)
    await relay.start()
    vtm = MagicMock()
    relay._shared_vtm = vtm
    _reader, writer = await _connect(relay)
    await _wait_for_clients(relay, 1)

    stop = asyncio.create_task(relay.stop())
    await asyncio.sleep(0.1)
    stop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop

    assert shutdowns == [True]
    vtm.close.assert_called_once()
    assert relay._shared_vtm is None
    # The stub ignores its closed socket, so it outlives the cancelled stop.
    for task in list(relay._client_tasks):
        task.cancel()
    await asyncio.gather(*relay._client_tasks, return_exceptions=True)
    writer.close()


@pytest.mark.asyncio
async def test_prewarm_after_stop_opens_no_session():
    """Doorbell events can call prewarm() after unload; it must not open a session."""
    relay = _relay()
    relay._stopping = True

    await relay.prewarm()

    relay._api.ensure_client.assert_not_called()
    assert relay._shared_vtm is None


@pytest.mark.asyncio
async def test_a_session_opened_while_stop_began_closes_itself(monkeypatch):
    relay = _relay()
    vtm = MagicMock()

    def start_then_stop_begins():
        relay._stopping = True  # stop() started during the handshake
        return MagicMock(streamssn="ssn")

    vtm.start.side_effect = start_then_stop_begins

    async def open_source(loop, client):
        return vtm

    monkeypatch.setattr(relay, "_open_source", open_source)

    await relay.prewarm()

    vtm.close.assert_called_once()
    assert relay._shared_vtm is None
    assert relay._shared_reader is None


@pytest.mark.asyncio
async def test_stop_does_not_wait_on_a_session_that_is_still_opening(monkeypatch):
    """prewarm() holds the lock across an unbounded handshake; stop() must not block on it."""
    monkeypatch.setattr(live_camera, "RELAY_SESSION_CLOSE_TIMEOUT", 0.2)
    relay = _relay()
    await relay._shared_lock.acquire()  # a handshake in progress
    relay._stopping = True

    started = time.monotonic()
    await asyncio.wait_for(relay._shutdown_shared(), 5)

    assert time.monotonic() - started < 1
    relay._shared_lock.release()


def test_no_idle_timer_is_armed_while_stopping():
    relay = _relay()
    relay._stopping = True

    relay._arm_idle_timer()

    assert relay._idle_handle is None


@pytest.mark.asyncio
async def test_a_slow_session_close_does_not_hold_up_stop(monkeypatch, caplog):
    monkeypatch.setattr(live_camera, "RELAY_SESSION_CLOSE_TIMEOUT", 0.2)
    relay = _relay()
    await relay.start()
    stuck_vtm = MagicMock()
    stuck_vtm.close.side_effect = lambda: time.sleep(1.5)
    relay._shared_vtm = stuck_vtm

    started = time.monotonic()
    with caplog.at_level(logging.WARNING):
        await asyncio.wait_for(relay.stop(), 5)

    assert time.monotonic() - started < 1.2
    assert "shared session close took over" in caplog.text


@pytest.mark.asyncio
async def test_start_after_stop_serves_again(monkeypatch):
    async def serve_once(self, reader, writer):
        writer.write(b"ok")
        await writer.drain()
        writer.close()

    monkeypatch.setattr(Hp7StreamRelay, "_serve_client", serve_once)
    relay = _relay()
    await relay.start()
    await relay.stop()
    await relay.start()  # clears the stopping flag

    reader, writer = await _connect(relay)
    assert await asyncio.wait_for(reader.read(), 2) == b"ok"
    writer.close()
    await relay.stop()


@pytest.mark.asyncio
async def test_stop_without_clients_and_twice():
    relay = _relay()
    await relay.start()
    await asyncio.wait_for(relay.stop(), 5)
    await asyncio.wait_for(relay.stop(), 5)  # second stop is a no-op
    assert relay._server is None
