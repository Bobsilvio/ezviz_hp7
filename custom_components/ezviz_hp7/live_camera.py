"""Live video camera entity for the HP7 / CP7 — VTM cloud relay path.

The HP7 doesn't speak the Hik-Connect UDP P2P transport that Pedro's
go2rtc fork uses (his test hardware was a server-grade 4K NVR with a
keep-alive UDP P2P session; consumer doorbells dropped behind a NAT do
not register on the P2P cloud at all). Live-validated 2026-06-06 by
Bobsilvio: every P2P_SETUP response on every advertised P2P server came
back as a bare ClientID ack with no 0xFF sub-TLV — the cloud simply
cannot route a P2P_SETUP to an HP7.

The official EZVIZ app streams the HP7 through the **VTM relay**: a TCP
ysproto session that delivers MPEG-PS (H.264 video + MP2 audio). Renier's
pyEzvizApi already implements this pipeline; this module wires
``pylocalapi.cloud_stream.open_cloud_stream`` into a per-entry TCP relay
that Home Assistant's Stream component can consume.

Architecture per viewing session:

    HP7  →  VTM cloud (ysproto://...:8554/live)
        |
        v  VtmStreamClient.iter_payloads()  (sync iterator)
    pylocalapi (executor thread)
        |
        v  MPEG-PS bytes  ──>  queue.Queue
    asyncio drain task
        |
        v  ffmpeg stdin (-f mpeg -i pipe:0 -c:v copy -c:a aac -f mpegts pipe:1)
    ffmpeg subprocess
        |
        v  MPEG-TS bytes  ──>  127.0.0.1:<port> TCP socket
        v
    HA Stream component / HLS / WebRTC
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import TYPE_CHECKING, Any, Optional

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .pylocalapi.cloud_stream import open_cloud_stream

if TYPE_CHECKING:
    from .api import Hp7Api

_LOGGER = logging.getLogger(__name__)

# How long to wait for ffmpeg to flush its stdin/stdout when tearing down.
FFMPEG_KILL_TIMEOUT = 2.0

# Bytes read at a time from ffmpeg stdout when forwarding to the TCP socket.
RELAY_CHUNK = 65536

# Max bytes in the producer → consumer hand-off queue. Roughly one second of
# 4 Mbit/s video; new packets are dropped if HA falls behind.
PAYLOAD_QUEUE_SIZE = 128

# Rate-limit defaults — the EZVIZ cloud temporarily locks accounts after a
# small handful of failed/repeated login attempts (error 1015). HA's Stream
# component is happy to reconnect every few seconds when an upstream stream
# errors, so without a circuit breaker here a single bad config can lock the
# account in under a minute.
MIN_RETRY_INTERVAL = 30.0  # seconds between connection attempts
LOCKOUT_THRESHOLD = 3  # consecutive failures before backing off harder
LOCKOUT_BACKOFF = 600.0  # seconds (10 min) once threshold is hit


class Hp7StreamRelay:
    """Per-entry TCP server that, on each accept, opens one VTM cloud session
    and forwards muxed MPEG-TS to the connected client (HA Stream component)."""

    def __init__(
        self,
        api: "Hp7Api",
        serial: str,
        channel: int = 1,
        ffmpeg_path: str = "ffmpeg",
    ) -> None:
        self._api = api
        self._serial = serial
        self._channel = channel
        self._ffmpeg_path = ffmpeg_path
        self._server: Optional[asyncio.AbstractServer] = None
        self._port: int = 0
        # Rate-limit state.
        self._last_attempt: float = 0.0
        self._last_error: Optional[str] = None
        self._consecutive_failures: int = 0
        self._connect_lock = asyncio.Lock()

    @property
    def port(self) -> int:
        return self._port

    @property
    def stream_url(self) -> str:
        return f"tcp://127.0.0.1:{self._port}"

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client, "127.0.0.1", 0
        )
        sock = self._server.sockets[0]
        self._port = int(sock.getsockname()[1])
        _LOGGER.debug(
            "Hp7StreamRelay listening on tcp://127.0.0.1:%d (serial=%s)",
            self._port,
            self._serial,
        )

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        try:
            await self._server.wait_closed()
        except Exception:
            pass
        self._server = None
        self._port = 0

    def _required_cooldown(self) -> float:
        if self._consecutive_failures >= LOCKOUT_THRESHOLD:
            return LOCKOUT_BACKOFF
        return MIN_RETRY_INTERVAL

    def _seconds_until_next_attempt(self) -> float:
        if self._last_error is None or self._last_attempt == 0.0:
            return 0.0
        elapsed = asyncio.get_event_loop().time() - self._last_attempt
        return self._required_cooldown() - elapsed

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        _LOGGER.debug("Hp7StreamRelay: client connected from %s", peer)

        wait = self._seconds_until_next_attempt()
        if wait > 0:
            _LOGGER.warning(
                "Hp7StreamRelay: rate-limited (last error: %s; %d consecutive "
                "failures; refusing for another %.0fs)",
                self._last_error,
                self._consecutive_failures,
                wait,
            )
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            return

        loop = asyncio.get_event_loop()
        vtm = None
        proc: Optional[asyncio.subprocess.Process] = None
        feed_task: Optional[asyncio.Task] = None
        reader_thread: Optional[threading.Thread] = None
        stop_event = threading.Event()
        payload_q: "queue.Queue[Optional[bytes]]" = queue.Queue(
            maxsize=PAYLOAD_QUEUE_SIZE
        )

        async with self._connect_lock:
            self._last_attempt = loop.time()
            try:
                # Ensure the cached EzvizClient is logged in. The coordinator
                # owns the long-lived client; we only borrow it for VTM bootstrap.
                await loop.run_in_executor(None, self._api.ensure_client)
                ezviz_client = self._api._client
                if ezviz_client is None:
                    raise RuntimeError("EzvizClient unavailable after ensure_client()")

                vtm = await loop.run_in_executor(
                    None,
                    lambda: open_cloud_stream(
                        ezviz_client, self._serial, channel=self._channel
                    ),
                )
                info = await loop.run_in_executor(None, vtm.start)
                _LOGGER.info(
                    "Hp7StreamRelay: VTM stream up (serial=%s ssn=%s)",
                    self._serial,
                    getattr(info, "streamssn", "?"),
                )
                self._consecutive_failures = 0
                self._last_error = None
            except Exception as exc:
                self._consecutive_failures += 1
                self._last_error = str(exc)
                _LOGGER.warning(
                    "Hp7StreamRelay: VTM connect failed (%d/%d): %s",
                    self._consecutive_failures,
                    LOCKOUT_THRESHOLD,
                    exc,
                )
                if vtm is not None:
                    try:
                        await loop.run_in_executor(None, vtm.close)
                    except Exception:
                        pass
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
                return

        try:
            proc = await asyncio.create_subprocess_exec(
                self._ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "warning",
                "-fflags",
                "+genpts+nobuffer",
                "-flags",
                "low_delay",
                "-f",
                "mpeg",
                "-i",
                "pipe:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "64k",
                "-f",
                "mpegts",
                "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

            reader_thread = threading.Thread(
                target=self._read_vtm_into_queue,
                args=(vtm, payload_q, stop_event),
                name=f"hp7-vtm-{self._serial}",
                daemon=True,
            )
            reader_thread.start()

            feed_task = asyncio.create_task(self._feed_ffmpeg(proc, payload_q))

            assert proc.stdout is not None
            while True:
                data = await proc.stdout.read(RELAY_CHUNK)
                if not data:
                    break
                try:
                    writer.write(data)
                    await writer.drain()
                except (ConnectionResetError, BrokenPipeError):
                    break
        except Exception as exc:
            _LOGGER.warning(
                "Hp7StreamRelay: stream error for serial=%s: %s",
                self._serial,
                exc,
            )
        finally:
            stop_event.set()
            if feed_task is not None and not feed_task.done():
                feed_task.cancel()
                try:
                    await feed_task
                except (asyncio.CancelledError, Exception):
                    pass
            if proc is not None:
                try:
                    if proc.stdin is not None and not proc.stdin.is_closing():
                        proc.stdin.close()
                except Exception:
                    pass
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=FFMPEG_KILL_TIMEOUT)
                except (asyncio.TimeoutError, Exception):
                    pass
            if vtm is not None:
                try:
                    await loop.run_in_executor(None, vtm.close)
                except Exception:
                    pass
            if reader_thread is not None:
                reader_thread.join(timeout=2.0)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            _LOGGER.debug("Hp7StreamRelay: client %s closed", peer)

    @staticmethod
    def _read_vtm_into_queue(
        vtm: Any,
        q: "queue.Queue[Optional[bytes]]",
        stop_event: threading.Event,
    ) -> None:
        """Drain the VTM iterator into the hand-off queue. Runs in a thread."""
        try:
            for body in vtm.iter_payloads():
                if stop_event.is_set():
                    break
                if not body:
                    continue
                try:
                    q.put(body, timeout=2.0)
                except queue.Full:
                    # Consumer fell behind; drop oldest by clearing one slot
                    # so we can put the fresh chunk. Better to drop than stall.
                    try:
                        q.get_nowait()
                        q.put_nowait(body)
                    except (queue.Empty, queue.Full):
                        pass
        except Exception as exc:
            _LOGGER.warning("Hp7StreamRelay: VTM iterator stopped: %s", exc)
        finally:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass

    @staticmethod
    async def _feed_ffmpeg(
        proc: asyncio.subprocess.Process,
        q: "queue.Queue[Optional[bytes]]",
    ) -> None:
        """Drain the queue into ffmpeg stdin. Runs on the asyncio loop."""
        loop = asyncio.get_event_loop()
        assert proc.stdin is not None
        try:
            while True:
                body = await loop.run_in_executor(None, q.get)
                if body is None:
                    return
                if proc.stdin.is_closing():
                    return
                try:
                    proc.stdin.write(body)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    return
        finally:
            try:
                if proc.stdin is not None and not proc.stdin.is_closing():
                    proc.stdin.close()
            except Exception:
                pass


class Hp7LiveCamera(Camera):
    """Live H.264 stream from the HP7/CP7 via the VTM cloud relay."""

    _attr_has_entity_name = True
    _attr_translation_key = "live"

    def __init__(self, serial: str, model: str, relay: Hp7StreamRelay) -> None:
        super().__init__()
        self._serial = serial
        self._model = model
        self._relay = relay
        self._attr_unique_id = f"{DOMAIN}_{serial}_live"

    @property
    def device_info(self) -> DeviceInfo:
        from .device_info import make_device_info

        return make_device_info(self._serial, self._model)

    @property
    def supported_features(self) -> int:
        from homeassistant.components.camera import CameraEntityFeature

        return CameraEntityFeature.STREAM

    async def stream_source(self) -> Optional[str]:
        if self._relay.port == 0:
            return None
        return self._relay.stream_url


async def async_setup_live_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Bootstrap the per-entry stream relay and add the live camera entity."""
    data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    serial: str = data["serial"]
    model: str = data.get("model") or "HP7"
    api: "Hp7Api" = data["api"]

    relay = Hp7StreamRelay(api=api, serial=serial, channel=1)
    await relay.start()
    data["live_relay"] = relay

    async_add_entities([Hp7LiveCamera(serial, model, relay)])


async def async_unload_live_entities(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Tear down the per-entry stream relay on unload."""
    data: Optional[dict[str, Any]] = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        return
    relay: Optional[Hp7StreamRelay] = data.pop("live_relay", None)
    if relay is not None:
        await relay.stop()
