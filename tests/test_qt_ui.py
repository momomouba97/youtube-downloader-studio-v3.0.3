import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QPointF, QRect, Qt, Signal, QObject
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from app.channel_parser import ChannelParser
from app.database import Database
from app.thumbnail_manager import ThumbnailManager, is_allowed_thumbnail_url
from app.video_manager import VideoManager
from app.workers import ImportWorker
from ui.video_list import ROW_HEIGHT, VideoListModel, VideoRowDelegate
from tests.test_video_manager import make_videos


@pytest.fixture
def app(qapp):
    return qapp


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "q.db")
    d.upsert_channel("UC1", "Chan", "https://www.youtube.com/@chan/videos")
    d.upsert_videos("UC1", make_videos())
    yield d
    d.close()


class FakeThumbs(QObject):
    thumbnailReady = Signal(str)
    def get(self, vid, url):
        pm = QPixmap(160, 90); pm.fill(QColor("#3b4a6b")); return pm
    def shutdown(self): pass
    def disk_usage(self): return 0
    def clear(self): pass


def test_model_checkbox_and_flags(app, db):
    vm = VideoManager(db, 50)
    m = VideoListModel(vm); m.reload()
    assert m.rowCount() == 50
    i = m.index(0)
    assert m.data(i, Qt.CheckStateRole) == Qt.Unchecked
    assert m.setData(i, Qt.Checked, Qt.CheckStateRole)
    assert m.data(i, Qt.CheckStateRole) == Qt.Checked
    assert db.get_video(m.data(i, Qt.UserRole + 1)["video_id"])["selected"] == 1
    vm.set_filter("unavailable"); m.reload()
    assert not (m.flags(m.index(0)) & Qt.ItemIsUserCheckable)
    assert not m.setData(m.index(0), Qt.Checked, Qt.CheckStateRole)


