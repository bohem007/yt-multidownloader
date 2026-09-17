"""Test integracyjny engine.py — realne pobranie krótkiego, publicznego klipu.

Oznaczony @pytest.mark.slow — pomijany przez `pytest -m "not slow"`.
Wymaga dostępu do internetu i ffmpeg na PATH.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.engine as engine_module
from src import storage
from src.config import settings
from src.engine import DownloadEngine, DownloadJob, EngineError, list_available_subtitles
from src.errors import InvalidUrlError, PlaylistTooLargeError
from src.progress import ProgressEvent

# "Me at the zoo" — pierwsze wideo wgrane na YouTube, ~19s, publiczne,
# stabilne od 2005 roku — dobry, szybki fixture do testu integracyjnego.
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

# Film z polskim audio/napisami, na którym wykryto błąd "brak napisów" —
# domyślne subtitleslangs=["en"] w yt-dlp nic nie znajdowało.
POLISH_TEST_VIDEO_URL = "https://www.youtube.com/watch?v=6eBSHbLKuN0"

# URL z briefu "Poprawna obsługa URL-i z playlisty" — v= i list= razem
# (typowy link "autoplay z listy"). Bez noplaylist=True yt-dlp domyślnie
# rozwiązuje ten URL jako CAŁĄ playlistę (potwierdzone manualnie: 'entries'
# w info_dict, brak 'duration' pojedynczego wideo) — dokładnie zgłoszony bug.
MIXED_PLAYLIST_URL = "https://www.youtube.com/watch?v=uXlzoi70qUY&list=PL3jltwT7zlHiI4lHQh8fdlHGhw4Lfp5Aq"

# TED talk (Ken Robinson, "Do schools kill creativity?") — manualne napisy
# EN z prawdziwą interpunkcją, ~20 minut. TEST_VIDEO_URL ("Me at the zoo",
# 19s) ma manualne napisy BEZ ŻADNEJ kończącej interpunkcji (potwierdzone
# manualnie: cały transkrypt to jedno "zdanie") — strukturalnie nie może
# wygenerować >1 akapitu, więc test podziału na akapity potrzebuje dłuższego
# materiału z realną interpunkcją.
LONG_TEST_VIDEO_URL = "https://www.youtube.com/watch?v=iG9CE55wbtY"


def test_submit_invalid_url_raises_engine_error_with_original_exception():
    engine = DownloadEngine()

    job = DownloadJob(
        url="https://vimeo.com/12345",
        mode="audio",
        output_format="mp3",
        session_id="test-session",
        job_id="test-job-invalid-url",
    )

    with pytest.raises(EngineError) as exc_info:
        engine.submit(job)

    engine_error = exc_info.value
    assert isinstance(engine_error.original_exception, InvalidUrlError)


@pytest.mark.slow
def test_engine_submit_downloads_audio_and_respects_size_limit():
    engine = DownloadEngine()
    events: list[ProgressEvent] = []

    job = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="audio",
        output_format="mp3",
        session_id="test-session",
        job_id="test-job",
    )

    result = None
    try:
        result = engine.submit(job, on_event=events.append)

        assert result.path.exists()
        assert result.path.is_file()
        assert result.path.stat().st_size > 0
        assert result.uploader
        assert result.title

        # enforce_size_limit już przeszedł wewnątrz submit() bez wyjątku —
        # tu tylko potwierdzamy, że wynik faktycznie jest pod limitem.
        storage.enforce_size_limit(result.path)

        assert any(event.event_type == "on_finished" for event in events)
    finally:
        if result is not None:
            storage.cleanup(result.path.parent)


@pytest.mark.slow
def test_engine_submit_mixed_playlist_url_with_single_scope_downloads_only_that_video():
    """Kryterium akceptacji z briefu: URL z v= i list=, playlist_scope="single"
    (domyślny) + Tryb Audio → pobiera WYŁĄCZNIE wskazane wideo, bez błędu
    "Nie udało się ustalić ścieżki pliku wynikowego" (który wystąpiłby, gdyby
    yt-dlp po cichu ściągnęło wiele plików całej playlisty — _resolve_result
    zakłada jeden plik wynikowy)."""
    engine = DownloadEngine()
    job = DownloadJob(
        url=MIXED_PLAYLIST_URL,
        mode="audio",
        output_format="mp3",
        session_id="test-session",
        job_id="test-job-mixed-playlist-single",
        playlist_scope="single",
    )

    result = None
    try:
        result = engine.submit(job)
        assert result.path.exists()
        assert result.path.suffix == ".mp3"
    finally:
        if result is not None:
            storage.cleanup(result.path.parent)


@pytest.mark.slow
def test_engine_submit_audio_mp3_returns_path_with_mp3_extension():
    """Regresja: submit() musi zwrócić ścieżkę PO postprocessingu ffmpeg
    (.mp3), nie ścieżkę opartą na outtmpl sprzed konwersji (oryginalny
    kontener pobranego audio, np. .webm)."""
    engine = DownloadEngine()

    job = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="audio",
        output_format="mp3",
        session_id="test-session",
        job_id="test-job-mp3-extension",
    )

    result = None
    try:
        result = engine.submit(job)
        assert result.path.suffix == ".mp3"
    finally:
        if result is not None:
            storage.cleanup(result.path.parent)


@pytest.mark.slow
def test_list_available_subtitles_returns_non_empty_for_polish_video():
    result = list_available_subtitles(POLISH_TEST_VIDEO_URL)

    assert result["manual"] or result["automatic"]


@pytest.mark.slow
def test_engine_submit_subtitle_downloads_detected_language():
    """Regresja: bez subtitleslangs jawnie ustawionego na wykryty język,
    yt-dlp domyślnie szuka tylko 'en' — dla filmu bez angielskich
    napisów/auto-napisów nic się nie zapisywało."""
    available = list_available_subtitles(POLISH_TEST_VIDEO_URL)
    lang_candidates = available["manual"] + available["automatic"]
    assert lang_candidates, "brak jakichkolwiek napisów do przetestowania"

    lang = next((code for code in lang_candidates if code.startswith("pl")), lang_candidates[0])

    engine = DownloadEngine()
    job = DownloadJob(
        url=POLISH_TEST_VIDEO_URL,
        mode="subtitle",
        output_format="srt",
        session_id="test-session",
        job_id="test-job-subtitle-lang",
        subtitle_lang=lang,
    )

    result = None
    try:
        result = engine.submit(job)
        assert result.path.exists()
        assert result.path.stat().st_size > 0
    finally:
        if result is not None:
            storage.cleanup(result.path.parent)


@pytest.mark.slow
def test_engine_submit_subtitle_downloads_manual_caption_not_phantom_media_file():
    """Regresja: dla MANUALNYCH napisów (nie tylko automatycznych, patrz
    test wyżej) yt-dlp też wypełnia requested_downloads fantomowym wpisem
    wskazującym na plik medialny, który nigdy nie zostaje zapisany
    (skip_download=True) — _resolve_result musi poprawnie sięgnąć po
    requested_subtitles, weryfikując .exists() na każdym kandydacie."""
    available = list_available_subtitles(TEST_VIDEO_URL)
    assert available["manual"], "oczekiwano realnych manualnych napisów dla tego filmu"

    lang = available["manual"][0]

    engine = DownloadEngine()
    job = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="subtitle",
        output_format="srt",
        session_id="test-session",
        job_id="test-job-subtitle-manual",
        subtitle_lang=lang,
    )

    result = None
    try:
        result = engine.submit(job)
        assert result.path.exists()
        assert result.path.suffix == ".srt"
        assert result.path.stat().st_size > 0
    finally:
        if result is not None:
            storage.cleanup(result.path.parent)


class _FakeYDL:
    """Podstawia yt_dlp.YoutubeDL — extract_info() zwraca info_dict przekazany
    z zewnątrz, bez żadnego realnego zapytania do sieci. Wystarczy do
    przetestowania _finalize_transcript bez czekania na prawdziwe pobranie
    (patrz test_engine_submit_transcript_downloads_and_cleans_manual_caption_to_txt
    dla wersji integracyjnej z realnym VTT z YouTube)."""

    def __init__(self, opts: dict, info: dict) -> None:
        self._opts = opts
        self._info = info

    def __enter__(self) -> "_FakeYDL":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def extract_info(self, url: str, download: bool = True) -> dict:
        return self._info


def test_engine_submit_transcript_mode_converts_vtt_to_txt(monkeypatch, tmp_path):
    """Regresja: submit() w trybie transcript musi zwrócić .txt oczyszczony
    przez transcript_cleaner, a NIE surowy .vtt zwracany przez _resolve_result
    (dokładnie ta sama ścieżka rozwiązywania co Subtitle) — i musi usunąć
    oryginalny plik .vtt (użytkownik dostaje tylko czysty tekst)."""
    vtt_path = tmp_path / "Video.en.vtt"
    vtt_path.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello world.\n", encoding="utf-8"
    )

    fake_info = {
        "requested_subtitles": {"en": {"filepath": str(vtt_path)}},
        # Fantomowy wpis medialny (skip_download=True) — nigdy nie istnieje
        # na dysku, _resolve_result musi go zignorować (patrz .exists() guard).
        "requested_downloads": [{"filepath": str(tmp_path / "Video.en.mp4")}],
        "uploader": "Channel",
        "title": "Video",
    }

    monkeypatch.setattr(
        engine_module, "YoutubeDL", lambda opts: _FakeYDL(opts, fake_info)
    )
    monkeypatch.setattr(engine_module.storage, "create", lambda session_id, job_id: tmp_path)

    engine = DownloadEngine()
    job = DownloadJob(
        url="https://www.youtube.com/watch?v=jNQXAC9IVRw",
        mode="transcript",
        output_format="txt",
        session_id="test-session",
        job_id="test-job-transcript-mock",
        subtitle_lang="en",
    )

    result = engine.submit(job)

    assert result.path.suffix == ".txt"
    assert result.path.read_text(encoding="utf-8") == "Hello world."
    assert not vtt_path.exists()
    assert result.uploader == "Channel"
    assert result.title == "Video"


def test_engine_submit_passes_cookiefile_to_every_ydl_instance(monkeypatch, tmp_path):
    """Regresja: cookies.txt wgrany przez użytkownika (app.py przekazuje
    surowe bajty jako job.cookie_data, NIE ścieżkę) musi trafić do yt_dlp
    jako 'cookiefile' wskazujący na ISTNIEJĄCY plik na dysku z tą samą
    zawartością — w KAŻDYM wywołaniu YoutubeDL, w tym w sondzie
    _check_playlist_limit (bez cookiefile tam materiał z ograniczeniem
    wiekowym nigdy nie dociera do głównego pobrania, które cookies miało).
    Sprawdzamy realne opcje przekazane do YoutubeDL, nie tylko brak wyjątku."""
    media_path = tmp_path / "Video.mp4"
    media_path.write_bytes(b"fake mp4 bytes")

    fake_info = {
        "requested_downloads": [{"filepath": str(media_path)}],
        "uploader": "Channel",
        "title": "Video",
    }

    captured_opts: list[dict] = []

    def _fake_ydl_factory(opts: dict) -> _FakeYDL:
        captured_opts.append(opts)
        return _FakeYDL(opts, fake_info)

    monkeypatch.setattr(engine_module, "YoutubeDL", _fake_ydl_factory)
    monkeypatch.setattr(engine_module.storage, "create", lambda session_id, job_id: tmp_path)

    cookie_bytes = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tfoo\tbar\n"

    engine = DownloadEngine()
    job = DownloadJob(
        url="https://www.youtube.com/watch?v=jNQXAC9IVRw",
        mode="video",
        output_format="mp4",
        session_id="test-session",
        job_id="test-job-cookies",
        cookie_data=cookie_bytes,
        # playlist_scope="all" wymusza wywołanie _check_playlist_limit (patrz
        # test_engine_submit_skips_playlist_limit_probe_when_scope_is_single)
        # — bez tego byłoby tylko jedno wywołanie YoutubeDL, nie dwa.
        playlist_scope="all",
    )

    result = engine.submit(job)

    assert result.path == media_path
    # Dwa wywołania YoutubeDL: sonda _check_playlist_limit + główne pobranie —
    # OBA muszą dostać cookiefile, nie tylko drugie.
    assert len(captured_opts) == 2
    for opts in captured_opts:
        assert "cookiefile" in opts
        cookiefile_path = Path(opts["cookiefile"])
        assert cookiefile_path.exists()
        assert cookiefile_path.read_bytes() == cookie_bytes
        # Plik cookie żyje w job_dir (tmp_path), nie w systemowym katalogu temp.
        assert cookiefile_path.parent == tmp_path


def test_engine_submit_sets_noplaylist_true_for_single_scope(monkeypatch, tmp_path):
    """Regresja centralna tego briefu: URL zawierający jednocześnie v= i
    list= musi ściągnąć WYŁĄCZNIE wideo wskazane przez v=, nie całą
    playlistę (yt-dlp domyślnie: noplaylist=False) — inaczej
    _resolve_result (zakłada jeden plik wynikowy) dostaje wiele ściągniętych
    plików i cicho zwraca None → "Nie udało się ustalić ścieżki..."."""
    media_path = tmp_path / "Video.mp4"
    media_path.write_bytes(b"fake mp4 bytes")
    fake_info = {"requested_downloads": [{"filepath": str(media_path)}]}

    captured_opts: list[dict] = []

    def _fake_ydl_factory(opts: dict) -> _FakeYDL:
        captured_opts.append(opts)
        return _FakeYDL(opts, fake_info)

    monkeypatch.setattr(engine_module, "YoutubeDL", _fake_ydl_factory)
    monkeypatch.setattr(engine_module.storage, "create", lambda session_id, job_id: tmp_path)

    engine = DownloadEngine()
    job = DownloadJob(
        url="https://www.youtube.com/watch?v=uXlzoi70qUY&list=PL3jltwT7zlHiI4lHQh8fdlHGhw4Lfp5Aq",
        mode="video",
        output_format="mp4",
        session_id="test-session",
        job_id="test-job-noplaylist",
        playlist_scope="single",
    )

    engine.submit(job)

    # Główne pobranie jest zawsze OSTATNIM wywołaniem YoutubeDL (niezależnie
    # od tego, czy poprzedziła je sonda _check_playlist_limit — to osobna
    # sprawa, pokryta testem niżej) — sprawdzamy właśnie to wywołanie.
    assert captured_opts[-1]["noplaylist"] is True


def test_engine_submit_skips_playlist_limit_probe_when_scope_is_single(monkeypatch, tmp_path):
    """Sonda _check_playlist_limit jest zbędnym zapytaniem do YouTube, gdy
    playlist_scope=="single" — noplaylist=True i tak ściągnie jedno wideo
    niezależnie od liczby pozycji w URL-u."""
    media_path = tmp_path / "Video.mp4"
    media_path.write_bytes(b"fake mp4 bytes")
    fake_info = {"requested_downloads": [{"filepath": str(media_path)}]}

    call_count = 0

    def _fake_ydl_factory(opts: dict) -> _FakeYDL:
        nonlocal call_count
        call_count += 1
        return _FakeYDL(opts, fake_info)

    monkeypatch.setattr(engine_module, "YoutubeDL", _fake_ydl_factory)
    monkeypatch.setattr(engine_module.storage, "create", lambda session_id, job_id: tmp_path)

    engine = DownloadEngine()
    job = DownloadJob(
        url="https://www.youtube.com/watch?v=uXlzoi70qUY&list=PL3jltwT7zlHiI4lHQh8fdlHGhw4Lfp5Aq",
        mode="video",
        output_format="mp4",
        session_id="test-session",
        job_id="test-job-skip-probe",
        playlist_scope="single",
    )

    engine.submit(job)

    assert call_count == 1  # tylko główne pobranie, żadnej sondy


def test_engine_submit_skips_playlist_limit_probe_for_subtitle_mode_even_with_all_scope(
    monkeypatch, tmp_path
):
    """Limit liczby pozycji nie dotyczy Subtitle/Transcript (app.py) — sonda
    _check_playlist_limit musi być pominięta nawet gdy playlist_scope=="all",
    jeśli mode nie jest video/audio."""
    vtt_path = tmp_path / "Video.en.vtt"
    vtt_path.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHi.\n", encoding="utf-8")
    fake_info = {"requested_subtitles": {"en": {"filepath": str(vtt_path)}}}

    call_count = 0

    def _fake_ydl_factory(opts: dict) -> _FakeYDL:
        nonlocal call_count
        call_count += 1
        return _FakeYDL(opts, fake_info)

    monkeypatch.setattr(engine_module, "YoutubeDL", _fake_ydl_factory)
    monkeypatch.setattr(engine_module.storage, "create", lambda session_id, job_id: tmp_path)

    engine = DownloadEngine()
    job = DownloadJob(
        url="https://www.youtube.com/watch?v=uXlzoi70qUY&list=PL3jltwT7zlHiI4lHQh8fdlHGhw4Lfp5Aq",
        mode="subtitle",
        output_format="srt",
        session_id="test-session",
        job_id="test-job-subtitle-all-scope",
        subtitle_lang="en",
        playlist_scope="all",
    )

    engine.submit(job)

    assert call_count == 1  # tylko główne pobranie, żadnej sondy limitu


def test_check_playlist_limit_uses_in_playlist_flat_mode_not_bool(monkeypatch):
    """Regresja: extract_flat=True (bool) dla URL-i "mixed" cicho WYŁĄCZAŁ
    ochronę limitu (info bez 'entries' => _check_playlist_limit po prostu
    wracał, nigdy nie podnosząc PlaylistTooLargeError, bez żadnego błędu).
    Weryfikujemy realną opcję przekazaną do YoutubeDL."""
    fake_info = {"entries": [{"id": "a"}, {"id": "b"}]}
    captured_opts: list[dict] = []

    def _fake_ydl_factory(opts: dict) -> _FakeYDL:
        captured_opts.append(opts)
        return _FakeYDL(opts, fake_info)

    monkeypatch.setattr(engine_module, "YoutubeDL", _fake_ydl_factory)

    DownloadEngine()._check_playlist_limit(
        "https://www.youtube.com/watch?v=uXlzoi70qUY&list=PL3jltwT7zlHiI4lHQh8fdlHGhw4Lfp5Aq"
    )

    assert captured_opts[0]["extract_flat"] == "in_playlist"


def test_check_playlist_limit_raises_for_mixed_url_shaped_response_over_limit(monkeypatch):
    """End-to-end regresja dla drugiej konsekwencji tego samego buga: dla
    URL-a "mixed" z liczbą pozycji przekraczającą MAX_PLAYLIST_ITEMS,
    _check_playlist_limit MUSI faktycznie podnieść PlaylistTooLargeError —
    z extract_flat=True (bool) tego nigdy nie robił (cicho wracał, myśląc,
    że to nie playlista)."""
    fake_info = {
        "_type": "playlist",
        "entries": [{"id": f"video{i}"} for i in range(settings.max_playlist_items + 5)],
    }

    def _fake_ydl_factory(opts: dict) -> _FakeYDL:
        return _FakeYDL(opts, fake_info)

    monkeypatch.setattr(engine_module, "YoutubeDL", _fake_ydl_factory)

    with pytest.raises(PlaylistTooLargeError):
        DownloadEngine()._check_playlist_limit(
            "https://www.youtube.com/watch?v=uXlzoi70qUY&list=PL3jltwT7zlHiI4lHQh8fdlHGhw4Lfp5Aq"
        )


def test_count_playlist_items_returns_entry_count_for_real_playlist(monkeypatch):
    fake_info = {"entries": [{"id": "a"}, {"id": "b"}, None, {"id": "c"}]}
    captured_opts: list[dict] = []

    def _fake_ydl_factory(opts: dict) -> _FakeYDL:
        captured_opts.append(opts)
        return _FakeYDL(opts, fake_info)

    monkeypatch.setattr(engine_module, "YoutubeDL", _fake_ydl_factory)

    # None (fałszywy wpis) nie liczy się jako pozycja — 3 realne z 4 entries.
    assert engine_module.count_playlist_items("https://www.youtube.com/playlist?list=PLxxx") == 3
    # Regresja: NIE extract_flat=True (bool) — dla URL-i "mixed" zwraca stub
    # bez 'entries' (potwierdzone w REPL-u), patrz komentarz przy
    # _PLAYLIST_FLAT_MODE. Weryfikujemy realną opcję przekazaną do YoutubeDL,
    # nie tylko że test przechodzi przez inną, przypadkową ścieżkę.
    assert captured_opts[0]["extract_flat"] == engine_module._PLAYLIST_FLAT_MODE
    assert captured_opts[0]["extract_flat"] == "in_playlist"


def test_count_playlist_items_returns_none_when_not_a_playlist(monkeypatch):
    fake_info = {"id": "single-video"}  # brak "entries" — to nie playlista

    def _fake_ydl_factory(opts: dict) -> _FakeYDL:
        return _FakeYDL(opts, fake_info)

    monkeypatch.setattr(engine_module, "YoutubeDL", _fake_ydl_factory)

    assert engine_module.count_playlist_items("https://www.youtube.com/watch?v=xxx") is None


def test_count_playlist_items_resolves_entries_for_mixed_url_shaped_response(monkeypatch):
    """Regresja dokładnie dla zgłoszonego buga: symuluje kształt odpowiedzi,
    jaki extract_flat="in_playlist" faktycznie zwraca dla URL-i "mixed"
    (potwierdzone w REPL-u: _type=playlist, entries obecne) — w
    przeciwieństwie do stub-obiektu (_type=url, brak entries), jaki
    zwracałoby extract_flat=True (bool) dla tego samego URL-a."""
    fake_info = {
        "_type": "playlist",
        "id": "PL3jltwT7zlHiI4lHQh8fdlHGhw4Lfp5Aq",
        "entries": [{"id": f"video{i}"} for i in range(12)],
    }

    def _fake_ydl_factory(opts: dict) -> _FakeYDL:
        return _FakeYDL(opts, fake_info)

    monkeypatch.setattr(engine_module, "YoutubeDL", _fake_ydl_factory)

    mixed_url = "https://www.youtube.com/watch?v=uXlzoi70qUY&list=PL3jltwT7zlHiI4lHQh8fdlHGhw4Lfp5Aq"
    assert engine_module.count_playlist_items(mixed_url) == 12


def test_list_available_subtitles_passes_cookiefile_pointing_to_real_file_with_content(monkeypatch):
    """Regresja: list_available_subtitles() ma tę samą sondę YoutubeDL co
    _check_playlist_limit miał przed naprawą — bez cookiefile materiał z
    ograniczeniem wiekowym nigdy nie zwróci listy języków, niezależnie od
    tego, czy użytkownik wgrał cookies.txt (UI pokazywałby "nie znaleziono
    napisów" mimo poprawnych cookies do właściwego pobrania). Sprawdzamy
    realne opcje przekazane do YoutubeDL, nie tylko brak wyjątku — i że
    tymczasowy plik cookie jest usuwany zaraz po sondzie (nie żyje w job_dir,
    bo ta funkcja jest wołana PRZED istnieniem joba)."""
    fake_info = {"subtitles": {"en": {}}, "automatic_captions": {}}

    captured_opts: list[dict] = []
    captured_cookiefile_content: list[bytes] = []
    captured_cookiefile_path: list[Path] = []

    def _fake_ydl_factory(opts: dict) -> _FakeYDL:
        captured_opts.append(opts)
        if "cookiefile" in opts:
            path = Path(opts["cookiefile"])
            # Musi istnieć TERAZ, w trakcie sondy — funkcja usuwa go
            # dopiero po zamknięciu YoutubeDL, więc czytamy zawartość tutaj.
            assert path.exists()
            captured_cookiefile_content.append(path.read_bytes())
            captured_cookiefile_path.append(path)
        return _FakeYDL(opts, fake_info)

    monkeypatch.setattr(engine_module, "YoutubeDL", _fake_ydl_factory)

    cookie_bytes = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tfoo\tbar\n"
    result = engine_module.list_available_subtitles(
        "https://www.youtube.com/watch?v=jNQXAC9IVRw", cookie_data=cookie_bytes
    )

    assert result == {"manual": ["en"], "automatic": []}
    assert len(captured_opts) == 1
    assert "cookiefile" in captured_opts[0]
    assert captured_cookiefile_content == [cookie_bytes]
    # Sprzątnięty natychmiast po sondzie — ta funkcja nie ma job_dir do
    # późniejszego storage.cleanup(), więc musi posprzątać sama.
    assert not captured_cookiefile_path[0].exists()


@pytest.mark.slow
def test_engine_submit_transcript_downloads_and_cleans_manual_caption_to_txt():
    """Regresja end-to-end: tryb Transkrypt reużywa dokładnie tę samą ścieżkę
    resolvowania co Subtitle (patrz test wyżej dla manualnych napisów), ale
    dodatkowo przepuszcza wynik przez transcript_cleaner (dedup + podział na
    akapity) — wynikowy plik musi być .txt, bez timestampów/tagów VTT, bez
    oczywistych zduplikowanych zdań pod rząd (rolling captions), i z
    faktycznym podziałem na akapity (materiał jest długi — patrz komentarz
    przy LONG_TEST_VIDEO_URL o tym, czemu TEST_VIDEO_URL do tego nie wystarcza)."""
    engine = DownloadEngine()
    job = DownloadJob(
        url=LONG_TEST_VIDEO_URL,
        mode="transcript",
        output_format="txt",
        session_id="test-session",
        job_id="test-job-transcript",
        subtitle_lang="en",
    )

    result = None
    try:
        result = engine.submit(job)
        assert result.path.exists()
        assert result.path.suffix == ".txt"

        text = result.path.read_text(encoding="utf-8")
        assert text.strip()
        assert "-->" not in text
        assert "WEBVTT" not in text
        assert "<c>" not in text and "<i>" not in text

        sentences = [s.strip() for s in text.split(".") if s.strip()]
        for previous, current in zip(sentences, sentences[1:]):
            assert previous != current, "wykryto zduplikowane zdanie pod rząd"

        assert text.count("\n\n") >= 2, "oczekiwano co najmniej 3 akapitów dla tak długiego materiału"
    finally:
        if result is not None:
            storage.cleanup(result.path.parent)
