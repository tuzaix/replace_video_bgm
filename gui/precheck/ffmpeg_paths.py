"""
FFmpeg/FFprobe path resolution utilities with configurable priority and logging.

This module centralizes how the application locates ffmpeg and ffprobe
executables in both development and PyInstaller-frozen runtimes and allows
configuring the search priority (bundled vs system PATH). It also provides
helpers for obtaining version information and a simple NVENC availability
check using ffmpeg output.

Design goals:
- Keep GUI code lean by moving path detection and subprocess calls here.
- Offer configurable priority so callers can prefer bundled binaries or
  allow system fallback as needed.
- Provide optional logging hooks for better diagnosability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple
from pathlib import Path
import shutil
import os
import subprocess

from .runtime_paths import runtime_base_dir, PROJECT_ROOT


@dataclass
class FFResolution:
    """Result of resolving FFmpeg/FFprobe paths.

    Attributes
    ----------
    ffmpeg_path : Optional[str]
        Resolved path to ffmpeg executable, or None if not found.
    ffprobe_path : Optional[str]
        Resolved path to ffprobe executable, or None if not found.
    source : str
        One of: 'bundled_meipass', 'bundled_vendor', 'system', 'none'.
    directory : Optional[str]
        Directory chosen for PATH modification when using bundled.
    """

    ffmpeg_path: Optional[str]
    ffprobe_path: Optional[str]
    source: str
    directory: Optional[str]


def _log(logger: Optional[Callable[[str], None]], msg: str) -> None:
    if logger:
        try:
            logger(msg)
        except Exception:
            pass


def _bundled_bin_dir() -> Tuple[Optional[Path], Optional[str]]:
    """Return bundled ffmpeg/bin directory and a source tag.

    It first checks the PyInstaller runtime base directory (sys._MEIPASS) and
    then falls back to the repository vendor path.
    """
    base = runtime_base_dir()
    meipass_bin = base / "ffmpeg" / "bin"
    if meipass_bin.exists():
        return meipass_bin, "bundled_meipass"
    vendor_bin = PROJECT_ROOT / "vendor" / "ffmpeg" / "bin"
    if vendor_bin.exists():
        return vendor_bin, "bundled_vendor"
    return None, None


def _ensure_path_front(dir_path: Path, logger: Optional[Callable[[str], None]] = None) -> None:
    """Prepend a directory to PATH, removing existing duplicates.

    Parameters
    ----------
    dir_path : Path
        Directory to place at the front of PATH.
    logger : Optional[Callable[[str], None]]
        Optional logging callback.
    """
    cur = os.environ.get("PATH", "")
    parts = cur.split(os.pathsep) if cur else []
    d = str(dir_path)
    parts = [p for p in parts if os.path.abspath(p) != os.path.abspath(d)]
    os.environ["PATH"] = d + os.pathsep + os.pathsep.join(parts)
    _log(logger, f"PATH updated: {d} is placed at front")


def resolve_ffmpeg_paths(
    prefer_bundled: bool = True,
    allow_system_fallback: bool = False,
    modify_env: bool = True,
    logger: Optional[Callable[[str], None]] = None,
) -> FFResolution:
    """Resolve ffmpeg/ffprobe paths with configurable priority.

    Parameters
    ----------
    prefer_bundled : bool
        If True, search bundled ffmpeg first (MEIPASS/vendor). If found,
        optionally prepend its bin to PATH.
    allow_system_fallback : bool
        If True and bundled ffmpeg is not found, use system PATH (shutil.which).
        If False, return none when bundled not available.
    modify_env : bool
        If True and a bundled directory is used, prepend it to PATH.
    logger : Optional[Callable[[str], None]]
        Optional logging callback for decisions.

    Returns
    -------
    FFResolution
        Resolved paths and the source tag.
    """
    ffmpeg_path: Optional[str] = None
    ffprobe_path: Optional[str] = None
    source = "none"
    directory: Optional[str] = None

    if prefer_bundled:
        bdir, tag = _bundled_bin_dir()
        if bdir:
            _log(logger, f"Bundled ffmpeg directory found: {bdir} ({tag})")
            if modify_env:
                _ensure_path_front(bdir, logger)
            ffmpeg_path = shutil.which("ffmpeg")
            ffprobe_path = shutil.which("ffprobe")
            # Verify resolved paths come from bundled dir
            try:
                if ffmpeg_path and os.path.abspath(os.path.dirname(ffmpeg_path)) == os.path.abspath(str(bdir)):
                    source = tag or "bundled"
                    directory = str(bdir)
                    _log(logger, f"Resolved ffmpeg from bundled: {ffmpeg_path}")
                else:
                    _log(logger, "Resolved ffmpeg is not in bundled dir; ignoring")
                    ffmpeg_path = None
                    ffprobe_path = None
            except Exception:
                ffmpeg_path = None
                ffprobe_path = None
        else:
            _log(logger, "Bundled ffmpeg directory not found")

    if not ffmpeg_path and allow_system_fallback:
        ffmpeg_path = shutil.which("ffmpeg")
        ffprobe_path = shutil.which("ffprobe")
        if ffmpeg_path:
            source = "system"
            directory = os.path.dirname(ffmpeg_path)
            _log(logger, f"Falling back to system ffmpeg: {ffmpeg_path}")
        else:
            _log(logger, "System ffmpeg not found")

    return FFResolution(ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path, source=source, directory=directory)


def _run_cmd_silent(cmd: list[str], timeout: int = 8) -> str:
    """Run a command and return combined stdout/stderr output.

    On Windows, suppress console window popups.
    """
    try:
        kwargs = {}
        try:
            if os.name == "nt":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kwargs = {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
        except Exception:
            kwargs = {}
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)
        out = res.stdout.strip() or res.stderr.strip()
        return out or "<无输出>"
    except Exception as e:
        return f"<执行失败: {e}>"


def get_ffmpeg_versions(ffmpeg_path: Optional[str], ffprobe_path: Optional[str], timeout: int = 8) -> Tuple[str, str]:
    """Return version outputs for ffmpeg and ffprobe.

    Parameters
    ----------
    ffmpeg_path : Optional[str]
        Path to ffmpeg. If None, returns a not-found marker.
    ffprobe_path : Optional[str]
        Path to ffprobe. If None, returns a not-found marker.
    timeout : int
        Subprocess timeout in seconds.

    Returns
    -------
    Tuple[str, str]
        (ffmpeg_version_output, ffprobe_version_output)
    """
    ffmpeg_ver = _run_cmd_silent([ffmpeg_path, "-version"], timeout) if ffmpeg_path else "(未找到 ffmpeg)"
    ffprobe_ver = _run_cmd_silent([ffprobe_path, "-version"], timeout) if ffprobe_path else "(未找到 ffprobe)"
    return ffmpeg_ver, ffprobe_ver


def detect_nvenc(ffmpeg_path: Optional[str], timeout: int = 8) -> Tuple[bool, str, str]:
    """Detect NVENC availability using ffmpeg outputs.

    Parameters
    ----------
    ffmpeg_path : Optional[str]
        Path to ffmpeg executable. If None, returns (False, '', '').
    timeout : int
        Subprocess timeout.

    Returns
    -------
    Tuple[bool, str, str]
        (nvenc_available, encoders_output, hwaccels_output)
    """
    if not ffmpeg_path:
        return False, "", ""
    encoders = _run_cmd_silent([ffmpeg_path, "-hide_banner", "-encoders"], timeout)
    hwaccels = _run_cmd_silent([ffmpeg_path, "-hide_banner", "-hwaccels"], timeout)
    has_h264 = "h264_nvenc" in encoders
    has_hevc = "hevc_nvenc" in encoders
    return (has_h264 or has_hevc), encoders, hwaccels

def allow_system_fallback_env() -> bool:
    """Check env var FFMPEG_DEV_FALLBACK to allow system ffmpeg fallback in dev.

    Returns True when the environment variable is set to one of
    '1', 'true', 'yes', 'on' (case-insensitive), otherwise False.
    """
    val = os.getenv("FFMPEG_DEV_FALLBACK", "")
    try:
        val = val.strip().lower()
    except Exception:
        val = ""
    return val in ("1", "true", "yes", "on")


__all__ = [
    "FFResolution",
    "resolve_ffmpeg_paths",
    "get_ffmpeg_versions",
    "detect_nvenc",
    "allow_system_fallback_env",
]