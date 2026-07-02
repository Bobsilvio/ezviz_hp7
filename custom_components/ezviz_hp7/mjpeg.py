"""Low-latency MJPEG live view via a per-viewer ffmpeg JPEG transcode.

The relay already exposes the live stream at ``tcp://127.0.0.1:N`` as
MPEG-TS. When the user picks ``mjpeg`` as the live-view mode, the camera
entity overrides ``handle_async_mjpeg_stream`` to spawn one ffmpeg that
reads that URL, decodes whatever video codec is inside (H.264 *or* HEVC),
re-encodes to motion-JPEG, and pipes the multipart output straight to the
browser.

Why MJPEG: it's **codec-agnostic** (decoding to JPEG sidesteps the HEVC
grey-screen and the go2rtc "Producer missing url" / multi-viewer problems
of the WebRTC path entirely) and bypasses HA's HLS muxer (no 6-9 s
segment buffering), at the cost of one ffmpeg per active viewer and no
audio. The WebRTC/HLS path stays available for users who want audio.

This MJPEG approach (and the ffmpeg tuning below) is adapted from
albrzmr's fork — https://github.com/albrzmr/ezviz_hp7 — with thanks.
"""

from __future__ import annotations

import asyncio
import logging
import time

from aiohttp import web

_LOGGER = logging.getLogger(__name__)

# ffmpeg's mpjpeg muxer uses the literal boundary "ffmpeg" by default.
_BOUNDARY = "ffmpeg"
_CONTENT_TYPE = f"multipart/x-mixed-replace; boundary={_BOUNDARY}"

_FFMPEG_GRACE = 2.0


def _build_ffmpeg_cmd(
    ffmpeg_path: str,
    upstream_url: str,
    *,
    fps: int,
    width: int,
    height: int,
    quality: int,
) -> list[str]:
    """ffmpeg that reads the relay's MPEG-TS, drops audio, decodes the
    video (H.264 or HEVC) and emits multipart MJPEG to stdout.

    ``-fflags +discardcorrupt`` drops reference-less frames instead of
    rendering them, which is what painted the first couple of seconds grey
    on the LAN path (decoding started on pre-keyframe slack). Credit
    albrzmr for the tuning.

    ``-analyzeduration`` / ``-probesize`` are set explicitly and large:
    HPD7 HEVC starts mid-GOP and ffmpeg needs to see a full IDR with its
    VPS/SPS/PPS parameter sets before it can determine the frame size
    (#39 alex66a-hub — "Could not find codec parameters ... unspecified
    size"). We must NOT use ``-fflags +nobuffer`` here: nobuffer forces
    analyzeduration to 0, so ffmpeg gives up before the first keyframe.
    These are a *ceiling*, not a fixed wait — ffmpeg starts emitting as
    soon as it has decoded the parameter sets.
    """
    return [
        ffmpeg_path,
        "-loglevel", "warning",
        "-analyzeduration", "10000000",
        "-probesize", "10000000",
        "-f", "mpegts",
        "-fflags", "+discardcorrupt",
        "-i", upstream_url,
        "-an",
        "-c:v", "mjpeg",
        "-q:v", str(quality),
        "-r", str(fps),
        "-vf", f"scale={width}:{height}",
        "-f", "mpjpeg",
        "pipe:1",
    ]


async def serve_mjpeg(
    request: web.Request,
    *,
    ffmpeg_path: str,
    upstream_url: str,
    fps: int = 8,
    width: int = 1280,
    height: int = 720,
    quality: int = 5,
) -> web.StreamResponse:
    """Stream a continuous MJPEG response back to the HTTP client.

    Spawns one ffmpeg that pulls from ``upstream_url`` (the relay) and
    forwards its stdout to the response body until either side closes.
    """
    cmd = _build_ffmpeg_cmd(
        ffmpeg_path, upstream_url,
        fps=fps, width=width, height=height, quality=quality,
    )
    started_at = time.monotonic()
    peer = request.remote
    _LOGGER.info(
        "[MJPEG] session START client=%s upstream=%s (%dx%d @ %dfps q=%d)",
        peer, upstream_url, width, height, fps, quality,
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": _CONTENT_TYPE,
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Connection": "close",
        },
    )
    await response.prepare(request)

    stderr_task = asyncio.create_task(_drain_stderr(proc))
    total_bytes = 0
    end_reason = "ok"
    try:
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(64 * 1024)
            if not chunk:
                end_reason = "ffmpeg_eof"
                break
            total_bytes += len(chunk)
            try:
                await response.write(chunk)
            except (ConnectionResetError, ConnectionAbortedError):
                end_reason = "client_disconnected"
                break
    except asyncio.CancelledError:
        end_reason = "cancelled"
        raise
    except Exception as exc:  # noqa: BLE001
        end_reason = f"error:{type(exc).__name__}"
        _LOGGER.warning(
            "[MJPEG] unexpected session error: %s: %s", type(exc).__name__, exc
        )
        raise
    finally:
        await _terminate(proc)
        stderr_task.cancel()
        try:
            await stderr_task
        except asyncio.CancelledError:
            pass
        try:
            await response.write_eof()
        except (ConnectionResetError, ConnectionAbortedError):
            pass
        duration = time.monotonic() - started_at
        _LOGGER.info(
            "[MJPEG] session END client=%s duration=%.1fs bytes=%d reason=%s",
            peer, duration, total_bytes, end_reason,
        )
    return response


async def _drain_stderr(proc: asyncio.subprocess.Process) -> None:
    if proc.stderr is None:
        return
    try:
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode(errors="replace").rstrip()
            if text:
                _LOGGER.debug("ffmpeg(mjpeg): %s", text)
    except asyncio.CancelledError:
        return


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=_FFMPEG_GRACE)
    except (TimeoutError, asyncio.TimeoutError):
        try:
            proc.kill()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=_FFMPEG_GRACE)
        except (TimeoutError, asyncio.TimeoutError):
            pass
