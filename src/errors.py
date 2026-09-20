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


class ItemDownloadTimeoutError(Exception):
    """Pobranie JEDNEJ pozycji (submit()/pojedynczy element playlisty)
    przekroczyło twardy limit ITEM_DOWNLOAD_TIMEOUT_SECONDS — podnoszone
    przez engine.py::DownloadEngine._download_one jako defense-in-depth,
    niezależny od retries/fragment_retries/extractor_retries skonfigurowanych
    w yt-dlp (patrz _build_ydl_opts). Zaobserwowane manualnie 2026-09-20:
    mimo skonfigurowanych retries, pojedyncza pozycja potrafiła zapętlić się
    na serii błędów 403/connection timeout praktycznie bez końca."""


class InvalidPlaylistSelectionError(Exception):
    """Ręcznie wskazane numery pozycji (playlist_scope="selected") poza
    zakresem 1..liczba pozycji playlisty — podnoszone przez engine.py jako
    backstop PO walidacji app.py (Warstwa architektoniczna: engine.py
    sprawdza limity przed pobraniem niezależnie od UI, na wypadek gdyby
    playlista zmieniła długość między sondą app.py a właściwym pobraniem)."""


_BOT_CHECK_MARKERS = ("confirm you're not a bot", "sign in to confirm", "not a bot")
# Osobny, jednoznaczny przypadek od _BOT_CHECK_MARKERS: "Sign in to confirm
# your age" (brak/nieważne cookies — problem DO ROZWIĄZANIA przez wgranie
# cookies.txt) zawsze zawiera "sign in to confirm", więc trafia w gałąź
# bot-check WCZEŚNIEJ (sprawdzaną pierwszą) i nigdy nie dociera tutaj.
# "Sorry, this content is age-restricted" pojawia się natomiast NAWET z
# prawidłowymi, świeżymi cookies — NIE jest to problem konta Google
# użytkownika (potwierdzone: to samo konto ogląda tę treść normalnie w
# przeglądarce). To udokumentowane, aktualne ograniczenie yt-dlp
# (github.com/yt-dlp/yt-dlp/issues/17619): dla zalogowanych sesji YouTube
# coraz częściej wymaga środowiska JS (Deno/Node) do rozwiązania wyzwań
# szyfrujących, którego świadomie nie dodajemy (zbyt duży wzrost zakresu) —
# stąd komunikat opisuje to jako ograniczenie narzędzia, nie instrukcję do
# naprawienia przez użytkownika.
_AGE_RESTRICTED_MARKERS = ("content is age-restricted",)
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

    if isinstance(exc, ItemDownloadTimeoutError):
        return (
            f"Przekroczono limit czasu ({settings.item_download_timeout_seconds}s) "
            "pobierania tego materiału — YouTube mógł tymczasowo ograniczać "
            "przepustowość dla tego strumienia. Spróbuj ponownie później."
        )

    if isinstance(exc, InvalidPlaylistSelectionError):
        return (
            "Wybrane numery pozycji playlisty są nieprawidłowe — sprawdź, "
            "czy mieszczą się w zakresie pozycji tej playlisty, i spróbuj ponownie."
        )

    if isinstance(exc, DownloadError):
        message = str(exc).lower()

        if any(marker in message for marker in _BOT_CHECK_MARKERS):
            return (
                "YouTube wymaga potwierdzenia, że nie jesteś botem. Wgraj plik "
                "cookies.txt (wyeksportowany z zalogowanej sesji przeglądarki) "
                "i spróbuj ponownie."
            )

        if any(marker in message for marker in _AGE_RESTRICTED_MARKERS):
            return (
                "YouTube blokuje pobranie tego materiału pomimo prawidłowych "
                "cookies — to znane, aktualne ograniczenie techniczne yt-dlp "
                "dla części treści z ograniczeniem wiekowym, niezwiązane "
                "z ustawieniami Twojego konta Google. Aplikacja obecnie nie "
                "obsługuje obejścia tego ograniczenia."
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
