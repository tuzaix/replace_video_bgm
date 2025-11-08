"""Helper package for video concatenation tools.

This package contains modules used by the GUI application.
Adding this __init__ ensures PyInstaller and Python treat
the directory as a regular package, avoiding import errors
like `ModuleNotFoundError: No module named 'concat_tool'`.

Exports
-------
- video_concat: main worker utilities for FFmpeg concat operations.
"""

# Re-export commonly used module for convenience
from . import video_concat  # noqa: F401

__all__ = ["video_concat"]