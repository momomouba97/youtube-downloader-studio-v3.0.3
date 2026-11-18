"""Download queue with a hard parallel limit. Scheduler state lives on the UI thread only;
workers (QThreadPool) just emit signals, so nothing blocks the interface."""
import errno
import json
import os
import shutil
import re
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from . import downloader as dl
from .resources import tool_path

WAITING, PREPARING, DOWNLOADING, MERGING = "waiting", "preparing", "downloading", "merging"
PAUSED, COMPLETED, FAILED, CANCELLED = "paused", "completed", "failed", "cancelled"
ACTIVE = {PREPARING, DOWNLOADING, MERGING}
PENDING = {WAITING, PAUSED}
IN_PROGRESS = ACTIVE | PENDING
MIN_FREE_BYTES = 500 * 1024 * 1024
MAX_PARALLEL = 10
BACKOFF_SECONDS = (2, 5, 10)


def default_ffmpeg_dir():
    p = tool_path("ffmpeg")
    return str(Path(p).parent) if p else None


class _Worker(QRunnable):
    def __init__(self, mgr, task, cfg, fn):
        super().__init__()
        self.mgr, self.task, self.cfg, self.fn = mgr, dict(task), cfg, fn
        self.setAutoDelete(True)

    def run(self):
        vid, mgr, cfg = self.task["video_id"], self.mgr, self.cfg
        get_abort = lambda: mgr._abort.get(vid)
        last = {"t": 0.0, "s": ""}

        def on_progress(d):
            now = time.monotonic()
            if d.get("status") == last["s"] and now - last["t"] < 0.2:
                return
            last["t"], last["s"] = now, d.get("status", "")
            mgr._progress.emit(vid, dict(d))

        max_attempts = cfg["retries"] + 1
        attempt = 0
        while True:
            attempt += 1
            try:
                res = self.fn(self.task["url"], cfg["out_dir"], profile=cfg["profile"], template=cfg["template"],
                              ffmpeg_dir=cfg["ffmpeg_dir"], on_progress=on_progress, should_abort=get_abort)
                res["attempts"] = attempt
                mgr._done.emit(vid, "completed", res)
                return
            except dl.DownloadAborted as a:
                mgr._done.emit(vid, a.reason, {"attempts": attempt})
                return
            except dl.DownloadError as e:
                msg, retryable = str(e), e.retryable
            except OSError as e:
                if e.errno == errno.ENOSPC:
                    msg, retryable = "No space left on the disk.", False
                else:
                    msg, retryable = dl.classify_error(e)
            except Exception as e:  # never let one video crash the pool
                msg, retryable = dl.classify_error(e)
            if not retryable or attempt >= max_attempts:
                mgr._done.emit(vid, "failed", {"error": msg, "attempts": attempt})
                return
            wait = cfg["backoff"][min(attempt - 1, len(cfg["backoff"]) - 1)]
            mgr._progress.emit(vid, {"status": "preparing", "percent": 0.0, "speed": 0, "eta": None,
                                     "note": f"Retry {attempt}/{cfg['retries']} in {wait}s: {msg}"})
            end = time.monotonic() + wait
            while time.monotonic() < end:
                reason = get_abort()
                if reason:
                    mgr._done.emit(vid, reason, {"attempts": attempt})
                    return
                time.sleep(0.05)


