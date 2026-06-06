#!/usr/bin/env python3
"""Standalone tester for the VTM cloud relay path (TCP / MPEG-PS).

The HP7 is a NAT-bound consumer doorbell that does NOT register on the
Hikvision UDP P2P cloud. The official EZVIZ app streams it through the
VTM relay: a TCP ysproto session delivering MPEG-PS. Inside, the PES
carries H.264 (stream_id 0xE0) and AAC ADTS audio mis-labelled as MP2
(stream_id 0xC0).

This CLI parses the MPEG-PS in Python, hands the video and audio
elementary streams to ffmpeg via two local TCP sockets (one per input,
ffmpeg opens them as clients in parallel — FIFOs deadlock because
ffmpeg probes the first input synchronously before opening the second),
and exposes a clean MPEG-TS (H.264 + AAC) on a third local TCP port so
VLC / ffplay can attach:

    cd /path/to/ezviz_hp7
    python3 tools/hp7_vtm.py \
        --account YOUR_EZVIZ_EMAIL \
        --password YOUR_PASSWORD \
        --region eu \
        --serial BE7062577-BE6963574

Then open VLC -> File -> Open Network Stream -> tcp://127.0.0.1:<port>.

Credentials can come from EZVIZ_ACCOUNT / EZVIZ_PASSWORD / EZVIZ_REGION
/ EZVIZ_SERIAL env vars so the password never lands in shell history.

Requirements: ffmpeg in $PATH; the Python deps the integration already
needs (requests, pycryptodome, cryptography, xmltodict, paho-mqtt,
pandas).
"""
from __future__ import annotations

import argparse
import logging
import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components" / "ezviz_hp7"))

from _pes import PesParser  # noqa: E402
from pylocalapi.client import EzvizClient  # noqa: E402
from pylocalapi.cloud_stream import open_cloud_stream  # noqa: E402

REGION_URLS = {
    "eu": "apiieu.ezvizlife.com",
    "us": "apiisa.ezvizlife.com",
    "cn": "apiicn.ezvizlife.com",
    "as": "apiias.ezvizlife.com",
    "sa": "apiisa.ezvizlife.com",
    "ru": "apirus.ezvizru.com",
}

VIDEO_STREAM_ID = 0xE0
AUDIO_STREAM_ID = 0xC0


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _accept_one(server_sock: socket.socket, label: str) -> socket.socket:
    """Block until ffmpeg connects to our local listener; return the conn socket."""
    conn, peer = server_sock.accept()
    logging.info("hp7_vtm: %s accepted from %s", label, peer)
    return conn


