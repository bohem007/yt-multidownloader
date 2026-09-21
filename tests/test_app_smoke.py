"""Smoke testy app.py przez streamlit.testing.v1.AppTest.

Bez realnego pobierania i bez realnych zapytań do Neon — warstwę bazy
(m.in. get_recent_history, którego zakładka "Historia" woła na KAŻDYM
rerunie skryptu) podstawia autouse `database_calls` z tests/conftest.py,
żeby testy były szybkie, deterministyczne i offline.
"""

from __future__ import annotations

import dataclasses
import queue as queue_module
import threading
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import src.config as config_module
import src.downloads as downloads
import src.engine as engine_module
from src.config import Settings
from src.db import Database
from src.engine import PlaylistDownloadResult, PlaylistItemResult, PlaylistSnapshot
from src.errors import EmptyPlaylistSnapshotError
from src.progress import ProgressEvent

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(autouse=True)
def _isolated_links_dir(monkeypatch, tmp_path_factory):
    """Katalog linków do pobrania (src/downloads.py) MUSI leżeć poza tmp_path
    testu — app.py woła storage.cleanup(result_file.parent), a ZIP-y w
    testach leżą wprost w tmp_path, więc wspólny katalog zostałby skasowany."""
    links_base = tmp_path_factory.mktemp("links-base")
    monkeypatch.setattr(
        downloads,
        "settings",
        dataclasses.replace(downloads.settings, storage_base_dir=str(links_base)),
    )
    downloads.set_route_enabled(True)
    yield
    downloads.set_route_enabled(False)
    downloads.purge_all()


def _publish_seed_zip(directory: Path, content: bytes, name: str = "Playlista-Moja playlista.zip"):
    source = directory / f"seed-{len(content)}.zip"
    source.write_bytes(content)
    return downloads.publish(source, name)


def _link_urls(at: AppTest) -> list[str]:
    return [element.proto.url for element in at.get("link_button")]


def _run_app(monkeypatch) -> AppTest:
    at = AppTest.from_file(APP_PATH)
    at.run()
    return at


def test_app_loads_without_exceptions(monkeypatch):
    at = _run_app(monkeypatch)
    assert not at.exception


def test_selecting_audio_mode_reveals_format_selectbox(monkeypatch):
    at = _run_app(monkeypatch)

    at.selectbox(key="mode_select").select("Audio (MP3 / FLAC)").run()

    assert not at.exception
    assert at.selectbox(key="audio_format_select") is not None


def test_subtitle_language_selectbox_filters_automatic_captions_to_allowed_set(monkeypatch):
    """automatic_captions z YouTube potrafi zawierać >150 kodów (pełna
    lista celów auto-tłumaczenia) — selectbox ma pokazywać WYŁĄCZNIE
    manualne napisy plus automatyczne ograniczone do {pl, de, en}."""
    fake_subtitles = {
        "manual": ["fr"],
        "automatic": ["pl", "de", "en", "es", "it", "ja"],
    }
    monkeypatch.setattr(engine_module, "list_available_subtitles", lambda url, cookie_data=None: fake_subtitles)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input("https://www.youtube.com/watch?v=jNQXAC9IVRw").run()
    at.selectbox(key="mode_select").select("Napisy (SRT / VTT)").run()

    assert not at.exception
    lang_select = at.selectbox(key="subtitle_lang_select")
    assert set(lang_select.proto.options) == {"fr", "pl", "de", "en"}


def test_transcript_mode_shows_language_selector_without_format_choice(monkeypatch):
    """Tryb Transkrypt reużywa selektor języka Subtitle (ten sam cache/
    filtr pl/de/en), ale NIE pyta o format napisów (SRT/VTT) — zawsze
    czyści VTT do .txt wewnętrznie (patrz profiles.py::_transcript_profile)."""
    fake_subtitles = {"manual": ["en"], "automatic": []}
    monkeypatch.setattr(engine_module, "list_available_subtitles", lambda url, cookie_data=None: fake_subtitles)

    at = _run_app(monkeypatch)
    # URL inny niż w pozostałych testach — _cached_list_available_subtitles
    # (st.cache_data) jest cache'owany po URL na poziomie procesu testowego,
    # nie per-AppTest-instancja, więc wspólny URL złapałby zapamiętany
    # wynik z innego testu niezależnie od monkeypatcha na tej funkcji.
    at.text_input(key="url_input").input("https://www.youtube.com/watch?v=transcripttest1").run()
    at.selectbox(key="mode_select").select("Transkrypt (TXT)").run()

    assert not at.exception
    selectbox_keys = [sb.key for sb in at.selectbox]
    assert "subtitle_lang_select" in selectbox_keys
    assert "subtitle_format_select" not in selectbox_keys
    assert list(at.selectbox(key="subtitle_lang_select").proto.options) == ["en"]
    assert at.button(key="download_button").proto.disabled is False


def test_completed_transcript_job_shows_txt_download_button(monkeypatch, tmp_path):
    """Wynik trybu Transkrypt (.txt) przechodzi przez ten sam potok
    zakończenia joba co Video/Audio/Subtitle — bez żadnych zmian w
    _render_progress/_render_result specyficznych dla tego trybu."""
    at = _run_app(monkeypatch)

    result_file = tmp_path / "Uploader-Title.en.txt"
    result_file.write_bytes("Czysty tekst transkryptu.".encode("utf-8"))

    finished_queue: queue_module.Queue = queue_module.Queue()
    finished_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Zakończono",
            result_path=result_file,
            result_uploader="Test Uploader",
            result_title="Test Title",
        )
    )
    _simulate_job_in_flight(at, finished_queue)

    at.run()

    assert not at.exception
    assert at.session_state["status"] == "done"
    assert at.session_state["result_file_name"] == "Uploader-Title.en.txt"
    assert at.session_state["result_data"] == "Czysty tekst transkryptu.".encode("utf-8")
    assert len(at.download_button) >= 1


def test_mixed_url_shows_playlist_scope_radio_with_real_item_count(monkeypatch):
    """URL z v= i list= (typowy link "autoplay z listy") musi pokazać radio
    z TRZEMA opcjami (2026-09-20: dodano "Wybrane numery...") i realną
    (sondowaną) liczbą pozycji, nie zaślepką."""
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 7)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=mixedtest1&list=PLmixedtest1"
    ).run()

    assert not at.exception
    radio = at.radio(key="playlist_scope_radio")
    assert radio.options == [
        "Tylko to wideo",
        "Cała playlista (7 pozycji)",
        "Wybrane numery wideo z playlisty",
    ]
    assert radio.value == "Tylko to wideo"  # domyślnie pojedyncze wideo


def test_playlist_only_url_shows_radio_with_no_single_video_option(monkeypatch):
    """URL bez v= (np. /playlist?list=...) nie ma wariantu "tylko wideo" —
    nic takiego nie istnieje do wybrania — ale MA opcję wyboru numerów."""
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 4)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/playlist?list=PLplaylistonly1"
    ).run()

    assert not at.exception
    radio = at.radio(key="playlist_scope_radio")
    assert radio.options == ["Cała playlista (4 pozycji)", "Wybrane numery wideo z playlisty"]
    assert radio.value == "Cała playlista (4 pozycji)"  # domyślnie cała playlista
    assert at.session_state["playlist_scope"] == "all"



def test_playlist_scope_all_trimming_uses_configured_limit_not_hardcoded_default(monkeypatch):
    """Regresja: literalna "10" zaszyta w etykiecie/komunikacie zamiast
    settings.max_playlist_items przechodziłaby niezauważona, gdyby testy
    zawsze używały domyślnego limitu. Tu limit jest jawnie skonfigurowany na
    NIEDOMYŚLNĄ wartość (5): etykieta i informacja muszą podawać "5" (z 7),
    nie "10", a przycisk pozostaje aktywny (przycinanie, nie blokada)."""
    monkeypatch.setattr(config_module, "settings", Settings.from_env({"MAX_PLAYLIST_ITEMS": "5"}))
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 7)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=configtest1&list=PLconfigtest1"
    ).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (pierwsze 5 z 7)").run()

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is False
    info_messages = [i.value for i in at.info]
    assert any("7" in message and "5" in message for message in info_messages)
    assert not any("10" in message for message in info_messages)
    assert not at.warning and not at.error


def test_playlist_scope_all_allows_download_under_configured_higher_limit(monkeypatch):
    """Odwrotność powyższego — limit skonfigurowany WYŻEJ niż domyślne 10
    (tu 15) musi odblokować przycisk dla liczby pozycji POD tym limitem
    (12), mimo że 12 > domyślne 10. To jest dokładny scenariusz z
    manualnego testu, który zgłosił błędną blokadę."""
    monkeypatch.setattr(config_module, "settings", Settings.from_env({"MAX_PLAYLIST_ITEMS": "15"}))
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 12)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=configtest2&list=PLconfigtest2"
    ).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (12 pozycji)").run()

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is False
    assert not any(w.value for w in at.warning)



