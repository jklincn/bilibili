from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtSvgWidgets, QtWidgets

from .core import (
    BinaryPaths,
    FormatOption,
    app_root,
    discover_binaries,
    format_duration,
    normalize_video_url,
    static_asset,
)
from .workers import DownloadWorker, MetadataWorker


class AnchoredComboBox(QtWidgets.QComboBox):
    _POPUP_GAP = 8
    _POPUP_SCREEN_MARGIN = 16
    _POPUP_MAX_HEIGHT = 320
    _RADIUS = 12
    _OUTLINE_INSET = 1.0
    _BOTTOM_LINE_INSET = 14
    _BOTTOM_LINE_Y_OFFSET = 2

    def _style_popup_container(self) -> QtWidgets.QWidget | None:
        popup = self.view().window()
        if popup is None:
            return None
        popup.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        popup.setStyleSheet(
            "background: #ffffff; border: 1px solid #cfe0f5; border-radius: 18px;"
        )
        return popup

    def showPopup(self) -> None:  # pragma: no cover - UI path
        self._style_popup_container()
        super().showPopup()
        QtCore.QTimer.singleShot(0, self._reposition_popup)

    def _reposition_popup(self) -> None:  # pragma: no cover - UI path
        popup = self._style_popup_container()
        if popup is None or not popup.isVisible():
            return

        anchor = self.mapToGlobal(QtCore.QPoint(0, self.height() + self._POPUP_GAP))
        screen = QtGui.QGuiApplication.screenAt(anchor)
        if screen is None and self.window().windowHandle() is not None:
            screen = self.window().windowHandle().screen()
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        frame = popup.frameGeometry()

        min_x = available.left() + self._POPUP_SCREEN_MARGIN
        max_x = available.right() - frame.width() - self._POPUP_SCREEN_MARGIN + 1
        popup_x = max(min_x, min(anchor.x(), max_x))
        min_y = available.top() + self._POPUP_SCREEN_MARGIN
        max_y = available.bottom() - frame.height() - self._POPUP_SCREEN_MARGIN + 1
        popup_y = max(min_y, min(anchor.y(), max_y))

        popup.move(popup_x, popup_y)

    def _outline_color(self) -> QtGui.QColor:
        if not self.isEnabled():
            return QtGui.QColor("#d2e2f4")

        popup = self.view().window()
        if popup is not None and popup.isVisible():
            return QtGui.QColor("#93b9e9")
        if self.underMouse():
            return QtGui.QColor("#b9d2ee")
        return QtGui.QColor("#d2e2f4")

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # pragma: no cover - UI path
        super().paintEvent(event)

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        outline_color = self._outline_color()
        painter.setPen(QtGui.QPen(outline_color, 1))
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        inset = self._OUTLINE_INSET
        rect = QtCore.QRectF(self.rect()).adjusted(
            inset, inset, -inset, -inset - 1
        )
        painter.drawRoundedRect(rect, self._RADIUS, self._RADIUS)

        # Draw the lower edge slightly inside the clip rect so it stays visible
        # on Windows at fractional scaling, while also making the bottom segment
        # a touch narrower than the full rounded outline.
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QtGui.QPen(outline_color, 1))
        bottom_y = self.height() - self._BOTTOM_LINE_Y_OFFSET
        left = self._BOTTOM_LINE_INSET
        right = self.width() - self._BOTTOM_LINE_INSET - 1
        painter.drawLine(left, bottom_y, right, bottom_y)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.binaries: BinaryPaths = discover_binaries()
        self.metadata: dict[str, Any] | None = None
        self.formats: list[FormatOption] = []
        self.default_format_index = -1
        self.selected_format_index = -1
        self.query_thread: QtCore.QThread | None = None
        self.query_worker: MetadataWorker | None = None
        self.download_thread: QtCore.QThread | None = None
        self.download_worker: DownloadWorker | None = None
        self.settings = QtCore.QSettings("Codex", "BilibiliDownloader")
        self.dependencies_ok = False
        self._initial_center_pending = True
        self.download_path = ""
        self._base_compact_window_width = 900
        self._base_compact_window_height = 400
        self._minimum_window_width = 800
        self._minimum_window_height = 390
        self._reference_screen_width = 1920
        self._reference_screen_height = 1080
        self._compact_scale_min = 0.85
        self._compact_scale_max = 1.65
        self._compact_window_width = self._base_compact_window_width
        self._compact_window_height = self._base_compact_window_height
        self._results_window_height_padding = 8
        self._window_resize_animation_ms = 230
        self._window_resize_animation: QtCore.QPropertyAnimation | None = None
        self._hero_logo_top_margin = 20
        self._content_top_gap = 0
        self._native_window_styles_applied = False

        self.setWindowFlags(
            QtCore.Qt.WindowType.Window
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowSystemMenuHint
            | QtCore.Qt.WindowType.WindowMinimizeButtonHint
            | QtCore.Qt.WindowType.WindowMaximizeButtonHint
            | QtCore.Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.setWindowTitle("Bilibili 视频下载器")
        self._refresh_compact_window_size(QtGui.QGuiApplication.primaryScreen())
        self.resize(self._compact_window_width, self._compact_window_height)
        self.setMinimumSize(self._minimum_window_width, self._minimum_window_height)

        self._build_ui()
        self._apply_theme()
        self._restore_settings()
        self._validate_binaries()

    def _build_ui(self) -> None:
        self.central = QtWidgets.QWidget()
        self.central.setObjectName("CentralCanvas")
        self.central.installEventFilter(self)
        self.setCentralWidget(self.central)

        root_layout = QtWidgets.QVBoxLayout(self.central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.window_surface = QtWidgets.QFrame()
        self.window_surface.setObjectName("WindowSurface")
        self.window_surface.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.window_surface.installEventFilter(self)
        root_layout.addWidget(self.window_surface)

        outer_layout = QtWidgets.QVBoxLayout(self.window_surface)
        outer_layout.setContentsMargins(0, 0, 0, 14)
        outer_layout.setSpacing(8)

        self.title_bar = QtWidgets.QWidget()
        self.title_bar.setObjectName("TitleBar")
        self.title_bar.setFixedHeight(40)
        self.title_bar.installEventFilter(self)
        title_layout = QtWidgets.QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(0)
        title_layout.addStretch(1)

        self.minimize_button = QtWidgets.QToolButton()
        self.minimize_button.setObjectName("CaptionButton")
        self.minimize_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.minimize_button.clicked.connect(self.showMinimized)

        self.maximize_button = QtWidgets.QToolButton()
        self.maximize_button.setObjectName("CaptionButton")
        self.maximize_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.maximize_button.clicked.connect(self._toggle_maximize_restore)

        self.close_button = QtWidgets.QToolButton()
        self.close_button.setObjectName("CloseCaptionButton")
        self.close_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.close)

        title_layout.addWidget(self.minimize_button)
        title_layout.addWidget(self.maximize_button)
        title_layout.addWidget(self.close_button)
        outer_layout.addWidget(self.title_bar)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(20, 2, 20, 0)

        self.log_toggle_button = QtWidgets.QPushButton("显示日志")
        self.log_toggle_button.setObjectName("FloatingLogButton")
        self.log_toggle_button.setCheckable(True)
        self.log_toggle_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.log_toggle_button.clicked.connect(self._toggle_log_overlay)

        top_row.addStretch(1)
        top_row.addWidget(self.log_toggle_button)
        outer_layout.addLayout(top_row)

        self.body_layout = QtWidgets.QVBoxLayout()
        self.body_layout.setContentsMargins(20, 0, 20, 0)
        self.body_layout.setSpacing(0)
        self.body_layout.addSpacing(self._content_top_gap)

        self.content_shell = QtWidgets.QWidget()
        self.content_shell.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self.body_layout.addWidget(
            self.content_shell,
            0,
            QtCore.Qt.AlignmentFlag.AlignHCenter,
        )
        self.body_layout.addStretch(1)
        outer_layout.addLayout(self.body_layout, 1)

        content_layout = QtWidgets.QVBoxLayout(self.content_shell)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        self._sync_content_shell_width(self._compact_window_width)

        self.hero_card = self._make_card("HeroCard")
        hero_layout = QtWidgets.QVBoxLayout(self.hero_card)
        hero_layout.setContentsMargins(28, self._hero_logo_top_margin, 28, 24)
        hero_layout.setSpacing(18)

        self.hero_logo = QtSvgWidgets.QSvgWidget(str(static_asset("Bilibili_logo_1.svg")))
        self.hero_logo.setObjectName("HeroLogo")
        self.hero_logo.setFixedSize(276, 88)

        url_row = QtWidgets.QHBoxLayout()
        url_row.setSpacing(14)

        self.url_input = QtWidgets.QLineEdit()
        self.url_input.setPlaceholderText("粘贴 Bilibili 视频链接，例如 https://www.bilibili.com/video/BV...")
        self.url_input.setClearButtonEnabled(True)
        self.url_input.returnPressed.connect(self.start_query)

        self.query_button = QtWidgets.QPushButton("解析视频")
        self.query_button.setObjectName("PrimaryAction")
        self.query_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.query_button.setMinimumWidth(148)
        self.query_button.clicked.connect(self.start_query)

        url_row.addWidget(self.url_input, 1)
        url_row.addWidget(self.query_button)

        self.hero_status_label = QtWidgets.QLabel("")
        self.hero_status_label.setObjectName("HeroStatus")
        self.hero_status_label.setWordWrap(True)
        self.hero_status_label.hide()

        self.hero_status_bar = QtWidgets.QProgressBar()
        self.hero_status_bar.setRange(0, 0)
        self.hero_status_bar.setTextVisible(False)
        self.hero_status_bar.setFixedHeight(6)
        self.hero_status_bar.hide()

        hero_layout.addWidget(self.hero_logo, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        hero_layout.addSpacing(60)
        hero_layout.addLayout(url_row)
        hero_layout.addWidget(self.hero_status_label)
        hero_layout.addWidget(self.hero_status_bar)
        content_layout.addWidget(self.hero_card)

        self.info_card = self._make_card()
        info_layout = QtWidgets.QVBoxLayout(self.info_card)
        info_layout.setContentsMargins(22, 20, 22, 20)
        info_layout.setSpacing(10)

        self.video_title_prefix = QtWidgets.QLabel("标题")
        self.video_title_prefix.setObjectName("FieldLabel")

        self.video_title_label = QtWidgets.QLabel("尚未解析视频")
        self.video_title_label.setObjectName("InfoTitle")
        self.video_title_label.setWordWrap(True)

        self.video_meta_label = QtWidgets.QLabel("解析成功后会在这里显示标题、UP 主和时长。")
        self.video_meta_label.setObjectName("InfoMeta")
        self.video_meta_label.setWordWrap(True)

        self.current_spec_card = QtWidgets.QFrame()
        self.current_spec_card.setObjectName("SpecCard")
        current_spec_layout = QtWidgets.QVBoxLayout(self.current_spec_card)
        current_spec_layout.setContentsMargins(0, 6, 0, 4)
        current_spec_layout.setSpacing(6)

        spec_header = QtWidgets.QHBoxLayout()
        spec_header.setContentsMargins(0, 0, 0, 0)
        spec_header.setSpacing(8)

        self.spec_hint_label = QtWidgets.QLabel("可选规格")
        self.spec_hint_label.setObjectName("FieldLabel")

        self.spec_helper_label = QtWidgets.QLabel("点击可切换清晰度与编码")
        self.spec_helper_label.setObjectName("HintText")
        self.spec_helper_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )

        spec_header.addWidget(self.spec_hint_label)
        spec_header.addStretch(1)
        spec_header.addWidget(self.spec_helper_label)

        self.other_specs_combo = AnchoredComboBox()
        self.other_specs_combo.setObjectName("SpecCombo")
        self.other_specs_combo.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.other_specs_combo.setMaxVisibleItems(8)
        self.other_specs_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.other_specs_combo.setMinimumContentsLength(22)
        self.other_specs_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.other_specs_combo.setMinimumHeight(42)
        specs_view = QtWidgets.QListView()
        specs_view.setObjectName("SpecComboPopup")
        specs_view.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        specs_view.setMouseTracking(True)
        specs_view.setSpacing(2)
        specs_view.setUniformItemSizes(True)
        specs_view.setVerticalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        specs_view.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        specs_view.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.other_specs_combo.setView(specs_view)
        self.other_specs_combo._style_popup_container()
        self.other_specs_combo.currentIndexChanged.connect(self._on_other_spec_changed)

        current_spec_layout.addLayout(spec_header)
        current_spec_layout.addWidget(self.other_specs_combo)

        info_layout.addWidget(self.video_title_prefix)
        info_layout.addWidget(self.video_title_label)
        info_layout.addWidget(self.video_meta_label)
        info_layout.addWidget(self.current_spec_card)
        self.info_card.hide()
        content_layout.addWidget(self.info_card)

        self.controls_card = self._make_card()
        controls_layout = QtWidgets.QVBoxLayout(self.controls_card)
        controls_layout.setContentsMargins(22, 16, 22, 14)
        controls_layout.setSpacing(10)

        progress_header = QtWidgets.QHBoxLayout()
        progress_header.setContentsMargins(0, 0, 0, 0)
        progress_header.setSpacing(10)

        self.download_status_prefix = QtWidgets.QLabel("下载状态")
        self.download_status_prefix.setObjectName("FieldLabel")

        self.download_button = QtWidgets.QPushButton("开始下载")
        self.download_button.setObjectName("PrimaryAction")
        self.download_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.download_button.setMinimumWidth(174)
        self.download_button.setMinimumHeight(40)
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.start_download)

        self.progress_label = QtWidgets.QLabel("等待解析视频")
        self.progress_label.setObjectName("StatusValue")

        progress_header.addWidget(self.download_status_prefix)
        progress_header.addStretch(1)
        progress_header.addWidget(self.progress_label)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMinimumHeight(8)

        action_row = QtWidgets.QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(10)

        self.download_hint_label = QtWidgets.QLabel("确认规格后即可开始下载")
        self.download_hint_label.setObjectName("HintText")
        self.download_hint_label.setWordWrap(True)

        action_row.addWidget(self.download_hint_label, 1)
        action_row.addWidget(self.download_button, 0)

        controls_layout.addLayout(progress_header)
        controls_layout.addWidget(self.progress_bar)
        controls_layout.addLayout(action_row)
        self.controls_card.hide()
        content_layout.addWidget(self.controls_card)

        self.log_overlay = QtWidgets.QFrame(self.central)
        self.log_overlay.setObjectName("LogOverlay")
        self.log_overlay.hide()

        log_layout = QtWidgets.QVBoxLayout(self.log_overlay)
        log_layout.setContentsMargins(20, 18, 20, 18)
        log_layout.setSpacing(12)

        log_header = QtWidgets.QHBoxLayout()
        log_header.setSpacing(12)

        log_title = QtWidgets.QLabel("运行日志")
        log_title.setObjectName("SectionLabel")

        self.log_close_button = QtWidgets.QPushButton("关闭")
        self.log_close_button.setObjectName("OverlayCloseButton")
        self.log_close_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.log_close_button.clicked.connect(lambda: self._set_log_visibility(False))

        log_header.addWidget(log_title)
        log_header.addStretch(1)
        log_header.addWidget(self.log_close_button)

        self.log_output = QtWidgets.QPlainTextEdit()
        self.log_output.setObjectName("LogOutput")
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(500)

        log_layout.addLayout(log_header)
        log_layout.addWidget(self.log_output, 1)

        self._update_log_overlay_geometry()
        self._set_results_visible(False)
        self._sync_caption_buttons()
        self._update_content_mode(False)

    def _make_card(self, object_name: str = "Card") -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName(object_name)
        card.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        return card

    def _toggle_log_overlay(self) -> None:
        self._set_log_visibility(not self.log_overlay.isVisible())

    def _toggle_maximize_restore(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._sync_caption_buttons()

    def _sync_caption_buttons(self) -> None:
        self.minimize_button.setText("─")
        self.maximize_button.setText("❐" if self.isMaximized() else "□")
        self.close_button.setText("✕")

    def _update_content_mode(self, results_visible: bool) -> None:
        self.body_layout.setStretch(2, 2 if not results_visible else 0)

    def _set_log_visibility(self, visible: bool) -> None:
        self.log_overlay.setVisible(visible)
        self.log_toggle_button.blockSignals(True)
        self.log_toggle_button.setChecked(visible)
        self.log_toggle_button.blockSignals(False)
        self.log_toggle_button.setText("隐藏日志" if visible else "显示日志")
        if visible:
            self._update_log_overlay_geometry()
            self.log_overlay.raise_()
            self.log_output.ensureCursorVisible()

    def _update_log_overlay_geometry(self) -> None:
        if not hasattr(self, "log_overlay"):
            return
        parent_rect = self.central.rect()
        width = min(520, max(360, parent_rect.width() - 64))
        height = min(360, max(240, parent_rect.height() - 72))
        x = parent_rect.width() - width - 20
        y = 20
        self.log_overlay.setGeometry(x, y, width, height)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # pragma: no cover - UI path
        super().resizeEvent(event)
        self._update_log_overlay_geometry()

    def changeEvent(self, event: QtCore.QEvent) -> None:  # pragma: no cover - UI path
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.WindowStateChange:
            self._sync_caption_buttons()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # pragma: no cover - UI path
        super().showEvent(event)
        if not self._native_window_styles_applied:
            self._native_window_styles_applied = True
            self._apply_windows_taskbar_styles()
        if self._initial_center_pending:
            self._initial_center_pending = False
            QtCore.QTimer.singleShot(
                0,
                self._shrink_for_compact if not self.info_card.isVisible() else self._center_on_screen,
            )

    def _apply_windows_taskbar_styles(self) -> None:
        if sys.platform != "win32":
            return

        try:
            hwnd = int(self.winId())
        except Exception:
            return

        if not hwnd:
            return

        try:
            user32 = ctypes.windll.user32
            get_window_long_ptr = user32.GetWindowLongPtrW
            set_window_long_ptr = user32.SetWindowLongPtrW
            set_window_pos = user32.SetWindowPos

            GWL_STYLE = -16
            WS_MINIMIZEBOX = 0x00020000
            WS_MAXIMIZEBOX = 0x00010000
            WS_SYSMENU = 0x00080000
            WS_CAPTION = 0x00C00000
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020

            style = get_window_long_ptr(hwnd, GWL_STYLE)
            style |= WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU
            style &= ~WS_CAPTION
            set_window_long_ptr(hwnd, GWL_STYLE, style)
            set_window_pos(
                hwnd,
                0,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
            )
        except Exception:
            pass

    def _is_caption_control_hit(self, widget: QtWidgets.QWidget | None) -> bool:
        caption_controls = {
            getattr(self, "minimize_button", None),
            getattr(self, "maximize_button", None),
            getattr(self, "close_button", None),
            getattr(self, "log_toggle_button", None),
        }
        current = widget
        while current is not None:
            if current in caption_controls:
                return True
            current = current.parentWidget()
        return False

    def _is_in_drag_zone(self, global_pos: QtCore.QPoint) -> bool:
        local = self.window_surface.mapFromGlobal(global_pos)
        return (
            0 <= local.x() < self.window_surface.width()
            and 0 <= local.y() <= self.title_bar.height()
        )

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if not all(
            hasattr(self, name) for name in ("central", "window_surface", "title_bar")
        ):
            return super().eventFilter(watched, event)
        if watched in {self.title_bar, self.window_surface, self.central}:
            if (
                event.type() == QtCore.QEvent.Type.MouseButtonDblClick
                and isinstance(event, QtGui.QMouseEvent)
                and event.button() == QtCore.Qt.MouseButton.LeftButton
            ):
                global_pos = event.globalPosition().toPoint()
                hit_widget = QtWidgets.QApplication.widgetAt(global_pos)
                if not self._is_caption_control_hit(hit_widget) and self._is_in_drag_zone(global_pos):
                    self._toggle_maximize_restore()
                    return True
            if (
                event.type() == QtCore.QEvent.Type.MouseButtonPress
                and isinstance(event, QtGui.QMouseEvent)
                and event.button() == QtCore.Qt.MouseButton.LeftButton
            ):
                global_pos = event.globalPosition().toPoint()
                hit_widget = QtWidgets.QApplication.widgetAt(global_pos)
                if not self._is_caption_control_hit(hit_widget) and self._is_in_drag_zone(global_pos):
                    handle = self.windowHandle()
                    if handle is not None and not self.isMaximized():
                        handle.startSystemMove()
                        return True
        return super().eventFilter(watched, event)

    def _center_on_screen(self) -> None:
        window_handle = self.windowHandle()
        screen = window_handle.screen() if window_handle is not None else None
        if screen is None:
            screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        top_left = frame.topLeft()

        min_x = available.left()
        min_y = available.top()
        max_x = available.right() - frame.width() + 1
        max_y = available.bottom() - frame.height() + 1

        self.move(
            max(min_x, min(top_left.x(), max_x)),
            max(min_y, min(top_left.y(), max_y)),
        )

    def _target_window_geometry(
        self,
        width: int,
        height: int,
        screen: QtGui.QScreen | None,
    ) -> QtCore.QRect:
        current = self.geometry()
        if screen is None:
            rect = QtCore.QRect(current)
            rect.setSize(QtCore.QSize(width, height))
            return rect

        available = screen.availableGeometry()
        center = current.center()
        target = QtCore.QRect(0, 0, width, height)
        target.moveCenter(center)

        min_x = available.left()
        min_y = available.top()
        max_x = available.right() - target.width() + 1
        max_y = available.bottom() - target.height() + 1
        target.moveTo(
            max(min_x, min(target.x(), max_x)),
            max(min_y, min(target.y(), max_y)),
        )
        return target

    def _sync_content_shell_width(self, window_width: int) -> None:
        if not hasattr(self, "content_shell"):
            return

        shell_width = max(680, min(980, window_width - 140))
        self.content_shell.setMinimumWidth(shell_width)
        self.content_shell.setMaximumWidth(shell_width)

    def _resize_window_with_animation(
        self,
        target_width: int,
        target_height: int,
        animated: bool = True,
    ) -> None:
        screen = self.windowHandle().screen() if self.windowHandle() is not None else None
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()

        target_geometry = self._target_window_geometry(target_width, target_height, screen)
        if self.geometry() == target_geometry:
            return

        if self._window_resize_animation is not None:
            self._window_resize_animation.stop()

        self._sync_content_shell_width(target_width)

        if not animated:
            self.setGeometry(target_geometry)
            return

        if self._window_resize_animation is None:
            self._window_resize_animation = QtCore.QPropertyAnimation(
                self, b"geometry", self
            )

        self._window_resize_animation.setDuration(self._window_resize_animation_ms)
        self._window_resize_animation.setStartValue(self.geometry())
        self._window_resize_animation.setEndValue(target_geometry)
        self._window_resize_animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._window_resize_animation.start()

    def _refresh_compact_window_size(self, screen: QtGui.QScreen | None) -> None:
        if screen is None:
            self._compact_window_width = self._base_compact_window_width
            self._compact_window_height = self._base_compact_window_height
            return

        available = screen.availableGeometry()
        width_scale = available.width() / self._reference_screen_width
        height_scale = available.height() / self._reference_screen_height
        scale = min(width_scale, height_scale)
        scale = max(self._compact_scale_min, min(self._compact_scale_max, scale))

        target_width = int(round(self._base_compact_window_width * scale))
        target_height = int(round(self._base_compact_window_height * scale))
        max_width = max(self._minimum_window_width, available.width() - 80)
        max_height = max(self._minimum_window_height, available.height() - 80)

        self._compact_window_width = max(
            self._minimum_window_width, min(target_width, max_width)
        )
        self._compact_window_height = max(
            self._minimum_window_height, min(target_height, max_height)
        )
        self._sync_content_shell_width(self._compact_window_width)

    def _set_results_visible(self, visible: bool) -> None:
        self.info_card.setVisible(visible)
        self.controls_card.setVisible(visible)
        self._update_content_mode(visible)
        if visible:
            self._expand_for_results()
        else:
            self._shrink_for_compact()
        self.hero_card.setProperty("plain", True)
        self.style().unpolish(self.hero_card)
        self.style().polish(self.hero_card)
        self.hero_card.update()

    def _set_query_feedback(self, message: str = "", busy: bool = False) -> None:
        has_message = bool(message)
        self.hero_status_label.setVisible(has_message)
        self.hero_status_bar.setVisible(has_message and busy)
        self.hero_status_label.setText(message)

    def _expand_for_results(self) -> None:
        if self.isMaximized():
            return
        screen = self.windowHandle().screen() if self.windowHandle() is not None else None
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return

        layout = self.central.layout()
        if layout is not None:
            layout.activate()
        available = screen.availableGeometry()
        target_height = min(
            max(
                self.sizeHint().height() + self._results_window_height_padding,
                self._compact_window_height,
            ),
            available.height() - 80,
        )
        if target_height != self.height():
            self._resize_window_with_animation(self.width(), target_height, animated=True)

    def _shrink_for_compact(self) -> None:
        if self.isMaximized():
            return
        screen = self.windowHandle().screen() if self.windowHandle() is not None else None
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return

        self._refresh_compact_window_size(screen)
        target_width = self._compact_window_width
        target_height = self._compact_window_height
        if self.width() != target_width or self.height() != target_height:
            self._resize_window_with_animation(target_width, target_height, animated=True)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: transparent;
            }
            QWidget#CentralCanvas {
                background: transparent;
            }
            QFrame#WindowSurface {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #f8fbff,
                    stop: 1 #f2f6fd
                );
                border: 1px solid rgba(36, 44, 58, 0.22);
                border-radius: 8px;
            }
            QWidget#TitleBar {
                background: transparent;
            }
            QWidget {
                color: #17324d;
            }
            QFrame#Card, QFrame#HeroCard {
                background: rgba(255, 255, 255, 0.9);
                border: 1px solid #dbe6f3;
                border-radius: 28px;
            }
            QFrame#HeroCard {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #ffffff,
                    stop: 0.65 #f6faff,
                    stop: 1 #ebf3ff
                );
            }
            QFrame#HeroCard[plain="true"] {
                background: transparent;
                border: none;
            }
            QFrame#SpecCard {
                background: transparent;
                border: none;
                border-radius: 0;
            }
            QFrame#LogOverlay {
                background: rgba(255, 255, 255, 0.98);
                border: 1px solid #cfdceb;
                border-radius: 22px;
            }
            QLabel#HeroStatus {
                color: #5b7490;
                font-size: 13px;
            }
            QLabel#InfoTitle {
                color: #17324d;
                font-size: 24px;
                font-weight: 700;
            }
            QLabel#SpecTitle {
                color: #17324d;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#SectionLabel {
                color: #36506a;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QLabel#FieldLabel {
                color: #4f6781;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QLabel#InfoMeta {
                color: #587089;
                font-size: 13px;
            }
            QLabel#StatusLabel {
                color: #17324d;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#StatusValue {
                color: #17324d;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#HintText {
                color: #6f8499;
                font-size: 12px;
            }

            QLineEdit, QComboBox, QPlainTextEdit {
                background: rgba(255, 255, 255, 0.96);
                border: 1px solid #d7e3f0;
                border-radius: 18px;
                color: #17324d;
                selection-background-color: #d9e8ff;
                selection-color: #17324d;
            }
            QLineEdit, QComboBox {
                min-height: 48px;
                padding: 0 18px;
                font-size: 14px;
            }
            QComboBox#SpecCombo {
                background: rgba(255, 255, 255, 0.95);
                border: none;
                border-radius: 12px;
                padding: 0 12px;
                font-weight: 600;
                min-height: 42px;
                font-size: 13px;
            }
            QComboBox#SpecCombo:hover {
                background: #f9fcff;
            }
            QComboBox#SpecCombo:on {
                background: #ffffff;
            }
            QComboBox#SpecCombo:disabled {
                color: #17324d;
            }
            QComboBox::drop-down {
                border: none;
                width: 0px;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
                border: none;
                margin: 0;
            }
            QListView#SpecComboPopup {
                background: transparent;
                border: none;
                border-radius: 0;
                outline: 0;
                padding: 6px;
                show-decoration-selected: 1;
            }
            QListView#SpecComboPopup::item {
                min-height: 26px;
                padding: 4px 10px;
                margin: 1px 0;
                border-radius: 10px;
            }
            QListView#SpecComboPopup::item:hover {
                background: #f2f7ff;
            }
            QListView#SpecComboPopup::item:selected {
                background: #dfeaff;
                color: #17324d;
            }
            QListView#SpecComboPopup QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 6px 4px 6px 0;
            }
            QListView#SpecComboPopup QScrollBar::handle:vertical {
                background: #c8d7ea;
                min-height: 32px;
                border-radius: 5px;
            }
            QPlainTextEdit {
                padding: 12px;
                font-family: Consolas, "Courier New", monospace;
                font-size: 11px;
            }
            QPushButton {
                background: rgba(255, 255, 255, 0.85);
                border: 1px solid #d8e3f0;
                border-radius: 18px;
                color: #17324d;
                min-height: 44px;
                padding: 0 18px;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #f4f8ff;
                border-color: #b8d0ed;
            }
            QPushButton:pressed {
                background: #e5f0ff;
            }
            QPushButton#PrimaryAction {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #2f7ff7,
                    stop: 1 #1f67de
                );
                border: 1px solid #2b72e7;
                color: white;
            }
            QPushButton#PrimaryAction:hover {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #2a74e1,
                    stop: 1 #1b5cc6
                );
                border-color: #2367d6;
            }
            QPushButton#FloatingLogButton, QPushButton#OverlayCloseButton {
                min-height: 36px;
                padding: 0 16px;
                font-size: 13px;
            }
            QToolButton#CaptionButton, QToolButton#CloseCaptionButton {
                background: transparent;
                border: none;
                border-radius: 0;
                min-width: 48px;
                max-width: 48px;
                min-height: 40px;
                max-height: 40px;
                padding: 0;
                font-family: "Segoe UI Symbol", "Microsoft YaHei UI", "Microsoft JhengHei UI";
                font-size: 16px;
                font-weight: 500;
                color: #2f3f52;
            }
            QToolButton#CloseCaptionButton {
                border-top-right-radius: 8px;
            }
            QToolButton#CaptionButton:hover {
                background: rgba(40, 52, 68, 0.10);
                color: #18283b;
            }
            QToolButton#CaptionButton:pressed {
                background: rgba(40, 52, 68, 0.16);
                color: #0f2238;
            }
            QToolButton#CloseCaptionButton:hover {
                background: #e81123;
                color: white;
            }
            QToolButton#CloseCaptionButton:pressed {
                background: #c50f1f;
                color: white;
            }
            QPushButton:disabled {
                background: #f3f5f8;
                color: #98a7b7;
                border-color: #e2e8f0;
            }
            QProgressBar {
                background: #e4ecf7;
                border: 1px solid #d5e1ef;
                border-radius: 7px;
            }
            QProgressBar::chunk {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #2f7ff7,
                    stop: 1 #20c0d5
                );
                border-radius: 7px;
            }
            """
        )

    def _restore_settings(self) -> None:
        download_path = self.settings.value("download_path", type=str)
        if not download_path or not Path(download_path).exists():
            download_path = self._default_save_directory()
        self.download_path = download_path
        self._set_log_visibility(self.settings.value("show_logs", False, type=bool))
        screen = QtGui.QGuiApplication.primaryScreen()
        self._refresh_compact_window_size(screen)

        size_value = self.settings.value("window_size")
        if isinstance(size_value, QtCore.QSize):
            width = size_value.width()
            height = size_value.height()

            if screen is not None:
                available = screen.availableGeometry()
                if width >= int(available.width() * 0.9) or height >= int(available.height() * 0.9):
                    width = self._compact_window_width
                    height = self._compact_window_height
                width = min(width, max(self.minimumWidth(), available.width() - 80))
                height = min(height, max(self.minimumHeight(), available.height() - 80))

            width = max(width, self.minimumWidth())
            height = max(height, self.minimumHeight())

            # Shrink previously saved default heights so the window feels less tall.
            if (size_value.width(), size_value.height()) in {
                (1080, 760),
                (1120, 760),
                (1240, 820),
                (1360, 720),
                (1280, 720),
            }:
                width = self._compact_window_width
                height = self._compact_window_height

            self.resize(width, height)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # pragma: no cover - UI path
        self.settings.setValue("download_path", self.download_path)
        self.settings.setValue("show_logs", self.log_overlay.isVisible())
        self.settings.setValue("window_size", self.size())
        super().closeEvent(event)

    def _validate_binaries(self) -> None:
        missing = self.binaries.missing()
        self.dependencies_ok = not missing
        if self.dependencies_ok:
            self.append_log(f"已找到依赖目录: {self.binaries.ffmpeg_dir}")
            return

        for path in missing:
            self.append_log(f"缺少依赖文件: {path}")

        self._set_log_visibility(True)
        self._set_results_visible(True)
        self.info_card.hide()
        self.query_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.progress_label.setText("缺少依赖，请确认 exe 同级 bin 目录中包含 yt-dlp 和 ffmpeg")
        self.download_hint_label.setText("请先补齐依赖后再下载")
    def append_log(self, message: str) -> None:
        timestamp = QtCore.QDateTime.currentDateTime().toString("HH:mm:ss")
        self.log_output.appendPlainText(f"[{timestamp}] {message}")
        if self.log_overlay.isVisible():
            self.log_output.ensureCursorVisible()

    def _default_save_directory(self) -> str:
        return str(app_root())

    def pick_save_directory(self) -> str:
        current = self.download_path if self.download_path and Path(self.download_path).exists() else self._default_save_directory()
        dialog = QtWidgets.QFileDialog(self, "选择保存目录")
        dialog.setFileMode(QtWidgets.QFileDialog.FileMode.Directory)
        dialog.setOption(QtWidgets.QFileDialog.Option.ShowDirsOnly, True)
        dialog.setDirectory(current)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return ""
        selected = dialog.selectedFiles()
        directory = selected[0] if selected else ""
        if directory:
            self.download_path = directory
            self.settings.setValue("download_path", directory)
            return directory
        return ""

    def start_query(self) -> None:
        if self.query_thread is not None or not self.dependencies_ok:
            return

        raw_url = self.url_input.text().strip()
        if not raw_url:
            self._set_query_feedback("请输入 Bilibili 链接后再解析")
            self.progress_label.setText("请先输入 Bilibili 链接")
            self.url_input.setFocus()
            return

        url = normalize_video_url(raw_url)
        self.url_input.setCursorPosition(0)
        self.url_input.home(False)
        if url != raw_url:
            self.append_log(f"已标准化链接用于解析: {url}")

        self.metadata = None
        self.formats = []
        self.default_format_index = -1
        self.selected_format_index = -1
        self._set_results_visible(False)
        self.other_specs_combo.blockSignals(True)
        self.other_specs_combo.clear()
        self.other_specs_combo.blockSignals(False)
        self.download_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText("等待解析")
        self.download_hint_label.setText("解析完成后可直接下载")
        self.query_button.setText("正在解析...")
        self.query_button.setEnabled(False)
        self._set_query_feedback("正在解析视频，请稍候...", busy=True)
        self.append_log(f"开始解析链接: {url}")

        self.query_thread = QtCore.QThread(self)
        self.query_worker = MetadataWorker(url, self.binaries)
        self.query_worker.moveToThread(self.query_thread)

        self.query_thread.started.connect(self.query_worker.run)
        self.query_worker.finished.connect(self._on_query_finished)
        self.query_worker.error.connect(self._on_query_error)
        self.query_worker.finished.connect(self.query_thread.quit)
        self.query_worker.error.connect(self.query_thread.quit)
        self.query_thread.finished.connect(self._cleanup_query_worker)
        self.query_thread.start()

    def _cleanup_query_worker(self) -> None:
        if self.query_worker is not None:
            self.query_worker.deleteLater()
        if self.query_thread is not None:
            self.query_thread.deleteLater()
        self.query_worker = None
        self.query_thread = None
        self.query_button.setText("解析视频")
        self.query_button.setEnabled(self.dependencies_ok)

    def _on_query_finished(
        self,
        metadata: dict[str, Any],
        options: list[FormatOption],
        default_index: int,
    ) -> None:
        self.metadata = metadata
        self.formats = options
        self.default_format_index = default_index
        self.selected_format_index = default_index

        self._populate_video_summary(metadata)
        self._populate_format_choices(options, default_index)
        self._update_current_format_card()
        self._set_results_visible(True)
        self._set_query_feedback()
        self.progress_bar.setValue(0)
        self.progress_label.setText("已就绪")
        self.download_hint_label.setText("已选中推荐规格，可直接开始下载")
        self.download_button.setEnabled(True)
        self.append_log(f"解析完成，共找到 {len(options)} 个可下载视频格式")

    def _on_query_error(self, message: str) -> None:
        self._set_log_visibility(True)
        self._set_results_visible(False)
        self._set_query_feedback("解析失败，请检查链接或查看日志")
        self.progress_label.setText("解析失败")
        self.download_hint_label.setText("请修正链接后重试")
        self.append_log(f"解析失败: {message}")

    def _populate_video_summary(self, metadata: dict[str, Any]) -> None:
        title = str(metadata.get("title") or "未命名视频")
        uploader = str(metadata.get("uploader") or metadata.get("channel") or "未知 UP 主")
        duration = format_duration(metadata.get("duration"))
        self.video_title_label.setText(title)
        self.video_meta_label.setText(f"{uploader}  |  时长 {duration}")

    def _populate_format_choices(self, options: list[FormatOption], default_index: int) -> None:
        self.other_specs_combo.blockSignals(True)
        self.other_specs_combo.clear()
        for index, option in enumerate(options):
            self.other_specs_combo.addItem(
                self._format_option_label(option, recommended=index == default_index),
                index,
            )
        combo_index = default_index if 0 <= default_index < len(options) else 0
        self.other_specs_combo.setCurrentIndex(combo_index if options else -1)
        self.other_specs_combo.blockSignals(False)
        self.other_specs_combo.setEnabled(len(options) > 1)
        if len(options) > 1:
            self.spec_helper_label.setText("点击可切换清晰度与编码")
        elif len(options) == 1:
            self.spec_helper_label.setText("当前仅有一种可用规格")
        else:
            self.spec_helper_label.setText("暂无可用规格")

    def _format_option_label(self, option: FormatOption, *, recommended: bool = False) -> str:
        parts = [
            f"分辨率 {option.resolution_label}",
            f"容器 {option.ext.upper()}",
            f"编码 {option.codec_label}",
            f"预计大小 {option.size_label}",
        ]
        prefix = "推荐 | " if recommended else ""
        return prefix + " | ".join(parts)


    def _on_other_spec_changed(self, combo_index: int) -> None:
        if combo_index < 0:
            return
        data = self.other_specs_combo.itemData(combo_index)
        if not isinstance(data, int):
            return
        self.selected_format_index = data
        self._update_current_format_card()

    def _update_current_format_card(self) -> None:
        current_row = self.current_format_index()
        if current_row < 0 or current_row >= len(self.formats):
            return

        combo_index = self.other_specs_combo.findData(current_row)
        if combo_index >= 0 and combo_index != self.other_specs_combo.currentIndex():
            self.other_specs_combo.blockSignals(True)
            self.other_specs_combo.setCurrentIndex(combo_index)
            self.other_specs_combo.blockSignals(False)

        if self.dependencies_ok and self.download_thread is None:
            self.download_button.setEnabled(True)

    def current_format_index(self) -> int:
        return self.selected_format_index

    def start_download(self) -> None:
        if self.download_thread is not None or not self.dependencies_ok:
            return
        if self.metadata is None:
            self.progress_label.setText("请先解析视频，再开始下载")
            self.download_hint_label.setText("完成解析后可选择规格并下载")
            return

        selected_index = self.current_format_index()
        if selected_index < 0 or selected_index >= len(self.formats):
            self.progress_label.setText("请先选择一个视频规格")
            self.download_hint_label.setText("可在“可选规格”中切换清晰度与编码")
            return

        save_dir_text = self.pick_save_directory()
        if not save_dir_text:
            self.progress_label.setText("已取消选择保存位置")
            self.download_hint_label.setText("请重新选择下载目录")
            return

        save_dir = Path(save_dir_text).expanduser()
        save_dir.mkdir(parents=True, exist_ok=True)
        self.settings.setValue("download_path", str(save_dir))

        url = normalize_video_url(self.url_input.text().strip())
        option = self.formats[selected_index]
        video_id = str(self.metadata.get("id") or "")

        self.download_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText("准备下载")
        self.download_hint_label.setText("正在建立下载任务...")
        self.append_log(f"准备下载，规格：{self._format_option_label(option)}，保存目录：{save_dir}")

        self.download_thread = QtCore.QThread(self)
        self.download_worker = DownloadWorker(
            url, save_dir, option, self.binaries, video_id=video_id
        )
        self.download_worker.moveToThread(self.download_thread)

        self.download_thread.started.connect(self.download_worker.run)
        self.download_worker.progress.connect(self._on_download_progress)
        self.download_worker.log.connect(self.append_log)
        self.download_worker.completed.connect(self._on_download_completed)
        self.download_worker.error.connect(self._on_download_error)
        self.download_worker.finished.connect(self.download_thread.quit)
        self.download_thread.finished.connect(self._cleanup_download_worker)
        self.download_thread.start()

    def _cleanup_download_worker(self) -> None:
        if self.download_worker is not None:
            self.download_worker.deleteLater()
        if self.download_thread is not None:
            self.download_thread.deleteLater()
        self.download_worker = None
        self.download_thread = None
        self.download_button.setEnabled(bool(self.formats) and self.dependencies_ok)

    def _on_download_progress(self, value: int, percent_text: str, speed_text: str, eta_text: str) -> None:
        self.progress_bar.setValue(max(0, min(value, 100)))
        self.progress_label.setText(percent_text or f"{value}%")
        hint_parts: list[str] = []
        if speed_text:
            hint_parts.append(f"速度 {speed_text}")
        if eta_text:
            hint_parts.append(f"剩余 {eta_text}")
        self.download_hint_label.setText("  |  ".join(hint_parts) if hint_parts else "正在下载...")

    def _on_download_completed(self, final_target: str) -> None:
        self.progress_bar.setValue(100)
        self.progress_label.setText("已完成")
        file_name = Path(final_target).name if final_target else "文件"
        self.download_hint_label.setText(f"下载完成：{file_name}")
        self.append_log(f"下载完成，文件保存路径：{final_target}")

    def _on_download_error(self, message: str) -> None:
        self._set_log_visibility(True)
        self.progress_label.setText("下载失败")
        self.download_hint_label.setText("请检查网络或规格后重试")
        self.append_log(f"下载失败: {message}")
