"""Walidacja URL — whitelist domen YouTube. Patrz blueprint, Rozdział 8."""

from __future__ import annotations

from typing import Literal
from urllib.parse import parse_qs, urlparse

ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "music.youtube.com",
}


def validate_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc in ALLOWED_HOSTS


_MIX_LIST_ID_PREFIX = "RD"


def is_mix_playlist_url(url: str) -> bool:
    """True dla playlist Mix/Radio generowanych dynamicznie przez YouTube —
    id listy zaczyna się od "RD" (RD<id wideo>, RDAMVM…, RDCLAK…, RDMM…).
    Zwykłe playlisty mają inne prefiksy (PL…, OLAK5uy…, UU…, FL…), więc ich
    to nie łapie. Czysto stringowe, bez zapytania sieciowego. Sama obecność
    RD niczego nie mówi o odczytywalności — patrz classify_url: bez `v=`
    YouTube zwraca "This playlist type is unviewable"."""
    list_id = parse_qs(urlparse(url).query).get("list", [""])[0]
    return list_id.startswith(_MIX_LIST_ID_PREFIX)


def classify_url(url: str) -> Literal["single", "mixed", "playlist_only"]:
    """Klasyfikuje URL po obecności parametrów `v`/`list` w query — czysto
    stringowo, bez żadnego zapytania sieciowego (to robi count_playlist_items
    w engine.py, osobno, bo wymaga odpytania YouTube).

    "single": brak `list` — normalny URL pojedynczego wideo.
    "mixed": jest i wideo, i `list` (typowy URL z autoplaya z listy — bez
        noplaylist=True yt-dlp domyślnie ściągnąłby całą playlistę, mimo że
        użytkownik wkleił link do JEDNEGO konkretnego wideo — to jest bug,
        który ta funkcja pomaga naprawić).
    "playlist_only": jest `list`, brak wideo (np. /playlist?list=...).

    Dla youtu.be wideo jest w PATH, nie w query (inaczej niż youtube.com) —
    bez tego rozróżnienia klasyfikacja fałszywie wykrywałaby "playlist_only"
    dla każdego youtu.be/VIDEO_ID?list=... (poprawny, częsty format linków
    współdzielonych z YouTube), bo tam nie ma parametru `v` w query."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    has_list = bool(query.get("list", [""])[0])

    if parsed.netloc == "youtu.be":
        has_video = bool(parsed.path.strip("/"))
    else:
        has_video = bool(query.get("v", [""])[0])

    if not has_list:
        return "single"
    return "mixed" if has_video else "playlist_only"
