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
from concurrent.futures import ThreadPoolExecutor, as_completed

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from video_tool.render_subtitle import SubtitleRenderer


def main():
    renderer = SubtitleRenderer()
    available_styles = list(renderer.styles.keys())
    
    parser = argparse.ArgumentParser(description="Styled Subtitle Rendering CLI using pycaps")
    parser.add_argument("path", type=str, help="Path to the input video file or directory")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output path or directory (default: same as input)")
    parser.add_argument("--font", "-f", type=str, default="Microsoft YaHei", help="Font family to use (default: Microsoft YaHei)")
    parser.add_argument("--style", "-s", type=str, default="classic_white", choices=available_styles, help=f"Predefined style to use. Available: {', '.join(available_styles)}")
    parser.add_argument("--css", "-c", type=str, default=None, help="Custom CSS for subtitle styling (overrides --style)")
    parser.add_argument("--v-align", type=str, default="bottom", choices=["top", "center", "bottom"], help="Vertical alignment of subtitles (default: bottom)")
    parser.add_argument("--v-offset", type=float, default=0.0, help="Vertical offset from -1.0 to 1.0 (default: 0.0)")
    parser.add_argument("--workers", "-w", type=int, default=1, help="Number of concurrent workers (default: 1, serial)")
    parser.add_argument("--no-gpu", action="store_true", help="Disable GPU acceleration even if available")

    args = parser.parse_args()

    if args.no_gpu:
        os.environ["MOVIELITE_USE_GPU"] = "0"
    else:
        os.environ["MOVIELITE_USE_GPU"] = "1"

    renderer.font_family = args.font
    # Update styles with the potentially new font family
    renderer.__init__(font_family=args.font) 
    
    input_path = Path(args.path)
    if not input_path.exists():
        print(f"❌ Error: Input path '{input_path}' does not exist.")
        sys.exit(1)

    video_files = []
    if input_path.is_file():
        video_files.append(input_path)
    else:
        # Define supported video extensions
        extensions = [".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".webm"]
        video_files = [f for f in input_path.iterdir() if f.is_file() and f.suffix.lower() in extensions]
        print(f"📂 Found {len(video_files)} video files in '{input_path}'.")

    if not video_files:
        print(f"⚠️ No video files found to process.")
        sys.exit(0)

    # Determine default output directory
    if args.output:
        final_output_dir = Path(args.output)
    else:
        if input_path.is_file():
            # If input is a file, default output is same directory
            final_output_dir = input_path.parent
        else:
            # If input is a directory, default output is directory-字幕版
            final_output_dir = input_path.parent / f"{input_path.name}-字幕版"
    
    if not final_output_dir.exists():
        final_output_dir.mkdir(parents=True, exist_ok=True)
        print(f"📁 Created output directory: '{final_output_dir}'")

    success_count = 0
    fail_count = 0
    
    def process_single_video(video_file: Path) -> tuple[bool, str]:
        """Process a single video file and return success status and filename."""
        # Output filename: if in the same directory as input, add a suffix to avoid overwriting
        if final_output_dir == video_file.parent:
            output_filename = f"{video_file.stem}-字幕版{video_file.suffix}"
        else:
            output_filename = f"{video_file.stem}{video_file.suffix}"
            
        output_path = str(final_output_dir / output_filename)
        
        # 尝试在视频同目录下读取封面文案配置
        caption_text = None
        config_path = video_file.parent / f"{video_file.stem}_caption_config.json"
        if config_path.exists():
            try:
                import json
                import random
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 支持之前的格式 {"captions": [...]} 和现在的格式 [{"category": "...", "content": "..."}]
                    if isinstance(data, list):
                        captions = [item.get("content") for item in data if item.get("content")]
                    elif isinstance(data, dict):
                        captions = data.get("captions", [])
                    else:
                        captions = []
                    
                    if captions:
                        selected_caption = random.choice(captions)
                        caption_text = f"#{selected_caption}"
            except Exception as ce:
                print(f"⚠️ Warning: Failed to read caption config for {video_file.name}: {ce}")

        success = renderer.render(
            video_path=str(video_file),
            output_path=output_path,
            css=args.css,
            style_name=args.style,
            v_align=args.v_align,
            v_offset=args.v_offset,
            caption_text=caption_text
        )
        return success, video_file.name

    if args.workers > 1:
        print(f"🚀 Starting concurrent processing with {args.workers} workers...")
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_video = {executor.submit(process_single_video, video_file): video_file for video_file in video_files}
            for future in as_completed(future_to_video):
                success, filename = future.result()
                if success:
                    success_count += 1
                    print(f"✅ Finished: {filename}")
                else:
                    fail_count += 1
                    print(f"❌ Failed: {filename}")
    else:
        for video_file in video_files:
            print(f"\n🎬 Processing: {video_file.name}...")
            success, filename = process_single_video(video_file)
            if success:
                success_count += 1
                print(f"✅ Finished: {filename}")
            else:
                fail_count += 1
                print(f"❌ Failed: {filename}")

    print("\n" + "="*30)
    print(f"📊 Batch Processing Summary:")
    print(f"✅ Successful: {success_count}")
    print(f"❌ Failed:     {fail_count}")
    print(f"📦 Total:      {len(video_files)}")
    print("="*30)

    if fail_count > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
