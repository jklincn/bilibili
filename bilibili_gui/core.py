from __future__ import annotations

import json
import locale
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
BVID_RE = re.compile(r"\b(BV[0-9A-Za-z]{10})\b", re.IGNORECASE)
AVID_RE = re.compile(r"\bav(\d+)\b", re.IGNORECASE)
SUBPROCESS_ENCODINGS = tuple(
    dict.fromkeys(
        encoding
        for encoding in (
            "utf-8",
            locale.getpreferredencoding(False),
            "gb18030",
            "gbk",
            "cp936",
        )
        if encoding
    )
)


@dataclass(slots=True)
class BinaryPaths:
    yt_dlp: Path
    ffmpeg: Path

    @property
    def ffmpeg_dir(self) -> Path:
        return self.ffmpeg.parent

    def missing(self) -> list[Path]:
        return [path for path in (self.yt_dlp, self.ffmpeg) if not path.exists()]


@dataclass(slots=True)
class FormatOption:
    format_id: str
    width: int
    height: int
    fps: float
    ext: str
    vcodec: str
    acodec: str
    filesize: int | None
    estimated_filesize: int | None
    note: str
    is_h264: bool

    @property
    def has_audio(self) -> bool:
        return self.acodec not in {"", "none", "unknown"}

    @property
    def resolution_label(self) -> str:
        if self.width and self.height:
            return f"{self.width} x {self.height}"
        if self.height:
            return f"{self.height}p"
        return "未知分辨率"

    @property
    def codec_label(self) -> str:
        if self.is_h264:
            return "H.264"
        return self.vcodec or "未知编码"

    @property
    def audio_label(self) -> str:
        return "含音频" if self.has_audio else "需合并最佳音频"

    @property
    def size_label(self) -> str:
        return humanize_bytes(self.estimated_filesize or self.filesize)

    @property
    def download_selector(self) -> str:
        if self.has_audio:
            return f"{self.format_id}/best"
        return f"{self.format_id}+bestaudio/best"


def app_root() -> Path:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)

    root = app_root()
    internal_root = root / "_internal"
    if internal_root.exists():
        return internal_root
    return root


def static_asset(*parts: str) -> Path:
    return resource_root() / "static" / Path(*parts)


