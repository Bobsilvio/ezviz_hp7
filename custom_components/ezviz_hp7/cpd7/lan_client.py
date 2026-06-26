"""HP7/CP7 doorbell LAN client (synchronous sockets, class-based).

Replaces the dead port-8000 NetSDK path with the working LAN protocol on
ports 9010 (control) and 9020 (play).  Wire format and decryption details
are documented in docs/cpd7-stream-recipe/02-PROTOCOL.md.
"""

from __future__ import annotations

import hashlib
import logging
import re
import socket
import struct
import time
import uuid

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from .crypto import generate_ecdh_keypair

_LOGGER = logging.getLogger(__name__)


PORT_CTRL = 9010
PORT_PLAY = 9020
DEFAULT_RX_PORT = 10105

CMD_INIT = 0x2013  # init session   (port 9010)
CMD_INVITE = 0x2011  # invite stream  (port 9010)
CMD_PLAY = 0x3105  # play           (port 9020)

HEADER_LEN = 32
MD5_TRAILER_LEN = 32

MAGIC = b"\x9e\xba\xac\xe9"
AES_IV = b"01234567" + b"\x00" * 8

# Placeholder OperationCode the official EZVIZ app sends on the wire —
# the firmware validates length, not content.  Use the same string so
# captured traffic looks identical.
PLACEHOLDER_OP_CODE = "ABCDEFG"

# Session ID for the cmd 0x2013 INIT request.  The firmware accepts
# any value; this matches what the official app uses.
INIT_SESSION_ID = 10011


def _encrypt(key: bytes, plaintext: bytes) -> bytes:
    return AES.new(key, AES.MODE_CBC, AES_IV).encrypt(pad(plaintext, AES.block_size))


def _decrypt(key: bytes, ciphertext: bytes) -> bytes:
    return unpad(AES.new(key, AES.MODE_CBC, AES_IV).decrypt(ciphertext), AES.block_size)


def _build_packet(seq: int, cmd: int, plaintext_xml: bytes, key: bytes) -> bytes:
    body = _encrypt(key, plaintext_xml)
    header = (
        MAGIC
        + b"\x01\x00\x00\x00"
        + struct.pack(">I", seq)
        + b"\x00\x00\x00\x00"
        + struct.pack(">I", cmd)
        + b"\xff\xff\xff\xff"
        + struct.pack(">I", len(body))
        + b"\x00\x00\x00\x00"
    )
    trailer = hashlib.md5(body).hexdigest().encode("ascii")
    return header + body + trailer


def _parse_response(wire: bytes, key: bytes) -> tuple[dict, bytes]:
    if wire[:4] != MAGIC:
        raise ConnectionError(f"bad magic: {wire[:4].hex()}")
    seq = struct.unpack(">I", wire[8:12])[0]
    cmd = struct.unpack(">I", wire[16:20])[0]
    body_len = struct.unpack(">I", wire[24:28])[0]
    body = wire[HEADER_LEN : HEADER_LEN + body_len]
    if body.startswith(b"<?xml"):
        plain = body
    else:
        plain = _decrypt(key, body)
    return {"seq": seq, "cmd": cmd, "body_len": body_len}, plain


def _recv_response(sock: socket.socket, timeout: float = 5.0) -> bytes:
    sock.settimeout(timeout)
    header = b""
    while len(header) < HEADER_LEN:
        chunk = sock.recv(HEADER_LEN - len(header))
        if not chunk:
            raise ConnectionError("peer closed during header")
        header += chunk
    body_len = struct.unpack(">I", header[24:28])[0]
    rest_total = body_len + MD5_TRAILER_LEN
    rest = b""
    while len(rest) < rest_total:
        chunk = sock.recv(rest_total - len(rest))
        if not chunk:
            raise ConnectionError("peer closed during body")
        rest += chunk
    return header + rest


