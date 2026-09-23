"""Trasa HTTP serwująca pliki z src/downloads.py — poza kanałem WebSocket.

Podpinana w asgi_app.py przez st.App(routes=[...]). FileResponse streamuje
plik z dysku w kawałkach (bez wczytywania całości do RAM) i sam ustawia
Content-Disposition: attachment z poprawnym kodowaniem nazwy (RFC 5987).
Content-Type wynika z rozszerzenia pliku (media_type_for).
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response

from src import downloads

DOWNLOAD_ROUTE = downloads.DOWNLOAD_URL_PREFIX + "{token}"

# Własna instancja, nie globalny rejestr `mimetypes`: ten na Windows dociąga
# typy z rejestru systemu (inne na każdej maszynie), a ponowne mimetypes.init()
# w dowolnej bibliotece skasowałoby nasze wpisy. Wbudowana tabela Pythona nie
# zna .flac/.m4a — bez nich wychodziłoby application/octet-stream, które gzip
# serwera (Starlette pomija tylko audio/*, video/*, archiwa…) kompresowałby
# mimo że to już skompresowane audio.
_MEDIA_TYPES = mimetypes.MimeTypes()
_MEDIA_TYPES.add_type("audio/flac", ".flac")
_MEDIA_TYPES.add_type("audio/mp4", ".m4a")


def media_type_for(path: Path) -> str:
    """Content-Type pliku wynikowego po jego rozszerzeniu — jedno miejsce,
    z którego korzysta trasa pobierania."""
    media_type, _encoding = _MEDIA_TYPES.guess_type(path.name)
    return media_type or "application/octet-stream"


async def download_endpoint(request: Request) -> Response:
    link = downloads.lookup(request.path_params["token"])
    if link is None:
        return PlainTextResponse(
            "Link do pobrania wygasł lub jest nieprawidłowy — wróć do aplikacji "
            "i pobierz plik ponownie.",
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )
    if request.method == "GET":
        # Odpowiedź z plikiem zaraz rusza — to jest moment "zapisano" dla UI
        # (HEAD niczego nie pobiera, więc go nie oznacza).
        downloads.mark_fetched(link.token)
    return FileResponse(
        link.path,
        filename=link.file_name,
        media_type=media_type_for(link.path),
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
