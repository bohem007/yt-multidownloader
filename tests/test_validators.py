import pytest

from src.validators import classify_url, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
    ],
)
def test_validate_url_accepts_known_youtube_hosts(url):
    assert validate_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://www.youtube.com/watch?v=dQw4w9WgXcQ",  # http, nie https
        "https://vimeo.com/12345",  # inny host
        "https://evil.com/youtube.com",  # spoofing hosta w path
        "",  # pusty string
        "not a url",
    ],
)
def test_validate_url_rejects_invalid_urls(url):
    assert validate_url(url) is False


def test_classify_url_single_video_without_list_param():
    assert classify_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "single"


def test_classify_url_mixed_when_both_v_and_list_present():
    """Dokładnie ten przypadek, który powodował ciche pobranie całej
    playlisty zamiast jednego wideo (bug z briefu)."""
    url = "https://www.youtube.com/watch?v=uXlzoi70qUY&list=PL3jltwT7zlHiI4lHQh8fdlHGhw4Lfp5Aq"
    assert classify_url(url) == "mixed"


def test_classify_url_playlist_only_when_list_present_without_v():
    assert classify_url("https://www.youtube.com/playlist?list=PL3jltwT7zlHiI4lHQh8fdlHGhw4Lfp5Aq") == "playlist_only"


def test_classify_url_youtu_be_with_video_in_path_and_list_in_query_is_mixed():
    """youtu.be niesie wideo w PATH, nie w query — bez tego rozróżnienia
    zwykły, częsty link współdzielony z YouTube (youtu.be/ID?list=...)
    fałszywie klasyfikowałby się jako playlist_only."""
    assert classify_url("https://youtu.be/dQw4w9WgXcQ?list=PL3jltwT7zlHiI4lHQh8fdlHGhw4Lfp5Aq") == "mixed"


def test_classify_url_youtu_be_without_list_is_single():
    assert classify_url("https://youtu.be/dQw4w9WgXcQ") == "single"


def test_classify_url_empty_list_param_is_treated_as_single():
    assert classify_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=") == "single"
