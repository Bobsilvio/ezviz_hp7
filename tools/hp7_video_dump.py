#!/usr/bin/env python3
"""Video (H.264 elementary stream) extractor for HP7 / CP5 / CP7.

Same VTM cloud relay as tools/hp7_audio_dump.py, but pulls the H.264
PES payloads (stream_id 0xE0) into a raw .h264 file we can analyse
offline. Used to triage issue #33: when ffmpeg can't find SPS/PPS we
need to see whether the elementary stream actually contains them at
all, or whether the firmware (CP5, some HP7 builds) only sends them
once before HA's stream worker attaches.

Usage:

    cd /path/to/ezviz_hp7
    python3 tools/hp7_video_dump.py \\
        --account YOUR_EZVIZ_EMAIL \\
        --password YOUR_PASSWORD \\
        --region eu \\
        --serial BEXXXXXXXX-BEXXXXXXXX \\
        --seconds 20 \\
        --output /tmp/hp7_video.h264

After the run you get /tmp/hp7_video.h264 + a printed summary of the
NAL unit histogram (SPS=7, PPS=8, IDR=5, non-IDR=1, SEI=6).

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
import logging
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components" / "ezviz_hp7"))

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


def feed_loop(vtm, splitter: PesSplitter, stop_event: threading.Event,
              limit_bytes: Optional[int]) -> None:
    for body in vtm.iter_payloads():
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
        n for n, v in (
            ("account", args.account),
            ("password", args.password),
            ("serial", args.serial),
        ) if not v
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

    print(
        f"[hp7_video_dump] open_cloud_stream(serial={args.serial}, "
        f"channel={args.channel})"
    )
    vtm = open_cloud_stream(client, args.serial, channel=args.channel)
    info = vtm.start()
    print(f"[hp7_video_dump] VTM up: ssn={info.streamssn!r}")

    splitter = PesSplitter(VIDEO_STREAM_ID)
    stop_event = threading.Event()

    t = threading.Thread(
        target=feed_loop,
        args=(vtm, splitter, stop_event, args.max_bytes),
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
        vtm.close()
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
