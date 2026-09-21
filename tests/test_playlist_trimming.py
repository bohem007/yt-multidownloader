"""MAX_PLAYLIST_ITEMS PRZYCINA zadanie playlisty (nie blokuje) we WSZYSTKICH
trybach (Video/Audio/Napisy/Transkrypt) — mock yt_dlp, bez sieci.

"Cała playlista" obejmuje pozycje 1..N (bezwzględne), "Wybrane numery" —
pierwsze N z posortowanych wybranych. Tury (start_index/next_start_index)
działają WEWNĄTRZ przyciętego zakresu, więc kontynuacja nigdy nie wychodzi
poza pozycję N. Pozycje ponad limit nie trafiają do raportu (ani jako
"skipped"). Ustawienia są PINOWANE w każdym teście (nie zależą od lokalnego .env).
"""

from __future__ import annotations

import zipfile

import pytest

import src.engine as engine_module
from src.config import Settings
from src.engine import DownloadEngine, DownloadJob, EngineError
from src.errors import InvalidPlaylistSelectionError

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL3jltwT7zlHiI4lHQh8fdlHGhw4Lfp5Aq"

MODES = [("video", "mp4"), ("audio", "mp3"), ("subtitle", "srt"), ("transcript", "txt")]
MODE_IDS = [mode for mode, _ in MODES]


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


def _flat_info(count: int) -> dict:
    return {
        "_type": "playlist",
        "title": "Playlista testowa",
        "entries": [{"id": f"vid{i}", "title": f"Tytuł {i}"} for i in range(1, count + 1)],
    }


def _download_info(mode: str, job_dir, video_id: str, media_size: int) -> dict:
    if mode in ("subtitle", "transcript"):
        vtt_path = job_dir / f"{video_id}.en.vtt"
        vtt_path.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nTreść.\n", encoding="utf-8")
        return {
            "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
            "requested_subtitles": {"en": {"filepath": str(vtt_path)}},
            "uploader": "Channel",
            "title": f"Video {video_id}",
        }
    media_path = job_dir / f"{video_id}.{'mp4' if mode == 'video' else 'mp3'}"
    media_path.write_bytes(b"x" * media_size)
    return {
        "requested_downloads": [{"filepath": str(media_path)}],
        "uploader": "Channel",
        "title": f"Video {video_id}",
    }


class _Recorder:
    """Podstawia YoutubeDL: zwraca płaską listę dla sondy i zapisuje, które
    pozycje faktycznie pobrano (kolejność wywołań = kolejność pobrań)."""

    def __init__(self, job_dir, mode: str, total: int, media_size: int = 16) -> None:
        self.job_dir = job_dir
        self.mode = mode
        self.flat_info = _flat_info(total)
        self.media_size = media_size
        self.downloaded: list[str] = []

    def __call__(self, opts: dict) -> _FakeYDL:
        if "extract_flat" in opts:
            return _FakeYDL(opts, self.flat_info)
        recorder = self

        class _DownloadYDL(_FakeYDL):
            def extract_info(self, url: str, download: bool = True) -> dict:
                video_id = url.split("v=")[1]
                recorder.downloaded.append(video_id)
                return _download_info(recorder.mode, recorder.job_dir, video_id, recorder.media_size)

        return _DownloadYDL(opts, {})


def _setup(
    monkeypatch, tmp_path, mode: str, total: int, limit: int, zip_mb: int = 500, media_size: int = 16
) -> _Recorder:
    recorder = _Recorder(tmp_path, mode, total, media_size)
    monkeypatch.setattr(engine_module, "YoutubeDL", recorder)
    monkeypatch.setattr(engine_module.storage, "create", lambda session_id, job_id: tmp_path)
    monkeypatch.setattr(
        engine_module,
        "settings",
        Settings.from_env({"MAX_PLAYLIST_ITEMS": str(limit), "MAX_ZIP_SIZE_MB": str(zip_mb)}),
    )
    return recorder


def _job(mode: str, output_format: str, scope: str = "all") -> DownloadJob:
    return DownloadJob(
        url=PLAYLIST_URL,
        mode=mode,
        output_format=output_format,
        session_id="test-session",
        job_id=f"test-job-trim-{mode}-{scope}",
        playlist_scope=scope,
        subtitle_lang="en" if mode in ("subtitle", "transcript") else None,
    )


@pytest.mark.parametrize(("mode", "output_format"), MODES, ids=MODE_IDS)
def test_all_scope_over_limit_downloads_only_first_n_positions(
    monkeypatch, tmp_path, mode, output_format
):
    recorder = _setup(monkeypatch, tmp_path, mode, total=5, limit=3)

    result = DownloadEngine().submit_playlist(_job(mode, output_format))

    assert recorder.downloaded == ["vid1", "vid2", "vid3"]
    # raport bez "skipped" dla pozycji ponad limit (mogłoby ich być setki)
    assert [(item.index, item.status) for item in result.items] == [
        (1, "done"),
        (2, "done"),
        (3, "done"),
    ]
    assert result.next_start_index is None
    assert result.stopped_early_reason is None
    with zipfile.ZipFile(result.zip_path) as zf:
        names = sorted(zf.namelist())
    assert len(names) == 3
    assert [n[:2] for n in names] == ["01", "02", "03"]


