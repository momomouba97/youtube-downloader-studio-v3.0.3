"""Home page: live statistics, quick actions, recent activity."""
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget)

from app.formatting import format_size


class StatCard(QFrame):
    clicked = Signal()

    def __init__(self, caption, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        self.value = QLabel("0")
        self.value.setObjectName("bigNumber")
        self.caption = QLabel(caption)
        self.caption.setObjectName("muted")
        lay.addWidget(self.value)
        lay.addWidget(self.caption)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()


class DashboardPage(QWidget):
    navigate = Signal(str)          # page name
    action = Signal(str)            # "download_selected" | "open_folder" | "import_channel"

    CARDS = [("videos", "Videos in library", "Library"), ("selected", "Selected videos", "Library"),
             ("active", "Active downloads", "Downloads"), ("completed", "Completed downloads", "Completed"),
             ("failed", "Failed downloads", "Failed"), ("storage", "Storage used by downloads", "Completed"),
             ("free", "Free disk space", "Settings")]

    def __init__(self, db, manager, parent=None):
        super().__init__(parent)
        self.db, self.mgr = db, manager
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)
        title = QLabel("Dashboard")
        title.setObjectName("title")
        lay.addWidget(title)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.cards = {}
        for i, (key, caption, page) in enumerate(self.CARDS):
            c = StatCard(caption)
            c.clicked.connect(lambda p=page: self.navigate.emit(p))
            grid.addWidget(c, i // 4, i % 4)
            self.cards[key] = c
        lay.addLayout(grid)

        actions = QHBoxLayout()
        for text, fn, primary in (("Add Link", lambda: self.navigate.emit("Add Link"), True),
                                  ("Import Channel", lambda: self.action.emit("import_channel"), False),
                                  ("Browse Library", lambda: self.navigate.emit("Library"), False),
                                  ("Download Selected", lambda: self.action.emit("download_selected"), False),
                                  ("Open Download Folder", lambda: self.action.emit("open_folder"), False)):
            b = QPushButton(text)
            if primary:
                b.setObjectName("primary")
            b.clicked.connect(fn)
            actions.addWidget(b)
        actions.addStretch(1)
        lay.addLayout(actions)

        sub = QLabel("Recent activity")
        sub.setObjectName("title")
        lay.addWidget(sub)
        self.activity = QListWidget()
        self.activity.setSelectionMode(QListWidget.NoSelection)
        lay.addWidget(self.activity, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self.refresh)
        manager.countsChanged.connect(self.schedule_refresh)
        self.refresh()

    def schedule_refresh(self):
        if not self._timer.isActive():
            self._timer.start()

    def showEvent(self, ev):
        self.refresh()
        super().showEvent(ev)

    def refresh(self):
        st, c, tot = self.db.stats(), self.mgr.counts(), self.db.download_totals()
        free = 0
        path = Path(self.mgr.download_dir())
        while not path.exists() and path != path.parent:      # folder not created yet: ask its nearest parent
            path = path.parent
        try:
            free = shutil.disk_usage(path).free
        except OSError:
            pass
        vals = {"videos": f"{st['total']:,}", "selected": f"{st['selected']:,}", "active": str(c["in_progress"]),
                "completed": str(c["completed"]), "failed": str(c["failed"]),
                "storage": format_size(tot["bytes"]), "free": format_size(free)}
        for k, v in vals.items():
            self.cards[k].value.setText(v)
        self.activity.clear()
        for r in self.db.recent_activity(15):
            self.activity.addItem(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(r['ts']))}   {r['message']}")
        if self.activity.count() == 0:
            self.activity.addItem("No activity yet — add a link or import a channel to get started.")
