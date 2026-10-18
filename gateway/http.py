"""Minimal HTTP/1.1 + WebSocket (RFC 6455) server primitives on asyncio streams.

No web framework: request parsing, responses (gzip for large JSON, no-store for the
API), static files with correct MIME types and path-traversal protection, and
WebSocket handshake/framing.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import mimetypes
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_BODY = 12 * 1024 * 1024
MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".woff2": "font/woff2", ".woff": "font/woff",
    ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
    ".webm": "video/webm", ".mp4": "video/mp4", ".glsl": "text/plain; charset=utf-8",
}
REASONS = {200: "OK", 204: "No Content", 301: "Moved Permanently", 302: "Found", 304: "Not Modified",
           400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
           405: "Method Not Allowed", 409: "Conflict", 413: "Payload Too Large", 500: "Internal Server Error",
           503: "Service Unavailable"}


class HttpError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message or REASONS.get(status, "Error")


@dataclass
class Request:
    method: str
    target: str
    path: str
    query: Dict[str, list]
    headers: Dict[str, str]
    body: bytes
    peer: Tuple[str, int]
    reader: asyncio.StreamReader = field(repr=False)
    writer: asyncio.StreamWriter = field(repr=False)

    def q(self, name: str, default: Optional[str] = None) -> Optional[str]:
        v = self.query.get(name)
        return v[0] if v else default

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpError(400, "body must be JSON")

    @property
    def is_local(self) -> bool:
        host = self.peer[0] if self.peer else ""
        return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.") or host == "::ffff:127.0.0.1"


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, data, status: int = 200, cache: bool = False) -> "Response":
        body = json.dumps(data, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        h = {"Content-Type": "application/json; charset=utf-8"}
        if not cache:
            h["Cache-Control"] = "no-store"
        return cls(status, body, h)

    @classmethod
    def text(cls, text: str, status: int = 200, ctype: str = "text/plain; charset=utf-8") -> "Response":
        return cls(status, text.encode("utf-8"), {"Content-Type": ctype, "Cache-Control": "no-store"})

    @classmethod
    def redirect(cls, location: str) -> "Response":
        return cls(302, b"", {"Location": location})


async def read_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> Optional[Request]:
    try:
        line = await reader.readline()
    except (ConnectionError, asyncio.LimitOverrunError):
        return None
    if not line:
        return None
    try:
        method, target, _version = line.decode("latin-1").strip().split(" ", 2)
    except ValueError:
        raise HttpError(400, "bad request line")
    headers: Dict[str, str] = {}
    while True:
        h = await reader.readline()
        if not h or h in (b"\r\n", b"\n"):
            break
        if len(headers) > 100:
            raise HttpError(400, "too many headers")
        k, _, v = h.decode("latin-1").partition(":")
        headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", "0") or 0)
    if length > MAX_BODY:
        raise HttpError(413)
    body = await reader.readexactly(length) if length else b""
    parts = urlsplit(target)
    return Request(method.upper(), target, unquote(parts.path), parse_qs(parts.query), headers, body,
                   writer.get_extra_info("peername") or ("", 0), reader, writer)


async def write_response(req: Optional[Request], writer: asyncio.StreamWriter, resp: Response) -> None:
    body = resp.body
    headers = dict(resp.headers)
    ctype = headers.get("Content-Type", "")
    if (req is not None and len(body) > 8192 and "gzip" in req.headers.get("accept-encoding", "")
            and ("json" in ctype or "javascript" in ctype or "text/" in ctype)):
        body = gzip.compress(body, compresslevel=5)
        headers["Content-Encoding"] = "gzip"
        headers["Vary"] = "Accept-Encoding"
    headers["Content-Length"] = str(len(body))
    headers.setdefault("Connection", "keep-alive")
    head = f"HTTP/1.1 {resp.status} {REASONS.get(resp.status, 'OK')}\r\n"
    head += "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
    writer.write(head.encode("latin-1") + (body if (req is None or req.method != "HEAD") else b""))
    await writer.drain()


def static_file(root: Path, rel: str) -> Response:
    """Serve root/rel; refuses anything that resolves outside root."""
    root = root.resolve()
    rel = rel.lstrip("/")
    try:
        p = (root / rel).resolve()
    except (OSError, ValueError):
        raise HttpError(404)
    if root != p and root not in p.parents:
        raise HttpError(403, "outside the static root")
    if p.is_dir():
        p = p / "index.html"
    if not p.is_file():
        raise HttpError(404)
    ext = p.suffix.lower()
    ctype = MIME.get(ext) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    headers = {"Content-Type": ctype}
    headers["Cache-Control"] = "no-cache" if ext in (".html", ".js", ".css", ".json") else "max-age=3600"
    return Response(200, p.read_bytes(), headers)


# ----------------------------------------------------------------------------------------
# WebSocket
# ----------------------------------------------------------------------------------------
def ws_accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode("latin-1")).digest()).decode("ascii")


async def ws_handshake(req: Request) -> bool:
    if req.headers.get("upgrade", "").lower() != "websocket" or "sec-websocket-key" not in req.headers:
        return False
    accept = ws_accept_key(req.headers["sec-websocket-key"])
    resp = ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n")
    req.writer.write(resp.encode("latin-1"))
    await req.writer.drain()
    return True


def ws_frame(payload: bytes, opcode: int = 0x1, mask: bool = False) -> bytes:
    """Build one final frame. Servers send unmasked frames; clients must mask."""
    head = bytes([0x80 | opcode])
    n = len(payload)
    mbit = 0x80 if mask else 0
    if n < 126:
        head += bytes([mbit | n])
    elif n < 65536:
        head += bytes([mbit | 126]) + struct.pack("!H", n)
    else:
        head += bytes([mbit | 127]) + struct.pack("!Q", n)
    if mask:
        key = os.urandom(4)
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        head += key
    return head + payload


async def ws_read_frame(reader: asyncio.StreamReader) -> Tuple[int, bytes]:
    """Read one complete message (handles fragmentation). Returns (opcode, payload)."""
    chunks = []
    first_op = None
    while True:
        b1, b2 = await reader.readexactly(2)
        fin, op = b1 & 0x80, b1 & 0x0F
        masked, n = b2 & 0x80, b2 & 0x7F
        if n == 126:
            (n,) = struct.unpack("!H", await reader.readexactly(2))
        elif n == 127:
            (n,) = struct.unpack("!Q", await reader.readexactly(8))
        if n > MAX_BODY:
            raise HttpError(413)
        key = await reader.readexactly(4) if masked else b""
        data = await reader.readexactly(n) if n else b""
        if masked:
            data = bytes(b ^ key[i % 4] for i, b in enumerate(data))
        if op >= 0x8:  # control frames may interleave
            return op, data
        if first_op is None:
            first_op = op
        chunks.append(data)
        if fin:
            return first_op, b"".join(chunks)
