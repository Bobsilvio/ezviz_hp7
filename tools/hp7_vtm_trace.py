#!/usr/bin/env python3
"""VTM control-channel tracer for HP7 / CP7 / CP5.

Opens the EZVIZ cloud VTM session exactly like the live relay does, but
instead of pulling A/V it records the *sequence of VTM packet headers*
(channel + message code + length, never the bodies — those carry tokens
and media). This tells us how far the handshake gets on a given device.

Why this exists (#33, #36, #37): on a working HP7 the device starts
pushing STREAM packets (channel 0x01) on its own right after the
StreamInfo handshake — the client sends nothing else. On CP5 and the
HEVC HP7 firmware the relay times out 'waiting for VTM stream data',
i.e. the STREAM packets never arrive. Comparing the trace of a broken
device against a working one shows the exact message code where the
sequence diverges, which is the clue to what extra command (if any) the
firmware needs.

Usage:

    cd /path/to/ezviz_hp7
    python3 tools/hp7_vtm_trace.py \\
        --account YOUR_EZVIZ_EMAIL \\
        --password YOUR_PASSWORD \\
        --region eu \\
        --serial YOUR_SERIAL \\
        --packets 40

Paste the printed table into the issue. No bodies, no tokens, no media
are captured — only header metadata — so it's safe to share as-is.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

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

# Known VTM message codes / channels for readable output.
MSG_CODES = {
    0x132: "KEEPALIVE_REQ",
    0x133: "KEEPALIVE_RSP",
    0x13B: "STREAMINFO_REQ",
    0x13C: "STREAMINFO_RSP",
    0x13D: "STREAMINFO_NOTIFY",
    0x14A: "VTMSTREAM_ECDH_NOTIFY",
    0x6AE: "STREAM_DATA",
}
CHANNELS = {
    0x00: "MESSAGE",
    0x01: "STREAM",
    0x0A: "ENC_MESSAGE",
    0x0B: "ENC_STREAM",
}


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
        "--packets",
        type=int,
        default=40,
        help="How many VTM packet headers to record before stopping",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
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
    print(f"[hp7_vtm_trace] login -> {host}")
    client = EzvizClient(account=args.account, password=args.password, url=host)
    client.login()

    print(
        f"[hp7_vtm_trace] open_cloud_stream(serial={args.serial}, "
        f"channel={args.channel})"
    )
    vtm = open_cloud_stream(client, args.serial, channel=args.channel)

    try:
        events = vtm.trace_packets(max_packets=max(1, args.packets))
    except Exception as exc:  # noqa: BLE001
        print(f"[hp7_vtm_trace] trace stopped early: {exc!r}")
        events = []
    finally:
        try:
            vtm.close()
        except Exception:
            pass
        try:
            client.logout()
        except Exception:
            pass

    print()
    print(f"[hp7_vtm_trace] captured {len(events)} packet headers")
    print(f"  {'#':>3}  {'channel':<12} {'message_code':<24} {'len':>5} enc")
    stream_seen = False
    for e in events:
        d = e.as_dict()
        ch = d.get("channel", 0) or 0
        mc = d.get("message_code")
        ch_name = CHANNELS.get(ch, "?")
        mc_name = MSG_CODES.get(mc, "?") if mc is not None else "-"
        mc_str = f"{hex(mc)} {mc_name}" if mc is not None else "-"
        if ch == 0x01:
            stream_seen = True
        print(
            f"  {d.get('index'):>3}  0x{ch:02x} {ch_name:<8} "
            f"{mc_str:<24} {d.get('length'):>5} {d.get('encrypted')}"
        )

    print()
    if stream_seen:
        print(
            "  STREAM packets (channel 0x01) arrived -> the device IS pushing "
            "A/V. If live still fails it's a codec/ffmpeg issue downstream, "
            "not the VTM handshake."
        )
    else:
        print(
            "  NO STREAM packets (channel 0x01) seen -> the device accepted "
            "the handshake but never started pushing video. This is the CP5 / "
            "HEVC-HP7 failure (#33/#36). The last message code above is where "
            "it stalls; share this table."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
