"""
Theme constants for GUI styling.

This module centralizes commonly used colors and sizes to maintain
visual consistency across tabs and utility widgets.

All colors are provided as CSS-compatible hex strings or rgba strings.
Qt-specific color instances should be created at call sites to avoid
cross-module GUI dependencies.
"""

# Primary brand colors
PRIMARY_BLUE = "#2563eb"         # Tailwind blue-600
PRIMARY_BLUE_HOVER = "#1d4ed8"   # Tailwind blue-700

# Danger/accent colors
DANGER_RED = "#ef4444"           # Tailwind red-500
DANGER_RED_HOVER = "#dc2626"     # Tailwind red-600

# Neutral colors
GRAY_BG = "#e5e7eb"              # Tailwind gray-200
GRAY_TEXT = "#374151"            # Tailwind gray-700

# Overlay settings
OVERLAY_BACKDROP = "rgba(17, 24, 39, 160)"  # Tailwind gray-900, ~63% opacity
OVERLAY_LABEL_COLOR = "#ffffff"
OVERLAY_LABEL_FONT_PX = 13
OVERLAY_LABEL_WEIGHT = 600

# Button sizing
BUTTON_HEIGHT = 30
BUTTON_RADIUS = 6
BUTTON_PADDING_HORIZONTAL = 14
BUTTON_PADDING_VERTICAL = 6

# Stage text and color mappings
# These mappings centralize the relationship between normalized stage keys
# and user-visible texts/colors to keep UI semantics consistent across tabs.
STAGE_TEXT_MAP = {
    "idle": "空闲",
    "preprocess": "预处理",
    "concat": "拼接",
    "finished": "完成",
}

STAGE_COLOR_MAP = {
    "idle": PRIMARY_BLUE,       # Default visual when idle
    "preprocess": "#f59e0b",   # Tailwind amber-500
    "concat": "#3b82f6",       # Tailwind blue-500
    "finished": "#22c55e",     # Tailwind green-500
}

# Common dialog titles and message formatters
MISSING_PATHS_WARNING_TITLE = "提示"

def format_missing_paths_warning(paths) -> str:
    """构造缺失/不可访问路径的汇总警告消息。

    Parameters
    ----------
    paths : Iterable[Union[str, pathlib.Path]]
        待汇总展示的路径列表，接受字符串或 Path 对象。

    Returns
    -------
    str
        适用于 QMessageBox 的多行文本，形如：
        "以下文件不存在或不可访问:\n<path1>\n<path2>\n..."

    Notes
    -----
    - 该函数不做存在性判断，仅负责格式化已标记为缺失/不可访问的路径。
    - 避免跨模块强依赖 Qt，保持为纯字符串格式化函数。
    """
    try:
        from pathlib import Path  # 局部导入以避免在非路径场景下的开销
    except Exception:
        Path = None
    try:
        lines = []
        for p in (paths or []):
            try:
                if Path is not None and isinstance(p, Path):
                    lines.append(str(p))
                else:
                    lines.append(str(p))
            except Exception:
                pass
        return "以下文件不存在或不可访问:\n" + "\n".join(lines)
    except Exception:
        return "以下文件不存在或不可访问:\n"


def build_progressbar_stylesheet(height: int, chunk_color: str = PRIMARY_BLUE) -> str:
    """构造统一的 QProgressBar 样式字符串（QSS）。

    此函数集中管理进度条的样式，使各标签页的视觉一致：
    - 固定高度（min/max-height）
    - 1px 边框与 4px 圆角
    - 文本居中显示
    - 进度块（chunk）颜色可配置

    Parameters
    ----------
    height : int
        进度条的像素高度，用于设定 min/max-height。
    chunk_color : str, optional
        进度块颜色（CSS 颜色字符串），默认使用主题主色。

    Returns
    -------
    str
        可直接用于 `QProgressBar.setStyleSheet` 的 QSS 字符串。

    Notes
    -----
    - 函数不依赖 Qt 类型，纯字符串构造，便于在非 GUI 环境下复用或测试。
    """
    try:
        h = int(height)
    except Exception:
        h = BUTTON_HEIGHT
    try:
        color = str(chunk_color)
    except Exception:
        color = PRIMARY_BLUE
    return (
        f"QProgressBar{{min-height:{h}px;max-height:{h}px;border:1px solid #bbb;border-radius:4px;text-align:center;}}"
        f"QProgressBar::chunk{{background-color:{color};margin:0px;}}"
    )