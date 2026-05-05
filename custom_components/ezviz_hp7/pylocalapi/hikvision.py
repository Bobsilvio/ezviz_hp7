"""EZVIZ CPD7 Hikvision LAN protocol client.

Protocol flow for LAN streaming without RTSP:
  1. CAS-LAN port 9010 pre-handshake (cmd 0x3003) — fixed replayed packet
  2. LAN login on port 8000 (RSA-1024 challenge + HMAC-MD5 auth)
  3. Session upgrade (cmd 0x00111050) — unlocks StartRealPlay
  4. RealPlay stream start (cmd 0x00030000) → IMKH H.264 frames

The permanent_password (from EUCAS cmd 0x2845) is used as the HMAC key
for LAN login. Without it, the device creates a guest session where all
post-login commands return NORIGHT (0x97).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import socket
import struct
import time
from typing import Callable

_LOGGER = logging.getLogger(__name__)

# ── Embedded mobile RSA-1024 private key (from EZVIZ libHCCore.so) ─────
# This is the same key the EZVIZ mobile app uses for LAN auth.
MOBILE_RSA_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIICXQIBAAKBgQDS4QxkTd8VUV5FfS92mS2WwjyWTxF1v/y4rn46c/WbBaSef+LY\n"
    "0yNip4BNrG4fQxKAW+ZU+u0Dj5NXDs3t/lMPdc4Fgh1Sn0y8QbRY9btQfAdPFAyg\n"
    "UV0psr911obZ4FbKaodyd79NWwKnH5C2lHtb7klMqSGu1Pwg66J7VaJttwIDAQAB\n"
    "AoGBAL+GSfznhM8VwasXEX6DjJY5/1D8qvzoy5zoThjErLKJI4QY3mzTBnZZbvwc\n"
    "uT+HaUxPKxjPdWgghE8zUPDwZXokDEQqgz8RGlOnoOgA1hp0tn0iif8/Xqm+k2zW\n"
    "Nr4uNHDox/ss3xvvAc7h3UfLEYOH+j7C11aUU8K6GBYC8lvhAkEA+TT+nXugITzB\n"
    "ouHxDMaoR0iY39Y+DL7ECx1jjNG1Iv6szR2I6NUUs3FWfvCK1tiVwvw9IyT0N8qv\n"
    "FdUvBsH9vwJBANigmHwfHTs+6oSVdGane/RdVKm8G/wmACorf085IyuF1GzM/1Nu\n"
    "jIN742BpfYRkt7qTGARxWM2/ytbZDm4z/gkCQQChN1xkSt67wc9O7TYA2t9wRhHH\n"
    "9JRtsFepDRkit2OkQPdPNoUkgvyCXZbkRf67oJ+55W4ztytakH+V8zUZ/ROHAkA+\n"
    "TPGNwOUHRPDtcI4pd8GOZckTh6YEvmkNt7TFdAlJWxPctpg3xnNi3R5ne+89RDoS\n"
    "znr5zB9eDOqpH4Om7g0BAkAsNa1AIdpXq0loyILvWE16defq9Z5HsGowLidkMAqP\n"
    "JiON2NABYJp3XVEbfKJ4UJ64lS/GjYFCcy0IFK2hax/B\n"
    "-----END RSA PRIVATE KEY-----\n"
)

# ── Protocol constants ─────────────────────────────────────────────────
SDK_HDR_MAGIC = b"\x5a\x00\x00\x00"
SDK_HDR_CLIENT_TYPE_MOBILE = b"\x00\x00\x00\x01"
SDK_HDR_TRAIL = b"\x6f\x00"
USER_FIELD = b"admin".ljust(48, b"\x00")

CAS_LAN_PORT = 9010
DEVICE_PORT = 8000
CHANNEL_DEFAULT = 33


# ── RSA key loading ────────────────────────────────────────────────────


def _load_rsa_key(pem_data: str | None = None) -> tuple:
    """Load the mobile RSA-1024 private key.

    Returns (priv, pub_der, pn) where pn = priv.private_numbers().
    """
    from cryptography.hazmat.primitives.serialization import (
        load_pem_private_key,
        Encoding,
        PublicFormat,
    )

    priv = load_pem_private_key(
        (pem_data or MOBILE_RSA_PEM).encode("ascii"), password=None
    )
    pub_der = priv.public_key().public_bytes(
        encoding=Encoding.DER,
        format=PublicFormat.PKCS1,
    )
    pn = priv.private_numbers()
    return priv, pub_der, pn


# ── SDK header builder ─────────────────────────────────────────────────


def make_sdk_header(ip_lsb: bytes, mac: bytes = b"\x00" * 6) -> bytes:
    """Build 32-byte SDK header with mobile client type (full rights).

    Layout:
      [ 0:4]  magic 0x5a000000
      [ 4:8]  zeros
      [ 8:12] 0x00010000
      [12:16] 0x05013d4b
      [16:20] client_type — 0x00000001 = mobile (full rights)
      [20:24] src_ip LSB-first (4B)
      [24:30] MAC address (6B) — zeros mimics mobile
      [30:32] 0x6f00 trail
    """
    return (
        SDK_HDR_MAGIC
        + b"\x00" * 4
        + b"\x00\x01\x00\x00"
        + b"\x05\x01\x3d\x4b"
        + SDK_HDR_CLIENT_TYPE_MOBILE
        + ip_lsb
        + mac
        + SDK_HDR_TRAIL
    )


# ── Socket helpers ─────────────────────────────────────────────────────


def _recv_exact(sock: socket.socket, n: int, timeout: float = 5.0) -> bytes:
    """Read exactly n bytes from a socket."""
    sock.settimeout(timeout)
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise IOError(f"connection closed after {len(out)}/{n} bytes")
        out += chunk
    return out


def _recv_msg(sock: socket.socket) -> bytes:
    """Read one length-prefixed message. Length includes the 4-byte prefix."""
    hdr = _recv_exact(sock, 4)
    n = struct.unpack(">I", hdr)[0]
    body = _recv_exact(sock, n - 4) if n > 4 else b""
    return hdr + body


def _get_outgoing_ip(host: str, port: int = DEVICE_PORT) -> bytes:
    """Get our outgoing IP as LSB-first bytes by probing the device."""
    s = socket.create_connection((host, port), timeout=3)
    local_ip = s.getsockname()[0]
    s.close()
    return bytes(reversed([int(b) for b in local_ip.split(".")]))


# ── CAS-LAN pre-handshake (port 9010) ──────────────────────────────────


def cas_lan_handshake(host: str, port: int = CAS_LAN_PORT) -> bool:
    """Send cmd 0x3003 pre-handshake on CAS-LAN port.

    The EZVIZ app sends this before LAN login. Without it, the device
    returns NORIGHT (0x97) for every cmd post-login. The 176B body is
    fixed per device — replay works (verified across multiple pcaps).

    Args:
        host: Device IP address.
        port: CAS-LAN port (9010).

    Returns:
        True if handshake response was received.
    """
    pkt = bytes.fromhex(
        "9ebaace901000000000000010000000000003003ffffffff0000007000000000"
        "36e25f27576c8314563ffec728b4ab757f5bd209d80019834f8bb5f9d834573a"
        "69e801248fa4c0a15cd1b33bf8ad37c357e98fcdd31184aeab7f31db530f781f"
        "9e52b11452c08db2fe1f483b1abf76b701b18deb9125ab33486e079c69ff4042"
        "b183b4dccab5d48ccc985ff7b2230aaa"
        "3366653761366534653730663932663436343037626363373935316431636362"
    )
    try:
        s = socket.create_connection((host, port), timeout=5)
        s.sendall(pkt)
        s.settimeout(5)
        resp = b""
        try:
            while True:
                c = s.recv(4096)
                if not c:
                    break
                resp += c
                if len(resp) > 1024:
                    break
        except socket.timeout:
            pass
        s.close()
        _LOGGER.debug("CAS-LAN handshake: %dB response", len(resp))
        return len(resp) > 0
    except (socket.gaierror, ConnectionRefusedError, OSError) as exc:
        _LOGGER.debug("CAS-LAN handshake failed (port may be closed): %s", exc)
        return False


# ── LAN login ──────────────────────────────────────────────────────────


def _decrypt_rsa_challenge(rsa_ct: bytes, pn) -> bytes:
    """Decrypt RSA-1024 PKCS1v1.5 challenge, return inner_ascii (32B hex)."""
    n = pn.public_numbers.n
    pt = pow(int.from_bytes(rsa_ct, "big"), pn.d, n).to_bytes(128, "big")
    if pt[:2] != b"\x00\x02":
        raise ValueError(f"RSA PKCS1 padding mismatch: {pt[:16].hex()}")
    return pt[pt.index(b"\x00", 2) + 1 :]


def _compute_ct1(inner_ascii: bytes, user: bytes = b"admin") -> bytes:
    """ct1 = HMAC-MD5(inner_ascii, user)."""
    return hmac.new(inner_ascii, user, hashlib.md5).digest()


def _compute_ct2(
    inner_ascii: bytes,
    user: bytes,
    tail: bytes,
    password: bytes,
) -> bytes:
    """ct2 = HMAC-MD5(inner_ascii, SHA256(user||tail||password).hex().lower())."""
    sha = hashlib.sha256(user + tail + password).digest()
    sha_hex_lower = sha.hex().encode("ascii")
    return hmac.new(inner_ascii, sha_hex_lower, hashlib.md5).digest()


def _build_login_p1(pub_der: bytes, hdr: bytes) -> bytes:
    """Build 224-byte login request: 4B prefix + 32B hdr + 48B user + 140B pubkey."""
    body = hdr + USER_FIELD + pub_der
    return struct.pack(">I", 4 + len(body)) + body


def _build_login_p2(ct1: bytes, ct2: bytes, hdr: bytes) -> bytes:
    """Build 84-byte login response: 4B prefix + 32B hdr + 16B ct1 + 16B zeros + 16B ct2."""
    body = hdr + ct1 + b"\x00" * 16 + ct2
    return struct.pack(">I", 4 + len(body)) + body


def lan_login(
    host: str,
    password: bytes,
    port: int = DEVICE_PORT,
) -> tuple[bytes, str]:
    """Perform full LAN login on port 8000.

    Args:
        host: Device IP.
        password: Permanent password bytes (for HMAC key).
        port: Device command port (8000).

    Returns:
        (session_token, serial) — 4-byte session token and device serial.

    Raises:
        ConnectionError: If login fails at any step.
    """
    priv, pub_der, pn = _load_rsa_key()

    ip_lsb = _get_outgoing_ip(host, port)
    hdr = make_sdk_header(ip_lsb)

    sock = socket.create_connection((host, port), timeout=5)

    try:
        # Part 1: send pubkey, receive RSA challenge
        pkt1 = _build_login_p1(pub_der, hdr)
        sock.sendall(pkt1)
        resp1 = _recv_msg(sock)

        if len(resp1) < 208:
            raise ConnectionError(f"Login p1 rejected: {len(resp1)}B {resp1.hex()}")

        rsa_ct = resp1[16:144]
        tail = resp1[144:208]

        # Decrypt RSA challenge
        inner_ascii = _decrypt_rsa_challenge(rsa_ct, pn)

        # Compute HMACs
        user = b"admin"
        ct1 = _compute_ct1(inner_ascii, user)
        ct2 = _compute_ct2(inner_ascii, user, tail, password)

        # Part 2: send HMACs, receive session token
        pkt2 = _build_login_p2(ct1, ct2, hdr)
        sock.sendall(pkt2)
        resp2 = _recv_msg(sock)

        if len(resp2) < 24:
            raise ConnectionError(f"Login p2 rejected: {len(resp2)}B {resp2.hex()}")

        status_code = resp2[8:12]
        if status_code != b"\x00\x00\x00\x01":
            raise ConnectionError(f"Login FAILED (status={status_code.hex()})")

        session_token = resp2[16:20]
        serial = resp2[20:].split(b"\x00", 1)[0].decode("ascii", errors="replace")

        _LOGGER.debug("LAN login OK: token=%s serial=%s", session_token.hex(), serial)
        return session_token, serial

    finally:
        # Send RST to close — mimics the mobile app behaviour
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        except OSError:
            pass
        sock.close()


# ── Command helpers (post-login) ───────────────────────────────────────


def _build_cmd_packet(
    cmd: int,
    session_token: bytes,
    ip_lsb: bytes,
    body: bytes = b"",
    req_id: bytes | None = None,
) -> bytes:
    """Build a 0x63-format command packet.

    Layout:
      [0:4]   total_len (BE)
      [4:8]   magic 0x63000000
      [8:12]  req_id (random)
      [12:16] cmd (BE)
      [16:20] src_ip LSB-first
      [20:24] session_token
      [24:32] zeros (8B)
      [32:]   body (if any)
    """
    hdr = (
        b"\x63\x00\x00\x00"
        + (req_id or os.urandom(4))
        + struct.pack(">I", cmd)
        + ip_lsb
        + session_token
        + b"\x00" * 8
        + body
    )
    return struct.pack(">I", 4 + len(hdr)) + hdr


def _send_cmd(
    host: str,
    cmd: int,
    session_token: bytes,
    ip_lsb: bytes,
    body: bytes = b"",
    timeout: float = 4.0,
    port: int = DEVICE_PORT,
) -> tuple[bytes, int]:
    """Open new socket, send cmd, read framed response, close.

    Returns (response_bytes, status_code_int).
    """
    pkt = _build_cmd_packet(cmd, session_token, ip_lsb, body)
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.sendall(pkt)
        sock.settimeout(timeout)
        rsp = b""
        try:
            while True:
                c = sock.recv(8192)
                if not c:
                    break
                rsp += c
                if len(rsp) >= 4:
                    n = struct.unpack(">I", rsp[:4])[0]
                    if len(rsp) >= n:
                        break
        except socket.timeout:
            pass
        code = struct.unpack(">I", rsp[8:12])[0] if len(rsp) >= 12 else 0xFFFF
        return rsp, code
    finally:
        sock.close()


def session_upgrade(
    host: str,
    session_token: bytes,
    ip_lsb: bytes,
    port: int = DEVICE_PORT,
) -> bool:
    """Send cmd 0x00111050 'session upgrade' — unlocks StartRealPlay.

    Returns True if the command was accepted (code=0x0d or 0x01).
    """
    rsp, code = _send_cmd(host, 0x00111050, session_token, ip_lsb, timeout=4, port=port)
    ok = code == 0x0D or code == 0x01
    _LOGGER.debug(
        "Session upgrade 0x00111050: code=0x%02x ok=%s (%dB)", code, ok, len(rsp)
    )
    return ok


def stream_config(
    host: str,
    session_token: bytes,
    ip_lsb: bytes,
    channel: int = CHANNEL_DEFAULT,
    port: int = DEVICE_PORT,
) -> bool:
    """Send cmd 0x00111040 stream config — configures stream parameters.

    Args:
        host: Device IP.
        session_token: From lan_login().
        ip_lsb: Outgoing IP LSB-first.
        channel: Camera channel (33 for CP7 main cam).
        port: Device command port.

    Returns:
        True if command was accepted.
    """
    body = struct.pack(">I", channel) + bytes.fromhex(
        "000000ff000000ff0000000000000000"
        "00000000000000000000000000000000"
        "000007ea000000050000000100000000"
        "0000000000000000000007ea00000005"
        "00000001000000170000003b0000003b"
        + "00" * 64
    )
    rsp, code = _send_cmd(
        host, 0x00111040, session_token, ip_lsb, body=body, timeout=4, port=port
    )
    ok = code == 0x01 or code == 0x0D
    _LOGGER.debug("Stream config 0x00111040: code=0x%02x (%dB)", code, len(rsp))
    return ok


# ── Stream start ───────────────────────────────────────────────────────


def start_realplay_stream(
    host: str,
    session_token: bytes,
    ip_lsb: bytes,
    channel: int = CHANNEL_DEFAULT,
    port: int = DEVICE_PORT,
) -> socket.socket:
    """Start H.264 RealPlay stream via cmd 0x00030000.

    Returns an open socket with the stream data. Caller must close it.
    The stream contains IMKH-framed H.264 data.

    Args:
        host: Device IP.
        session_token: From lan_login().
        ip_lsb: Outgoing IP LSB-first.
        channel: Camera channel.
        port: Device command port.

    Returns:
        Open socket streaming IMKH H.264 data.

    Raises:
        ConnectionError: If stream start fails.
    """
    body = struct.pack(">I", channel) + b"\x00\x00\x00\x00\x00\x00\x04\x01"
    pkt = _build_cmd_packet(0x00030000, session_token, ip_lsb, body)

    sock = socket.create_connection((host, port), timeout=5)
    try:
        sock.sendall(pkt)
        # Check initial response
        sock.settimeout(3.0)
        first = sock.recv(64)
        if len(first) < 4:
            raise ConnectionError(f"Stream start: empty response")
        if first[:4] == b"IMKH":
            _LOGGER.debug("RealPlay stream: IMKH header confirmed")
        elif len(first) >= 8:
            status_val = struct.unpack(">I", first[4:8])[0] if len(first) >= 8 else 0
            if status_val == 0x97:
                sock.close()
                raise ConnectionError(
                    "RealPlay: NORIGHT (0x97) — session upgrade missing or failed"
                )
            if status_val != 0:
                _LOGGER.debug(
                    "RealPlay initial: status=0x%04x", status_val
                )
    except socket.timeout:
        _LOGGER.debug("RealPlay initial response: timeout (stream may still start)")

    return sock


# ── High-level convenience ─────────────────────────────────────────────


def full_chain(
    host: str,
    permanent_password: str,
    channel: int = CHANNEL_DEFAULT,
) -> socket.socket | None:
    """Run the full LAN stream chain: CAS-LAN → login → upgrade → stream.

    Args:
        host: Device IP.
        permanent_password: From query_permanent_password().
        channel: Camera channel.

    Returns:
        Open stream socket, or None if any step failed.
    """
    # Step 1: CAS-LAN pre-handshake
    if not cas_lan_handshake(host):
        _LOGGER.warning("CAS-LAN handshake failed (continuing anyway)")

    time.sleep(0.2)

    # Step 2: LAN login
    try:
        session_token, serial = lan_login(host, permanent_password.encode("ascii"))
    except (ConnectionError, ValueError) as exc:
        _LOGGER.error("LAN login failed for %s: %s", host, exc)
        return None

    ip_lsb = _get_outgoing_ip(host)

    time.sleep(0.1)

    # Step 3: Session upgrade
    if not session_upgrade(host, session_token, ip_lsb):
        _LOGGER.warning("Session upgrade 0x00111050 returned unexpected code")

    time.sleep(0.1)

    # Step 4: Stream start
    try:
        sock = start_realplay_stream(host, session_token, ip_lsb, channel)
        return sock
    except ConnectionError as exc:
        _LOGGER.error("Stream start failed: %s", exc)
        return None


# ── Stream reading helpers (IMKH parsing) ──────────────────────────────

# IMKH frame header: 4B magic + 4B total_size + 4B channel + 4B data_type + 8B timestamp
IMKH_HEADER_SIZE = 24


def read_imkh_frame(stream: socket.socket, timeout: float = 5.0) -> bytes | None:
    """Read one IMKH-framed H.264 chunk from the stream.

    The IMKH header is included in the returned bytes.
    Returns None on timeout or connection close.

    Format: [IMKH magic(4)][total_size(4)][channel(4)][data_type(4)][timestamp(8)][payload...]
    total_size includes the IMKH header (24 + payload length).
    """
    try:
        stream.settimeout(timeout)
        hdr = b""
        while len(hdr) < 4:
            c = stream.recv(4 - len(hdr))
            if not c:
                return None
            hdr += c

        if hdr[:4] != b"IMKH":
            _LOGGER.warning("Unexpected stream data: %s", hdr[:4].hex())
            return hdr  # return whatever we got

        # Read remaining 20 bytes of IMKH header (total_size is at bytes 4-7)
        rest = b""
        while len(rest) < 20:
            c = stream.recv(20 - len(rest))
            if not c:
                return None
            rest += c

        total_size = struct.unpack(">I", rest[:4])[0]
        payload_len = total_size - 24  # subtract IMKH header

        # Read payload
        payload = b""
        while len(payload) < payload_len:
            c = stream.recv(min(payload_len - len(payload), 65536))
            if not c:
                break
            payload += c

        return hdr + rest + payload

    except socket.timeout:
        return None
    except IOError:
        return None
