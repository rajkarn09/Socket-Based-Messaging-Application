"""
protocol.py  –  Custom TCP Framing Protocol
=============================================
Implements a simple but robust length-prefixed framing scheme so that
JSON messages can be reliably sent and received over a TCP stream.

Wire format for every packet:
    [4 bytes – big-endian uint32 payload length] [payload bytes (UTF-8 JSON)]

Why length-prefixed framing?
    TCP is a *stream* protocol – it does NOT preserve message boundaries.
    Without explicit framing, two small sends may arrive as one big recv
    ("sticky packets") or one large send may arrive in many small chunks
    ("fragmentation").  The 4-byte header tells the receiver exactly how
    many bytes to read, eliminating both problems.

Public helpers
--------------
    send_message(sock, msg_dict)   – serialize dict → JSON → framed bytes → send
    recv_message(sock)             – recv framed bytes → JSON → dict (or None on disconnect)
"""

import json
import struct
import socket
import logging

# ── Logger for this module ──────────────────────────────────────────
logger = logging.getLogger("protocol")

# Maximum payload size we are willing to accept (16 MB).
# This prevents a malicious or buggy peer from making us allocate
# unbounded memory.
MAX_PAYLOAD_SIZE = 16 * 1024 * 1024  # 16 MB

# The struct format for our 4-byte big-endian unsigned-int header.
HEADER_FORMAT = "!I"              # network byte-order, unsigned 32-bit int
HEADER_SIZE   = struct.calcsize(HEADER_FORMAT)  # == 4


# ────────────────────────────────────────────────────────────────────
#  Low-level helpers
# ────────────────────────────────────────────────────────────────────

def _send_all(sock: socket.socket, data: bytes) -> None:
    """
    Send *all* bytes in `data` over the socket.

    socket.sendall() already handles partial sends internally, but we
    wrap it here so that every network write goes through a single
    function that we can log / instrument later.
    """
    sock.sendall(data)


def _recv_exactly(sock: socket.socket, num_bytes: int) -> bytes | None:
    """
    Read exactly `num_bytes` from the socket.

    Returns the bytes on success, or None if the connection was closed
    before all bytes could be read (clean disconnect).

    Raises
    ------
    ConnectionError
        If the socket reports an error mid-read.
    """
    buffer = bytearray()
    while len(buffer) < num_bytes:
        remaining = num_bytes - len(buffer)
        try:
            chunk = sock.recv(remaining)
        except OSError:
            # Socket was closed or reset by the other side.
            return None
        if not chunk:
            # recv() returned b"" → peer closed the connection.
            return None
        buffer.extend(chunk)
    return bytes(buffer)


# ────────────────────────────────────────────────────────────────────
#  Public API
# ────────────────────────────────────────────────────────────────────

def send_message(sock: socket.socket, msg_dict: dict) -> None:
    """
    Serialize `msg_dict` as JSON, frame it with a 4-byte length header,
    and send the resulting bytes over `sock`.

    Parameters
    ----------
    sock : socket.socket
        A connected TCP socket.
    msg_dict : dict
        The message to send.  Must be JSON-serializable.

    Raises
    ------
    ConnectionError / OSError
        If the socket write fails.
    """
    # 1. Serialize dictionary → JSON string → UTF-8 bytes
    payload = json.dumps(msg_dict, ensure_ascii=False).encode("utf-8")

    # 2. Build the 4-byte length header
    header = struct.pack(HEADER_FORMAT, len(payload))

    # 3. Send header + payload as one contiguous write
    _send_all(sock, header + payload)
    logger.debug("SENT  %d bytes  type=%s", len(payload), msg_dict.get("type"))


def recv_message(sock: socket.socket) -> dict | None:
    """
    Read one length-prefixed packet from `sock` and return the
    deserialized dict.

    Returns
    -------
    dict
        The parsed message on success.
    None
        If the peer closed the connection cleanly.

    Raises
    ------
    ValueError
        If the payload exceeds MAX_PAYLOAD_SIZE or is not valid JSON.
    ConnectionError / OSError
        If the socket read fails unexpectedly.
    """
    # 1. Read the 4-byte header
    header_bytes = _recv_exactly(sock, HEADER_SIZE)
    if header_bytes is None:
        # Clean disconnect – no data at all
        return None

    # 2. Unpack payload length
    (payload_length,) = struct.unpack(HEADER_FORMAT, header_bytes)

    if payload_length > MAX_PAYLOAD_SIZE:
        raise ValueError(
            f"Payload length {payload_length} exceeds maximum "
            f"allowed size of {MAX_PAYLOAD_SIZE} bytes."
        )

    if payload_length == 0:
        # Edge case: zero-length payload → treat as empty dict
        return {}

    # 3. Read exactly `payload_length` bytes
    payload_bytes = _recv_exactly(sock, payload_length)
    if payload_bytes is None:
        # Connection dropped mid-message
        return None

    # 4. Decode UTF-8 → JSON → dict
    try:
        msg_dict = json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid JSON payload: {exc}") from exc

    logger.debug("RECV  %d bytes  type=%s", payload_length, msg_dict.get("type"))
    return msg_dict
