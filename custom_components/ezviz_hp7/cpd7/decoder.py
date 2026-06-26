"""Incremental ChaCha20 stream decoder for the HP7/CP7 doorbell.

The offline decoder in docs/cpd7-stream-recipe/code/cpd7_decode_offline.py
buffers the entire capture; here we want to feed bytes as they arrive on
the play socket and emit MPEG-PS plaintext for ffmpeg.

Wire layout (each RTSP-Interleaved chunk):
    $ <chan:1> <plen:2 BE> <payload:plen>

Chunk 0 (handshake): payload contains an IMKH wrapper and an inner
``$\x01`` ECDH REQ packet from which we derive the ChaCha20 key.

Subsequent chunks (chan = 0x01): payload starts with a 4-byte outer
prefix, then an inner ``$\x02`` ECDH DATA packet whose body is
ChaCha20-encrypted plaintext (HEVC + audio inside MPEG-PS).
"""

from __future__ import annotations

import logging
import struct

from Crypto.Cipher import ChaCha20
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .crypto import transform_nonce

_LOGGER = logging.getLogger(__name__)


HMAC_TRAILER_SIZE = 32
MPEG_PS_PACK = b"\x00\x00\x01\xba"

# HEVC VPS NAL start code with NAL header byte 0x40 (NAL type 32 = VPS) and
# byte 0x01 (layer 0, temporal_id_plus1 = 1).  CP7 and newer HP7 firmware
# emit HEVC; both 4- and 3-byte start codes are valid.
HEVC_VPS_4B = b"\x00\x00\x00\x01\x40\x01"
HEVC_VPS_3B = b"\x00\x00\x01\x40\x01"

# H.264 SPS NAL: first byte 0x67 = forbidden=0, nal_ref_idc=3, nal_type=7
# (Sequence Parameter Set).  Older HP7 firmware streams H.264 instead of
# HEVC; the camera does not advertise the codec in INVITE, so we detect
# either keyframe marker downstream.
H264_SPS_4B = b"\x00\x00\x00\x01\x67"
H264_SPS_3B = b"\x00\x00\x01\x67"

# When the first keyframe arrives, we'd ideally start the output at
# the MPEG-PS pack header that *contains* it so ffmpeg gets a clean
# muxer boundary.  But the rfind() walks back arbitrarily far — if the
# closest pack header is way before the keyframe it belongs to a
# previous GOP whose P-frames decode against references we never
# received.  Result: ffmpeg paints a few frames with the right shape
# but grey/distorted texture before recovering at the next keyframe.
# Cap how far back we accept a pack header so anything older falls
# back to starting at the bare keyframe — ffmpeg resyncs on the next
# pack header within milliseconds.  64 KB ≈ one MPEG-PS PES packet at
# typical bitrates, which is plenty to land on the boundary that
# actually surrounds the keyframe.
_MAX_PACK_LOOKBACK_BEFORE_KF = 64 * 1024

# Hard cap on the pre-keyframe buffer.  At ~150 KB/s the doorbell sends
# keyframes well within ~2 seconds, so a couple of MB is plenty.  If we
# blow past this without seeing a keyframe we emit anyway so the stream
# never truly stalls.
MAX_PRE_KEYFRAME_BUF = 2 * 1024 * 1024


def _nonce_12b(nonce_4b: bytes) -> bytes:
    return transform_nonce(nonce_4b) + b"\x00" * 8


