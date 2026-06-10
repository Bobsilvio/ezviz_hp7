#!/usr/bin/env python3
"""mitmproxy addon — dump raw VTM control/stream bytes to a file.

The EZVIZ app tunnels its VTM (video) control channel over TLS port 443
to *.ezvizlife.com, but the payload inside is NOT HTTP — it's a binary
protobuf-over-VTM protocol. mitmproxy shows these as "TCP" flows and the
interactive hex viewer is painful to copy out of.

This addon side-steps the UI: it taps the `tcp_message` hook, captures
the first N messages in each direction for every ezvizlife.com TCP flow,
and writes a clean annotated hex dump to a file you can paste into the
GitHub issue. We're hunting the `forceiframe` request the app sends right
after opening live view (issue #33) so we can replicate it in the relay
and make CP5 / some-HP7 firmware emit an IDR.

Usage (Sergio / andresako):

    pip install mitmproxy            # if not already
    # 1. Set the iPhone's HTTP proxy to your computer's IP, port 8080.
    # 2. Install the mitmproxy CA cert on the iPhone (http://mitm.it).
    # 3. Run:
    mitmdump -s tools/mitm_vtm_dump.py
    # 4. Open the live view in the EZVIZ app. Watch it once, close it.
    # 5. Ctrl-C mitmdump.
    # 6. Paste /tmp/vtm_dump.txt into the issue (mask nothing — it's
    #    binary control data, no plaintext credentials live in here).

The dump groups messages per-flow, tags direction (>> client→server,
<< server→client), and prints offset-annotated hex + ASCII so the
protobuf field tags are easy to spot.

Tunables via env var:
    VTM_DUMP_HOST     substring to match (default 'ezvizlife.com')
    VTM_DUMP_MAX      max messages per direction per flow (default 8)
    VTM_DUMP_OUT      output path (default /tmp/vtm_dump.txt)
"""
from __future__ import annotations

import os
from collections import defaultdict

HOST_MATCH = os.environ.get("VTM_DUMP_HOST", "ezvizlife.com")
MAX_MSGS = int(os.environ.get("VTM_DUMP_MAX", "8"))
OUT_PATH = os.environ.get("VTM_DUMP_OUT", "/tmp/vtm_dump.txt")

# Skip the obvious REST/JSON flows — we only care about the binary VTM
# channel. REST responses are HTTP and handled by the http hooks, but a
# TCP flow that starts with 'GET '/'POST '/'HTTP' is plaintext REST that
# leaked into the TCP tab; ignore it.
_HTTP_PREFIXES = (b"GET ", b"POST ", b"PUT ", b"HEAD ", b"HTTP/")


def _hexdump(data: bytes, width: int = 16) -> str:
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off : off + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        hex_part = f"{hex_part:<{width * 3 - 1}}"
        ascii_part = "".join(
            chr(b) if 32 <= b < 127 else "." for b in chunk
        )
        lines.append(f"  {off:04x}  {hex_part}  |{ascii_part}|")
    return "\n".join(lines)


class VtmDump:
    def __init__(self) -> None:
        # (flow_id, direction) -> count
        self._counts: dict[tuple[str, str], int] = defaultdict(int)
        self._fh = open(OUT_PATH, "w")
        self._fh.write(
            f"# VTM raw dump — host~={HOST_MATCH!r} max={MAX_MSGS}/dir/flow\n\n"
        )
        self._fh.flush()
        self._seen_flows: set[str] = set()

    def _flow_matches(self, flow) -> bool:
        try:
            addr = flow.server_conn.address
        except Exception:
            return False
        if not addr:
            return False
        host = str(addr[0])
        # server_conn.address is the resolved IP; use the SNI / sni or the
        # original host when available.
        sni = getattr(flow.server_conn, "sni", None)
        candidates = [host]
        if sni:
            candidates.append(str(sni))
        return any(HOST_MATCH in c for c in candidates)

    def tcp_message(self, flow) -> None:
        if not self._flow_matches(flow):
            return
        msg = flow.messages[-1]
        data = bytes(msg.content)
        if not data:
            return
        if data[:5] in _HTTP_PREFIXES or data[:4] in _HTTP_PREFIXES:
            return  # plaintext REST leaked into a TCP flow

        direction = ">> client->server" if msg.from_client else "<< server->client"
        key = (flow.id, "c" if msg.from_client else "s")
        if self._counts[key] >= MAX_MSGS:
            return
        self._counts[key] += 1
        idx = self._counts[key]

        if flow.id not in self._seen_flows:
            self._seen_flows.add(flow.id)
            try:
                peer = flow.server_conn.sni or flow.server_conn.address[0]
            except Exception:
                peer = "?"
            self._fh.write(f"\n===== flow {flow.id[:8]} peer={peer} =====\n")

        # First byte 0x24 is the VTM magic — flag it so the interesting
        # packets are obvious in the dump.
        magic = " [VTM magic 0x24]" if data[:1] == b"\x24" else ""
        self._fh.write(
            f"\n[{direction} #{idx}] len={len(data)}{magic}\n"
        )
        self._fh.write(_hexdump(data[:256]))
        self._fh.write("\n")
        self._fh.flush()

    def done(self) -> None:
        try:
            self._fh.write("\n# end of capture\n")
            self._fh.close()
        except Exception:
            pass


addons = [VtmDump()]
