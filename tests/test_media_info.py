import pytest
from app.channel_parser import ImportCancelled, ImportFailed
from app.media_info import (analyze_url, available_heights, estimate_size, make_key, parse_media_entry,
                            simplify_formats)
from app.platforms import UnsupportedURL, detect_platform, extract_urls, platform_from_extractor, validate_media_url


class FakeYDL:
    def __init__(self, info, seen=None):
        self.info, self.seen = info, seen
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def extract_info(self, url, download=False, process=True):
        assert download is False
        if self.seen is not None:
            self.seen.append(url)
        if isinstance(self.info, Exception):
            raise self.info
        return self.info


def fac(info, seen=None):
    return lambda opts: FakeYDL(info, seen)


FORMATS = [
    {"format_id": "sb0", "ext": "mhtml", "format_note": "storyboard", "vcodec": "none", "acodec": "none"},
    {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "filesize": 3_000_000, "tbr": 128},
    {"format_id": "18", "ext": "mp4", "height": 360, "width": 640, "vcodec": "avc1", "acodec": "mp4a", "filesize": 9_000_000},
    {"format_id": "136", "ext": "mp4", "height": 720, "vcodec": "avc1", "acodec": "none", "filesize": 30_000_000},
    {"format_id": "137", "ext": "mp4", "height": 1080, "vcodec": "avc1", "acodec": "none", "filesize_approx": 80_000_000},
    {"format_id": "313", "ext": "webm", "height": 2160, "vcodec": "vp9", "acodec": "none", "filesize": 400_000_000},
    {"format_id": "x", "ext": "mp4", "height": 816, "width": 1920, "vcodec": "avc1", "acodec": "none"},
]


def test_platform_detection_and_extraction():
    cases = {"https://www.tiktok.com/@u/video/7": "TikTok", "https://vm.tiktok.com/abc": "TikTok",
             "https://fb.watch/xyz/": "Facebook", "https://www.facebook.com/watch/?v=1": "Facebook",
             "https://www.instagram.com/reel/C1/": "Instagram", "https://x.com/u/status/1": "X / Twitter",
             "https://youtu.be/abc": "YouTube", "https://faketiktok.com/a": "", "https://example.org/a": ""}
    for u, want in cases.items():
        assert detect_platform(u) == want, u
    assert platform_from_extractor("TikTokUser") == "TikTok" and platform_from_extractor("Youtube") == "YouTube"
    assert extract_urls("a https://youtu.be/1, b (https://vimeo.com/2). https://youtu.be/1") == \
        ["https://youtu.be/1", "https://vimeo.com/2"]


def test_url_validation_rules():
    assert validate_media_url("https://www.tiktok.com/@a/video/1", allow_other=False) == "TikTok"
    assert validate_media_url("https://example.org/v", allow_other=True) == ""
    with pytest.raises(UnsupportedURL):
        validate_media_url("https://example.org/v", allow_other=False)
    for bad in ("file:///x", "http://127.0.0.1/x", "http://10.0.0.2/x", "https://localhost/x",
                "https://u:p@youtube.com/x", "gopher://x", ""):
        with pytest.raises(UnsupportedURL):
            validate_media_url(bad, allow_other=True)


def test_keys_are_unique_across_platforms():
    assert make_key("Youtube", "abc") == "abc"
    assert make_key("TikTok", "123") == "tiktok:123" and make_key("Instagram", "123") == "instagram:123"


def test_parse_entry_variants():
    yt = parse_media_entry({"id": "dQw4w9WgXcQ", "title": " T ", "duration": 61.5, "url": "dQw4w9WgXcQ",
                            "ie_key": "Youtube"}, playlist_title="PL", index=3)
    assert yt.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ" and yt.duration == 61 and yt.playlist_index == 3
    tk = parse_media_entry({"id": "7123", "title": "clip", "webpage_url": "https://www.tiktok.com/@a/video/7123",
                            "extractor_key": "TikTok", "uploader": "alice", "upload_date": "20240131",
                            "thumbnails": [{"url": "https://cdn/x.jpg", "width": 300}]})
    assert tk.key == "tiktok:7123" and tk.platform == "TikTok" and tk.upload_date == "2024-01-31"
    assert tk.thumbnail == "https://cdn/x.jpg" and tk.uploader == "alice"
    assert parse_media_entry({"id": "", "title": "x"}) is None and parse_media_entry("junk") is None
    assert parse_media_entry({"id": "a1", "title": "[Private video]", "url": "https://v.com/a1"}).available is False


def test_formats_heights_and_size_estimates():
    f = simplify_formats(FORMATS)
    assert all(x["ext"] != "mhtml" for x in f)
    assert available_heights(f) == [2160, 1080, 720, 360]            # 1920x816 snaps to 1080p
    assert estimate_size(f, "video", 720) == 30_000_000 + 3_000_000   # video + best audio
    assert estimate_size(f, "video", 1080) == 80_000_000 + 3_000_000  # filesize_approx is used
    assert estimate_size(f, "video", 0) == 400_000_000 + 3_000_000    # best
    assert estimate_size(f, "video", 360) == 9_000_000                # muxed format: no extra audio
    assert estimate_size(f, "audio", audio_kbps=192, duration=100) == 2_400_000
    assert estimate_size([], "video", 720) is None


