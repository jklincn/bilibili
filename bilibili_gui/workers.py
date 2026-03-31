from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from PySide6 import QtCore

from .core import (
    CREATE_NO_WINDOW,
    BinaryPaths,
    FormatOption,
    build_subprocess_env,
    decode_subprocess_output,
    build_download_command,
    build_metadata_command,
    collect_video_formats,
    parse_metadata_output,
    parse_progress_line,
    pick_default_format_index,
)


class MetadataWorker(QtCore.QObject):
    finished = QtCore.Signal(object, object, int)
    error = QtCore.Signal(str)

    def __init__(self, url: str, binaries: BinaryPaths) -> None:
        super().__init__()
        self.url = url
        self.binaries = binaries

    @QtCore.Slot()
    def run(self) -> None:
        try:
            completed = subprocess.run(
                build_metadata_command(self.url, self.binaries),
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
                env=build_subprocess_env(),
                check=False,
            )
            stdout = decode_subprocess_output(completed.stdout)
            stderr = decode_subprocess_output(completed.stderr)
            if completed.returncode != 0:
                error_text = (stderr or stdout).strip()
                raise RuntimeError(error_text or "yt-dlp 返回了非 0 状态码")

            payload = parse_metadata_output(stdout)
            options = collect_video_formats(payload)
            if not options:
                raise RuntimeError("没有解析到可下载的视频格式")

            default_index = pick_default_format_index(options)
            self.finished.emit(payload, options, default_index)
        except Exception as exc:  # pragma: no cover - UI path
            self.error.emit(str(exc))


class DownloadWorker(QtCore.QObject):
    progress = QtCore.Signal(int, str, str, str)
    log = QtCore.Signal(str)
    completed = QtCore.Signal(str)
    cancelled = QtCore.Signal()
    error = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(
        self,
        url: str,
        save_dir: Path,
        option: FormatOption,
        binaries: BinaryPaths,
        video_id: str = "",
    ) -> None:
        super().__init__()
        self.url = url
        self.save_dir = save_dir
        self.option = option
        self.binaries = binaries
        self.video_id = video_id
        self._cancel_event = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._final_path = ""
        self._started_at = 0.0
        self._initial_files: set[Path] = set()
        self._last_progress_value = 0
        self._last_speed_text = ""
        self._last_eta_text = ""

    def cancel(self) -> None:
        self._cancel_event.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            command = build_download_command(self.url, self.save_dir, self.option, self.binaries)
            self.log.emit(
                f"\u5f00\u59cb\u4e0b\u8f7d\uff0c\u683c\u5f0f {self.option.format_id}\uff0c\u4fdd\u5b58\u5230 {self.save_dir}"
            )
            self._started_at = time.time()
            self._initial_files = {
                path.resolve()
                for path in self.save_dir.iterdir()
                if path.is_file()
            } if self.save_dir.exists() else set()

            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
                env=build_subprocess_env(),
            )
            threading.Thread(target=self._monitor_download_size, daemon=True).start()

            last_lines: list[str] = []
            assert self._process.stdout is not None

            for raw_line in self._process.stdout:
                line = decode_subprocess_output(raw_line).strip()
                if not line:
                    continue

                progress_line = line
                if progress_line.startswith("download:PROGRESS|"):
                    progress_line = progress_line.split("download:", 1)[1]
                if progress_line.startswith("PROGRESS|"):
                    value, percent_text, speed_text, eta_text = parse_progress_line(progress_line)
                    self._last_progress_value = max(self._last_progress_value, value)
                    if speed_text:
                        self._last_speed_text = speed_text
                    if eta_text:
                        self._last_eta_text = eta_text
                    self.progress.emit(
                        self._last_progress_value,
                        percent_text,
                        self._last_speed_text,
                        self._last_eta_text,
                    )
                    continue

                file_line = line
                if file_line.startswith("after_move:FILE|"):
                    file_line = file_line.split("after_move:", 1)[1]
                if file_line.startswith("FILE|"):
                    self._final_path = file_line.split("|", 1)[1].strip()
                    continue

                last_lines.append(line)
                last_lines = last_lines[-10:]
                self.log.emit(line)

                if self._cancel_event.is_set():
                    break

            return_code = self._process.wait()
            if self._cancel_event.is_set():
                self.cancelled.emit()
                return

            if return_code != 0:
                error_text = "\n".join(last_lines) or "yt-dlp \u4e0b\u8f7d\u5931\u8d25"
                raise RuntimeError(error_text)

            self.progress.emit(100, "100%", "\u5b8c\u6210", "0s")
            self.completed.emit(self._resolve_final_path())
        except Exception as exc:  # pragma: no cover - UI path
            self.error.emit(str(exc))
        finally:
            self.finished.emit()

    def _resolve_final_path(self) -> str:
        if self._final_path and "\ufffd" not in self._final_path:
            try:
                if Path(self._final_path).exists():
                    return self._final_path
            except OSError:
                pass

        candidates: list[Path] = []
        if self.video_id:
            candidates.extend(
                sorted(
                    self.save_dir.glob(f"* [{self.video_id}].*"),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
            )

        if not candidates:
            recent_threshold = self._started_at - 2
            candidates.extend(
                sorted(
                    (
                        path
                        for path in self.save_dir.iterdir()
                        if path.is_file() and path.stat().st_mtime >= recent_threshold
                    ),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
            )

        if candidates:
            return str(candidates[0])
        return self._final_path or str(self.save_dir)


    def _monitor_download_size(self) -> None:
        total_bytes = self.option.estimated_filesize or self.option.filesize
        if not total_bytes:
            return

        while not self._cancel_event.is_set():
            process = self._process
            if process is None:
                time.sleep(0.2)
                continue
            if process.poll() is not None:
                break

            downloaded_bytes = self._estimate_downloaded_bytes()
            if downloaded_bytes > 0:
                percent = min(downloaded_bytes / total_bytes * 100, 99.0)
                value = max(self._last_progress_value, int(percent))
                if value > self._last_progress_value:
                    self._last_progress_value = value
                    self.progress.emit(
                        value,
                        f"{percent:.1f}%",
                        self._last_speed_text,
                        self._last_eta_text,
                    )

            time.sleep(0.35)

    def _estimate_downloaded_bytes(self) -> int:
        if not self.save_dir.exists():
            return 0

        partial_total = 0
        output_total = 0
        recent_threshold = self._started_at - 1

        for path in self.save_dir.iterdir():
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
                resolved = path.resolve()
            except OSError:
                continue

            is_recent = resolved not in self._initial_files or stat.st_mtime >= recent_threshold
            if not is_recent:
                continue

            lowered = path.name.lower()
            if lowered.endswith('.part') or '.part-frag' in lowered or lowered.endswith('.ytdl'):
                partial_total += stat.st_size
            else:
                output_total += stat.st_size

        return max(partial_total, output_total)
