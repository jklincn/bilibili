from __future__ import annotations

import ctypes
import os
import sys

from PySide6 import QtCore, QtGui, QtWidgets

from .core import static_asset
from .window import MainWindow


def configure_windows_dpi() -> None:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

    if sys.platform != "win32":
        return

    try:
        awareness_context = ctypes.c_void_p(-4)
        ctypes.windll.user32.SetProcessDpiAwarenessContext(awareness_context)
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def configure_windows_app_id() -> None:
    if sys.platform != "win32":
        return

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Jklin.BilibiliDownloader"
        )
    except Exception:
        pass


def application_icon() -> QtGui.QIcon:
    for icon_path in (
        static_asset("Bilibili_logo_2.ico"),
        static_asset("Bilibili_logo_2.webp"),
    ):
        if icon_path.exists():
            return QtGui.QIcon(str(icon_path))
    return QtGui.QIcon()


def build_application() -> QtWidgets.QApplication:
    configure_windows_dpi()
    configure_windows_app_id()
    QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(application_icon())

    for family in (
        "Segoe UI Variable Text",
        "Microsoft YaHei UI",
        "Segoe UI",
        "Arial",
    ):
        if family in QtGui.QFontDatabase.families():
            app.setFont(QtGui.QFont(family, 10))
            break
    return app


def main() -> int:
    app = build_application()
    window = MainWindow()
    window.setWindowIcon(application_icon())
    window.show()
    return app.exec()
