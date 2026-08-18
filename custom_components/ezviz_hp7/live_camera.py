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
import time
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


class Cpd7LanSource:
    """VTM-shaped wrapper around the CPD7 LAN pipeline.

    Exposes ``start()`` / ``iter_payloads()`` / ``close()`` so it is a
    drop-in replacement for the cloud VTM session inside the relay's
    broadcast reader. ``iter_payloads()`` yields MPEG-PS bytes, exactly
    like the cloud path, so the rest of the pipeline (PesParser -> ffmpeg)
    is unchanged. LAN protocol credit: albrzmr (see cpd7/__init__.py).
    """

    def __init__(
        self, api: "Hp7Api", serial: str, channel: int = 1,
        stream_quality: str = "main",
    ) -> None:
        self._api = api
        self._serial = serial
        self._channel = channel
        self._stream_quality = stream_quality
        self._client: Any = None
        self._decoder: Any = None
        self._closed = False

    def start(self) -> "Cpd7LanSource":
        """Open the LAN session (blocking — run in an executor)."""
        from .cpd7 import Cpd7LanClient, StreamDecoder

        key = self._api.fetch_lan_aes_key(self._serial)
        local_ip = self._api.get_local_ip(self._serial)
        if not local_ip:
            raise RuntimeError(
                f"could not resolve LAN IP for {self._serial} "
                "(device not on this network?)"
            )
        related = self._api.get_related_device(self._serial)
        client = Cpd7LanClient(
            local_ip, related, key, channel=self._channel,
            stream_quality=self._stream_quality,
        )
        client.start()
        self._client = client
        self._decoder = StreamDecoder(client.ecdh_priv)
        _LOGGER.info(
            "Hp7StreamRelay: LAN source up (serial=%s ip=%s stream=%s)",
            self._serial, local_ip, self._stream_quality,
        )
        return self

    @property
    def streamssn(self) -> str:
        return f"lan:{self._serial}"

    def iter_payloads(self):
        """Yield MPEG-PS payloads decoded from the LAN play socket."""
        empty_strikes = 0
        while not self._closed:
            buf = self._client.read_chunk()
            if not buf:
                empty_strikes += 1
                # Tolerate brief gaps; give up after sustained silence.
                if empty_strikes > 3:
                    break
                continue
            empty_strikes = 0
            self._decoder.feed(buf)
            out = self._decoder.take()
            if out:
                yield out

    def close(self) -> None:
        self._closed = True
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None


class _PreStartedSource:
    """Wrap an already-started source so a second ``start()`` is a no-op.

    Used by the "auto" path, which starts the LAN source eagerly to probe
    whether it works before committing the broadcast reader to it.
    """

    def __init__(self, src: Any) -> None:
        self._src = src

    def start(self) -> Any:
        return self._src

    def iter_payloads(self):
        return self._src.iter_payloads()

    def close(self) -> None:
        self._src.close()

    @property
    def streamssn(self) -> str:
        return getattr(self._src, "streamssn", "lan")

VIDEO_STREAM_ID = 0xE0
AUDIO_STREAM_ID = 0xC0

FFMPEG_KILL_TIMEOUT = 2.0
RELAY_CHUNK = 65536
PAYLOAD_QUEUE_SIZE = 256
# Video carries whole MPEG-PS chunks and cannot tolerate gaps the way audio
# can, so give it a deep buffer: at ~460 KB/s a 4096-slot queue is tens of
# seconds of slack, which in practice means the drop path is never reached.
# Dropping is then a genuine last resort rather than routine behaviour, and
# the saturation warning below tells us whether it ever actually happens.
VIDEO_QUEUE_SIZE = 4096
# Watchdog on the relay ffmpeg's output (#41): if it produces nothing for
# this long the session is dead — either the decode is failing silently
# (HPD5 bad-NAL case: relay ingested 141 MB while emitting 0 bytes for 2 min)
# or the viewer is gone and we'd never notice because we only detect a dead
# peer on write. Without this, _handle_client blocks on proc.stdout.read()
# forever and each retry leaks a broadcast subscriber + an ffmpeg process.
# First-output allowance is generous: the 10 MB probe window (0.13.9) can
# legitimately take ~60 s on a low-bitrate stream before the first byte.
FIRST_OUTPUT_TIMEOUT = 90.0
OUTPUT_STALL_TIMEOUT = 30.0
# Upper bound on the GOP cache (#37). A 1 Mbit/s stream with a 30 s keyframe
# interval accumulates ~4 MB per GOP; 8 MB covers that with headroom. If a
# GOP exceeds this the cache is dropped for that GOP (new viewers fall back
# to waiting for the next keyframe — the pre-cache behaviour).
GOP_CACHE_MAX = 8 * 1024 * 1024
# Interval between relay progress log lines (#44).
PROGRESS_LOG_SEC = 60.0
# A subscriber queue saturated this long with nothing draining it has no live
# consumer left; evict it so its leaked ffmpeg stops competing for CPU (#44).
DEAD_SUB_TIMEOUT = 30.0

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


def _iter_nal_spans(data: bytes):
    """Yield (start_code_offset, header_byte_offset) for every NAL.

    Same scan as _iter_nal_types, but keeps the offsets so callers can cut
    the payload at NAL boundaries.
    """
    n = len(data)
    i = 0
    while i < n - 3:
        if data[i] == 0 and data[i + 1] == 0:
            if data[i + 2] == 1:
                if i + 3 < n:
                    yield i, i + 3
                i += 4
                continue
            if data[i + 2] == 0 and i + 3 < n and data[i + 3] == 1:
                if i + 4 < n:
                    yield i, i + 4
                i += 5
                continue
        i += 1


def _iter_nal_types(data: bytes):
    """Yield raw NAL header bytes (the byte right after each start code).

    Handles both 3-byte (00 00 01) and 4-byte (00 00 00 01) start codes.
    """
    n = len(data)
    i = 0
    while i < n - 3:
        if data[i] == 0 and data[i + 1] == 0:
            if data[i + 2] == 1:
                if i + 3 < n:
                    yield data[i + 3]
                i += 4
                continue
            if data[i + 2] == 0 and i + 3 < n and data[i + 3] == 1:
                if i + 4 < n:
                    yield data[i + 4]
                i += 5
                continue
        i += 1


# Parameter-set start markers: HEVC VPS/SPS/PPS with nuh_layer_id=0 and
# temporal_id_plus1=1 (so the second byte pins them down and can't collide
# with an H.264 header), H.264 SPS/PPS. Both 4- and 3-byte start codes.
_PARAM_SET_MARKERS = (
    b"\x00\x00\x00\x01\x40\x01", b"\x00\x00\x01\x40\x01",   # HEVC VPS
    b"\x00\x00\x00\x01\x42\x01", b"\x00\x00\x01\x42\x01",   # HEVC SPS
    b"\x00\x00\x00\x01\x44\x01", b"\x00\x00\x01\x44\x01",   # HEVC PPS
    b"\x00\x00\x00\x01\x67", b"\x00\x00\x01\x67",           # H.264 SPS
    b"\x00\x00\x00\x01\x68", b"\x00\x00\x01\x68",           # H.264 PPS
)

