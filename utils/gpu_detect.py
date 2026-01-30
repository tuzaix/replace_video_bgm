"""
GPU and Encoder Detection Utilities

This module provides utilities to:
- Detect NVENC availability via ffmpeg encoders output
- Infer GPU vendor based on available hardware encoders reported by ffmpeg

Design goals:
- Reusable in CLI and GUI contexts without Qt dependencies
- Keep subprocesses silent on Windows to avoid console popups
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

from utils.common_utils import get_subprocess_silent_kwargs


def _read_text(b: bytes) -> str:
    """Decode bytes to text using utf-8 with fallback on Windows codepage."""
    try:
        return (b or b"").decode("utf-8", errors="ignore")
    except Exception:
        try:
            return (b or b"").decode("mbcs", errors="ignore")
        except Exception:
            return ""


def ffmpeg_output(args: list[str], timeout: int = 8) -> str:
    """Run ffmpeg with given args and return stdout text, or empty string on error."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return ""
    try:
        res = subprocess.run([ffmpeg_bin, *args], capture_output=True, timeout=timeout, **get_subprocess_silent_kwargs())
        if res.returncode != 0:
            return _read_text(res.stdout) + "\n" + _read_text(res.stderr)
        return _read_text(res.stdout)
    except Exception:
        return ""


def is_nvenc_available(timeout: int = 8) -> bool:
    """Return True if ffmpeg reports NVENC encoders (h264_nvenc or hevc_nvenc).

    Parameters
    ----------
    timeout : int
        Subprocess timeout in seconds.
    """
    enc = ffmpeg_output(["-hide_banner", "-encoders"], timeout)
    # return False
    return ("h264_nvenc" in enc) or ("hevc_nvenc" in enc)


def detect_gpu_vendor(timeout: int = 8) -> str:
    """Infer GPU vendor from ffmpeg hardware encoders.

    Returns one of: 'nvidia', 'intel', 'amd', 'none', 'unknown'.
    """
    enc = ffmpeg_output(["-hide_banner", "-encoders"], timeout)
    if not enc:
        return "unknown"
    enc_l = enc.lower()
    try:
        if ("h264_nvenc" in enc_l) or ("hevc_nvenc" in enc_l):
            return "nvidia"
        if ("h264_qsv" in enc_l) or ("hevc_qsv" in enc_l):
            return "intel"
        if ("h264_amf" in enc_l) or ("hevc_amf" in enc_l):
            return "amd"
        # No known hardware encoder found
        return "none"
    except Exception:
        return "unknown"


__all__ = [
    "is_nvenc_available",
    "detect_gpu_vendor",
]
