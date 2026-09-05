"""Verify authenticated loopback-only GUI asset delivery."""

from __future__ import annotations

from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

import pytest

from mailarchiver.loopback import LoopbackAssetServer


def test_bootstrap_ticket_is_one_use_and_assets_require_its_cookie(tmp_path: Path) -> None:
    """Requirement: every GUI asset requires a session established by a one-use nonce."""
    (tmp_path / "index.html").write_text("authenticated", encoding="utf-8")
    server = LoopbackAssetServer(tmp_path)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    bootstrap = server.url("index.html", [("window", "one")])
    try:
        with pytest.raises(HTTPError) as unauthenticated:
            urlopen(f"{server.origin}/index.html", timeout=2)
        assert unauthenticated.value.code == 401

        with opener.open(bootstrap, timeout=2) as response:
            assert response.read() == b"authenticated"
            assert response.geturl() == f"{server.origin}/index.html?window=one"

        with pytest.raises(HTTPError) as replayed:
            build_opener(HTTPCookieProcessor(CookieJar())).open(bootstrap, timeout=2)
        assert replayed.value.code == 403
    finally:
        server.close()


def test_authenticated_server_rejects_foreign_origin_and_traversal(tmp_path: Path) -> None:
    """Requirement: loopback authentication does not enable CORS or filesystem traversal."""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    server = LoopbackAssetServer(assets)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        opener.open(server.url("index.html"), timeout=2).close()
        request = Request(f"{server.origin}/index.html", headers={"Origin": "http://127.0.0.1:9"})
        with pytest.raises(HTTPError) as foreign:
            opener.open(request, timeout=2)
        assert foreign.value.code == 403

        with pytest.raises(HTTPError) as traversal:
            opener.open(f"{server.origin}/%2e%2e/outside.txt", timeout=2)
        assert traversal.value.code == 404
    finally:
        server.close()
