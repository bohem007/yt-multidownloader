"""Mapowanie wyjątków pobierania na czytelne komunikaty. Patrz Warstwa 9 spec.

Wyjątki domenowe (InvalidUrlError, PlaylistTooLargeError, FileTooLargeError)
są podnoszone przez validators/storage/engine, a `map_download_error`
tłumaczy je — razem z surowym `yt_dlp.utils.DownloadError` — na komunikat
w języku polskim, nigdy na surowy traceback.
"""

from __future__ import annotations

from yt_dlp.utils import DownloadError

from src.config import settings


class InvalidUrlError(Exception):
    """URL nie przeszedł walidacji (validators.validate_url)."""


class PlaylistTooLargeError(Exception):
    """Playlista przekracza MAX_PLAYLIST_ITEMS — podnoszone przez engine.py."""


class FileTooLargeError(Exception):
    """Wynikowy plik/katalog przekracza MAX_FILE_SIZE_MB — podnoszone przez storage.py."""


_BOT_CHECK_MARKERS = ("confirm you're not a bot", "sign in to confirm", "not a bot")
_SUBTITLE_MARKERS = ("subtitle", "subtitles")


def map_download_error(exc: Exception) -> str:
    """Zwraca czytelny komunikat PL dla użytkownika — nigdy surowy traceback."""

    if isinstance(exc, InvalidUrlError):
        return (
            "Niepoprawny adres URL — akceptowane są tylko linki YouTube "
            "(youtube.com, youtu.be, music.youtube.com) po HTTPS."
        )

    if isinstance(exc, PlaylistTooLargeError):
        return (
            f"Playlista zawiera więcej pozycji niż dopuszczalny limit "
            f"({settings.max_playlist_items}). Wybierz krótszą playlistę."
        )

    if isinstance(exc, FileTooLargeError):
        return (
            f"Wynikowy plik przekracza limit {settings.max_file_size_mb} MB "
            f"darmowego tieru. Wybierz krótszy materiał lub niższą jakość."
        )

    if isinstance(exc, DownloadError):
        message = str(exc).lower()

        if any(marker in message for marker in _BOT_CHECK_MARKERS):
            return (
                "YouTube wymaga potwierdzenia, że nie jesteś botem. Wgraj plik "
                "cookies.txt (wyeksportowany z zalogowanej sesji przeglądarki) "
                "i spróbuj ponownie."
            )

        if any(marker in message for marker in _SUBTITLE_MARKERS):
            return (
                "Brak napisów w żądanym języku dla tego materiału — spróbuj "
                "innego języka lub sprawdź dostępne napisy."
            )

        return (
            "Nie udało się pobrać materiału z YouTube. Sprawdź adres URL "
            "i spróbuj ponownie później."
        )

    return "Wystąpił nieoczekiwany błąd podczas pobierania. Spróbuj ponownie."
