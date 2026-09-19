"""Trasa HTTP serwująca pliki z src/downloads.py — poza kanałem WebSocket.

Podpinana w asgi_app.py przez st.App(routes=[...]). FileResponse streamuje
plik z dysku w kawałkach (bez wczytywania całości do RAM) i sam ustawia
Content-Disposition: attachment z poprawnym kodowaniem nazwy (RFC 5987).
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response

from src import downloads

DOWNLOAD_ROUTE = downloads.DOWNLOAD_URL_PREFIX + "{token}"


async def download_endpoint(request: Request) -> Response:
    link = downloads.lookup(request.path_params["token"])
    if link is None:
        return PlainTextResponse(
            "Link do pobrania wygasł lub jest nieprawidłowy — wróć do aplikacji "
            "i pobierz plik ponownie.",
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )
    return FileResponse(
        link.path,
        filename=link.file_name,
        media_type="application/zip",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
