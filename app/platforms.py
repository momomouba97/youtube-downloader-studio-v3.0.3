"""Supported sites, link detection and URL safety checks (no network, no yt-dlp)."""
import ipaddress
import re
from urllib.parse import urlparse

# (display name, host suffixes). Any link yt-dlp understands also works when "other sites" is allowed.
PLATFORMS = [
    ("YouTube", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
    ("Facebook", ("facebook.com", "fb.watch", "fb.com")),
    ("Instagram", ("instagram.com",)),
    ("TikTok", ("tiktok.com",)),
    ("X / Twitter", ("twitter.com", "x.com")),
    ("Vimeo", ("vimeo.com",)),
    ("Dailymotion", ("dailymotion.com", "dai.ly")),
    ("Twitch", ("twitch.tv",)),
    ("Reddit", ("reddit.com", "redd.it")),
    ("SoundCloud", ("soundcloud.com",)),
    ("Bilibili", ("bilibili.com", "b23.tv")),
    ("Pinterest", ("pinterest.com", "pin.it")),
    ("Snapchat", ("snapchat.com",)),
    ("LinkedIn", ("linkedin.com",)),
    ("Bandcamp", ("bandcamp.com",)),
]
_EXTRACTOR_NAMES = {"youtube": "YouTube", "facebook": "Facebook", "instagram": "Instagram", "tiktok": "TikTok",
                    "twitter": "X / Twitter", "vimeo": "Vimeo", "dailymotion": "Dailymotion", "twitch": "Twitch",
                    "reddit": "Reddit", "soundcloud": "SoundCloud", "bilibili": "Bilibili", "pinterest": "Pinterest",
                    "snapchat": "Snapchat", "linkedin": "LinkedIn", "bandcamp": "Bandcamp"}
_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.I)
_TRAILING = ".,;:!?)]}>'\""


def _host(url):
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def detect_platform(url):
    """Display name of a known platform for this URL, or '' if the host is not in the list."""
    h = _host(url)
    for name, suffixes in PLATFORMS:
        if any(h == s or h.endswith("." + s) for s in suffixes):
            return name
    return ""


def platform_from_extractor(extractor_key, url=""):
    key = re.sub(r"[^a-z]", "", (extractor_key or "").lower())
    for k, name in _EXTRACTOR_NAMES.items():
        if key.startswith(k):
            return name
    return detect_platform(url)


def extract_urls(text):
    """All http(s) links in pasted text, de-duplicated, in order."""
    out, seen = [], set()
    for m in _URL_RE.findall(text or ""):
        u = m.rstrip(_TRAILING)
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def is_blocked_host(host):
    if not host or host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast


class UnsupportedURL(ValueError):
    pass


def validate_media_url(url, allow_other=True, allow_any_host=False):
    """Raise UnsupportedURL unless the link is safe to hand to yt-dlp. Returns the platform name ('' if other)."""
    u = (url or "").strip()
    try:
        p = urlparse(u)
        host = (p.hostname or "").lower().rstrip(".")
    except ValueError as exc:
        raise UnsupportedURL("This is not a valid link.") from exc
    if p.scheme not in ("http", "https") or not host:
        raise UnsupportedURL("Only http(s) links are supported.")
    if p.username or p.password:
        raise UnsupportedURL("Links containing a user name or password are not accepted.")
    if allow_any_host:
        return detect_platform(u)
    if is_blocked_host(host):
        raise UnsupportedURL("Local or private network addresses are not allowed.")
    plat = detect_platform(u)
    if not plat and not allow_other:
        raise UnsupportedURL("This website is not in the supported list. Enable “Allow other websites” in Settings to try it.")
    return plat
