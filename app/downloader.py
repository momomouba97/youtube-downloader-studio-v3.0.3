"""Download one item with yt-dlp (+FFmpeg). No UI code; runs in worker threads."""
import errno
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .platforms import UnsupportedURL, validate_media_url
from .resources import tool_path

DEFAULT_TEMPLATE = "%(title)s [%(id)s].%(ext)s"
VIDEO_CONTAINERS = ("mp4", "mkv", "webm")
AUDIO_FORMATS = ("mp3", "m4a", "opus", "flac", "wav", "ogg")   # m4a = AAC
_AUDIO_CODEC = {"mp3": "mp3", "m4a": "m4a", "opus": "opus", "flac": "flac", "wav": "wav", "ogg": "vorbis"}
LOSSLESS = {"flac", "wav"}
QUALITY_HEIGHTS = (2160, 1440, 1080, 720, 480, 360)
AUDIO_BITRATES = (320, 256, 192, 128, 96, 64)
AUDIO_EXTENSIONS = {"." + f for f in AUDIO_FORMATS} | {".aac", ".weba", ".wma"}


class DownloadAborted(Exception):
    """Raised inside a download when the user cancelled or paused it."""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason  # "cancel" | "pause"


class DownloadError(Exception):
    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Profile:
    """Everything that decides *what* is downloaded and how. Stored with each task (so retries match)."""
    kind: str = "video"              # "video" | "audio"
    container: str = "mp4"           # video container
    max_height: int = 1080           # 0 = best available
    audio_format: str = "mp3"
    audio_quality: int = 192         # kbps (ignored for flac/wav)
    embed_metadata: bool = True
    embed_thumbnail: bool = False
    limit_rate_kbps: int = 0         # 0 = unlimited
    fragments: int = 4               # concurrent fragment downloads (HLS/DASH speed-up)
    cookies_file: str = ""
    allow_other_sites: bool = True

    @classmethod
    def from_dict(cls, d=None):
        d = d or {}
        base = cls()

        def pick(key, allowed, default):
            v = d.get(key, default)
            return v if v in allowed else default

        def num(key, lo, hi, default):
            v = d.get(key, default)
            return max(lo, min(hi, v)) if isinstance(v, int) and not isinstance(v, bool) else default

        return cls(kind=pick("kind", ("video", "audio"), base.kind),
                   container=pick("container", VIDEO_CONTAINERS, base.container),
                   max_height=pick("max_height", (0,) + QUALITY_HEIGHTS, base.max_height),
                   audio_format=pick("audio_format", AUDIO_FORMATS, base.audio_format),
                   audio_quality=num("audio_quality", 32, 320, base.audio_quality),
                   embed_metadata=bool(d.get("embed_metadata", base.embed_metadata)),
                   embed_thumbnail=bool(d.get("embed_thumbnail", base.embed_thumbnail)),
                   limit_rate_kbps=num("limit_rate_kbps", 0, 1_000_000, base.limit_rate_kbps),
                   fragments=num("fragments", 1, 8, base.fragments),
                   cookies_file=str(d.get("cookies_file") or ""),
                   allow_other_sites=bool(d.get("allow_other_sites", base.allow_other_sites)))

    def to_dict(self):
        return asdict(self)

    @property
    def extension(self):
        return self.audio_format if self.kind == "audio" else self.container

    def label(self):
        if self.kind == "audio":
            q = "" if self.audio_format in LOSSLESS else f" {self.audio_quality}k"
            return f"{self.audio_format.upper()}{q}"
        return f"{self.container.upper()} " + ("best" if not self.max_height else f"≤{self.max_height}p")


def format_selector(max_quality=1080, fmt="mp4"):
    """Video format expression: best quality not above max_quality (0/None = best); prefers H.264 for MP4."""
    h = int(max_quality or 0)
    cap = f"[height<={h}]" if h else ""
    anyh = f"[height<=?{h}]" if h else ""
    if fmt == "mp4":
        return (f"bv*{cap}[vcodec^=avc1]+ba[ext=m4a]"
                f"/b{cap}[ext=mp4]"
                f"/bv*{cap}[ext=mp4]+ba[ext=m4a]"
                f"/bv*{cap}+ba"
                f"/b{anyh}")
    if fmt == "webm":
        return f"bv*{cap}[ext=webm]+ba[ext=webm]/bv*{cap}+ba/b{anyh}"
    return f"bv*{cap}+ba/b{anyh}"


def audio_selector(audio_format="mp3"):
    if audio_format == "m4a":
        return "ba[ext=m4a]/ba/b"
    if audio_format == "opus":
        return "ba[ext=webm]/ba/b"
    return "ba/b"


