"""Test SessionState — Warstwa 3 specyfikacji.

Działa na zwykłym dict(), bez uruchomionego Streamlit — SessionState nie
importuje `st.session_state`.
"""

from pathlib import Path

from src.session import SessionState


def test_session_state_works_on_plain_dict():
    state = SessionState({})

    assert state.status == "idle"
    assert state.is_terminal() is False


def test_idle_to_running_to_done_transition():
    state = SessionState({})

    state.set_running()
    assert state.status == "running"
    assert state.is_terminal() is False

    state.set_progress(42.5, "Pobrano 42%")
    assert state.status == "running"
    assert state.percent == 42.5
    assert state.message == "Pobrano 42%"

    state.set_done(Path("/tmp/session/job/file.mp3"))
    assert state.status == "done"
    assert state.percent == 100.0
    assert state.result_path == Path("/tmp/session/job/file.mp3")
    assert state.is_terminal() is True


def test_idle_to_running_to_error_transition():
    state = SessionState({})

    state.set_running()
    state.set_error("Niepoprawny URL")

    assert state.status == "error"
    assert state.error_message == "Niepoprawny URL"
    assert state.is_terminal() is True


def test_reset_returns_to_idle():
    state = SessionState({})
    state.set_running()
    state.set_done(Path("x.mp3"))

    state.reset()

    assert state.status == "idle"
    assert state.percent == 0.0
    assert state.result_path is None
    assert state.error_message is None
    assert state.is_terminal() is False


def test_session_state_persists_across_reconstruction_with_same_store():
    store: dict = {}
    SessionState(store).set_running()

    # symuluje kolejny rerun Streamlit operujący na tym samym store
    state_after_rerun = SessionState(store)

    assert state_after_rerun.status == "running"


def test_session_state_defaults_to_fresh_dict_when_no_store_given():
    state = SessionState()

    assert state.status == "idle"


def test_url_locked_defaults_false_and_is_settable():
    state = SessionState({})

    assert state.url_locked is False

    state.set_url_locked(True)
    assert state.url_locked is True


def test_reset_unlocks_url():
    state = SessionState({})
    state.set_url_locked(True)

    state.reset()

    assert state.url_locked is False


def test_last_mode_format_defaults_none_and_is_settable():
    state = SessionState({})

    assert state.last_mode_format is None

    state.set_last_mode_format(("audio", "mp3"))
    assert state.last_mode_format == ("audio", "mp3")


def test_clear_result_returns_to_idle_without_touching_url_lock_or_mode_format():
    state = SessionState({})
    state.set_url_locked(True)
    state.set_last_mode_format(("audio", "mp3"))
    state.set_running()
    state.set_done(Path("x.mp3"), data=b"abc", file_name="x.mp3", uploader="Chan", title="Tit")

    state.clear_result()

    assert state.status == "idle"
    assert state.percent == 0.0
    assert state.result_path is None
    assert state.result_data is None
    assert state.result_file_name is None
    assert state.result_uploader is None
    assert state.result_title is None
    assert state.error_message is None
    # NIE dotknięte przez clear_result():
    assert state.url_locked is True
    assert state.last_mode_format == ("audio", "mp3")


def test_begin_job_stores_job_id_and_subtitle_lang():
    state = SessionState({})

    state.begin_job("job-123", subtitle_lang="pl")

    assert state.status == "running"
    assert state.job_id == "job-123"
    assert state.subtitle_lang == "pl"
    assert state.queue is not None


def test_begin_job_without_subtitle_lang_defaults_to_none():
    state = SessionState({})

    state.begin_job("job-456")

    assert state.subtitle_lang is None


def test_set_done_stores_uploader_and_title():
    state = SessionState({})

    state.set_done(Path("x.mp3"), data=b"abc", file_name="x.mp3", uploader="Channel", title="Title")

    assert state.result_uploader == "Channel"
    assert state.result_title == "Title"


def test_playlist_scope_defaults_to_single_and_is_settable():
    state = SessionState({})

    assert state.playlist_scope == "single"

    state.set_playlist_scope("all")
    assert state.playlist_scope == "all"


def test_reset_returns_playlist_scope_to_single():
    state = SessionState({})
    state.set_playlist_scope("all")

    state.reset()

    assert state.playlist_scope == "single"


def test_playlist_report_and_title_default_none_and_are_settable_via_set_done():
    state = SessionState({})

    assert state.playlist_report is None
    assert state.playlist_title is None

    items = [object(), object()]
    state.set_done(Path("playlist.zip"), data=b"zip", playlist_report=items, playlist_title="Moja playlista")

    assert state.playlist_report == items
    assert state.playlist_title == "Moja playlista"


def test_clear_result_also_clears_playlist_report_and_title():
    state = SessionState({})
    state.set_done(Path("playlist.zip"), data=b"zip", playlist_report=[object()], playlist_title="Tytuł")

    state.clear_result()

    assert state.playlist_report is None
    assert state.playlist_title is None


