"""GPU detection utilities.

This module provides functions to detect presence of NVIDIA GPU across
platforms with multiple fallbacks. It is designed to be UI-agnostic so
it can be reused in different contexts.
"""

from __future__ import annotations

import platform
import shutil
import subprocess


def detect_nvidia_gpu() -> bool:
    """Detect whether an NVIDIA GPU is present on the system.

    Strategy (cross-platform):
    1) Prefer `nvidia-smi` when available (Windows/Linux). If it lists GPUs, return True.
    2) Windows fallback: query Win32_VideoController via PowerShell and check adapter names.
    3) macOS: run `system_profiler SPDisplaysDataType` to find entries containing "NVIDIA".
    4) Linux fallback: use `lspci` output to grep for "NVIDIA".

    Returns
    -------
    bool
        True if an NVIDIA GPU appears to be present; False otherwise.
    """
    try:
        # 1) Prefer nvidia-smi if available
        nvsmi = shutil.which("nvidia-smi")
        if nvsmi:
            try:
                out = subprocess.check_output([nvsmi, "-L"], stderr=subprocess.STDOUT, timeout=3)
                text = out.decode(errors="ignore")
                if any("GPU" in line for line in text.splitlines()):
                    return True
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass

        system = platform.system().lower()

        # 2) Windows fallback: PowerShell query of video controllers
        if system == "windows":
            try:
                ps_cmd = [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy", "Bypass",
                    "-Command",
                    "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
                ]
                out = subprocess.check_output(ps_cmd, stderr=subprocess.STDOUT, timeout=3)
                text = out.decode(errors="ignore").lower()
                if "nvidia" in text:
                    return True
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass

        # 3) macOS: system_profiler
        if system == "darwin":
            try:
                out = subprocess.check_output(["system_profiler", "SPDisplaysDataType"], stderr=subprocess.STDOUT, timeout=4)
                text = out.decode(errors="ignore").lower()
                if "nvidia" in text:
                    return True
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass

        # 4) Linux: lspci fallback
        if system == "linux":
            try:
                lspci = shutil.which("lspci")
                if lspci:
                    out = subprocess.check_output([lspci], stderr=subprocess.STDOUT, timeout=3)
                    text = out.decode(errors="ignore").lower()
                    if "nvidia" in text:
                        return True
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass
    except Exception:
        # Any unexpected error -> consider not detected
        pass
    return False


__all__ = ["detect_nvidia_gpu"]