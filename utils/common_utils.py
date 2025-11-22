from __future__ import annotations

import os


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
