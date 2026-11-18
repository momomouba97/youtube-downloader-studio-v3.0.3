"""Palettes and QSS. Dark palette follows the project spec; light is a matching counterpart."""

PALETTES = {
    "dark": {"bg": "#101114", "sidebar": "#15171C", "card": "#191C23", "card_hover": "#20242D",
             "secondary": "#242832", "text": "#F5F5F5", "text2": "#A1A1AA", "border": "#2B2F3A",
             "accent2": "#8B5CF6", "success": "#22C55E", "warning": "#F59E0B", "error": "#EF4444"},
    "light": {"bg": "#F4F5F7", "sidebar": "#FFFFFF", "card": "#FFFFFF", "card_hover": "#F0F1F4",
              "secondary": "#E7E9EE", "text": "#16181D", "text2": "#5F6572", "border": "#D9DCE3",
              "accent2": "#7C3AED", "success": "#16A34A", "warning": "#D97706", "error": "#DC2626"},
}


def palette(theme="dark", accent="#E63946"):
    p = dict(PALETTES.get(theme, PALETTES["dark"]))
    p["accent"] = accent
    return p


def build_qss(theme="dark", accent="#E63946"):
    c = palette(theme, accent)
    return f"""
QMainWindow, QWidget {{ background: {c['bg']}; color: {c['text']}; font-size: 13px; }}
QLineEdit, QComboBox {{ background: {c['secondary']}; border: 1px solid {c['border']};
    border-radius: 8px; padding: 7px 10px; selection-background-color: {c['accent']}; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c['card']}; border: 1px solid {c['border']};
    selection-background-color: {c['secondary']}; }}
QPushButton {{ background: {c['secondary']}; border: 1px solid {c['border']}; border-radius: 8px;
    padding: 7px 14px; }}
QPushButton:hover {{ background: {c['card_hover']}; }}
QPushButton:disabled {{ color: {c['text2']}; }}
QPushButton#primary {{ background: {c['accent']}; border: none; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: {c['accent']}; }}
QListView {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 12px; outline: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['secondary']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QLabel {{ background: transparent; }}
QFrame#card {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#card:hover {{ border: 1px solid {c['accent']}; }}
QLabel#bigNumber {{ font-size: 26px; font-weight: 700; background: transparent; }}
QListWidget {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 12px; outline: none; }}
QListWidget::item {{ padding: 6px 10px; }}
QSlider::groove:horizontal {{ background: {c['secondary']}; height: 6px; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {c['text']}; width: 14px; margin: -5px 0; border-radius: 7px; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QGroupBox::indicator {{ width: 16px; height: 16px; border: 1.5px solid {c['text2']}; border-radius: 4px; background: transparent; }}
QCheckBox::indicator:checked, QGroupBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
QCheckBox::indicator:hover, QGroupBox::indicator:hover {{ border-color: {c['accent']}; }}
QGroupBox {{ border: 1px solid {c['border']}; border-radius: 10px; margin-top: 10px; padding-top: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
QSpinBox {{ background: {c['secondary']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 5px 8px; }}
QPlainTextEdit {{ background: {c['secondary']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 6px; }}
QFrame#sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
QPushButton#nav {{ background: transparent; border: none; text-align: left; padding: 10px 14px; border-radius: 8px; }}
QPushButton#nav:hover {{ background: {c['card_hover']}; }}
QPushButton#nav:checked {{ background: {c['secondary']}; color: {c['accent']}; font-weight: 600; }}
QLabel#muted {{ color: {c['text2']}; }}
QLabel#title {{ font-size: 18px; font-weight: 700; }}
QProgressBar {{ background: {c['secondary']}; border: none; border-radius: 4px; height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
"""
