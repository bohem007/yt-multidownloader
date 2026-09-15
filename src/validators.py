"""Walidacja URL — whitelist domen YouTube. Patrz blueprint, Rozdział 8."""

from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "music.youtube.com",
}


def validate_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc in ALLOWED_HOSTS
