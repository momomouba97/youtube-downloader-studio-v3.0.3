import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time
import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QMimeData, QUrl, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QStyleOptionViewItem

from app import downloader as dl
from app.database import Database
from app.download_manager import COMPLETED
from app.media_info import Analysis, MediaEntry
from app.settings_manager import SettingsManager
from tests.test_download_manager import wait_until
from tests.test_qt_ui import FakeThumbs
from tests.test_video_manager import make_videos


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "p3.db")
    d.upsert_channel("UC1", "Chan", "https://www.youtube.com/@chan/videos")
    d.upsert_videos("UC1", make_videos(30))
    yield d
    d.close()


FORMATS = [
    {"format_id": "140", "ext": "m4a", "height": 0, "vcodec": "none", "acodec": "mp4a", "filesize": 3_000_000, "tbr": 128, "fps": 0, "width": 0},
    {"format_id": "136", "ext": "mp4", "height": 720, "vcodec": "avc1", "acodec": "none", "filesize": 30_000_000, "tbr": 0, "fps": 30, "width": 1280},
    {"format_id": "137", "ext": "mp4", "height": 1080, "vcodec": "avc1", "acodec": "none", "filesize": 80_000_000, "tbr": 0, "fps": 30, "width": 1920},
]


def entry(i, plat="TikTok", **kw):
    d = dict(key=f"tiktok:{i}", raw_id=str(i), title=f"Clip {i}", url=f"https://www.tiktok.com/@alice/video/{i}",
             thumbnail="", duration=60 + i, uploader="alice", platform=plat, playlist_title="Alice clips",
             playlist_index=i)
    d.update(kw)
    return MediaEntry(**d)


def fake_analyze(url, **kw):
    if "bad" in url:
        from app.channel_parser import ImportFailed
        raise ImportFailed("This site requires you to be logged in.")
    if "single" in url:
        e = MediaEntry(key="abc", raw_id="abc", title="Solo video", url="https://www.youtube.com/watch?v=abc",
                       duration=100, uploader="Chan", platform="YouTube")
        return Analysis(url, "video", "Solo video", "Chan", "YouTube", [e], FORMATS)
    es = [entry(i) for i in range(1, 6)]
    es[3].available = False
    return Analysis(url, "playlist", "Alice clips", "alice", "TikTok", es)


def rec_fn(calls):
    def fn(url, out_dir, profile=None, template="", **kw):
        calls.append({"url": url, "out_dir": str(out_dir), "profile": profile, "template": template})
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "f" + url.rsplit("/", 1)[-1].replace("=", "") + "." + profile.extension)
        open(p, "wb").write(b"x" * 100)
        return {"file_path": p, "size": 100, "height": 0}
    return fn


def window(tmp_path, db, fn=None, calls=None):
    from app.main_window import MainWindow
    s = SettingsManager(tmp_path / "s.json")
    s.set("download_dir", str(tmp_path / "dl"))
    w = MainWindow(db, s, FakeThumbs(), download_fn=fn or rec_fn(calls if calls is not None else []),
                   analyze_fn=fake_analyze)
    w.dl_manager._ffmpeg_dir = None
    w.dl_manager._backoff = (0.01,)
    w.show()
    return w


def analyze(w, text):
    lp = w.link_page
    lp.url_edit.setPlainText(text)
    lp.analyze()
    assert wait_until(lambda: lp.worker is not None and not lp.worker.isRunning() and lp.analyze_btn.isEnabled(), 10)
    QApplication.processEvents()


def test_pages_and_start_on_dashboard(qapp, db, tmp_path):
    w = window(tmp_path, db)
    assert list(w.pages) == ["Dashboard", "Add Link", "Library", "Downloads", "Completed", "Failed", "Player", "Settings"]
    assert w.stack.currentWidget() is w.dashboard and w.nav["Dashboard"].isChecked()
    for name in w.pages:
        w._goto(name)
        assert w.stack.currentWidget() is w.pages[name]
    w.close()