def runtime_temp_dir() -> Path:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        local_appdata = Path(os.environ.get("LOCALAPPDATA", app_root()))
        temp_dir = local_appdata / "BilibiliDownloader" / "temp"
    else:
        temp_dir = app_root() / ".tmp" / "runtime"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def build_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    temp_dir = str(runtime_temp_dir())
    env["TEMP"] = temp_dir
    env["TMP"] = temp_dir
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def discover_binaries() -> BinaryPaths:
    root = app_root()
    candidate_dirs = [
        root / "bin",
        root / "_internal" / "bin",
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate_dirs.append(Path(meipass) / "bin")

    existing_dir = next((path for path in candidate_dirs if path.exists()), candidate_dirs[0])
    bin_dir = existing_dir
    return BinaryPaths(
        yt_dlp=bin_dir / "yt-dlp.exe",
        ffmpeg=bin_dir / "ffmpeg.exe",
    )


def humanize_bytes(size: int | float | None) -> str:
    if not size:
        return "大小未知"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_duration(seconds: int | float | None) -> str:
    if not seconds:
        return "时长未知"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def is_h264_codec(codec: str | None) -> bool:
    if not codec:
        return False
    lowered = codec.lower()
    return "avc" in lowered or "h264" in lowered


def pick_best_audio_size(metadata: dict[str, Any]) -> int | None:
    best_audio_size: int | None = None
    for fmt in metadata.get("formats") or []:
        vcodec = str(fmt.get("vcodec") or "")
        acodec = str(fmt.get("acodec") or "")
        if vcodec not in {"", "none"}:
            continue
        if not acodec or acodec in {"none", "unknown"}:
            continue
        filesize = fmt.get("filesize") or fmt.get("filesize_approx")
        if not filesize:
            continue
        size_value = int(filesize)
        if best_audio_size is None or size_value > best_audio_size:
            best_audio_size = size_value
    return best_audio_size


def collect_video_formats(metadata: dict[str, Any]) -> list[FormatOption]:
    options: list[FormatOption] = []
    best_audio_size = pick_best_audio_size(metadata)
    for fmt in metadata.get("formats") or []:
        vcodec = str(fmt.get("vcodec") or "")
        if not vcodec or vcodec == "none":
            continue

        format_id = str(fmt.get("format_id") or "").strip()
        if not format_id:
            continue

        filesize = fmt.get("filesize") or fmt.get("filesize_approx")
        video_size = int(filesize) if filesize else None
        estimated_size = video_size
        has_audio = str(fmt.get("acodec") or "none") not in {"", "none", "unknown"}
        if estimated_size is not None and not has_audio and best_audio_size:
            estimated_size += best_audio_size
        options.append(
            FormatOption(
                format_id=format_id,
                width=int(fmt.get("width") or 0),
                height=int(fmt.get("height") or 0),
                fps=float(fmt.get("fps") or 0),
                ext=str(fmt.get("ext") or "-"),
                vcodec=vcodec,
                acodec=str(fmt.get("acodec") or "none"),
                filesize=video_size,
                estimated_filesize=estimated_size,
                note=str(fmt.get("format_note") or fmt.get("format") or ""),
                is_h264=is_h264_codec(vcodec),
            )
        )

    options.sort(
        key=lambda item: (
            item.height,
            item.width,
            item.is_h264,
            item.has_audio,
            item.fps,
            item.estimated_filesize or item.filesize or 0,
        ),
        reverse=True,
    )
    return options


def pick_default_format_index(options: list[FormatOption]) -> int:
    if not options:
        return -1

    selected = -1
    selected_key: tuple[int, int, bool, float, int] | None = None
    for index, option in enumerate(options):
        if not option.is_h264:
            continue
        current_key = (
            option.height,
            option.width,
            option.has_audio,
            option.fps,
            option.estimated_filesize or option.filesize or 0,
        )
        if selected_key is None or current_key > selected_key:
            selected = index
            selected_key = current_key
    if selected >= 0:
        return selected
    return 0


def build_metadata_command(url: str, binaries: BinaryPaths) -> list[str]:
    return [
        str(binaries.yt_dlp),
        "--ignore-config",
        "--no-warnings",
        "--no-playlist",
        "--dump-single-json",
        "--ffmpeg-location",
        str(binaries.ffmpeg_dir),
        url,
    ]


def build_download_command(url: str, save_dir: Path, option: FormatOption, binaries: BinaryPaths) -> list[str]:
    output_template = str(save_dir / "%(title)s.%(ext)s")
    return [
        str(binaries.yt_dlp),
        "--ignore-config",
        "--newline",
        "--no-warnings",
        "--no-playlist",
        "--windows-filenames",
        "--progress-template",
        "download:PROGRESS|%(progress.status)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress._speed_str)s|%(progress._eta_str)s",
        "--progress-delta",
        "0.25",
        "--print",
        "after_move:FILE|%(filepath)s",
        "--ffmpeg-location",
        str(binaries.ffmpeg_dir),
        "--merge-output-format",
        "mp4",
        "--output",
        output_template,
        "-f",
        option.download_selector,
        url,
    ]


def parse_metadata_output(raw_output: str) -> dict[str, Any]:
    return json.loads(raw_output)


def parse_progress_line(line: str) -> tuple[int, str, str, str]:
    parts = (line.split("|", 6) + ["", "", "", "", "", "", ""])[:7]
    _, status_text, downloaded_text, total_text, total_estimate_text, speed_text, eta_text = parts

    def _to_int(value: str) -> int | None:
        value = value.strip()
        if not value or value == "NA":
            return None
        try:
            return int(float(value))
        except ValueError:
            return None

    downloaded = _to_int(downloaded_text)
    total = _to_int(total_text) or _to_int(total_estimate_text)
    if downloaded is not None and total and total > 0:
        value = int(max(0, min(downloaded / total * 100, 100)))
        if status_text.strip().lower() not in {"finished"}:
            value = min(value, 99)
        percent_text = f"{downloaded / total * 100:.1f}%"
        return value, percent_text, speed_text.strip(), eta_text.strip()

    match = PERCENT_RE.search(downloaded_text)
    value = int(float(match.group(1))) if match else 0
    return value, downloaded_text.strip(), speed_text.strip(), eta_text.strip()


def decode_subprocess_output(payload: bytes | None) -> str:
    if not payload:
        return ""

    for encoding in SUBPROCESS_ENCODINGS:
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue

    return payload.decode("utf-8", errors="replace")


def normalize_bvid(value: str) -> str:
    return f"BV{value[2:]}"


def normalize_video_url(raw_url: str) -> str:
    url = raw_url.strip()
    if not url:
        return ""

    bvid_match = BVID_RE.search(url)
    if bvid_match:
        return f"https://www.bilibili.com/video/{normalize_bvid(bvid_match.group(1))}/"

    avid_match = AVID_RE.search(url)
    if avid_match:
        return f"https://www.bilibili.com/video/av{avid_match.group(1)}/"

    if "://" not in url and any(domain in url.lower() for domain in ("bilibili.com", "b23.tv", "b23.wtf")):
        url = f"https://{url.lstrip('/')}"

    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url

    host = parts.netloc.lower()
    host = host[4:] if host.startswith("www.") else host

    if host.endswith("bilibili.com"):
        bvid_match = BVID_RE.search(parts.path)
        if bvid_match:
            return f"https://www.bilibili.com/video/{normalize_bvid(bvid_match.group(1))}/"

        avid_match = AVID_RE.search(parts.path)
        if avid_match:
            return f"https://www.bilibili.com/video/av{avid_match.group(1)}/"

    cleaned_path = parts.path or "/"
    return urlunsplit((parts.scheme, parts.netloc, cleaned_path, "", ""))
