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