def test_playlist_analysis_selection_and_audio_download(qapp, db, tmp_path):
    calls = []
    w = window(tmp_path, db, calls=calls)
    analyze(w, "https://www.tiktok.com/@alice")
    lp = w.link_page
    assert lp.model.rowCount() == 5 and len(lp.model.checked_entries()) == 4       # 1 unavailable is never ticked
    assert lp.dl_btn.isEnabled() and "4" in lp.dl_btn.text() and "TikTok" in lp.summary.text()
    lp.model.set_all(False)
    assert not lp.dl_btn.isEnabled()
    lp.filter.setText("Clip 2")
    assert lp.model.rowCount() == 1
    lp.filter.setText("")
    lp.model.set_all(True)
    assert lp.model.setData(lp.model.index(0), Qt.Unchecked, Qt.CheckStateRole)     # untick one
    assert len(lp.model.checked_entries()) == 3
    assert not lp.model.setData(lp.model.index(3), Qt.Checked, Qt.CheckStateRole)   # unavailable can't be ticked
    lp.kind_box.setCurrentIndex(lp.kind_box.findData("audio"))
    lp.fmt_box.setCurrentIndex(lp.fmt_box.findData("mp3"))
    lp.q_box.setCurrentIndex(lp.q_box.findData(256))
    lp.speed.setValue(300); lp.frag.setValue(6)
    assert "Estimated size" in lp.estimate.text()                                   # durations x bitrate
    lp.dl_btn.click()
    assert w.stack.currentWidget() is w.downloads_page
    assert wait_until(lambda: w.dl_manager.counts()[COMPLETED] == 3)
    assert len(calls) == 3
    pr = calls[0]["profile"]
    assert (pr.kind, pr.audio_format, pr.audio_quality, pr.limit_rate_kbps, pr.fragments) == ("audio", "mp3", 256, 300, 6)
    assert sorted(c["template"].split("%")[0] for c in calls) == ["002 - ", "003 - ", "005 - "]    # playlist numbering kept
    assert all(c["out_dir"].endswith(os.path.join("alice", "Alice clips")) for c in calls)
    t = w.dl_manager.tasks["tiktok:2"]
    assert t["platform"] == "TikTok" and t["label"] == "MP3 256k" and t["file_path"].endswith(".mp3")
    # remembered for next time and visible in Settings
    assert w.settings.get("download_kind") == "audio" and w.settings.get("audio_quality") == 256
    w._goto("Settings")
    assert w.settings_page.kind.currentData() == "audio" and w.settings_page.speed.value() == 300
    w.close()


def test_single_video_quality_list_estimate_and_formats(qapp, db, tmp_path):
    w = window(tmp_path, db)
    analyze(w, "https://www.youtube.com/watch?v=single")
    lp = w.link_page
    heights = [lp.q_box.itemData(i) for i in range(lp.q_box.count())]
    assert heights == [0, 1080, 720]                                  # Best + only what the video offers
    lp.q_box.setCurrentIndex(lp.q_box.findData(720))
    assert "~31.5 MB" in lp.estimate.text()                           # 30 MB video + 3 MB audio
    lp.q_box.setCurrentIndex(lp.q_box.findData(0))
    assert "~" in lp.estimate.text()
    lp.kind_box.setCurrentIndex(lp.kind_box.findData("audio"))
    assert [lp.fmt_box.itemData(i) for i in range(lp.fmt_box.count())] == ["mp3", "m4a", "opus", "flac", "wav", "ogg"]
    lp.fmt_box.setCurrentIndex(lp.fmt_box.findData("flac"))
    assert not lp.q_box.isEnabled() and lp.profile().audio_format == "flac"
    lp.fmt_box.setCurrentIndex(lp.fmt_box.findData("mp3"))
    assert lp.q_box.isEnabled()
    lp.kind_box.setCurrentIndex(lp.kind_box.findData("video"))
    assert [lp.fmt_box.itemData(i) for i in range(lp.fmt_box.count())] == ["mp4", "mkv", "webm"]
    p = lp.profile()
    assert p.kind == "video" and p.container == "mp4" and p.max_height == 0
    w.close()


def test_failed_link_is_reported_and_others_still_load(qapp, db, tmp_path):
    w = window(tmp_path, db)
    analyze(w, "https://www.instagram.com/bad/\nhttps://www.tiktok.com/@alice")
    lp = w.link_page
    assert lp.model.rowCount() == 5 and "1 link(s) failed" in lp.status.text() and "logged in" in lp.status.text()
    assert any("logged in" in t.label.text() for t in w.toasts.toasts)
    w.close()


