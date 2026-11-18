"""Settings: downloads, formats, organization, network access, appearance, storage, about.
Every change is saved immediately."""
import platform
import subprocess

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from app import __version__
from app import downloader as dl
from app.formatting import format_size
from app.resources import tool_path

QUALITY_ITEMS = [("Best available", 0), ("2160p (4K)", 2160), ("1440p (2K)", 1440), ("1080p (Full HD)", 1080),
                 ("720p (HD)", 720), ("480p", 480), ("360p", 360)]
AUDIO_ITEMS = [("MP3", "mp3"), ("M4A (AAC)", "m4a"), ("Opus", "opus"), ("FLAC (lossless)", "flac"),
               ("WAV (uncompressed)", "wav"), ("OGG (Vorbis)", "ogg")]
STRUCTURE_ITEMS = [("Channel / uploader folders", "channel"), ("Platform › channel", "platform_channel"),
                   ("Playlist folders", "playlist"), ("Everything in one folder", "flat")]
ACCENTS = [("Red", "#E63946"), ("Purple", "#8B5CF6"), ("Blue", "#3B82F6"), ("Green", "#22C55E"), ("Orange", "#F59E0B")]


def _ffmpeg_version():
    p = tool_path("ffmpeg")
    if not p:
        return "not found"
    try:
        out = subprocess.run([p, "-version"], capture_output=True, text=True, timeout=10,
                             stdin=subprocess.DEVNULL).stdout.splitlines()
        return out[0].replace("ffmpeg version ", "")[:60] if out else "unknown"
    except Exception:
        return "unavailable"


