"""
Video Slicer Script

Usage:
    python tools/video_slicer.py <video_dir> <segment_duration> <output_dir>

Parameters:
    video_dir: Directory containing videos to slice.
    segment_duration: Duration of each slice in seconds.
    output_dir: Directory to save the sliced segments.
"""

from __future__ import annotations

import os
import sys
import argparse
import subprocess
from pathlib import Path
from typing import List

# Ensure project root is in sys.path for internal imports
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.common_utils import is_video_file, get_subprocess_silent_kwargs
from utils.calcu_video_info import ffmpeg_bin, ffprobe_bin


def get_video_duration(video_path: Path) -> float:
    """获取视频总时长（秒）。"""
    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, **get_subprocess_silent_kwargs())
        return float(result.stdout.strip())
    except Exception as e:
        print(f"警告：无法获取视频时长 {video_path.name}: {e}")
        return 0.0


def slice_video(video_path: Path, segment_duration: int, output_dir: Path, max_segments: int = 0, start_ms: int = 0) -> int:
    """将单个视频切片并保存到输出目录。
    
    max_segments: 每个视频最多切出的个数。0 表示不限制。
    start_ms: 起始偏移位置（毫秒）。
    """
    duration = get_video_duration(video_path)
    start_offset = start_ms / 1000.0
    
    if duration <= start_offset:
        print(f" (跳过：原视频时长 {duration:.2f}s 不足起始位置 {start_offset:.2f}s)", end="")
        return 0

    remaining_duration = duration - start_offset
    if remaining_duration < 3:
        print(f" (跳过：剩余时长不足 3 秒: {remaining_duration:.2f}s)", end="")
        return 0

    # 计算理论总切片数，但循环中会受到 max_segments 限制
    theoretical_segments = int(remaining_duration // segment_duration) + (1 if remaining_duration % segment_duration > 0 else 0)
    
    success_count = 0
    for i in range(theoretical_segments):
        # 如果设置了最大切片数，达到后停止
        if max_segments > 0 and success_count >= max_segments:
            break

        start_time = start_offset + (i * segment_duration)
        remaining = duration - start_time
        if remaining <= 0:
            break
            
        current_duration = min(segment_duration, remaining)
        
        # 不足3秒的则直接丢弃
        if current_duration < 3:
            continue
            
        output_name = f"{video_path.stem}_seg_{i:03d}{video_path.suffix}"
        output_path = output_dir / output_name
        
        # 使用 -ss (定位) -t (时长) -i (输入) -c copy (无损) 进行切片
        cmd = [
            ffmpeg_bin,
            "-y",
            "-ss", str(start_time),
            "-t", str(current_duration),
            "-i", str(video_path),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-map", "0",
            "-movflags", "+faststart",
            str(output_path)
        ]
        
        try:
            subprocess.run(cmd, capture_output=True, check=True, **get_subprocess_silent_kwargs())
            success_count += 1
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode("utf-8", errors="ignore") if e.stderr else str(e)
            print(f"错误：切片 {output_name} 失败: {err_msg[:200]}")
            
    return success_count


def main():
    parser = argparse.ArgumentParser(description="批量视频切片工具")
    parser.add_argument("video_dir", type=str, help="视频目录")
    parser.add_argument("segment_duration", type=int, help="切片时长（秒）")
    parser.add_argument("output_dir", type=str, help="输出目录")
    parser.add_argument(
        "--count", "-c", 
        type=int, 
        default=0, 
        help="每个视频最多切出的个数（默认 0 表示切完）"
    )
    parser.add_argument(
        "--start", "-s", 
        type=int, 
        default=0, 
        help="起始位置（毫秒，默认 0）"
    )
    
    args = parser.parse_args()
    
    video_dir = Path(args.video_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    segment_duration = args.segment_duration
    max_count = args.count
    start_ms = args.start
    
    if not video_dir.exists() or not video_dir.is_dir():
        print(f"❌ 错误：输入视频目录不存在 -> {video_dir}")
        sys.exit(1)
        
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 查找所有视频文件（递归查找）
    video_files = []
    for root, _, files in os.walk(video_dir):
        for f in files:
            if is_video_file(f):
                video_files.append(Path(root) / f)
                
    if not video_files:
        print("ℹ️ 提示：未在指定目录中找到视频文件。")
        sys.exit(0)
        
    print(f"🚀 开始处理 {len(video_files)} 个视频文件...")
    print(f"📂 输入目录: {video_dir}")
    print(f"📂 输出目录: {output_dir}")
    print(f"⏱️ 切片时长: {segment_duration} 秒")
    if start_ms > 0:
        print(f"⏩ 起始位置: {start_ms} 毫秒 ({start_ms/1000.0:.2f} 秒)")
    if max_count > 0:
        print(f"🔢 每个视频最多切出: {max_count} 个")
    print("-" * 40)
    
    total_segments = 0
    for i, video_path in enumerate(video_files, 1):
        print(f"[{i}/{len(video_files)}] 正在切片: {video_path.name} ...", end="", flush=True)
        count = slice_video(video_path, segment_duration, output_dir, max_segments=max_count, start_ms=start_ms)
        total_segments += count
        print(f" 完成 (生成 {count} 个切片)")
        
    print("-" * 40)
    print(f"✨ 任务完成！共生成 {total_segments} 个切片文件。")
    print(f"📍 结果已保存至: {output_dir}")


if __name__ == "__main__":
    main()
