"""Incremental MPEG-PS PES splitter.

Used to split the VTM stream's MPEG-PS bytestream into per-stream_id payloads
so the video (H.264, stream_id 0xE0) and audio (AAC ADTS, stream_id 0xC0)
can be fed separately to ffmpeg — the standard ffmpeg mpeg demuxer
mis-identifies the audio PID as MP2 and rejects every packet.

Usage:

    parser = PesParser()
    while True:
        chunk = source.read()
        for stream_id, payload in parser.feed(chunk):
            ...

The parser keeps an internal byte buffer and yields exactly one
(stream_id, payload) tuple per complete PES packet. Pack headers (0xBA),
system headers (0xBB) and padding (0xBE) are silently consumed.
"""
from __future__ import annotations

from typing import Iterator, List, Tuple


# stream_ids that carry an optional PES header (PTS/DTS/etc) before the
# payload. Outside this range the payload starts immediately after the 6-byte
# length field.
def _has_optional_header(stream_id: int) -> bool:
    if 0xC0 <= stream_id <= 0xEF:
        return True
    return stream_id in (0xBD, 0xFD)


# Headers we recognise as "container" framing rather than actual PES packets —
# we want to consume them and move on without yielding.
def _is_container_marker(stream_id: int) -> bool:
    # 0xBE = padding stream; 0xBF = private_stream_2;
    # 0xF0..0xF2, 0xF8 = ECM / EMM / DSMCC; 0xFF = program_stream_directory.
    return stream_id in (0xBE, 0xBF, 0xF0, 0xF1, 0xF2, 0xF8, 0xFF)


class PesParser:
    """Stateful PES splitter; call feed() with each new chunk of bytes."""

    def __init__(self) -> None:
        self._buf = bytearray()
        # diagnostics
        self.packets_yielded = 0
        self.resync_drops = 0

    def feed(self, data: bytes) -> List[Tuple[int, bytes]]:
        """Append data and return every complete (stream_id, payload) tuple."""
        if data:
            self._buf += data
        out: List[Tuple[int, bytes]] = []
        while True:
            consumed = self._step(out)
            if consumed == 0:
                break
        return out

    def _step(self, out: List[Tuple[int, bytes]]) -> int:
        """Try to advance one packet. Returns bytes consumed (0 = need more)."""
        buf = self._buf
        if len(buf) < 6:
            return 0

        # Resync to a start-code prefix.
        if not (buf[0] == 0 and buf[1] == 0 and buf[2] == 1):
            idx = buf.find(b"\x00\x00\x01")
            if idx < 0:
                # Keep the last 2 bytes in case a start code straddles the
                # boundary; drop the rest.
                drop = len(buf) - 2
                if drop > 0:
                    self.resync_drops += drop
                    del buf[:drop]
                return 0
            self.resync_drops += idx
            del buf[:idx]
            return idx

        stream_id = buf[3]

        # MPEG-PS pack header (0xBA): 14 bytes + (last 3 bits of byte 13) of
        # stuffing bytes.
        if stream_id == 0xBA:
            if len(buf) < 14:
                return 0
            stuffing = buf[13] & 0x07
            total = 14 + stuffing
            if len(buf) < total:
                return 0
            del buf[:total]
            return total

        # MPEG-PS system header (0xBB): 16-bit length follows.
        if stream_id == 0xBB:
            length = (buf[4] << 8) | buf[5]
            total = 6 + length
            if len(buf) < total:
                return 0
            del buf[:total]
            return total

        # Other container markers — consume and ignore.
        if _is_container_marker(stream_id):
            length = (buf[4] << 8) | buf[5]
            total = 6 + length
            if len(buf) < total:
                return 0
            del buf[:total]
            return total

        # MPEG-PS program_end_code (0xB9): exactly 4 bytes total.
        if stream_id == 0xB9:
            del buf[:4]
            return 4

        # Real PES packet.
        pes_length = (buf[4] << 8) | buf[5]

        if pes_length == 0:
            # Unbounded packet (only video). Find the next start code; it
            # marks the end of this packet's payload.
            idx = buf.find(b"\x00\x00\x01", 6)
            if idx < 0:
                # Need more data before we can close the packet.
                return 0
            payload_offset = 6
            if _has_optional_header(stream_id):
                if idx < 9:
                    del buf[:idx]
                    return idx
                header_data_len = buf[8]
                payload_offset = 9 + header_data_len
                if payload_offset > idx:
                    payload_offset = idx
            payload = bytes(buf[payload_offset:idx])
            out.append((stream_id, payload))
            self.packets_yielded += 1
            del buf[:idx]
            return idx

        total = 6 + pes_length
        if len(buf) < total:
            return 0

        payload_offset = 6
        if _has_optional_header(stream_id):
            if total < 9:
                del buf[:total]
                return total
            header_data_len = buf[8]
            payload_offset = 9 + header_data_len
            if payload_offset > total:
                payload_offset = total
        payload = bytes(buf[payload_offset:total])
        out.append((stream_id, payload))
        self.packets_yielded += 1
        del buf[:total]
        return total
