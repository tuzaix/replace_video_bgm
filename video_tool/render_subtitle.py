"""
Render Subtitle Module
Using pycaps to render styled subtitles onto videos.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pycaps import CapsPipelineBuilder, load_transcription, SubtitleLayoutOptions, VerticalAlignment, VerticalAlignmentType

from utils.common_utils import get_subprocess_silent_kwargs
from video_tool.render_subtitle_fontcss_config import get_subtitle_styles


class SubtitleRenderer:
    """Class to handle styled subtitle rendering using pycaps."""

    def __init__(self, font_family: str = "Microsoft YaHei"):
        self.font_family = font_family
        self.styles = get_subtitle_styles(self.font_family)
        self.default_css = self.styles["classic_white"]

    def find_subtitle_file(self, video_path: Path) -> Optional[Path]:
        """Find a subtitle file with the same name as the video."""
        extensions = [".srt", ".ass", ".vtt"]
        for ext in extensions:
            subtitle_path = video_path.with_suffix(ext)
            if subtitle_path.exists():
                return subtitle_path
        return None

    def render(self, video_path: str, output_path: Optional[str] = None, css: Optional[str] = None, 
               style_name: str = "classic_white", 
               v_align: str = "bottom", v_offset: float = 0.0) -> bool:
        """Render styled subtitles onto the video.
        
        Args:
            video_path: Path to the input video.
            output_path: Optional output path.
            css: Custom CSS content.
            style_name: Predefined style name.
            v_align: Vertical alignment (top, center, bottom).
            v_offset: Vertical offset (-1.0 to 1.0).
        """
        if not CapsPipelineBuilder:
            return False

        v_path = Path(video_path).resolve()
        if not v_path.exists():
            print(f"Error: Video file not found: {v_path}")
            return False

        s_path = self.find_subtitle_file(v_path)
        if not s_path:
            print(f"Error: No matching subtitle file found for: {v_path.name}")
            return False

        if output_path is None:
            out_path = v_path.parent / f"{v_path.stem}_{style_name}{v_path.suffix}"
        else:
            out_path = Path(output_path).resolve()

        # Determine CSS to use
        final_css = css
        if not final_css:
            final_css = self.styles.get(style_name, self.default_css)

        print(f"🚀 Starting subtitle rendering (Style: {style_name}, Position: {v_align}+{v_offset})...")
        print(f"🎬 Video: {v_path.name}")
        print(f"📝 Subtitle: {s_path.name}")
        print(f"📦 Output: {out_path.name}")

        try:
            builder = CapsPipelineBuilder()
            
            # Configure layout options for positioning
            align_type = VerticalAlignmentType(v_align.lower())
            layout_options = SubtitleLayoutOptions(
                vertical_align=VerticalAlignment(align=align_type, offset=v_offset)
            )
            builder.with_layout_options(layout_options)

            # 确保路径转换为绝对路径字符串，处理 Windows 路径编码问题
            video_input = str(v_path.absolute())
            subtitle_input = str(s_path.absolute())
            output_file = str(out_path.absolute())
            
            builder.with_input_video(video_input)
            builder.with_output_video(output_file)
            
            # 使用新的 transcription 加载接口
            builder.with_transcription(subtitle_input)

            # 使用 add_css_content 直接添加 CSS 字符串
            builder.add_css_content(final_css)

            # Build and run the pipeline
            pipeline = builder.build()
            
            print(f"⏳ Running pycaps pipeline...")
            # pipeline.run() 在新版本中会自动完成渲染并输出到 with_output_video 指定的路径
            pipeline.run()
            
            print(f"✨ Successfully rendered styled subtitles to: {out_path}")
            return True
        except Exception as e:
            import traceback
            print(f"❌ Error during rendering: {e}")
            traceback.print_exc()
            return False


if __name__ == "__main__":
    # Simple test logic if run directly
    if len(sys.argv) > 1:
        renderer = SubtitleRenderer()
        renderer.render(sys.argv[1])
    else:
        print("Usage: python render_subtitle.py <video_path>")
