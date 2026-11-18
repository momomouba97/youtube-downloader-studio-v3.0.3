import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

try:
    from PySide6.QtWidgets import QApplication
except ImportError:          # Qt-less environment: Qt tests are skipped by importorskip
    QApplication = None


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole session (a QCoreApplication would break pixmap tests)."""
    if QApplication is None:
        pytest.skip("PySide6 not installed")
    return QApplication.instance() or QApplication([])