def _discover_local_ip(host: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((host, 1))
    try:
        return s.getsockname()[0]
    finally:
        s.close()


# ── XML builders ──────────────────────────────────────────────────────────


def _xml_init(session: int) -> bytes:
    return (
        f'<?xml version="1.0" encoding="utf-8"?>\n'
        f"<Request>\n"
        f"\t<OperationCode>{PLACEHOLDER_OP_CODE}</OperationCode>\n"
        f"\t<Session>{session}</Session>\n"
        f"</Request>\n"
    ).encode()


def _xml_invite(
    *,
    related_device: str,
    channel: int,
    encrypt_stream: bool,
    receiver_addr: str,
    receiver_port: int,
    pubkey_b64: str,
) -> bytes:
    enc_str = "TRUE" if encrypt_stream else "FALSE"
    timestamp = int(time.time() * 1000)
    uid = str(uuid.uuid4())
    pubkey_xml = f"\n\t<PublicKey>{pubkey_b64}</PublicKey>" if pubkey_b64 else ""
    return (
        f'<?xml version="1.0" encoding="utf-8"?>\n'
        f"<Request>\n"
        f"\t<OperationCode>{PLACEHOLDER_OP_CODE}</OperationCode>\n"
        f'\t<Channel RelatedDevice="{related_device}">{channel}</Channel>\n'
        f'\t<ReceiverInfo Address="" Port="{receiver_port}" '
        f'ServerType="1" StreamType="MAIN" NewStreamType="1" TransProto="TCP" />\n'
        f"\t<IsEncrypt>{enc_str}</IsEncrypt>\n"
        f'\t<ReceiverInfoEx SessionID="" Port="{receiver_port}" />\n'
        f'\t<Authentication Ticket="" BizCode="biz=1" Interval="180" />\n'
        f"\t<Uuid>{uid}</Uuid>\n"
        f"\t<Timestamp>{timestamp}</Timestamp>"
        f"{pubkey_xml}\n"
        f"</Request>\n"
    ).encode()


def _xml_play(session: int, rate: int = 1, mode: int = -1) -> bytes:
    return (
        f'<?xml version="1.0" encoding="utf-8"?>\n'
        f"<Request>\n"
        f"\t<Session>{session}</Session>\n"
        f"\t<Rate>{rate}</Rate>\n"
        f"\t<Mode>{mode}</Mode>\n"
        f"</Request>\n"
    ).encode()


# ── Client ────────────────────────────────────────────────────────────────


class Cpd7LanClient:
    """Synchronous LAN client.  Keep one instance per active stream session.

    Sequence:
        c = Cpd7LanClient(host, related_device, aes_key)
        c.start()                              # blocks ~1s for handshake
        ecdh_priv = c.ecdh_priv                # for StreamDecoder
        while True:
            buf = c.read_chunk()
            if not buf:
                break
            ...
        c.close()
    """

    def __init__(
        self,
        host: str,
        related_device: str,
        aes_key: bytes,
        channel: int = 1,
        rx_port: int = DEFAULT_RX_PORT,
        encrypt_stream: bool = True,
        connect_timeout: float = 5.0,
        recv_timeout: float = 8.0,
        cmd_timeout: float = 8.0,
    ) -> None:
        if len(aes_key) != 16:
            raise ValueError(
                f"AES-128 control key must be 16 ASCII bytes, got {len(aes_key)}"
            )
        self._host = host
        self._related = related_device
        self._channel = channel
        self._aes_key = aes_key
        self._rx_port = rx_port
        self._encrypt_stream = encrypt_stream
        self._connect_timeout = connect_timeout
        self._recv_timeout = recv_timeout
        self._cmd_timeout = cmd_timeout
        self._play_sock: socket.socket | None = None
        self._ecdh_priv = None
        self._ecdh_pub_b64: str | None = None
        self._closed = False

    @property
    def ecdh_priv(self):
        return self._ecdh_priv

    def _send_cmd(
        self, sock: socket.socket, seq: int, cmd: int, xml: bytes, tag: str
    ) -> tuple[dict, bytes]:
        pkt = _build_packet(seq, cmd, xml, self._aes_key)
        sock.sendall(pkt)
        resp = _recv_response(sock, timeout=self._cmd_timeout)
        meta, plain = _parse_response(resp, self._aes_key)
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "CPD7 %s seq=%d cmd=0x%04x rx_cmd=0x%04x body=%dB",
                tag,
                seq,
                cmd,
                meta["cmd"],
                meta["body_len"],
            )
        return meta, plain

    def start(self) -> None:
        """Run INIT → INVITE → PLAY.  Blocks until the play socket is open."""
        if self._play_sock is not None:
            raise RuntimeError("already started")

        self._ecdh_priv, self._ecdh_pub_b64 = generate_ecdh_keypair()
        my_ip = _discover_local_ip(self._host)
        _LOGGER.debug("CPD7 LAN start host=%s my_ip=%s", self._host, my_ip)

        # INIT
        sock_ctrl = socket.create_connection(
            (self._host, PORT_CTRL), timeout=self._connect_timeout
        )
        try:
            self._send_cmd(sock_ctrl, 1, CMD_INIT, _xml_init(INIT_SESSION_ID), "INIT")
        finally:
            sock_ctrl.close()

        # INVITE
        sock_inv = socket.create_connection(
            (self._host, PORT_CTRL), timeout=self._connect_timeout
        )
        try:
            invite_xml = _xml_invite(
                related_device=self._related,
                channel=self._channel,
                encrypt_stream=self._encrypt_stream,
                receiver_addr=my_ip,
                receiver_port=self._rx_port,
                pubkey_b64=self._ecdh_pub_b64 or "",
            )
            _, resp_plain = self._send_cmd(
                sock_inv, 1, CMD_INVITE, invite_xml, "INVITE"
            )
        finally:
            sock_inv.close()

        m = re.search(rb"<Session>(\d+)</Session>", resp_plain)
        if not m:
            raise ConnectionError(
                "no <Session> in InviteStream response: "
                + resp_plain[:200].decode(errors="replace")
            )
        session = int(m.group(1))

        # PLAY (keep socket open for the encrypted stream that follows)
        sock_play = socket.create_connection(
            (self._host, PORT_PLAY), timeout=self._connect_timeout
        )
        try:
            self._send_cmd(sock_play, 1, CMD_PLAY, _xml_play(session), "PLAY")
        except Exception:
            sock_play.close()
            raise
        self._play_sock = sock_play

    def read_chunk(self, max_bytes: int = 65536) -> bytes:
        """Blocking recv on the play socket.  Returns b'' on timeout or EOF."""
        if not self._play_sock or self._closed:
            return b""
        self._play_sock.settimeout(self._recv_timeout)
        try:
            return self._play_sock.recv(max_bytes)
        except TimeoutError:
            return b""
        except OSError:
            return b""

    def close(self) -> None:
        self._closed = True
        if self._play_sock is not None:
            try:
                self._play_sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._play_sock.close()
            except OSError:
                pass
            self._play_sock = None