def test_clicking_download_with_playlist_scope_all_starts_real_job(monkeypatch, tmp_path):
    """Faza 2b: placeholder usunięty — kliknięcie "Pobierz" z wybraną
    "Cała playlista" (pod limitem) musi faktycznie wystartować joba
    (submit_playlist przez JobRunner), tak jak dla playlist_scope="single",
    nie pokazywać już żadnego placeholdera. Fałszywy silnik jest natychmiastowy
    (bez realnego I/O), więc w tym samym rerunie AppTest zdąży też odebrać
    terminalny event z fragmentu _render_progress — dlatego asercja sprawdza
    finalny sukces (download_button), nie ulotny stan "running", który jest
    z natury zależny od wyścigu wątku w tle."""
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 3)

    zip_file = tmp_path / "playlist.zip"
    zip_file.write_bytes(b"fake zip bytes")
    submit_playlist_called = threading.Event()

    def _fake_submit_playlist(self, job, on_event=None, start_index=1, selected_indices=None):
        submit_playlist_called.set()
        return PlaylistDownloadResult(zip_path=zip_file, items=[], playlist_title="Fake")

    monkeypatch.setattr(engine_module.DownloadEngine, "submit_playlist", _fake_submit_playlist)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=mixedtest4&list=PLmixedtest4"
    ).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (3 pozycji)").run()

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is False

    at.button(key="download_button").click().run()

    assert not at.exception
    info_messages = [info.value for info in at.info]
    assert not any("w przygotowaniu" in message for message in info_messages)
    assert submit_playlist_called.wait(timeout=5.0)
    assert at.session_state["status"] in ("running", "done")
    if at.session_state["status"] != "done":
        at.run()
    assert at.session_state["status"] == "done"
    token = at.session_state["result_download_token"]
    assert token is not None and downloads.lookup(token) is not None
    assert _link_urls(at) == [downloads.download_url(token)]
    assert len(at.download_button) == 0


def test_selecting_selected_scope_reveals_number_input_and_blocks_download_when_empty(monkeypatch):
    """Punkt 4 (brief 2026-09-20): wybranie "Wybrane numery wideo z
    playlisty" pokazuje pole tekstowe na numery — puste pole blokuje
    "Pobierz" (bez komunikatu błędu, tak jak puste URL — pole po prostu
    nie zostało jeszcze wypełnione, to nie jest błąd do zgłaszania)."""
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 32)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=selectedtest1&list=PLselectedtest1"
    ).run()
    at.radio(key="playlist_scope_radio").set_value("Wybrane numery wideo z playlisty").run()

    assert not at.exception
    assert at.text_input(key="selected_indices_input") is not None
    assert at.session_state["playlist_scope"] == "selected"
    assert at.button(key="download_button").proto.disabled is True
    assert len(at.error) == 0


def test_selected_scope_invalid_syntax_shows_error_and_blocks_download(monkeypatch):
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 32)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=selectedtest2&list=PLselectedtest2"
    ).run()
    at.radio(key="playlist_scope_radio").set_value("Wybrane numery wideo z playlisty").run()
    at.text_input(key="selected_indices_input").input("15, abc").run()

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is True
    assert any("abc" in e.value for e in at.error)


def test_selected_scope_rejects_duplicates_and_out_of_range(monkeypatch):
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 10)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=selectedtest3&list=PLselectedtest3"
    ).run()
    at.radio(key="playlist_scope_radio").set_value("Wybrane numery wideo z playlisty").run()

    at.text_input(key="selected_indices_input").input("2, 2").run()
    assert at.button(key="download_button").proto.disabled is True
    assert any("powtórzone" in e.value for e in at.error)

    at.text_input(key="selected_indices_input").input("2, 21").run()
    assert at.button(key="download_button").proto.disabled is True
    assert any("poza zakresem" in e.value and "21" in e.value for e in at.error)



def test_clicking_download_with_selected_indices_passes_them_to_job(monkeypatch, tmp_path):
    """End-to-end (fałszywy silnik): "Pobierz" z ważnym wyborem numerów
    musi wystartować joba z playlist_scope="selected" i przekazać
    selected_indices do submit_playlist(), a wynikowa nazwa ZIP-a musi
    użyć gałęzi "-pozycje-{lista}" (nie zakresu od-do)."""
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 32)

    zip_file = tmp_path / "playlist.zip"
    zip_file.write_bytes(b"fake zip bytes")
    received: dict = {}

    def _fake_submit_playlist(self, job, on_event=None, start_index=1, selected_indices=None):
        received["playlist_scope"] = job.playlist_scope
        received["selected_indices"] = selected_indices
        return PlaylistDownloadResult(
            zip_path=zip_file,
            items=[
                PlaylistItemResult(index=15, title="Wideo 15", status="done"),
                PlaylistItemResult(index=21, title="Wideo 21", status="done"),
            ],
            playlist_title="Moja playlista",
        )

    monkeypatch.setattr(engine_module.DownloadEngine, "submit_playlist", _fake_submit_playlist)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=selectedtest5&list=PLselectedtest5"
    ).run()
    at.radio(key="playlist_scope_radio").set_value("Wybrane numery wideo z playlisty").run()
    at.text_input(key="selected_indices_input").input("21, 15").run()

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is False

    at.button(key="download_button").click().run()
    for _ in range(10):
        if at.session_state["status"] == "done":
            break
        at.run()

    assert received["playlist_scope"] == "selected"
    assert received["selected_indices"] == [15, 21]  # posortowane, niezależnie od kolejności wejścia
    assert at.session_state["status"] == "done"
    assert at.session_state["result_file_name"] == "Playlista-Moja playlista-pozycje-15,21.mp4.zip"


def test_completed_playlist_job_shows_zip_download_link_with_report(monkeypatch, tmp_path):
    """Kryterium akceptacji 1+2: submit_playlist() zwrócił ZIP + raport
    per pozycja (jedna pozycja error) — status="done" (bo ≥1 sukces),
    nazwa pliku "Playlista-{tytuł}-pozycje-{start}-{end}.zip" (nie przez
    build_display_filename), raport (podsumowanie + lista błędów) widoczny
    w _render_result. Sufiks zakresu jest teraz na KAŻDEJ turze, łącznie
    z pierwszą (fix regresji z 2026-09-20 — poprzednio pierwsza tura nie
    miała sufiksu wcale)."""
    at = _run_app(monkeypatch)

    zip_file = tmp_path / "playlist.zip"
    zip_file.write_bytes(b"fake zip bytes")

    items = [
        PlaylistItemResult(index=1, title="Wideo 1", status="done"),
        PlaylistItemResult(index=2, title="Wideo 2", status="error", error_message="Video unavailable"),
    ]

    finished_queue: queue_module.Queue = queue_module.Queue()
    finished_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Zakończono",
            result_path=zip_file,
            playlist_items=items,
            playlist_title="Moja playlista",
            output_format="mp4",
        )
    )
    _simulate_job_in_flight(at, finished_queue)

    at.run()

    assert not at.exception
    assert at.session_state["status"] == "done"
    assert at.session_state["result_file_name"] == "Playlista-Moja playlista-pozycje-01-02.mp4.zip"
    # ZIP zostaje na dysku pod nieodgadywalnym tokenem — NIE w RAM/session_state.
    assert at.session_state["result_data"] is None
    token = at.session_state["result_download_token"]
    link = downloads.lookup(token)
    assert link is not None
    assert link.path.read_bytes() == b"fake zip bytes"
    assert link.file_name == "Playlista-Moja playlista-pozycje-01-02.mp4.zip"
    assert _link_urls(at) == [downloads.download_url(token)]
    assert len(at.download_button) == 0

    # Ostatnia tura (brak kontynuacji): "zakończone" + uczciwy czas ważności.
    assert [s.value for s in at.success] == ["Pobieranie playlisty zakończone."]
    caption_texts = [c.value for c in at.caption]
    assert "Link do pobrania jest ważny przez 30 minut." in caption_texts
    assert not any("zostanie zastąpiony" in text for text in caption_texts)
    assert any("1 z 2 pozycji pobranych" in text for text in caption_texts)
    write_texts = [w.value for w in at.markdown]
    assert any("Wideo 2" in text and "Video unavailable" in text for text in write_texts)

    # Faza 2c: brak next_start_index (domyślne None, nic nie zatrzymało
    # pętli przed końcem) → nie ma przycisku "Pobierz kolejne pozycje".
    assert "continue_playlist_button" not in [b.key for b in at.button]


