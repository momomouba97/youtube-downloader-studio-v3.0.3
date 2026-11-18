"""Built-in video/audio player (Qt Multimedia). Plays what you downloaded without leaving the app."""
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSlider,
                               QStackedLayout, QVBoxLayout, QWidget)

from app.downloader import AUDIO_EXTENSIONS
from app.video_manager import format_duration

SPEEDS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]


def is_audio_path(path):
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


class PlayerPage(QWidget):
    openExternally = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []                    # [{"title", "path"}]
        self.index = -1
        self._seeking = False
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget()
        self.video.setMinimumSize(320, 180)
        self.video.installEventFilter(self)
        self.player.setVideoOutput(self.video)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(14)
        left = QVBoxLayout()
        self.title = QLabel("Player")
        self.title.setObjectName("title")
        left.addWidget(self.title)

        stage = QWidget()
        self.stack = QStackedLayout(stage)
        self.stack.setStackingMode(QStackedLayout.StackAll)
        self.stack.addWidget(self.video)
        self.cover = QLabel("♪")
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet("font-size: 96px; background: transparent;")
        self.cover.setVisible(False)
        self.stack.addWidget(self.cover)
        left.addWidget(stage, 1)
        self.message = QLabel("")
        self.message.setObjectName("muted")
        self.message.setWordWrap(True)
        left.addWidget(self.message)

        bar = QHBoxLayout()
        self.prev_btn, self.play_btn, self.next_btn = QPushButton("⏮"), QPushButton("▶"), QPushButton("⏭")
        for b in (self.prev_btn, self.play_btn, self.next_btn):
            b.setFixedWidth(44)
        self.prev_btn.clicked.connect(self.previous)
        self.play_btn.clicked.connect(self.toggle_play)
        self.next_btn.clicked.connect(self.next)
        self.pos = QSlider(Qt.Horizontal)
        self.pos.setRange(0, 0)
        self.pos.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.pos.sliderReleased.connect(self._seek_released)
        self.time = QLabel("0:00 / 0:00")
        self.time.setObjectName("muted")
        self.mute_btn = QPushButton("🔊")
        self.mute_btn.setFixedWidth(44)
        self.mute_btn.clicked.connect(self.toggle_mute)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(80)
        self.vol.setFixedWidth(90)
        self.vol.valueChanged.connect(lambda v: self.audio.setVolume(v / 100.0))
        self.speed = QComboBox()
        for sp in SPEEDS:
            self.speed.addItem(f"{sp:g}×", sp)
        self.speed.setCurrentIndex(SPEEDS.index(1.0))
        self.speed.currentIndexChanged.connect(lambda: self.player.setPlaybackRate(self.speed.currentData()))
        self.full_btn = QPushButton("⛶")
        self.full_btn.setFixedWidth(44)
        self.full_btn.clicked.connect(self.toggle_fullscreen)
        for w in (self.prev_btn, self.play_btn, self.next_btn):
            bar.addWidget(w)
        bar.addWidget(self.pos, 1)
        bar.addWidget(self.time)
        bar.addWidget(self.mute_btn)
        bar.addWidget(self.vol)
        bar.addWidget(self.speed)
        bar.addWidget(self.full_btn)
        left.addLayout(bar)
        outer.addLayout(left, 1)

        right = QVBoxLayout()
        q = QLabel("Play queue")
        q.setObjectName("title")
        right.addWidget(q)
        self.queue = QListWidget()
        self.queue.setFixedWidth(300)
        self.queue.itemActivated.connect(lambda it: self.play_index(self.queue.row(it)))
        self.queue.itemDoubleClicked.connect(lambda it: self.play_index(self.queue.row(it)))
        right.addWidget(self.queue, 1)
        self.external_btn = QPushButton("Open with default player")
        self.external_btn.clicked.connect(self._open_external)
        right.addWidget(self.external_btn)
        outer.addLayout(right)

        p = self.player
        p.positionChanged.connect(self._on_position)
        p.durationChanged.connect(self._on_duration)
        p.playbackStateChanged.connect(self._on_state)
        p.mediaStatusChanged.connect(self._on_status)
        p.errorOccurred.connect(self._on_error)
        for key, fn in (("Space", self.toggle_play), ("Left", lambda: self.seek_by(-5000)),
                        ("Right", lambda: self.seek_by(5000)), ("Up", lambda: self.vol.setValue(self.vol.value() + 10)),
                        ("Down", lambda: self.vol.setValue(self.vol.value() - 10)), ("M", self.toggle_mute),
                        ("F", self.toggle_fullscreen), ("N", self.next), ("P", self.previous)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self._refresh_buttons()

    # ---- queue ---------------------------------------------------------------------
    def set_queue(self, items, index=0, autoplay=True):
        self.items = [dict(i) for i in items if i.get("path")]
        self.queue.clear()
        for it in self.items:
            self.queue.addItem(QListWidgetItem(("♪  " if is_audio_path(it["path"]) else "▶  ") + it["title"]))
        if self.items:
            self.play_index(max(0, min(index, len(self.items) - 1)), autoplay)
        else:
            self.index = -1
            self.player.stop()
            self.title.setText("Player")
        self._refresh_buttons()

    def play_index(self, i, autoplay=True):
        if not (0 <= i < len(self.items)):
            return
        it = self.items[i]
        self.index = i
        self.message.setText("")
        if not os.path.isfile(it["path"]):
            self.message.setText("This file no longer exists on disk.")
            return
        self.title.setText(it["title"])
        self.cover.setVisible(is_audio_path(it["path"]))
        self.player.setSource(QUrl.fromLocalFile(it["path"]))
        self.queue.setCurrentRow(i)
        if autoplay:
            self.player.play()
        self._refresh_buttons()

    def next(self):
        if self.index + 1 < len(self.items):
            self.play_index(self.index + 1)

    def previous(self):
        if self.player.position() > 3000 or self.index <= 0:
            self.player.setPosition(0)
        else:
            self.play_index(self.index - 1)

    # ---- transport -----------------------------------------------------------------------
    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek_by(self, ms):
        self.player.setPosition(max(0, min(self.player.duration() or 0, self.player.position() + ms)))

    def toggle_mute(self):
        self.audio.setMuted(not self.audio.isMuted())
        self.mute_btn.setText("🔇" if self.audio.isMuted() else "🔊")

    def toggle_fullscreen(self):
        self.video.setFullScreen(not self.video.isFullScreen())

    def stop(self):
        self.player.stop()

    def _seek_released(self):
        self._seeking = False
        self.player.setPosition(self.pos.value())

    def eventFilter(self, obj, ev):
        if obj is self.video and ev.type() == QEvent.MouseButtonDblClick:
            self.toggle_fullscreen()
            return True
        return super().eventFilter(obj, ev)

    # ---- player signals --------------------------------------------------------------------
    def _on_position(self, ms):
        if not self._seeking:
            self.pos.setValue(ms)
        self.time.setText(f"{format_duration(ms // 1000)} / {format_duration((self.player.duration() or 0) // 1000)}")

    def _on_duration(self, ms):
        self.pos.setRange(0, max(0, ms))

    def _on_state(self, state):
        self.play_btn.setText("⏸" if state == QMediaPlayer.PlayingState else "▶")

    def _on_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.next()

    def _on_error(self, error, text=""):
        path = self.items[self.index]["path"] if 0 <= self.index < len(self.items) else ""
        self.message.setText(f"This Mac cannot play this file inside the app ({text or 'unsupported format'}). "
                             f"Use “Open with default player”.")
        self._failed_path = path

    def _open_external(self):
        if 0 <= self.index < len(self.items):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.items[self.index]["path"]))

    def _refresh_buttons(self):
        has = bool(self.items)
        self.prev_btn.setEnabled(has)
        self.next_btn.setEnabled(has and self.index + 1 < len(self.items))
        self.play_btn.setEnabled(has)
        self.external_btn.setEnabled(has)