def test_delegate_checkbox_hit_test_is_independent_of_thumbnail(app, db):
    vm = VideoManager(db, 50)
    m = VideoListModel(vm); m.reload()
    d = VideoRowDelegate(FakeThumbs())
    opt = QStyleOptionViewItem(); opt.rect = QRect(0, 0, 900, ROW_HEIGHT)
    def click(x, y):
        ev = QMouseEvent(QEvent.MouseButtonRelease, QPointF(x, y), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        return d.editorEvent(ev, m, opt, m.index(0))
    assert click(150, 40) is False                      # thumbnail area: no toggle
    assert m.data(m.index(0), Qt.CheckStateRole) == Qt.Unchecked
    cb = d.checkbox_rect(opt.rect).center()
    assert click(cb.x(), cb.y()) is True                # checkbox: toggles
    assert m.data(m.index(0), Qt.CheckStateRole) == Qt.Checked
    assert click(cb.x(), cb.y()) is True                # and back
    assert m.data(m.index(0), Qt.CheckStateRole) == Qt.Unchecked


def test_delegate_paints_all_states(app, db):
    vm = VideoManager(db, 50)
    for flt in ("all", "unavailable"):
        vm.set_filter(flt)
        m = VideoListModel(vm); m.reload()
        d = VideoRowDelegate(FakeThumbs())
        img = QImage(900, ROW_HEIGHT * 3, QImage.Format_ARGB32); img.fill(QColor("#191C23"))
        p = QPainter(img)
        for r in range(3):
            opt = QStyleOptionViewItem(); opt.rect = QRect(0, r * ROW_HEIGHT, 900, ROW_HEIGHT)
            d.paint(p, opt, m.index(r))
        p.end()
        assert not img.isNull()


def test_import_worker_with_fake_parser(app, db):
    class FakeYDL:
        def __init__(s, o): pass
        def __enter__(s): return s
        def __exit__(s, *a): return False
        def extract_info(s, url, download=False, process=True):
            return {"channel_id": "UC9", "channel": "New Chan",
                    "entries": [{"id": f"w{i:010d}", "title": f"W{i}", "duration": 10} for i in range(300)]}
    w = ImportWorker("@newchan", db, parser=ChannelParser(lambda o: FakeYDL(o)))
    got = {}
    w.imported.connect(lambda cid, t, n, s: got.update(cid=cid, t=t, n=n))
    w.failed.connect(lambda m: got.update(err=m))
    w.start(); assert w.wait(10000)
    app.processEvents()
    assert got == {"cid": "UC9", "t": "New Chan", "n": 300}
    assert db.count_videos("UC9") == 300
    assert db.recent_activity(1)[0]["kind"] == "import"


def test_import_worker_reports_invalid_url(app, db):
    w = ImportWorker("https://evil.com/@x", db)
    errs = []
    w.failed.connect(errs.append)
    w.start(); assert w.wait(5000); app.processEvents()
    assert errs and "youtube.com" in errs[0]


def test_thumbnail_url_allowlist_and_cache(app, tmp_path):
    for ok in ("https://i.ytimg.com/vi/x/mqdefault.jpg", "https://p16-sign.tiktokcdn.com/x.jpg",
               "https://scontent.cdninstagram.com/v/x.jpg", "https://external.fbcdn.net/x.jpg"):
        assert is_allowed_thumbnail_url(ok), ok
    for bad in ("http://i.ytimg.com/vi/x/a.jpg", "file:///etc/passwd", "https://127.0.0.1/a.jpg",
                "https://localhost/a.jpg", "https://192.168.0.9/a.jpg", "https://169.254.169.254/latest", "", "ftp://x/a.jpg"):
        assert not is_allowed_thumbnail_url(bad), bad
    tm = ThumbnailManager(tmp_path / "c", max_mb=50)
    assert tm.get("v", "https://192.168.0.9/a.jpg") is None    # private hosts are never fetched
    pm = QPixmap(10, 10); pm.fill(QColor("red"))
    pm.save(str(tm._path("v1", "https://i.ytimg.com/a.jpg")), "PNG")
    assert tm.get("v1", "https://i.ytimg.com/a.jpg") is not None   # served from disk cache
    assert tm.disk_usage() > 0
    tm.clear(); assert tm.disk_usage() == 0
    tm.shutdown()


def test_main_window_builds_and_close_while_importing(app, db, tmp_path):
    from app.main_window import MainWindow
    from app.settings_manager import SettingsManager
    win = MainWindow(db, SettingsManager(tmp_path / "s.json"), FakeThumbs())
    win.show(); app.processEvents()
    assert win.model.rowCount() == 50 and "Page 1 of 3" in win.page_label.text()
    win.next_btn.click(); app.processEvents()
    assert "Page 2 of 3" in win.page_label.text()
    win.search.setText("Video 05"); app.processEvents()
    assert "10 videos" in win.page_label.text()
    img = win.grab()
    assert not img.isNull()
    win.close()


# ---------------------------------------------------------------- Phase 2: downloads end-to-end
import time
import zipfile
from PySide6.QtWidgets import QMessageBox
from app import downloader as dl
from app.download_manager import COMPLETED, FAILED
from app.settings_manager import SettingsManager
from tests.test_download_manager import ok_fn, wait_until


def _window(tmp_path, db, fn):
    from app.main_window import MainWindow
    s = SettingsManager(tmp_path / "s.json")
    s.set("download_dir", str(tmp_path / "dl"))
    w = MainWindow(db, s, FakeThumbs(), download_fn=fn)
    w.dl_manager._backoff = (0.01,)
    w.dl_manager._ffmpeg_dir = None
    w.show()
    return w


def _select(w, ids):
    for i in ids:
        assert w.manager.toggle(f"vid{i:08d}", True)
    w.refresh()


def test_download_button_flow_end_to_end(app, db, tmp_path):
    w = _window(tmp_path, db, ok_fn)
    assert not w.dl_btn.isEnabled()                        # nothing selected yet
    _select(w, [0, 1, 2])
    assert w.dl_btn.isEnabled() and "(3)" in w.dl_btn.text()
    w.dl_btn.click()
    assert w.stack.currentWidget() is w.downloads_page     # jumped to Downloads
    assert wait_until(lambda: w.dl_manager.counts()[COMPLETED] == 3)
    app.processEvents()
    assert w.completed_page.model.rowCount() == 3 and w.downloads_page.model.rowCount() == 0
    assert "Completed  (3)" in w.nav["Completed"].text() and "Downloads  (0)" in w.nav["Downloads"].text()
    assert w.model.manager.db.get_video("vid00000000")["download_status"] == "completed"
    assert w.downloads_page.overall.value() == 1000 and "Downloaded: 3" in w.downloads_page.stats.text()
    # only the selected videos were downloaded
    assert {r["video_id"] for r in db.list_downloads()} == {"vid00000000", "vid00000001", "vid00000002"}
    # a second click downloads nothing new
    msgs = []
    QMessageBox.information = staticmethod(lambda *a, **k: msgs.append(a[2]))
    w.dl_btn.click()
    assert msgs and "Nothing new" in msgs[0]
    w.close()


def test_failed_page_and_retry_all(app, db, tmp_path):
    state = {"fail": True}

    def fn(url, out_dir, **kw):
        if state["fail"] and url.endswith(("vid00000001", "vid00000002")):
            raise dl.DownloadError("This video is private.", retryable=False)
        return ok_fn(url, out_dir, **kw)

    w = _window(tmp_path, db, fn)
    _select(w, [0, 1, 2])
    w.dl_btn.click()
    assert wait_until(lambda: w.dl_manager.counts()["in_progress"] == 0)
    app.processEvents()
    assert w.failed_page.model.rowCount() == 2 and w.completed_page.model.rowCount() == 1
    t = w.dl_manager.tasks["vid00000001"]
    assert t["error"] == "This video is private."
    state["fail"] = False
    w.failed_page.bar.itemAt(0).widget().click()           # "Retry All Failed"
    assert wait_until(lambda: w.dl_manager.counts()[COMPLETED] == 3)
    app.processEvents()
    assert w.failed_page.model.rowCount() == 0 and w.completed_page.model.rowCount() == 3
    w.close()


def test_context_menu_remove_keeps_file_and_painting(app, db, tmp_path):
    w = _window(tmp_path, db, ok_fn)
    _select(w, [0])
    w.dl_btn.click()
    assert wait_until(lambda: w.dl_manager.counts()[COMPLETED] == 1)
    app.processEvents()
    path = w.dl_manager.tasks["vid00000000"]["file_path"]
    menu = w.completed_page.view.build_menu("vid00000000")
    names = [a.text() for a in menu.actions() if a.text()]
    assert {"Open File", "Show in Finder", "Copy Path", "Copy Video URL"} <= set(names)
    assert not any("Delete" in n for n in names)           # never a silent delete
    img = QImage(900, 84 * 2, QImage.Format_ARGB32)
    p = QPainter(img)
    opt = QStyleOptionViewItem(); opt.rect = QRect(0, 0, 900, 84)
    w.completed_page.delegate.paint(p, opt, w.completed_page.model.index(0))
    p.end()
    [a for a in menu.actions() if a.text().startswith("Remove")][0].trigger()
    assert os.path.isfile(path) and "vid00000000" not in w.dl_manager.tasks
    assert db.get_video("vid00000000")["download_status"] == "none"
    w.close()


def test_active_row_painting_and_pause_resume_buttons(app, db, tmp_path):
    import threading
    gate = threading.Event()

    def fn(url, out_dir, should_abort=None, **kw):
        kw["on_progress"]({"status": "downloading", "percent": 42.0, "speed": 2_500_000, "eta": 75,
                           "downloaded": 40_000_000, "total": 95_000_000})
        end = time.monotonic() + 5
        while not gate.is_set() and time.monotonic() < end:
            r = should_abort()
            if r:
                raise dl.DownloadAborted(r)
            time.sleep(0.01)
        return ok_fn(url, out_dir, should_abort=should_abort, **kw)

    w = _window(tmp_path, db, fn)
    _select(w, [0, 1])
    w.dl_btn.click()
    assert wait_until(lambda: w.dl_manager.tasks["vid00000000"]["percent"] == 42.0)
    img = QImage(900, 84, QImage.Format_ARGB32)
    p = QPainter(img)
    opt = QStyleOptionViewItem(); opt.rect = QRect(0, 0, 900, 84)
    w.downloads_page.delegate.paint(p, opt, w.downloads_page.model.index(0))
    p.end()
    assert w.downloads_page.pause_btn.isEnabled() and not w.downloads_page.resume_btn.isEnabled()
    w.downloads_page.pause_btn.click()
    assert wait_until(lambda: w.dl_manager.counts()["paused"] == 2)
    assert "PAUSED" in w.downloads_page.stats.text() and w.downloads_page.resume_btn.isEnabled()
    gate.set()
    w.downloads_page.resume_btn.click()
    assert wait_until(lambda: w.dl_manager.counts()[COMPLETED] == 2)
    w.close()


def test_zip_export_from_window(app, db, tmp_path, monkeypatch):
    w = _window(tmp_path, db, ok_fn)
    _select(w, [0, 1, 2])
    w.dl_btn.click()
    assert wait_until(lambda: w.dl_manager.counts()[COMPLETED] == 3)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)       # do not block on the result dialog
    files = [t["file_path"] for t in w.dl_manager.tasks.values()]
    w.start_zip(files, str(tmp_path / "exp" / "all.zip"))
    assert wait_until(lambda: not w.zip_worker.isRunning() and (tmp_path / "exp" / "all.zip").exists(), 10)
    wait_until(lambda: any(a["kind"] == "export" for a in db.recent_activity(5)))
    with zipfile.ZipFile(tmp_path / "exp" / "all.zip") as z:
        assert len(z.namelist()) == 3 and z.testzip() is None
    w.close()