def profile_selector(profile):
    if profile.kind == "audio":
        return audio_selector(profile.audio_format)
    return format_selector(profile.max_height, profile.container)


def validate_template(template):
    t = (template or "").strip()
    if "%(ext)s" not in t:
        raise ValueError("The filename template must contain %(ext)s.")
    if any(x in t for x in ("/", "\\", "..", "\x00")):
        raise ValueError("The filename template must not contain path separators or '..'.")
    return t


def safe_dirname(name):
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name or "").strip(" .")
    return cleaned[:80] or "Channel"


_NON_RETRYABLE = [
    ("private video", "This video is private."),
    ("video unavailable", "This video is unavailable."),
    ("not available", "This video is not available."),
    ("has been removed", "This video has been removed."),
    ("members-only", "Members-only video (login required)."),
    ("join this channel", "Members-only video (login required)."),
    ("sign in to confirm", "YouTube asks to sign in to confirm you are not a bot."),
    ("sign in to view", "Sign-in is required to view this video."),
    ("login required", "This site requires you to be logged in. If you have access, set a cookies file in Settings."),
    ("log in", "This site requires you to be logged in. If you have access, set a cookies file in Settings."),
    ("rate-limit", "The site is rate-limiting requests. Wait a while or provide a cookies file."),
    ("age-restricted", "Age-restricted video (login required)."),
    ("copyright", "Blocked for copyright reasons."),
    ("drm", "This content is DRM-protected and cannot be downloaded."),
    ("requested format is not available", "The chosen format/quality is not available for this item."),
    ("ffmpeg is not installed", "FFmpeg was not found, so audio/video cannot be processed."),
    ("ffmpeg not found", "FFmpeg was not found, so audio/video cannot be processed."),
    ("ffprobe and ffmpeg not found", "FFmpeg was not found, so audio/video cannot be processed."),
    ("no space left", "No space left on the disk."),
    ("premieres in", "This video has not premiered yet."),
    ("live event will begin", "This live event has not started yet."),
    ("unsupported url", "This link is not supported."),
    ("is not a valid url", "This is not a valid link."),
]


def classify_error(message):
    """Return (short_message, retryable)."""
    clean = re.sub(r"\x1b\[[0-9;]*m", "", str(message)).replace("ERROR:", "").strip()
    low = clean.lower()
    for needle, friendly in _NON_RETRYABLE:
        if needle in low:
            return friendly, False
    return (clean[:300] or "Unknown error"), True


def _default_factory(opts):
    import yt_dlp  # lazy
    return yt_dlp.YoutubeDL(opts)


def build_ydl_opts(profile, out_dir, template, hook=None, pp_hook=None, ffmpeg_dir=None):
    """yt-dlp options for a profile. Pure function (unit-tested)."""
    opts = {
        "format": profile_selector(profile),
        "outtmpl": os.path.join(str(out_dir), template),
        "noplaylist": True, "quiet": True, "no_warnings": True, "noprogress": True,
        "retries": 5, "fragment_retries": 5, "socket_timeout": 30,
        "continuedl": True, "overwrites": False, "updatetime": False,
        "trim_file_name": 150, "ignoreerrors": False,
        "concurrent_fragment_downloads": profile.fragments,
    }
    if hook:
        opts["progress_hooks"] = [hook]
    if pp_hook:
        opts["postprocessor_hooks"] = [pp_hook]
    if profile.limit_rate_kbps:
        opts["ratelimit"] = profile.limit_rate_kbps * 1024
    if profile.cookies_file and os.path.isfile(profile.cookies_file):
        opts["cookiefile"] = profile.cookies_file
    if ffmpeg_dir:
        opts["ffmpeg_location"] = str(ffmpeg_dir)
    pps = []
    if profile.kind == "audio":
        pp = {"key": "FFmpegExtractAudio", "preferredcodec": _AUDIO_CODEC[profile.audio_format]}
        if profile.audio_format not in LOSSLESS:
            pp["preferredquality"] = str(profile.audio_quality)
        pps.append(pp)
    else:
        opts["merge_output_format"] = profile.container
    if profile.embed_metadata:
        pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": profile.kind == "video"})
    embeddable = profile.audio_format in ("mp3", "m4a", "flac", "opus", "ogg") if profile.kind == "audio" \
        else profile.container in ("mp4", "mkv")
    if profile.embed_thumbnail and embeddable:
        opts["writethumbnail"] = True
        pps.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
        pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
    if pps:
        opts["postprocessors"] = pps
    return opts