def test_completed_playlist_job_with_next_start_index_shows_continue_button_and_context_message(
    monkeypatch, tmp_path
):
    """Kryterium akceptacji 1 (Faza 2c): next_start_index ustawiony →
    widoczny przycisk "Pobierz kolejne pozycje" oraz komunikat kontekstowy
    o zamierzonym zatrzymaniu (nie błąd) z numerem pozycji do wznowienia."""
    at = _run_app(monkeypatch)

    zip_file = tmp_path / "playlist.zip"
    zip_file.write_bytes(b"fake zip bytes")

    items = [
        PlaylistItemResult(index=1, title="Wideo 1", status="done"),
        PlaylistItemResult(index=2, title="Wideo 2", status="done"),
    ]
    finished_queue: queue_module.Queue = queue_module.Queue()
    finished_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Zakończono",
            result_path=zip_file,
            playlist_items=items,
            playlist_title="Moja playlista",
            next_start_index=3,
            output_format="mp4",
        )
    )
    _simulate_job_in_flight(at, finished_queue)

    at.run()

    assert not at.exception
    assert at.session_state["status"] == "done"
    # Pierwsze wywołanie (start_index=1 domyślnie) dostaje sufiks zakresu
    # tak samo jak kontynuacje (fix regresji z 2026-09-20).
    assert at.session_state["result_file_name"] == "Playlista-Moja playlista-pozycje-01-02.mp4.zip"
    assert at.session_state["playlist_next_start_index"] == 3

    info_messages = [i.value for i in at.info]
    assert any("pozycji 2" in m and "pozycji 3" in m for m in info_messages)
    assert "continue_playlist_button" in [b.key for b in at.button]

    # Tura z kontynuacją: NIE "playlisty zakończone", NIE stały czas ważności.
    assert [s.value for s in at.success] == ["Tura zakończona: pobrano pozycje 1-2."]
    caption_texts = [c.value for c in at.caption]
    assert "Ten plik zostanie zastąpiony, gdy pobierzesz kolejne pozycje." in caption_texts
    assert not any("minut" in text for text in caption_texts)


def test_clicking_continue_button_starts_continuation_and_replaces_result_on_completion(
    monkeypatch, tmp_path
):
    """Kryteria akceptacji 2+3 (Faza 2c): kliknięcie "Pobierz kolejne
    pozycje" startuje nowy job z start_index=next_start_index (bezwzględny);
    poprzedni ZIP/raport są widoczne PRZED kliknięciem; po zakończeniu
    kontynuacji nowy ZIP (z zakresem pozycji w nazwie) zastępuje poprzedni,
    a next_start_index=None (koniec playlisty) chowa przycisk."""
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 5)

    received: dict = {}
    continuation_zip = tmp_path / "continuation.zip"
    continuation_zip.write_bytes(b"continuation zip bytes")

    def _fake_submit_playlist(self, job, on_event=None, start_index=1, selected_indices=None):
        received["start_index"] = start_index
        return PlaylistDownloadResult(
            zip_path=continuation_zip,
            items=[
                PlaylistItemResult(index=3, title="Wideo 3", status="done"),
                PlaylistItemResult(index=4, title="Wideo 4", status="done"),
                PlaylistItemResult(index=5, title="Wideo 5", status="done"),
            ],
            playlist_title="Moja playlista",
            next_start_index=None,
        )

    monkeypatch.setattr(engine_module.DownloadEngine, "submit_playlist", _fake_submit_playlist)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=mixedtest6&list=PLmixedtest6"
    ).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (5 pozycji)").run()

    # Symulacja zakończonego PIERWSZEGO wywołania, zatrzymanego po pozycji 2
    # (wstrzyknięte bezpośrednio — to samo, co job_runner.py zrobiłby przez
    # ProgressEvent, tylko bez realnego wątku w tle).
    first_link = _publish_seed_zip(tmp_path, b"first zip bytes")
    at.session_state["status"] = "done"
    at.session_state["result_download_token"] = first_link.token
    at.session_state["result_file_name"] = "Playlista-Moja playlista.zip"
    at.session_state["playlist_report"] = [
        PlaylistItemResult(index=1, title="Wideo 1", status="done"),
        PlaylistItemResult(index=2, title="Wideo 2", status="done"),
    ]
    at.session_state["playlist_title"] = "Moja playlista"
    at.session_state["playlist_next_start_index"] = 3
    at.session_state["job_id"] = "first-job-id"

    at.run()

    assert not at.exception
    first_urls = _link_urls(at)
    assert first_urls == [downloads.download_url(first_link.token)]  # poprzedni ZIP wciąż dostępny
    assert "continue_playlist_button" in [b.key for b in at.button]

    at.button(key="continue_playlist_button").click().run()
    assert not at.exception

    for _ in range(10):
        if at.session_state["status"] == "done" and received.get("start_index") is not None:
            break
        at.run()

    assert received["start_index"] == 3
    assert at.session_state["status"] == "done"
    assert at.session_state["result_file_name"] == "Playlista-Moja playlista-pozycje-03-05.mp4.zip"
    assert at.session_state["playlist_next_start_index"] is None
    assert "continue_playlist_button" not in [b.key for b in at.button]

    new_token = at.session_state["result_download_token"]
    assert new_token != first_link.token
    assert downloads.lookup(new_token).path.read_bytes() == b"continuation zip bytes"
    # Poprzedni ZIP zwolniony z dysku, gdy nowy wynik go zastąpił.
    assert downloads.lookup(first_link.token) is None
    assert not first_link.path.exists()
    assert _link_urls(at) == [downloads.download_url(new_token)]


def test_download_link_stays_unique_across_full_continuation_chain_including_last_turn(
    monkeypatch, tmp_path
):
    """Regresja: fix poprzedniej sesji (key oparty o job_id + running/final)
    naprawił przejścia MIĘDZY kontynuacjami, ale manualny retest na pełnym
    łańcuchu (4 tury: 1-3, 4-6, 7-9, 10-12) pokazał, że przycisk "Zapisz
    plik" znów nie reaguje TYLKO w OSTATNIEJ turze — tej, po której
    next_start_index=None (brak przycisku "Pobierz kolejne pozycje" niżej).
    Ten test symuluje CAŁY łańcuch (3 kontynuacje, ostatnia bez dalszego
    ciągu) i porównuje key/url widgetu download_button dla KAŻDEJ kolejnej
    pary renderów — w tym pary (przedostatnia tura → ostatnia), która
    poprzednio przeszła niezauważona, bo poprzedni test kończył się na
    JEDNYM przejściu z next_start_index wciąż ustawionym po obu stronach."""
    # 12 pozycji musi mieścić się w limicie — bez tego etykieta radia to
    # "pierwsze N z 12" (test przechodził tylko przy lokalnym .env z limitem ≥12).
    monkeypatch.setattr(config_module, "settings", Settings.from_env({"MAX_PLAYLIST_ITEMS": "12"}))
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 12)

    calls: list[int] = []
    # (start_index, first, last, next_start_index_po_tej_turze) — ostatnia
    # tura (10-12) ma next_start_index=None, czyli "koniec łańcucha".
    turns = [
        (4, 4, 6, 7),
        (7, 7, 9, 10),
        (10, 10, 12, None),
    ]

    def _fake_submit_playlist(self, job, on_event=None, start_index=1, selected_indices=None):
        n = len(calls)
        calls.append(start_index)
        _start, first, last, next_after = turns[n]
        turn_dir = tmp_path / f"turn{n}"
        turn_dir.mkdir()
        zip_path = turn_dir / f"turn{n}.zip"
        zip_path.write_bytes(f"turn {n} zip bytes".encode())
        items = [
            PlaylistItemResult(index=i, title=f"Wideo {i}", status="done")
            for i in range(first, last + 1)
        ]
        return PlaylistDownloadResult(
            zip_path=zip_path, items=items, playlist_title="Moja playlista", next_start_index=next_after
        )

    monkeypatch.setattr(engine_module.DownloadEngine, "submit_playlist", _fake_submit_playlist)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=uXlzoi70qUY&list=PL3jltwT7zlHiI4lHQh8fdlHGhw4Lfp5Aq"
    ).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (12 pozycji)").run()

    # Symulacja zakończonej PIERWSZEJ tury (pozycje 1-3, next_start_index=4)
    # — punkt startowy dla trzech kolejnych kontynuacji symulowanych niżej.
    seed_link = _publish_seed_zip(tmp_path, b"turn seed zip bytes")
    at.session_state["status"] = "done"
    at.session_state["result_download_token"] = seed_link.token
    at.session_state["result_file_name"] = "Playlista-Moja playlista.zip"
    at.session_state["playlist_report"] = [
        PlaylistItemResult(index=i, title=f"Wideo {i}", status="done") for i in (1, 2, 3)
    ]
    at.session_state["playlist_title"] = "Moja playlista"
    at.session_state["playlist_next_start_index"] = 4
    at.session_state["job_id"] = "seed-job"
    at.run()

    seen_urls = _link_urls(at)
    assert len(seen_urls) == 1

    for turn_index in range(3):
        assert "continue_playlist_button" in [b.key for b in at.button], (
            f"brak przycisku kontynuacji przed turą {turn_index}"
        )
        at.button(key="continue_playlist_button").click().run()
        for _ in range(10):
            if at.session_state["status"] == "done" and len(calls) > turn_index:
                break
            at.run()

        assert calls[turn_index] == turns[turn_index][0]
        urls = _link_urls(at)
        assert len(urls) == 1
        seen_urls.append(urls[0])

    # Ostatnia tura: next_start_index=None → koniec łańcucha, przycisk
    # "Pobierz kolejne pozycje" musi zniknąć.
    assert at.session_state["playlist_next_start_index"] is None
    assert "continue_playlist_button" not in [b.key for b in at.button]
    last_link = downloads.lookup(at.session_state["result_download_token"])
    assert last_link.path.read_bytes() == b"turn 2 zip bytes"

    # WSZYSTKIE 4 renderowania (seed + 3 tury, w tym przedostatnia→ostatnia)
    # mają unikalny link, a na dysku zostaje tylko ostatni plik — każda
    # kontynuacja zwolniła poprzedni.
    assert len(seen_urls) == len(set(seen_urls)), seen_urls
    assert seen_urls[-1] == downloads.download_url(last_link.token)
    remaining = [p for p in Path(downloads.settings.storage_base_dir).rglob("*") if p.is_file()]
    assert len(remaining) == 1