class DownloadManager(QObject):
    taskAdded = Signal(str)
    taskChanged = Signal(str)
    taskRemoved = Signal(str)
    countsChanged = Signal()
    message = Signal(str, str)           # level ("info"|"warning"|"error"), text
    _progress = Signal(str, dict)        # worker -> UI thread
    _done = Signal(str, str, dict)

    def __init__(self, db, settings, default_dir, download_fn=None, ffmpeg_dir="auto",
                 backoff=BACKOFF_SECONDS, min_free_bytes=MIN_FREE_BYTES, parent=None):
        super().__init__(parent)
        self.db, self.settings = db, settings
        self.default_dir = str(default_dir)
        self._fn = download_fn or dl.download_video
        self._ffmpeg_dir = default_ffmpeg_dir() if ffmpeg_dir == "auto" else ffmpeg_dir
        self._backoff = tuple(backoff)
        self._min_free = min_free_bytes
        self.tasks = OrderedDict()
        self._waiting, self._held, self._running = deque(), [], set()
        self._abort = {}
        self._paused = False
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(MAX_PARALLEL)
        self._progress.connect(self._on_progress)
        self._done.connect(self._on_done)

    # ---- configuration ---------------------------------------------------------
    def download_dir(self):
        return self.settings.get("download_dir") or self.default_dir

    def parallel(self):
        return max(1, min(MAX_PARALLEL, int(self.settings.get("parallel_downloads"))))

    def default_profile(self):
        """Profile built from the current settings (frozen into each task when it is queued)."""
        g = self.settings.get
        return dl.Profile.from_dict({
            "kind": g("download_kind"), "container": g("format"), "max_height": g("max_quality"),
            "audio_format": g("audio_format"), "audio_quality": g("audio_quality"),
            "embed_metadata": g("embed_metadata"), "embed_thumbnail": g("embed_thumbnail"),
            "limit_rate_kbps": g("speed_limit_kbps"), "fragments": g("fragments"),
            "cookies_file": g("cookies_file"), "allow_other_sites": g("allow_other_sites")})

    def out_dir_for(self, task):
        """<download folder>[/<platform>]/<channel|uploader|playlist> according to the folder-structure setting."""
        structure = self.settings.get("folder_structure")
        pl = dl.safe_dirname(task.get("playlist_title")) if task.get("playlist_title") else ""
        if task.get("dest_dir"):                                  # explicit "Save to" folder from Add Link
            base = Path(task["dest_dir"])
            return base / pl if pl and structure != "flat" else base
        base = Path(self.download_dir())
        who = task.get("channel_title") or task.get("uploader") or task.get("platform") or "Downloads"
        if structure == "flat":
            return base
        if structure == "playlist" and pl:
            return base / pl
        if structure == "platform_channel":
            parts = [dl.safe_dirname(task.get("platform") or "Web")]
            if task.get("uploader") or task.get("channel_title"):
                parts.append(dl.safe_dirname(task.get("channel_title") or task["uploader"]))
            if pl:
                parts.append(pl)
            return base.joinpath(*parts)
        return base / dl.safe_dirname(who) / pl if pl else base / dl.safe_dirname(who)

    def _cfg(self, task):
        try:
            template = dl.validate_template(self.settings.get("filename_template"))
        except ValueError:
            template = dl.DEFAULT_TEMPLATE
        prefix = task.get("file_prefix") or ""
        if prefix and not re.fullmatch(r"[0-9]{1,5} - ", prefix):
            prefix = ""                                           # only our own "001 - " numbering is allowed
        profile = dl.Profile.from_dict(task.get("profile")) if task.get("profile") else self.default_profile()
        return {"out_dir": str(self.out_dir_for(task)), "template": prefix + template,
                "retries": int(self.settings.get("retry_attempts")), "profile": profile,
                "ffmpeg_dir": self._ffmpeg_dir, "backoff": self._backoff}

    # ---- history -----------------------------------------------------------------
    def load_history(self):
        for r in self.db.list_downloads():
            if r["video_id"] in self.tasks:
                continue
            row = dict(r)
            try:
                row["profile"] = json.loads(row.get("profile") or "{}") or None
            except ValueError:
                row["profile"] = None
            t = self._task(row, r["status"], session=False)
            t.update(file_path=r["file_path"], size=r["size"], height=r["height"], error=r["error"],
                     attempts=r["attempts"], finished_at=r["finished_at"], kind=row.get("kind") or "video",
                     label=row.get("label") or "", percent=100.0 if r["status"] == COMPLETED else 0.0)
            self.tasks[r["video_id"]] = t
        self.countsChanged.emit()

    @staticmethod
    def _task(row, status, session=True):
        return {"video_id": row["video_id"], "title": row["title"], "thumbnail": row.get("thumbnail", ""),
                "url": row["url"], "channel_id": row.get("channel_id", ""),
                "channel_title": row.get("channel_title", ""), "status": status, "percent": 0.0,
                "speed": 0, "eta": None, "downloaded": 0, "total": 0, "attempts": 0, "error": "",
                "note": "", "file_path": "", "size": 0, "height": 0, "finished_at": 0.0, "session": session,
                "platform": row.get("platform", "") or ("YouTube" if row.get("channel_id") else ""),
                "uploader": row.get("uploader", ""), "playlist_title": row.get("playlist_title", ""),
                "kind": row.get("kind", "video"), "label": row.get("label", ""), "profile": row.get("profile"),
                "file_prefix": row.get("file_prefix", ""), "dest_dir": row.get("dest_dir", "")}

    # ---- queue operations (UI thread) ----------------------------------------------
    def enqueue(self, rows, profile=None, dest_dir="", numbering=False):
        """Queue items (sqlite rows or dicts). `profile` (Profile/dict) overrides the settings for this batch;
        `dest_dir` overrides the download folder; `numbering` prefixes playlist items with 001 - , 002 - …
        Returns (queued, skipped)."""
        rows = [dict(r) for r in rows]
        prof = (dl.Profile.from_dict(profile) if isinstance(profile, dict) else profile) or self.default_profile()
        prof_dict = prof.to_dict()
        try:
            Path(self.download_dir()).mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(self.download_dir()).free
        except OSError as exc:
            self.message.emit("error", f"Cannot use the download folder: {exc.strerror or exc}")
            return 0, len(rows)
        if free < self._min_free:
            self.message.emit("error", f"Not enough free disk space ({free // 1048576} MB free). "
                                       f"Free some space or change the download folder.")
            return 0, len(rows)
        titles = self.db.channel_titles()
        queued = skipped = 0
        for r in rows:
            vid = r["video_id"]
            old = self.tasks.get(vid)
            if old and old["status"] in IN_PROGRESS:
                skipped += 1
                continue
            if old and old["status"] == COMPLETED and old["file_path"] and os.path.isfile(old["file_path"]):
                skipped += 1
                continue
            r.setdefault("channel_title", titles.get(r.get("channel_id", ""), ""))
            t = self._task(r, WAITING)
            t["profile"], t["kind"], t["label"], t["dest_dir"] = prof_dict, prof.kind, prof.label(), dest_dir or ""
            if numbering and r.get("playlist_index"):
                t["file_prefix"] = f"{int(r['playlist_index']):03d} - "
            self.tasks[vid] = t
            self.tasks.move_to_end(vid)
            self._waiting.append(vid)
            queued += 1
            self.taskAdded.emit(vid)
        self.countsChanged.emit()
        self._pump()
        return queued, skipped

    def _pump(self):
        while not self._paused and self._waiting and len(self._running) < self.parallel():
            vid = self._waiting.popleft()
            t = self.tasks[vid]
            t["status"], t["note"] = PREPARING, ""
            self._abort[vid] = None
            self._running.add(vid)
            self._pool.start(_Worker(self, t, self._cfg(t), self._fn))
            self.taskChanged.emit(vid)
        self.countsChanged.emit()

    def _on_progress(self, vid, d):
        t = self.tasks.get(vid)
        if not t or vid not in self._running:
            return
        st = d.get("status")
        if st in (PREPARING, DOWNLOADING, MERGING) and t["status"] in ACTIVE:
            t["status"] = st
        for k in ("percent", "speed", "eta", "downloaded", "total", "note"):
            if k in d:
                t[k] = d[k]
        self.taskChanged.emit(vid)

    def _on_done(self, vid, outcome, payload):
        self._running.discard(vid)
        self._abort.pop(vid, None)
        t = self.tasks.get(vid)
        if t is None:
            self._pump()
            return
        t["speed"], t["eta"], t["note"] = 0, None, ""
        t["attempts"] = payload.get("attempts", t["attempts"])
        if outcome == "completed":
            t.update(status=COMPLETED, percent=100.0, file_path=payload["file_path"], size=payload["size"],
                     height=payload["height"], error="", finished_at=time.time())
            if t["kind"] == "video" and not t["label"]:
                t["label"] = "VIDEO"
            self.db.record_download({**t, "status": COMPLETED})
            self.db.log_activity("download", f"Downloaded: {t['title']}")
        elif outcome == "failed":
            t.update(status=FAILED, error=payload.get("error", "Unknown error"), finished_at=time.time())
            self.db.record_download({**t, "status": FAILED})
            self.db.log_activity("failed", f"Failed: {t['title']} ({t['error']})")
        elif outcome == "pause":
            t["status"] = PAUSED
            self._held.append(vid)
        else:
            t["status"] = CANCELLED
        self.taskChanged.emit(vid)
        self.countsChanged.emit()
        self._pump()

    def pause_all(self):
        self._paused = True
        for vid in list(self._waiting):
            self.tasks[vid]["status"] = PAUSED
            self._held.append(vid)
            self.taskChanged.emit(vid)
        self._waiting.clear()
        for vid in self._running:
            self._abort[vid] = "pause"
        self.countsChanged.emit()

    def resume_all(self):
        self._paused = False
        for vid in self._held:
            t = self.tasks.get(vid)
            if t and t["status"] == PAUSED:
                t["status"] = WAITING
                self._waiting.append(vid)
                self.taskChanged.emit(vid)
        self._held.clear()
        self._pump()

    def is_paused(self):
        return self._paused

    def cancel(self, vid):
        t = self.tasks.get(vid)
        if not t:
            return
        if vid in self._running:
            self._abort[vid] = "cancel"
            return
        if vid in self._waiting:
            self._waiting.remove(vid)
        if vid in self._held:
            self._held.remove(vid)
        if t["status"] in PENDING:
            t["status"] = CANCELLED
            self.taskChanged.emit(vid)
            self.countsChanged.emit()

    def cancel_all(self):
        for vid in list(self._waiting) + list(self._held) + list(self._running):
            self.cancel(vid)

    def prioritize(self, vid):
        """Move a waiting item to the front of the queue."""
        if vid in self._waiting:
            self._waiting.remove(vid)
            self._waiting.appendleft(vid)
            self.taskChanged.emit(vid)
            return True
        return False

    def retry(self, vid):
        t = self.tasks.get(vid)
        if not t or t["status"] not in (FAILED, CANCELLED):
            return False
        t.update(status=WAITING, error="", percent=0.0, session=True)
        self._waiting.append(vid)
        self.tasks.move_to_end(vid)
        self.taskChanged.emit(vid)
        self._pump()
        return True

    def retry_failed(self):
        ids = [v for v, t in self.tasks.items() if t["status"] == FAILED]
        for v in ids:
            self.retry(v)
        return len(ids)

    def remove(self, vid):
        """Forget a finished task. Files on disk are never touched here."""
        t = self.tasks.get(vid)
        if not t or t["status"] in IN_PROGRESS:
            return False
        if t["status"] in (COMPLETED, FAILED):
            self.db.remove_download(vid)
        del self.tasks[vid]
        self.taskRemoved.emit(vid)
        self.countsChanged.emit()
        return True

    def clear(self, statuses):
        ids = [v for v, t in self.tasks.items() if t["status"] in statuses]
        for v in ids:
            self.remove(v)
        return len(ids)

    def clear_completed(self):
        return self.clear({COMPLETED, CANCELLED})

    # ---- stats -----------------------------------------------------------------------
    def counts(self):
        c = {s: 0 for s in (WAITING, PREPARING, DOWNLOADING, MERGING, PAUSED, COMPLETED, FAILED, CANCELLED)}
        for t in self.tasks.values():
            c[t["status"]] += 1
        c["active"] = c[PREPARING] + c[DOWNLOADING] + c[MERGING]
        c["in_progress"] = c["active"] + c[WAITING] + c[PAUSED]
        return c

    def summary(self):
        """Totals for tasks queued in this session (drives the overall progress bar)."""
        s = [t for t in self.tasks.values() if t["session"] and t["status"] != CANCELLED]
        total = len(s)
        done = sum(1 for t in s if t["status"] == COMPLETED)
        failed = sum(1 for t in s if t["status"] == FAILED)
        pct = (sum(100.0 if t["status"] in (COMPLETED, FAILED) else t["percent"] for t in s) / total) if total else 0.0
        return {"total": total, "completed": done, "failed": failed,
                "remaining": total - done - failed, "percent": pct}

    def has_active_work(self):
        return bool(self._running or self._waiting)

    def shutdown(self, timeout_ms=8000):
        """Stop everything quickly and wait for worker threads (called when the app quits)."""
        self._waiting.clear()
        self._held.clear()
        for vid in self._running:
            self._abort[vid] = "cancel"
        self._pool.waitForDone(timeout_ms)
