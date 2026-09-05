"""Authenticated loopback-only delivery of packaged GUI assets."""

from __future__ import annotations

import hmac
import mimetypes
import secrets
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from threading import Lock, Thread
from urllib.parse import quote, urlencode, urlsplit


class LoopbackAssetServer:
    """Serve a fixed asset tree after one-use bootstrap-cookie authentication."""

    def __init__(self, asset_root: Path) -> None:
        self.asset_root = asset_root.resolve(strict=True)
        self._session = secrets.token_urlsafe(32)
        self._cookie_name = f"mailarchiver_{secrets.token_hex(8)}"
        self._tickets: dict[str, str] = {}
        self._lock = Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                owner._get(self)

            def do_HEAD(self) -> None:  # noqa: N802
                owner._get(self, head=True)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        host, port = self._server.server_address
        self.origin = f"http://{host}:{port}"
        self.authority = f"{host}:{port}"
        self._thread = Thread(
            target=self._server.serve_forever,
            name="mailarchiver-loopback",
            daemon=True,
        )
        self._thread.start()

    def url(self, asset: str, parameters: list[tuple[str, str]] | None = None) -> str:
        """Create a one-use bootstrap URL that redirects without retaining its ticket."""
        relative = self._asset_path(asset)
        target = f"/{quote(relative.as_posix())}"
        if parameters:
            target += f"?{urlencode(parameters)}"
        ticket = secrets.token_urlsafe(32)
        with self._lock:
            self._tickets[ticket] = target
        return f"{self.origin}/bootstrap/{ticket}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        with self._lock:
            self._tickets.clear()
        self._session = ""

    def _get(self, request: BaseHTTPRequestHandler, *, head: bool = False) -> None:
        if request.headers.get("Host") != self.authority:
            self._error(request, HTTPStatus.FORBIDDEN)
            return
        origin = request.headers.get("Origin")
        if origin is not None and origin != self.origin:
            self._error(request, HTTPStatus.FORBIDDEN)
            return
        path = urlsplit(request.path).path
        if path.startswith("/bootstrap/"):
            ticket = path.removeprefix("/bootstrap/")
            with self._lock:
                target = self._tickets.pop(ticket, None)
            if target is None:
                self._error(request, HTTPStatus.FORBIDDEN)
                return
            request.send_response(HTTPStatus.SEE_OTHER)
            request.send_header(
                "Set-Cookie",
                f"{self._cookie_name}={self._session}; HttpOnly; SameSite=Strict; Path=/",
            )
            request.send_header("Location", target)
            request.send_header("Cache-Control", "no-store")
            request.end_headers()
            return
        if not self._authenticated(request):
            self._error(request, HTTPStatus.UNAUTHORIZED)
            return
        try:
            relative = self._asset_path(path.removeprefix("/"))
            asset = (self.asset_root / relative).resolve(strict=True)
            asset.relative_to(self.asset_root)
            if not asset.is_file():
                raise FileNotFoundError(asset)
            content = asset.read_bytes()
        except (OSError, ValueError):
            self._error(request, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
        request.send_response(HTTPStatus.OK)
        request.send_header("Content-Type", content_type)
        request.send_header("Content-Length", str(len(content)))
        request.send_header("Cache-Control", "no-store")
        request.send_header("Referrer-Policy", "no-referrer")
        request.send_header("X-Content-Type-Options", "nosniff")
        request.end_headers()
        if not head:
            request.wfile.write(content)

    def _authenticated(self, request: BaseHTTPRequestHandler) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(request.headers.get("Cookie", ""))
            value = cookie[self._cookie_name].value
        except (KeyError, ValueError):
            return False
        return bool(self._session) and hmac.compare_digest(value, self._session)

    def _asset_path(self, value: str) -> PurePosixPath:
        relative = PurePosixPath(value)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("invalid GUI asset path")
        return relative

    @staticmethod
    def _error(request: BaseHTTPRequestHandler, status: HTTPStatus) -> None:
        request.send_response(status)
        request.send_header("Content-Length", "0")
        request.send_header("Cache-Control", "no-store")
        request.send_header("X-Content-Type-Options", "nosniff")
        request.end_headers()
