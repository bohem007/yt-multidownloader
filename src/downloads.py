"""Krótkotrwałe linki do pobrania KAŻDEGO pliku wynikowego z dysku —
pojedynczego (wideo, audio, napisy, TXT) i ZIP-a playlisty.

Plik ląduje w dedykowanym katalogu na dysku, a użytkownik dostaje zwykły
link HTTP GET (trasa w src/download_routes.py), który przeglądarka pobiera
strumieniowo, poza kanałem WebSocket. Nie `st.download_button`: z ZIP-ami
rzędu 1 GB w `data=` zawisał bezterminowo, a w Streamlit 1.63 przy każdym
zamontowaniu wysyła w tle pełny GET pliku z nieczytaną odpowiedzią — duże
wyniki wyczerpywały pulę połączeń przeglądarki (patrz docs/HISTORIA.md).

To świadome odstępstwo od "plik wczytany do RAM i natychmiast usunięty"
(CLAUDE.md, Warstwa 10): plik żyje na dysku do `DOWNLOAD_LINK_TTL_MINUTES`,
niezależnie od sesji ("Nowy URL", zmiana trybu i nowy job go nie kasują).
Wcześniej znika tylko ZIP tury playlisty, zastąpiony kolejną turą (release()).

Bezpieczeństwo: link to nieodgadywalny token (256 bit) — sam token jest
jedynym "uwierzytelnieniem", więc nigdy nie używamy tu job_id ani nazw plików.
Trasa HTTP nie przyjmuje żadnej ścieżki od klienta, tylko token szukany w
rejestrze poniżej.
"""

from __future__ import annotations

import secrets
import shutil
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from src.config import settings

_LINKS_DIRNAME = "yt-multidownloader-links"
DOWNLOAD_URL_PREFIX = "/api/download/"


@dataclass(frozen=True)
class DownloadLink:
    token: str
    path: Path
    file_name: str
    expires_at: float
    # True od chwili, gdy trasa zaczęła wysyłać plik (mark_fetched) —
    # "przeglądarka rozpoczęła pobieranie"; anulowania okna zapisu nie widać.
    fetched: bool = False


_lock = threading.Lock()
_links: dict[str, DownloadLink] = {}
_route_enabled = False


def set_route_enabled(enabled: bool) -> None:
    """asgi_app.py ustawia to na True, gdy trasa HTTP jest realnie podpięta.
    Bez tego (np. `streamlit run app.py` wprost) link do pobrania byłby
    martwy — app.py sprawdza flagę i pokazuje wyraźny komunikat zamiast tego."""
    global _route_enabled
    _route_enabled = enabled


def is_route_enabled() -> bool:
    return _route_enabled


def download_url(token: str) -> str:
    return f"{DOWNLOAD_URL_PREFIX}{token}"


def _root() -> Path:
    return Path(settings.storage_base_dir) / _LINKS_DIRNAME


def _ttl_seconds() -> float:
    return settings.download_link_ttl_minutes * 60


def publish(source: Path, file_name: str) -> DownloadLink:
    """Przenosi `source` do katalogu linków i rejestruje token.

    Przeniesienie (nie kopia) — ZIP znika z katalogu joba, więc wołający może
    od razu zrobić storage.cleanup() reszty (pojedyncze pliki mediów), bez
    podwójnego zajmowania dysku."""
    sweep_expired()

    token = secrets.token_urlsafe(32)
    directory = _root() / token
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"payload{source.suffix}"
    shutil.move(str(source), target)

    link = DownloadLink(
        token=token,
        path=target,
        file_name=file_name,
        expires_at=time.monotonic() + _ttl_seconds(),
    )
    with _lock:
        _links[token] = link
    return link


def lookup(token: str) -> DownloadLink | None:
    """Zwraca link, jeśli token istnieje, nie wygasł i plik wciąż jest na dysku.

    Woła ją także trasa async (download_routes.py): wygasły token znika z
    rejestru od razu, ale plik kasuje wątek w tle — rmtree w pętli zdarzeń
    wstrzymałby na czas operacji dyskowej wszystkie żądania serwera."""
    with _lock:
        link = _links.get(token)
    if link is None:
        return None
    if time.monotonic() >= link.expires_at:
        _expire_in_background(token)
        return None
    if not link.path.exists():
        return None
    return link


def mark_fetched(token: str) -> None:
    """Odnotowuje, że plik tokenu zaczął być pobierany — źródło prawdy dla
    sygnału "plik niezapisany" w UI. Nieznany token nie jest błędem."""
    with _lock:
        link = _links.get(token)
        if link is not None and not link.fetched:
            _links[token] = replace(link, fetched=True)


def release(token: str) -> None:
    """Usuwa token i jego plik. Nieznany token nie jest błędem."""
    with _lock:
        link = _links.pop(token, None)
    if link is not None:
        _delete_link_directory(link.path.parent)


def _expire_in_background(token: str) -> None:
    with _lock:
        link = _links.pop(token, None)
    if link is None:
        return
    threading.Thread(
        target=_delete_link_directory,
        args=(link.path.parent,),
        name="downloads-expired-cleanup",
        daemon=True,
    ).start()


def _delete_link_directory(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)


def sweep_expired() -> None:
    """Sprząta wygasłe linki oraz osierocone katalogi (np. po restarcie
    procesu, albo gdy rmtree nie powiódł się na Windows przy trwającym
    pobieraniu). Wołane przy każdym publish() i przy starcie serwera —
    nie ma osobnego wątku sprzątającego, więc na nieaktywnym serwerze
    wygasłe pliki czekają do następnej aktywności."""
    now = time.monotonic()
    with _lock:
        expired = [token for token, link in _links.items() if now >= link.expires_at]
    for token in expired:
        release(token)

    root = _root()
    if not root.is_dir():
        return
    with _lock:
        known = set(_links)
    cutoff = time.time() - _ttl_seconds()
    for entry in root.iterdir():
        if entry.name in known or not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def purge_all() -> None:
    """Usuwa WSZYSTKIE linki i pliki — start/stop procesu serwera."""
    with _lock:
        _links.clear()
    shutil.rmtree(_root(), ignore_errors=True)
