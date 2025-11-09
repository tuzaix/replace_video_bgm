"""Runtime path helpers for resource lookup.

This module centralizes functions that determine the base directory and
compose resource paths that work in both development and PyInstaller-frozen
runtime environments. Import these helpers from other modules to avoid
duplicated implementations and circular dependencies.
"""

from __future__ import annotations

import sys
from pathlib import Path


# Project root directory (repository root), used when not frozen.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def runtime_base_dir() -> Path:
    """Return base directory for resource lookup depending on runtime.

    - Frozen (PyInstaller onefile/onedir): use sys._MEIPASS as base.
    - Development (non-frozen): use project root (PROJECT_ROOT).

    Returns
    -------
    Path
        The directory from which bundled resources should be read.
    """
    try:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return Path(getattr(sys, "_MEIPASS")).resolve()
    except Exception:
        pass
    return PROJECT_ROOT


def resource_path(*parts: str) -> Path:
    """Compose a resource path that works in both dev and frozen runtime.

    Parameters
    ----------
    parts : str
        Path components under the base directory. For example:
        resource_path("gui", "wechat", "admin1.png").

    Returns
    -------
    Path
        The resolved path to the resource.
    """
    return runtime_base_dir().joinpath(*parts)


__all__ = ["runtime_base_dir", "resource_path", "PROJECT_ROOT"]