# Random-access points — a frame a decoder can start on. HEVC BLA (16-18),
# IDR_W_RADL (19), IDR_N_LP (20) and CRA (21); H.264 IDR (5). These are
# what actually reset the reference picture set, and firmware does NOT
# necessarily precede every one of them with parameter sets (#51): the
# HPD7 on V5.4.0 repeats VPS/SPS/PPS far less often than it emits IDRs.
_RAP_MARKERS = (
    b"\x00\x00\x00\x01\x20\x01", b"\x00\x00\x01\x20\x01",   # HEVC BLA_W_LP
    b"\x00\x00\x00\x01\x22\x01", b"\x00\x00\x01\x22\x01",   # HEVC BLA_W_RADL
    b"\x00\x00\x00\x01\x24\x01", b"\x00\x00\x01\x24\x01",   # HEVC BLA_N_LP
    b"\x00\x00\x00\x01\x26\x01", b"\x00\x00\x01\x26\x01",   # HEVC IDR_W_RADL
    b"\x00\x00\x00\x01\x28\x01", b"\x00\x00\x01\x28\x01",   # HEVC IDR_N_LP
    b"\x00\x00\x00\x01\x2a\x01", b"\x00\x00\x01\x2a\x01",   # HEVC CRA
    b"\x00\x00\x00\x01\x65", b"\x00\x00\x01\x65",           # H.264 IDR
)

# Kept for the fan-out resync path, which only needs "can a viewer join
# here" — parameter sets and random-access points both qualify.
_KEYFRAME_MARKERS = _PARAM_SET_MARKERS + _RAP_MARKERS


def _has_keyframe(payload: bytes) -> bool:
    return any(m in payload for m in _KEYFRAME_MARKERS)


def _has_param_sets(payload: bytes) -> bool:
    return any(m in payload for m in _PARAM_SET_MARKERS)


def _extract_param_sets(payload: bytes) -> bytes:
    """Return the parameter-set NALs in ``payload``, framing included.

    Slices from the first parameter set up to the start of the first NAL
    that isn't one, so the result is a self-contained Annex B prefix a
    decoder can be initialised with.
    """
    spans = [(off, payload[hdr]) for off, hdr in _iter_nal_spans(payload)]
    start = None
    for idx, (off, hdr) in enumerate(spans):
        is_ps = hdr in (0x40, 0x42, 0x44, 0x67, 0x68)
        if is_ps and start is None:
            start = off
        elif not is_ps and start is not None:
            return payload[start:off]
    return payload[start:] if start is not None else b""


def _sniff_video_codec(payload: bytes) -> Optional[str]:
    """Best-effort H.264 vs H.265 detection from raw NAL header bytes.

    We only trust the unambiguous parameter-set headers, matched as exact
    bytes (nuh_layer_id=0), to avoid false positives — e.g. an H.264
    P-slice header 0x41 would otherwise decode as HEVC nal_type 32 under a
    naive (byte>>1)&0x3F.

    HEVC param sets: VPS=0x40, SPS=0x42, PPS=0x44.
    H.264 param sets / IDR: SPS=0x67, PPS=0x68, IDR=0x65.

    Some firmware (#33) never emits parameter sets at all; for those this
    returns None and the caller falls back to the configured default.
    """
    for nal in _iter_nal_types(payload):
        if nal in (0x40, 0x42, 0x44):
            return "hevc"
        if nal in (0x67, 0x68, 0x65):
            return "h264"
    return None


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


def _mk_drain(table: dict, q: Any):
    """Return a callback stamping this queue's last-drain time (#44)."""
    qid = id(q)

    def _stamp() -> None:
        table[qid] = time.monotonic()

    return _stamp


