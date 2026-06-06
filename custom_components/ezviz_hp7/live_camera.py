"""Live video camera entity for the HP7 / CP7 — VTM cloud relay path.

The HP7 / CP7 don't register on the Hikvision UDP P2P cloud (verified
2026-06-06 against a live HP7: every P2P_SETUP response on the 5 cloud
servers came back as a bare ClientID echo without the 0xFF sub-TLV — the
cloud cannot route a P2P_SETUP to a consumer doorbell). The official
EZVIZ app streams them through the VTM cloud relay: a TCP ysproto
session delivering MPEG-PS that wraps H.264 video (PES stream_id 0xE0)
and AAC-LC 16 kHz mono audio that ships as AAC-ADTS but is mis-labelled
inside the PES as MP2 (stream_id 0xC0). The standard ffmpeg `mpeg`
demuxer therefore rejects every audio packet.

Per viewing session this module:

    HP7  ->  VTM cloud (ysproto://...:8554/live)
        |
        v  VtmStreamClient.iter_payloads()  (sync, executor thread)
        |
        v  _pes.PesParser  (Python MPEG-PS PES splitter)
        |
        +--->  video bytes ->  queue.Queue  ->  TCP 127.0.0.1:V
        +--->  audio bytes ->  queue.Queue  ->  TCP 127.0.0.1:A
                                                |
                                                v
        ffmpeg subprocess:
            -f h264 -i tcp://127.0.0.1:V
            -f aac  -i tcp://127.0.0.1:A
            -c:v copy -c:a aac -ar 16000 -ac 1 -b:a 32k
            -max_interleave_delta 0 -f mpegts pipe:1
        (stdout) ->  TCP relay 127.0.0.1:<port>  ->  HA Stream / HLS

The audio leg is re-encoded (rather than `copy`) so the MPEG-TS muxer
gets the AudioSpecificConfig extradata that ADTS strips, and
`-use_wallclock_as_timestamps 1` gives ffmpeg something to anchor on
since the raw h264/aac inputs carry no container timestamps.

A circuit breaker rate-limits accept attempts: MIN_RETRY_INTERVAL = 30 s,
LOCKOUT_THRESHOLD = 3 consecutive failures flips to LOCKOUT_BACKOFF
(10 min). HA's Stream component is happy to reconnect every few seconds
when an upstream stream errors; without this throttle a single bad
config could lock the EZVIZ account in under a minute.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import socket
import threading
from contextlib import closing
from typing import TYPE_CHECKING, Any, List, Optional

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._pes import PesParser
from .const import DOMAIN
from .pylocalapi.cloud_stream import open_cloud_stream

if TYPE_CHECKING:
    from .api import Hp7Api

_LOGGER = logging.getLogger(__name__)

VIDEO_STREAM_ID = 0xE0
AUDIO_STREAM_ID = 0xC0

FFMPEG_KILL_TIMEOUT = 2.0
RELAY_CHUNK = 65536
PAYLOAD_QUEUE_SIZE = 256

MIN_RETRY_INTERVAL = 30.0
LOCKOUT_THRESHOLD = 3
LOCKOUT_BACKOFF = 600.0

INPUT_ACCEPT_TIMEOUT = 20.0


def _free_local_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_local_listener(port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


def _reader_thread(
    vtm: Any,
    v_q: "queue.Queue[Optional[bytes]]",
    a_q: "queue.Queue[Optional[bytes]]",
    stop: threading.Event,
) -> None:
    """Drain VtmStreamClient -> PesParser -> push per-stream bytes into queues."""
    parser = PesParser()
    v_bytes = 0
    a_bytes = 0
    try:
        for body in vtm.iter_payloads():
            if stop.is_set():
                break
            if not body:
                continue
            for stream_id, payload in parser.feed(body):
                if not payload:
                    continue
                if stream_id == VIDEO_STREAM_ID:
                    try:
                        v_q.put(payload, timeout=2.0)
                        v_bytes += len(payload)
                    except queue.Full:
                        pass
                elif stream_id == AUDIO_STREAM_ID:
                    try:
                        a_q.put(payload, timeout=2.0)
                        a_bytes += len(payload)
                    except queue.Full:
                        pass
    except Exception as exc:
        _LOGGER.debug("Hp7StreamRelay: reader stopped: %s", exc)
    finally:
        for q in (v_q, a_q):
            try:
                q.put_nowait(None)
            except queue.Full:
                pass
        _LOGGER.debug(
            "Hp7StreamRelay: reader done video=%d B audio=%d B yielded=%d resync_drops=%d",
            v_bytes, a_bytes, parser.packets_yielded, parser.resync_drops,
        )


def _sender_thread(
    listener: socket.socket,
    q: "queue.Queue[Optional[bytes]]",
    stop: threading.Event,
    label: str,
) -> None:
    """Wait for ffmpeg to connect, then forward the queue into that socket."""
    try:
        listener.settimeout(INPUT_ACCEPT_TIMEOUT)
        try:
            conn, peer = listener.accept()
        except socket.timeout:
            _LOGGER.warning(
                "Hp7StreamRelay: ffmpeg %s input accept timed out", label
            )
            return
        _LOGGER.debug("Hp7StreamRelay: %s accepted from %s", label, peer)
    finally:
        try:
            listener.close()
        except OSError:
            pass

    sent = 0
    try:
        while not stop.is_set():
            try:
                payload = q.get(timeout=0.5)
            except queue.Empty:
                continue
            if payload is None:
                return
            try:
                conn.sendall(payload)
                sent += len(payload)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
    finally:
        _LOGGER.debug("Hp7StreamRelay: %s sender done (%d B sent)", label, sent)
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass


class Hp7StreamRelay:
    """Per-entry TCP server. Each accept opens a VTM session and forwards
    muxed MPEG-TS (H.264 + AAC) to the connected client (HA Stream component)."""

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
            self._port, self._serial,
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
                self._last_error, self._consecutive_failures, wait,
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
        v_listener: Optional[socket.socket] = None
        a_listener: Optional[socket.socket] = None
        threads: List[threading.Thread] = []
        stop_event = threading.Event()

        async with self._connect_lock:
            self._last_attempt = loop.time()
            try:
                await loop.run_in_executor(None, self._api.ensure_client)
                ezviz_client = self._api._client
                if ezviz_client is None:
                    raise RuntimeError(
                        "EzvizClient unavailable after ensure_client()"
                    )

                vtm = await loop.run_in_executor(
                    None,
                    lambda: open_cloud_stream(
                        ezviz_client, self._serial, channel=self._channel
                    ),
                )
                info = await loop.run_in_executor(None, vtm.start)
                _LOGGER.info(
                    "Hp7StreamRelay: VTM stream up (serial=%s ssn=%s)",
                    self._serial, getattr(info, "streamssn", "?"),
                )
                self._consecutive_failures = 0
                self._last_error = None
            except Exception as exc:
                self._consecutive_failures += 1
                self._last_error = str(exc)
                _LOGGER.warning(
                    "Hp7StreamRelay: VTM connect failed (%d/%d): %s",
                    self._consecutive_failures, LOCKOUT_THRESHOLD, exc,
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
            v_port = _free_local_port()
            a_port = _free_local_port()
            v_listener = _start_local_listener(v_port)
            a_listener = _start_local_listener(a_port)

            cmd = [
                self._ffmpeg_path,
                "-hide_banner", "-loglevel", "error",
                "-fflags", "+genpts+nobuffer", "-flags", "low_delay",
                "-analyzeduration", "200000", "-probesize", "200000",
                "-use_wallclock_as_timestamps", "1",
                "-f", "h264", "-r", "15",
                "-i", f"tcp://127.0.0.1:{v_port}",
                "-analyzeduration", "200000", "-probesize", "200000",
                "-use_wallclock_as_timestamps", "1",
                "-f", "aac",
                "-i", f"tcp://127.0.0.1:{a_port}",
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy",
                # Re-encode audio so the mpegts muxer gets a proper AAC
                # AudioSpecificConfig extradata (the inbound ADTS strips it).
                "-c:a", "aac", "-ar", "16000", "-ac", "1", "-b:a", "32k",
                "-max_interleave_delta", "0",
                "-f", "mpegts", "pipe:1",
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

            v_q: "queue.Queue[Optional[bytes]]" = queue.Queue(
                maxsize=PAYLOAD_QUEUE_SIZE
            )
            a_q: "queue.Queue[Optional[bytes]]" = queue.Queue(
                maxsize=PAYLOAD_QUEUE_SIZE
            )

            reader_t = threading.Thread(
                target=_reader_thread,
                args=(vtm, v_q, a_q, stop_event),
                name=f"hp7-vtm-reader-{self._serial}",
                daemon=True,
            )
            v_sender_t = threading.Thread(
                target=_sender_thread,
                args=(v_listener, v_q, stop_event, "video"),
                name=f"hp7-vtm-vsend-{self._serial}",
                daemon=True,
            )
            a_sender_t = threading.Thread(
                target=_sender_thread,
                args=(a_listener, a_q, stop_event, "audio"),
                name=f"hp7-vtm-asend-{self._serial}",
                daemon=True,
            )
            threads = [reader_t, v_sender_t, a_sender_t]
            for t in threads:
                t.start()

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
                self._serial, exc,
            )
        finally:
            stop_event.set()
            if proc is not None:
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
            for listener in (v_listener, a_listener):
                if listener is not None:
                    try:
                        listener.close()
                    except OSError:
                        pass
            for t in threads:
                t.join(timeout=2.0)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            _LOGGER.debug("Hp7StreamRelay: client %s closed", peer)


class Hp7LiveCamera(Camera):
    """Live H.264 + AAC stream from the HP7/CP7 via the VTM cloud relay."""

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
