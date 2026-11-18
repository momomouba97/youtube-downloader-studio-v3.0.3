"""Shared test helpers: tiny local HTTP server + a generated test video (no YouTube access needed)."""
import functools
import http.server
import subprocess
import threading
import time

from app.resources import tool_path


def make_test_video(path, seconds=2, size="320x180"):
    ff = tool_path("ffmpeg")
    if not ff:
        return False
    for codec in ("libx264", "mpeg4"):
        r = subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"testsrc=duration={seconds}:size={size}:rate=15",
                            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                            "-pix_fmt", "yuv420p", "-c:v", codec, "-c:a", "aac", "-shortest", str(path)],
                           capture_output=True)
        if r.returncode == 0:
            return True
    return False


class _Handler(http.server.SimpleHTTPRequestHandler):
    slow = 0.0   # seconds to sleep between 16 KB chunks

    def log_message(self, *a):
        pass

    def do_GET(self):
        if not self.slow:
            return super().do_GET()
        path = self.translate_path(self.path)
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            for i in range(0, len(data), 16384):
                self.wfile.write(data[i:i + 16384])
                self.wfile.flush()
                time.sleep(self.slow)
        except (BrokenPipeError, ConnectionResetError):
            pass


class LocalServer:
    def __init__(self, directory, slow=0.0):
        handler = type("H", (_Handler,), {"slow": slow})
        self.httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(handler, directory=str(directory)))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()

    def url(self, name):
        return f"http://127.0.0.1:{self.port}/{name}"
