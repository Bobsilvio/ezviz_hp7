#!/usr/bin/env python3
"""Video (H.264 elementary stream) extractor for HP7 / CP5 / CP7.

Pulls H.264 PES payloads (stream_id 0xE0) from either the VTM cloud relay
(default) or the CPD7 LAN stream into a raw .h264 file we can analyse offline.
Used to triage issue #33: when ffmpeg can't find SPS/PPS we need to see whether
the elementary stream actually contains them at all, or whether the firmware
(CP5, some HP7 builds) only sends them once before HA's stream worker attaches.

Usage (cloud, default):
    cd /path/to/ezviz_hp7
    python3 tools/hp7_video_dump.py \
        --account YOUR_EZVIZ_EMAIL \
        --password YOUR_PASSWORD \
        --region eu \
        --serial BEXXXXXXXX-BEXXXXXXXX \
        --seconds 20 \
        --output /tmp/hp7_video.h264

Usage (local CPD7 LAN stream):
    python3 tools/hp7_video_dump.py \
        --account YOUR_EZVIZ_EMAIL \
        --password YOUR_PASSWORD \
        --region eu \
        --serial BEXXXXXXXX-BEXXXXXXXX \
        --stream-source local \
        --stream-quality main \
        --seconds 20 \
        --output /tmp/hp7_video_local.h264

The LAN path first tries to reuse ``Cpd7LanSource`` from ``live_camera.py``.
That module is Home-Assistant-facing, so when Home Assistant is not installed
(the normal case for this standalone tool) a small VTM-shaped wrapper around
the same CPD7 LAN client/decoder is used instead. Both expose
``start()`` / ``iter_payloads()`` / ``close()`` and yield MPEG-PS bytes.

After the run you get the output file + a printed summary of the NAL unit
histogram (SPS=7, PPS=8, IDR=5, non-IDR=1, SEI=6).

To verify the codec / look for SPS/PPS yourself:
    ffprobe -loglevel debug /tmp/hp7_video.h264 2>&1 | head -40
    ffplay /tmp/hp7_video.h264

What we want to know on issue #33: does the dump contain ANY NAL
type 7 (SPS) and type 8 (PPS)? If yes — the relay is fine, ffmpeg
just needs the bitstream filter (aggressive_mpegts toggle). If no —
the firmware is broken and we need a different fix (synthetic SPS
injection, or fall back to JPEG snapshot mode).
"""
from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
COMPONENT_DIR = ROOT / "custom_components" / "ezviz_hp7"
sys.path.insert(0, str(COMPONENT_DIR))

from pylocalapi.client import EzvizClient  # noqa: E402
from pylocalapi.cloud_stream import open_cloud_stream  # noqa: E402
# Re-use the PesSplitter from the audio dump tool — same MPEG-PS parser,
# different target stream_id.
from hp7_audio_dump import PesSplitter  # noqa: E402

REGION_URLS = {
    "eu": "apiieu.ezvizlife.com",
    "us": "apiisa.ezvizlife.com",
    "cn": "apiicn.ezvizlife.com",
    "as": "apiias.ezvizlife.com",
    "sa": "apiisa.ezvizlife.com",
    "ru": "apirus.ezvizru.com",
}

VIDEO_STREAM_ID = 0xE0  # MPEG-PS video stream 0 (H.264)
NAL_TYPE_NAMES = {
    1: "non-IDR slice",
    2: "data partition A",
    5: "IDR slice",
    6: "SEI",
    7: "SPS",
    8: "PPS",
    9: "access unit delimiter",
    12: "filler",
}


