"""
Batch segment videos in a directory using FFmpeg segment muxer.

This script performs lossless segmentation (`-c copy`) for each video under
an input directory. Output files are named as `<stem>_seg_%03d.mp4` and written
to a specified output directory (default: `<input_dir>/segments`).
"""

from __future__ import annotations

import os
import sys
import subprocess
import argparse
import shutil
from pathlib import Path
from typing import List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.common_utils import is_video_file
from utils.calcu_video_info import ffmpeg_bin, ffprobe_bin


from utils.common_utils import get_subprocess_silent_kwargs as _popen_silent_kwargs


def list_videos(dir_path: Path) -> List[Path]:
    """List supported videos directly under `dir_path` (non-recursive)."""
    out: List[Path] = []
    try:
        for name in os.listdir(dir_path):
            p = dir_path / name
            if p.is_file() and is_video_file(p.name):
                out.append(p)
    except Exception:
        pass
    return sorted(out)


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
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, **_popen_silent_kwargs())
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def segment_one(ffmpeg_path: str, in_path: Path, out_dir: Path, segment_time: int, accurate: bool = False) -> Tuple[bool, List[Path], str | None]:
    """通过循环调用 FFmpeg 进行手动切片，不使用内置 segment muxer。"""
    duration = get_video_duration(in_path)
    if duration <= 0:
        return False, [], "无法获取视频时长"

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    created_files: List[Path] = []
    num_segments = int(duration // segment_time) + (1 if duration % segment_time > 0 else 0)
    
    kwargs = _popen_silent_kwargs()
    
    for i in range(num_segments):
        start_time = i * segment_time
        # 最后一节的持续时间通常小于 segment_time
        remaining = duration - start_time
        current_duration = min(segment_time, remaining)
        
        out_path = out_dir / f"{in_path.stem}_seg_{i:03d}.mp4"
        
        # 构造命令
        # -ss 放在 -i 之前是快速定位（寻址），但在 copy 模式下可能导致开头黑屏
        # 为了平衡，我们这里使用 -ss <start> -t <duration> -i <input>
        cmd = [
            ffmpeg_path,
            "-y",
            "-ss", str(start_time),
            "-t", str(current_duration),
            "-i", str(in_path),
        ]

        if accurate:
            cmd += [
                "-c:v", "libx264",
                "-crf", "22",
                "-preset", "veryfast",
                "-c:a", "aac",
                "-b:a", "192k",
            ]
        else:
            cmd += [
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
            ]

        cmd += [
            "-map", "0",
            "-movflags", "+faststart",
            str(out_path)
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, **kwargs)
            if proc.returncode == 0:
                created_files.append(out_path)
            else:
                err = (proc.stderr or b"").decode("utf-8", errors="ignore")
                return False, created_files, f"Segment {i} 失败: {err[:200]}"
        except Exception as e:
            return False, created_files, str(e)

    return True, created_files, None


def main() -> None:
    """CLI entry: segment all videos in a directory to fixed duration chunks."""
    parser = argparse.ArgumentParser(
        description=(
            "按照指定时长对视频进行切片（等长分割）。\n"
            "1. 默认无损模式：速度极快，但由于不重编码，切片点只能在关键帧上，时长会有微小误差。\n"
            "2. 精准模式(--accurate)：通过重编码确保时长绝对精确。\n"
            "3. 分批功能(--batch-size)：自动将生成的切片分文件夹存放。"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("video_dir", type=str, help="视频目录")
    parser.add_argument("segment_time", type=int, help="切片时长（秒）")
    parser.add_argument(
        "--output-dir", "-o",
        dest="output_dir",
        type=str,
        default=None,
        help="输出目录（默认为视频目录下的 segments 文件夹）",
    )
    parser.add_argument(
        "--batch-size", "-b",
        type=int,
        default=0,
        help="每个分包文件夹存放的切片数量（默认0，不分包）",
    )
    parser.add_argument(
        "--accurate", "-a",
        action="store_true",
        help="精准模式（重编码，耗时较长但时长精确）",
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=4,
        help="并发任务数（默认4）",
    )
    
    args = parser.parse_args()

    base_dir = Path(args.video_dir).resolve()
    if not base_dir.exists() or not base_dir.is_dir():
        print(f"❌ 错误：目录不存在 -> {base_dir}")
        sys.exit(1)

    # 临时存放所有切片的目录
    temp_out_dir = base_dir / "_temp_segments"
    temp_out_dir.mkdir(parents=True, exist_ok=True)

    videos = list_videos(base_dir)
    if not videos:
        print("ℹ️ 提示：未找到视频文件。")
        if temp_out_dir.exists():
            temp_out_dir.rmdir()
        sys.exit(0)

    mode_str = "🎯 精准模式 (重编码)" if args.accurate else "⚡ 无损模式 (快切，时长可能受GOP影响有误差)"
    
    print("=" * 60)
    print(f"🚀 开始批量切片任务")
    print(f"📂 模式: {mode_str}")
    print(f"📂 输入目录: {base_dir}")
    if args.batch_size > 0:
        print(f"� 分包大小: {args.batch_size} 个切片/文件夹")
    print(f"⏱️ 目标时长: {args.segment_time}s")
    print(f"🧵 并发线程: {args.workers}")
    print(f"📦 待处理数: {len(videos)}")
    print("=" * 60)

    all_created_files: List[Path] = []
    success_count = 0
    
    video_results = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_video = {
            executor.submit(segment_one, ffmpeg_bin, v, temp_out_dir, args.segment_time, args.accurate): v 
            for v in videos
        }
        
        for future in as_completed(future_to_video):
            video_path = future_to_video[future]
            try:
                ok, created, err = future.result()
                video_results.append((video_path, ok, created, err))
            except Exception as e:
                video_results.append((video_path, False, [], str(e)))

    # 按原始视频名称排序，保证切片分包时的顺序性
    video_results.sort(key=lambda x: x[0].name)

    # 收集每个视频对应的切片列表
    video_to_segments = []
    for i, (video_path, ok, created, err) in enumerate(video_results, 1):
        if ok:
            success_count += 1
            if created:
                video_to_segments.append(created)
            print(f"[{i}/{len(videos)}] ✅ {video_path.name} -> 生成 {len(created)} 个切片")
        else:
            print(f"[{i}/{len(videos)}] ❌ {video_path.name} 失败: {err[:200]}...")

    # 混编逻辑 (Interleaving / Round-Robin)
    all_created_files: List[Path] = []
    if video_to_segments:
        max_segs = max(len(segs) for segs in video_to_segments)
        for idx in range(max_segs):
            for seg_list in video_to_segments:
                if idx < len(seg_list):
                    all_created_files.append(seg_list[idx])

    # 执行分包归档逻辑
    if args.batch_size > 0 and all_created_files:
        print("\n📂 正在进行分包归档（采用复制模式，保留临时目录）...")
        batch_count = 0
        for i in range(0, len(all_created_files), args.batch_size):
            batch_count += 1
            batch_dir = base_dir / f"batch_segments_{batch_count:03d}"
            batch_dir.mkdir(parents=True, exist_ok=True)
            
            current_batch = all_created_files[i : i + args.batch_size]
            for file_path in current_batch:
                target_path = batch_dir / file_path.name
                if target_path.exists():
                    target_path = batch_dir / f"{file_path.stem}_{id(file_path)}{file_path.suffix}"
                # 使用 copy2 替代 move 以保留临时目录内容
                shutil.copy2(str(file_path), str(target_path))
        
        print(f"✨ 分包完成，共生成 {batch_count} 个文件夹。")
        print(f"📍 原始切片保留在: {temp_out_dir}")
    elif all_created_files:
        # 如果不分包，将切片从临时目录复制到最终输出目录
        final_out_dir = Path(args.output_dir).resolve() if args.output_dir else (base_dir / "segments")
        final_out_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n📂 正在导出切片到: {final_out_dir}")
        for file_path in all_created_files:
            shutil.copy2(str(file_path), str(final_out_dir / file_path.name))
        print(f"✨ 导出完成。原始切片保留在: {temp_out_dir}")

    print("=" * 60)
    print(f"✨ 任务完成！")
    print(f"✅ 成功处理: {success_count}/{len(videos)}")
    print(f"📁 总计切片: {len(all_created_files)}")
    print("=" * 60)


if __name__ == "__main__":
    main()