def _sender_thread(
    listener: socket.socket,
    q: "queue.Queue[Optional[bytes]]",
    stop: threading.Event,
    label: str,
    on_drain: Any = None,
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
            if on_drain is not None:
                # Liveness signal: this subscriber's consumer is alive and
                # actually taking data (#44 follow-up).
                on_drain()
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
        bind_host: str = "127.0.0.1",
        aggressive_mpegts: bool = False,
        video_codec: str = "auto",
        stream_source: str = "cloud",
        stream_mode: str = "mjpeg",
        stream_quality: str = "main",
        diagnostic_dumps: bool = False,
        encryption_key: str = "",
        hass: Any = None,
    ) -> None:
        self._hass = hass
        self._api = api
        self._serial = serial
        self._channel = channel
        self._ffmpeg_path = ffmpeg_path
        # Delivery mode ("webrtc" | "mjpeg"). Only used to warn once when we
        # sniff HEVC while on WebRTC, where the browser can't decode a copy and
        # go2rtc must transcode (grey screen / dial refused on weak hosts).
        self._stream_mode = (stream_mode or "mjpeg").lower()
        self._warned_hevc_webrtc = False
        # Stream source: "cloud" (VTM relay), "local" (CPD7 LAN), or "auto"
        # (try LAN, fall back to cloud). LAN bypasses the cloud entirely and
        # works on firmware whose VTM channel never pushes (#33/#36/#37).
        self._stream_source = (stream_source or "cloud").lower()
        # LAN encoder stream: "main" (full res) or "sub" (low-res, far
        # cheaper to decode on weak hosts — #44).
        self._stream_quality = (stream_quality or "main").lower()
        # Configured codec: "auto" | "h264" | "hevc". "auto" inspects the
        # first NAL units off the broadcast and sets _detected_codec; the
        # other two force it. Newer HP7 (HPD7) streams HEVC (#36, #37).
        self._video_codec = (video_codec or "auto").lower()
        self._detected_codec: Optional[str] = None
        # Requested fixed port (0 = pick a free one). External consumers
        # like go2rtc need a stable URL; OptionsFlow exposes this.
        self._listen_port = int(listen_port) if listen_port else 0
        # Bind host. 127.0.0.1 keeps the unauthenticated raw stream
        # local-only; 0.0.0.0 (opt-in) lets an external host — Frigate on
        # another box — ingest it (#41).
        self._bind_host = (bind_host or "127.0.0.1").strip() or "127.0.0.1"
        # Opt-in toggle to re-emit SPS/PPS in front of every IDR. Helps
        # firmwares that emit them only at the first IDR (CP5, some HP7
        # builds — #33); breaks the firmwares that already inline them.
        self._aggressive_mpegts = bool(aggressive_mpegts)
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
        # Per-client queues for the LAN raw-MPEG-PS path (single muxed feed).
        self._sub_raw_qs: List["queue.Queue[Optional[bytes]]"] = []
        # Diagnostic ES dump (#41): when debug logging is on, the first
        # 64 KB of demuxed video ES per session is written to the HA config
        # dir so misframed NAL payloads (HPD5) can be hex-inspected without
        # a packet capture.
        self._diagnostic_dumps = bool(diagnostic_dumps)
        self._configured_key = (encryption_key or "").strip()
        self._es_dump_buf = bytearray()
        self._es_dump_done = False
        # Raw MPEG-PS dump + keyframe-marker counter (#41): the HPD5 "synced"
        # stream turned out to contain no parameter sets in the video ES, so
        # we also need to see the container *before* demux and whether
        # VPS/SPS markers ever occur across the whole session.
        self._ps_dump_buf = bytearray()
        self._ps_dump_done = False
        self._kf_markers_seen = 0
        # Dropped-chunk counter (#44). A full per-subscriber queue means we
        # silently discard stream data — which corrupts that viewer's feed and
        # looks exactly like stuttering/freezing. Counting it separates
        # HA-side backpressure (drops > 0: a consumer can't keep up) from
        # device-side starvation (drops == 0: the doorbell itself sends late).
        self._drops = 0
        # id(queue) -> monotonic time it first saturated, for dead-subscriber
        # eviction (#44). Cleared as soon as a chunk goes in cleanly.
        # id(queue) -> monotonic time its consumer last took a chunk. Seeded
        # on subscribe so a fresh viewer is never evicted before it starts.
        self._q_last_drain: dict[int, float] = {}
        # Video queues currently waiting for a keyframe after saturating.
        self._q_resync: set[int] = set()
        self._warned_saturation = False
        self._warned_scrambled = False
        # Payload decryption (#47): key + carry-over buffer. A NAL can span
        # several video PES packets, so only the "complete" prefix reported
        # by mpeg_ps_decryptable_prefix_length() may be decrypted per call.
        self._decrypt_key: Optional[str] = None
        self._decrypt_buf = bytearray()
        self._decrypt_failed = False
        # GOP cache (#37): the stream since the last keyframe. A new viewer
        # can only start painting from an IDR, and some doorbells emit
        # keyframes tens of seconds apart — replaying this to each new
        # subscriber makes the live view start immediately instead of
        # sitting blank until the next keyframe. Guarded by _gop_lock
        # (updated from the broadcast reader thread, read at subscribe).
        self._gop_buf = bytearray()
        # Most recent VPS/SPS/PPS seen, kept apart from the GOP buffer:
        # this firmware emits IDRs far more often than it repeats the
        # parameter sets, so a GOP that starts on an IDR still needs them
        # prepended before a decoder can use it (#51).
        self._param_sets: bytes = b""
        self._gop_lock = threading.Lock()
        # True while the active shared source is the CPD7 LAN pipeline.
        self._active_lan: bool = False
        self._idle_handle: Optional[asyncio.TimerHandle] = None
        self._active_clients: int = 0

    @property
    def port(self) -> int:
        return self._port

    @property
    def stream_url(self) -> str:
        return f"tcp://127.0.0.1:{self._port}"

    @property
    def detected_codec(self) -> Optional[str]:
        """Video codec sniffed off the live stream ("h264"/"hevc"), or None."""
        return self._detected_codec

    @property
    def active_source(self) -> str:
        """Which source is actually feeding the stream right now.

        Reflects reality rather than the configured option: with source=auto
        the relay may have fallen back cloud->local (or vice-versa), so report
        what's live. Falls back to the configured value when nothing is open.
        """
        if self._active_lan:
            return "local"
        if self._shared_vtm is not None:
            return "cloud"
        return self._stream_source

    def _es_dump(self, payload: bytes) -> None:
        """Collect the first 64 KB of video ES and write it once (#41).

        Debug-only (gated on the logger level). Runs on the reader thread,
        so the blocking file write is fine. The dump lets HPD5 users share
        exactly what sits in front of the NAL start codes.
        """
        if self._es_dump_done or not self._diagnostic_dumps:
            return
        self._es_dump_buf.extend(payload)
        if len(self._es_dump_buf) < 64 * 1024:
            return
        self._es_dump_done = True
        try:
            if self._hass is not None:
                path = self._hass.config.path(
                    f"ezviz_hp7_es_dump_{self._serial}.bin"
                )
            else:
                path = f"/tmp/ezviz_hp7_es_dump_{self._serial}.bin"
            with open(path, "wb") as fh:
                fh.write(self._es_dump_buf[: 64 * 1024])
            _LOGGER.info(
                "Hp7StreamRelay: wrote 64 KB video-ES diagnostic dump to %s "
                "(first bytes: %s)",
                path, self._es_dump_buf[:24].hex(" "),
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Hp7StreamRelay: ES dump write failed: %s", exc)
        finally:
            self._es_dump_buf = bytearray()

    def _fanout(
        self,
        qs: List["queue.Queue[Optional[bytes]]"],
        item: bytes,
        video: bool = False,
    ) -> None:
        """Broadcast ``item`` to subscribers, evicting dead ones (#44).

        A subscriber whose queue stays saturated for DEAD_SUB_TIMEOUT with
        nothing ever draining it has no live consumer — its client task
        failed to tear down. AnthoPakPak reported ``subs=9`` with only
        Frigate plus an occasional preview open, i.e. ~7 abandoned
        subscribers; since every subscriber costs a relay ffmpeg (plus an
        mjpeg ffmpeg per viewer), those leaked processes compete for the
        same CPU as the live one. Evicting them frees that up.
        """
        now = time.monotonic()
        kf = video and _has_keyframe(item)
        for q in list(qs):
            qid = id(q)
            if video and qid in self._q_resync:
                # Saturated earlier: skip everything until a keyframe so the
                # decoder resumes on a decodable boundary instead of being
                # handed a stream with holes punched mid-GOP (which freezes
                # the picture on the last good frame while audio, carried on
                # its own queue, keeps playing normally).
                if not kf:
                    self._drops += 1
                    continue
                self._q_resync.discard(qid)
            if not self._put_latest(q, item) and video:
                if not self._warned_saturation:
                    self._warned_saturation = True
                    _LOGGER.warning(
                        "Hp7StreamRelay: VIDEO QUEUE SATURATED (%d slots) — "
                        "the consumer is slower than the doorbell; dropping "
                        "to the next keyframe (serial=%s)",
                        VIDEO_QUEUE_SIZE, self._serial,
                    )
                # Drop the whole backlog and resync at the next keyframe:
                # keeps the viewer current AND decodable.
                self._q_resync.add(qid)
                try:
                    while True:
                        q.get_nowait()
                except queue.Empty:
                    pass
            # Liveness is "did the consumer take anything recently", NOT "is
            # the queue full". Under the drop-oldest policy a queue whose
            # consumer is even marginally slower than the producer sits full
            # permanently by design, so the previous saturation test evicted
            # perfectly healthy viewers after 30 s.
            last = self._q_last_drain.get(id(q))
            if last is None or now - last < DEAD_SUB_TIMEOUT:
                continue
            self._q_last_drain.pop(id(q), None)
            try:
                qs.remove(q)
            except ValueError:
                pass
            # Sentinel so the sender thread exits and the client tears down.
            try:
                q.put_nowait(None)
            except queue.Full:
                pass
            _LOGGER.warning(
                "Hp7StreamRelay: evicted a %s subscriber whose consumer took "
                "nothing for %.0fs (serial=%s, %d left)",
                "video" if video else "audio",
                DEAD_SUB_TIMEOUT, self._serial, len(qs),
            )

    def _put_latest(self, q: "queue.Queue[Optional[bytes]]", item: bytes) -> bool:
        """Enqueue ``item``, discarding the OLDEST chunk when the queue is full.

        Previously a full queue dropped the *newest* chunk and kept the stale
        backlog, so a viewer that fell behind once kept draining old data
        forever — the picture stuttered and the delay grew without bound
        (#44 AnthoPakPak, drops=22.8M). Dropping the oldest instead lets a
        saturated viewer skip forward to live data and resync at the next
        keyframe, which is the behaviour a live stream wants.

        Returns True if the chunk went in without evicting anything (i.e. the
        consumer is keeping up), False if it saturated.
        """
        try:
            q.put_nowait(item)
            return True
        except queue.Full:
            pass
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        try:
            q.put_nowait(item)
        except queue.Full:
            pass
        self._drops += 1
        return False

    def _ps_dump(self, body: bytes) -> None:
        """Collect the first 128 KB of raw (decrypted) MPEG-PS and write it
        once (#41). Debug-only. Complements the ES dump: if VPS/SPS exist
        here but not in the demuxed ES, the demux is stripping them; if they
        don't exist here either, the device delivers them out-of-band.
        """
        if self._ps_dump_done or not self._diagnostic_dumps:
            return
        self._ps_dump_buf.extend(body)
        if len(self._ps_dump_buf) < 128 * 1024:
            return
        self._ps_dump_done = True
        try:
            if self._hass is not None:
                path = self._hass.config.path(
                    f"ezviz_hp7_ps_dump_{self._serial}.bin"
                )
            else:
                path = f"/tmp/ezviz_hp7_ps_dump_{self._serial}.bin"
            with open(path, "wb") as fh:
                fh.write(self._ps_dump_buf[: 128 * 1024])
            _LOGGER.info(
                "Hp7StreamRelay: wrote 128 KB raw MPEG-PS diagnostic dump "
                "to %s",
                path,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Hp7StreamRelay: PS dump write failed: %s", exc)
        finally:
            self._ps_dump_buf = bytearray()

    def _gop_update(self, chunk: bytes) -> None:
        """Track the stream since the last random-access point (#37, #51).

        Called from the broadcast reader thread for every video-bearing
        chunk. The cache restarts whenever a chunk carries a point a
        decoder can start on — parameter sets or an IDR/CRA. Anything else
        is appended. Chunk boundaries are arbitrary, so the cache may lead
        with a partial pack or NAL — both the mpeg demuxer and the raw ES
        parsers resync on the next start code, and +discardcorrupt drops
        the remnant frame.

        Restarting on an IDR alone is not enough to hand a decoder, since
        it still needs VPS/SPS/PPS and this firmware does not repeat them
        with every IDR (#51). So the most recent parameter sets are kept
        separately and prepended at snapshot time.
        """
        with self._gop_lock:
            if _has_param_sets(chunk):
                extracted = _extract_param_sets(chunk)
                if extracted:
                    self._param_sets = extracted
            if _has_keyframe(chunk):
                self._kf_markers_seen += 1
                self._gop_buf = bytearray(chunk)
            elif self._gop_buf:
                if len(self._gop_buf) + len(chunk) > GOP_CACHE_MAX:
                    # Oversized GOP: drop and wait for the next keyframe —
                    # new viewers get the pre-cache behaviour for this GOP.
                    self._gop_buf = bytearray()
                else:
                    self._gop_buf.extend(chunk)

    def _gop_snapshot(self) -> List[bytes]:
        """Return a decodable GOP prefix as queue-sized chunks (#51).

        Preloading a segment the decoder cannot start on is worse than
        preloading nothing: ffmpeg burns through it emitting "Could not
        find ref with POC n" for every frame and shows no picture anyway,
        so the viewer waits for the next real keyframe regardless. Measured
        on an HPD7 (V5.4.0): 27 undecodable frames replayed in 31 ms, then
        10-12 s of black.

        So only hand back a buffer that starts on a random-access point,
        and prepend the last parameter sets when the buffer itself lacks
        them.
        """
        with self._gop_lock:
            buf = bytes(self._gop_buf)
            params = self._param_sets
        if not buf or not _has_keyframe(buf):
            return []
        if not _has_param_sets(buf):
            if not params:
                # An IDR with no parameter sets anywhere is not decodable.
                return []
            buf = params + buf
        return [buf[i:i + RELAY_CHUNK] for i in range(0, len(buf), RELAY_CHUNK)]

    def _maybe_decrypt(self, body: bytes) -> bytes:
        """Decrypt scrambled video payloads when the device encrypts them.

        Image/Video Encryption leaves MPEG-PS/PES and Annex B NAL framing in
        the clear and encrypts only the NAL bodies, which is why such a
        stream looks structurally perfect yet decodes to garbage. pyEzvizApi
        can undo it given the camera key, so on firmware where encryption
        can no longer be switched off at all (#47) this is the only way to
        get a picture. Returns the input unchanged when decryption isn't
        active or isn't possible.
        """
        if self._decrypt_key is None or self._decrypt_failed:
            return body
        try:
            from .pylocalapi.stream import (
                decrypt_hikvision_ps_video,
                mpeg_ps_decryptable_prefix_length,
            )

            self._decrypt_buf.extend(body)
            n = mpeg_ps_decryptable_prefix_length(bytes(self._decrypt_buf))
            if n <= 0:
                return b""
            chunk = bytes(self._decrypt_buf[:n])
            del self._decrypt_buf[:n]
            return decrypt_hikvision_ps_video(chunk, self._decrypt_key)
        except Exception as exc:  # noqa: BLE001
            self._decrypt_failed = True
            _LOGGER.error(
                "Hp7StreamRelay: stream decryption failed, passing through "
                "unchanged (serial=%s): %s", self._serial, exc,
            )
            return body

    def _enable_decryption(self) -> None:
        """Fetch the camera key so scrambled payloads can be decrypted."""
        if self._decrypt_key is not None or self._decrypt_failed:
            return
        key = self._configured_key
        if not key:
            try:
                key = self._api.get_camera_encryption_key(self._serial)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Hp7StreamRelay: camera key unavailable: %s", exc)
                key = None
        if not key:
            if self._serial_needs_otp():
                # The failed request above is what makes EZVIZ send the code,
                # so it is already in the user's inbox — and it dies in 30
                # minutes. Put a form in front of them rather than a log line
                # nobody reads (#47).
                _LOGGER.warning(
                    "Hp7StreamRelay: the stream is encrypted and EZVIZ has "
                    "just e-mailed the 4-digit code that unlocks it "
                    "(serial=%s). Enter it in Settings -> Repairs within 30 "
                    "minutes, or call ezviz_hp7.fetch_encryption_key with "
                    "it. The 6-character label code will NOT work here.",
                    self._serial,
                )
                if self._hass is not None:
                    try:
                        self._hass.loop.call_soon_threadsafe(
                            self._raise_otp_repair
                        )
                    except Exception:  # noqa: BLE001
                        pass
                return
            _LOGGER.warning(
                "Hp7StreamRelay: the stream is encrypted but no key is "
                "available (serial=%s). The cloud only hands the key out "
                "after 2FA elevation, so enter the device's 6-character "
                "verification code in Configure -> 'Encryption key' and it "
                "will be used directly.",
                self._serial,
            )
            return
        self._decrypt_key = key
        _LOGGER.warning(
            "Hp7StreamRelay: stream is encrypted — decrypting it with the "
            "camera key (serial=%s)", self._serial,
        )

    def _serial_needs_otp(self) -> bool:
        """True when the cloud asked for elevation on the last key request."""
        try:
            bare = self._api._bare_serial(self._serial)
            return bare in getattr(self._api, "key_needs_otp", set())
        except Exception:  # noqa: BLE001
            return False

    def _raise_otp_repair(self) -> None:
        """Repairs form asking for the code EZVIZ just mailed (loop thread)."""
        try:
            from homeassistant.helpers import issue_registry as ir

            ir.async_create_issue(
                self._hass,
                DOMAIN,
                f"encryption_otp_{self._serial}",
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="encryption_otp",
                translation_placeholders={"serial": self._serial},
                data={"serial": self._serial},
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Hp7StreamRelay: could not raise OTP repair: %s", exc)

    def _check_scrambled(self, parser: Any) -> None:
        """Tell the user when the device is scrambling the stream (#41).

        With Image/Video Encryption enabled the container and NAL framing
        stay perfectly readable while the payloads are scrambled, so ffmpeg
        spins on garbage and every symptom points at a demux bug. The stream
        announces the condition itself via PES_scrambling_control, so say so
        instead of letting people chase it — and note that a firmware or app
        update can silently re-enable encryption behind the user's back.
        """
        if self._warned_scrambled or parser.scrambled_packets < 8:
            return
        self._warned_scrambled = True
        _LOGGER.warning(
            "Hp7StreamRelay: the doorbell is SCRAMBLING the video "
            "(PES_scrambling_control set on %d packets, serial=%s). Image/"
            "Video Encryption is enabled on the device; looking for the key "
            "so the stream can be decrypted.",
            parser.scrambled_packets, self._serial,
        )
        self._enable_decryption()
        if self._decrypt_key is not None:
            # Decrypting, so no need to nag the user about the app setting.
            return
        if self._hass is not None:
            try:
                self._hass.loop.call_soon_threadsafe(self._raise_scrambled_repair)
            except Exception:  # noqa: BLE001
                pass

    def _raise_scrambled_repair(self) -> None:
        """Repairs notice for an encrypted stream (loop thread)."""
        try:
            from homeassistant.helpers import issue_registry as ir

            ir.async_create_issue(
                self._hass,
                DOMAIN,
                f"stream_encrypted_{self._serial}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="stream_encrypted",
                translation_placeholders={"serial": self._serial},
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Hp7StreamRelay: could not raise repair: %s", exc)

    def _warn_if_hevc_on_webrtc(self, codec: Optional[str]) -> None:
        """Log a one-shot warning when HEVC is seen on the WebRTC path.

        Browsers can't decode an HEVC copy over WebRTC, so go2rtc must
        transcode; on weak hosts that fails with a grey screen or
        'dial tcp ... connection refused' (#36 andresako). MJPEG mode
        sidesteps this entirely and is now the default — steer the user there.
        """
        if (
            codec == "hevc"
            and self._stream_mode == "webrtc"
            and not self._warned_hevc_webrtc
        ):
            self._warned_hevc_webrtc = True
            _LOGGER.warning(
                "Hp7StreamRelay: HEVC/H.265 detected while Stream mode is "
                "'webrtc' (serial=%s). Browsers can't show HEVC over WebRTC and "
                "go2rtc must transcode, which fails on weak hosts. Switch "
                "Stream mode to 'mjpeg' (Configure) for a codec-agnostic, "
                "go2rtc-free live view.",
                self._serial,
            )
            # Surface it in the UI (Settings -> Repairs), not just the log.
            # The sniff runs in the broadcast reader thread, so hop to the loop.
            if self._hass is not None:
                try:
                    self._hass.loop.call_soon_threadsafe(self._raise_hevc_repair)
                except Exception:  # noqa: BLE001
                    pass

    def _raise_hevc_repair(self) -> None:
        """Create a Repairs issue steering the user to MJPEG (loop thread)."""
        try:
            from homeassistant.helpers import issue_registry as ir

            ir.async_create_issue(
                self._hass,
                DOMAIN,
                f"hevc_webrtc_{self._serial}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="hevc_webrtc",
                translation_placeholders={"serial": self._serial},
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Hp7StreamRelay: could not raise HEVC repair: %s", exc)

    async def start(self) -> None:
        if self._server is not None:
            return
        # Try the configured fixed port first; fall back to a random free
        # one if it's taken so HA setup doesn't fail when (e.g.) the user
        # left a previous relay running.
        try:
            self._server = await asyncio.start_server(
                self._handle_client, self._bind_host, self._listen_port
            )
        except OSError as exc:
            if self._listen_port:
                _LOGGER.warning(
                    "Hp7StreamRelay: fixed port %d busy (%s); falling back to "
                    "a random one",
                    self._listen_port, exc,
                )
                self._server = await asyncio.start_server(
                    self._handle_client, self._bind_host, 0
                )
            else:
                raise
        sock = self._server.sockets[0]
        self._port = int(sock.getsockname()[1])
        _LOGGER.debug(
            "Hp7StreamRelay listening on tcp://%s:%d "
            "(serial=%s aggressive_mpegts=%s video_codec=%s)",
            self._bind_host, self._port, self._serial,
            self._aggressive_mpegts, self._video_codec,
        )
        if self._diagnostic_dumps:
            _LOGGER.warning(
                "Hp7StreamRelay: diagnostic dumps ENABLED — writing stream "
                "captures to the config dir (serial=%s). Turn the option off "
                "again once you have collected them.",
                self._serial,
            )
        if self._bind_host not in ("127.0.0.1", "localhost", "::1"):
            _LOGGER.warning(
                "Hp7StreamRelay: relay bound to %s — the raw stream on port "
                "%d is UNAUTHENTICATED; make sure only trusted hosts can "
                "reach it",
                self._bind_host, self._port,
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

    async def _open_source(self, loop, ezviz_client):
        """Open the configured stream source (cloud VTM or CPD7 LAN).

        Returns an object exposing ``start()`` / ``iter_payloads()`` /
        ``close()``. For "auto" the LAN path is tried first and the cloud
        VTM is used as fallback.
        """
        def _open_cloud():
            return open_cloud_stream(
                ezviz_client, self._serial, channel=self._channel
            )

        def _open_lan():
            return Cpd7LanSource(
                self._api, self._serial, channel=self._channel,
                stream_quality=self._stream_quality,
            )

        if self._stream_source == "cloud":
            return await loop.run_in_executor(None, _open_cloud)
        if self._stream_source == "local":
            return await loop.run_in_executor(None, _open_lan)
        # auto: prefer LAN, fall back to cloud on any LAN failure.
        try:
            src = await loop.run_in_executor(None, _open_lan)
            # Probe the LAN handshake now so a failure falls back to cloud
            # before we commit the broadcast reader to it.
            await loop.run_in_executor(None, src.start)
            return _PreStartedSource(src)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.info(
                "Hp7StreamRelay: LAN source unavailable (%s) — falling back "
                "to cloud VTM (serial=%s)", exc, self._serial,
            )
            return await loop.run_in_executor(None, _open_cloud)

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
                vtm = await self._open_source(loop, ezviz_client)
                info = await loop.run_in_executor(None, vtm.start)
                self._consecutive_failures = 0
                self._last_error = None
                _LOGGER.info(
                    "Hp7StreamRelay: pre-warm source up (serial=%s src=%s ssn=%s)",
                    self._serial,
                    self._stream_source,
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
            self._active_lan = isinstance(
                vtm, (Cpd7LanSource, _PreStartedSource)
            )
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
        # Fresh session — a cached GOP from the previous source is stale.
        with self._gop_lock:
            self._gop_buf = bytearray()
            self._param_sets = b""
        self._es_dump_buf = bytearray()
        self._es_dump_done = False
        self._ps_dump_buf = bytearray()
        self._ps_dump_done = False
        self._kf_markers_seen = 0
        self._drops = 0
        self._q_last_drain = {}
        self._q_resync = set()
        self._decrypt_buf = bytearray()
        self._warned_saturation = False
        self._warned_scrambled = False
        self._decrypt_buf = bytearray()
        v_bytes = a_bytes = 0
        # Time-based, not byte-based: at ~400 KB/s a 256 KB threshold emitted
        # an INFO line every ~0.6 s forever — a 24/7 Frigate consumer logged
        # tens of thousands of lines (#44, 20 GB session).
        next_v_log = next_a_log = time.monotonic() + PROGRESS_LOG_SEC
        try:
            for body in self._shared_vtm.iter_payloads():
                if self._shared_stop.is_set():
                    break
                if not body:
                    continue
                if self._active_lan:
                    # LAN path: the decoder emits complete MPEG-PS (muxed
                    # video + audio). Fan the raw feed out for the VIDEO
                    # (one ffmpeg `-f mpeg` input, demuxed internally) AND
                    # separately extract the AUDIO PES (0xC0) — the LAN
                    # MPEG-PS PMT mislabels the audio as MP2 but it's
                    # actually AAC ADTS (confirmed from a capture: ff f1
                    # ... = AAC LC 16 kHz mono). ffmpeg can't decode it as
                    # MP2, so we feed the extracted AAC to a second input
                    # instead. Video stays a clean raw passthrough; audio
                    # comes from the de-mislabelled PES.
                    body = self._maybe_decrypt(body)
                    if not body:
                        continue
                    v_bytes += len(body)
                    self._gop_update(body)
                    self._ps_dump(body)
                    self._fanout(self._sub_raw_qs, body, video=True)
                    for stream_id, payload in parser.feed(body):
                        if stream_id == AUDIO_STREAM_ID and payload:
                            a_bytes += len(payload)
                            self._fanout(self._sub_a_qs, payload)
                        elif stream_id == VIDEO_STREAM_ID and payload:
                            self._es_dump(payload)
                            if self._detected_codec is None:
                                # Sniff h264 vs hevc so "auto" knows whether
                                # to transcode on the LAN path too (CP7
                                # streams HEVC, which WebRTC can't show as a
                                # copy — #37 Quenbo).
                                guess = _sniff_video_codec(payload)
                                if guess is not None:
                                    self._detected_codec = guess
                                    _LOGGER.info(
                                        "Hp7StreamRelay: detected LAN video "
                                        "codec=%s (serial=%s)",
                                        guess, self._serial,
                                    )
                                    self._warn_if_hevc_on_webrtc(guess)
                    self._check_scrambled(parser)
                    if time.monotonic() >= next_v_log:
                        _LOGGER.info(
                            "Hp7StreamRelay: broadcast LAN MPEG-PS progress "
                            "%d B audio=%d B subs=%d kf_markers=%d drops=%d",
                            v_bytes, a_bytes, len(self._sub_raw_qs),
                            self._kf_markers_seen, self._drops,
                        )
                        next_v_log = time.monotonic() + PROGRESS_LOG_SEC
                    continue
                for stream_id, payload in parser.feed(body):
                    if not payload:
                        continue
                    if stream_id == VIDEO_STREAM_ID:
                        v_bytes += len(payload)
                        self._gop_update(payload)
                        self._es_dump(payload)
                        if self._detected_codec is None:
                            guess = _sniff_video_codec(payload)
                            if guess is not None:
                                self._detected_codec = guess
                                _LOGGER.info(
                                    "Hp7StreamRelay: detected video codec=%s "
                                    "(serial=%s)",
                                    guess, self._serial,
                                )
                                self._warn_if_hevc_on_webrtc(guess)
                        self._fanout(self._sub_v_qs, payload, video=True)
                        if time.monotonic() >= next_v_log:
                            _LOGGER.info(
                                "Hp7StreamRelay: broadcast video progress %d B "
                                "subs=%d drops=%d",
                                v_bytes,
                                len(self._sub_v_qs),
                                self._drops,
                            )
                            next_v_log = time.monotonic() + PROGRESS_LOG_SEC
                    elif stream_id == AUDIO_STREAM_ID:
                        a_bytes += len(payload)
                        self._fanout(self._sub_a_qs, payload)
                        if time.monotonic() >= next_a_log:
                            _LOGGER.info(
                                "Hp7StreamRelay: broadcast audio progress %d B "
                                "subs=%d",
                                a_bytes,
                                len(self._sub_a_qs),
                            )
                            next_a_log = time.monotonic() + PROGRESS_LOG_SEC
        except Exception as exc:
            # EBADF here is the expected race when the shared VTM session
            # is torn down by another thread (idle timeout or stop()) while
            # the reader is still parked in recv(). Demote to debug; only
            # surface unexpected exceptions at WARNING.
            errno_val = getattr(exc, "errno", None)
            if (
                self._shared_stop.is_set()
                or errno_val == 9  # EBADF
                or "Bad file descriptor" in str(exc)
            ):
                _LOGGER.debug(
                    "Hp7StreamRelay: broadcast reader stopped (expected "
                    "teardown): %s",
                    exc,
                )
            else:
                _LOGGER.warning(
                    "Hp7StreamRelay: broadcast reader stopped: %s", exc
                )
        finally:
            for q in (
                list(self._sub_v_qs)
                + list(self._sub_a_qs)
                + list(self._sub_raw_qs)
            ):
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
            maxsize=VIDEO_QUEUE_SIZE
        )
        a_q: "queue.Queue[Optional[bytes]]" = queue.Queue(
            maxsize=PAYLOAD_QUEUE_SIZE
        )
        raw_q: "queue.Queue[Optional[bytes]]" = queue.Queue(
            maxsize=VIDEO_QUEUE_SIZE
        )
        raw_listener: Optional[socket.socket] = None

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

        # Subscribe to the broadcast. LAN serves one muxed MPEG-PS feed;
        # cloud serves split video/audio PES streams.
        lan = self._active_lan
        # Preload the cached GOP (#37) so this viewer starts painting from
        # the last keyframe immediately instead of sitting blank until the
        # doorbell emits the next one (tens of seconds on a quiet scene).
        # Preload BEFORE subscribing: chunks land in order ahead of the live
        # feed; a queue.Full mid-preload truncates the replay and the viewer
        # just falls back to waiting for the next keyframe.
        gop = self._gop_snapshot()
        if lan:
            for chunk in gop:
                try:
                    raw_q.put_nowait(chunk)
                except queue.Full:
                    break
            now0 = time.monotonic()
            self._q_last_drain[id(raw_q)] = now0
            self._q_last_drain[id(a_q)] = now0
            self._sub_raw_qs.append(raw_q)
            self._sub_a_qs.append(a_q)  # de-mislabelled AAC audio
        else:
            for chunk in gop:
                try:
                    v_q.put_nowait(chunk)
                except queue.Full:
                    break
            now0 = time.monotonic()
            self._q_last_drain[id(v_q)] = now0
            self._q_last_drain[id(a_q)] = now0
            self._sub_v_qs.append(v_q)
            self._sub_a_qs.append(a_q)
        if gop:
            _LOGGER.debug(
                "Hp7StreamRelay: preloaded %d GOP chunks to new client",
                len(gop),
            )
        self._active_clients += 1
        self._arm_idle_timer()  # cancel idle teardown while we're connected

        # Wall-clock re-timestamping was added for #37: the device's
        # uptime-based DTS is already large and wraps the 33-bit MPEG-TS
        # clock within hours, which makes HA's stream worker abort with
        # "Timestamp discontinuity". But it paces the output by ARRIVAL
        # time instead of capture time, so any systematic pacing error
        # accumulates linearly and never recovers — the unbounded delay in
        # #44, which VLC saw too (i.e. upstream of the MJPEG path). Only
        # HA's stream worker actually needs it, so restrict it to WebRTC
        # and let +genpts derive timestamps from the stream everywhere else.
        wallclock = (
            ["-use_wallclock_as_timestamps", "1"]
            if self._stream_mode == "webrtc"
            else []
        )
        try:
            if lan:
                # LAN: one input, the muxed MPEG-PS feed. ffmpeg demuxes it
                # internally and copies video. Codec autodetected by the
                # mpeg demuxer, so no -f h264/hevc guessing.
                #
                # Audio is dropped (-an) for now. The LAN MPEG-PS tags audio
                # as MP2 but the frames have broken headers ("mp2 Header
                # missing", #36 bob) — it's almost certainly AAC/G.711
                # mislabelled, like the VTM cloud path. Undecodable MP2 can't
                # be re-encoded to the AAC that HA's WebRTC needs, and copying
                # raw MP2 doesn't play over WebRTC either, so we keep video
                # clean and stable and revisit audio once the real codec is
                # identified from a capture.
                raw_port = _free_local_port()
                a_port = _free_local_port()
                raw_listener = _start_local_listener(raw_port)
                a_listener = _start_local_listener(a_port)
                # Transcode HEVC -> H.264 so HA's WebRTC path shows a picture
                # (HEVC copy = grey screen / decoder errors, #36 hehsni, #37
                # Quenbo CP7). Transcode when the user forces hevc OR when
                # auto sniffed HEVC off the stream. hevc_copy / h264 pass the
                # elementary stream through untouched (HEVC-capable players,
                # Frigate, H.264 firmware).
                # In MJPEG mode the per-viewer ffmpeg decodes whatever codec
                # to JPEG itself, so transcoding HEVC->H.264 here is pure waste
                # (a double decode+encode) and its decoder was the source of
                # the "VPS 0 does not exist" errors on HPD7 (#39). Only the
                # WebRTC path actually needs H.264, so gate the transcode on it.
                lan_transcode = self._stream_mode == "webrtc" and (
                    self._video_codec == "hevc"
                    or (
                        self._video_codec == "auto"
                        and self._detected_codec == "hevc"
                    )
                )
                cmd = [
                    self._ffmpeg_path,
                    "-hide_banner", "-loglevel", "error",
                    # No +nobuffer on the input: nobuffer pins analyzeduration
                    # to 0, so ffmpeg gives up before it sees a full HEVC IDR
                    # with its VPS/SPS/PPS and can't determine the frame size
                    # ("Could not find codec parameters ... unspecified size",
                    # #39 HPD7). A large analyzeduration/probesize is a ceiling,
                    # not a fixed wait — it starts as soon as params are known.
                    "-fflags", "+genpts", "-flags", "low_delay",
                    "-analyzeduration", "10000000", "-probesize", "10000000",
                    # NO wall-clock re-timestamping on this input. It is a
                    # real MPEG-PS container that already carries correct
                    # timestamps; stamping by arrival instead collapses the
                    # timeline, because the doorbell delivers over TCP faster
                    # than realtime. Measured on a live capture: with
                    # wallclock consecutive frames land ~11 us apart (15
                    # frames inside one millisecond), so HA's stream worker
                    # sees zero-duration frames and cannot build segments —
                    # the picture freezes while the separately re-encoded
                    # audio keeps playing. With +genpts alone the same
                    # capture yields a clean 66.667 ms spacing (15 fps).
                    # Wallclock stays on the raw elementary inputs below and
                    # on the cloud path, which genuinely have no timestamps.
                    "-f", "mpeg",
                    "-i", f"tcp://127.0.0.1:{raw_port}",
                    "-analyzeduration", "200000", "-probesize", "200000",
                    *wallclock,
                    "-f", "aac",
                    "-i", f"tcp://127.0.0.1:{a_port}",
                    # Video from the raw MPEG-PS input (its mislabelled MP2
                    # audio is ignored, not mapped); audio from the extracted
                    # AAC input.
                    "-map", "0:v:0", "-map", "1:a:0",
                ]
                if lan_transcode:
                    cmd += [
                        "-c:v", "libx264",
                        "-preset", "ultrafast", "-tune", "zerolatency",
                        "-pix_fmt", "yuv420p",
                    ]
                else:
                    cmd += ["-c:v", "copy"]
                cmd += [
                    "-c:a", "aac", "-ar", "16000", "-ac", "1", "-b:a", "32k",
                    "-max_interleave_delta", "0",
                    "-mpegts_flags", "+resend_headers",
                    "-pat_period", "1",
                    "-sdt_period", "1",
                    "-f", "mpegts", "pipe:1",
                ]
            else:
                v_port = _free_local_port()
                a_port = _free_local_port()
                v_listener = _start_local_listener(v_port)
                a_listener = _start_local_listener(a_port)

                # Resolve the input codec + whether to transcode.
                #   h264       -> input h264, copy
                #   hevc       -> input hevc, transcode to h264 (WebRTC-safe)
                #   hevc_copy  -> input hevc, copy (low-power hosts, #36)
                #   auto       -> sniff; hevc gets transcoded, else copy
                if self._video_codec in ("h264", "hevc", "hevc_copy"):
                    cfg = self._video_codec
                elif self._detected_codec == "hevc":
                    cfg = "hevc"
                else:
                    cfg = "h264"
                in_is_hevc = cfg in ("hevc", "hevc_copy")
                transcode = cfg == "hevc"
                in_fmt = "hevc" if in_is_hevc else "h264"

                cmd = [
                    self._ffmpeg_path,
                    "-hide_banner", "-loglevel", "error",
                    "-fflags", "+genpts+nobuffer", "-flags", "low_delay",
                    "-analyzeduration", "200000", "-probesize", "200000",
                    *wallclock,
                    "-f", in_fmt, "-r", "15",
                    "-i", f"tcp://127.0.0.1:{v_port}",
                    "-analyzeduration", "200000", "-probesize", "200000",
                    *wallclock,
                    "-f", "aac",
                    "-i", f"tcp://127.0.0.1:{a_port}",
                    "-map", "0:v:0", "-map", "1:a:0",
                ]
                if transcode:
                    # HEVC->H.264: HA's go2rtc/WebRTC path can't hand H.265 to
                    # most browsers, so a copy leaves a grey screen (#36, #37).
                    # zerolatency keeps the relay live. Heavy on weak CPUs —
                    # hevc_copy skips this for hosts that can't afford it.
                    cmd += [
                        "-c:v", "libx264",
                        "-preset", "ultrafast", "-tune", "zerolatency",
                        "-pix_fmt", "yuv420p",
                    ]
                else:
                    cmd += ["-c:v", "copy"]
                cmd += [
                    # Re-encode audio so the mpegts muxer gets a proper AAC
                    # AudioSpecificConfig extradata (inbound ADTS strips it).
                    "-c:a", "aac", "-ar", "16000", "-ac", "1", "-b:a", "32k",
                    "-max_interleave_delta", "0",
                ]
                # Opt-in: re-prepend SPS/PPS in front of every IDR for
                # firmwares that only emit them at the first IDR (#33).
                # Skipped on transcode (libx264 already inlines them).
                if self._aggressive_mpegts and not transcode:
                    cmd += ["-bsf:v", "dump_extra"]
                cmd += [
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

            # Reader is the broadcast (shared source); only senders are
            # per-client. LAN feeds a single muxed socket; cloud feeds two.
            if lan:
                raw_sender_t = threading.Thread(
                    target=_sender_thread,
                    args=(raw_listener, raw_q, stop_event, "mpegps", _mk_drain(self._q_last_drain, raw_q)),
                    name=f"hp7-lan-send-{self._serial}",
                    daemon=True,
                )
                a_sender_t = threading.Thread(
                    target=_sender_thread,
                    args=(a_listener, a_q, stop_event, "audio", _mk_drain(self._q_last_drain, a_q)),
                    name=f"hp7-lan-asend-{self._serial}",
                    daemon=True,
                )
                threads = [raw_sender_t, a_sender_t]
            else:
                v_sender_t = threading.Thread(
                    target=_sender_thread,
                    args=(v_listener, v_q, stop_event, "video", _mk_drain(self._q_last_drain, v_q)),
                    name=f"hp7-vtm-vsend-{self._serial}",
                    daemon=True,
                )
                a_sender_t = threading.Thread(
                    target=_sender_thread,
                    args=(a_listener, a_q, stop_event, "audio", _mk_drain(self._q_last_drain, a_q)),
                    name=f"hp7-vtm-asend-{self._serial}",
                    daemon=True,
                )
                threads = [v_sender_t, a_sender_t]
            for t in threads:
                t.start()

            assert proc.stdout is not None
            got_output = False
            while True:
                try:
                    data = await asyncio.wait_for(
                        proc.stdout.read(RELAY_CHUNK),
                        timeout=(
                            OUTPUT_STALL_TIMEOUT
                            if got_output
                            else FIRST_OUTPUT_TIMEOUT
                        ),
                    )
                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "Hp7StreamRelay: relay ffmpeg produced no output for "
                        "%.0fs (serial=%s, got_output=%s) — closing session",
                        OUTPUT_STALL_TIMEOUT if got_output
                        else FIRST_OUTPUT_TIMEOUT,
                        self._serial, got_output,
                    )
                    break
                if not data:
                    break
                got_output = True
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
            for q in (v_q, a_q, raw_q):
                try:
                    q.put_nowait(None)
                except queue.Full:
                    pass
            for qs, q in (
                (self._sub_v_qs, v_q),
                (self._sub_a_qs, a_q),
                (self._sub_raw_qs, raw_q),
            ):
                try:
                    qs.remove(q)
                except ValueError:
                    pass
            for lst in (v_listener, a_listener, raw_listener):
                if lst is not None:
                    try:
                        lst.close()
                    except OSError:
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

    def __init__(
        self,
        serial: str,
        model: str,
        relay: Hp7StreamRelay,
        stream_mode: str = "mjpeg",
        ffmpeg_path: str = "ffmpeg",
    ) -> None:
        super().__init__()
        self._serial = serial
        self._model = model
        self._relay = relay
        self._stream_mode = (stream_mode or "mjpeg").lower()
        self._ffmpeg_path = ffmpeg_path
        self._attr_unique_id = f"{DOMAIN}_{serial}_live"

    @property
    def device_info(self) -> DeviceInfo:
        from .device_info import make_device_info
        return make_device_info(self._serial, self._model)

    @property
    def supported_features(self) -> int:
        from homeassistant.components.camera import CameraEntityFeature

        # In MJPEG mode we don't advertise STREAM: HA would otherwise try to
        # run its HLS/WebRTC pipeline (go2rtc) instead of calling our
        # handle_async_mjpeg_stream. The MJPEG path is codec-agnostic and
        # avoids the go2rtc/HEVC issues entirely.
        if self._stream_mode == "mjpeg":
            return CameraEntityFeature(0)
        return CameraEntityFeature.STREAM

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Surface what's actually driving the live view (source/mode/codec).

        Lets the user see — and put an on-screen badge for — whether they're
        on local vs cloud, WebRTC vs MJPEG, and H.264 vs HEVC, which is
        otherwise invisible with source=auto / mode=auto.
        """
        codec = self._relay.detected_codec
        if not codec:
            cfg = self._relay._video_codec
            codec = cfg if cfg and cfg != "auto" else "unknown"
        return {
            "stream_source": self._relay.active_source,
            "stream_mode": self._stream_mode,
            "video_codec": codec,
        }

    async def stream_source(self) -> Optional[str]:
        if self._stream_mode == "mjpeg":
            return None
        if self._relay.port == 0:
            return None
        return self._relay.stream_url

    async def handle_async_mjpeg_stream(self, request):
        """Serve a per-viewer MJPEG transcode of the live stream.

        Used when stream_mode == 'mjpeg' — ffmpeg decodes whatever codec
        the relay carries (H.264 or HEVC) into motion-JPEG, sidestepping
        go2rtc/WebRTC and the HEVC grey-screen. Falls back to the base
        implementation otherwise.
        """
        if self._stream_mode != "mjpeg":
            return await super().handle_async_mjpeg_stream(request)
        # Make sure the relay (and its shared upstream) is warm before we
        # point ffmpeg at it.
        try:
            await self._relay.prewarm()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Hp7LiveCamera: mjpeg prewarm failed: %s", exc)
        if self._relay.port == 0:
            return None
        from .mjpeg import serve_mjpeg

        return await serve_mjpeg(
            request,
            ffmpeg_path=self._ffmpeg_path,
            upstream_url=self._relay.stream_url,
        )

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

    aggressive_mpegts = bool(data.get("aggressive_mpegts", False))
    video_codec = str(data.get("video_codec") or "auto")
    stream_source = str(data.get("stream_source") or "cloud")
    stream_mode = str(data.get("stream_mode") or "auto")

    relay = Hp7StreamRelay(
        api=api,
        serial=serial,
        channel=1,
        listen_port=relay_port,
        bind_host=str(data.get("relay_bind") or "127.0.0.1"),
        aggressive_mpegts=aggressive_mpegts,
        video_codec=video_codec,
        stream_source=stream_source,
        stream_mode=stream_mode,
        stream_quality=str(data.get("stream_quality") or "main"),
        diagnostic_dumps=bool(data.get("diagnostic_dumps", False)),
        encryption_key=str(data.get("encryption_key") or ""),
        hass=hass,
    )
    await relay.start()
    data["live_relay"] = relay
    # Let the coordinator see the relay so it can back polling off while
    # someone is watching the LAN stream (#44).
    coordinator = data.get("coordinator")
    if coordinator is not None:
        coordinator.live_relay = relay

    # "auto": probe the actual video codec once and pick the delivery mode so
    # users don't have to know it — H.264 → webrtc (audio + low latency),
    # HEVC → mjpeg (WebRTC can't show HEVC). This avoids the recurring "grey
    # screen / no video" reports on HEVC firmware. The probe reuses the same
    # prewarmed session the first viewer will use, so it isn't wasted; if the
    # codec can't be determined (device offline / cloud-only firmware that
    # never emits), we fall back to mjpeg, which always works.
    effective_mode = stream_mode
    if stream_mode == "auto":
        effective_mode = await _resolve_auto_stream_mode(relay)
        relay._stream_mode = effective_mode
        data["stream_mode_effective"] = effective_mode

    # Only WebRTC can hit the HEVC-can't-play situation; clear stale repairs
    # for every other resolved mode.
    if effective_mode != "webrtc":
        try:
            from homeassistant.helpers import issue_registry as ir

            ir.async_delete_issue(hass, DOMAIN, f"hevc_webrtc_{serial}")
        except Exception:  # noqa: BLE001
            pass

    async_add_entities(
        [Hp7LiveCamera(serial, model, relay, stream_mode=effective_mode)]
    )


async def _resolve_auto_stream_mode(relay: "Hp7StreamRelay") -> str:
    """Probe the video codec and map it to a delivery mode.

    Returns "webrtc" for H.264, "mjpeg" for HEVC or when the codec can't be
    sniffed within the timeout (mjpeg is the safe, codec-agnostic default).
    """
    from .const import STREAM_MODE_MJPEG, STREAM_MODE_WEBRTC

    try:
        await relay.prewarm()
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Hp7StreamRelay: auto-probe prewarm failed: %s", exc)
        return STREAM_MODE_MJPEG

    # Poll up to ~10 s for the broadcast reader to sniff the first keyframe.
    codec: Optional[str] = None
    for _ in range(50):
        codec = relay.detected_codec
        if codec is not None:
            break
        await asyncio.sleep(0.2)

    mode = STREAM_MODE_WEBRTC if codec == "h264" else STREAM_MODE_MJPEG
    _LOGGER.info(
        "Hp7StreamRelay: auto stream mode — detected codec=%s → %s (serial=%s)",
        codec or "unknown", mode, relay._serial,
    )
    return mode


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