def test_playlist_job_logs_error_status_only_when_all_items_failed(monkeypatch, tmp_path):
    """Decyzja produktowa: status w historii DB = "error" TYLKO gdy ZERO
    pozycji się udało — inaczej "done" z podsumowaniem w error_message."""
    logged: dict = {}

    def _fake_log_job_finish(self, job_id, status, duration_ms=None, file_size_bytes=None, error_message=None):
        logged["status"] = status
        logged["error_message"] = error_message

    monkeypatch.setattr(Database, "log_job_finish", _fake_log_job_finish)

    at = _run_app(monkeypatch)

    zip_file = tmp_path / "playlist.zip"
    zip_file.write_bytes(b"fake zip bytes")

    items = [
        PlaylistItemResult(index=1, title="Wideo 1", status="error", error_message="boom"),
    ]
    finished_queue: queue_module.Queue = queue_module.Queue()
    finished_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Zakończono",
            result_path=zip_file,
            playlist_items=items,
            playlist_title="Moja playlista",
            output_format="mp4",
        )
    )
    _simulate_job_in_flight(at, finished_queue)
    at.session_state["db_job_id"] = 42

    at.run()

    assert not at.exception
    assert logged["status"] == "error"
    assert "0/1" in logged["error_message"]


def test_playlist_job_logs_done_status_with_summary_when_some_items_succeed(monkeypatch, tmp_path):
    logged: dict = {}

    def _fake_log_job_finish(self, job_id, status, duration_ms=None, file_size_bytes=None, error_message=None):
        logged["status"] = status
        logged["error_message"] = error_message

    monkeypatch.setattr(Database, "log_job_finish", _fake_log_job_finish)

    at = _run_app(monkeypatch)

    zip_file = tmp_path / "playlist.zip"
    zip_file.write_bytes(b"fake zip bytes")

    items = [
        PlaylistItemResult(index=1, title="Wideo 1", status="done"),
        PlaylistItemResult(index=2, title="Wideo 2", status="skipped", error_message="Pominięto."),
    ]
    finished_queue: queue_module.Queue = queue_module.Queue()
    finished_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Zakończono",
            result_path=zip_file,
            playlist_items=items,
            playlist_title="Moja playlista",
            output_format="mp4",
        )
    )
    _simulate_job_in_flight(at, finished_queue)
    at.session_state["db_job_id"] = 42

    at.run()

    assert not at.exception
    assert logged["status"] == "done"
    assert "1/2" in logged["error_message"]


def test_playlist_subtitle_probe_uses_representative_video_url_not_playlist_url(monkeypatch):
    """Kryterium akceptacji 3: dla playlist_scope="all" + Napisy/Transkrypt,
    dropdown języka musi być odpytany o URL PIERWSZEJ pozycji
    (resolve_representative_video_url), NIE surowy URL playlisty — sonda
    napisów na URL-u playlisty jest dokładnie tą samą klasą buga, jaką
    miał oryginalny extract_flat dla count_playlist_items."""
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 2)
    monkeypatch.setattr(
        engine_module,
        "resolve_representative_video_url",
        lambda url, cookie_data=None: "https://www.youtube.com/watch?v=representative1",
    )

    captured_urls: list[str] = []

    def _fake_list_available_subtitles(url, cookie_data=None):
        captured_urls.append(url)
        return {"manual": ["en"], "automatic": []}

    monkeypatch.setattr(engine_module, "list_available_subtitles", _fake_list_available_subtitles)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/playlist?list=PLrepresentativetest1"
    ).run()
    at.selectbox(key="mode_select").select("Napisy (SRT / VTT)").run()

    assert not at.exception
    assert captured_urls == ["https://www.youtube.com/watch?v=representative1"]
    assert list(at.selectbox(key="subtitle_lang_select").proto.options) == ["en"]


def test_switching_playlist_scope_after_completed_job_clears_previous_result(monkeypatch, tmp_path):
    """Kryterium akceptacji 4: zmiana zakresu "Tylko to wideo" <-> "Cała
    playlista" po zakończonym pobraniu czyści poprzedni wynik/raport."""
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 3)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=mixedtest5&list=PLmixedtest5"
    ).run()

    result_file = tmp_path / "Uploader-Title.mp3"
    result_file.write_bytes(b"fake mp3 bytes")
    finished_queue: queue_module.Queue = queue_module.Queue()
    finished_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Zakończono",
            result_path=result_file,
            result_uploader="Test Uploader",
            result_title="Test Title",
        )
    )
    _simulate_job_in_flight(at, finished_queue)
    at.run()
    assert at.session_state["status"] == "done"

    at.radio(key="playlist_scope_radio").set_value("Cała playlista (3 pozycji)").run()

    assert not at.exception
    assert at.session_state["status"] == "idle"


def test_switching_mode_after_playlist_stopped_early_clears_next_start_index_and_hides_continue_button(
    monkeypatch, tmp_path
):
    """Kryterium akceptacji 5 (Faza 2c): zmiana trybu między wznowieniami
    musi wyczyścić stan wznowienia (playlist_next_start_index) — inaczej
    "Pobierz kolejne pozycje" wznowiłoby playlistę dla trybu, którego
    użytkownik już nie wybrał."""
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 5)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=mixedtest7&list=PLmixedtest7"
    ).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (5 pozycji)").run()

    link = _publish_seed_zip(tmp_path, b"first zip bytes")
    at.session_state["status"] = "done"
    at.session_state["result_download_token"] = link.token
    at.session_state["result_file_name"] = "Playlista-Moja playlista.zip"
    at.session_state["playlist_report"] = [
        PlaylistItemResult(index=1, title="Wideo 1", status="done"),
    ]
    at.session_state["playlist_title"] = "Moja playlista"
    at.session_state["playlist_next_start_index"] = 2
    at.run()

    assert "continue_playlist_button" in [b.key for b in at.button]

    at.selectbox(key="mode_select").select("Audio (MP3 / FLAC)").run()

    assert not at.exception
    assert at.session_state["status"] == "idle"
    assert at.session_state["playlist_next_start_index"] is None
    assert at.session_state["result_download_token"] is None
    assert "continue_playlist_button" not in [b.key for b in at.button]
    # Czyszczenie wyniku zwalnia też plik z dysku, nie zostawia go do TTL.
    assert downloads.lookup(link.token) is None
    assert not link.path.exists()


def test_new_url_button_releases_playlist_zip_from_disk(monkeypatch, tmp_path):
    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=mixedtest8&list=PLmixedtest8"
    ).run()

    link = _publish_seed_zip(tmp_path, b"zip to release")
    at.session_state["status"] = "done"
    at.session_state["result_download_token"] = link.token
    at.session_state["result_file_name"] = link.file_name
    at.session_state["playlist_report"] = [PlaylistItemResult(index=1, title="Wideo 1", status="done")]
    at.run()
    assert _link_urls(at) == [downloads.download_url(link.token)]

    at.button(key="new_url_button").click().run()

    assert not at.exception
    assert downloads.lookup(link.token) is None
    assert not link.path.exists()
    assert _link_urls(at) == []


def test_expired_download_link_shows_warning_instead_of_dead_button(monkeypatch, tmp_path):
    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=mixedtest9&list=PLmixedtest9"
    ).run()

    link = _publish_seed_zip(tmp_path, b"expiring zip")
    downloads.release(link.token)
    at.session_state["status"] = "done"
    at.session_state["result_download_token"] = link.token
    at.session_state["result_file_name"] = link.file_name
    at.session_state["playlist_report"] = [PlaylistItemResult(index=1, title="Wideo 1", status="done")]
    at.run()

    assert not at.exception
    assert _link_urls(at) == []
    assert any("wygasł" in w.value for w in at.warning)


def test_playlist_result_without_download_route_shows_launch_instruction(monkeypatch, tmp_path):
    """Zabezpieczenie przed uruchomieniem `streamlit run app.py` wprost —
    bez trasy HTTP link do pobrania byłby martwy."""
    downloads.set_route_enabled(False)
    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=mixedtest10&list=PLmixedtest10"
    ).run()

    link = _publish_seed_zip(tmp_path, b"zip")
    at.session_state["status"] = "done"
    at.session_state["result_download_token"] = link.token
    at.session_state["result_file_name"] = link.file_name
    at.session_state["playlist_report"] = [PlaylistItemResult(index=1, title="Wideo 1", status="done")]
    at.run()

    assert not at.exception
    assert _link_urls(at) == []
    assert any("asgi_app.py" in e.value for e in at.error)


