"""
Render Subtitle CLI
Command line interface for styled subtitle rendering using pycaps.
"""

from __future__ import annotations

import os
import sys
import argparse
from pathlib import Path
from typing import Optional

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from video_tool.render_subtitle import SubtitleRenderer


def main():
    renderer = SubtitleRenderer()
    available_styles = list(renderer.styles.keys())
    
    parser = argparse.ArgumentParser(description="Styled Subtitle Rendering CLI using pycaps")
    parser.add_argument("video_path", type=str, help="Path to the input video file")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output path (default: input_dir/<stem>_<style>.mp4)")
    parser.add_argument("--font", "-f", type=str, default="Microsoft YaHei", help="Font family to use (default: Microsoft YaHei)")
    parser.add_argument("--style", "-s", type=str, default="classic_white", choices=available_styles, help=f"Predefined style to use. Available: {', '.join(available_styles)}")
    parser.add_argument("--css", "-c", type=str, default=None, help="Custom CSS for subtitle styling (overrides --style)")

    args = parser.parse_args()

    renderer.font_family = args.font
    # Update styles with the potentially new font family
    renderer.__init__(font_family=args.font) 
    
    success = renderer.render(
        video_path=args.video_path,
        output_path=args.output,
        css=args.css,
        style_name=args.style
    )

    if success:
        print("✅ Styled subtitle rendering complete.")
        sys.exit(0)
    else:
        print("❌ Styled subtitle rendering failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
