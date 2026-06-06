#!/usr/bin/env python3
"""Standalone tester for the VTM cloud relay path (TCP / MPEG-PS).

The HP7 is a NAT-bound consumer doorbell that does NOT register on the
Hikvision UDP P2P cloud — the P2P_SETUP responses always come back as bare
ClientID acks (no 0xFF sub-TLV, no device port). The official EZVIZ app
streams it through the VTM relay (cloud TCP transport, MPEG-PS payload),
which is what Renier's pyEzvizApi already implements and which the vendored
pylocalapi/cloud_stream + pylocalapi/stream snapshot in this repo carries.

This CLI exercises that exact path:

    cd /path/to/ezviz_hp7
    python3 tools/hp7_vtm.py \
        --account YOUR_EZVIZ_EMAIL \
        --password YOUR_PASSWORD \
        --region eu \
        --serial BE7062577-BE6963574

Then open VLC -> File -> Open Network Stream -> tcp://127.0.0.1:<port>
(the port is printed once ffmpeg starts listening).

Credentials can also come from EZVIZ_ACCOUNT / EZVIZ_PASSWORD /
EZVIZ_REGION / EZVIZ_SERIAL env vars so the password never lands in shell
history.

Requirements:
- ffmpeg in $PATH
- Python deps already used by the integration: requests, pycryptodome,
  cryptography, xmltodict, paho-mqtt, pandas (the pylocalapi snapshot
  imports several of these at module load).
"""
from __future__ import annotations

import argparse
import logging
import os
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

# Make the integration package importable when run from the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components" / "ezviz_hp7"))

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


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _feed_loop(vtm, proc: subprocess.Popen, stop: threading.Event) -> int:
    sent = 0
    assert proc.stdin is not None
    try:
        for body in vtm.iter_payloads():
            if stop.is_set():
                break
            if not body:
                continue
            try:
                proc.stdin.write(body)
                proc.stdin.flush()
                sent += len(body)
            except (BrokenPipeError, ValueError):
                break
    finally:
        try:
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass
    return sent


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
        help="Local TCP port; default = pick a free one",
    )
    p.add_argument(
        "--ffmpeg",
        default=shutil.which("ffmpeg") or "ffmpeg",
        help="Path to ffmpeg binary",
    )
    p.add_argument(
        "--input-format",
        default="mpeg",
        choices=("mpeg", "mpegts", "hevc"),
        help="ffmpeg -f for the stdin payload (mpeg=MPEG-PS, default)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s: %(message)s",
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
            "Pass them via --account / --password / --serial or env vars "
            "EZVIZ_ACCOUNT / EZVIZ_PASSWORD / EZVIZ_SERIAL.",
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
    print(f"[hp7_vtm] login OK, sessionId={(client._token.get('session_id') or '')[:12]}…")

    print(f"[hp7_vtm] open_cloud_stream(serial={args.serial}, channel={args.channel})…")
    try:
        vtm = open_cloud_stream(client, args.serial, channel=args.channel)
    except Exception as exc:
        print(f"[hp7_vtm] open_cloud_stream FAIL: {exc}", file=sys.stderr)
        try:
            client.logout()
        except Exception:
            pass
        return 1

    print(f"[hp7_vtm] VTM stream URL bootstrapped: {vtm.stream_url}")
    print(f"[hp7_vtm] vtm.start()… (handshake + redirect chain)")
    try:
        info = vtm.start()
    except Exception as exc:
        print(f"[hp7_vtm] vtm.start() FAIL: {exc}", file=sys.stderr)
        try:
            vtm.close()
        except Exception:
            pass
        try:
            client.logout()
        except Exception:
            pass
        return 1
    print(f"[hp7_vtm] StreamInfoRsp: result={info.result} streamssn={info.streamssn!r}")

    port = args.port or _free_port()
    cmd = [
        args.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "info",
        "-fflags",
        "+genpts+nobuffer",
        "-flags",
        "low_delay",
        "-f",
        args.input_format,
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
        f"tcp://127.0.0.1:{port}?listen=1",
    ]
    print(f"[hp7_vtm] Spawning ffmpeg → tcp://127.0.0.1:{port}")
    print()
    print(f"  Open VLC:  tcp://127.0.0.1:{port}")
    print(f"  Or:        ffplay -fflags +nobuffer tcp://127.0.0.1:{port}")
    print()
    print("Press Ctrl-C to stop.")
    print()

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    stop = threading.Event()
    feed_thread = threading.Thread(target=_feed_loop, args=(vtm, proc, stop), daemon=True)
    feed_thread.start()

    def _on_signal(*_) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    rc = 0
    try:
        while feed_thread.is_alive() and proc.poll() is None:
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