@pytest.mark.parametrize(("mode", "output_format"), MODES, ids=MODE_IDS)
def test_all_scope_at_or_under_limit_is_not_trimmed(monkeypatch, tmp_path, mode, output_format):
    recorder = _setup(monkeypatch, tmp_path, mode, total=3, limit=3)

    result = DownloadEngine().submit_playlist(_job(mode, output_format))

    assert recorder.downloaded == ["vid1", "vid2", "vid3"]
    assert [item.status for item in result.items] == ["done", "done", "done"]


@pytest.mark.parametrize(("mode", "output_format"), MODES, ids=MODE_IDS)
def test_progress_counts_against_the_trimmed_range(monkeypatch, tmp_path, mode, output_format):
    _setup(monkeypatch, tmp_path, mode, total=5, limit=3)
    events = []

    DownloadEngine().submit_playlist(_job(mode, output_format), on_event=events.append)

    progress = [e for e in events if e.event_type == "on_progress"]
    assert [e.message for e in progress] == [
        "Pobrano 1 z 3 pozycji",
        "Pobrano 2 z 3 pozycji",
        "Pobrano 3 z 3 pozycji",
    ]
    assert progress[-1].percent == pytest.approx(100.0)


@pytest.mark.parametrize(("mode", "output_format"), MODES, ids=MODE_IDS)
def test_resume_start_index_works_inside_the_trimmed_range(monkeypatch, tmp_path, mode, output_format):
    recorder = _setup(monkeypatch, tmp_path, mode, total=5, limit=4)

    result = DownloadEngine().submit_playlist(_job(mode, output_format), start_index=3)

    assert recorder.downloaded == ["vid3", "vid4"]
    assert [item.index for item in result.items] == [3, 4]
    assert result.next_start_index is None


@pytest.mark.parametrize(("mode", "output_format"), MODES, ids=MODE_IDS)
def test_selected_over_limit_takes_first_n_of_the_sorted_selection(
    monkeypatch, tmp_path, mode, output_format
):
    recorder = _setup(monkeypatch, tmp_path, mode, total=32, limit=2)

    result = DownloadEngine().submit_playlist(
        _job(mode, output_format, scope="selected"), selected_indices=[20, 3, 9]
    )

    assert recorder.downloaded == ["vid3", "vid9"]
    assert [(item.index, item.status) for item in result.items] == [(3, "done"), (9, "done")]
    assert result.next_start_index is None


@pytest.mark.parametrize(("mode", "output_format"), MODES, ids=MODE_IDS)
def test_selected_within_limit_is_not_trimmed_even_for_a_long_playlist(
    monkeypatch, tmp_path, mode, output_format
):
    recorder = _setup(monkeypatch, tmp_path, mode, total=32, limit=2)

    result = DownloadEngine().submit_playlist(
        _job(mode, output_format, scope="selected"), selected_indices=[20, 5]
    )

    assert recorder.downloaded == ["vid5", "vid20"]
    assert [item.status for item in result.items] == ["done", "done"]


def test_selected_numbers_outside_the_playlist_are_still_rejected_before_trimming(
    monkeypatch, tmp_path
):
    """Walidacja zakresu 1..długość playlisty idzie PRZED przycięciem —
    numer 99 jest błędem, nawet gdyby wypadł poza pierwsze N wybranych."""
    recorder = _setup(monkeypatch, tmp_path, "video", total=5, limit=2)

    with pytest.raises(EngineError) as exc_info:
        DownloadEngine().submit_playlist(
            _job("video", "mp4", scope="selected"), selected_indices=[1, 2, 99]
        )

    assert isinstance(exc_info.value.original_exception, InvalidPlaylistSelectionError)
    assert recorder.downloaded == []


def test_zip_size_turns_stay_inside_the_trimmed_range(monkeypatch, tmp_path):
    """Tury służą wyłącznie podziałowi wg MAX_ZIP_SIZE_MB i działają WEWNĄTRZ
    zakresu 1..N: kontynuacja kończy się na pozycji N (next_start_index=None),
    a pozycje 4-5 (ponad limit 3) nigdy nie pojawiają się ani jako pobrane, ani
    jako "skipped". Każda pozycja ma ~1,2 MB przy limicie ZIP-a 1 MB, więc każda
    tura zatrzymuje się po jednej pozycji."""
    recorder = _setup(
        monkeypatch, tmp_path, "video", total=5, limit=3, zip_mb=1, media_size=1_300_000
    )
    engine = DownloadEngine()

    def _run_turn(start_index: int):
        for leftover in tmp_path.iterdir():  # ten sam job_dir między turami (jak w testach ZIP-a)
            leftover.unlink()
        return engine.submit_playlist(_job("video", "mp4"), start_index=start_index)

    turn1 = _run_turn(1)
    assert [(i.index, i.status) for i in turn1.items] == [(1, "done"), (2, "skipped"), (3, "skipped")]
    assert turn1.next_start_index == 2

    turn2 = _run_turn(turn1.next_start_index)
    assert [(i.index, i.status) for i in turn2.items] == [(2, "done"), (3, "skipped")]
    assert turn2.next_start_index == 3

    turn3 = _run_turn(turn2.next_start_index)
    assert [(i.index, i.status) for i in turn3.items] == [(3, "done")]
    assert turn3.next_start_index is None  # koniec na N — brak kontynuacji ponad limit
    assert recorder.downloaded == ["vid1", "vid2", "vid3"]
