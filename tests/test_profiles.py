import pytest

from src.profiles import AUDIO_FLAC, AUDIO_MP3, VIDEO, get_profile


def test_get_profile_video_returns_video_profile():
    profile = get_profile("video", "mp4")
    assert profile.selector == VIDEO.selector
    assert profile.postprocessors == VIDEO.postprocessors


def test_get_profile_audio_mp3():
    profile = get_profile("audio", "mp3")
    assert profile is AUDIO_MP3
    assert profile.postprocessors[0]["preferredcodec"] == "mp3"


def test_get_profile_audio_flac():
    profile = get_profile("audio", "flac")
    assert profile is AUDIO_FLAC
    assert profile.postprocessors[0]["preferredcodec"] == "flac"


def test_get_profile_subtitle_uses_requested_format():
    profile = get_profile("subtitle", "vtt")
    assert profile.extra_opts["skip_download"] is True
    assert profile.extra_opts["subtitlesformat"] == "vtt"


def test_get_profile_subtitle_defaults_to_srt_for_unknown_format():
    profile = get_profile("subtitle", "txt")
    assert profile.extra_opts["subtitlesformat"] == "srt"


def test_get_profile_playlist_inherits_audio_profile_and_adds_playlist_opts():
    profile = get_profile("playlist", "mp3")
    assert profile.selector == AUDIO_MP3.selector
    assert profile.postprocessors == AUDIO_MP3.postprocessors
    assert profile.extra_opts["ignoreerrors"] is True
    assert "%(playlist_index)s" in profile.extra_opts["outtmpl_template"]


def test_get_profile_playlist_inherits_video_profile_for_mp4():
    profile = get_profile("playlist", "mp4")
    assert profile.selector == VIDEO.selector
    assert profile.postprocessors == VIDEO.postprocessors
    assert profile.extra_opts["ignoreerrors"] is True


def test_get_profile_audio_mp3_with_custom_bitrate_overrides_preferredquality():
    profile = get_profile("audio", "mp3", audio_bitrate_kbps=192)
    assert profile.selector == AUDIO_MP3.selector
    assert profile.postprocessors[0]["preferredcodec"] == "mp3"
    assert profile.postprocessors[0]["preferredquality"] == "192"


def test_get_profile_audio_mp3_without_bitrate_keeps_default_vbr():
    profile = get_profile("audio", "mp3", audio_bitrate_kbps=None)
    assert profile is AUDIO_MP3
    assert profile.postprocessors[0]["preferredquality"] == "0"


def test_get_profile_transcript_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        get_profile("transcript", "txt")


def test_get_profile_unknown_mode_raises_value_error():
    with pytest.raises(ValueError):
        get_profile("bogus", "mp4")