def test_drag_drop_and_clipboard_detection(qapp, db, tmp_path):
    w = window(tmp_path, db)
    mime = QMimeData()
    mime.setUrls([QUrl("https://www.tiktok.com/@alice"), QUrl("file:///tmp/x.mp4")])
    mime.setText("see https://vimeo.com/123 and https://www.tiktok.com/@alice")
    assert w.urls_from_mime(mime) == ["https://www.tiktok.com/@alice", "https://vimeo.com/123"]
    w.open_links(["https://www.tiktok.com/@alice"])
    assert w.stack.currentWidget() is w.link_page
    assert wait_until(lambda: w.link_page.model.rowCount() == 5)
    # clipboard
    cb = QApplication.clipboard()
    cb.setText("nothing here")
    assert w._check_clipboard() is None
    cb.setText("https://example.org/not-in-list")
    assert w._check_clipboard() is None                               # only known platforms trigger a prompt
    cb.setText("https://vm.tiktok.com/ZMabc/")
    toast = w._check_clipboard()
    assert toast is not None and "TikTok" in toast.label.text()
    assert w._check_clipboard() is None                               # same link: no repeat prompt
    toast.action_btn.click()
    assert w.stack.currentWidget() is w.link_page and "vm.tiktok.com" in w.link_page.url_edit.toPlainText()
    w.settings.set("detect_clipboard", False)
    cb.setText("https://www.instagram.com/reel/XYZ/")
    assert w._check_clipboard() is None
    w.close()


def test_toasts_are_coalesced_and_respect_settings(qapp, db, tmp_path):
    w = window(tmp_path, db)
    w.queue_entries([entry(i) for i in range(1, 5)], dl.Profile(), "", False)
    assert wait_until(lambda: w.dl_manager.counts()[COMPLETED] == 4)
    w.toasts.toasts.clear()
    w.flush_toasts()
    assert len(w.toasts.toasts) == 1 and "4 downloads completed" in w.toasts.toasts[0].label.text()
    w.toasts.toasts[0].action_btn.click()
    assert w.stack.currentWidget() is w.completed_page
    w.settings.set("notify_completed", False)
    w._done_titles = ["x"]
    n = len(w.toasts.toasts)
    w.flush_toasts()
    assert len(w.toasts.toasts) == n
    w.close()


def test_theme_and_accent_switch_live(qapp, db, tmp_path):
    w = window(tmp_path, db)
    dark_qss = w.styleSheet()
    sp = w.settings_page
    sp.theme.setCurrentIndex(sp.theme.findData("light"))
    sp.accent.setCurrentIndex(sp.accent.findData("#3B82F6"))
    assert w.settings.get("theme") == "light" and w.styleSheet() != dark_qss and "#3B82F6" in w.styleSheet()
    assert w.delegate.c["card"] == "#FFFFFF" and w.completed_page.delegate.c["accent"] == "#3B82F6"
    assert w.link_page.delegate.c["bg"] == "#F4F5F7"
    assert SettingsManager(tmp_path / "s.json").get("theme") == "light"
    w.close()


def test_dashboard_numbers_and_navigation(qapp, db, tmp_path):
    w = window(tmp_path, db)
    w.manager.toggle("vid00000000", True)
    w.queue_entries([entry(1), entry(2)], dl.Profile(), "", False)
    assert wait_until(lambda: w.dl_manager.counts()[COMPLETED] == 2)
    w.dashboard.refresh()
    v = {k: c.value.text() for k, c in w.dashboard.cards.items()}
    assert v["videos"] == "30" and v["selected"] == "1" and v["completed"] == "2" and v["active"] == "0" and v["failed"] == "0"
    assert v["storage"] == "200.0 B" or v["storage"].endswith("B")
    assert any("Downloaded" in w.dashboard.activity.item(i).text() for i in range(w.dashboard.activity.count()))
    w.dashboard.cards["failed"].clicked.emit()
    assert w.stack.currentWidget() is w.failed_page
    w.close()


def test_completed_filters_sort_and_totals(qapp, db, tmp_path):
    w = window(tmp_path, db)
    w.queue_entries([entry(1), entry(2)], dl.Profile(kind="audio", audio_format="mp3"), "", False)
    w.queue_entries([entry(3, plat="Facebook", key="facebook:3", title="Zebra video")], dl.Profile(), "", False)
    assert wait_until(lambda: w.dl_manager.counts()[COMPLETED] == 3)
    QApplication.processEvents()
    cp = w.completed_page
    assert cp.model.rowCount() == 3 and "3 file(s)" in cp.totals.text()
    assert [cp.platform.itemData(i) for i in range(cp.platform.count())] == ["", "Facebook", "TikTok"]
    cp.platform.setCurrentIndex(cp.platform.findData("TikTok")); assert cp.model.rowCount() == 2
    cp.platform.setCurrentIndex(0)
    cp.kind.setCurrentIndex(cp.kind.findData("video")); assert cp.model.rowCount() == 1
    cp.kind.setCurrentIndex(0)
    cp.search.setText("zebra"); assert cp.model.rowCount() == 1
    cp.search.setText("")
    cp.sort.setCurrentIndex(cp.sort.findData("name"))
    titles = [cp.model.data(cp.model.index(i)) for i in range(3)]
    assert titles == sorted(titles, key=str.lower)
    w.close()


