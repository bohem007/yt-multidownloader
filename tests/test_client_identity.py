"""Testy src/client_identity.py — hash klienta z X-Forwarded-For (czyste
funkcje, bez Streamlita i bez sieci)."""

from unittest.mock import MagicMock

import pytest

from src.client_identity import (
    UNKNOWN_CLIENT,
    client_ip_from_headers,
    client_ip_hash_from_headers,
    hash_client_ip,
    history_visible_for,
)
from src.config import Settings


class FakeStreamlitHeaders:
    """Odpowiednik StreamlitHeaders: get_all zwraca wszystkie linie nagłówka."""

    def __init__(self, *forwarded_lines: str) -> None:
        self._lines = list(forwarded_lines)

    def get_all(self, name: str) -> list[str]:
        return list(self._lines) if name.lower() == "x-forwarded-for" else []


def test_same_address_gives_same_hash():
    a = client_ip_hash_from_headers({"X-Forwarded-For": "203.0.113.7"}, "s")
    b = client_ip_hash_from_headers({"X-Forwarded-For": "203.0.113.7"}, "s")
    assert a == b


def test_different_addresses_give_different_hashes():
    a = client_ip_hash_from_headers({"X-Forwarded-For": "203.0.113.7"}, "s")
    b = client_ip_hash_from_headers({"X-Forwarded-For": "203.0.113.8"}, "s")
    assert a != b


def test_different_secret_gives_different_hash():
    headers = {"X-Forwarded-For": "203.0.113.7"}
    assert client_ip_hash_from_headers(headers, "one") != client_ip_hash_from_headers(headers, "two")


def test_hash_is_hex_of_bounded_length_and_never_contains_the_raw_address():
    result = client_ip_hash_from_headers({"X-Forwarded-For": "203.0.113.7"}, "s")
    assert len(result) == 32
    assert int(result, 16) >= 0
    assert "203.0.113.7" not in result


def test_hash_works_with_empty_secret():
    assert len(hash_client_ip("203.0.113.7", "")) == 32


def test_missing_header_falls_back_to_unknown():
    assert client_ip_hash_from_headers({}, "s") == UNKNOWN_CLIENT
    assert client_ip_hash_from_headers(FakeStreamlitHeaders(), "s") == UNKNOWN_CLIENT


def test_takes_first_address_of_a_forwarded_chain():
    chain = {"X-Forwarded-For": "203.0.113.7, 10.0.0.1, 10.0.0.2"}
    assert client_ip_from_headers(chain) == "203.0.113.7"
    assert client_ip_hash_from_headers(chain, "s") == client_ip_hash_from_headers(
        {"X-Forwarded-For": "203.0.113.7"}, "s"
    )


def test_repeated_header_lines_are_joined_in_order():
    headers = FakeStreamlitHeaders("203.0.113.7", "10.0.0.1")
    assert client_ip_from_headers(headers) == "203.0.113.7"


def test_skips_garbage_and_takes_first_valid_address():
    assert client_ip_from_headers({"X-Forwarded-For": "garbage, , 203.0.113.9, 10.0.0.1"}) == "203.0.113.9"


@pytest.mark.parametrize("value", ["", "unknown", "203.0.113.7:8080", "999.1.1.1", "   "])
def test_only_garbage_falls_back_to_unknown(value):
    assert client_ip_hash_from_headers({"X-Forwarded-For": value}, "s") == UNKNOWN_CLIENT


def test_ipv6_is_canonicalised_so_equivalent_forms_share_a_hash():
    a = client_ip_hash_from_headers({"X-Forwarded-For": "2001:db8::1"}, "s")
    b = client_ip_hash_from_headers({"X-Forwarded-For": "2001:0db8:0000:0000:0000:0000:0000:0001"}, "s")
    assert a == b != UNKNOWN_CLIENT


def test_magicmock_headers_from_apptest_do_not_influence_the_hash():
    assert client_ip_hash_from_headers(MagicMock(), "s") == UNKNOWN_CLIENT


def test_magicmock_inside_header_values_is_ignored():
    headers = MagicMock()
    headers.get_all.return_value = [MagicMock(), "203.0.113.7"]
    assert client_ip_from_headers(headers) == "203.0.113.7"


def test_headers_raising_an_error_fall_back_to_unknown():
    headers = MagicMock()
    headers.get_all.side_effect = RuntimeError("boom")
    assert client_ip_hash_from_headers(headers, "s") == UNKNOWN_CLIENT


def test_unknown_client_history_visible_only_with_explicit_local_environment():
    explicit_local = Settings.from_env({"ENVIRONMENT": "local"})
    implicit_default = Settings.from_env({})
    production = Settings.from_env({"ENVIRONMENT": "production"})

    assert history_visible_for(UNKNOWN_CLIENT, explicit_local) is True
    assert history_visible_for(UNKNOWN_CLIENT, implicit_default) is False
    assert history_visible_for(UNKNOWN_CLIENT, production) is False


def test_known_client_history_visible_in_every_environment():
    assert history_visible_for("a" * 32, Settings.from_env({})) is True
    assert history_visible_for("a" * 32, Settings.from_env({"ENVIRONMENT": "production"})) is True