def test_analyze_single_video():
    info = {"id": "abc", "title": "Solo", "extractor_key": "Youtube", "webpage_url": "https://www.youtube.com/watch?v=abc",
            "duration": 100, "uploader": "Chan", "formats": FORMATS}
    a = analyze_url("https://youtu.be/abc", ydl_factory=fac(info))
    assert a.kind == "video" and len(a.entries) == 1 and a.platform == "YouTube" and a.heights == [2160, 1080, 720, 360]
    assert a.entries[0].key == "abc" and a.entries[0].uploader == "Chan"


def test_analyze_playlist_nested_numbered_and_skips():
    ents = [{"id": f"v{i:010d}", "title": f"T{i}", "url": f"https://www.youtube.com/watch?v=v{i:010d}",
             "ie_key": "Youtube", "duration": 60} for i in range(300)]
    info = {"id": "PL1", "title": "My List - Videos", "uploader": "Chan", "extractor_key": "YoutubeTab",
            "entries": [{"entries": iter(ents[:100])}, {"entries": ents[100:]}, None, {"id": "", "title": "bad"}]}
    seen = []
    a = analyze_url("https://www.youtube.com/playlist?list=PL1", ydl_factory=fac(info), on_progress=seen.append)
    assert a.kind == "playlist" and a.title == "My List" and len(a.entries) == 300 and a.skipped == 1
    assert [e.playlist_index for e in a.entries][:3] == [1, 2, 3] and a.entries[-1].playlist_index == 300
    assert all(e.playlist_title == "My List" and e.uploader == "Chan" for e in a.entries)
    assert a.total_duration == 300 * 60 and seen[-1] == 300


def test_analyze_other_platform_playlist_keys():
    info = {"id": "u", "title": "alice", "extractor_key": "TikTokUser", "uploader": "alice",
            "entries": [{"id": "1", "title": "a", "url": "https://www.tiktok.com/@alice/video/1", "ie_key": "TikTok"},
                        {"id": "2", "title": "b", "url": "https://www.tiktok.com/@alice/video/2", "ie_key": "TikTok"}]}
    a = analyze_url("https://www.tiktok.com/@alice", ydl_factory=fac(info))
    assert [e.key for e in a.entries] == ["tiktok:1", "tiktok:2"] and a.platform == "TikTok"


def test_analyze_errors_and_cancel():
    with pytest.raises(UnsupportedURL):
        analyze_url("https://example.org/x", allow_other=False, ydl_factory=fac({}))
    with pytest.raises(ImportFailed):
        analyze_url("https://vimeo.com/1", ydl_factory=fac(None))
    with pytest.raises(ImportFailed) as e:
        analyze_url("https://www.instagram.com/reel/x/", ydl_factory=fac(RuntimeError("ERROR: login required")))
    assert "cookies" in str(e.value)
    with pytest.raises(ImportFailed):
        analyze_url("https://vimeo.com/1", ydl_factory=fac({"id": "p", "title": "x", "entries": []}))
    info = {"id": "p", "title": "x", "extractor_key": "Youtube",
            "entries": [{"id": f"v{i:010d}", "title": "t", "url": f"https://www.youtube.com/watch?v=v{i:010d}"} for i in range(5)]}
    with pytest.raises(ImportCancelled):
        analyze_url("https://www.youtube.com/playlist?list=p", ydl_factory=fac(info), should_cancel=lambda: True)


def test_noplaylist_and_cookies_are_passed_to_ytdlp(tmp_path):
    ck = tmp_path / "c.txt"; ck.write_text("x")
    captured = {}

    def factory(opts):
        captured.update(opts)
        return FakeYDL({"id": "abc", "title": "t", "extractor_key": "Youtube", "formats": []})
    analyze_url("https://www.youtube.com/watch?v=abc&list=PL", noplaylist=True, cookies_file=str(ck), ydl_factory=factory)
    assert captured["noplaylist"] is True and captured["cookiefile"] == str(ck) and captured["skip_download"] is True


def test_real_ytdlp_analysis_of_a_direct_media_link(tmp_path):
    from tests.helpers import LocalServer
    (tmp_path / "clip.mp4").write_bytes(b"\x00" * 4096)
    with LocalServer(tmp_path) as srv:
        a = analyze_url(srv.url("clip.mp4"), allow_any_host=True)
    assert a.kind == "video" and len(a.entries) == 1 and a.entries[0].title
    assert a.entries[0].url.startswith("http://127.0.0.1") and any(f["ext"] == "mp4" for f in a.formats)