def test_playlist_next_start_index_defaults_none_and_settable_via_set_done():
    state = SessionState({})

    assert state.playlist_next_start_index is None

    state.set_done(Path("playlist.zip"), data=b"zip", playlist_next_start_index=6)

    assert state.playlist_next_start_index == 6


def test_clear_result_also_clears_playlist_next_start_index():
    state = SessionState({})
    state.set_done(Path("playlist.zip"), data=b"zip", playlist_next_start_index=6)

    state.clear_result()

    assert state.playlist_next_start_index is None


def test_begin_job_with_clear_previous_result_false_keeps_previous_result():
    """Faza 2c: "Pobierz kolejne pozycje" nie może wyczyścić poprzedniego
    ZIP-a/raportu, dopóki NOWY job faktycznie się nie zakończy — użytkownik
    mógł jeszcze nie zdążyć zapisać poprzedniego pliku."""
    state = SessionState({})
    state.set_done(
        Path("playlist.zip"),
        data=b"previous zip bytes",
        file_name="Playlista-Tytul.zip",
        playlist_report=[object()],
        playlist_title="Tytuł",
        playlist_next_start_index=6,
    )

    state.begin_job("job-continue", clear_previous_result=False)

    assert state.status == "running"
    assert state.result_data == b"previous zip bytes"
    assert state.playlist_report is not None
    assert state.playlist_title == "Tytuł"
    assert state.playlist_next_start_index == 6


def test_begin_job_default_clears_previous_result_as_before():
    """Zero regresji: domyślne begin_job() (bez argumentu) zachowuje się
    jak dotychczas — czyści wynik poprzedniego zadania."""
    state = SessionState({})
    state.set_done(Path("x.mp3"), data=b"abc", uploader="Chan", title="Tit")

    state.begin_job("job-fresh")

    assert state.result_data is None
    assert state.result_uploader is None


def test_cancel_queued_job_restores_done_when_previous_result_present():
    state = SessionState({})
    state.set_done(Path("playlist.zip"), data=b"previous zip bytes", playlist_next_start_index=6)
    state.begin_job("job-continue", clear_previous_result=False)

    state.cancel_queued_job()

    assert state.status == "done"
    assert state.result_data == b"previous zip bytes"
    assert state.job_id is None
    assert state.queue is None


def test_cancel_queued_job_restores_idle_when_no_previous_result():
    state = SessionState({})
    state.begin_job("job-fresh")

    state.cancel_queued_job()

    assert state.status == "idle"


def test_set_done_stores_download_token_and_clear_result_drops_it():
    state = SessionState({})

    state.set_done(Path("playlist.zip"), download_token="tok-123", file_name="Playlista-X.zip")

    assert state.result_download_token == "tok-123"
    assert state.result_data is None

    state.clear_result()

    assert state.result_download_token is None


def test_cancel_queued_job_restores_done_when_previous_result_is_a_download_token():
    state = SessionState({})
    state.set_done(Path("playlist.zip"), download_token="tok-123", playlist_next_start_index=6)
    state.begin_job("job-continue", clear_previous_result=False)

    state.cancel_queued_job()

    assert state.status == "done"
    assert state.result_download_token == "tok-123"


def test_client_ip_hash_is_resolved_once_and_survives_reset():
    calls = []

    def resolve() -> str:
        calls.append(1)
        return "hash-a"

    state = SessionState({})

    assert state.client_ip_hash(resolve) == "hash-a"
    assert state.client_ip_hash(resolve) == "hash-a"
    state.reset()
    assert state.client_ip_hash(resolve) == "hash-a"
    assert len(calls) == 1


def test_history_cache_defaults_to_not_loaded():
    state = SessionState({})

    assert state.history_loaded is False
    assert state.history_rows is None
    assert state.history_error is False


def test_set_history_cache_stores_rows_and_marks_loaded():
    state = SessionState({})
    rows = [{"id": 1, "source_url": "https://youtu.be/x"}]

    state.set_history_cache(rows)

    assert state.history_loaded is True
    assert state.history_rows == rows
    assert state.history_error is False


def test_set_history_cache_with_error_marks_loaded_without_rows():
    state = SessionState({})

    state.set_history_cache(None, error=True)

    assert state.history_loaded is True
    assert state.history_rows is None
    assert state.history_error is True


def test_invalidate_history_cache_resets_to_not_loaded():
    state = SessionState({})
    state.set_history_cache([{"id": 1}])

    state.invalidate_history_cache()

    assert state.history_loaded is False
    assert state.history_rows is None
    assert state.history_error is False


def test_history_cache_survives_reset():
    """Jak client_ip_hash/session_id — historia nie zależy od URL-a, więc
    "Nowy URL" (reset()) nie powinien wymuszać ponownego odczytu z bazy."""
    state = SessionState({})
    rows = [{"id": 1, "source_url": "https://youtu.be/x"}]
    state.set_history_cache(rows)

    state.reset()

    assert state.history_loaded is True
    assert state.history_rows == rows
