#!/usr/bin/env python3
"""Audio extractor for the HP7 / CP7 VTM stream.

Connects to the same VTM cloud relay tools/hp7_vtm.py uses, splits the
MPEG-PS bytestream into PES packets, extracts the audio PES payloads
(stream_id 0xC0 — what ffmpeg's mpeg demuxer mis-decodes as MP2), and
saves them as a single raw blob plus a small report so we can identify
the real codec offline.

Usage:

    cd /path/to/ezviz_hp7
    python3 tools/hp7_audio_dump.py \
        --account YOUR_EZVIZ_EMAIL \
        --password YOUR_PASSWORD \
        --region eu \
        --serial BE7062577-BE6963574 \
        --seconds 15 \
        --output /tmp/hp7_audio.bin

After the run you'll have /tmp/hp7_audio.bin plus a printed summary:
   total audio bytes, first 64 bytes hex, histogram of byte values
   (handy to tell A-law from µ-law from raw PCM).

To play it back and confirm the codec:

    # Probable: G.711 A-law (PCMA), 8 kHz mono
    ffplay -f alaw -ar 8000 -ac 1 /tmp/hp7_audio.bin

    # Alternative: G.711 µ-law
    ffplay -f mulaw -ar 8000 -ac 1 /tmp/hp7_audio.bin

    # Alternative: 16-bit signed PCM (little-endian, 8 kHz mono)
    ffplay -f s16le -ar 8000 -ac 1 /tmp/hp7_audio.bin

Whichever sounds like a voice rather than noise is the right codec.
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

REGION_URLS = {
    "eu": "apiieu.ezvizlife.com",
    "us": "apiisa.ezvizlife.com",
    "cn": "apiicn.ezvizlife.com",
    "as": "apiias.ezvizlife.com",
    "sa": "apiisa.ezvizlife.com",
    "ru": "apirus.ezvizru.com",
}

AUDIO_STREAM_ID = 0xC0  # MPEG-PS audio stream 0
PRIVATE_STREAM_1 = 0xBD


class PesSplitter:
    """Incremental MPEG-PS splitter that yields PES payloads for one stream_id.

    Holds an internal buffer; callers feed MPEG-PS bytes via .feed() and read
    the extracted elementary-stream bytes from .payload.
    """

    def __init__(self, target_stream_id: int) -> None:
        self._target = target_stream_id
        self._buf = bytearray()
        self.payload = bytearray()
        self.pes_packets_seen = 0
        self.video_pes_skipped = 0
        self.other_pes_skipped = 0

    def feed(self, data: bytes) -> None:
        self._buf += data
        while True:
            consumed = self._try_one_packet()
            if consumed <= 0:
                break

    def _try_one_packet(self) -> int:
        """Try to parse one PES packet from the head of the buffer.

        Returns:
            >0  bytes consumed
            0   not enough data yet (caller stops)
            -1  resynchronisation needed (caller stops the loop)
        """
        buf = self._buf
        # Need at least the 6-byte PES header: start_code + stream_id + length.
        if len(buf) < 6:
            return 0

        # Resync if first 3 bytes aren't a PES start code.
        if not (buf[0] == 0x00 and buf[1] == 0x00 and buf[2] == 0x01):
            # Drop bytes until we find a 0x00 0x00 0x01 prefix.
            idx = buf.find(b"\x00\x00\x01")
            if idx < 0:
                # Keep last 2 bytes (potential partial start code).
                del buf[:-2]
                return 0
            del buf[:idx]
            return 1  # loop again

        stream_id = buf[3]

        # MPEG-PS pack header (0xBA) and system header (0xBB) have their own
        # length encoding; just skip them.
        if stream_id == 0xBA:
            if len(buf) < 14:
                return 0
            # pack_header is 14 bytes + stuffing (last 3 bits of byte 13).
            stuffing = buf[13] & 0x07
            total = 14 + stuffing
            if len(buf) < total:
                return 0
            del buf[:total]
            return total

        if stream_id == 0xBB:  # system header
            if len(buf) < 6:
                return 0
            length = (buf[4] << 8) | buf[5]
            total = 6 + length
            if len(buf) < total:
                return 0
            del buf[:total]
            return total

        # Standard PES packet.
        pes_length = (buf[4] << 8) | buf[5]
        if pes_length == 0:
            # "Unbounded" — used for video. Resync on next start code.
            idx = buf.find(b"\x00\x00\x01", 6)
            if idx < 0:
                return 0
            del buf[:idx]
            return idx

        total = 6 + pes_length
        if len(buf) < total:
            return 0

        # Parse optional PES header (only for stream_ids that have one).
        payload_start = 6
        has_optional_header = (
            stream_id == self._target
            or stream_id == PRIVATE_STREAM_1
            or (0xC0 <= stream_id <= 0xEF)
        )
        if has_optional_header:
            # byte 6: PES_scrambling_control + flags
            # byte 7: PTS_DTS_flags + extension flags
            # byte 8: PES_header_data_length
            if total < 9:
                del buf[:total]
                return total
            header_data_len = buf[8]
            payload_start = 9 + header_data_len

        self.pes_packets_seen += 1
        payload = bytes(buf[payload_start:total])

        if stream_id == self._target:
            self.payload += payload
        elif 0xE0 <= stream_id <= 0xEF:
            self.video_pes_skipped += 1
        else:
            self.other_pes_skipped += 1

        del buf[:total]
        return total


def feed_loop(vtm, splitter: PesSplitter, stop_event: threading.Event,
              limit_bytes: Optional[int]) -> None:
    """Drain vtm.iter_payloads() into the splitter until stopped or limit hit."""
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
    p.add_argument(
        "--seconds",
        type=int,
        default=15,
        help="Capture duration",
    )
    p.add_argument(
        "--max-bytes",
        type=int,
        default=512 * 1024,
        help="Stop after this many audio bytes captured",
    )
    p.add_argument(
        "--output",
        default="/tmp/hp7_audio.bin",
        help="Where to write the raw audio dump",
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
    print(f"[hp7_audio_dump] login → {host}")
    client = EzvizClient(account=args.account, password=args.password, url=host)
    client.login()

    print(f"[hp7_audio_dump] open_cloud_stream(serial={args.serial}, channel={args.channel})")
    vtm = open_cloud_stream(client, args.serial, channel=args.channel)
    info = vtm.start()
    print(f"[hp7_audio_dump] VTM up: ssn={info.streamssn!r}")

    splitter = PesSplitter(AUDIO_STREAM_ID)
    stop_event = threading.Event()

    t = threading.Thread(
        target=feed_loop,
        args=(vtm, splitter, stop_event, args.max_bytes),
        daemon=True,
    )
    t.start()

    print(f"[hp7_audio_dump] capturing for {args.seconds} s (max {args.max_bytes} B)…")
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

    audio = bytes(splitter.payload)
    Path(args.output).write_bytes(audio)
    print()
    print(f"[hp7_audio_dump] saved {len(audio)} B to {args.output}")
    print(f"  PES packets parsed: {splitter.pes_packets_seen}")
    print(f"  video PES skipped : {splitter.video_pes_skipped}")
    print(f"  other PES skipped : {splitter.other_pes_skipped}")
    if audio:
        print(f"  first 64 B hex    : {audio[:64].hex(' ')}")
        c = Counter(audio[: 8 * 1024])
        unique = len(c)
        top5 = c.most_common(5)
        print(f"  unique byte values (first 8 KiB): {unique}/256")
        print(f"  top-5 most common: {top5}")
        # A-law / µ-law tend to have flat-ish histograms over the byte range
        # (because samples are companded into 8-bit space). Raw PCM 16-bit
        # tends to cluster heavily near 0x00 and 0xFF or use only one byte of
        # each pair. Print enough info to tell them apart visually.

    print()
    print("Try playback:")
    print(f"  ffplay -f alaw  -ar 8000 -ac 1 {args.output}")
    print(f"  ffplay -f mulaw -ar 8000 -ac 1 {args.output}")
    print(f"  ffplay -f s16le -ar 8000 -ac 1 {args.output}")
    print(f"  ffplay -f s16be -ar 8000 -ac 1 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