def test_playlist_publish_failure_reports_error_instead_of_crashing(monkeypatch, tmp_path):
    def _boom(source, file_name):
        raise OSError("disk full")

    monkeypatch.setattr(downloads, "publish", _boom)
    at = _run_app(monkeypatch)

    zip_file = tmp_path / "playlist.zip"
    zip_file.write_bytes(b"fake zip bytes")
    finished_queue: queue_module.Queue = queue_module.Queue()
    finished_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Zakończono",
            result_path=zip_file,
            playlist_items=[PlaylistItemResult(index=1, title="Wideo 1", status="done")],
            playlist_title="Moja playlista",
        )
    )
    _simulate_job_in_flight(at, finished_queue)
    at.run()

    assert not at.exception
    assert at.session_state["status"] == "error"
    assert "przygotować pliku" in at.session_state["error_message"]


def test_invalid_url_shows_error_on_download_click(monkeypatch):
    at = _run_app(monkeypatch)

    at.text_input(key="url_input").input("https://vimeo.com/12345").run()
    at.button(key="download_button").click().run()

    assert not at.exception
    assert len(at.error) >= 1
    assert "URL" in at.error[0].value


def test_new_url_button_disabled_while_url_empty(monkeypatch):
    at = _run_app(monkeypatch)

    assert not at.exception
    assert at.button(key="new_url_button").proto.disabled is True


def test_filled_url_enables_new_url_button_and_locks_input_on_next_rerun(monkeypatch):
    """La blokada text_input ma jednorenderowe opóźnienie z konieczności:
    Streamlit nie przyjmuje nowej wartości widgetu renderowanego w TYM
    SAMYM przebiegu jako disabled=True — więc pole jest zablokowane od
    NASTĘPNEGO przebiegu, nie od razu. "Nowy URL" nie ma tego problemu,
    bo jego `disabled` liczy się z wartości `url` zwróconej w tym samym
    przebiegu, nie z osobno przechowywanego flaga."""
    at = _run_app(monkeypatch)

    at.text_input(key="url_input").input("https://www.youtube.com/watch?v=jNQXAC9IVRw").run()

    assert not at.exception
    assert at.button(key="new_url_button").proto.disabled is False
    assert at.text_input(key="url_input").proto.disabled is False

    at.run()

    assert not at.exception
    assert at.text_input(key="url_input").value == "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    assert at.text_input(key="url_input").proto.disabled is True


def test_new_url_button_clears_and_unlocks_url_input(monkeypatch):
    at = _run_app(monkeypatch)

    at.text_input(key="url_input").input("https://www.youtube.com/watch?v=jNQXAC9IVRw").run()
    at.button(key="new_url_button").click().run()

    assert not at.exception
    assert at.text_input(key="url_input").value == ""
    assert at.text_input(key="url_input").proto.disabled is False


def _simulate_job_in_flight(at: AppTest, job_queue: "queue_module.Queue") -> None:
    """Symuluje "zadanie w toku" bez realnego wątku/silnika — wstrzykuje
    stan bezpośrednio do session_state (klucze SessionState — status/queue/
    job_id/started_at — są tu celowo odczytane po nazwie, bo to jedyny
    sposób odtworzenia stanu w połowie joba bez prawdziwego JobRunner)."""
    at.session_state["status"] = "running"
    at.session_state["queue"] = job_queue
    at.session_state["job_id"] = "job-smoke-test"
    at.session_state["started_at"] = 0.0


def test_premature_on_finished_without_result_path_is_not_treated_as_terminal(monkeypatch):
    """Regresja: engine.py emituje "on_finished" z progress_hooks w chwili,
    gdy sam DOWNLOAD się kończy — ZANIM postprocessing (ffmpeg/zapis
    napisów) faktycznie się skończy, więc ten event nie ma result_path.
    Jeśli fragment odpytujący kolejkę złapie WYŁĄCZNIE ten wczesny event
    (typowe: postprocessing trwa dłużej niż jeden tick pollingu 0.5s),
    nie może potraktować go jako zakończenie joba — inaczej UI zawsze
    kończy z "nie znaleziono pliku wynikowego", nawet gdy pobranie się
    naprawdę powiodło (i job_runner.py wysyłał już poprawny drugi event)."""
    at = _run_app(monkeypatch)

    premature_queue: queue_module.Queue = queue_module.Queue()
    premature_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Pobieranie zakończone, przetwarzanie...",
            # BEZ result_path — dokładnie tak, jak faktycznie emituje to
            # engine.py z progress_hooks (patrz komentarz w job_runner.py).
        )
    )
    _simulate_job_in_flight(at, premature_queue)

    at.run()

    assert not at.exception
    assert at.session_state["status"] == "running"
    assert at.session_state["error_message"] is None


def test_completed_job_with_result_path_shows_download_button(monkeypatch, tmp_path):
    at = _run_app(monkeypatch)

    result_file = tmp_path / "Uploader-Title.mp3"
    result_file.write_bytes(b"fake mp3 bytes")

    finished_queue: queue_module.Queue = queue_module.Queue()
    finished_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Zakończono",
            result_path=result_file,
            result_uploader="Test Uploader",
            result_title="Test Title",
        )
    )
    _simulate_job_in_flight(at, finished_queue)

    at.run()

    assert not at.exception
    assert at.session_state["status"] == "done"
    assert at.session_state["result_data"] == b"fake mp3 bytes"
    assert len(at.download_button) >= 1


def test_download_result_button_does_not_trigger_script_rerun_on_click(monkeypatch, tmp_path):
    """Regresja (trzecia iteracja buga "Zapisz plik nie reaguje"): domyślne
    on_click="rerun" na st.download_button zmuszałoby KAŻDE kliknięcie do
    przejścia przez tę samą, jednowątkową kolejkę rerunów skryptu, którą
    (potwierdzone w źródłach streamlit.runtime.fragment/scriptrunner)
    mogą zapychać osierocone auto-reruny fragmentu run_every=0.5 z
    _render_progress — ten fragment nigdy nie dostaje jawnego sygnału
    zatrzymania (stop_auto_rerun) po tym, jak zwykły pełny rerun przestaje
    go wywoływać. on_click="ignore" usuwa tę zależność: pobranie ZIP-a/
    pojedynczego pliku ma być czysto przeglądarkowe, bez rerunu.

    AppTest nie odtworzy realnego narastania ruchu WebSocket w tle — ten
    test weryfikuje TYLKO konfigurację widgetu (proto.ignore_rerun), nie
    zachowanie sieciowe. Manualna weryfikacja w przeglądarce (DevTools →
    Network, obserwacja po zakończeniu joba) wciąż jest potrzebna."""
    at = _run_app(monkeypatch)

    result_file = tmp_path / "Uploader-Title.mp3"
    result_file.write_bytes(b"fake mp3 bytes")

    finished_queue: queue_module.Queue = queue_module.Queue()
    finished_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Zakończono",
            result_path=result_file,
            result_uploader="Test Uploader",
            result_title="Test Title",
        )
    )
    _simulate_job_in_flight(at, finished_queue)

    at.run()

    assert not at.exception
    assert len(at.download_button) >= 1
    assert at.download_button[0].proto.ignore_rerun is True


def test_both_finished_events_in_same_queue_batch_resolve_to_done(monkeypatch, tmp_path):
    """Regresja: dla trybu Subtitle engine.py (progress_hooks) i job_runner.py
    wysyłają DWA zdarzenia "on_finished" pod rząd — pierwsze bez result_path
    (sam download skończony, postprocessing/zapis napisów jeszcze w locie),
    drugie z result_path (prawdziwie terminalne). Jeśli oba trafią do kolejki
    ZANIM fragment zdąży ją odpytać (typowe — czas między nimi jest krótszy
    niż jeden tick pollingu 0.5s), pętla drenująca w _render_progress musi
    wyciągnąć OBA w jednym cyklu i potraktować jako terminalne wyłącznie
    drugie — inaczej UI zatrzymałoby się na pierwszym (puste result_path)
    i pokazałoby "nie znaleziono pliku wynikowego" mimo realnego sukcesu."""
    at = _run_app(monkeypatch)

    result_file = tmp_path / "Uploader-Title.srt"
    result_file.write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nfake subtitle\n")

    batched_queue: queue_module.Queue = queue_module.Queue()
    batched_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Pobieranie zakończone, przetwarzanie...",
            # BEZ result_path — dokładnie jak wczesny event z progress_hooks.
        )
    )
    batched_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Zakończono",
            result_path=result_file,
            result_uploader="Test Uploader",
            result_title="Test Title",
        )
    )
    _simulate_job_in_flight(at, batched_queue)

    at.run()

    assert not at.exception
    assert at.session_state["status"] == "done"
    assert at.session_state["error_message"] is None
    assert at.session_state["result_data"] is not None
    assert len(at.download_button) >= 1


def _playlist_items(indices: list[int], status: str = "done") -> list[PlaylistItemResult]:
    return [PlaylistItemResult(index=i, title=f"Wideo {i}", status=status) for i in indices]


