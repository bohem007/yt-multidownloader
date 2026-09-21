"""Tożsamość klienta dla historii zadań (i później rate limitingu).

Adres bierzemy WYŁĄCZNIE z nagłówka `X-Forwarded-For` (pierwszy poprawny
adres z listy). `st.context.ip_address` celowo NIE jest używane: za proxy HF
zwraca adres wewnętrzny proxy, wspólny dla wszystkich użytkowników, więc
wszyscy dzieliliby hash i historię. Brak nagłówka → `UNKNOWN_CLIENT`.

Do bazy trafia tylko HMAC-SHA256(IP_HASH_SECRET, adres) — nigdy surowy adres.
Pierwszy element `X-Forwarded-For` może podstawić klient (spoofing); właściwy
indeks w łańcuchu proxy ustalimy dopiero na Space (pozycja 10, rate limiting).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
from typing import Any

from src.config import Settings, settings

UNKNOWN_CLIENT = "unknown"
FORWARDED_FOR_HEADER = "X-Forwarded-For"
_HASH_HEX_LENGTH = 32  # 128 bitów — kolumna client_ip_hash to TEXT bez limitu


def _header_values(headers: Any, name: str) -> list[str]:
    """Wszystkie wartości nagłówka jako list[str]; cokolwiek innego (np.
    MagicMock z AppTest, brak nagłówka, wyjątek) daje pustą listę."""
    try:
        get_all = getattr(headers, "get_all", None)
        raw = get_all(name) if callable(get_all) else headers.get(name)
    except Exception:
        return []
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return [value for value in raw if isinstance(value, str)]


def client_ip_from_headers(headers: Any) -> str | None:
    """Pierwszy element `X-Forwarded-For` będący poprawnym adresem IP
    (kanoniczna postać), albo None."""
    joined = ",".join(_header_values(headers, FORWARDED_FOR_HEADER))
    for candidate in joined.split(","):
        try:
            return str(ipaddress.ip_address(candidate.strip()))
        except ValueError:
            continue
    return None


def hash_client_ip(ip: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256).hexdigest()[
        :_HASH_HEX_LENGTH
    ]


def client_ip_hash_from_headers(headers: Any, secret: str) -> str:
    ip = client_ip_from_headers(headers)
    if ip is None:
        return UNKNOWN_CLIENT
    return hash_client_ip(ip, secret)


def current_client_ip_hash() -> str:
    """Hash klienta bieżącej sesji Streamlit (bez kontekstu → UNKNOWN_CLIENT)."""
    import streamlit as st

    try:
        headers = st.context.headers
    except Exception:
        return UNKNOWN_CLIENT
    return client_ip_hash_from_headers(headers, settings.ip_hash_secret)


def history_visible_for(client_hash: str, current: Settings | None = None) -> bool:
    """Fail-closed: historię sesji o nieznanym adresie pokazujemy tylko przy
    JAWNIE ustawionym ENVIRONMENT=local — inaczej (w tym brak zmiennej, którego
    domyślną wartością jest "local") wszyscy "nieznani" widzieliby nawzajem
    swoje pobrania."""
    current = current or settings
    if client_hash != UNKNOWN_CLIENT:
        return True
    return current.environment == "local" and current.environment_explicit
