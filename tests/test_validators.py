import pytest

from src.validators import classify_url, is_mix_playlist_url, validate_url


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


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ",  # mix z wideo
        "https://www.youtube.com/playlist?list=RDdQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDAMVMdQw4w9WgXcQ",  # radio YT Music
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDMM",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDEMabcdef",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDCLAK5uy_abcdef",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDGMEMabcdef",
        "https://youtu.be/dQw4w9WgXcQ?list=RDdQw4w9WgXcQ",
    ],
)
def test_is_mix_playlist_url_true_for_rd_list_ids(url):
    assert is_mix_playlist_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",  # zwykłe wideo
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL3jltwT7zlHiI4lHQh8fdlHGhw4Lfp5Aq",
        "https://www.youtube.com/playlist?list=OLAK5uy_abcdef",  # album YT Music
        "https://www.youtube.com/playlist?list=UUabcdef",
        "https://www.youtube.com/playlist?list=FLabcdef",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=",
        "https://www.youtube.com/watch?v=RDdQw4w9WgXc",  # RD tylko w id wideo, nie listy
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=rdlowercase",  # prefiks jest wielkoliterowy
    ],
)
def test_is_mix_playlist_url_false_for_other_urls(url):
    assert is_mix_playlist_url(url) is False