def _completed_playlist_file_name(
    monkeypatch,
    tmp_path,
    *,
    items: list[PlaylistItemResult],
    output_format: str | None,
    playlist_scope: str = "all",
    title: str | None = "Moja playlista",
) -> str:
    """Wstrzykuje zakończony job playlisty (ProgressEvent, jak z job_runner.py)
    i zwraca nazwę ZIP-a z session_state. Przy okazji sprawdza, że nazwa w UI
    jest tą samą, z którą link do pobrania (downloads.py -> Content-Disposition
    w download_routes.py) faktycznie serwuje plik."""
    at = _run_app(monkeypatch)

    zip_file = tmp_path / "playlist.zip"
    zip_file.write_bytes(b"fake zip bytes")

    finished_queue: queue_module.Queue = queue_module.Queue()
    finished_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Zakończono",
            result_path=zip_file,
            playlist_items=items,
            playlist_title=title,
            playlist_scope=playlist_scope,
            output_format=output_format,
        )
    )
    _simulate_job_in_flight(at, finished_queue)
    at.run()

    assert not at.exception
    assert at.session_state["status"] == "done"
    file_name = at.session_state["result_file_name"]
    link = downloads.lookup(at.session_state["result_download_token"])
    assert link is not None
    assert link.file_name == file_name
    return file_name


@pytest.mark.parametrize(
    "output_format",
    ["mp4", "mp3", "flac", "srt", "vtt", "txt"],
)
def test_playlist_zip_filename_carries_format_extension_before_zip(
    monkeypatch, tmp_path, output_format
):
    name = _completed_playlist_file_name(
        monkeypatch, tmp_path, items=_playlist_items(list(range(1, 8))), output_format=output_format
    )

    assert name == f"Playlista-Moja playlista-pozycje-01-07.{output_format}.zip"


def test_playlist_zip_filename_continuation_turn_carries_format_extension(monkeypatch, tmp_path):
    name = _completed_playlist_file_name(
        monkeypatch, tmp_path, items=_playlist_items(list(range(8, 15))), output_format="mp3"
    )

    assert name == "Playlista-Moja playlista-pozycje-08-14.mp3.zip"


def test_playlist_zip_filename_selected_short_list_carries_format_extension(monkeypatch, tmp_path):
    name = _completed_playlist_file_name(
        monkeypatch,
        tmp_path,
        items=_playlist_items([15, 21]),
        output_format="flac",
        playlist_scope="selected",
    )

    assert name == "Playlista-Moja playlista-pozycje-15,21.flac.zip"


def test_playlist_zip_filename_selected_long_list_carries_format_extension(monkeypatch, tmp_path):
    name = _completed_playlist_file_name(
        monkeypatch,
        tmp_path,
        items=_playlist_items([2, 4, 6, 8, 10, 12]),
        output_format="txt",
        playlist_scope="selected",
    )

    assert name == "Playlista-Moja playlista-pozycje-wybrane.txt.zip"


def test_playlist_zip_filename_very_long_title_keeps_extension(monkeypatch, tmp_path):
    """Tytuł playlisty nie jest dziś nigdzie skracany (app.py), więc
    rozszerzenie z definicji zostaje na końcu — test przypina to zachowanie,
    żeby ewentualne przyszłe skracanie nazwy bazowej nie ucięło ".mp4.zip"."""
    long_title = "A" * 300

    name = _completed_playlist_file_name(
        monkeypatch,
        tmp_path,
        items=_playlist_items([1, 2]),
        output_format="mp4",
        title=long_title,
    )

    assert name == f"Playlista-{long_title}-pozycje-01-02.mp4.zip"
    assert name.endswith(".mp4.zip")


def test_playlist_zip_filename_sanitizes_illegal_chars_and_keeps_extension(monkeypatch, tmp_path):
    name = _completed_playlist_file_name(
        monkeypatch,
        tmp_path,
        items=_playlist_items([1, 2]),
        output_format="mp3",
        title='Rock: "hits" <2024>/live?',
    )

    assert name == "Playlista-Rock_ _hits_ _2024__live_-pozycje-01-02.mp3.zip"
    assert not any(ch in name for ch in ':/\\*?"<>|')


def test_playlist_zip_filename_without_processed_items_still_carries_extension(monkeypatch, tmp_path):
    """Edge case: wszystkie pozycje "skipped" (brak zakresu) — sufiks
    "-pozycje-..." się nie pojawia, ale rozszerzenie formatu tak."""
    name = _completed_playlist_file_name(
        monkeypatch,
        tmp_path,
        items=_playlist_items([3, 4], status="skipped"),
        output_format="mp4",
    )

    assert name == "Playlista-Moja playlista.mp4.zip"


def test_playlist_zip_filename_without_output_format_falls_back_to_plain_zip(monkeypatch, tmp_path):
    """Zdarzenie bez output_format (None) nie może wywalić budowania nazwy —
    wraca stara nazwa bez rozszerzenia formatu."""
    name = _completed_playlist_file_name(
        monkeypatch, tmp_path, items=_playlist_items([1, 2]), output_format=None
    )

    assert name == "Playlista-Moja playlista-pozycje-01-02.zip"


# --- Mix/Radio (list=RD…): migawka listy ------------------------------------


def _mix_url(seed: str) -> str:
    return f"https://www.youtube.com/watch?v={seed}&list=RD{seed}"


def _mix_snapshot(url: str, count: int = 20, title: str = "Mix - Test") -> PlaylistSnapshot:
    seed = url.split("v=")[1].split("&")[0]
    entries = tuple(
        {
            "id": f"{seed}-{i}",
            "url": f"https://www.youtube.com/watch?v={seed}-{i}",
            "title": f"Tytuł {i}",
        }
        for i in range(1, count + 1)
    )
    return PlaylistSnapshot(url=url, title=title, entries=entries)


def _patch_mix_engine(monkeypatch, count: int = 20, exc: Exception | None = None):
    """Podstawia snapshot_playlist (zlicza wywołania) i count_playlist_items
    (zlicza — dla mixa NIE może być wołany). Zwraca (snapshot_calls, count_calls)."""
    snapshot_calls: list[str] = []
    count_calls: list[str] = []

    def _fake_snapshot(url, cookie_data=None):
        snapshot_calls.append(url)
        if exc is not None:
            raise exc
        return _mix_snapshot(url, count)

    def _fake_count(url, cookie_data=None):
        count_calls.append(url)
        return 99

    monkeypatch.setattr(engine_module, "snapshot_playlist", _fake_snapshot)
    monkeypatch.setattr(engine_module, "count_playlist_items", _fake_count)
    return snapshot_calls, count_calls


def test_mix_url_shows_warning_and_up_to_n_items_from_snapshot(monkeypatch):
    snapshot_calls, count_calls = _patch_mix_engine(monkeypatch, count=20)
    url = _mix_url("mixwarn0001")

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(url).run()

    assert not at.exception
    assert at.radio(key="playlist_scope_radio").options == [
        "Tylko to wideo",
        "Cała playlista (do 20 pozycji)",
        "Wybrane numery wideo z playlisty",
    ]
    warnings = [w.value for w in at.warning]
    assert any("generowany dynamicznie" in w and "20 pozycji" in w and "migawki" in w for w in warnings)
    # licznik z migawki — zwykła sonda (15-19 s, losowa liczba) nie jest wołana
    assert snapshot_calls == [url]
    assert count_calls == []
    assert at.session_state["playlist_snapshot"].url == url


def test_mix_snapshot_is_created_once_and_survives_reruns(monkeypatch):
    snapshot_calls, _ = _patch_mix_engine(monkeypatch, count=5)
    url = _mix_url("mixonce0001")

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(url).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (do 5 pozycji)").run()
    at.run()
    at.run()

    assert not at.exception
    assert snapshot_calls == [url]


def test_mix_snapshot_is_reused_for_continuation_turn(monkeypatch, tmp_path):
    """Tura 1 i tura 2 dostają TĘ SAMĄ migawkę; snapshot_playlist wołany raz
    (kolejne tury nie odczytują listy od nowa), nazwa ZIP-a z zakresem
    względem migawki i rozszerzeniem formatu."""
    snapshot_calls, _ = _patch_mix_engine(monkeypatch, count=5)
    received: list[tuple[PlaylistSnapshot | None, int]] = []

    def _fake_submit_playlist(self, job, on_event=None, start_index=1, selected_indices=None):
        received.append((job.playlist_snapshot, start_index))
        # osobny katalog na turę — app.py po publikacji woła storage.cleanup(zip.parent)
        turn_dir = tmp_path / f"turn{len(received)}"
        turn_dir.mkdir()
        zip_file = turn_dir / "playlist.zip"
        zip_file.write_bytes(b"fake zip bytes")
        first_turn = len(received) == 1
        indices = [1, 2] if first_turn else [3, 4, 5]
        return PlaylistDownloadResult(
            zip_path=zip_file,
            items=[PlaylistItemResult(index=i, title=f"Wideo {i}", status="done") for i in indices],
            playlist_title=job.playlist_snapshot.title,
            next_start_index=3 if first_turn else None,
        )

    monkeypatch.setattr(engine_module.DownloadEngine, "submit_playlist", _fake_submit_playlist)

    url = _mix_url("mixturns001")
    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(url).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (do 5 pozycji)").run()
    at.button(key="download_button").click().run()
    for _ in range(30):
        if at.session_state["status"] == "done" and len(received) == 1:
            break
        at.run()
    assert at.session_state["playlist_next_start_index"] == 3

    at.button(key="continue_playlist_button").click().run()
    for _ in range(30):
        if len(received) == 2 and at.session_state["status"] == "done":
            break
        at.run()

    assert not at.exception
    assert [start for _, start in received] == [1, 3]
    assert received[0][0] is received[1][0]
    assert received[0][0].url == url
    assert snapshot_calls == [url]
    assert at.session_state["result_file_name"] == "Playlista-Mix - Test-pozycje-03-05.mp4.zip"
    assert at.session_state["playlist_next_start_index"] is None


