"""Testy src/downloads.py (rejestr linków) i src/download_routes.py (trasa HTTP).

Trasa jest wołana bezpośrednio jako aplikacja ASGI (bez serwera i bez
httpx/TestClient) — sprawdza realne nagłówki i strumieniowane ciało.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import threading
import time
from urllib.parse import unquote

import pytest

import src.downloads as downloads
from src.download_routes import DOWNLOAD_ROUTE, download_endpoint
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route


@pytest.fixture(autouse=True)
def _links_dir(monkeypatch, tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setattr(
        downloads,
        "settings",
        dataclasses.replace(downloads.settings, storage_base_dir=str(base), download_link_ttl_minutes=30),
    )
    yield base
    downloads.purge_all()


def _source(tmp_path, content: bytes = b"zip-bytes", name: str = "job.zip"):
    job_dir = tmp_path / "job"
    job_dir.mkdir(exist_ok=True)
    path = job_dir / name
    path.write_bytes(content)
    return path


def _advance_clock_past_ttl(monkeypatch) -> None:
    # TTL liczony jest od publish(), więc przesuwamy czas do przodu.
    real_monotonic = time.monotonic
    monkeypatch.setattr(downloads.time, "monotonic", lambda: real_monotonic() + 31 * 60)


def _wait_until(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_publish_moves_file_and_registers_unguessable_token(tmp_path):
    source = _source(tmp_path)

    link = downloads.publish(source, "Playlista-X.zip")

    assert not source.exists()
    assert link.path.read_bytes() == b"zip-bytes"
    assert link.file_name == "Playlista-X.zip"
    # token_urlsafe(32) = 43 znaki — nieodgadywalny, nie job_id/nazwa pliku.
    assert len(link.token) >= 43
    assert downloads.lookup(link.token) == link
    assert downloads.download_url(link.token) == f"/api/download/{link.token}"


def test_tokens_are_unique(tmp_path):
    first = downloads.publish(_source(tmp_path, b"a", "a.zip"), "a.zip")
    second = downloads.publish(_source(tmp_path, b"b", "b.zip"), "b.zip")

    assert first.token != second.token
    assert first.path != second.path


def test_lookup_unknown_token_returns_none():
    assert downloads.lookup("does-not-exist") is None


def test_lookup_expired_token_returns_none_and_removes_file(monkeypatch, tmp_path):
    link = downloads.publish(_source(tmp_path), "x.zip")
    _advance_clock_past_ttl(monkeypatch)

    assert downloads.lookup(link.token) is None
    assert _wait_until(lambda: not link.path.parent.exists())


def test_lookup_of_expired_token_deletes_file_in_background_not_inline(monkeypatch, tmp_path):
    """lookup() woła też trasa async — kasowanie nie może iść w jej wątku
    (pętli zdarzeń). Wstrzymane kasowanie: lookup() musi wrócić, zanim
    plik zniknie; gdyby kasował synchronicznie, czekałby na bramkę."""
    link = downloads.publish(_source(tmp_path), "x.zip")
    real_delete = downloads._delete_link_directory
    gate = threading.Event()
    deleting_threads: list[threading.Thread] = []

    def _gated_delete(directory):
        gate.wait(timeout=5)
        deleting_threads.append(threading.current_thread())
        real_delete(directory)

    monkeypatch.setattr(downloads, "_delete_link_directory", _gated_delete)
    _advance_clock_past_ttl(monkeypatch)

    assert downloads.lookup(link.token) is None
    assert deleting_threads == []
    assert link.path.exists()
    # Token znika z rejestru od razu, niezależnie od kasowania pliku.
    assert downloads.lookup(link.token) is None

    gate.set()
    assert _wait_until(lambda: not link.path.parent.exists())
    assert deleting_threads[0] is not threading.current_thread()


def test_lookup_returns_none_when_file_disappeared(tmp_path):
    link = downloads.publish(_source(tmp_path), "x.zip")
    link.path.unlink()

    assert downloads.lookup(link.token) is None


def test_release_removes_directory_and_unknown_token_is_not_an_error(tmp_path):
    link = downloads.publish(_source(tmp_path), "x.zip")

    downloads.release(link.token)
    downloads.release(link.token)
    downloads.release("never-existed")

    assert not link.path.parent.exists()
    assert downloads.lookup(link.token) is None


def test_sweep_expired_removes_only_old_orphan_directories(_links_dir):
    root = _links_dir / downloads._LINKS_DIRNAME
    old_orphan = root / "old-orphan"
    fresh_orphan = root / "fresh-orphan"
    for directory in (old_orphan, fresh_orphan):
        directory.mkdir(parents=True)
        (directory / "payload.zip").write_bytes(b"x")
    long_ago = time.time() - 31 * 60
    os.utime(old_orphan, (long_ago, long_ago))

    downloads.sweep_expired()

    assert not old_orphan.exists()
    assert fresh_orphan.exists()


def test_publish_sweeps_expired_links_of_earlier_jobs(monkeypatch, tmp_path):
    old = downloads.publish(_source(tmp_path, b"old", "old.zip"), "old.zip")
    _advance_clock_past_ttl(monkeypatch)

    downloads.publish(_source(tmp_path, b"new", "new.zip"), "new.zip")

    assert not old.path.exists()


def test_purge_all_removes_everything(tmp_path):
    link = downloads.publish(_source(tmp_path), "x.zip")

    downloads.purge_all()

    assert not link.path.exists()
    assert downloads.lookup(link.token) is None


def _request_for(token: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": downloads.download_url(token),
        "headers": [],
        "path_params": {"token": token},
    }
    return Request(scope)


def _run_asgi(app, scope: dict) -> tuple[int, dict[str, str], bytes]:
    """Wykonuje aplikację ASGI i zbiera status/nagłówki/ciało."""
    messages: list[dict] = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))

    start = next(m for m in messages if m["type"] == "http.response.start")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], headers, body


def _run_response(response: Response) -> tuple[int, dict[str, str], bytes]:
    return _run_asgi(response, {"type": "http", "method": "GET", "headers": [], "path": "/"})


def test_route_pattern_matches_generated_url():
    route = Route(DOWNLOAD_ROUTE, download_endpoint)
    match, child_scope = route.matches(
        {"type": "http", "method": "GET", "path": downloads.download_url("abc_DEF-123")}
    )

    assert match.name == "FULL"
    assert child_scope["path_params"] == {"token": "abc_DEF-123"}


def test_endpoint_streams_file_as_attachment_with_unicode_filename(tmp_path):
    payload = os.urandom(300_000)
    link = downloads.publish(_source(tmp_path, payload), "Playlista-Zażółć gęślą-pozycje-01-07.mp4.zip")

    response = asyncio.run(download_endpoint(_request_for(link.token)))
    status, headers, body = _run_response(response)

    assert status == 200
    assert body == payload
    assert headers["content-type"] == "application/zip"
    assert headers["content-length"] == str(len(payload))
    disposition = headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert "Playlista-Zażółć gęślą-pozycje-01-07.mp4.zip" in unquote(disposition)
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    ("suffix", "expected_content_type"),
    [
        (".mp4", "video/mp4"),
        (".mp3", "audio/mpeg"),
        (".flac", "audio/flac"),
        (".m4a", "audio/mp4"),
        (".srt", "text/plain; charset=utf-8"),
        (".vtt", "text/vtt; charset=utf-8"),
        (".txt", "text/plain; charset=utf-8"),
        (".zip", "application/zip"),
    ],
)
def test_endpoint_content_type_follows_file_extension(tmp_path, suffix, expected_content_type):
    link = downloads.publish(_source(tmp_path, b"payload", f"result{suffix}"), f"Autor-Tytuł{suffix}")

    status, headers, _body = _run_response(asyncio.run(download_endpoint(_request_for(link.token))))

    assert status == 200
    assert headers["content-type"] == expected_content_type


@pytest.mark.parametrize(
    ("suffix", "compressed"),
    [(".flac", False), (".m4a", False), (".mp3", False), (".mp4", False), (".zip", False), (".txt", True)],
)
def test_server_gzip_does_not_recompress_already_compressed_results(tmp_path, suffix, compressed):
    """Serwer st.App przepuszcza odpowiedzi przez GZipMiddleware Starlette
    (minimum_size=500, compresslevel=9) — tu ten sam układ. Audio/wideo/ZIP
    wychodzą bez ponownej kompresji; tekst jest kontrolą, że gzip działa."""
    payload = os.urandom(4096)
    link = downloads.publish(_source(tmp_path, payload, f"result{suffix}"), f"Autor-Tytuł{suffix}")
    app = Starlette(
        routes=[Route(DOWNLOAD_ROUTE, download_endpoint)],
        middleware=[Middleware(GZipMiddleware, minimum_size=500, compresslevel=9)],
    )
    scope = {
        "type": "http",
        "method": "GET",
        "path": downloads.download_url(link.token),
        "headers": [(b"accept-encoding", b"gzip")],
        "query_string": b"",
    }

    status, headers, body = _run_asgi(app, scope)

    assert status == 200
    assert (headers.get("content-encoding") == "gzip") is compressed
    if not compressed:
        assert body == payload


def test_endpoint_returns_404_for_unknown_or_expired_token():
    response = asyncio.run(download_endpoint(_request_for("nope")))
    status, headers, body = _run_response(response)

    assert status == 404
    assert "wygasł" in body.decode()
    assert headers["cache-control"] == "no-store"


def test_endpoint_never_serves_paths_outside_registry(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")

    for token in (str(outside), "../secret.txt", "..%2Fsecret.txt"):
        response = asyncio.run(download_endpoint(_request_for(token)))
        assert response.status_code == 404