class StreamDecoder:
    """Incremental decoder.

    Usage::

        d = StreamDecoder(ecdh_priv)
        while not_eof:
            d.feed(raw_bytes_from_socket)
            mpeg_ps = d.take()
            if mpeg_ps:
                ffmpeg_stdin.write(mpeg_ps)
    """

    def __init__(self, ecdh_priv) -> None:
        self._priv = ecdh_priv
        self._buf = bytearray()
        self._chacha20_key: bytes | None = None
        self._first_chunk_consumed = False
        self._first_decrypt_logged = False
        self._mpeg_started = False
        # Pre-keyframe accumulator.  We hold plaintext here until we spot
        # the first HEVC VPS (or H.264 SPS), then emit starting from the
        # most recent MPEG-PS pack header before the keyframe — this gives
        # the downstream decoder a clean parameter-set + IDR sequence at
        # offset 0 instead of a mid-frame join (which makes PyAV bail out
        # with ``PPS id out of range``).
        self._pending = bytearray()
        self._out = bytearray()

    @property
    def keys_derived(self) -> bool:
        return self._chacha20_key is not None

    def feed(self, data: bytes) -> None:
        if not data:
            return
        self._buf.extend(data)
        while self._consume_one_chunk():
            pass

    def take(self) -> bytes:
        out = bytes(self._out)
        self._out.clear()
        return out

    # ── Internals ─────────────────────────────────────────────────────────

    def _consume_one_chunk(self) -> bool:
        """Try to consume one RTSP-IL chunk from self._buf.

        Returns True if a chunk was consumed (or a stray byte skipped),
        False if more data is needed.
        """
        if len(self._buf) < 4:
            return False
        if self._buf[0] != 0x24:  # not '$' — skip stray byte and resync
            del self._buf[0]
            return True
        chan = self._buf[1]
        plen = struct.unpack(">H", bytes(self._buf[2:4]))[0]
        total = 4 + plen
        if len(self._buf) < total:
            return False
        payload = bytes(self._buf[4:total])
        del self._buf[:total]
        self._handle_chunk(chan, payload)
        return True

    def _handle_chunk(self, chan: int, payload: bytes) -> None:
        if not self._first_chunk_consumed:
            self._first_chunk_consumed = True
            try:
                self._derive_keys(payload)
            except Exception as exc:
                _LOGGER.warning("ECDH key derivation failed: %s", exc)
            return
        if chan != 0x01:
            return
        if self._chacha20_key is None:
            return
        plain = self._decrypt_data_chunk(payload)
        if plain:
            self._absorb_plain(plain)

    def _derive_keys(self, chunk0: bytes) -> None:
        off = chunk0.find(b"\x24\x01")
        if off < 0:
            raise RuntimeError(
                "handshake chunk missing $\\x01 marker "
                f"(first 32B: {chunk0[:32].hex()})"
            )
        pkt = chunk0[off:]
        if len(pkt) < 0x2B + 91:
            raise RuntimeError(f"handshake packet too short: {len(pkt)}B")
        header_len = pkt[2]
        encrypted_key = pkt[0x0B + header_len : 0x0B + header_len + 32]
        peer_pub = pkt[0x2B + header_len : 0x2B + header_len + 91]

        peer = serialization.load_der_public_key(peer_pub)
        shared = self._priv.exchange(ec.ECDH(), peer)
        chacha20_key = (
            Cipher(algorithms.AES(shared), modes.ECB())
            .decryptor()
            .update(encrypted_key)
        )
        self._chacha20_key = chacha20_key
        _LOGGER.debug(
            "CPD7 decoder: ChaCha20 key derived (shared=%s..., key=%s...)",
            shared.hex()[:16],
            chacha20_key.hex()[:16],
        )

    def _decrypt_data_chunk(self, chunk: bytes) -> bytes:
        if len(chunk) < 4:
            return b""
        if self._chacha20_key is None:
            return b""
        pkt = chunk[4:]  # skip 4-byte outer prefix
        if pkt[:2] != b"\x24\x02":
            return b""
        if len(pkt) < 0x0B + HMAC_TRAILER_SIZE:
            return b""
        nonce_12 = _nonce_12b(pkt[7:11])
        body = pkt[0x0B:-HMAC_TRAILER_SIZE]
        try:
            plain = ChaCha20.new(key=self._chacha20_key, nonce=nonce_12).decrypt(body)
        except Exception as exc:
            _LOGGER.debug("ChaCha20 decrypt failed: %s", exc)
            return b""
        if not self._first_decrypt_logged and plain:
            self._first_decrypt_logged = True
            _LOGGER.info(
                "CPD7 decoder: first decrypted chunk %dB head=%s",
                len(plain),
                plain[:64].hex(),
            )
        return plain

    @staticmethod
    def _find_keyframe(buf: bytes) -> int:
        """Return the offset of the first keyframe marker in ``buf``, or -1.

        Accepts either HEVC VPS (CP7 / newer HP7) or H.264 SPS (older HP7
        firmware).  The HP7/CP7 firmware does not advertise the codec on
        the INVITE response, so we match either marker.
        """
        candidates = [
            buf.find(HEVC_VPS_4B),
            buf.find(HEVC_VPS_3B),
            buf.find(H264_SPS_4B),
            buf.find(H264_SPS_3B),
        ]
        candidates = [c for c in candidates if c >= 0]
        return min(candidates) if candidates else -1

    def _absorb_plain(self, plain: bytes) -> None:
        if self._mpeg_started:
            self._out.extend(plain)
            return

        self._pending.extend(plain)
        buf = bytes(self._pending)
        kf_off = self._find_keyframe(buf)
        if kf_off >= 0:
            # Found keyframe (HEVC VPS or H.264 SPS).  Prefer starting
            # at the surrounding MPEG-PS pack header (cleaner muxer
            # boundary for ffmpeg) BUT only if it sits within one PES
            # packet of the keyframe — a pack header from a previous
            # GOP would feed ffmpeg P-frames whose references we
            # missed, producing grey/distorted output until the next
            # keyframe lands.  See ``_MAX_PACK_LOOKBACK_BEFORE_KF``.
            pack_off = buf.rfind(MPEG_PS_PACK, 0, kf_off)
            if pack_off >= 0 and kf_off - pack_off <= _MAX_PACK_LOOKBACK_BEFORE_KF:
                start = pack_off
            else:
                start = kf_off
            self._mpeg_started = True
            self._out.extend(buf[start:])
            self._pending.clear()
            _LOGGER.debug(
                "CPD7 decoder: keyframe found at +%d (pack@%d, start@%d) — synced",
                kf_off,
                pack_off,
                start,
            )
            return

        # Not yet — bound the buffer in case the keyframe never arrives.
        if len(self._pending) > MAX_PRE_KEYFRAME_BUF:
            keep = MAX_PRE_KEYFRAME_BUF // 2
            del self._pending[:-keep]
            _LOGGER.debug(
                "CPD7 decoder: pre-keyframe buffer trimmed to %d B (still waiting)",
                keep,
            )