def test_player_queue_logic_and_play_from_completed(qapp, db, tmp_path):
    pytest.importorskip("PySide6.QtMultimedia")
    from tests.helpers import make_test_video
    from app.resources import tool_path
    if not tool_path("ffmpeg"):
        pytest.skip("ffmpeg not available")
    clip = tmp_path / "clip.mp4"; assert make_test_video(clip, seconds=2)
    audio = tmp_path / "song.mp3"
    import subprocess
    subprocess.run([tool_path("ffmpeg"), "-y", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2",
                    str(audio)], check=True, stdin=subprocess.DEVNULL)
    w = window(tmp_path, db)
    pp = w.player_page
    assert hasattr(pp, "set_queue")
    pp.player.play = lambda: None                                      # no audio device needed for the logic test
    pp.set_queue([{"title": "Clip", "path": str(clip)}, {"title": "Song", "path": str(audio)}, {"title": "Gone", "path": "/nope.mp4"}])
    assert pp.index == 0 and pp.queue.count() == 3 and pp.next_btn.isEnabled()
    if not wait_until(lambda: pp.player.duration() > 0, 8):
        pytest.skip("this machine's media backend could not load the test clip")
    assert pp.cover.isHidden()                                         # video: no cover art
    pp.next()
    assert pp.index == 1 and pp.title.text() == "Song" and not pp.cover.isHidden()   # audio: cover shown
    pp.next()
    assert pp.index == 2 and "no longer exists" in pp.message.text()
    pp.speed.setCurrentIndex(pp.speed.findData(1.5)); assert abs(pp.player.playbackRate() - 1.5) < 1e-6
    pp.vol.setValue(30); assert abs(pp.audio.volume() - 0.3) < 0.02
    pp.toggle_mute(); assert pp.audio.isMuted() and pp.mute_btn.text() == "🔇"
    # double-click / "Play in App" on a completed task opens the player on that file
    w.dl_manager.tasks["v"] = w.dl_manager._task({"video_id": "v", "title": "From list", "url": "https://x/1"}, COMPLETED)
    w.dl_manager.tasks["v"].update(file_path=str(clip), finished_at=time.time() + 5)
    w.play_task("v")
    assert w.stack.currentWidget() is w.player_page and pp.title.text() == "From list"
    menu = w.completed_page.view.build_menu("v")
    assert "Play in App" in [a.text() for a in menu.actions()]
    w.close()


def test_waiting_items_can_move_to_top(qapp, db, tmp_path):
    import threading
    gate = threading.Event()

    def fn(url, out_dir, profile=None, should_abort=None, **kw):
        gate.wait(5)
        return rec_fn([])(url, out_dir, profile=profile)

    w = window(tmp_path, db, fn=fn)
    w.settings.set("parallel_downloads", 1)
    w.queue_entries([entry(i) for i in range(1, 4)], dl.Profile(), "", False)
    assert wait_until(lambda: w.dl_manager.counts()["active"] == 1)
    menu = w.downloads_page.view.build_menu("tiktok:3")
    names = [a.text() for a in menu.actions()]
    assert "Move to Top of Queue" in names and "Cancel" in names
    [a for a in menu.actions() if a.text() == "Move to Top of Queue"][0].trigger()
    assert list(w.dl_manager._waiting)[0] == "tiktok:3"
    gate.set()
    assert wait_until(lambda: w.dl_manager.counts()[COMPLETED] == 3)
    w.close()


def test_dashboard_free_space_works_before_download_folder_exists(qapp, db, tmp_path):
    w = window(tmp_path, db)
    assert not (tmp_path / "dl").exists()
    w.dashboard.refresh()
    assert w.dashboard.cards["free"].value.text() not in ("0 B", "")
    assert w.link_page.dest_label.text().startswith("Default download folder")
    w.close()
