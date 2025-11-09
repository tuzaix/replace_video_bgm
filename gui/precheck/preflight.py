"""Preflight checks module for GUI startup.

This module abstracts the startup runtime checks and helper functions
previously defined in main_gui.py. It provides:

- detect_nvidia_gpu: Detect presence of NVIDIA GPU
- runtime_base_dir/resource_path: Runtime-aware resource location helpers
- default_license_path/license_is_ok: License file path inference and check
- show_no_nvidia_dialog/show_license_failure_dialog: User prompts
- run_preflight_checks: Orchestrate the above checks

All functions include docstrings and are designed to work in both
development and PyInstaller-frozen runtime.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtWidgets

# Ensure imports work both in development and PyInstaller-frozen runtime.
# In frozen mode, bundled packages are available without modifying sys.path.
# In development mode, add project root so `crypto_tool` can be imported.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if not getattr(sys, "frozen", False):
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

from .gpu_detect import detect_nvidia_gpu, show_no_nvidia_dialog
from .license_check import (
    default_license_path,
    license_is_ok,
    show_license_failure_dialog,
)
from .runtime_paths import runtime_base_dir, resource_path


# GPU 检测逻辑已抽出至 gpu_detect 模块；
# 运行时路径辅助函数抽出至 runtime_paths 模块。





def run_preflight_checks(app: QtWidgets.QApplication) -> bool:
    """Run startup preflight checks: GPU requirement and license check.

    This function orchestrates two independent checks and their UI prompts:
    1) NVIDIA GPU presence. If missing, show a blocking dialog and quit.
    2) License/authorization check. If it fails, show a dialog with a
       "copy machine fingerprint" helper and quit.

    Parameters
    ----------
    app : QtWidgets.QApplication
        The Qt application instance.

    Returns
    -------
    bool
        True to continue launching; False to terminate the app.
    """
    # 1) NVIDIA GPU check
    try:
        has_nv = detect_nvidia_gpu()
    except Exception:
        has_nv = False
    if not has_nv:
        show_no_nvidia_dialog(app)
        return False

    # 2) License/authorization check
    try:
        lic_ok = license_is_ok()
    except Exception:
        lic_ok = False
    if not lic_ok:
        show_license_failure_dialog(app)
        return False

    return True


__all__ = [
    "detect_nvidia_gpu",
    "runtime_base_dir",
    "resource_path",
    "default_license_path",
    "license_is_ok",
    "show_no_nvidia_dialog",
    "show_license_failure_dialog",
    "run_preflight_checks",
]