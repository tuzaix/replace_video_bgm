"""
Common table helper functions for GUI pages.

These utilities centralize repetitive QTableWidget operations, improving
consistency across tabs and making code easier to maintain.

Functions defined here are intentionally small and safe to call from GUI
threads. For long-running or I/O-heavy tasks, keep them in worker threads.
"""

from __future__ import annotations

from typing import Optional
from PySide6 import QtWidgets, QtGui


def resolve_display_name(file_path: str) -> str:
    """
    Return a compact display name for file paths used in table views.

    Strategy:
    - If the path contains separators, return the basename.
    - Otherwise, return the original string.

    Args:
        file_path: Full or partial path to a file.

    Returns:
        A short display name (usually the basename).
    """
    try:
        import os
        return os.path.basename(file_path) or file_path
    except Exception:
        return file_path


def set_table_row_colors(
    table: QtWidgets.QTableWidget,
    row: int,
    ok: bool,
    fg_ok: QtGui.QColor | None = None,
    fg_fail: QtGui.QColor | None = None,
) -> None:
    """
    Set the foreground color for all cells in a row based on success state.

    Args:
        table: Target QTableWidget.
        row: Row index to colorize.
        ok: True for success (green-ish), False for failure (red-ish).
        fg_ok: Optional success color override.
        fg_fail: Optional failure color override.

    Notes:
        - Silently ignores out-of-range rows.
        - Does not change background color to avoid theme conflicts.
    """
    try:
        if row < 0 or row >= table.rowCount():
            return
        if fg_ok is None:
            fg_ok = QtGui.QColor(0, 128, 0)
        if fg_fail is None:
            fg_fail = QtGui.QColor(200, 40, 40)
        color = fg_ok if ok else fg_fail
        for col in range(table.columnCount()):
            item = table.item(row, col)
            if item is not None:
                item.setForeground(color)
    except Exception:
        pass


def ensure_table_headers(
    table: QtWidgets.QTableWidget,
    headers: list[str],
) -> None:
    """
    Ensure a QTableWidget has the given headers and correct column count.

    Args:
        table: Target QTableWidget.
        headers: A list of header texts.

    Behavior:
        - Resizes the table to match len(headers).
        - Applies header labels.
    """
    try:
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
    except Exception:
        pass