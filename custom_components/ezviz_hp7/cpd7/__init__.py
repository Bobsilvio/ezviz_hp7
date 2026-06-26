"""Pure-Python LAN streaming pipeline for the HP7/CP7 doorbell.

The CPD7 LAN protocol (ports 9010 control + 9020 play, AES-128-CBC control
frames, ECDH P-256 key agreement, ChaCha20 media decryption) was reverse
engineered by **albrzmr** (https://github.com/albrzmr/ezviz_hp7, a fork of
this integration). These modules are vendored from that work under its MIT
license, with thanks. Local credentials (AES control key + client
authorization) are obtained via the EZVIZ p2p-register + CAS path wired in
``api.py``.

The flow is:
  1. ``Cpd7LanClient.start()``      INIT/INVITE/PLAY on ports 9010+9020,
                                    encrypted with AES-128-CBC using the
                                    AES key obtained from the EUCAS server.
                                    Generates an ephemeral ECDH P-256 keypair
                                    and embeds the pubkey in the InviteStream.
  2. ``Cpd7LanClient.read_chunk()`` blocking recv from the play socket.
  3. ``StreamDecoder.feed(raw)``    parses RTSP-Interleaved chunks, derives
                                    the per-session ChaCha20 key from the
                                    first ``$\x01`` handshake, and decrypts
                                    every subsequent ``$\x02`` data packet.
  4. ``StreamDecoder.take()``       returns accumulated MPEG-PS bytes,
                                    starting at the first pack header
                                    (``00 00 01 BA``).

Crypto is centralised in ``crypto.py``.
"""
from .lan_client import Cpd7LanClient
from .decoder import StreamDecoder

__all__ = ["Cpd7LanClient", "StreamDecoder"]
