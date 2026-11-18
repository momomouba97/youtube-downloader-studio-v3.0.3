"""Analyse a link (single video / playlist / channel page) without downloading anything."""
import os
import re
from dataclasses import dataclass, field

from .channel_parser import ImportCancelled, ImportFailed
from .downloader import QUALITY_HEIGHTS, classify_error
from .platforms import UnsupportedURL, detect_platform, platform_from_extractor, validate_media_url


@dataclass
class MediaEntry:
    key: str                 # unique id used by the queue: raw id for YouTube, "extractor:id" otherwise
    raw_id: str
    title: str
    url: str
    thumbnail: str = ""
    duration: int = 0
    uploader: str = ""
    platform: str = ""
    upload_date: str = ""
    playlist_title: str = ""
    playlist_index: int = 0
    available: bool = True
    checked: bool = True


@dataclass
class Analysis:
    source_url: str
    kind: str                # "video" | "playlist"
    title: str
    uploader: str
    platform: str
    entries: list = field(default_factory=list)
    formats: list = field(default_factory=list)       # only for a single video
    skipped: int = 0

    @property
    def heights(self):
        return available_heights(self.formats)

    @property
    def total_duration(self):
        return sum(e.duration for e in self.entries)


def _default_factory(opts):
    import yt_dlp
    return yt_dlp.YoutubeDL(opts)


def make_key(extractor_key, raw_id):
    return raw_id if re.sub(r"[^a-z]", "", (extractor_key or "").lower()).startswith("youtube") \
        else f"{(extractor_key or 'web').lower()}:{raw_id}"


def _best_thumb(info):
    best, w = "", -1
    for t in info.get("thumbnails") or []:
        u = t.get("url", "")
        if u.startswith("https://") and (t.get("width") or 0) > w:
            best, w = u, t.get("width") or 0
    t = info.get("thumbnail")
    return best or (t if isinstance(t, str) and t.startswith("https://") else "")


def _date(info):
    d = info.get("upload_date")
    return f"{d[:4]}-{d[4:6]}-{d[6:]}" if isinstance(d, str) and re.fullmatch(r"\d{8}", d) else ""


def parse_media_entry(info, *, fallback_url="", playlist_title="", index=0, default_extractor=""):
    """yt-dlp entry/info dict -> MediaEntry (or None if it has no usable id/url)."""
    if not isinstance(info, dict):
        return None
    raw_id = info.get("id")
    ext = info.get("extractor_key") or info.get("ie_key") or default_extractor
    url = info.get("webpage_url") or info.get("url") or fallback_url
    if not isinstance(raw_id, str) or not raw_id:
        return None
    if isinstance(url, str) and not url.startswith(("http://", "https://")):
        url = f"https://www.youtube.com/watch?v={raw_id}" if str(ext).lower().startswith("youtube") else ""
    if not url:
        return None
    title = (info.get("title") or "").strip() or raw_id
    dur = info.get("duration")
    low = title.lower()
    return MediaEntry(
        key=make_key(ext, raw_id), raw_id=raw_id, title=title, url=url, thumbnail=_best_thumb(info),
        duration=int(dur) if isinstance(dur, (int, float)) else 0,
        uploader=(info.get("uploader") or info.get("channel") or info.get("creator") or "").strip(),
        platform=platform_from_extractor(ext, url), upload_date=_date(info),
        playlist_title=playlist_title, playlist_index=index,
        available=not ("[private video]" in low or "[deleted video]" in low
                       or info.get("availability") in ("private", "needs_auth", "subscriber_only", "premium_only")))


def simplify_formats(formats):
    out = []
    for f in formats or []:
        if not isinstance(f, dict) or f.get("format_note") == "storyboard" or f.get("ext") == "mhtml":
            continue
        out.append({"format_id": f.get("format_id"), "ext": f.get("ext"), "height": f.get("height") or 0,
                    "width": f.get("width") or 0, "fps": f.get("fps") or 0,
                    "vcodec": f.get("vcodec") or "none", "acodec": f.get("acodec") or "none",
                    "filesize": f.get("filesize") or f.get("filesize_approx") or 0, "tbr": f.get("tbr") or 0})
    return out


