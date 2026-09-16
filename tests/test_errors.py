from __future__ import annotations

from yt_dlp.utils import DownloadError

from src.errors import map_download_error


def _download_error(message: str) -> DownloadError:
    # DownloadError.__init__ oczekuje wiadomości jako pierwszego argumentu —
    # str(exc) w map_download_error odczytuje ją z powrotem.
    return DownloadError(message)


def test_sign_in_to_confirm_age_maps_to_missing_cookies_message():
    """Regresja: 'Sign in to confirm your age' (brak/nieważne cookies) musi
    NADAL trafiać w gałąź bot-check (wgraj cookies.txt) — ten test istnieje
    głównie po to, żeby przyszła zmiana w gałęzi age-restricted nie zaczęła
    przypadkiem łapać też tego przypadku."""
    message = map_download_error(
        _download_error("ERROR: [youtube] abc123: Sign in to confirm your age")
    )
    assert "cookies.txt" in message
    assert "konto Google" not in message


def test_content_is_age_restricted_maps_to_known_tool_limitation_message():
    """Gałąź: 'Sorry, this content is age-restricted' pojawia się NAWET
    z prawidłowymi, świeżymi cookies — potwierdzone, że to NIE jest problem
    weryfikacji wieku konta Google użytkownika (to samo konto ogląda treść
    normalnie w przeglądarce), a znane, aktualne ograniczenie techniczne
    yt-dlp (github.com/yt-dlp/yt-dlp/issues/17619). Komunikat NIE może więc
    sugerować ani wgrania cookies.txt, ani sprawdzenia ustawień konta Google —
    oba założenia byłyby nieprawdziwe i myślące użytkownika na złą ścieżkę."""
    message = map_download_error(
        _download_error("ERROR: [youtube] abc123: Sorry, this content is age-restricted.")
    )
    assert "cookies.txt" not in message
    assert "myaccount.google.com" not in message
    assert "URL" not in message
    assert "ograniczenie" in message
    assert "yt-dlp" in message


def test_the_two_age_related_messages_are_distinguishable():
    """Rdzeń wymagania: te dwa komunikaty muszą pozostać różne — inaczej
    użytkownik dostanie tę samą, myloną instrukcję dla dwóch różnych,
    nie-zamiennych przyczyn (brak cookies vs. brak weryfikacji wieku konta)."""
    sign_in_message = map_download_error(
        _download_error("ERROR: [youtube] abc123: Sign in to confirm your age")
    )
    age_restricted_message = map_download_error(
        _download_error("ERROR: [youtube] abc123: Sorry, this content is age-restricted.")
    )
    assert sign_in_message != age_restricted_message
