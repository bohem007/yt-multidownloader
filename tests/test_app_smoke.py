"""Smoke testy app.py przez streamlit.testing.v1.AppTest.

Bez realnego pobierania i bez realnych zapytań do Neon —
Database.get_recent_history jest podstawiony atrapą (zakładka "Historia"
wykonuje zapytanie na KAŻDYM rerunie skryptu, niezależnie od aktywnej
zakładki — Streamlit renderuje treść wszystkich st.tabs() w każdym
przebiegu), żeby testy były szybkie, deterministyczne i offline.
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
from src.engine import PlaylistDownloadResult, PlaylistItemResult
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
    monkeypatch.setattr(Database, "get_recent_history", lambda self, limit=20: [])
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


def test_playlist_scope_all_over_limit_blocks_download_for_video_mode(monkeypatch):
    """N > MAX_PLAYLIST_ITEMS dla Video musi zablokować przycisk "Pobierz"
    PRZED kliknięciem, z komunikatem podającym realny limit — nie zaszytą
    liczbę. settings PINOWANE monkeypatchem (nie ambient .env developera) —
    bez tego test byłby niedeterministyczny: przechodzi/pada zależnie od
    tego, co akurat ma lokalny .env (patrz diagnoza "zaszyta wartość 10")."""
    monkeypatch.setattr(config_module, "settings", Settings.from_env({}))
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 15)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=mixedtest2&list=PLmixedtest2"
    ).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (15 pozycji)").run()

    assert not at.exception
    # Tryb domyślny (pierwsza opcja selectboxa) to Video — limit obowiązuje.
    assert at.button(key="download_button").proto.disabled is True
    warning_messages = [w.value for w in at.warning]
    assert any("15" in message and "10" in message for message in warning_messages)


def test_playlist_scope_all_gating_uses_configured_limit_not_hardcoded_default(monkeypatch):
    """Regresja: dotychczasowe testy limitu playlisty zawsze porównywały
    przeciw domyślnej wartości MAX_PLAYLIST_ITEMS=10 — literalna "10"
    zaszyta w gatingu/komunikacie zamiast settings.max_playlist_items
    przechodziłaby więc niezauważona. Tu limit jest jawnie skonfigurowany
    na NIEDOMYŚLNĄ wartość (5) w obie strony sprawdzenia: liczba pozycji
    (7) > 5 musi zablokować przycisk, a komunikat musi podawać "5", nie "10"."""
    monkeypatch.setattr(config_module, "settings", Settings.from_env({"MAX_PLAYLIST_ITEMS": "5"}))
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 7)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=configtest1&list=PLconfigtest1"
    ).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (7 pozycji)").run()

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is True
    warning_messages = [w.value for w in at.warning]
    assert any("7" in message and "5" in message for message in warning_messages)
    assert not any("10" in message for message in warning_messages)


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


def test_playlist_scope_all_over_limit_does_not_block_download_for_subtitle_mode(monkeypatch):
    """Limit liczby pozycji NIE dotyczy Subtitle/Transcript — przycisk
    "Pobierz" nie może być blokowany przez playlist_limit_exceeded dla
    tych trybów, niezależnie od N."""
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 15)
    monkeypatch.setattr(
        engine_module,
        "list_available_subtitles",
        lambda url, cookie_data=None: {"manual": ["en"], "automatic": []},
    )

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=mixedtest3&list=PLmixedtest3"
    ).run()
    at.radio(key="playlist_scope_radio").set_value("Cała playlista (15 pozycji)").run()
    at.selectbox(key="mode_select").select("Napisy (SRT / VTT)").run()

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is False


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


def test_selected_scope_over_max_playlist_items_blocks_video_mode(monkeypatch):
    """Punkt 4: limit MAX_PLAYLIST_ITEMS dla "selected" dotyczy LICZBY
    WYBRANYCH pozycji, nie długości całej playlisty (settings PINOWANE,
    nie ambient .env — patrz test_playlist_scope_all_over_limit_...)."""
    monkeypatch.setattr(config_module, "settings", Settings.from_env({"MAX_PLAYLIST_ITEMS": "2"}))
    monkeypatch.setattr(engine_module, "count_playlist_items", lambda url, cookie_data=None: 32)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input(
        "https://www.youtube.com/watch?v=selectedtest4&list=PLselectedtest4"
    ).run()
    at.radio(key="playlist_scope_radio").set_value("Wybrane numery wideo z playlisty").run()
    at.text_input(key="selected_indices_input").input("5, 10, 15").run()

    assert not at.exception
    assert at.button(key="download_button").proto.disabled is True
    warning_messages = [w.value for w in at.warning]
    assert any("Wybrano 3 pozycji" in m and "limit" in m for m in warning_messages)


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
    assert at.session_state["result_file_name"] == "Playlista-Moja playlista-pozycje-15,21.zip"


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
        )
    )
    _simulate_job_in_flight(at, finished_queue)

    at.run()

    assert not at.exception
    assert at.session_state["status"] == "done"
    assert at.session_state["result_file_name"] == "Playlista-Moja playlista-pozycje-01-02.zip"
    # ZIP zostaje na dysku pod nieodgadywalnym tokenem — NIE w RAM/session_state.
    assert at.session_state["result_data"] is None
    token = at.session_state["result_download_token"]
    link = downloads.lookup(token)
    assert link is not None
    assert link.path.read_bytes() == b"fake zip bytes"
    assert link.file_name == "Playlista-Moja playlista-pozycje-01-02.zip"
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
        )
    )
    _simulate_job_in_flight(at, finished_queue)

    at.run()

    assert not at.exception
    assert at.session_state["status"] == "done"
    # Pierwsze wywołanie (start_index=1 domyślnie) dostaje sufiks zakresu
    # tak samo jak kontynuacje (fix regresji z 2026-09-20).
    assert at.session_state["result_file_name"] == "Playlista-Moja playlista-pozycje-01-02.zip"
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
    assert at.session_state["result_file_name"] == "Playlista-Moja playlista-pozycje-03-05.zip"
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
