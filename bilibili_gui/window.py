from __future__ import annotations

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
        self._compact_window_width = 1060
        self._compact_window_height = 470

        self.setWindowFlags(
            QtCore.Qt.WindowType.Window | QtCore.Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.setWindowTitle("Bilibili 视频下载器")
        self.resize(self._compact_window_width, self._compact_window_height)
        self.setMinimumSize(860, 430)

        self._build_ui()
        self._apply_theme()
        self._restore_settings()
        self._validate_binaries()

    def _build_ui(self) -> None:
        self.central = QtWidgets.QWidget()
        self.central.setObjectName("CentralCanvas")
        self.setCentralWidget(self.central)

        root_layout = QtWidgets.QVBoxLayout(self.central)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(0)

        self.window_surface = QtWidgets.QFrame()
        self.window_surface.setObjectName("WindowSurface")
        root_layout.addWidget(self.window_surface)

        outer_layout = QtWidgets.QVBoxLayout(self.window_surface)
        outer_layout.setContentsMargins(20, 14, 20, 20)
        outer_layout.setSpacing(12)

        self.title_bar = QtWidgets.QWidget()
        self.title_bar.setObjectName("TitleBar")
        self.title_bar.setFixedHeight(42)
        self.title_bar.installEventFilter(self)
        title_layout = QtWidgets.QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(0, 0, 2, 0)
        title_layout.setSpacing(2)
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
        top_row.setContentsMargins(0, 0, 0, 0)

        self.log_toggle_button = QtWidgets.QPushButton("显示日志")
        self.log_toggle_button.setObjectName("FloatingLogButton")
        self.log_toggle_button.setCheckable(True)
        self.log_toggle_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.log_toggle_button.clicked.connect(self._toggle_log_overlay)

        top_row.addStretch(1)
        top_row.addWidget(self.log_toggle_button)
        outer_layout.addLayout(top_row)

        self.body_layout = QtWidgets.QVBoxLayout()
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(0)
        self.body_layout.addStretch(1)

        self.content_shell = QtWidgets.QWidget()
        self.content_shell.setMinimumWidth(820)
        self.content_shell.setMaximumWidth(900)
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
        content_layout.setSpacing(14)

        self.hero_card = self._make_card("HeroCard")
        hero_layout = QtWidgets.QVBoxLayout(self.hero_card)
        hero_layout.setContentsMargins(28, 28, 28, 26)
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
        hero_layout.addSpacing(18)
        hero_layout.addLayout(url_row)
        hero_layout.addWidget(self.hero_status_label)
        hero_layout.addWidget(self.hero_status_bar)
        content_layout.addWidget(self.hero_card)

        self.info_card = self._make_card()
        info_layout = QtWidgets.QVBoxLayout(self.info_card)
        info_layout.setContentsMargins(22, 20, 22, 20)
        info_layout.setSpacing(12)

        self.video_title_label = QtWidgets.QLabel("尚未解析视频")
        self.video_title_label.setObjectName("InfoTitle")
        self.video_title_label.setWordWrap(True)

        self.video_meta_label = QtWidgets.QLabel("解析成功后会在这里显示标题、UP 主和时长。")
        self.video_meta_label.setObjectName("InfoMeta")
        self.video_meta_label.setWordWrap(True)

        self.current_spec_card = QtWidgets.QFrame()
        self.current_spec_card.setObjectName("SpecCard")
        current_spec_layout = QtWidgets.QVBoxLayout(self.current_spec_card)
        current_spec_layout.setContentsMargins(14, 10, 14, 10)
        current_spec_layout.setSpacing(0)

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

        current_spec_layout.addWidget(self.other_specs_combo)

        info_layout.addWidget(self.video_title_label)
        info_layout.addWidget(self.video_meta_label)
        info_layout.addWidget(self.current_spec_card)
        self.info_card.hide()
        content_layout.addWidget(self.info_card)

        self.controls_card = self._make_card()
        controls_layout = QtWidgets.QVBoxLayout(self.controls_card)
        controls_layout.setContentsMargins(22, 20, 22, 20)
        controls_layout.setSpacing(12)

        progress_row = QtWidgets.QHBoxLayout()
        progress_row.setSpacing(12)

        self.download_button = QtWidgets.QPushButton("开始下载")
        self.download_button.setObjectName("PrimaryAction")
        self.download_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.download_button.setMinimumWidth(148)
        self.download_button.setMinimumHeight(40)
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.start_download)

        progress_row.addWidget(self.download_button, 0)

        self.progress_label = QtWidgets.QLabel("等待解析视频")
        self.progress_label.setObjectName("StatusLabel")

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMinimumHeight(14)
        progress_row.insertWidget(0, self.progress_bar, 1)

        controls_layout.addWidget(self.progress_label)
        controls_layout.addLayout(progress_row)
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
        self.minimize_button.setText("—")
        self.maximize_button.setText("❐" if self.isMaximized() else "▢")
        self.close_button.setText("✕")

    def _update_content_mode(self, results_visible: bool) -> None:
        self.body_layout.setStretch(0, 1 if not results_visible else 0)
        self.body_layout.setStretch(2, 2 if not results_visible else 1)

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
        if self._initial_center_pending:
            self._initial_center_pending = False
            QtCore.QTimer.singleShot(
                0,
                self._shrink_for_compact if not self.info_card.isVisible() else self._center_on_screen,
            )

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self.title_bar:
            if (
                event.type() == QtCore.QEvent.Type.MouseButtonDblClick
                and isinstance(event, QtGui.QMouseEvent)
                and event.button() == QtCore.Qt.MouseButton.LeftButton
            ):
                self._toggle_maximize_restore()
                return True
            if (
                event.type() == QtCore.QEvent.Type.MouseButtonPress
                and isinstance(event, QtGui.QMouseEvent)
                and event.button() == QtCore.Qt.MouseButton.LeftButton
            ):
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

    def _set_results_visible(self, visible: bool) -> None:
        self.info_card.setVisible(visible)
        self.controls_card.setVisible(visible)
        self._update_content_mode(visible)
        if visible:
            self._expand_for_results()
        else:
            self._shrink_for_compact()
        self.hero_card.setProperty("plain", not visible)
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

        available = screen.availableGeometry()
        target_height = min(max(self.sizeHint().height() + 48, self.height()), available.height() - 80)
        if target_height > self.height():
            self.resize(self.width(), target_height)
            self._center_on_screen()

    def _shrink_for_compact(self) -> None:
        if self.isMaximized():
            return
        screen = self.windowHandle().screen() if self.windowHandle() is not None else None
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        target_width = min(self._compact_window_width, available.width() - 80)
        target_height = min(self._compact_window_height, available.height() - 80)
        if self.width() != target_width or self.height() != target_height:
            self.resize(target_width, target_height)
            self._center_on_screen()

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
                border-radius: 28px;
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
                background: #f6faff;
                border: 1px solid #d8e6f5;
                border-radius: 20px;
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
            QLabel#InfoMeta {
                color: #587089;
                font-size: 13px;
            }
            QLabel#StatusLabel {
                color: #17324d;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#HintText {
                color: #6b8197;
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
                background: transparent;
                border: none;
                border-radius: 0;
                padding: 0;
                font-weight: 600;
                min-height: 30px;
                font-size: 13px;
            }
            QComboBox#SpecCombo:hover {
                background: transparent;
                border: none;
            }
            QComboBox#SpecCombo:on {
                background: transparent;
                border: none;
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
                min-height: 40px;
                padding: 0 16px;
                font-size: 13px;
            }
            QToolButton#CaptionButton, QToolButton#CloseCaptionButton {
                background: transparent;
                border: none;
                border-radius: 8px;
                min-width: 38px;
                max-width: 38px;
                min-height: 30px;
                max-height: 30px;
                padding: 0;
                font-size: 12px;
                font-weight: 500;
                color: #3d4f63;
            }
            QToolButton#CaptionButton:hover {
                background: rgba(40, 52, 68, 0.10);
                color: #1a2d43;
            }
            QToolButton#CaptionButton:pressed {
                background: rgba(40, 52, 68, 0.16);
                color: #10243a;
            }
            QToolButton#CloseCaptionButton:hover {
                background: rgba(232, 62, 86, 0.14);
                color: #b42335;
            }
            QToolButton#CloseCaptionButton:pressed {
                background: rgba(232, 62, 86, 0.2);
                color: #9c1f30;
            }
            QPushButton:disabled {
                background: #f3f5f8;
                color: #98a7b7;
                border-color: #e2e8f0;
            }
            QProgressBar {
                background: #e6edf6;
                border: none;
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

        size_value = self.settings.value("window_size")
        if isinstance(size_value, QtCore.QSize):
            width = size_value.width()
            height = size_value.height()

            screen = QtGui.QGuiApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                if width >= int(available.width() * 0.9) or height >= int(available.height() * 0.9):
                    width = self._compact_window_width
                    height = self._compact_window_height

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
        self.progress_label.setText("视频信息已就绪，可以开始下载")
        self.download_button.setEnabled(True)
        self.append_log(f"解析完成，共找到 {len(options)} 个可下载视频格式")

    def _on_query_error(self, message: str) -> None:
        self._set_log_visibility(True)
        self._set_results_visible(False)
        self._set_query_feedback("解析失败，请检查链接或查看日志")
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
            return

        selected_index = self.current_format_index()
        if selected_index < 0 or selected_index >= len(self.formats):
            self.progress_label.setText("请先选择一个视频规格")
            return

        save_dir_text = self.pick_save_directory()
        if not save_dir_text:
            self.progress_label.setText("已取消选择保存位置")
            return

        save_dir = Path(save_dir_text).expanduser()
        save_dir.mkdir(parents=True, exist_ok=True)
        self.settings.setValue("download_path", str(save_dir))

        url = normalize_video_url(self.url_input.text().strip())
        option = self.formats[selected_index]
        video_id = str(self.metadata.get("id") or "")

        self.download_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText("正在下载...")
        self.append_log(f"准备下载，规格 {self._format_option_label(option)}，保存目录 {save_dir}")

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
        self.progress_label.setText(f"下载进度 {percent_text or f'{value}%'}")

    def _on_download_completed(self, final_target: str) -> None:
        self.progress_bar.setValue(100)
        self.progress_label.setText(f"下载完成: {final_target}")
        self.append_log(f"下载完成，文件保存路径：{final_target}")

    def _on_download_error(self, message: str) -> None:
        self._set_log_visibility(True)
        self.progress_label.setText("下载失败")
        self.append_log(f"下载失败: {message}")
