"""Add Link page: paste video / playlist / profile links from many platforms, analyse them, tick the items
you want, choose video or audio + format + quality, then queue them."""
import dataclasses

from PySide6.QtCore import (QAbstractListModel, QEvent, QModelIndex, QRect, QSize, Qt, Signal)
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListView, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QSpinBox, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from app import downloader as dl
from app.formatting import format_size
from app.media_info import estimate_size
from app.platforms import extract_urls
from app.video_manager import format_duration
from app.workers import AnalyzeWorker
from ui.styles import palette

ROLE_ENTRY = Qt.UserRole + 1
ROW_HEIGHT = 84

VIDEO_FORMATS = [("MP4 — best compatibility", "mp4"), ("MKV — flexible container", "mkv"), ("WebM — open format", "webm")]
AUDIO_FORMAT_LABELS = [("MP3", "mp3"), ("M4A (AAC)", "m4a"), ("Opus", "opus"), ("FLAC — lossless", "flac"),
                       ("WAV — uncompressed", "wav"), ("OGG (Vorbis)", "ogg")]
HEIGHT_LABELS = {2160: "2160p (4K)", 1440: "1440p (2K)", 1080: "1080p (Full HD)", 720: "720p (HD)",
                 480: "480p", 360: "360p"}


def links_from_text(text):
    """Links in pasted text; bare domains on their own line (tiktok.com/@x) get https:// added."""
    urls = extract_urls(text)
    for line in (text or "").splitlines():
        t = line.strip()
        if t and "://" not in t and " " not in t and "." in t and "/" in t and f"https://{t}" not in urls:
            urls.append(f"https://{t}")
    return urls


def _checked(v):
    return v == Qt.Checked or v == 2 or getattr(v, "value", None) == 2


class EntryModel(QAbstractListModel):
    selectionChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries, self._rows, self._filter = [], [], ""

    def _rebuild(self):
        f = self._filter.lower()
        self._rows = [e for e in self.entries if not f or f in e.title.lower() or f in e.uploader.lower()]

    def clear(self):
        self.beginResetModel()
        self.entries, self._rows = [], []
        self.endResetModel()
        self.selectionChanged.emit()

    def add_analysis(self, analysis):
        known = {e.key for e in self.entries}
        new = [e for e in analysis.entries if e.key not in known]
        self.beginResetModel()
        self.entries.extend(new)
        self._rebuild()
        self.endResetModel()
        self.selectionChanged.emit()
        return len(new)

    def set_filter(self, text):
        self.beginResetModel()
        self._filter = text or ""
        self._rebuild()
        self.endResetModel()

    def set_all(self, checked):
        """Tick / untick everything currently visible (unavailable items are never ticked)."""
        for e in self._rows:
            e.checked = bool(checked) and e.available
        if self._rows:
            self.dataChanged.emit(self.index(0), self.index(len(self._rows) - 1), [Qt.CheckStateRole])
        self.selectionChanged.emit()

    def checked_entries(self):
        return [e for e in self.entries if e.checked and e.available]

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        e = self._rows[index.row()]
        if role == Qt.DisplayRole:
            return e.title
        if role == Qt.CheckStateRole:
            return Qt.Checked if e.checked and e.available else Qt.Unchecked
        if role == ROLE_ENTRY:
            return e
        return None

    def flags(self, index):
        f = Qt.ItemIsEnabled
        if index.isValid() and self._rows[index.row()].available:
            f |= Qt.ItemIsUserCheckable
        return f

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.CheckStateRole or not index.isValid():
            return False
        e = self._rows[index.row()]
        if not e.available:
            return False
        e.checked = _checked(value)
        self.dataChanged.emit(index, index, [Qt.CheckStateRole])
        self.selectionChanged.emit()
        return True

    def thumbnail_ready(self, key):
        for i, e in enumerate(self._rows):
            if e.key == key:
                ix = self.index(i)
                self.dataChanged.emit(ix, ix)
                return