class _StandaloneLanApi:
    """Minimal Hp7Api-shaped adapter needed by Cpd7LanSource.

    The standalone dump tool already owns a logged-in EzvizClient. Reusing it
    here avoids a second login while providing the three helpers used by the
    LAN source: AES key fetch, LAN IP resolution, and related-device serial.
    """

    def __init__(self, client: EzvizClient, api_host: str) -> None:
        self._client = client
        self._api_host = api_host

    @staticmethod
    def _bare_serial(serial: str) -> str:
        return serial.split("-", 1)[0]

    @staticmethod
    def get_related_device(serial: str) -> str:
        if "-" in serial:
            return serial.split("-", 1)[1]
        return serial

    def _p2p_register(self) -> None:
        """Authorize this client for the doorbell's LAN streaming ports."""
        import requests

        try:
            session_id = self._client.export_token().get("session_id")
        except Exception:  # noqa: BLE001
            session_id = None
        if not session_id:
            return

        url = f"https://{self._api_host}/v3/p2pbusiness/configurations/p2p"
        headers = {
            "appId": "ys7",
            "clientType": "1",
            "netType": "WIFI",
            "User-Agent": "EZVIZ/CloudClient",
        }
        try:
            resp = requests.post(
                url,
                headers=headers,
                data={"sessionId": session_id},
                timeout=8.0,
            )
            logging.getLogger(__name__).debug(
                "hp7_video_dump: p2p register -> %d", resp.status_code
            )
        except requests.RequestException as exc:
            # Same as Hp7Api: this authorization hint is best-effort; CAS
            # below gives the useful error if the session still isn't valid.
            logging.getLogger(__name__).debug(
                "hp7_video_dump: p2p register failed (best-effort): %s", exc
            )

    def fetch_lan_aes_key(self, serial: str) -> bytes:
        from pylocalapi.cas import EzvizCAS

        self._p2p_register()
        bare = self._bare_serial(serial)
        try:
            info = EzvizCAS(self._client.export_token()).cas_get_encryption(bare)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"CAS get-encryption failed: {exc}") from exc

        session = (info or {}).get("Response", {}).get("Session", {})
        key_str = str(session.get("@Key") or "")
        if len(key_str) != 16:
            result = str((info or {}).get("Response", {}).get("Result"))
            hint = ""
            if result in ("1052175", "1052170"):
                hint = (
                    " — if this is a CP5/CP7, turn OFF Image/Video Encryption "
                    "in the EZVIZ app; the LAN stream needs it disabled"
                )
            raise RuntimeError(
                f"invalid LAN AES key from CAS (Result={result}, "
                f"key={key_str!r}){hint}"
            )
        return key_str.encode("ascii")

    def get_local_ip(self, serial: str) -> Optional[str]:
        from pylocalapi.local_stream import _local_sdk_endpoint_from_client

        try:
            endpoint = _local_sdk_endpoint_from_client(self._client, serial)
            host = getattr(endpoint, "host", None)
            return str(host) if host else None
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).debug(
                "hp7_video_dump: local IP resolve failed for %s: %s",
                serial,
                exc,
            )
            return None