def test_new_url_button_invalidates_mix_snapshot(monkeypatch):
    snapshot_calls, _ = _patch_mix_engine(monkeypatch, count=5)
    url_a, url_b = _mix_url("mixnewurlA1"), _mix_url("mixnewurlB1")

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(url_a).run()
    assert at.session_state["playlist_snapshot"].url == url_a

    at.button(key="new_url_button").click().run()
    assert at.session_state["playlist_snapshot"] is None

    at.text_input(key="url_input").input(url_b).run()

    assert not at.exception
    assert snapshot_calls == [url_a, url_b]
    assert at.session_state["playlist_snapshot"].url == url_b


def test_snapshot_bound_to_a_different_url_is_not_reused(monkeypatch):
    snapshot_calls, _ = _patch_mix_engine(monkeypatch, count=5)
    old_url, new_url = _mix_url("mixstale0001"), _mix_url("mixstale0002")

    at = _run_app(monkeypatch)
    at.session_state["playlist_snapshot"] = _mix_snapshot(old_url, count=5)
    at.text_input(key="url_input").input(new_url).run()

    assert not at.exception
    assert snapshot_calls == [new_url]
    assert at.session_state["playlist_snapshot"].url == new_url


def test_mix_playlist_only_url_without_v_shows_error_and_blocks_download(monkeypatch):
    snapshot_calls, count_calls = _patch_mix_engine(monkeypatch)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input("https://www.youtube.com/playlist?list=RDmixnov00001").run()

    assert not at.exception
    assert any("v=" in e.value for e in at.error)
    assert at.button(key="download_button").proto.disabled is True
    assert len(at.radio) == 0
    assert snapshot_calls == [] and count_calls == []


def test_mix_snapshot_failure_shows_error_and_offers_only_single_video(monkeypatch):
    _patch_mix_engine(monkeypatch, exc=EmptyPlaylistSnapshotError("pusta"))

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(_mix_url("mixfail00001")).run()

    assert not at.exception
    assert any("Nie udało się odczytać żadnych pozycji" in e.value for e in at.error)
    assert at.radio(key="playlist_scope_radio").options == ["Tylko to wideo"]
    assert at.session_state["playlist_snapshot"] is None


def test_mix_limit_is_rd_limit_not_max_playlist_items(monkeypatch):
    """20 pozycji migawki w Video przy MAX_PLAYLIST_ITEMS=10 nie blokuje
    pobierania (limit dla mixa = MAX_PLAYLIST_RD_ITEMS, wbudowany w migawkę);
    dotyczy też trybu wybranych numerów."""
    monkeypatch.setattr(
        config_module,
        "settings",
        Settings.from_env({"MAX_PLAYLIST_ITEMS": "10", "MAX_PLAYLIST_RD_ITEMS": "20"}),
    )
    _patch_mix_engine(monkeypatch, count=20)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(_mix_url("mixlimit0001")).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (do 20 pozycji)").run()

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is False
    assert not any("limit" in w.value for w in at.warning)

    at.radio(key="playlist_scope_radio").set_value("Wybrane numery wideo z playlisty").run()
    at.text_input(key="selected_indices_input").input("1,2,3,4,5,6,7,8,9,10,11,12").run()

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is False


def test_mix_subtitle_probe_uses_first_snapshot_item_without_rereading_the_mix(monkeypatch):
    _patch_mix_engine(monkeypatch, count=5)
    probed: list[str] = []

    def _fail_resolve(url, cookie_data=None):
        raise AssertionError("resolve_representative_video_url nie może czytać mixa od nowa")

    monkeypatch.setattr(engine_module, "resolve_representative_video_url", _fail_resolve)
    monkeypatch.setattr(
        engine_module,
        "list_available_subtitles",
        lambda url, cookie_data=None: probed.append(url) or {"manual": ["en"], "automatic": []},
    )
    url = _mix_url("mixsubs00001")

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(url).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (do 5 pozycji)").run()
    at.selectbox(key="mode_select").set_value("Napisy (SRT / VTT)").run()

    assert not at.exception
    assert probed == ["https://www.youtube.com/watch?v=mixsubs00001-1"]


def test_regular_playlist_url_does_not_use_snapshot(monkeypatch):
    """Regresja: URL zwykłej playlisty (PL…) nadal idzie przez count_playlist_items."""
    monkeypatch.setattr(config_module, "settings", Settings.from_env({"MAX_PLAYLIST_ITEMS": "200"}))
    snapshot_calls, count_calls = _patch_mix_engine(monkeypatch)
    url = "https://www.youtube.com/watch?v=regplaylist1&list=PLregplaylist1"

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(url).run()

    assert not at.exception
    assert snapshot_calls == []
    assert count_calls == [url]
    assert at.radio(key="playlist_scope_radio").options[1] == "Cała playlista (99 pozycji)"
    assert at.session_state["playlist_snapshot"] is None


# --- MAX_PLAYLIST_ITEMS przycina zadanie (nie blokuje) we WSZYSTKICH trybach --

ALL_MODE_LABELS = ["Video (MP4)", "Audio (MP3 / FLAC)", "Napisy (SRT / VTT)", "Transkrypt (TXT)"]
MODE_EXTENSIONS = {
    "Video (MP4)": "mp4",
    "Audio (MP3 / FLAC)": "mp3",
    "Napisy (SRT / VTT)": "srt",
    "Transkrypt (TXT)": "txt",
}


def _limit_test_url(kind: str, mode_label: str) -> str:
    # unikalny URL na przypadek — sondy UI są cache'owane (st.cache_data) po URL-u
    seed = f"{kind}{''.join(ch for ch in mode_label if ch.isalnum())}"
    return f"https://www.youtube.com/watch?v={seed}&list=PL{seed}"


def _open_playlist_in_mode(monkeypatch, url: str, scope_label: str, mode_label: str) -> AppTest:
    # Hermetycznie: w trybie napisów UI woła obie sondy (język napisów oraz
    # reprezentatywne wideo playlisty) — bez podstawienia poszłyby do sieci.
    monkeypatch.setattr(engine_module, "resolve_representative_video_url", lambda url, cookie_data=None: None)
    monkeypatch.setattr(
        engine_module,
        "list_available_subtitles",
        lambda url, cookie_data=None: {"manual": ["en"], "automatic": []},
    )
    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(url).run()
    at.radio(key="playlist_scope_radio").set_value(scope_label).run()
    at.selectbox(key="mode_select").select(mode_label).run()
    return at


@pytest.mark.parametrize("mode_label", ALL_MODE_LABELS)
def test_playlist_scope_all_over_limit_trims_with_info_and_keeps_download_enabled(
    monkeypatch, mode_label
):
    """Playlista (15) dłuższa niż limit (10, PINOWANY): "Pobierz" zostaje
    aktywny, etykieta zakresu pokazuje przycięcie, a użytkownik dostaje
    spokojną informację (st.info — nie warning/error), bez nazwy trybu i bez
    prośby o "krótszą playlistę"."""
    monkeypatch.setattr(config_module, "settings", Settings.from_env({"MAX_PLAYLIST_ITEMS": "10"}))
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 15)

    at = _open_playlist_in_mode(
        monkeypatch,
        _limit_test_url("trimall", mode_label),
        "Cała playlista (pierwsze 10 z 15)",
        mode_label,
    )

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is False
    info_messages = [i.value for i in at.info]
    assert len(info_messages) == 1
    assert "15" in info_messages[0] and "pierwsze 10" in info_messages[0]
    assert "Wybrane numery" in info_messages[0]
    assert mode_label not in info_messages[0] and "krótszą" not in info_messages[0]
    assert not at.warning and not at.error
    assert at.session_state["playlist_scope"] == "all"


@pytest.mark.parametrize("mode_label", ALL_MODE_LABELS)
def test_playlist_scope_all_at_limit_shows_no_info_in_every_mode(monkeypatch, mode_label):
    monkeypatch.setattr(config_module, "settings", Settings.from_env({"MAX_PLAYLIST_ITEMS": "10"}))
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 10)

    at = _open_playlist_in_mode(
        monkeypatch, _limit_test_url("trimok", mode_label), "Cała playlista (10 pozycji)", mode_label
    )

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is False
    assert not at.info and not at.warning and not at.error