def download_video(url, out_dir, *, profile=None, template=DEFAULT_TEMPLATE, max_quality=1080, fmt="mp4",
                   ffmpeg_dir=None, on_progress=None, should_abort=None,
                   ydl_factory=_default_factory, allow_any_host=False):
    """Download one item. `profile` (a Profile or dict) wins over max_quality/fmt (kept for older callers).
    Returns {file_path, size, height}. Raises DownloadAborted (cancel/pause) or DownloadError(message, retryable)."""
    if profile is None:
        profile = Profile(max_height=int(max_quality or 0), container=fmt if fmt in VIDEO_CONTAINERS else "mp4",
                          allow_other_sites=False)   # legacy callers: known platforms only
    elif isinstance(profile, dict):
        profile = Profile.from_dict(profile)
    try:
        validate_media_url(url, allow_other=profile.allow_other_sites, allow_any_host=allow_any_host)
    except UnsupportedURL as exc:
        raise DownloadError(str(exc), retryable=False) from exc
    try:
        template = validate_template(template)
        Path(out_dir).mkdir(parents=True, exist_ok=True)
    except ValueError as exc:
        raise DownloadError(str(exc), retryable=False) from exc
    except OSError as exc:
        raise DownloadError(f"Cannot create the download folder: {exc.strerror or exc}", retryable=False) from exc

    emit = on_progress or (lambda d: None)
    state = {"done": 0, "n": 1, "bytes_before": 0}

    def hook(d):
        reason = should_abort() if should_abort else None
        if reason:
            raise DownloadAborted(reason)
        info = d.get("info_dict") or {}
        rf = info.get("requested_formats")
        state["n"] = len(rf) if rf else 1
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            frac = (got / total) if total else 0.0
            overall_total = info.get("filesize") or info.get("filesize_approx") or (total if state["n"] == 1 else 0)
            emit({"status": "downloading",
                  "percent": min(99.0, (state["done"] + frac) / state["n"] * 100.0),
                  "speed": d.get("speed") or 0, "eta": d.get("eta"),
                  "downloaded": state["bytes_before"] + got, "total": overall_total})
        elif d.get("status") == "finished":
            state["done"] += 1
            state["bytes_before"] += d.get("downloaded_bytes") or d.get("total_bytes") or 0
            if state["done"] >= state["n"] and (state["n"] > 1 or profile.kind == "audio"):
                emit({"status": "merging", "percent": 99.0, "speed": 0, "eta": None,
                      "downloaded": state["bytes_before"], "total": state["bytes_before"]})

    def pp_hook(d):
        reason = should_abort() if should_abort else None
        if reason == "cancel":
            raise DownloadAborted(reason)
        if d.get("status") == "started":
            emit({"status": "merging", "percent": 99.0, "speed": 0, "eta": None})

    if ffmpeg_dir is None:                      # use the bundled FFmpeg even when it is not on PATH
        found = tool_path("ffmpeg")
        ffmpeg_dir = os.path.dirname(found) if found else None
    opts = build_ydl_opts(profile, out_dir, template, hook, pp_hook, ffmpeg_dir)
    emit({"status": "preparing", "percent": 0.0, "speed": 0, "eta": None})
    path = None
    try:
        with ydl_factory(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise DownloadError("The site returned no data for this link.", retryable=True)
            if info.get("_type") == "playlist" and info.get("entries"):
                entries = [e for e in info["entries"] if e]
                info = entries[0] if entries else info
            rd = info.get("requested_downloads") or []
            if rd and rd[0].get("filepath"):
                path = rd[0]["filepath"]
            if not path or not os.path.isfile(path):
                guess = ydl.prepare_filename(info)
                base = os.path.splitext(guess)[0]
                exts = [profile.extension, "mp4", "mkv", "webm", "m4a", "mp3", "opus"]
                for cand in [guess] + [f"{base}.{e}" for e in exts]:
                    if os.path.isfile(cand):
                        path = cand
                        break
    except DownloadAborted:
        raise
    except DownloadError:
        raise
    except Exception as exc:
        reason = should_abort() if should_abort else None
        if reason:
            raise DownloadAborted(reason) from exc
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            raise DownloadError("No space left on the disk.", retryable=False) from exc
        msg, retryable = classify_error(exc)
        raise DownloadError(msg, retryable) from exc

    if not path or not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise DownloadError("The downloaded file was not found on disk.", retryable=True)
    return {"file_path": path, "size": os.path.getsize(path), "height": int(info.get("height") or 0)}
