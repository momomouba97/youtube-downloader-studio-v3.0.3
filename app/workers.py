"""Background workers. Network / yt-dlp work never runs on the UI thread."""
from PySide6.QtCore import QThread, Signal

from .channel_parser import ChannelParser, ImportCancelled, ImportFailed, InvalidChannelURL


class ImportWorker(QThread):
    progress = Signal(int)
    imported = Signal(str, str, int, int)   # channel_id, title, saved, skipped
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, url, db, parser=None, parent=None):
        super().__init__(parent)
        self._url, self._db = url, db
        self._parser = parser or ChannelParser()
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            res = self._parser.fetch_channel(self._url, self.progress.emit, lambda: self._cancel)
            self._db.upsert_channel(res.channel_id, res.title, res.url)
            saved = self._db.upsert_videos(res.channel_id, res.videos)
            self._db.log_activity("import", f"Imported {res.title}: {saved} videos")
            self.imported.emit(res.channel_id, res.title, saved, res.skipped)
        except ImportCancelled:
            self.cancelled.emit()
        except (ImportFailed, InvalidChannelURL) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")


class AnalyzeWorker(QThread):
    """Analyse one or more links (single videos, playlists, profiles) off the UI thread."""
    progress = Signal(str)
    analyzed = Signal(object)               # media_info.Analysis
    failed = Signal(str, str)               # url, friendly message
    finishedAll = Signal(int, int)          # ok, failed

    def __init__(self, urls, noplaylist=False, cookies_file="", allow_other=True, analyze_fn=None, parent=None):
        super().__init__(parent)
        self._urls, self._noplaylist = list(urls), noplaylist
        self._cookies, self._allow_other = cookies_file, allow_other
        self._fn = analyze_fn
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        from .channel_parser import ImportCancelled, ImportFailed
        from .media_info import analyze_url
        from .platforms import UnsupportedURL
        fn = self._fn or analyze_url
        ok = bad = 0
        for i, url in enumerate(self._urls, 1):
            if self._cancel:
                break
            self.progress.emit(f"Analysing link {i} of {len(self._urls)}…")
            try:
                res = fn(url, noplaylist=self._noplaylist, cookies_file=self._cookies, allow_other=self._allow_other,
                         on_progress=lambda n, i=i: self.progress.emit(f"Link {i}/{len(self._urls)}: found {n} item(s)…"),
                         should_cancel=lambda: self._cancel)
                self.analyzed.emit(res)
                ok += 1
            except ImportCancelled:
                break
            except (ImportFailed, UnsupportedURL) as exc:
                self.failed.emit(url, str(exc))
                bad += 1
            except Exception as exc:
                self.failed.emit(url, f"Unexpected error: {exc}")
                bad += 1
        self.finishedAll.emit(ok, bad)


class ZipWorker(QThread):
    progress = Signal(int, int, str)        # done_bytes, total_bytes, current file
    created = Signal(dict)                  # {path, count, size, skipped}
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, files, dest, parent=None):
        super().__init__(parent)
        self._files, self._dest = list(files), dest
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        from .zip_manager import ZipCancelled, ZipError, create_zip
        try:
            res = create_zip(self._files, self._dest, self.progress.emit, lambda: self._cancel)
            self.created.emit(res)
        except ZipCancelled:
            self.cancelled.emit()
        except ZipError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")
