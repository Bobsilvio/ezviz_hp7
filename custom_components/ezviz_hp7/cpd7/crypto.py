#!/usr/bin/env python3
"""
CPD7/HP7 pure-Python ECDH + ChaCha20 key derivation.

Implements the KDF discovered from reversing libezstreamclient.so:
  ECDH P-256  → shared_secret (32 B)
  AES-256-ECB(key=shared_secret) decrypt inner_payload → ChaCha20 key (32 B)
  Nonce per-packet: 4 B from wire offset 0x07, swapped, padded to 12 B.

See docs/cpd7-stream-recipe/07-CRYPTO-INTERNALS.md for the full algorithm.
"""

import base64
import struct
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from Crypto.Cipher import ChaCha20


# ---------------------------------------------------------------------------
# ECDH P-256 keypair generation
# ---------------------------------------------------------------------------

def generate_ecdh_keypair():
    """Generate ephemeral ECDH P-256 keypair.

    Returns (priv, pub_b64) where:
      priv   — cryptography ec.EllipticCurvePrivateKey object (keep in memory)
      pub_b64 — base64 DER SubjectPublicKeyInfo, ready for <PublicKey> XML.
    """
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_der = priv.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_b64 = base64.b64encode(pub_der).decode()
    return priv, pub_b64


# ---------------------------------------------------------------------------
# ECDH packet parsing  ($ \x01 and $ \x02)
# ---------------------------------------------------------------------------

ECDH_MAGIC = 0x24  # '$'
ECDH_TYPE_REQ = 0x01   # handshake (carries camera pubkey + encrypted session key)
ECDH_TYPE_DATA = 0x02  # data (ChaCha20-encrypted payload)

# Offsets within the packet (relative to first byte after '$')
# These match the layout emitted by encECDHReqPackage / encECDHDataPackage.
OFF_TYPE = 1
OFF_HEADER_LEN = 2
OFF_PAYLOAD_LEN = 3    # 2 bytes BE-on-wire (after byteswap via enc function)
OFF_MARKER = 5
OFF_SUBTYPE = 6
OFF_NONCE_RAW = 7      # 4 bytes
OFF_ENCRYPTED_KEY = 0x0B   # 32 bytes (two AES-256-ECB blocks)
OFF_PEER_PUBKEY = 0x2B     # 91 bytes (DER SubjectPublicKeyInfo P-256)
HEADER_FIXED = 0x86        # end of fixed header area

# Trailer layout (after optional payload at offset HEADER_FIXED):
#   CRC32(0x00..HEADER_FIXED)  : 4 bytes
#   CRC32(payload)             : 4 bytes
#   HMAC-SHA256                : 32 bytes
TRAILER_SIZE = 4 + 4 + 32  # 40 bytes


