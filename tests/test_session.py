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
