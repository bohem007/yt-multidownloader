import pytest

from src.validators import validate_url


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
