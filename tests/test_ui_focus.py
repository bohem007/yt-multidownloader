"""Testy src/ui_focus.py — jednorazowy fokus przez skrypt w st.html.

Bez przeglądarki: st.html jest podmienione na rejestrator, więc sprawdzamy
treść i liczbę emisji skryptu, nie samo ustawienie fokusu (test manualny).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import src.ui_focus as ui_focus
from src.session import SessionState

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture
def emitted(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def _record_html(body, *, unsafe_allow_javascript=False, **kwargs):
        calls.append({"body": body, "unsafe_allow_javascript": unsafe_allow_javascript})

    monkeypatch.setattr(ui_focus.st, "html", _record_html)
    return calls


def test_request_focus_sets_target_and_bumps_nonce():
    state = SessionState({})

    ui_focus.request_focus(state, "download")
    assert (state.focus_target, state.focus_nonce) == ("download", 1)

    ui_focus.request_focus(state, "url")
    assert (state.focus_target, state.focus_nonce) == ("url", 2)


def test_request_focus_rejects_unknown_target():
    with pytest.raises(ValueError):
        ui_focus.request_focus(SessionState({}), "nowhere")


def test_render_focus_script_emits_once_and_clears_target(emitted):
    state = SessionState({})
    ui_focus.request_focus(state, "download")

    ui_focus.render_focus_script(state)
    ui_focus.render_focus_script(state)

    assert len(emitted) == 1
    assert emitted[0]["unsafe_allow_javascript"] is True
    assert ui_focus.FOCUS_SELECTORS["download"] in emitted[0]["body"]
    assert "focus #1" in emitted[0]["body"]
    assert state.focus_target is None


def test_render_focus_script_without_request_emits_nothing(emitted):
    ui_focus.render_focus_script(SessionState({}))

    assert emitted == []


def test_each_request_produces_a_different_script(emitted):
    """Przeglądarka wykonuje skrypt st.html ponownie tylko przy zmianie
    treści — dwie prośby o ten sam cel muszą dać dwie RÓŻNE treści."""
    state = SessionState({})
    for _ in range(2):
        ui_focus.request_focus(state, "url")
        ui_focus.render_focus_script(state)

    assert len(emitted) == 2
    assert emitted[0]["body"] != emitted[1]["body"]


def test_download_focus_moves_only_from_the_url_field(emitted):
    """Fokus na "Pobierz" (z on_change pola URL — ENTER albo opuszczenie
    pola) nie może wyrwać użytkownika z innego widżetu; fokus na pole URL
    (po "Nowy URL") takiego warunku nie ma."""
    state = SessionState({})
    for target in ("download", "url"):
        ui_focus.request_focus(state, target)
        ui_focus.render_focus_script(state)

    download_script, url_script = (call["body"] for call in emitted)
    assert f'const onlyWhenFocusIn = "{ui_focus.FOCUS_SELECTORS["url"]}";' in download_script
    assert "const onlyWhenFocusIn = null;" in url_script


def test_focus_nonce_survives_session_reset():
    state = SessionState({})
    ui_focus.request_focus(state, "url")

    state.reset()
    ui_focus.request_focus(state, "url")

    assert state.focus_nonce == 2


def test_focus_selectors_match_widget_keys_in_app():
    """Selektor .st-key-<key> działa tylko, dopóki widżet w app.py ma ten
    sam key= — zmiana klucza po cichu wyłączyłaby fokus."""
    app_source = APP_PATH.read_text(encoding="utf-8")
    for selector in ui_focus.FOCUS_SELECTORS.values():
        key = re.match(r"\.st-key-([A-Za-z0-9_-]+) ", selector).group(1)
        assert f'key="{key}"' in app_source, selector
