"""Small non-blocking notifications (bottom-right), with an optional action button."""
from PySide6.QtCore import QEvent, QObject, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton

from ui.styles import palette


class Toast(QFrame):
    def __init__(self, parent, text, level="info", action=None, on_close=None, theme="dark", accent="#E63946"):
        super().__init__(parent)
        c = palette(theme, accent)
        color = {"info": c["accent2"], "success": c["success"], "warning": c["warning"], "error": c["error"]}.get(level, c["accent2"])
        self.setObjectName("toast")
        self.setStyleSheet(f"QFrame#toast {{ background: {c['card']}; border: 1px solid {color}; border-radius: 10px; }}"
                           f"QLabel {{ background: transparent; color: {c['text']}; }}")
        self._on_close = on_close
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
        lay.setSpacing(10)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(380)
        lay.addWidget(self.label, 1)
        self.action_btn = None
        if action:
            self.action_btn = QPushButton(action[0])
            self.action_btn.setObjectName("primary")
            self.action_btn.clicked.connect(lambda: (action[1](), self.dismiss()))
            lay.addWidget(self.action_btn)
        x = QPushButton("✕")
        x.setFixedWidth(28)
        x.clicked.connect(self.dismiss)
        lay.addWidget(x)
        self.fx = QGraphicsOpacityEffect(self)
        self.fx.setOpacity(1.0)
        self.setGraphicsEffect(self.fx)
        self.adjustSize()

    def dismiss(self, animate=True):
        if not self.isVisible() or not animate:
            self._finish()
            return
        self._anim = QPropertyAnimation(self.fx, b"opacity", self)
        self._anim.setDuration(200)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._finish)
        self._anim.start()

    def _finish(self):
        if self._on_close:
            cb, self._on_close = self._on_close, None
            cb(self)
        self.hide()
        self.deleteLater()


class ToastManager(QObject):
    """Stacks toasts in the bottom-right corner of `host` and keeps them positioned on resize."""

    def __init__(self, host, theme="dark", accent="#E63946", max_visible=4):
        super().__init__(host)
        self.host, self.theme, self.accent, self.max_visible = host, theme, accent, max_visible
        self.toasts = []
        host.installEventFilter(self)

    def set_theme(self, theme, accent):
        self.theme, self.accent = theme, accent

    def show(self, text, level="info", action=None, timeout=5000):
        while len(self.toasts) >= self.max_visible:
            self.toasts[0].dismiss(animate=False)
        t = Toast(self.host, text, level, action, self._closed, self.theme, self.accent)
        self.toasts.append(t)
        t.show()
        t.raise_()
        self._layout()
        if timeout:
            QTimer.singleShot(timeout, lambda t=t: t.dismiss() if t in self.toasts else None)
        return t

    def _closed(self, toast):
        if toast in self.toasts:
            self.toasts.remove(toast)
        self._layout()

    def _layout(self):
        y = self.host.height() - 16
        for t in reversed(self.toasts):
            t.adjustSize()
            y -= t.height()
            t.move(self.host.width() - t.width() - 16, y)
            t.raise_()
            y -= 8

    def eventFilter(self, obj, ev):
        if obj is self.host and ev.type() == QEvent.Resize:
            self._layout()
        return False
