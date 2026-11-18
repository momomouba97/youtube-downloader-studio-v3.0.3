import os
import pytest
from pathlib import Path

from app import downloader as dl
from app.resources import tool_path
from tests.helpers import LocalServer, make_test_video

needs_ffmpeg = pytest.mark.skipif(not tool_path("ffmpeg"), reason="ffmpeg not available")


def test_format_selector_caps_quality_and_prefers_h264():
    s = dl.format_selector(1080, "mp4")
    assert "height<=1080" in s and "vcodec^=avc1" in s and "height<=2160" not in s
    assert dl.format_selector(720, "mkv").startswith("bv*[height<=720]")


@pytest.mark.parametrize("t,ok", [
    ("%(title)s [%(id)s].%(ext)s", True), ("%(id)s.%(ext)s", True),
    ("../%(title)s.%(ext)s", False), ("a/b.%(ext)s", False), ("noext", False), ("", False),
])
def test_template_validation(t, ok):
    if ok:
        assert dl.validate_template(t) == t
    else:
        with pytest.raises(ValueError):
            dl.validate_template(t)


def test_safe_dirname():
    assert dl.safe_dirname('a/b:c*"d') == "a_b_c__d"
    assert dl.safe_dirname("..") == "Channel" and dl.safe_dirname("") == "Channel"


def test_classify_error():
    assert dl.classify_error("ERROR: [youtube] x: Private video. Sign in")[1] is False
    assert dl.classify_error("Video unavailable")[1] is False
    assert dl.classify_error("ERROR: ffmpeg not found")[1] is False
    msg, retry = dl.classify_error("HTTP Error 503: Service Unavailable")
    assert retry is True and "503" in msg


@pytest.mark.parametrize("bad", [
    "file:///etc/passwd", "ftp://youtube.com/x", "javascript:alert(1)", "http://127.0.0.1:8080/x.mp4",
    "https://localhost/x", "http://192.168.1.5/x.mp4", "https://user:pw@www.youtube.com/watch?v=x", "not a url"])
def test_unsafe_urls_are_always_rejected(tmp_path, bad):
    for allow in (False, True):
        with pytest.raises(dl.DownloadError) as e:
            dl.download_video(bad, tmp_path, profile=dl.Profile(allow_other_sites=allow))
        assert e.value.retryable is False


def test_unknown_sites_need_permission(tmp_path):
    with pytest.raises(dl.DownloadError) as e:                       # legacy default: known platforms only
        dl.download_video("https://evil.example.com/watch?v=x", tmp_path)
    assert "not in the supported list" in str(e.value)
    with pytest.raises(dl.DownloadError) as e:
        dl.download_video("https://evil.example.com/v", tmp_path, profile=dl.Profile(allow_other_sites=False))
    assert e.value.retryable is False


def test_bad_template_is_not_retryable(tmp_path):
    with pytest.raises(dl.DownloadError) as e:
        dl.download_video("https://www.youtube.com/watch?v=aaaaaaaaaaa", tmp_path, template="../x.%(ext)s")
    assert e.value.retryable is False


@needs_ffmpeg
def test_real_ytdlp_download_progress_and_output(tmp_path):
    src = tmp_path / "srv"; src.mkdir()
    assert make_test_video(src / "clip.mp4")
    events = []
    with LocalServer(src) as srv:
        res = dl.download_video(srv.url("clip.mp4"), tmp_path / "out", template="%(id)s.%(ext)s",
                                max_quality=1080, fmt="mp4", on_progress=events.append, allow_any_host=True)
    assert os.path.isfile(res["file_path"]) and res["size"] > 0
    assert Path(res["file_path"]).parent == tmp_path / "out"
    assert events[0]["status"] == "preparing"
    assert any(e["status"] == "downloading" for e in events)
    assert all(0 <= e["percent"] <= 100 for e in events)


@needs_ffmpeg
def test_existing_file_is_not_downloaded_twice(tmp_path):
    src = tmp_path / "srv"; src.mkdir()
    make_test_video(src / "clip.mp4")
    with LocalServer(src) as srv:
        a = dl.download_video(srv.url("clip.mp4"), tmp_path / "o", template="%(id)s.%(ext)s", allow_any_host=True)
        mtime = os.path.getmtime(a["file_path"])
        b = dl.download_video(srv.url("clip.mp4"), tmp_path / "o", template="%(id)s.%(ext)s", allow_any_host=True)
    assert a["file_path"] == b["file_path"] and os.path.getmtime(b["file_path"]) == mtime