class EntryDelegate(QStyledItemDelegate):
    THUMB = QSize(112, 63)

    def __init__(self, thumbs, theme="dark", accent="#E63946", parent=None):
        super().__init__(parent)
        self.thumbs = thumbs
        self.set_theme(theme, accent)

    def set_theme(self, theme, accent):
        self.c = palette(theme, accent)

    @staticmethod
    def checkbox_rect(r):
        return QRect(r.left() + 16, r.center().y() - 10, 20, 20)

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(self, p, option, index):
        e = index.data(ROLE_ENTRY)
        if e is None:
            return
        c, r = self.c, option.rect
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(r, QColor(c["card_hover"] if option.state & QStyle.State_MouseOver else c["card"]))
        p.setPen(QPen(QColor(c["border"])))
        p.drawLine(r.left() + 12, r.bottom(), r.right() - 12, r.bottom())
        fm = QFontMetrics(p.font())
        base = p.font()

        cb = self.checkbox_rect(r)
        on = e.checked and e.available
        p.setPen(QPen(QColor(c["accent"] if on else c["text2"]), 1.5))
        p.setBrush(QColor(c["accent"]) if on else Qt.NoBrush)
        p.setOpacity(1.0 if e.available else 0.35)
        p.drawRoundedRect(cb, 5, 5)
        if on:
            p.setPen(QPen(QColor("white"), 2))
            p.drawLine(cb.left() + 5, cb.center().y(), cb.left() + 9, cb.bottom() - 5)
            p.drawLine(cb.left() + 9, cb.bottom() - 5, cb.right() - 4, cb.top() + 5)
        p.setOpacity(1.0)

        tr = QRect(r.left() + 48, r.top() + (r.height() - self.THUMB.height()) // 2, self.THUMB.width(), self.THUMB.height())
        pm = self.thumbs.get(e.key, e.thumbnail) if self.thumbs and e.thumbnail else None
        if pm is not None and not pm.isNull():
            sc = pm.scaled(tr.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.setClipRect(tr)
            p.drawPixmap(tr.left() - (sc.width() - tr.width()) // 2, tr.top() - (sc.height() - tr.height()) // 2, sc)
            p.setClipping(False)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["secondary"]))
            p.drawRoundedRect(tr, 6, 6)
        if e.duration:
            dur = format_duration(e.duration)
            bw = fm.horizontalAdvance(dur) + 10
            badge = QRect(tr.right() - bw - 4, tr.bottom() - 20, bw, 16)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 190))
            p.drawRoundedRect(badge, 4, 4)
            p.setPen(QColor("white"))
            p.drawText(badge, Qt.AlignCenter, dur)

        label = e.platform or "Web"
        color = c["warning"] if not e.available else c["accent2"]
        if not e.available:
            label = "Unavailable"
        pw = fm.horizontalAdvance(label) + 20
        pill = QRect(r.right() - pw - 16, r.center().y() - 11, pw, 22)
        pc = QColor(color)
        pc.setAlpha(40)
        p.setPen(Qt.NoPen)
        p.setBrush(pc)
        p.drawRoundedRect(pill, 11, 11)
        p.setPen(QColor(color))
        p.drawText(pill, Qt.AlignCenter, label)

        tx = tr.right() + 16
        tw = max(pill.left() - tx - 16, 60)
        bold = p.font()
        bold.setBold(True)
        if bold.pixelSize() > 0:
            bold.setPixelSize(bold.pixelSize() + 1)
        p.setFont(bold)
        p.setPen(QColor(c["text"]))
        p.drawText(QRect(tx, r.top() + 14, tw, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(bold).elidedText(e.title, Qt.ElideRight, tw))
        p.setFont(base)
        p.setPen(QColor(c["text2"]))
        meta = "   •   ".join(x for x in ((f"#{e.playlist_index}" if e.playlist_index else ""), e.uploader,
                                           e.upload_date, e.key if len(e.key) < 24 else "") if x)
        p.drawText(QRect(tx, r.top() + 44, tw, 20), Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(meta, Qt.ElideRight, tw))
        p.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            hit = self.checkbox_rect(option.rect).adjusted(-8, -8, 8, 8)
            if hit.contains(event.position().toPoint()) and index.flags() & Qt.ItemIsUserCheckable:
                new = Qt.Unchecked if _checked(index.data(Qt.CheckStateRole)) else Qt.Checked
                return model.setData(index, new, Qt.CheckStateRole)
        return False


class LinkPage(QWidget):
    queueRequested = Signal(list, object, str, bool)   # entries, Profile, destination folder ("" = default), numbering
    message = Signal(str, str)                         # level, text

    def __init__(self, settings, thumbs, theme="dark", accent="#E63946", analyze_fn=None, parent=None):
        super().__init__(parent)
        self.s, self.thumbs, self._analyze_fn = settings, thumbs, analyze_fn
        self.worker = None
        self.dest_dir = ""
        self.single_formats, self.single_duration = [], 0
        self.model = EntryModel(self)
        self.delegate = EntryDelegate(thumbs, theme, accent)
        self._loading = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        title = QLabel("Add Link")
        title.setObjectName("title")
        lay.addWidget(title)

        self.url_edit = QPlainTextEdit()
        self.url_edit.setPlaceholderText("Paste one or more links — YouTube, Facebook, Instagram, TikTok, X, Vimeo, "
                                         "playlists, reels… (one per line)")
        self.url_edit.setFixedHeight(76)
        lay.addWidget(self.url_edit)

        row = QHBoxLayout()
        self.paste_btn = QPushButton("Paste")
        self.paste_btn.clicked.connect(self.paste)
        self.analyze_btn = QPushButton("Analyze")
        self.analyze_btn.setObjectName("primary")
        self.analyze_btn.clicked.connect(self.analyze)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(lambda: self.worker and self.worker.cancel())
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        self.single_cb = QCheckBox("Single video only (ignore the playlist in a link)")
        for w in (self.paste_btn, self.analyze_btn, self.cancel_btn, clear):
            row.addWidget(w)
        row.addWidget(self.single_cb)
        row.addStretch(1)
        lay.addLayout(row)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        lay.addWidget(self.status)
        lay.addWidget(self.progress)

        head = QHBoxLayout()
        self.summary = QLabel("")
        self.summary.setObjectName("muted")
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter results…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.model.set_filter)
        sel_all, sel_none = QPushButton("Select All"), QPushButton("Deselect All")
        sel_all.clicked.connect(lambda: self.model.set_all(True))
        sel_none.clicked.connect(lambda: self.model.set_all(False))
        head.addWidget(self.summary, 1)
        head.addWidget(self.filter, 1)
        head.addWidget(sel_all)
        head.addWidget(sel_none)
        lay.addLayout(head)

        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(self.delegate)
        self.view.setUniformItemSizes(True)
        self.view.setMouseTracking(True)
        self.view.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setSelectionMode(QListView.NoSelection)
        self.view.setFrameShape(QListView.NoFrame)
        self.empty = QLabel("Paste a link above and press Analyze.\nPlaylists and profiles list every video so you can pick.")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty, 1)
        lay.addWidget(self.view, 1)
        self.view.setVisible(False)
        for sig in (self.model.rowsInserted, self.model.rowsRemoved, self.model.modelReset):
            sig.connect(self._toggle_empty)

        lay.addWidget(self._build_options())
        self._loading = False
        self._set_dest("")
        self.reload_from_settings()
        self.model.selectionChanged.connect(self._update_summary)
        thumbs.thumbnailReady.connect(self.model.thumbnail_ready)

    # ------------------------------------------------------------------ options panel
    def _build_options(self):
        box = QFrame()
        box.setObjectName("card")
        g = QGridLayout(box)
        g.setContentsMargins(14, 12, 14, 12)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        self.kind_box = QComboBox()
        self.kind_box.addItem("Video", "video")
        self.kind_box.addItem("Audio only", "audio")
        self.fmt_box = QComboBox()
        self.q_box = QComboBox()
        g.addWidget(QLabel("Download"), 0, 0)
        g.addWidget(self.kind_box, 0, 1)
        g.addWidget(QLabel("Format"), 0, 2)
        g.addWidget(self.fmt_box, 0, 3)
        g.addWidget(QLabel("Quality"), 0, 4)
        g.addWidget(self.q_box, 0, 5)

        self.dest_label = QLineEdit()
        self.dest_label.setReadOnly(True)
        browse, reset = QPushButton("Choose folder…"), QPushButton("Default")
        browse.clicked.connect(self._browse_dest)
        reset.clicked.connect(lambda: self._set_dest(""))
        g.addWidget(QLabel("Save to"), 1, 0)
        g.addWidget(self.dest_label, 1, 1, 1, 3)
        g.addWidget(browse, 1, 4)
        g.addWidget(reset, 1, 5)

        self.folder_cb = QCheckBox("Put playlist items in their own folder")
        self.folder_cb.setChecked(True)
        self.number_cb = QCheckBox("Number playlist files (001 - …)")
        g.addWidget(self.folder_cb, 2, 0, 1, 3)
        g.addWidget(self.number_cb, 2, 3, 1, 3)

        self.adv = QGroupBox("Advanced options")
        self.adv.setCheckable(True)
        self.adv.setChecked(False)
        ag = QGridLayout(self.adv)
        self.speed = QSpinBox()
        self.speed.setRange(0, 1_000_000)
        self.speed.setSingleStep(100)
        self.speed.setSuffix(" KB/s")
        self.speed.setSpecialValueText("Unlimited")
        self.frag = QSpinBox()
        self.frag.setRange(1, 8)
        self.meta_cb = QCheckBox("Embed metadata (title, artist, chapters)")
        self.thumb_cb = QCheckBox("Embed cover image (experimental)")
        self.cookie_edit = QLineEdit()
        self.cookie_edit.setReadOnly(True)
        self.cookie_edit.setPlaceholderText("Optional cookies.txt for content you have access to (stays on this Mac)")
        ck_browse, ck_clear = QPushButton("Choose…"), QPushButton("Clear")
        ck_browse.clicked.connect(self._browse_cookies)
        ck_clear.clicked.connect(lambda: self._set_cookies(""))
        ag.addWidget(QLabel("Speed limit"), 0, 0)
        ag.addWidget(self.speed, 0, 1)
        ag.addWidget(QLabel("Parallel fragments"), 0, 2)
        ag.addWidget(self.frag, 0, 3)
        ag.addWidget(self.meta_cb, 1, 0, 1, 2)
        ag.addWidget(self.thumb_cb, 1, 2, 1, 2)
        ag.addWidget(QLabel("Cookies file"), 2, 0)
        ag.addWidget(self.cookie_edit, 2, 1, 1, 2)
        ag.addWidget(ck_browse, 2, 3)
        ag.addWidget(ck_clear, 2, 4)
        g.addWidget(self.adv, 3, 0, 1, 6)
        self.adv.toggled.connect(lambda on: [self.adv.layout().itemAt(i).widget().setVisible(on)
                                             for i in range(self.adv.layout().count())])
        for i in range(self.adv.layout().count()):
            self.adv.layout().itemAt(i).widget().setVisible(False)

        bottom = QHBoxLayout()
        self.estimate = QLabel("")
        self.estimate.setObjectName("muted")
        self.dl_btn = QPushButton("Download")
        self.dl_btn.setObjectName("primary")
        self.dl_btn.setEnabled(False)
        self.dl_btn.clicked.connect(self.download)
        bottom.addWidget(self.estimate, 1)
        bottom.addWidget(self.dl_btn)
        g.addLayout(bottom, 4, 0, 1, 6)

        self.kind_box.currentIndexChanged.connect(self._kind_changed)
        self.fmt_box.currentIndexChanged.connect(self._opts_changed)
        self.q_box.currentIndexChanged.connect(self._opts_changed)
        self.speed.valueChanged.connect(self._opts_changed)
        self.frag.valueChanged.connect(self._opts_changed)
        for cb in (self.meta_cb, self.thumb_cb, self.number_cb, self.folder_cb):
            cb.toggled.connect(self._opts_changed)
        return box

    # ------------------------------------------------------------------ settings <-> widgets
    def reload_from_settings(self):
        self._loading = True
        s = self.s
        self.kind_box.setCurrentIndex(self.kind_box.findData(s.get("download_kind")))
        self._populate_formats()
        self.speed.setValue(s.get("speed_limit_kbps"))
        self.frag.setValue(s.get("fragments"))
        self.meta_cb.setChecked(s.get("embed_metadata"))
        self.thumb_cb.setChecked(s.get("embed_thumbnail"))
        self.number_cb.setChecked(s.get("playlist_numbering"))
        self.single_cb.setChecked(s.get("single_video_only"))
        self.cookie_edit.setText(s.get("cookies_file"))
        self._loading = False
        self._update_summary()

    def _kind(self):
        return self.kind_box.currentData()

    def _populate_formats(self):
        self.fmt_box.blockSignals(True)
        self.fmt_box.clear()
        if self._kind() == "audio":
            for label, v in AUDIO_FORMAT_LABELS:
                self.fmt_box.addItem(label, v)
            self.fmt_box.setCurrentIndex(max(0, self.fmt_box.findData(self.s.get("audio_format"))))
        else:
            for label, v in VIDEO_FORMATS:
                self.fmt_box.addItem(label, v)
            self.fmt_box.setCurrentIndex(max(0, self.fmt_box.findData(self.s.get("format"))))
        self.fmt_box.blockSignals(False)
        self._populate_quality()

    def _populate_quality(self):
        self.q_box.blockSignals(True)
        self.q_box.clear()
        if self._kind() == "audio":
            if self.fmt_box.currentData() in dl.LOSSLESS:
                self.q_box.addItem("Lossless", 0)
                self.q_box.setEnabled(False)
            else:
                self.q_box.setEnabled(True)
                for kbps in dl.AUDIO_BITRATES:
                    self.q_box.addItem(f"{kbps} kbps" + (" (best)" if kbps == 320 else ""), kbps)
                self.q_box.setCurrentIndex(max(0, self.q_box.findData(self.s.get("audio_quality"))))
        else:
            self.q_box.setEnabled(True)
            heights = self.available_heights() or list(dl.QUALITY_HEIGHTS)
            self.q_box.addItem("Best available", 0)
            for h in heights:
                self.q_box.addItem(HEIGHT_LABELS.get(h, f"{h}p"), h)
            want = self.s.get("max_quality")
            idx = self.q_box.findData(want)
            if idx < 0:                                   # saved quality not offered: nearest lower one, else best
                lower = [h for h in heights if h <= want]
                idx = self.q_box.findData(lower[0]) if lower else 0
            self.q_box.setCurrentIndex(max(0, idx))
        self.q_box.blockSignals(False)

    def available_heights(self):
        from app.media_info import available_heights
        return available_heights(self.single_formats) if self.single_formats else []

    def _kind_changed(self):
        if not self._loading:
            self.s.set("download_kind", self._kind())
        self._populate_formats()
        self._update_summary()

    def _opts_changed(self, *a):
        if self._loading:
            return
        s = self.s
        if self._kind() == "audio":
            s.set("audio_format", self.fmt_box.currentData())
            if self.q_box.isEnabled() and self.q_box.currentData():
                s.set("audio_quality", self.q_box.currentData())
            self._populate_quality_if_format_changed()
        else:
            s.set("format", self.fmt_box.currentData())
            s.set("max_quality", self.q_box.currentData() or 0)
        s.set("speed_limit_kbps", self.speed.value())
        s.set("fragments", self.frag.value())
        s.set("embed_metadata", self.meta_cb.isChecked())
        s.set("embed_thumbnail", self.thumb_cb.isChecked())
        s.set("playlist_numbering", self.number_cb.isChecked())
        self._update_summary()

    def _populate_quality_if_format_changed(self):
        lossless = self.fmt_box.currentData() in dl.LOSSLESS
        if lossless == self.q_box.isEnabled():            # enabled state no longer matches the format
            self._populate_quality()

    def profile(self):
        audio = self._kind() == "audio"
        return dl.Profile.from_dict({
            "kind": self._kind(),
            "container": self.s.get("format") if audio else self.fmt_box.currentData(),
            "max_height": self.s.get("max_quality") if audio else (self.q_box.currentData() or 0),
            "audio_format": self.fmt_box.currentData() if audio else self.s.get("audio_format"),
            "audio_quality": (self.q_box.currentData() or self.s.get("audio_quality")) if audio else self.s.get("audio_quality"),
            "embed_metadata": self.meta_cb.isChecked(), "embed_thumbnail": self.thumb_cb.isChecked(),
            "limit_rate_kbps": self.speed.value(), "fragments": self.frag.value(),
            "cookies_file": self.cookie_edit.text(), "allow_other_sites": self.s.get("allow_other_sites")})

    # ------------------------------------------------------------------ destination / cookies
    def _set_dest(self, path):
        self.dest_dir = path
        self.dest_label.setText(path or "Default download folder (organized automatically)")

    def _browse_dest(self):
        d = QFileDialog.getExistingDirectory(self, "Save to", self.dest_dir or self.s.get("download_dir"))
        if d:
            self._set_dest(d)

    def _set_cookies(self, path):
        self.cookie_edit.setText(path)
        self.s.set("cookies_file", path)

    def _browse_cookies(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose cookies.txt", "", "Cookies (*.txt);;All files (*)")
        if f:
            self._set_cookies(f)

    # ------------------------------------------------------------------ analysis
    def paste(self):
        text = QApplication.clipboard().text()
        if text:
            self.url_edit.setPlainText((self.url_edit.toPlainText().rstrip() + "\n" + text).strip())

    def clear(self):
        self.url_edit.clear()
        self.model.clear()
        self.single_formats, self.single_duration = [], 0
        self._populate_quality()
        self.status.setText("")
        self._update_summary()

    def set_urls(self, urls, analyze=True):
        self.url_edit.setPlainText("\n".join(urls))
        if analyze:
            self.analyze()

    def analyze(self):
        if self.worker and self.worker.isRunning():
            return
        urls = links_from_text(self.url_edit.toPlainText())
        if not urls:
            QMessageBox.information(self, "Add Link", "Paste at least one http(s) link first.")
            return
        self.s.set("single_video_only", self.single_cb.isChecked())
        self.model.clear()
        self.single_formats, self.single_duration = [], 0
        self._analyses, self._errors = [], []
        self.analyze_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress.setVisible(True)
        self.status.setText("Analysing…")
        self.worker = AnalyzeWorker(urls, self.single_cb.isChecked(), self.s.get("cookies_file"),
                                    self.s.get("allow_other_sites"), self._analyze_fn, parent=self)
        self.worker.progress.connect(self.status.setText)
        self.worker.analyzed.connect(self._on_analyzed)
        self.worker.failed.connect(lambda u, m: self._errors.append((u, m)))
        self.worker.finishedAll.connect(self._on_finished)
        self.worker.start()

    def _on_analyzed(self, analysis):
        self._analyses.append(analysis)
        self.model.add_analysis(analysis)

    def _on_finished(self, ok, bad):
        self.analyze_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.progress.setVisible(False)
        if len(self._analyses) == 1 and self._analyses[0].kind == "video":
            a = self._analyses[0]
            self.single_formats, self.single_duration = a.formats, a.entries[0].duration
            self._populate_quality()
        n = len(self.model.entries)
        msg = f"{n} item(s) ready." if n else "Nothing to download."
        if bad:
            first = self._errors[0][1] if self._errors else ""
            msg += f"  {bad} link(s) failed: {first}"
            self.message.emit("error" if not n else "warning", f"{bad} link(s) could not be analysed: {first}")
        self.status.setText(msg)
        self._update_summary()

    def _toggle_empty(self, *a):
        has = bool(self.model.entries)
        self.view.setVisible(has)
        self.empty.setVisible(not has)

    # ------------------------------------------------------------------ summary / download
    def _update_summary(self):
        chosen = self.model.checked_entries()
        total = len(self.model.entries)
        dur = sum(e.duration for e in chosen)
        plats = sorted({e.platform for e in self.model.entries if e.platform})
        self.summary.setText(f"{len(chosen)} of {total} selected" + (f"   •   {format_duration(dur)}" if dur else "")
                             + (f"   •   {', '.join(plats)}" if plats else "") if total else "")
        self.dl_btn.setEnabled(bool(chosen))
        self.dl_btn.setText(f"Download {len(chosen)} Selected" if chosen else "Download")
        self.estimate.setText(self._estimate_text(chosen))

    def _estimate_text(self, chosen):
        if not chosen:
            return ""
        p = self.profile()
        if self.single_formats and len(chosen) == 1:
            sz = estimate_size(self.single_formats, p.kind, p.max_height, 0 if p.audio_format in dl.LOSSLESS else p.audio_quality,
                               self.single_duration)
            return f"Estimated size: ~{format_size(sz)}" if sz else "Estimated size: not published by the site"
        if p.kind == "audio" and p.audio_format not in dl.LOSSLESS:
            dur = sum(e.duration for e in chosen)
            if dur:
                return f"Estimated size: ~{format_size(dur * p.audio_quality * 1000 / 8)}"
        return ""

    def download(self):
        chosen = self.model.checked_entries()
        if not chosen:
            return
        entries = [dataclasses.replace(e) for e in chosen]
        if not self.folder_cb.isChecked():
            for e in entries:
                e.playlist_title = ""
        numbering = self.number_cb.isChecked() and any(e.playlist_index for e in entries)
        self.queueRequested.emit(entries, self.profile(), self.dest_dir, numbering)

    def set_theme(self, theme, accent):
        self.delegate.set_theme(theme, accent)
        self.view.viewport().update()
