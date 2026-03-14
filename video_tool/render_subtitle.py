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

    def _run_pycaps_render(self, input_video: str, output_video: str, srt_path: str, 
                           v_align: str, v_offset: float, css: str) -> bool:
        """Internal helper to run a single pycaps rendering pass."""
        try:
            builder = CapsPipelineBuilder()
            
            # Configure layout options
            align_type = VerticalAlignmentType(v_align.lower())
            layout_options = SubtitleLayoutOptions(
                vertical_align=VerticalAlignment(align=align_type, offset=v_offset)
            )
            builder.with_layout_options(layout_options)
            
            builder.with_input_video(input_video)
            builder.with_output_video(output_video)
            builder.with_transcription(srt_path)
            builder.add_css_content(css)
            
            pipeline = builder.build()
            pipeline.run()
            return True
        except Exception as e:
            print(f"❌ Pass Error: {e}")
            return False

    def render(self, video_path: str, output_path: Optional[str] = None, css: Optional[str] = None, 
               style_name: str = "classic_white", 
               v_align: str = "bottom", v_offset: float = 0.0,
               caption_text: Optional[str] = None) -> bool:
        """Render styled subtitles onto the video."""
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

        final_css = css if css else self.styles.get(style_name, self.default_css)

        print(f"🚀 Starting rendering (Style: {style_name}, Position: {v_align}+{v_offset})...")
        if caption_text:
            print(f"💡 With Caption: {caption_text}")

        import uuid
        temp_dir = v_path.parent / "temp"
        temp_dir.mkdir(exist_ok=True)
        
        temp_files = []
        try:
            current_video = str(v_path.absolute())
            
            # --- PASS 1: Render Cover Caption (if exists) ---
            if caption_text:
                print(f"⏳ Pass 1: Rendering cover caption...")
                temp_caption_srt = temp_dir / f"temp_cap_{uuid.uuid4().hex[:8]}.srt"
                temp_files.append(temp_caption_srt)
                with open(temp_caption_srt, 'w', encoding='utf-8') as f:
                    # 展示前 600ms 作为封面
                    f.write(f"1\n00:00:00,000 --> 00:00:00,600\n{caption_text}")
                
                # 为文案定制大号居中样式
                import re
                font_size_match = re.search(r"font-size:\s*(\d+)px", final_css)
                base_size = int(font_size_match.group(1)) if font_size_match else 40
                caption_css = final_css + f"\n.line {{ font-size: {int(base_size * 2.2)}px !important; text-align: center !important; font-weight: bold !important; }}"
                
                temp_v1 = temp_dir / f"temp_v1_{uuid.uuid4().hex[:8]}{v_path.suffix}"
                temp_files.append(temp_v1)
                
                # 强制居中对齐渲染文案
                success = self._run_pycaps_render(
                    current_video, str(temp_v1.absolute()), str(temp_caption_srt.absolute()),
                    "center", 0.0, caption_css
                )
                if not success: return False
                current_video = str(temp_v1.absolute())

            # --- PASS 2: Render Main Subtitles ---
            print(f"⏳ Pass 2: Rendering main subtitles...")
            success = self._run_pycaps_render(
                current_video, str(out_path.absolute()), str(s_path.absolute()),
                v_align, v_offset, final_css
            )
            
            if success:
                print(f"✨ Successfully rendered to: {out_path.name}")
            return success

        except Exception as e:
            import traceback
            traceback.print_exc()
            return False
        finally:
            # Cleanup all temp files
            for f in temp_files:
                if f.exists():
                    try:
                        f.unlink()
                    except:
                        pass
            
            # 如果 temp 目录为空，则删除它
            if 'temp_dir' in locals() and temp_dir.exists():
                try:
                    if not any(temp_dir.iterdir()):
                        temp_dir.rmdir()
                except:
                    pass


if __name__ == "__main__":
    # Simple test logic if run directly
    if len(sys.argv) > 1:
        renderer = SubtitleRenderer()
        renderer.render(sys.argv[1])
    else:
        print("Usage: python render_subtitle.py <video_path>")