def _start_listener(port: int) -> socket.socket:
    """Create a listening TCP socket on 127.0.0.1:port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


def _reader_thread(
    vtm,
    v_q: "queue.Queue[Optional[bytes]]",
    a_q: "queue.Queue[Optional[bytes]]",
    stop: threading.Event,
) -> None:
    """Drain VTM payloads → PES split → push per-stream bytes into queues.

    Runs independently of the per-stream senders so the reader doesn't
    stall on a single slow consumer (ffmpeg probes the first input
    synchronously before opening the second, so video must keep flowing
    while we're still waiting for ffmpeg to connect to the audio input).
    """
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
        logging.warning("hp7_vtm: reader loop error: %s", exc)
    finally:
        # Sentinels so the senders unblock.
        for q in (v_q, a_q):
            try:
                q.put_nowait(None)
            except queue.Full:
                pass
        logging.info(
            "hp7_vtm: reader done; video=%d B audio=%d B yielded=%d resync_drops=%d",
            v_bytes,
            a_bytes,
            parser.packets_yielded,
            parser.resync_drops,
        )


def _sender_thread(
    listener: socket.socket,
    q: "queue.Queue[Optional[bytes]]",
    stop: threading.Event,
    label: str,
) -> None:
    """Wait for ffmpeg to connect, then forward the queue into that socket."""
    try:
        listener.settimeout(20.0)
        try:
            conn, peer = listener.accept()
        except socket.timeout:
            logging.warning("hp7_vtm: %s accept timed out", label)
            return
        logging.info("hp7_vtm: %s accepted from %s", label, peer)
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
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                logging.warning("hp7_vtm: %s send error: %s", label, exc)
                return
    finally:
        logging.info("hp7_vtm: %s sender done (%d B sent)", label, sent)
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--account", default=os.environ.get("EZVIZ_ACCOUNT"))
    p.add_argument("--password", default=os.environ.get("EZVIZ_PASSWORD"))
    p.add_argument(
        "--region",
        default=os.environ.get("EZVIZ_REGION", "eu"),
        choices=sorted(REGION_URLS),
    )
    p.add_argument("--serial", default=os.environ.get("EZVIZ_SERIAL"))
    p.add_argument("--channel", type=int, default=1)
    p.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local TCP output port for VLC; default = pick a free one",
    )
    p.add_argument(
        "--ffmpeg",
        default=shutil.which("ffmpeg") or "ffmpeg",
        help="Path to ffmpeg binary",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    missing = [
        name
        for name, val in (
            ("account", args.account),
            ("password", args.password),
            ("serial", args.serial),
        )
        if not val
    ]
    if missing:
        print(
            f"Missing required arg(s): {', '.join(missing)}.\n"
            "Pass them via --account / --password / --serial or env vars.",
            file=sys.stderr,
        )
        return 2

    host = REGION_URLS.get(args.region) or REGION_URLS["eu"]

    print(f"[hp7_vtm] EzvizClient login → {host}")
    client = EzvizClient(account=args.account, password=args.password, url=host)
    try:
        client.login()
    except Exception as exc:
        print(f"[hp7_vtm] login FAIL: {exc}", file=sys.stderr)
        return 1
    print("[hp7_vtm] login OK")

    print(f"[hp7_vtm] open_cloud_stream(serial={args.serial}, channel={args.channel})…")
    try:
        vtm = open_cloud_stream(client, args.serial, channel=args.channel)
        info = vtm.start()
    except Exception as exc:
        print(f"[hp7_vtm] VTM bootstrap FAIL: {exc}", file=sys.stderr)
        try:
            client.logout()
        except Exception:
            pass
        return 1
    print(f"[hp7_vtm] VTM up: ssn={info.streamssn!r}")

    # Three TCP ports: video input, audio input, MPEG-TS output for VLC.
    v_port = _free_port()
    a_port = _free_port()
    out_port = args.port or _free_port()

    v_listener = _start_listener(v_port)
    a_listener = _start_listener(a_port)

    cmd = [
        args.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "info",
        "-fflags",
        "+genpts+nobuffer",
        "-flags",
        "low_delay",
        "-analyzeduration",
        "200000",
        "-probesize",
        "200000",
        "-use_wallclock_as_timestamps",
        "1",
        "-f",
        "h264",
        "-r",
        "15",
        "-i",
        f"tcp://127.0.0.1:{v_port}",
        "-analyzeduration",
        "200000",
        "-probesize",
        "200000",
        "-use_wallclock_as_timestamps",
        "1",
        "-f",
        "aac",
        "-i",
        f"tcp://127.0.0.1:{a_port}",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        # Re-encode audio so the mpegts muxer gets proper AAC extradata in
        # the PMT (the inbound ADTS stream loses its AudioSpecificConfig when
        # passed through "copy"). Cost is trivial for a 16 kHz mono talk
        # stream.
        "-c:a",
        "aac",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-b:a",
        "32k",
        "-max_interleave_delta",
        "0",
        "-f",
        "mpegts",
        f"tcp://127.0.0.1:{out_port}?listen=1",
    ]

    print(f"[hp7_vtm] Spawning ffmpeg")
    print(f"[hp7_vtm]   video input  -> tcp://127.0.0.1:{v_port} (Python listen)")
    print(f"[hp7_vtm]   audio input  -> tcp://127.0.0.1:{a_port} (Python listen)")
    print(f"[hp7_vtm]   output       -> tcp://127.0.0.1:{out_port} (ffmpeg listen)")
    print()
    print(f"  Open VLC:  tcp://127.0.0.1:{out_port}")
    print(f"  Or:        ffplay -fflags +nobuffer tcp://127.0.0.1:{out_port}")
    print()
    print("Wait ~3 s after seeing 'Output #0 to tcp://...' from ffmpeg.")
    print("Press Ctrl-C to stop.")
    print()

    proc = subprocess.Popen(cmd, stderr=sys.stderr)

    stop = threading.Event()
    v_q: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=128)
    a_q: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=128)

    reader_t = threading.Thread(
        target=_reader_thread, args=(vtm, v_q, a_q, stop), daemon=True
    )
    video_sender_t = threading.Thread(
        target=_sender_thread, args=(v_listener, v_q, stop, "video"), daemon=True
    )
    audio_sender_t = threading.Thread(
        target=_sender_thread, args=(a_listener, a_q, stop, "audio"), daemon=True
    )
    reader_t.start()
    video_sender_t.start()
    audio_sender_t.start()

    def _on_signal(*_) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    rc = 0
    try:
        while reader_t.is_alive() and proc.poll() is None:
            time.sleep(0.5)
            if stop.is_set():
                break
        if proc.poll() is None:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception as exc:
        print(f"[hp7_vtm] ERROR: {exc}", file=sys.stderr)
        rc = 1
    finally:
        stop.set()
        try:
            vtm.close()
        except Exception:
            pass
        try:
            client.logout()
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
    print("[hp7_vtm] done.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
