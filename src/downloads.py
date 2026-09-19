"""Krótkotrwałe linki do pobrania dużych plików (ZIP playlisty) z dysku.

`st.download_button(data=<bajty>)` przepuszcza cały plik przez RAM procesu i
kanał Streamlita — dla ZIP-ów rzędu 1 GB zawisał bezterminowo (patrz historia
buga "Zapisz plik"). Zamiast tego plik ląduje w dedykowanym katalogu na dysku,
a użytkownik dostaje zwykły link HTTP GET (trasa w src/download_routes.py),
który przeglądarka pobiera strumieniowo, poza kanałem WebSocket.

To świadome odstępstwo od "plik wczytany do RAM i natychmiast usunięty"
(CLAUDE.md, Warstwa 10): plik żyje na dysku do `DOWNLOAD_LINK_TTL_MINUTES`,
albo do zastąpienia go nowym wynikiem / resetu sesji (release()).

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
from dataclasses import dataclass
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
    """Zwraca link, jeśli token istnieje, nie wygasł i plik wciąż jest na dysku."""
    with _lock:
        link = _links.get(token)
    if link is None:
        return None
    if time.monotonic() >= link.expires_at:
        release(token)
        return None
    if not link.path.exists():
        return None
    return link


def release(token: str) -> None:
    """Usuwa token i jego plik. Nieznany token nie jest błędem."""
    with _lock:
        link = _links.pop(token, None)
    if link is not None:
        shutil.rmtree(link.path.parent, ignore_errors=True)


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