def test_closing_during_downloads_asks_and_stops_cleanly(app, db, tmp_path, monkeypatch):
    import threading
    gate = threading.Event()

    def fn(url, out_dir, should_abort=None, **kw):
        end = time.monotonic() + 10
        while not gate.is_set() and time.monotonic() < end:
            r = should_abort()
            if r:
                raise dl.DownloadAborted(r)
            time.sleep(0.01)
        raise dl.DownloadAborted("cancel")

    w = _window(tmp_path, db, fn)
    _select(w, [0, 1, 2])
    w.dl_btn.click()
    assert wait_until(lambda: w.dl_manager.counts()["active"] >= 1)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    assert w.close() is False and w.isVisible()                    # user said No -> keeps running
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    t0 = time.monotonic()
    assert w.close() is True
    assert time.monotonic() - t0 < 5 and w.dl_manager._pool.activeThreadCount() == 0   # worker threads really stopped


def test_settings_page_saves_and_validates(app, db, tmp_path):
    w = _window(tmp_path, db, ok_fn)
    sp = w.settings_page
    sp.parallel.setValue(4); sp.retries.setValue(1)
    sp.quality.setCurrentIndex(sp.quality.findData(720)); sp.fmt.setCurrentIndex(sp.fmt.findData("mkv"))
    sp.template.setText("../evil.%(ext)s"); sp.template.editingFinished.emit()
    assert "path separators" in sp.template_msg.text()
    assert w.settings.get("filename_template") == dl.DEFAULT_TEMPLATE
    sp.template.setText("%(id)s.%(ext)s"); sp.template.editingFinished.emit()
    s2 = SettingsManager(tmp_path / "s.json")                      # persisted to disk
    assert (s2.get("parallel_downloads"), s2.get("retry_attempts"), s2.get("max_quality"), s2.get("format")) == (4, 1, 720, "mkv")
    assert s2.get("filename_template") == "%(id)s.%(ext)s"
    w.close()
