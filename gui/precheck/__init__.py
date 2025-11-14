"""Startup precheck utilities for the GUI.

This package contains runtime checks used by the GUI at startup, such as:
- NVIDIA GPU detection
- License verification and user prompts
- Runtime-aware resource path helpers

By moving these checks out of main_gui.py, we keep the GUI file focused on
UI logic and make the preflight checks easier to test and maintain.

Convenience imports are provided so callers can use::

    from gui.precheck import (
        run_preflight_checks,
        detect_nvidia_gpu,
        show_no_nvidia_dialog,
        license_is_ok,
        show_license_failure_dialog,
        default_license_path,
        resource_path,
        runtime_base_dir,
        PROJECT_ROOT,
    )
"""

from .preflight import run_preflight_checks
from .gpu_detect import detect_nvidia_gpu, show_no_nvidia_dialog, list_nvidia_gpus
from .license_check import (
    default_license_path,
    license_is_ok,
    show_license_failure_dialog,
)
from .runtime_paths import resource_path, runtime_base_dir, PROJECT_ROOT

__all__ = [
    "run_preflight_checks",
    "detect_nvidia_gpu",
    "show_no_nvidia_dialog",
    "list_nvidia_gpus",
    "default_license_path",
    "license_is_ok",
    "show_license_failure_dialog",
    "resource_path",
    "runtime_base_dir",
    "PROJECT_ROOT",
]