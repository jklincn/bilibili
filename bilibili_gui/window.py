from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .core import (
    BinaryPaths,
    FormatOption,
    app_root,
    discover_binaries,
    format_duration,
    normalize_video_url,
)
from .workers import DownloadWorker, MetadataWorker


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.binaries: BinaryPaths = discover_binaries()
        self.metadata: dict[str, Any] | None = None
        self.formats: list[FormatOption] = []
        self.query_thread: QtCore.QThread | None = None
        self.query_worker: MetadataWorker | None = None
        self.download_thread: QtCore.QThread | None = None
        self.download_worker: DownloadWorker | None = None
        self.settings = QtCore.QSettings("Codex", "BilibiliDownloader")
        self.dependencies_ok = False

        self.setWindowTitle("Bilibili 视频下载器")
        self.resize(1120, 760)
        self.setMinimumSize(1040, 760)

        self._build_ui()
        self._apply_theme()
        self._restore_settings()
        self._validate_binaries()

    def _build_ui(self) -> None:
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setCentralWidget(self.scroll_area)

        page = QtWidgets.QWidget()
        self.scroll_area.setWidget(page)

        root_layout = QtWidgets.QVBoxLayout(page)
        root_layout.setContentsMargins(18, 16, 18, 18)
        root_layout.setSpacing(12)

        hero_card = self._make_card("HeroCard")
        hero_layout = QtWidgets.QVBoxLayout(hero_card)
        hero_layout.setContentsMargins(20, 18, 20, 18)
        hero_layout.setSpacing(12)

        hero_header = QtWidgets.QHBoxLayout()
        hero_header.setSpacing(16)

        hero_text_layout = QtWidgets.QVBoxLayout()
        hero_text_layout.setSpacing(4)

        hero_title = QtWidgets.QLabel("Bilibili 视频下载器")
        hero_title.setObjectName("HeroTitle")
        hero_subtitle = QtWidgets.QLabel(
            "贴入链接后快速解析清晰度、大小和编码，默认推荐最高分辨率的 H.264 版本。"
        )
        hero_subtitle.setObjectName("HeroSubtitle")
        hero_subtitle.setWordWrap(True)

        hero_text_layout.addWidget(hero_title)
        hero_text_layout.addWidget(hero_subtitle)

        self.platform_tag = QtWidgets.QLabel("仅限 Bilibili")
        self.platform_tag.setObjectName("Tag")
        self.platform_tag.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.platform_tag.setFixedSize(136, 42)

        hero_header.addLayout(hero_text_layout, 1)
        hero_header.addWidget(self.platform_tag, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        hero_layout.addLayout(hero_header)

        url_row = QtWidgets.QHBoxLayout()
        url_row.setSpacing(10)

        self.url_input = QtWidgets.QLineEdit()
        self.url_input.setPlaceholderText("粘贴 Bilibili 视频链接，例如 https://www.bilibili.com/video/BV...")
        self.url_input.setClearButtonEnabled(True)
        self.url_input.returnPressed.connect(self.start_query)

        self.query_button = QtWidgets.QPushButton("解析视频")
        self.query_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.query_button.setMinimumWidth(136)
        self.query_button.clicked.connect(self.start_query)

        url_row.addWidget(self.url_input, 1)
        url_row.addWidget(self.query_button, 0)
        hero_layout.addLayout(url_row)

        url_hint = QtWidgets.QLabel("解析后可直接选择清晰度、封装和编码版本。")
        url_hint.setObjectName("HintText")
        hero_layout.addWidget(url_hint)
        root_layout.addWidget(hero_card)

        info_card = self._make_card()
        info_layout = QtWidgets.QVBoxLayout(info_card)
        info_layout.setContentsMargins(18, 16, 18, 16)
        info_layout.setSpacing(8)

        info_title = QtWidgets.QLabel("视频信息和规格")
        info_title.setObjectName("SectionTitle")
        info_layout.addWidget(info_title)

        self.video_title_label = QtWidgets.QLabel("还没有解析视频")
        self.video_title_label.setObjectName("InfoTitle")
        self.video_title_label.setWordWrap(True)

        self.video_meta_label = QtWidgets.QLabel("这里会显示标题、UP 主、时长和视频编号。")
        self.video_meta_label.setObjectName("InfoMeta")
        self.video_meta_label.setWordWrap(True)

        self.selection_hint_label = QtWidgets.QLabel("解析完成后会自动选中最高分辨率的 H.264 版本。")
        self.selection_hint_label.setObjectName("HintText")
        self.selection_hint_label.setWordWrap(True)

        info_layout.addWidget(self.video_title_label)
        info_layout.addWidget(self.video_meta_label)
        info_layout.addWidget(self.selection_hint_label)

        self.format_table = QtWidgets.QTableWidget(0, 8)
        self.format_table.setObjectName("FormatTable")
        self.format_table.setAlternatingRowColors(True)
        self.format_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.format_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.format_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.format_table.setShowGrid(False)
        self.format_table.setMinimumHeight(200)
        self.format_table.setMaximumHeight(220)
        self.format_table.setHorizontalHeaderLabels(
            ["默认", "分辨率", "封装", "视频编码", "音频", "大小", "帧率", "说明"]
        )
        self.format_table.verticalHeader().setVisible(False)
        self.format_table.horizontalHeader().setStretchLastSection(True)
        self.format_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.format_table.horizontalHeader().setSectionResizeMode(
            7, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.format_table.itemSelectionChanged.connect(self._on_format_selection_changed)
        info_layout.addWidget(self.format_table)
        root_layout.addWidget(info_card)

        controls_card = self._make_card()
        controls_layout = QtWidgets.QVBoxLayout(controls_card)
        controls_layout.setContentsMargins(18, 16, 18, 16)
        controls_layout.setSpacing(8)

        self.log_toggle_button = QtWidgets.QPushButton("显示日志")
        self.log_toggle_button.setObjectName("LogToggleButton")
        self.log_toggle_button.setCheckable(True)
        self.log_toggle_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.log_toggle_button.setMinimumWidth(112)
        self.log_toggle_button.toggled.connect(self._set_log_visibility)

        save_row = QtWidgets.QHBoxLayout()
        save_row.setSpacing(8)

        self.save_path_input = QtWidgets.QLineEdit()
        self.save_path_input.setPlaceholderText("选择保存目录")
        self.save_path_input.setClearButtonEnabled(True)

        self.browse_button = QtWidgets.QPushButton("选择位置")
        self.browse_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.browse_button.setMinimumWidth(112)
        self.browse_button.clicked.connect(self.pick_save_directory)

        self.open_folder_button = QtWidgets.QPushButton("打开目录")
        self.open_folder_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.open_folder_button.setMinimumWidth(112)
        self.open_folder_button.clicked.connect(self.open_save_directory)

        save_row.addWidget(self.save_path_input, 1)
        save_row.addWidget(self.browse_button)
        save_row.addWidget(self.open_folder_button)
        controls_layout.addLayout(save_row)

        action_row = QtWidgets.QHBoxLayout()
        action_row.setSpacing(8)

        self.download_button = QtWidgets.QPushButton("开始下载")
        self.download_button.setObjectName("PrimaryAction")
        self.download_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.download_button.setMinimumWidth(132)
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.start_download)

        self.cancel_button = QtWidgets.QPushButton("取消下载")
        self.cancel_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.cancel_button.setMinimumWidth(132)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_download)

        action_row.addWidget(self.download_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.log_toggle_button)
        action_row.addStretch(1)
        controls_layout.addLayout(action_row)

        self.progress_label = QtWidgets.QLabel("等待下载任务")
        self.progress_label.setObjectName("StatusLabel")

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMinimumHeight(14)

        self.detail_label = QtWidgets.QLabel("速度和剩余时间会显示在这里")
        self.detail_label.setObjectName("HintText")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

        controls_layout.addWidget(self.progress_label)
        controls_layout.addWidget(self.progress_bar)
        controls_layout.addWidget(self.detail_label)
        root_layout.addWidget(controls_card)

        self.log_card = self._make_card()
        log_layout = QtWidgets.QVBoxLayout(self.log_card)
        log_layout.setContentsMargins(20, 18, 20, 18)
        log_layout.setSpacing(10)

        log_title = QtWidgets.QLabel("运行日志")
        log_title.setObjectName("SectionTitle")

        self.log_output = QtWidgets.QPlainTextEdit()
        self.log_output.setObjectName("LogOutput")
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(170)
        self.log_output.setMaximumHeight(220)
        self.log_output.setMaximumBlockCount(500)

        log_layout.addWidget(log_title)
        log_layout.addWidget(self.log_output)
        self.log_card.setVisible(False)
        root_layout.addWidget(self.log_card)
        root_layout.addStretch(1)

    def _make_card(self, object_name: str = "Card") -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName(object_name)
        card.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        return card

    def _set_log_visibility(self, visible: bool) -> None:
        self.log_card.setVisible(visible)
        if self.log_toggle_button.isChecked() != visible:
            self.log_toggle_button.blockSignals(True)
            self.log_toggle_button.setChecked(visible)
            self.log_toggle_button.blockSignals(False)
        self.log_toggle_button.setText("隐藏日志" if visible else "显示日志")
        if visible:
            QtCore.QTimer.singleShot(0, self.log_output.ensureCursorVisible)
            QtCore.QTimer.singleShot(
                0, lambda: self.scroll_area.ensureWidgetVisible(self.log_card, 0, 24)
            )

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #f4f7fb;
            }
            QScrollArea {
                border: none;
                background: #f4f7fb;
            }
            QFrame#Card, QFrame#HeroCard {
                background: white;
                border: 1px solid #d9e3ef;
                border-radius: 20px;
            }
            QFrame#HeroCard {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #ffffff,
                    stop: 0.52 #eef5ff,
                    stop: 1 #e6effd
                );
                border: 1px solid #cfdff2;
            }
            QLabel#HeroTitle {
                color: #17324d;
                font-size: 28px;
                font-weight: 700;
            }
            QLabel#HeroSubtitle {
                color: #57708b;
                font-size: 13px;
            }
            QLabel#Tag {
                background: rgba(36, 106, 222, 0.12);
                color: #246ade;
                border: 1px solid rgba(36, 106, 222, 0.2);
                border-radius: 16px;
                font-size: 12px;
                font-weight: 700;
                padding: 0 14px;
            }
            QLabel#SectionTitle {
                color: #17324d;
                font-size: 17px;
                font-weight: 700;
            }
            QLabel#InfoTitle {
                color: #17324d;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#InfoMeta {
                color: #5a7188;
                font-size: 12px;
            }
            QLabel#StatusLabel {
                color: #17324d;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#HintText {
                color: #708396;
                font-size: 12px;
            }
            QLineEdit, QPlainTextEdit, QTableWidget {
                background: #fbfdff;
                border: 1px solid #d7e2ef;
                border-radius: 12px;
                color: #17324d;
                selection-background-color: #d9e8ff;
                selection-color: #17324d;
            }
            QLineEdit {
                min-height: 44px;
                padding: 0 14px;
                font-size: 14px;
            }
            QPlainTextEdit {
                padding: 10px;
                font-family: Consolas, "Courier New", monospace;
                font-size: 11px;
            }
            QTableWidget {
                padding: 4px;
                gridline-color: transparent;
                alternate-background-color: #f6faff;
            }
            QHeaderView::section {
                background: transparent;
                color: #55708a;
                border: none;
                padding: 8px 10px;
                font-size: 11px;
                font-weight: 700;
            }
            QTableWidget::item {
                padding: 6px 8px;
                border-bottom: 1px solid #edf3fa;
            }
            QTableWidget::item:selected {
                background: #dce9ff;
                color: #17324d;
            }
            QPushButton {
                background: #f5f9ff;
                border: 1px solid #d7e2ef;
                border-radius: 12px;
                color: #17324d;
                min-height: 42px;
                padding: 0 16px;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #ecf4ff;
                border-color: #bfd3eb;
            }
            QPushButton:pressed {
                background: #dfeeff;
            }
            QPushButton#PrimaryAction {
                background: #246ade;
                border: 1px solid #246ade;
                color: white;
            }
            QPushButton#PrimaryAction:hover {
                background: #1d58b9;
                border-color: #1d58b9;
            }
            QPushButton#LogToggleButton {
                min-height: 38px;
                padding: 0 14px;
            }
            QPushButton:disabled {
                background: #f3f5f8;
                color: #98a7b7;
                border-color: #e2e8f0;
            }
            QProgressBar {
                background: #e7edf5;
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
            QScrollBar:vertical {
                background: transparent;
                width: 12px;
                margin: 4px;
            }
            QScrollBar::handle:vertical {
                background: #c8d5e5;
                min-height: 26px;
                border-radius: 6px;
            }
            """
        )

    def _restore_settings(self) -> None:
        download_path = self.settings.value("download_path", type=str)
        if not download_path:
            download_path = QtCore.QStandardPaths.writableLocation(
                QtCore.QStandardPaths.StandardLocation.DownloadLocation
            )
        self.save_path_input.setText(download_path)
        self._set_log_visibility(self.settings.value("show_logs", False, type=bool))

        size_value = self.settings.value("window_size")
        if isinstance(size_value, QtCore.QSize):
            self.resize(size_value)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # pragma: no cover - UI path
        self.settings.setValue("download_path", self.save_path_input.text().strip())
        self.settings.setValue("show_logs", self.log_card.isVisible())
        self.settings.setValue("window_size", self.size())
        super().closeEvent(event)

    def _validate_binaries(self) -> None:
        missing = self.binaries.missing()
        self.dependencies_ok = not missing
        if self.dependencies_ok:
            self.append_log(f"已找到依赖目录: {self.binaries.ffmpeg_dir}")
            self.progress_label.setText("等待下载任务")
            return

        for path in missing:
            self.append_log(f"缺少依赖文件: {path}")

        self._set_log_visibility(True)
        self.query_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.progress_label.setText("缺少依赖，请确认 exe 同级 bin 目录中包含 yt-dlp、ffmpeg、ffprobe")
        self.detail_label.setText(str(self.binaries.ffmpeg_dir))

    def append_log(self, message: str) -> None:
        timestamp = QtCore.QDateTime.currentDateTime().toString("HH:mm:ss")
        self.log_output.appendPlainText(f"[{timestamp}] {message}")

    def pick_save_directory(self) -> None:
        current = self.save_path_input.text().strip() or str(app_root())
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "选择保存目录", current)
        if directory:
            self.save_path_input.setText(directory)
            self.settings.setValue("download_path", directory)

    def open_save_directory(self) -> None:
        path = Path(self.save_path_input.text().strip() or ".").expanduser()
        path.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path.resolve())))

    def start_query(self) -> None:
        if self.query_thread is not None or not self.dependencies_ok:
            return

        raw_url = self.url_input.text().strip()
        if not raw_url:
            self.progress_label.setText("请先输入 Bilibili 链接")
            self.url_input.setFocus()
            return

        url = normalize_video_url(raw_url)
        if url != raw_url:
            self.url_input.setText(url)
            self.append_log(f"已标准化链接: {url}")

        self.metadata = None
        self.formats = []
        self.format_table.setRowCount(0)
        self.download_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText("正在读取视频信息...")
        self.detail_label.setText("请稍候，正在通过 yt-dlp 获取原始信息")
        self.video_title_label.setText("正在解析视频信息...")
        self.video_meta_label.setText("读取标题、UP 主、时长和清晰度中")
        self.selection_hint_label.setText("解析完成后会自动选中最高分辨率的 H.264 版本。")
        self.query_button.setEnabled(False)
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
        self.query_button.setEnabled(self.dependencies_ok)

    def _on_query_finished(
        self,
        metadata: dict[str, Any],
        options: list[FormatOption],
        default_index: int,
    ) -> None:
        self.metadata = metadata
        self.formats = options
        self._populate_video_summary(metadata)
        self._populate_format_table(options, default_index)
        self.progress_label.setText("视频信息已就绪，可以开始下载")
        self.detail_label.setText("默认已选中最高分辨率的 H.264 版本")
        self.download_button.setEnabled(True)
        self.append_log(f"解析完成，共找到 {len(options)} 个可下载视频格式")

    def _on_query_error(self, message: str) -> None:
        self._set_log_visibility(True)
        self.progress_label.setText("解析失败")
        self.detail_label.setText(message)
        self.video_title_label.setText("解析视频失败")
        self.video_meta_label.setText(message)
        self.append_log(f"解析失败: {message}")

    def _populate_video_summary(self, metadata: dict[str, Any]) -> None:
        title = str(metadata.get("title") or "未命名视频")
        uploader = str(metadata.get("uploader") or metadata.get("channel") or "未知 UP 主")
        duration = format_duration(metadata.get("duration"))
        video_id = str(metadata.get("id") or "-")
        self.video_title_label.setText(title)
        self.video_meta_label.setText(f"{uploader}    |    时长 {duration}    |    视频 ID {video_id}")

    def _populate_format_table(self, options: list[FormatOption], default_index: int) -> None:
        self.format_table.setRowCount(len(options))
        for row, option in enumerate(options):
            row_values = [
                "默认" if row == default_index else "",
                option.resolution_label,
                option.ext.upper(),
                option.codec_label,
                option.audio_label,
                option.size_label,
                f"{option.fps:.0f}" if option.fps else "-",
                option.note or "-",
            ]
            for column, value in enumerate(row_values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setTextAlignment(
                    QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
                )
                self.format_table.setItem(row, column, item)

        if default_index >= 0:
            self.format_table.selectRow(default_index)
        self._on_format_selection_changed()

    def _on_format_selection_changed(self) -> None:
        current_row = self.current_format_index()
        if current_row < 0 or current_row >= len(self.formats):
            return

        option = self.formats[current_row]
        recommendation = "H.264 默认推荐" if option.is_h264 else "当前项不是 H.264"
        self.selection_hint_label.setText(
            f"当前选择: {option.resolution_label} · {option.codec_label} · {option.size_label} · {recommendation}"
        )
        if self.dependencies_ok and self.download_thread is None:
            self.download_button.setEnabled(True)

    def current_format_index(self) -> int:
        model = self.format_table.selectionModel()
        if model is None:
            return -1
        indexes = model.selectedRows()
        if not indexes:
            return -1
        return indexes[0].row()

    def start_download(self) -> None:
        if self.download_thread is not None or not self.dependencies_ok:
            return
        if self.metadata is None:
            self.progress_label.setText("请先解析视频，再开始下载")
            return

        selected_index = self.current_format_index()
        if selected_index < 0 or selected_index >= len(self.formats):
            self.progress_label.setText("请先选择一个视频格式")
            return

        save_dir_text = self.save_path_input.text().strip()
        if not save_dir_text:
            self.progress_label.setText("请先选择保存目录")
            return

        save_dir = Path(save_dir_text).expanduser()
        save_dir.mkdir(parents=True, exist_ok=True)
        self.settings.setValue("download_path", str(save_dir))

        url = normalize_video_url(self.url_input.text().strip())
        self.url_input.setText(url)
        option = self.formats[selected_index]
        video_id = str(self.metadata.get("id") or "")

        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("正在下载...")
        self.detail_label.setText(f"目标格式: {option.resolution_label} · {option.codec_label}")
        self.append_log(
            f"准备下载，格式 {option.format_id}，分辨率 {option.resolution_label}，保存目录 {save_dir}"
        )

        self.download_thread = QtCore.QThread(self)
        self.download_worker = DownloadWorker(
            url, save_dir, option, self.binaries, video_id=video_id
        )
        self.download_worker.moveToThread(self.download_thread)

        self.download_thread.started.connect(self.download_worker.run)
        self.download_worker.progress.connect(self._on_download_progress)
        self.download_worker.log.connect(self.append_log)
        self.download_worker.completed.connect(self._on_download_completed)
        self.download_worker.cancelled.connect(self._on_download_cancelled)
        self.download_worker.error.connect(self._on_download_error)
        self.download_worker.finished.connect(self.download_thread.quit)
        self.download_thread.finished.connect(self._cleanup_download_worker)
        self.download_thread.start()

    def cancel_download(self) -> None:
        if self.download_worker is None:
            return
        self.append_log("收到取消请求，正在停止 yt-dlp ...")
        self.progress_label.setText("正在取消下载...")
        self.detail_label.setText("请稍候，正在终止下载进程")
        self.cancel_button.setEnabled(False)
        self.download_worker.cancel()

    def _cleanup_download_worker(self) -> None:
        if self.download_worker is not None:
            self.download_worker.deleteLater()
        if self.download_thread is not None:
            self.download_thread.deleteLater()
        self.download_worker = None
        self.download_thread = None
        self.cancel_button.setEnabled(False)
        self.download_button.setEnabled(bool(self.formats) and self.dependencies_ok)

    def _on_download_progress(self, value: int, percent_text: str, speed_text: str, eta_text: str) -> None:
        self.progress_bar.setValue(max(0, min(value, 100)))
        self.progress_label.setText(f"下载进度 {percent_text or f'{value}%'}")
        self.detail_label.setText(f"速度 {speed_text or '-'}    |    剩余 {eta_text or '-'}")

    def _on_download_completed(self, final_target: str) -> None:
        self.progress_bar.setValue(100)
        self.progress_label.setText("下载完成")
        self.detail_label.setText(final_target)
        self.append_log(f"下载完成: {final_target}")

    def _on_download_cancelled(self) -> None:
        self.progress_label.setText("下载已取消")
        self.detail_label.setText("你可以重新选择格式后再次下载")
        self.append_log("下载已取消")

    def _on_download_error(self, message: str) -> None:
        self._set_log_visibility(True)
        self.progress_label.setText("下载失败")
        self.detail_label.setText(message)
        self.append_log(f"下载失败: {message}")