@needs_ffmpeg
@pytest.mark.parametrize("reason", ["cancel", "pause"])
def test_abort_mid_download(tmp_path, reason):
    src = tmp_path / "srv"; src.mkdir()
    (src / "big.mp4").write_bytes(os.urandom(3_000_000))   # content is irrelevant for the abort test
    flag = {"v": None}

    def on_progress(d):
        if d["status"] == "downloading" and d["downloaded"] > 0:
            flag["v"] = reason

    with LocalServer(src, slow=0.02) as srv:
        with pytest.raises(dl.DownloadAborted) as e:
            dl.download_video(srv.url("big.mp4"), tmp_path / "o", template="%(id)s.%(ext)s",
                              on_progress=on_progress, should_abort=lambda: flag["v"], allow_any_host=True)
    assert e.value.reason == reason


@needs_ffmpeg
def test_server_error_is_reported_as_retryable_error(tmp_path):
    src = tmp_path / "srv"; src.mkdir()
    with LocalServer(src) as srv:
        with pytest.raises(dl.DownloadError):
            dl.download_video(srv.url("missing.mp4"), tmp_path / "o", allow_any_host=True)


# ---------------------------------------------------------------- Phase 3: profiles, audio, speed options
import subprocess


def _probe(path, entry="stream=codec_name,codec_type"):
    ff = tool_path("ffprobe")
    out = subprocess.run([ff, "-v", "error", "-show_entries", entry, "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.split()
    return out


def test_profile_validation_and_labels():
    p = dl.Profile.from_dict({"kind": "weird", "container": "avi", "max_height": 999, "audio_format": "wma",
                              "audio_quality": 99999, "fragments": 0, "limit_rate_kbps": -5, "cookies_file": 7})
    assert (p.kind, p.container, p.max_height, p.audio_format) == ("video", "mp4", 1080, "mp3")
    assert p.audio_quality == 320 and p.fragments == 1 and p.limit_rate_kbps == 0
    ok = dl.Profile.from_dict({"kind": "audio", "audio_format": "flac", "max_height": 0, "container": "webm"})
    assert ok.label() == "FLAC" and ok.extension == "flac"
    assert dl.Profile(kind="audio", audio_format="mp3", audio_quality=256).label() == "MP3 256k"
    assert dl.Profile(max_height=0).label() == "MP4 best" and dl.Profile(max_height=720).label() == "MP4 ≤720p"
    assert dl.Profile.from_dict(dl.Profile(kind="audio", audio_format="opus").to_dict()).audio_format == "opus"


def test_selectors_for_every_quality_and_container():
    assert "height" not in dl.format_selector(0, "mkv") .replace("[height<=?", "")          # "best" has no cap
    assert dl.format_selector(0, "mp4").endswith("/b")
    assert "height<=2160" in dl.format_selector(2160, "mp4") and "vcodec^=avc1" in dl.format_selector(2160, "mp4")
    assert dl.format_selector(720, "webm").startswith("bv*[height<=720][ext=webm]+ba[ext=webm]")
    assert dl.audio_selector("m4a").startswith("ba[ext=m4a]") and dl.audio_selector("mp3") == "ba/b"


def test_build_ydl_opts_audio_video_speed_cookies(tmp_path):
    ck = tmp_path / "cookies.txt"; ck.write_text("# Netscape HTTP Cookie File\n")
    a = dl.build_ydl_opts(dl.Profile(kind="audio", audio_format="mp3", audio_quality=256, limit_rate_kbps=500,
                                     fragments=6, cookies_file=str(ck)), tmp_path, "%(id)s.%(ext)s")
    assert a["postprocessors"][0] == {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "256"}
    assert a["ratelimit"] == 500 * 1024 and a["concurrent_fragment_downloads"] == 6 and a["cookiefile"] == str(ck)
    assert "merge_output_format" not in a and a["postprocessors"][1]["key"] == "FFmpegMetadata"
    f = dl.build_ydl_opts(dl.Profile(kind="audio", audio_format="flac"), tmp_path, "%(id)s.%(ext)s")
    assert "preferredquality" not in f["postprocessors"][0]                                   # lossless: no bitrate
    v = dl.build_ydl_opts(dl.Profile(container="mkv", max_height=0, embed_metadata=False, embed_thumbnail=True),
                          tmp_path, "%(id)s.%(ext)s")
    assert v["merge_output_format"] == "mkv" and v["writethumbnail"] is True
    assert [p["key"] for p in v["postprocessors"]] == ["FFmpegThumbnailsConvertor", "EmbedThumbnail"]
    assert "ratelimit" not in v and "cookiefile" not in v
    w = dl.build_ydl_opts(dl.Profile(container="webm", embed_thumbnail=True), tmp_path, "%(id)s.%(ext)s")
    assert "writethumbnail" not in w                                                         # webm cannot embed covers


@needs_ffmpeg
@pytest.mark.parametrize("fmt,codec", [("mp3", "mp3"), ("m4a", "aac"), ("opus", "opus"),
                                       ("flac", "flac"), ("wav", "pcm_s16le"), ("ogg", "vorbis")])
def test_audio_extraction_every_format(tmp_path, fmt, codec):
    src = tmp_path / "srv"; src.mkdir()
    assert make_test_video(src / "clip.mp4", seconds=2)
    events = []
    ff_dir = os.path.dirname(tool_path("ffmpeg"))
    with LocalServer(src) as srv:
        res = dl.download_video(srv.url("clip.mp4"), tmp_path / "out", template="%(id)s.%(ext)s",
                                profile=dl.Profile(kind="audio", audio_format=fmt, audio_quality=128),
                                ffmpeg_dir=ff_dir, on_progress=events.append, allow_any_host=True)
    path = res["file_path"]
    assert os.path.isfile(path) and path.endswith("." + fmt), path
    streams = _probe(path)
    assert codec in ",".join(streams) and "video" not in streams          # audio only, right codec
    assert any(e["status"] == "merging" for e in events)                  # "processing" phase is reported


@needs_ffmpeg
def test_mp3_bitrate_setting_is_applied(tmp_path):
    src = tmp_path / "srv"; src.mkdir()
    make_test_video(src / "clip.mp4", seconds=3)
    sizes = {}
    with LocalServer(src) as srv:
        for q in (64, 320):
            r = dl.download_video(srv.url("clip.mp4"), tmp_path / f"o{q}", template="%(id)s.%(ext)s",
                                  profile=dl.Profile(kind="audio", audio_format="mp3", audio_quality=q),
                                  ffmpeg_dir=os.path.dirname(tool_path("ffmpeg")), allow_any_host=True)
            sizes[q] = r["size"]
    assert sizes[320] > sizes[64] * 2


@needs_ffmpeg
def test_video_profile_download_keeps_video_and_audio(tmp_path):
    src = tmp_path / "srv"; src.mkdir()
    make_test_video(src / "clip.mp4")
    with LocalServer(src) as srv:
        r = dl.download_video(srv.url("clip.mp4"), tmp_path / "o", template="%(id)s.%(ext)s",
                              profile=dl.Profile(container="mp4", max_height=720), allow_any_host=True)
    assert set(_probe(r["file_path"], "stream=codec_type")) >= {"video", "audio"}


@needs_ffmpeg
def test_speed_limit_slows_the_transfer(tmp_path):
    src = tmp_path / "srv"; src.mkdir()
    (src / "big.mp4").write_bytes(os.urandom(600_000))
    import time as _t
    with LocalServer(src) as srv:
        t0 = _t.monotonic()
        dl.download_video(srv.url("big.mp4"), tmp_path / "fast", template="%(id)s.%(ext)s",
                          profile=dl.Profile(embed_metadata=False), allow_any_host=True)
        fast = _t.monotonic() - t0
        t0 = _t.monotonic()
        dl.download_video(srv.url("big.mp4"), tmp_path / "slow", template="%(id)s.%(ext)s",
                          profile=dl.Profile(limit_rate_kbps=200, embed_metadata=False), allow_any_host=True)
        slow = _t.monotonic() - t0
    assert slow > fast + 1.0            # 600 KB at 200 KB/s needs ~3 s; unlimited takes well under 1 s


@needs_ffmpeg
def test_bundled_ffmpeg_is_found_even_when_it_is_not_on_path(tmp_path, monkeypatch):
    """Regression (seen on GitHub Actions): ffmpeg lives in binaries/, not on PATH."""
    src = tmp_path / "srv"; src.mkdir()
    assert make_test_video(src / "clip.mp4")
    bindir = tmp_path / "bundle" / "binaries"; bindir.mkdir(parents=True)
    for name in ("ffmpeg", "ffprobe"):
        os.symlink(tool_path(name), bindir / name)
    monkeypatch.setattr(dl, "tool_path", lambda n: str(bindir / n))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))                  # no ffmpeg anywhere on PATH
    with LocalServer(src) as srv:
        r = dl.download_video(srv.url("clip.mp4"), tmp_path / "o", template="%(id)s.%(ext)s", allow_any_host=True)
        a = dl.download_video(srv.url("clip.mp4"), tmp_path / "a", template="%(id)s.%(ext)s", allow_any_host=True,
                              profile=dl.Profile(kind="audio", audio_format="mp3"))
    assert os.path.getsize(r["file_path"]) > 0 and a["file_path"].endswith(".mp3")
