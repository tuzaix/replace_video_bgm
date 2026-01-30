from __future__ import annotations

import os
import subprocess
from typing import Dict, Any


def get_subprocess_silent_kwargs() -> Dict[str, Any]:
    """获取隐藏控制台窗口的参数（仅限 Windows）。
    
    用于在打包后的 GUI 程序中调用 subprocess 时，避免弹出黑色的 cmd 窗口。
    """
    kwargs = {}
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = si
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def is_audio_file(name: str) -> bool:
    """判断是否为常见音频文件。"""
    ext = os.path.splitext(name)[1].lower()
    return ext in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def is_video_file(name: str) -> bool:
    """判断是否为常见视频文件。"""
    ext = os.path.splitext(name)[1].lower()
    return ext in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v"}


def is_image_file(name: str) -> bool:
    """判断是否为常见图片文件。"""
    ext = os.path.splitext(name)[1].lower()
    return ext in {".jpg", ".jpeg", ".png", ".bmp"}


def format_srt_timestamp(seconds: float) -> str:
    """将秒值格式化为 SRT 时间戳。"""
    milliseconds = int(round(seconds * 1000.0))
    hours = milliseconds // 3_600_000
    milliseconds -= hours * 3_600_000
    minutes = milliseconds // 60_000
    milliseconds -= minutes * 60_000
    secs = milliseconds // 1000
    milliseconds -= secs * 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"