def available_heights(formats):
    """Video heights actually offered, snapped to the standard ladder, best first."""
    hs = {f["height"] for f in formats if f.get("height") and f.get("vcodec") not in (None, "none")}
    ladder = set()
    for h in hs:
        for q in QUALITY_HEIGHTS:
            if h >= q * 0.9:             # 1080-line video with letterboxing (e.g. 1920x816) still counts as 1080p
                ladder.add(q)
                break
    return sorted(ladder, reverse=True)


def estimate_size(formats, kind="video", max_height=0, audio_kbps=0, duration=0):
    """Best-effort download size in bytes, or None when the site publishes no sizes."""
    audio = [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
    best_audio = max((f["filesize"] for f in audio if f.get("filesize")), default=0)
    if kind == "audio":
        if audio_kbps and duration:
            return int(audio_kbps * 1000 / 8 * duration)
        return best_audio or None
    vids = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("filesize")
            and (not max_height or 0 < f["height"] <= max_height * 1.1)]
    if not vids:
        return None
    video = max(vids, key=lambda f: (f["height"], f["filesize"]))
    muxed = video.get("acodec") not in (None, "none")
    return video["filesize"] + (0 if muxed else best_audio)


def _flatten(entries):
    for e in entries or []:
        if e is None:
            continue
        if isinstance(e, dict) and e.get("entries") is not None:
            yield from _flatten(e["entries"])
        else:
            yield e


def analyze_url(url, *, noplaylist=False, cookies_file="", allow_other=True, on_progress=None,
                should_cancel=None, ydl_factory=_default_factory, allow_any_host=False):
    """Return an Analysis. Raises UnsupportedURL, ImportCancelled or ImportFailed(friendly message)."""
    platform = validate_media_url(url, allow_other=allow_other, allow_any_host=allow_any_host)
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "skip_download": True,
            "extract_flat": "in_playlist", "noplaylist": bool(noplaylist), "lazy_playlist": True,
            "socket_timeout": 30, "ignoreerrors": False}
    if cookies_file and os.path.isfile(cookies_file):
        opts["cookiefile"] = cookies_file
    try:
        with ydl_factory(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ImportFailed("Nothing was found at this link.")
            ext = info.get("extractor_key") or ""
            plat = platform_from_extractor(ext, url) or platform
            if info.get("entries") is not None:
                title = (info.get("title") or "Playlist").strip()
                title = re.sub(r"\s+-\s+(Videos|Shorts|Live)$", "", title)
                uploader = (info.get("uploader") or info.get("channel") or "").strip()
                result = Analysis(url, "playlist", title, uploader, plat)
                n = 0
                for raw in _flatten(info["entries"]):
                    if should_cancel and should_cancel():
                        raise ImportCancelled()
                    n += 1
                    e = parse_media_entry(raw, playlist_title=title, index=n, default_extractor=ext)
                    if e is None:
                        result.skipped += 1
                        n -= 1
                        continue
                    e.platform = e.platform or plat
                    e.uploader = e.uploader or uploader
                    result.entries.append(e)
                    if on_progress and n % 25 == 0:
                        on_progress(n)
                for i, e in enumerate(result.entries, 1):
                    e.playlist_index = i
                if not result.entries:
                    raise ImportFailed("This playlist has no downloadable videos.")
            else:
                e = parse_media_entry(info, fallback_url=url, default_extractor=ext)
                if e is None:
                    raise ImportFailed("Could not read this video.")
                e.platform = e.platform or plat
                result = Analysis(url, "video", e.title, e.uploader, e.platform, [e],
                                  simplify_formats(info.get("formats")))
            if on_progress:
                on_progress(len(result.entries))
            return result
    except (ImportCancelled, ImportFailed, UnsupportedURL):
        raise
    except Exception as exc:
        raise ImportFailed(classify_error(exc)[0]) from exc