class _StandaloneCpd7LanSource:
    """Standalone equivalent of live_camera.Cpd7LanSource."""

    def __init__(
        self,
        api: _StandaloneLanApi,
        serial: str,
        channel: int = 1,
        stream_quality: str = "main",
    ) -> None:
        self._api = api
        self._serial = serial
        self._channel = channel
        self._stream_quality = stream_quality
        self._client: Any = None
        self._decoder: Any = None
        self._closed = False

    def start(self) -> "_StandaloneCpd7LanSource":
        from cpd7 import Cpd7LanClient, StreamDecoder

        key = self._api.fetch_lan_aes_key(self._serial)
        local_ip = self._api.get_local_ip(self._serial)
        if not local_ip:
            raise RuntimeError(
                f"could not resolve LAN IP for {self._serial} "
                "(device not on this network?)"
            )
        related = self._api.get_related_device(self._serial)
        client = Cpd7LanClient(
            local_ip,
            related,
            key,
            channel=self._channel,
            stream_quality=self._stream_quality,
        )
        client.start()
        self._client = client
        self._decoder = StreamDecoder(client.ecdh_priv)
        logging.getLogger(__name__).info(
            "hp7_video_dump: LAN source up (serial=%s ip=%s stream=%s)",
            self._serial,
            local_ip,
            self._stream_quality,
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


def _make_local_source(
    client: EzvizClient,
    api_host: str,
    serial: str,
    channel: int,
    stream_quality: str,
):
    api = _StandaloneLanApi(client, api_host)
    source_cls: Any = _StandaloneCpd7LanSource

    return source_cls(
        api,
        serial,
        channel=channel,
        stream_quality=stream_quality,
    )


def scan_nal_units(buf: bytes) -> Counter:
    """Count NAL units by type. Start codes are 00 00 01 or 00 00 00 01."""
    counts: Counter = Counter()
    n = len(buf)
    i = 0
    while i < n - 3:
        if buf[i] == 0 and buf[i + 1] == 0:
            if buf[i + 2] == 1:
                nal_byte = buf[i + 3] if i + 3 < n else 0
                counts[nal_byte & 0x1F] += 1
                i += 4
                continue
            if buf[i + 2] == 0 and i + 3 < n and buf[i + 3] == 1:
                nal_byte = buf[i + 4] if i + 4 < n else 0
                counts[nal_byte & 0x1F] += 1
                i += 5
                continue
        i += 1
    return counts


def feed_loop(
    source: Any,
    splitter: PesSplitter,
    stop_event: threading.Event,
    limit_bytes: Optional[int],
) -> None:
    for body in source.iter_payloads():
        if stop_event.is_set():
            break
        if not body:
            continue
        splitter.feed(body)
        if limit_bytes is not None and len(splitter.payload) >= limit_bytes:
            break


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
        "--stream-source",
        "--source",
        dest="stream_source",
        default="cloud",
        choices=("cloud", "local"),
        help="MPEG-PS source to capture (default: cloud VTM relay)",
    )
    p.add_argument(
        "--stream-quality",
        default="main",
        choices=("main", "sub"),
        help="LAN encoder stream when --stream-source=local (default: main)",
    )
    p.add_argument("--seconds", type=int, default=20)
    p.add_argument("--max-bytes", type=int, default=4 * 1024 * 1024)
    p.add_argument("--output", default="/tmp/hp7_video.h264")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    missing = [
        n
        for n, v in (
            ("account", args.account),
            ("password", args.password),
            ("serial", args.serial),
        )
        if not v
    ]
    if missing:
        print(
            f"Missing: {', '.join(missing)}. Use --account/--password/--serial "
            "or env vars EZVIZ_ACCOUNT/EZVIZ_PASSWORD/EZVIZ_SERIAL.",
            file=sys.stderr,
        )
        return 2

    host = REGION_URLS.get(args.region) or REGION_URLS["eu"]
    print(f"[hp7_video_dump] login → {host}")
    client = EzvizClient(account=args.account, password=args.password, url=host)
    client.login()

    if args.stream_source == "cloud":
        print(
            f"[hp7_video_dump] open_cloud_stream(serial={args.serial}, "
            f"channel={args.channel})"
        )
        source = open_cloud_stream(client, args.serial, channel=args.channel)
    else:
        print(
            f"[hp7_video_dump] Cpd7LanSource(serial={args.serial}, "
            f"channel={args.channel}, stream_quality={args.stream_quality})"
        )
        source = _make_local_source(
            client,
            host,
            args.serial,
            args.channel,
            args.stream_quality,
        )

    try:
        info = source.start()
    except Exception:
        try:
            client.logout()
        except Exception:
            pass
        raise

    streamssn = getattr(info, "streamssn", getattr(source, "streamssn", "?"))
    print(
        f"[hp7_video_dump] {args.stream_source} source up: "
        f"ssn={streamssn!r}"
    )

    splitter = PesSplitter(VIDEO_STREAM_ID)
    stop_event = threading.Event()

    t = threading.Thread(
        target=feed_loop,
        args=(source, splitter, stop_event, args.max_bytes),
        daemon=True,
    )
    t.start()
    print(
        f"[hp7_video_dump] capturing for {args.seconds} s "
        f"(max {args.max_bytes} B)…"
    )
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            if len(splitter.payload) >= args.max_bytes:
                break
            time.sleep(0.2)
    finally:
        stop_event.set()

    try:
        source.close()
    except Exception:
        pass
    try:
        client.logout()
    except Exception:
        pass

    video = bytes(splitter.payload)
    Path(args.output).write_bytes(video)
    print()
    print(f"[hp7_video_dump] saved {len(video)} B to {args.output}")
    print(f"  PES packets parsed: {splitter.pes_packets_seen}")
    print(f"  audio PES skipped : {splitter.other_pes_skipped}")
    print(f"  other PES skipped : {splitter.video_pes_skipped}")
    if video:
        nal_counts = scan_nal_units(video)
        print(f"  first 64 B hex    : {video[:64].hex(' ')}")
        print("  NAL units by type :")
        for nt, count in sorted(nal_counts.items()):
            name = NAL_TYPE_NAMES.get(nt, "?")
            print(f"    type {nt:>2} ({name:<22}) : {count}")
        sps = nal_counts.get(7, 0)
        pps = nal_counts.get(8, 0)
        idr = nal_counts.get(5, 0)
        print()
        if sps == 0 or pps == 0:
            print(
                "  ⚠️  No SPS/PPS found — the firmware never emits parameter "
                "sets in this window. ffmpeg can't decode this without "
                "synthetic SPS/PPS injection."
            )
        elif idr == 0:
            print(
                "  ⚠️  SPS/PPS present but no IDR — capture window may be "
                "too short."
            )
        else:
            print(
                f"  ✅ SPS={sps} PPS={pps} IDR={idr} — elementary stream "
                "is well-formed; the issue is downstream (ffmpeg config / "
                "bitstream filter)."
            )
    print()
    print("Verify externally:")
    print(f"  ffprobe -loglevel debug {args.output} 2>&1 | head -40")
    print(f"  ffplay {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
