"""Jednorazowy fokus klawiatury na widżecie — Streamlit nie ma API fokusu.

Dwa kroki: request_focus() zapisuje w SessionState, KTÓRY widżet ma dostać
fokus (zwykle w callbacku), a render_focus_script() — wołane RAZ, na końcu
app.py — wysyła do przeglądarki krótki skrypt i czyści prośbę.

Skrypt idzie przez st.html(unsafe_allow_javascript=True), nie przez
przestarzałe w Streamlit 1.63 st.components.v1.html: działa w głównym
dokumencie strony i wykonuje się przy każdej zmianie treści, więc nonce w
treści daje dokładnie jedno wykonanie na prośbę. Treść jest w całości
generowana tu, bez danych od użytkownika.

Selektory wyłącznie przez klasę .st-key-<key>, którą Streamlit dokłada do
kontenera widżetu z key= — FOCUS_SELECTORS to jedyne miejsce zależne od
struktury DOM Streamlita.
"""

from __future__ import annotations

import json

import streamlit as st

from src.session import SessionState

FOCUS_SELECTORS: dict[str, str] = {
    "download": ".st-key-download_button button",  # przycisk "Pobierz"
    "url": ".st-key-url_input input",  # pole URL
}

# Gdy skrypt rusza, widżet bywa jeszcze nienarysowany albo wyłączony —
# focus() ponawiamy co 50 ms, najwyżej ~1,5 s, potem rezygnujemy po cichu.
_RETRY_INTERVAL_MS = 50
_MAX_ATTEMPTS = 30


def request_focus(state: SessionState, target: str) -> None:
    if target not in FOCUS_SELECTORS:
        raise ValueError(f"Nieznany cel fokusu: {target!r}")
    state.request_focus(target)


def render_focus_script(state: SessionState) -> None:
    target = state.focus_target
    if target is None:
        return
    st.html(
        _focus_script(FOCUS_SELECTORS[target], state.focus_nonce),
        unsafe_allow_javascript=True,
    )
    state.clear_focus_target()


def _focus_script(selector: str, nonce: int) -> str:
    return (
        f"<script>/* focus #{nonce} */(() => {{\n"
        f"  const selector = {json.dumps(selector)};\n"
        "  let attempts = 0;\n"
        "  const timer = setInterval(() => {\n"
        "    attempts += 1;\n"
        "    const el = document.querySelector(selector);\n"
        "    if (el && !el.disabled) {\n"
        "      el.focus({ focusVisible: true });\n"
        "      if (document.activeElement === el) {\n"
        "        clearInterval(timer);\n"
        "        return;\n"
        "      }\n"
        "    }\n"
        f"    if (attempts >= {_MAX_ATTEMPTS}) clearInterval(timer);\n"
        f"  }}, {_RETRY_INTERVAL_MS});\n"
        "})();</script>"
    )
