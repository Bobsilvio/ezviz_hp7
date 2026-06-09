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

# Pre-warm: how long a shared VTM session stays alive after the last HA
# Stream client disconnected before we tear it down. Long enough that the
# typical "ring -> open dashboard" workflow finds an already-running session
# (sub-second first frame), short enough that we don't leave a cloud session
# open all day after a stray motion event.
PREWARM_IDLE_TIMEOUT = 120.0


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
        listen_port: int = 0,
    ) -> None:
        self._api = api
        self._serial = serial
        self._channel = channel
        self._ffmpeg_path = ffmpeg_path
        # Requested fixed port (0 = pick a free one). External consumers
        # like go2rtc need a stable URL; OptionsFlow exposes this.
        self._listen_port = int(listen_port) if listen_port else 0
        self._server: Optional[asyncio.AbstractServer] = None
        self._port: int = 0
        self._last_attempt: float = 0.0
        self._last_error: Optional[str] = None
        self._consecutive_failures: int = 0
        self._connect_lock = asyncio.Lock()
        # Shared (pre-warmed) VTM session + broadcast bookkeeping.
        self._shared_lock = asyncio.Lock()
        self._shared_vtm: Any = None
        self._shared_stop = threading.Event()
        self._shared_reader: Optional[threading.Thread] = None
        # Per-subscriber queues populated by the shared reader.
        self._sub_v_qs: List["queue.Queue[Optional[bytes]]"] = []
        self._sub_a_qs: List["queue.Queue[Optional[bytes]]"] = []
        self._idle_handle: Optional[asyncio.TimerHandle] = None
        self._active_clients: int = 0

    @property
    def port(self) -> int:
        return self._port

    @property
    def stream_url(self) -> str:
        return f"tcp://127.0.0.1:{self._port}"

    async def start(self) -> None:
        if self._server is not None:
            return
        # Try the configured fixed port first; fall back to a random free
        # one if it's taken so HA setup doesn't fail when (e.g.) the user
        # left a previous relay running.
        try:
            self._server = await asyncio.start_server(
                self._handle_client, "127.0.0.1", self._listen_port
            )
        except OSError as exc:
            if self._listen_port:
                _LOGGER.warning(
                    "Hp7StreamRelay: fixed port %d busy (%s); falling back to "
                    "a random one",
                    self._listen_port, exc,
                )
                self._server = await asyncio.start_server(
                    self._handle_client, "127.0.0.1", 0
                )
            else:
                raise
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
        await self._shutdown_shared()

    # ------------------------------------------------------------------
    # Shared VTM pre-warm
    # ------------------------------------------------------------------

    async def prewarm(self) -> None:
        """Open (or extend) a shared VTM session that future HA Stream
        clients can reuse instead of paying the cloud handshake cost.

        Safe to call repeatedly: if a shared session is already active the
        idle teardown timer is just reset.
        """
        loop = asyncio.get_event_loop()
        async with self._shared_lock:
            if self._shared_vtm is not None:
                self._arm_idle_timer()
                _LOGGER.debug(
                    "Hp7StreamRelay: prewarm extended (serial=%s)", self._serial
                )
                return
            # Rate-limit shares the relay's circuit-breaker.
            wait = self._seconds_until_next_attempt()
            if wait > 0:
                _LOGGER.debug(
                    "Hp7StreamRelay: prewarm skipped — rate-limited (%.0fs)",
                    wait,
                )
                return
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
                self._consecutive_failures = 0
                self._last_error = None
                _LOGGER.info(
                    "Hp7StreamRelay: pre-warm VTM up (serial=%s ssn=%s)",
                    self._serial,
                    getattr(info, "streamssn", "?"),
                )
            except Exception as exc:
                self._consecutive_failures += 1
                self._last_error = str(exc)
                _LOGGER.warning(
                    "Hp7StreamRelay: prewarm failed (%d/%d): %s",
                    self._consecutive_failures, LOCKOUT_THRESHOLD, exc,
                )
                return
            self._shared_vtm = vtm
            self._shared_stop = threading.Event()
            self._shared_reader = threading.Thread(
                target=self._broadcast_reader,
                name=f"hp7-vtm-broadcast-{self._serial}",
                daemon=True,
            )
            self._shared_reader.start()
            self._arm_idle_timer()

    def _arm_idle_timer(self) -> None:
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None
        if self._active_clients > 0:
            # Don't tear down while clients are connected — they cover us.
            return
        loop = asyncio.get_event_loop()
        self._idle_handle = loop.call_later(
            PREWARM_IDLE_TIMEOUT, self._idle_expired
        )

    def _idle_expired(self) -> None:
        self._idle_handle = None
        if self._active_clients > 0:
            return
        asyncio.create_task(self._shutdown_shared())

    async def _shutdown_shared(self) -> None:
        async with self._shared_lock:
            vtm = self._shared_vtm
            self._shared_vtm = None
            self._shared_stop.set()
            for q in list(self._sub_v_qs) + list(self._sub_a_qs):
                try:
                    q.put_nowait(None)
                except queue.Full:
                    pass
            if self._idle_handle is not None:
                self._idle_handle.cancel()
                self._idle_handle = None
        if vtm is not None:
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(None, vtm.close)
            except Exception:
                pass
        if self._shared_reader is not None:
            self._shared_reader.join(timeout=2.0)
            self._shared_reader = None
        _LOGGER.debug("Hp7StreamRelay: shared VTM torn down (serial=%s)", self._serial)

    def _broadcast_reader(self) -> None:
        """Read VTM payloads -> PesParser -> fan out to per-client queues."""
        parser = PesParser()
        v_bytes = a_bytes = 0
        next_v_log = 256 * 1024
        next_a_log = 32 * 1024
        try:
            for body in self._shared_vtm.iter_payloads():
                if self._shared_stop.is_set():
                    break
                if not body:
                    continue
                for stream_id, payload in parser.feed(body):
                    if not payload:
                        continue
                    if stream_id == VIDEO_STREAM_ID:
                        v_bytes += len(payload)
                        for q in list(self._sub_v_qs):
                            try:
                                q.put_nowait(payload)
                            except queue.Full:
                                pass
                        if v_bytes >= next_v_log:
                            _LOGGER.info(
                                "Hp7StreamRelay: broadcast video progress %d B "
                                "subs=%d",
                                v_bytes,
                                len(self._sub_v_qs),
                            )
                            next_v_log = v_bytes + 256 * 1024
                    elif stream_id == AUDIO_STREAM_ID:
                        a_bytes += len(payload)
                        for q in list(self._sub_a_qs):
                            try:
                                q.put_nowait(payload)
                            except queue.Full:
                                pass
                        if a_bytes >= next_a_log:
                            _LOGGER.info(
                                "Hp7StreamRelay: broadcast audio progress %d B "
                                "subs=%d",
                                a_bytes,
                                len(self._sub_a_qs),
                            )
                            next_a_log = a_bytes + 32 * 1024
        except Exception as exc:
            _LOGGER.warning(
                "Hp7StreamRelay: broadcast reader stopped: %s", exc
            )
        finally:
            for q in list(self._sub_v_qs) + list(self._sub_a_qs):
                try:
                    q.put_nowait(None)
                except queue.Full:
                    pass
            _LOGGER.info(
                "Hp7StreamRelay: broadcast done video=%d B audio=%d B "
                "resync_drops=%d",
                v_bytes, a_bytes, parser.resync_drops,
            )

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
        proc: Optional[asyncio.subprocess.Process] = None
        v_listener: Optional[socket.socket] = None
        a_listener: Optional[socket.socket] = None
        threads: List[threading.Thread] = []
        stop_event = threading.Event()
        v_q: "queue.Queue[Optional[bytes]]" = queue.Queue(
            maxsize=PAYLOAD_QUEUE_SIZE
        )
        a_q: "queue.Queue[Optional[bytes]]" = queue.Queue(
            maxsize=PAYLOAD_QUEUE_SIZE
        )

        # Ensure a shared VTM is running (this is the cold path on first
        # connect; subsequent connects reuse the same session for the next
        # PREWARM_IDLE_TIMEOUT seconds after the last client leaves).
        await self.prewarm()
        if self._shared_vtm is None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            return

        # Subscribe to the broadcast.
        self._sub_v_qs.append(v_q)
        self._sub_a_qs.append(a_q)
        self._active_clients += 1
        self._arm_idle_timer()  # cancel idle teardown while we're connected

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
                # Re-emit PAT/PMT every second so a mid-stream connect (HA
                # Stream worker probing after ffmpeg started producing) can
                # still pick up the codec parameters. Earlier 0.9.5 also
                # carried -bsf:v dump_extra + initial_discontinuity, but
                # those broke HP7 (Annex-B input had no extradata to dump,
                # and the discontinuity bit on the very first TS packet
                # caused PyAV to reject the stream with "Invalid data found
                # when processing input"). Keeping only the PAT/PMT refresh
                # — which is sufficient on its own for CP5.
                "-mpegts_flags", "+resend_headers",
                "-pat_period", "1",
                "-sdt_period", "1",
                "-f", "mpegts", "pipe:1",
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # Drain ffmpeg stderr to the logger so 0.9.x's silent-failure
            # mode (#33) becomes visible. One line per ffmpeg message.
            if proc.stderr is not None:
                async def _drain_ff_err(stream: asyncio.StreamReader) -> None:
                    try:
                        while True:
                            line = await stream.readline()
                            if not line:
                                return
                            _LOGGER.debug(
                                "Hp7StreamRelay: ffmpeg | %s",
                                line.decode(errors="replace").rstrip(),
                            )
                    except Exception:
                        return
                asyncio.create_task(_drain_ff_err(proc.stderr))

            # Reader is the broadcast (shared VTM); only senders are per-client.
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
            threads = [v_sender_t, a_sender_t]
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
            # Unsubscribe from the shared broadcast.
            for q in (v_q, a_q):
                try:
                    q.put_nowait(None)
                except queue.Full:
                    pass
            try:
                self._sub_v_qs.remove(v_q)
            except ValueError:
                pass
            try:
                self._sub_a_qs.remove(a_q)
            except ValueError:
                pass
            self._active_clients = max(0, self._active_clients - 1)
            self._arm_idle_timer()

            if proc is not None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=FFMPEG_KILL_TIMEOUT)
                except (asyncio.TimeoutError, Exception):
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

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Return a snapshot grabbed from the live stream.

        The frontend tile / Lovelace previews ask for a still image even on
        stream-only cameras; the base Camera class raises NotImplementedError
        if we don't override this. Spawn a one-shot ffmpeg against the local
        TCP relay (`-frames:v 1 -f image2`) to grab a single JPEG. The
        active VTM session served by the relay is reused, so we don't open
        a second cloud session just for a thumbnail.
        """
        if self._relay.port == 0:
            return None
        url = self._relay.stream_url
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-fflags",
                "+nobuffer",
                "-flags",
                "low_delay",
                "-i",
                url,
                "-frames:v",
                "1",
                "-q:v",
                "5",
                "-f",
                "image2",
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Hp7LiveCamera: ffmpeg snapshot spawn failed: %s", exc)
            return None
        try:
            data, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            _LOGGER.debug("Hp7LiveCamera: snapshot ffmpeg timed out")
            return None
        return data or None


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
    relay_port: int = int(data.get("relay_port") or 0)

    relay = Hp7StreamRelay(
        api=api, serial=serial, channel=1, listen_port=relay_port
    )
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