@pytest.mark.parametrize("mode_label", ALL_MODE_LABELS)
def test_selected_scope_over_limit_trims_with_info_and_keeps_download_enabled(monkeypatch, mode_label):
    """Wybrano 3 pozycje przy limicie 2 — pobierzemy pierwsze 2 z wybranych."""
    monkeypatch.setattr(config_module, "settings", Settings.from_env({"MAX_PLAYLIST_ITEMS": "2"}))
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 32)

    at = _open_playlist_in_mode(
        monkeypatch,
        _limit_test_url("trimsel", mode_label),
        "Wybrane numery wideo z playlisty",
        mode_label,
    )
    at.text_input(key="selected_indices_input").input("5, 10, 15").run()

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is False
    info_messages = [i.value for i in at.info]
    assert len(info_messages) == 1
    assert "Wybrano 3 pozycji" in info_messages[0] and "pierwsze 2" in info_messages[0]
    assert mode_label not in info_messages[0]
    assert not at.warning and not at.error


@pytest.mark.parametrize("mode_label", ALL_MODE_LABELS)
def test_selected_scope_within_limit_of_a_long_playlist_shows_no_info(monkeypatch, mode_label):
    """Playlista (32) dłuższa niż limit (10), ale wybrane 2 pozycje — bez zmian."""
    monkeypatch.setattr(config_module, "settings", Settings.from_env({"MAX_PLAYLIST_ITEMS": "10"}))
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 32)

    at = _open_playlist_in_mode(
        monkeypatch,
        _limit_test_url("trimfew", mode_label),
        "Wybrane numery wideo z playlisty",
        mode_label,
    )
    at.text_input(key="selected_indices_input").input("5, 10").run()

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is False
    assert not at.info and not at.warning and not at.error


def test_mix_snapshot_in_subtitle_mode_ignores_max_playlist_items(monkeypatch):
    """Mix/Radio (RD) w trybie napisów: limit to MAX_PLAYLIST_RD_ITEMS
    (wbudowany w migawkę), nie MAX_PLAYLIST_ITEMS — 20 pozycji przy limicie
    zwykłych playlist 10 nie jest przycinane ani komunikowane jako przycięcie."""
    monkeypatch.setattr(
        config_module,
        "settings",
        Settings.from_env({"MAX_PLAYLIST_ITEMS": "10", "MAX_PLAYLIST_RD_ITEMS": "20"}),
    )
    _patch_mix_engine(monkeypatch, count=20)
    monkeypatch.setattr(
        engine_module,
        "list_available_subtitles",
        lambda url, cookie_data=None: {"manual": ["en"], "automatic": []},
    )

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(_mix_url("mixsublimit1")).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (do 20 pozycji)").run()
    at.selectbox(key="mode_select").select("Napisy (SRT / VTT)").run()

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is False
    assert not at.info


# --- end-to-end: prawdziwy silnik, podstawiony yt_dlp -----------------------


class _E2EYDL:
    def __init__(self, opts: dict, on_extract) -> None:
        self._on_extract = on_extract

    def __enter__(self) -> "_E2EYDL":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def extract_info(self, url: str, download: bool = True) -> dict:
        return self._on_extract(url)


def _install_e2e_engine(monkeypatch, tmp_path, mode_label: str, total: int, limit: int) -> list[str]:
    """Prawdziwy DownloadEngine.submit_playlist z podstawionym YoutubeDL.
    Zwraca listę id faktycznie pobranych wideo (kolejność pobrań)."""
    pinned = Settings.from_env({"MAX_PLAYLIST_ITEMS": str(limit)})
    monkeypatch.setattr(config_module, "settings", pinned)
    monkeypatch.setattr(engine_module, "settings", pinned)
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: total)

    job_dir = tmp_path / "job"
    monkeypatch.setattr(
        engine_module.storage, "create", lambda session_id, job_id: (job_dir.mkdir(exist_ok=True), job_dir)[1]
    )
    flat = {
        "_type": "playlist",
        "title": "Playlista testowa",
        "entries": [{"id": f"e2e{i}", "title": f"Tytuł {i}"} for i in range(1, total + 1)],
    }
    downloaded: list[str] = []

    def _download_info(url: str) -> dict:
        video_id = url.split("v=")[1]
        downloaded.append(video_id)
        if mode_label in ("Napisy (SRT / VTT)", "Transkrypt (TXT)"):
            vtt_path = job_dir / f"{video_id}.en.vtt"
            vtt_path.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nTreść.\n", encoding="utf-8")
            return {
                "webpage_url": url,
                "requested_subtitles": {"en": {"filepath": str(vtt_path)}},
                "uploader": "Channel",
                "title": f"Video {video_id}",
            }
        ext = "mp4" if mode_label == "Video (MP4)" else "mp3"
        media_path = job_dir / f"{video_id}.{ext}"
        media_path.write_bytes(b"fake media bytes")
        return {
            "requested_downloads": [{"filepath": str(media_path)}],
            "uploader": "Channel",
            "title": f"Video {video_id}",
        }

    def _factory(opts: dict) -> _E2EYDL:
        if "extract_flat" in opts:
            return _E2EYDL(opts, lambda url: flat)
        return _E2EYDL(opts, _download_info)

    monkeypatch.setattr(engine_module, "YoutubeDL", _factory)
    return downloaded


def _click_download_and_wait(at: AppTest) -> None:
    at.button(key="download_button").click().run()
    for _ in range(40):
        if at.session_state["status"] in ("done", "error"):
            break
        at.run()


@pytest.mark.parametrize("mode_label", ALL_MODE_LABELS)
def test_e2e_all_scope_downloads_first_n_and_names_zip_by_actual_range(
    monkeypatch, tmp_path, mode_label
):
    """Playlista 5-pozycyjna, limit 3: prawdziwy silnik pobiera wideo 1..3, a
    nazwa ZIP-a (sufiks + rozszerzenie formatu) odzwierciedla faktyczny zakres."""
    downloaded = _install_e2e_engine(monkeypatch, tmp_path, mode_label, total=5, limit=3)

    at = _open_playlist_in_mode(
        monkeypatch,
        _limit_test_url("e2eall", mode_label),
        "Cała playlista (pierwsze 3 z 5)",
        mode_label,
    )
    _click_download_and_wait(at)

    assert not at.exception
    assert at.session_state["status"] == "done"
    assert downloaded == ["e2e1", "e2e2", "e2e3"]
    assert [item.index for item in at.session_state["playlist_report"]] == [1, 2, 3]
    assert at.session_state["playlist_next_start_index"] is None
    ext = MODE_EXTENSIONS[mode_label]
    assert at.session_state["result_file_name"] == f"Playlista-Playlista testowa-pozycje-01-03.{ext}.zip"
    assert "continue_playlist_button" not in [b.key for b in at.button]


def test_e2e_selected_scope_downloads_first_n_of_sorted_selection(monkeypatch, tmp_path):
    downloaded = _install_e2e_engine(monkeypatch, tmp_path, "Video (MP4)", total=12, limit=3)

    at = _open_playlist_in_mode(
        monkeypatch,
        _limit_test_url("e2esel", "Video (MP4)"),
        "Wybrane numery wideo z playlisty",
        "Video (MP4)",
    )
    at.text_input(key="selected_indices_input").input("9, 1, 5, 2").run()
    _click_download_and_wait(at)

    assert not at.exception
    assert at.session_state["status"] == "done"
    assert downloaded == ["e2e1", "e2e2", "e2e5"]
    assert [item.index for item in at.session_state["playlist_report"]] == [1, 2, 5]
    assert at.session_state["result_file_name"] == "Playlista-Playlista testowa-pozycje-1,2,5.mp4.zip"


def test_clicking_download_writes_job_history_to_fake_not_real_database(
    monkeypatch, tmp_path, database_calls
):
    """Strażnik izolacji bazy (tests/conftest.py::database_calls): kliknięcie
    "Pobierz" woła Database.log_job_start (start joba) i log_job_finish
    (koniec) — oba muszą trafić do atrapy, nie do Neon. Realne
    psycopg.connect jest w testach zablokowane, więc ominięcie atrapy
    kończyłoby się cichym db_job_id=None (app.py połyka wyjątek) — dlatego
    asercje idą po rejestrze atrapy, nie po braku błędu."""
    url = "https://www.youtube.com/watch?v=dbguard1&list=PLdbguard1"
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 3)

    zip_file = tmp_path / "playlist.zip"
    zip_file.write_bytes(b"fake zip bytes")

    def _fake_submit_playlist(self, job, on_event=None, start_index=1, selected_indices=None):
        # Niepusta lista: playlista z samymi błędami logowana jest jako "error".
        items = [PlaylistItemResult(index=1, title="Wideo 1", status="done")]
        return PlaylistDownloadResult(zip_path=zip_file, items=items, playlist_title="Fake")

    monkeypatch.setattr(engine_module.DownloadEngine, "submit_playlist", _fake_submit_playlist)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(url).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (3 pozycji)").run()
    assert database_calls.starts == []

    at.button(key="download_button").click().run()
    if at.session_state["status"] != "done":
        at.run()

    assert not at.exception
    assert [call["url"] for call in database_calls.starts] == [url]
    # AppTest nie ma nagłówka X-Forwarded-For → hash "unknown" (nie stała "local-dev").
    assert database_calls.starts[0]["client_ip_hash"] == "unknown"
    assert [call["job_id"] for call in database_calls.finishes] == [1]
    assert database_calls.finishes[0]["status"] == "done"