def parse_ecdh_packet(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse a '$'-prefixed ECDH packet.

    Returns None if data does not start with '$' or is too short.
    Returns dict with keys:
      pkt_type       — ECDH_TYPE_REQ or ECDH_TYPE_DATA
      header_len     — byte at offset 2
      payload_len    — uint16 from wire (after byte-swap)
      sub_type       — byte at offset 6
      nonce_raw      — 4 bytes from offset 7
      encrypted_key  — 32 bytes from offset 0x0B (only for type REQ)
      peer_pubkey    — 91 bytes DER from offset 0x2B (only for type REQ)
      payload        — bytes from HEADER_FIXED to HEADER_FIXED+payload_len
      trailer        — 40 bytes of CRC+HMAC trailer
    """
    if len(data) < HEADER_FIXED + TRAILER_SIZE:
        return None
    if data[0] != ECDH_MAGIC:
        return None

    pkt_type = data[OFF_TYPE]
    if pkt_type not in (ECDH_TYPE_REQ, ECDH_TYPE_DATA):
        return None

    # Marker byte at offset 5 must be 0x01 (set by encECDHReqPackage).
    # This distinguishes genuine ECDH packets from RTSP-Interleaved ($ + chan).
    if data[OFF_MARKER] != 0x01:
        return None
    header_len = data[OFF_HEADER_LEN]

    # Wire format at offset 3: byteswapped uint16 from enc function
    # *(ushort *)(param_8 + 3) = param_7 >> 8 | (ushort)((uVar8 & 0xff00ff) << 8);
    # This is effectively big-endian with confusion; decode as BE.
    payload_len_raw = struct.unpack('>H', data[OFF_PAYLOAD_LEN:OFF_PAYLOAD_LEN + 2])[0]

    nonce_raw = data[OFF_NONCE_RAW:OFF_NONCE_RAW + 4]

    result = {
        'pkt_type': pkt_type,
        'header_len': header_len,
        'payload_len': payload_len_raw,
        'sub_type': data[OFF_SUBTYPE],
        'nonce_raw': nonce_raw,
        'payload': data[HEADER_FIXED:HEADER_FIXED + payload_len_raw],
        'trailer': data[HEADER_FIXED + payload_len_raw:
                        HEADER_FIXED + payload_len_raw + TRAILER_SIZE],
    }

    if pkt_type == ECDH_TYPE_REQ:
        result['encrypted_key'] = data[OFF_ENCRYPTED_KEY:OFF_ENCRYPTED_KEY + 32]
        result['peer_pubkey'] = data[OFF_PEER_PUBKEY:OFF_PEER_PUBKEY + 91]

    return result


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

def derive_shared_secret(our_priv, peer_pubkey_der: bytes) -> bytes:
    """Compute ECDH P-256 shared secret.

    Args:
      our_priv: cryptography ec.EllipticCurvePrivateKey
      peer_pubkey_der: 91-byte DER SubjectPublicKeyInfo from camera
    Returns: 32-byte raw shared secret
    """
    peer_pub = serialization.load_der_public_key(peer_pubkey_der)
    shared = our_priv.exchange(ec.ECDH(), peer_pub)
    return shared  # 32 bytes


def derive_chacha20_key(shared_secret: bytes, encrypted_key: bytes) -> bytes:
    """AES-256-ECB decrypt the encrypted session key using the ECDH shared secret.

    This matches:
      mbedtls_aes_setkey_dec(ctx, shared_secret, 256)
      mbedtls_aes_crypt_ecb(ctx, DECRYPT, encrypted_key[0:16], out[0:16])
      mbedtls_aes_crypt_ecb(ctx, DECRYPT, encrypted_key[16:32], out[16:32])

    Args:
      shared_secret: 32 bytes from ECDH
      encrypted_key: 32 bytes from packet offset 0x0B
    Returns: 32-byte ChaCha20 session key
    """
    cipher = Cipher(algorithms.AES(shared_secret), modes.ECB())
    decryptor = cipher.decryptor()
    chacha_key = decryptor.update(encrypted_key) + decryptor.finalize()
    return chacha_key  # 32 bytes


# ---------------------------------------------------------------------------
# Nonce transformation
# ---------------------------------------------------------------------------

def transform_nonce(nonce_raw_4b: bytes) -> bytes:
    """Apply the two swap operations to convert wire nonce → ChaCha20 nonce.

    From decompiled code (decECDHReqPackage lines 870338-870340):
      uVar5 = *(uint*)(packet + 7)  // LE read
      uVar5 = (uVar5 & 0xff00ff00) >> 8 | (uVar5 & 0xff00ff) << 8  // swap16 in u32
      local_2dc = uVar5 >> 16 | uVar5 << 16  // ROL16

    The net result on ARM LE: wire bytes AA BB CC DD → nonce AA BB CC DD.
    (The two swaps cancel out for normal wire order.)

    Returns 4-byte bytes object suitable for ChaCha20 nonce prefix.
    """
    u32 = struct.unpack('<I', nonce_raw_4b)[0]
    u32 = ((u32 & 0xFF00FF00) >> 8) | ((u32 & 0x00FF00FF) << 8)
    u32 = (u32 >> 16) | ((u32 & 0xFFFF) << 16) & 0xFFFFFFFF
    return struct.pack('<I', u32)


def make_nonce_12b(nonce_raw_4b: bytes) -> bytes:
    """Transform the 4-byte wire nonce and pad to 12 bytes (IETF ChaCha20 format).

    Applies transform_nonce internally, then pads bytes 4-11 with zero
    (matches mbedtls_chacha20_starts usage).
    """
    return transform_nonce(nonce_raw_4b) + b'\x00' * 8


# ---------------------------------------------------------------------------
# Stream decryption
# ---------------------------------------------------------------------------

def decrypt_chacha20_packet(key_32b: bytes, nonce_12b: bytes, ciphertext: bytes) -> bytes:
    """Decrypt a single ChaCha20 packet.

    Uses IETF ChaCha20 (12-byte nonce, 4-byte counter starting at 0).
    Counter is reset to 0 for EACH packet (mbedtls_chacha20_starts with counter=0).
    """
    cipher = ChaCha20.new(key=key_32b, nonce=nonce_12b)
    return cipher.decrypt(ciphertext)
