"""Testy migawki listy Mix/Radio (list=RD…) w engine.py — mock yt_dlp, bez sieci.

Założenie użytkownika potwierdzone w Fazie 0 (eksperymenty na realnym YouTube):
lista mixa zmienia się między odczytami (wspólnych tylko 5-9 z 20 pozycji),
więc tury/wybrane numery muszą pracować na JEDNEJ migawce. Te testy pilnują,
że silnik nigdy nie odpytuje playlisty ponownie, gdy dostał migawkę.
"""

from __future__ import annotations

import pytest

import src.engine as engine_module
from src.config import Settings
from src.engine import (
    DownloadEngine,
    DownloadJob,
    EngineError,
    PlaylistSnapshot,
    snapshot_playlist,
)
from src.errors import (
    EmptyPlaylistSnapshotError,
    InvalidPlaylistSelectionError,
    InvalidUrlError,
)

MIX_URL = "https://www.youtube.com/watch?v=seed0000001&list=RDseed0000001"


class _FakeYDL:
    def __init__(self, opts: dict, info: dict) -> None:
        self.opts = opts
        self._info = info

    def __enter__(self) -> "_FakeYDL":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def extract_info(self, url: str, download: bool = True) -> dict:
        return self._info


def _flat_info(ids: list[str], title: str = "Mix - Test") -> dict:
    return {
        "_type": "playlist",
        "title": title,
        "entries": [
            {"id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": f"Tytuł {vid}"}
            for vid in ids
        ],
    }


def _snapshot(ids: list[str]) -> PlaylistSnapshot:
    entries = tuple(
        {"id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": f"Tytuł {vid}"}
        for vid in ids
    )
    return PlaylistSnapshot(url=MIX_URL, title="Mix - Test", entries=entries)


class _Recorder:
    """Podstawia YoutubeDL: zlicza odczyty flat (sonda listy) i zapisuje URL-e
    faktycznych pobrań wraz z noplaylist. Odczyt flat zwraca INNĄ listę niż
    migawka — gdyby silnik ją odczytał, pozycje w testach by się rozjechały."""

    def __init__(self, tmp_path, flat_info: dict | None = None) -> None:
        self.tmp_path = tmp_path
        self.flat_info = flat_info or _flat_info(["other1", "other2", "other3"])
        self.flat_reads: list[dict] = []
        self.downloads: list[tuple[str, bool]] = []

    def __call__(self, opts: dict) -> _FakeYDL:
        if "extract_flat" in opts:
            self.flat_reads.append(opts)
            return _FakeYDL(opts, self.flat_info)

        recorder = self

        class _DownloadYDL(_FakeYDL):
            def extract_info(self, url: str, download: bool = True) -> dict:
                recorder.downloads.append((url, bool(opts.get("noplaylist"))))
                video_id = url.split("v=")[1]
                media_path = recorder.tmp_path / f"Raw-{video_id}.mp4"
                media_path.write_bytes(b"fake mp4 bytes")
                return {
                    "requested_downloads": [{"filepath": str(media_path)}],
                    "uploader": "Channel",
                    "title": f"Video {video_id}",
                }

        return _DownloadYDL(opts, {})


def _install(monkeypatch, tmp_path, settings_env: dict | None = None, **recorder_kwargs) -> _Recorder:
    recorder = _Recorder(tmp_path, **recorder_kwargs)
    monkeypatch.setattr(engine_module, "YoutubeDL", recorder)
    monkeypatch.setattr(engine_module.storage, "create", lambda session_id, job_id: tmp_path)
    if settings_env is not None:
        monkeypatch.setattr(engine_module, "settings", Settings.from_env(settings_env))
    return recorder


def _job(**overrides) -> DownloadJob:
    fields = dict(
        url=MIX_URL,
        mode="video",
        output_format="mp4",
        session_id="test-session",
        job_id="test-job-mix",
        playlist_scope="all",
    )
    fields.update(overrides)
    return DownloadJob(**fields)


# --- snapshot_playlist ------------------------------------------------------


def test_snapshot_is_trimmed_to_max_playlist_rd_items_and_limits_the_read(monkeypatch, tmp_path):
    recorder = _install(
        monkeypatch,
        tmp_path,
        {"MAX_PLAYLIST_RD_ITEMS": "3"},
        flat_info=_flat_info(["a", "b", "c", "d", "e"]),
    )

    snapshot = snapshot_playlist(MIX_URL)

    assert [e["id"] for e in snapshot.entries] == ["a", "b", "c"]
    assert snapshot.title == "Mix - Test"
    assert snapshot.url == MIX_URL
    assert all(set(e) == {"id", "url", "title"} for e in snapshot.entries)
    # limit odczytu przekazany do yt-dlp (bez niego odczyt mixa: 13-27 s, setki pozycji)
    assert len(recorder.flat_reads) == 1
    assert recorder.flat_reads[0]["playlistend"] == 3


def test_snapshot_shorter_than_limit_keeps_what_exists(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, {}, flat_info=_flat_info(["a", "b", "c", "d"]))

    snapshot = snapshot_playlist(MIX_URL)

    assert [e["id"] for e in snapshot.entries] == ["a", "b", "c", "d"]


def test_snapshot_builds_video_url_from_id_when_entry_has_no_url(monkeypatch, tmp_path):
    info = {"_type": "playlist", "title": "Mix", "entries": [{"id": "abc"}, {"title": "bez id i url"}]}
    _install(monkeypatch, tmp_path, {}, flat_info=info)

    snapshot = snapshot_playlist(MIX_URL)

    assert snapshot.entries == (
        {"id": "abc", "url": "https://www.youtube.com/watch?v=abc", "title": None},
    )


