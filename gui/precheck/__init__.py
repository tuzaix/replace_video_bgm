"""Startup precheck utilities for the GUI.

This package contains runtime checks used by the GUI at startup, such as:
- NVIDIA GPU detection
- License verification and user prompts
- Runtime-aware resource path helpers

By moving these checks out of main_gui.py, we keep the GUI file focused on
UI logic and make the preflight checks easier to test and maintain.
"""

__all__ = [
    "preflight",
]