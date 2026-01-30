"""
Quick preview script to generate a sample cover image and verify caption line spacing.

This script:
- Ensures required dependencies (`opencv-python-headless`, `Pillow`) are installed.
- Builds a stitched base image from the repo sample image.
- Overlays a two-line caption using `render_caption_blocks`.
- Saves the result to `test/preview/preview_cover.png`.

Run:
    python tools/preview_line_spacing.py
"""

import os
import sys
import subprocess
from pathlib import Path
from utils.common_utils import get_subprocess_silent_kwargs


def ensure_deps():
    """Ensure `cv2` and `PIL` are available; install if missing."""
    try:
        import cv2  # noqa: F401
        from PIL import Image  # noqa: F401
        return
    except Exception:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "opencv-python-headless", "Pillow"], **get_subprocess_silent_kwargs())  # noqa: S603,S607


def main() -> str:
    """Generate a sample cover with tighter line spacing and return the output path."""
    ensure_deps()
    import cv2
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    from cover_tool.generate_cover import build_stitched_image, render_caption_blocks
    img_src = os.path.join(root, "gui", "wechat", "admin1.png")
    if not os.path.isfile(img_src):
        raise FileNotFoundError(f"Sample image not found: {img_src}")

    # Build base image using three copies (for visible stitching)
    stitched = build_stitched_image([img_src, img_src, img_src])

    # Define a centered caption block. Control active area uses 16:9 1600x900 for mapping.
    block = {
        "text": "第一行文字（行距预览）\n第二行文字（更紧凑）",
        "active_w": 1600,
        "active_h": 900,
        "box_w": 900,
        "box_h": 220,
        "pixel_x": 1600 // 2 - 900 // 2,
        "pixel_y": 900 // 2 - 220 // 2,
        "font_size": 64,
        "font_bold": False,
        "font_family": "SourceHanSansCN-Regular",
        "color": "#ffffffff",
        "bgcolor": "#00000088",
        "stroke_color": "#00000000",
        "align": "center",
        # You can override spacing via the following key if needed:
        # "line_gap_ratio": 0.18,
    }

    out = render_caption_blocks(stitched, [block])
    out_dir = os.path.join(root, "test", "preview")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out_path = os.path.join(out_dir, "preview_cover.png")
    cv2.imwrite(out_path, out)
    return out_path


if __name__ == "__main__":
    try:
        p = main()
        print(p)
    except Exception as e:
        print(f"[preview failed] {e}")
        raise