@pytest.mark.parametrize(
    "info",
    [{"_type": "playlist", "title": "Mix", "entries": []}, {"_type": "video", "title": "Nie playlista"}],
)
def test_empty_snapshot_raises_instead_of_returning_empty_list(monkeypatch, tmp_path, info):
    _install(monkeypatch, tmp_path, {}, flat_info=info)

    with pytest.raises(EmptyPlaylistSnapshotError):
        snapshot_playlist(MIX_URL)


def test_snapshot_rejects_non_youtube_url(monkeypatch, tmp_path):
    recorder = _install(monkeypatch, tmp_path, {})

    with pytest.raises(InvalidUrlError):
        snapshot_playlist("https://evil.example.com/watch?v=x&list=RDx")

    assert recorder.flat_reads == []


# --- submit_playlist z migawką ----------------------------------------------


def test_submit_playlist_with_snapshot_never_reads_the_playlist_again(monkeypatch, tmp_path):
    recorder = _install(monkeypatch, tmp_path)
    snapshot = _snapshot(["s1", "s2", "s3"])

    result = DownloadEngine().submit_playlist(_job(playlist_snapshot=snapshot))

    assert recorder.flat_reads == []
    assert [url.split("v=")[1] for url, _ in recorder.downloads] == ["s1", "s2", "s3"]
    assert [item.status for item in result.items] == ["done", "done", "done"]
    assert result.playlist_title == "Mix - Test"
    # każda pozycja jako pojedyncze wideo
    assert all(noplaylist for _, noplaylist in recorder.downloads)


def test_second_turn_takes_next_items_from_the_same_snapshot(monkeypatch, tmp_path):
    """Tura 2 (start_index=3) pobiera dokładnie kolejne pozycje MIGAWKI, nawet
    gdyby "drugi odczyt" listy zwrócił zupełnie inne wideo (a zwróciłby —
    _Recorder podsuwa inną listę pod extract_flat)."""
    recorder = _install(monkeypatch, tmp_path)
    snapshot = _snapshot(["s1", "s2", "s3", "s4", "s5"])

    result = DownloadEngine().submit_playlist(_job(playlist_snapshot=snapshot), start_index=3)

    assert recorder.flat_reads == []
    assert [url.split("v=")[1] for url, _ in recorder.downloads] == ["s3", "s4", "s5"]
    assert [item.index for item in result.items] == [3, 4, 5]
    assert result.next_start_index is None


def test_snapshot_replaces_max_playlist_items_limit(monkeypatch, tmp_path):
    """Limit dla mixa to MAX_PLAYLIST_RD_ITEMS (wbudowany w rozmiar migawki),
    a nie MAX_PLAYLIST_ITEMS — 4 pozycje w trybie Video przy limicie zwykłych
    playlist = 2 przechodzą."""
    _install(monkeypatch, tmp_path, {"MAX_PLAYLIST_ITEMS": "2"})
    snapshot = _snapshot(["s1", "s2", "s3", "s4"])

    result = DownloadEngine().submit_playlist(_job(playlist_snapshot=snapshot))

    assert [item.status for item in result.items] == ["done"] * 4


def test_selected_indices_refer_to_snapshot_positions(monkeypatch, tmp_path):
    recorder = _install(monkeypatch, tmp_path)
    snapshot = _snapshot(["s1", "s2", "s3", "s4", "s5"])
    job = _job(playlist_scope="selected", playlist_snapshot=snapshot)

    result = DownloadEngine().submit_playlist(job, selected_indices=[4, 2])

    assert recorder.flat_reads == []
    assert [url.split("v=")[1] for url, _ in recorder.downloads] == ["s2", "s4"]
    assert [item.index for item in result.items] == [2, 4]
    assert result.next_start_index is None


def test_selected_indices_outside_snapshot_are_rejected(monkeypatch, tmp_path):
    """Backstop silnika działa dla migawki: numer 6 przy 5 pozycjach migawki."""
    recorder = _install(monkeypatch, tmp_path)
    snapshot = _snapshot(["s1", "s2", "s3", "s4", "s5"])
    job = _job(playlist_scope="selected", playlist_snapshot=snapshot)

    with pytest.raises(EngineError) as exc_info:
        DownloadEngine().submit_playlist(job, selected_indices=[2, 6])

    assert isinstance(exc_info.value.original_exception, InvalidPlaylistSelectionError)
    assert recorder.downloads == []


def test_empty_snapshot_in_job_is_an_error_not_an_empty_zip(monkeypatch, tmp_path):
    recorder = _install(monkeypatch, tmp_path)
    empty = PlaylistSnapshot(url=MIX_URL, title="Mix", entries=())

    with pytest.raises(EngineError) as exc_info:
        DownloadEngine().submit_playlist(_job(playlist_snapshot=empty))

    assert isinstance(exc_info.value.original_exception, EmptyPlaylistSnapshotError)
    assert recorder.downloads == []


def test_regular_playlist_without_snapshot_still_reads_the_playlist(monkeypatch, tmp_path):
    """Regresja: zwykła playlista (bez migawki) nadal odczytuje listę jak
    dotąd i nie wymusza noplaylist na pozycjach."""
    recorder = _install(
        monkeypatch, tmp_path, flat_info=_flat_info(["p1", "p2"], title="Zwykła playlista")
    )

    result = DownloadEngine().submit_playlist(
        _job(url="https://www.youtube.com/playlist?list=PLregular1")
    )

    assert len(recorder.flat_reads) == 1
    assert "playlistend" not in recorder.flat_reads[0]
    assert [url.split("v=")[1] for url, _ in recorder.downloads] == ["p1", "p2"]
    assert not any(noplaylist for _, noplaylist in recorder.downloads)
    assert result.playlist_title == "Zwykła playlista"