class SettingsPage(QWidget):
    themeChanged = Signal()

    def __init__(self, settings, thumbs=None, manager=None, parent=None):
        super().__init__(parent)
        self.s, self.thumbs, self.mgr = settings, thumbs, manager
        self._loading = True
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)
        title = QLabel("Settings")
        title.setObjectName("title")
        lay.addWidget(title)

        # ---- downloads
        g1 = QGroupBox("Downloads")
        f1 = QFormLayout(g1)
        self.folder = QLineEdit()
        self.folder.setReadOnly(True)
        browse, open_btn = QPushButton("Browse…"), QPushButton("Open")
        browse.clicked.connect(self._browse)
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.folder.text())))
        row = QHBoxLayout()
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        row.addWidget(open_btn)
        f1.addRow("Download folder", row)
        self.kind = self._combo([("Video", "video"), ("Audio only", "audio")], "download_kind")
        f1.addRow("Default download type", self.kind)
        self.fmt = self._combo([("MP4", "mp4"), ("MKV", "mkv"), ("WebM", "webm")], "format")
        f1.addRow("Video format", self.fmt)
        self.quality = self._combo(QUALITY_ITEMS, "max_quality")
        f1.addRow("Maximum video quality", self.quality)
        self.audio_fmt = self._combo(AUDIO_ITEMS, "audio_format")
        f1.addRow("Audio format", self.audio_fmt)
        self.audio_q = self._combo([(f"{k} kbps", k) for k in dl.AUDIO_BITRATES], "audio_quality")
        f1.addRow("Audio quality (MP3/M4A/Opus/OGG)", self.audio_q)
        self.parallel = self._spin("parallel_downloads", 1, 10)
        self.parallel.valueChanged.connect(lambda v: self.mgr and self.mgr._pump())
        f1.addRow("Parallel downloads (1–10)", self.parallel)
        self.retries = self._spin("retry_attempts", 0, 10)
        f1.addRow("Retry attempts", self.retries)
        self.speed = self._spin("speed_limit_kbps", 0, 1_000_000, " KB/s", "Unlimited", 100)
        f1.addRow("Speed limit per download", self.speed)
        self.frag = self._spin("fragments", 1, 8)
        f1.addRow("Parallel fragments (faster HLS/DASH)", self.frag)
        self.meta = self._check("Embed metadata (title, artist, chapters)", "embed_metadata")
        self.thumb = self._check("Embed cover image (experimental)", "embed_thumbnail")
        f1.addRow("", self.meta)
        f1.addRow("", self.thumb)
        lay.addWidget(g1)

        # ---- organization
        g2 = QGroupBox("Organization")
        f2 = QFormLayout(g2)
        self.structure = self._combo(STRUCTURE_ITEMS, "folder_structure")
        f2.addRow("Folder structure", self.structure)
        self.template = QLineEdit()
        self.template.editingFinished.connect(self._template)
        f2.addRow("Filename template", self.template)
        self.template_msg = QLabel("")
        f2.addRow("", self.template_msg)
        self.numbering = self._check("Number playlist files (001 - …)", "playlist_numbering")
        f2.addRow("", self.numbering)
        lay.addWidget(g2)

        # ---- access
        g3 = QGroupBox("Websites & access")
        f3 = QFormLayout(g3)
        self.other = self._check("Allow websites that are not in the supported list (any link yt-dlp understands)",
                                 "allow_other_sites")
        f3.addRow("", self.other)
        self.cookies = QLineEdit()
        self.cookies.setReadOnly(True)
        self.cookies.setPlaceholderText("Optional cookies.txt — only for content your own account can access")
        ck, ckc = QPushButton("Choose…"), QPushButton("Clear")
        ck.clicked.connect(self._browse_cookies)
        ckc.clicked.connect(lambda: self._set_cookies(""))
        crow = QHBoxLayout()
        crow.addWidget(self.cookies, 1)
        crow.addWidget(ck)
        crow.addWidget(ckc)
        f3.addRow("Cookies file", crow)
        note = QLabel("Some platforms (Instagram, Facebook, private videos) require a logged-in session. The cookies "
                      "file stays on this Mac and is only passed to the downloader. DRM-protected content cannot be "
                      "downloaded.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        f3.addRow("", note)
        lay.addWidget(g3)

        # ---- appearance & behaviour
        g4 = QGroupBox("Appearance & behaviour")
        f4 = QFormLayout(g4)
        self.theme = self._combo([("Dark", "dark"), ("Light", "light")], "theme", emit_theme=True)
        f4.addRow("Theme", self.theme)
        self.accent = self._combo(ACCENTS, "accent", emit_theme=True)
        f4.addRow("Accent color", self.accent)
        self.clip = self._check("Detect supported links copied to the clipboard", "detect_clipboard")
        self.n_ok = self._check("Notify when a download completes", "notify_completed")
        self.n_fail = self._check("Notify when a download fails", "notify_failed")
        for w in (self.clip, self.n_ok, self.n_fail):
            f4.addRow("", w)
        lay.addWidget(g4)

        # ---- storage / about
        self.storage = QLabel("")
        self.storage.setObjectName("muted")
        clear = QPushButton("Clear Thumbnail Cache")
        clear.clicked.connect(self._clear_cache)
        srow = QHBoxLayout()
        srow.addWidget(self.storage, 1)
        srow.addWidget(clear)
        lay.addLayout(srow)
        self.about = QLabel(f"YouTube Downloader Studio {__version__}   •   Python {platform.python_version()}   •   "
                            f"yt-dlp {self._ytdlp()}   •   FFmpeg {_ffmpeg_version()}")
        self.about.setObjectName("muted")
        self.about.setWordWrap(True)
        lay.addWidget(self.about)
        lay.addStretch(1)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._loading = False
        self.reload()

    # ---- helpers ------------------------------------------------------------------
    def _combo(self, items, key, emit_theme=False):
        c = QComboBox()
        for label, v in items:
            c.addItem(label, v)

        def changed():
            if not self._loading:
                self.s.set(key, c.currentData())
                if emit_theme:
                    self.themeChanged.emit()
        c.currentIndexChanged.connect(changed)
        c._key = key
        return c

    def _spin(self, key, lo, hi, suffix="", special="", step=1):
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        if suffix:
            sp.setSuffix(suffix)
        if special:
            sp.setSpecialValueText(special)
        sp.valueChanged.connect(lambda v: None if self._loading else self.s.set(key, v))
        sp._key = key
        return sp

    def _check(self, text, key):
        c = QCheckBox(text)
        c.toggled.connect(lambda v: None if self._loading else self.s.set(key, v))
        c._key = key
        return c

    def reload(self):
        """Refresh every widget from the saved settings (other pages also change them)."""
        self._loading = True
        for w in (self.kind, self.fmt, self.quality, self.audio_fmt, self.audio_q, self.structure, self.theme, self.accent):
            w.setCurrentIndex(max(0, w.findData(self.s.get(w._key))))
        for w in (self.parallel, self.retries, self.speed, self.frag):
            w.setValue(self.s.get(w._key))
        for w in (self.meta, self.thumb, self.numbering, self.other, self.clip, self.n_ok, self.n_fail):
            w.setChecked(self.s.get(w._key))
        self.folder.setText(self.s.get("download_dir") or (self.mgr.default_dir if self.mgr else ""))
        self.template.setText(self.s.get("filename_template"))
        self.cookies.setText(self.s.get("cookies_file"))
        self._loading = False
        self.refresh_storage()

    @staticmethod
    def _ytdlp():
        try:
            import yt_dlp
            return yt_dlp.version.__version__
        except Exception:
            return "not installed"

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose download folder", self.folder.text())
        if d:
            self.s.set("download_dir", d)
            self.folder.setText(d)

    def _set_cookies(self, path):
        self.s.set("cookies_file", path)
        self.cookies.setText(path)

    def _browse_cookies(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose cookies.txt", "", "Cookies (*.txt);;All files (*)")
        if f:
            self._set_cookies(f)

    def _template(self):
        if self._loading:
            return
        try:
            self.s.set("filename_template", dl.validate_template(self.template.text()))
            self.template_msg.setText("")
        except ValueError as exc:
            self.template_msg.setText(str(exc))
            self.template_msg.setStyleSheet("color: #EF4444;")
            self.template.setText(self.s.get("filename_template"))

    def refresh_storage(self):
        if self.thumbs:
            self.storage.setText(f"Thumbnail cache: {format_size(self.thumbs.disk_usage())}")

    def _clear_cache(self):
        if self.thumbs:
            self.thumbs.clear()
            self.refresh_storage()
