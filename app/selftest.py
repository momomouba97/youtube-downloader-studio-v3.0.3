"""`--selftest`: verifies that a frozen build really contains everything it needs."""
import functools
import http.server
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time


def run(require_ffmpeg=False) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    tmp = tempfile.mkdtemp(prefix="ytds-selftest-")
    os.environ["YTDS_DATA_DIR"] = tmp
    failures, ctx = [], {}

    def step(name, fn, critical=True):
        try:
            print(f"[ok]   {name}: {fn()}")
        except Exception as exc:
            print(f"[{'FAIL' if critical else 'warn'}] {name}: {exc!r}")
            if critical:
                failures.append(name)

    def ffmpeg():
        from app.resources import tool_path
        return tool_path("ffmpeg")

    def local_clip():
        """A 1-second clip generated with the bundled ffmpeg, served from a local HTTP server."""
        if "url" in ctx:
            return ctx["url"]
        ff = ffmpeg()
        if not ff:
            raise RuntimeError("ffmpeg not bundled")
        srv_dir = os.path.join(tmp, "srv")
        os.makedirs(srv_dir)
        clip = os.path.join(srv_dir, "clip.mp4")
        for codec in ("libx264", "mpeg4"):
            r = subprocess.run([ff, "-y", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                                "testsrc=duration=1:size=320x180:rate=15", "-f", "lavfi", "-i",
                                "sine=frequency=440:duration=1", "-pix_fmt", "yuv420p", "-c:v", codec, "-c:a", "aac",
                                "-shortest", clip], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
            if r.returncode == 0:
                break
        else:
            raise RuntimeError("could not generate a test video with the bundled ffmpeg")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=srv_dir)
        handler.func.log_message = lambda *a, **k: None
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        ctx.update(url=f"http://127.0.0.1:{httpd.server_address[1]}/clip.mp4", clip=clip, httpd=httpd)
        return ctx["url"]

    def versions():
        import PySide6
        import sqlite3
        import yt_dlp
        return (f"python {platform.python_version()} {platform.machine()} | PySide6 {PySide6.__version__} | "
                f"yt-dlp {yt_dlp.version.__version__} | sqlite {sqlite3.sqlite_version}")

    def extractors():
        import certifi
        import yt_dlp.extractor.youtube  # noqa: F401  (must be inside the bundle)
        import yt_dlp.extractor.tiktok, yt_dlp.extractor.instagram, yt_dlp.extractor.facebook  # noqa: F401,E401
        assert os.path.isfile(certifi.where()), "certifi CA bundle missing"
        return "YouTube/TikTok/Instagram/Facebook extractors + CA bundle present"

    def tool(name):
        from app.resources import tool_path
        p = tool_path(name)
        if not p:
            if require_ffmpeg:
                raise RuntimeError(f"{name} not bundled")
            return f"{name} not bundled (allowed)"
        out = subprocess.run([p, "-version"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        if out.returncode != 0:
            raise RuntimeError(f"{name} failed to run: {out.stderr[:200]}")
        return out.stdout.splitlines()[0]

    def analyze():
        from app.media_info import analyze_url
        a = analyze_url(local_clip(), allow_any_host=True)
        assert a.kind == "video" and len(a.entries) == 1 and a.formats, "analysis returned nothing"
        return f"single video analysed, {len(a.formats)} format(s)"

    def download(profile):
        from app.downloader import download_video
        from app.resources import tool_path
        ff = tool_path("ffmpeg")
        res = download_video(local_clip(), os.path.join(tmp, "out-" + profile.extension), profile=profile,
                             template="%(id)s.%(ext)s", allow_any_host=True, ffmpeg_dir=os.path.dirname(ff))
        assert res["size"] > 0 and os.path.isfile(res["file_path"]) and res["file_path"].endswith("." + profile.extension)
        return f"{profile.label()} -> {res['size']} bytes"

    def player():
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtWidgets import QApplication
        from ui.player import PlayerPage
        app = QApplication.instance() or QApplication([])
        local_clip()
        pg = PlayerPage()
        pg.set_queue([{"title": "clip", "path": ctx["clip"]}], 0, autoplay=False)
        end = time.monotonic() + 10
        while time.monotonic() < end and pg.player.duration() == 0:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        if pg.player.duration() == 0:
            raise RuntimeError(f"media did not load: {pg.player.errorString() or 'no error text'}")
        return f"loaded {pg.player.duration()} ms"

    def gui():
        from PySide6.QtWidgets import QApplication
        from app.database import Database
        from app.main_window import MainWindow
        from app.settings_manager import SettingsManager
        from app.thumbnail_manager import ThumbnailManager
        app = QApplication.instance() or QApplication([])
        db = Database(os.path.join(tmp, "selftest.db"))
        win = MainWindow(db, SettingsManager(os.path.join(tmp, "s.json")),
                         ThumbnailManager(os.path.join(tmp, "thumbs")))
        win.show()
        for page in list(win.pages):
            win._goto(page)
            app.processEvents()
        win.close()
        db.close()
        return "main window created, all pages opened, closed"

    from app.downloader import Profile
    step("versions", versions)
    step("yt-dlp bundle", extractors)
    step("ffmpeg", lambda: tool("ffmpeg"))
    step("ffprobe", lambda: tool("ffprobe"))
    step("analyse a link (yt-dlp, local server)", analyze, critical=bool(ffmpeg()))
    step("download video (yt-dlp, local server)", lambda: download(Profile()), critical=bool(ffmpeg()))
    step("extract MP3 (bundled ffmpeg)", lambda: download(Profile(kind="audio", audio_format="mp3")), critical=False)
    step("extract M4A (bundled ffmpeg)", lambda: download(Profile(kind="audio", audio_format="m4a")), critical=False)
    step("built-in player (QtMultimedia)", player, critical=False)
    step("gui (offscreen)", gui)
    if "httpd" in ctx:
        ctx["httpd"].shutdown()
    print("SELFTEST FAILED: " + ", ".join(failures) if failures else "SELFTEST OK")
    return 1 if failures else 0
