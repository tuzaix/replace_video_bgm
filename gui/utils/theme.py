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