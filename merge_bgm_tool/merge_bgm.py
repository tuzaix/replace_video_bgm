#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_bgm.py — 合成视频只保留BGM音频的工具

功能：
- 批量处理视频目录与BGM目录，将视频的原音轨去除，仅保留BGM音轨。
- BGM使用 -stream_loop -1 参数循环，并使用 -shortest 使输出时长与原视频对齐。
- 视频流使用 -c:v copy 直接拷贝（不重编码）。

命令示例：
ffmpeg -i input_video.mp4 -stream_loop -1 -i input_bgm.mp3 -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -shortest output_video.mp4

参数：
- 视频目录
- BGM目录
- 输出视频目录

匹配策略：
1) 优先按文件名（不含扩展名）一一匹配，例如 video1.mp4 与 video1.mp3。
2) 若只有一个BGM文件，则对所有视频都使用该BGM。
3) 否则按排序顺序循环分配BGM给视频（视频数量大于BGM数量时会循环复用）。

注意：
- -c:v copy 需要原视频编码兼容MP4容器，否则可能失败；失败时会提示错误。
- 需要系统已安装 ffmpeg 并可执行（PATH下或通过 --ffmpeg-path 指定）。
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path
import argparse
from typing import List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import random

SUPPORTED_VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.avi', '.webm'}
SUPPORTED_AUDIO_EXTS = {'.mp3', '.wav', '.m4a', '.aac', '.flac'}


def find_files_by_ext(directory: Path, exts: set) -> List[Path]:
    files: List[Path] = []
    for p in sorted(directory.rglob('*')):
        if p.is_file() and p.suffix.lower() in exts:
            files.append(p)
    return files


def pick_ffmpeg(ffmpeg_path: str | None) -> str:
    if ffmpeg_path:
        return ffmpeg_path
    which = shutil.which('ffmpeg')
    if not which:
        raise FileNotFoundError('未找到 ffmpeg。请确保其在 PATH 中或使用 --ffmpeg-path 指定。')
    return which


def pick_ffprobe(ffmpeg_bin: str) -> str:
    ffprobe = shutil.which('ffprobe')
    if ffprobe:
        return ffprobe
    p = Path(ffmpeg_bin).parent
    candidates = [p / 'ffprobe', p / 'ffprobe.exe']
    for c in candidates:
        if c.exists():
            return str(c)
    raise FileNotFoundError('未找到 ffprobe。请确保其在 PATH 中或与 ffmpeg 同目录。')


def probe_duration(ffprobe_bin: str, media: Path) -> float | None:
    try:
        proc = subprocess.run(
            [ffprobe_bin, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(media)],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        if proc.returncode == 0:
            s = proc.stdout.strip()
            return float(s) if s else None
        return None
    except Exception:
        return None


def should_loop_audio(ffprobe_bin: str, video: Path, bgm: Path) -> bool:
    v_dur = probe_duration(ffprobe_bin, video)
    a_dur = probe_duration(ffprobe_bin, bgm)
    if v_dur is None or a_dur is None:
        # 无法探测，保险起见循环音频以保证视频全长
        return True
    return v_dur > a_dur


def pair_videos_bgms(videos: List[Path], bgms: List[Path], random_bgm: bool = False) -> List[Tuple[Path, Path]]:
    pairs: List[Tuple[Path, Path]] = []
    if not videos:
        return pairs
    if not bgms:
        raise ValueError('BGM目录中未找到音频文件。')

    if random_bgm:
        # 为每个视频随机挑选一个BGM
        return [(v, random.choice(bgms)) for v in videos]

    bgm_by_stem = {b.stem.lower(): b for b in bgms}

    if len(bgms) == 1:
        # 单一BGM：全部复用
        sole = bgms[0]
        return [(v, sole) for v in videos]

    # 多BGM：优先按同名匹配，否则循环分配
    idx = 0
    for v in videos:
        stem = v.stem.lower()
        if stem in bgm_by_stem:
            pairs.append((v, bgm_by_stem[stem]))
        else:
            pairs.append((v, bgms[idx % len(bgms)]))
            idx += 1
    return pairs


def build_ffmpeg_cmd(ffmpeg_bin: str, video: Path, bgm: Path, output: Path, audio_bitrate: str, loop_audio: bool, crf: int, preset: str) -> List[str]:
    cmd = [
        ffmpeg_bin,
        '-y',
        '-i', str(video),
    ]
    if loop_audio:
        cmd += ['-stream_loop', '-1']
    cmd += [
        '-i', str(bgm),
        '-map', '0:v',
        '-map', '1:a',
        '-r', '30',
        '-c:v', 'libx264',
        '-crf', str(crf),
        '-preset', preset,
        '-c:a', 'aac',
        '-b:a', audio_bitrate,
        '-shortest',
        str(output),
    ]
    return cmd


def process_one(ffmpeg_bin: str, ffprobe_bin: str, video: Path, bgm: Path, out_dir: Path, audio_bitrate: str, crf: int, preset: str) -> Tuple[bool, Path | None, str | None]:
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"{video.stem}_bgm.mp4"
    loop_audio = should_loop_audio(ffprobe_bin, video, bgm)
    cmd = build_ffmpeg_cmd(ffmpeg_bin, video, bgm, output, audio_bitrate, loop_audio, crf, preset)
    
    try:
        print(f"🎬 合成: {video.name} + 🎵 {bgm.name} -> {output.name} (loop={loop_audio})")
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        if proc.returncode == 0:
            print(f"✅ 成功: {output}")
            return True, output, None
        else:
            print(f"❌ 失败: {video.name} -> {output.name}\n{proc.stderr[:2000]}...")
            return False, None, proc.stderr
    except Exception as e:
        return False, None, str(e)


def main():
    parser = argparse.ArgumentParser(description='合成视频仅保留BGM的批量工具')
    parser.add_argument('video_dir', help='视频目录')
    parser.add_argument('bgm_dir', help='BGM目录或具体音频文件')
    parser.add_argument('--output-dir', default=None, help='合成输出视频目录（默认在视频目录旁创建_bgm后缀目录）')
    parser.add_argument('--ffmpeg-path', default=None, help='ffmpeg可执行路径（默认使用PATH中的ffmpeg）')
    parser.add_argument('--audio-bitrate', default='192k', help='音频码率，默认192k')
    parser.add_argument('--workers', type=int, default=6, help='并发合成数量，默认6')
    parser.add_argument('--random-bgm', action='store_true', help='为每个视频随机挑选一个BGM')
    parser.add_argument('--seed', type=int, default=None, help='随机种子，用于复现随机选择')
    parser.add_argument('--crf', type=int, default=28, help='视频质量CRF，数值越大压缩越强、体积越小，默认28（建议范围24-30）')
    parser.add_argument('--preset', default='veryslow', choices=['ultrafast','superfast','veryfast','faster','fast','medium','slow','slower','veryslow'], help='压缩速度/效率preset，越慢压缩越好，默认veryslow')

    args = parser.parse_args()

    video_dir = Path(args.video_dir)
    bgm_input = Path(args.bgm_dir)

    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        out_dir = video_dir.parent / f"{video_dir.name}_bgm"

    if not video_dir.exists() or not video_dir.is_dir():
        print(f"错误：视频目录不存在或不可用：{video_dir}")
        sys.exit(1)
    if not bgm_input.exists():
        print(f"错误：BGM路径不存在或不可用：{bgm_input}")
        sys.exit(1)

    videos = find_files_by_ext(video_dir, SUPPORTED_VIDEO_EXTS)

    if bgm_input.is_file():
        if bgm_input.suffix.lower() in SUPPORTED_AUDIO_EXTS:
            bgms = [bgm_input]
        else:
            print(f"错误：不支持的BGM文件类型：{bgm_input.suffix}")
            sys.exit(1)
    elif bgm_input.is_dir():
        bgms = find_files_by_ext(bgm_input, SUPPORTED_AUDIO_EXTS)
    else:
        print(f"错误：无效的BGM路径：{bgm_input}")
        sys.exit(1)

    if not videos:
        print("错误：未在视频目录中找到可支持的文件。")
        sys.exit(1)
    if not bgms:
        print("错误：未在BGM目录中找到可支持的音频文件。")
        sys.exit(1)

    try:
        ffmpeg_bin = pick_ffmpeg(args.ffmpeg_path)
    except FileNotFoundError as e:
        print(f"错误：{e}")
        sys.exit(1)

    # 选择 ffprobe
    try:
        ffprobe_bin = pick_ffprobe(ffmpeg_bin)
    except FileNotFoundError as e:
        print(f"错误：{e}")
        sys.exit(1)

    # 设置随机种子（如提供）
    if args.seed is not None:
        random.seed(args.seed)

    pairs = pair_videos_bgms(videos, bgms, random_bgm=args.random_bgm)

    total = len(pairs)
    ok = 0
    fail = 0
    failed_items: List[Tuple[Path, str]] = []

    print(f"📦 待处理视频数量：{total}")

    # 并发执行合成任务
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_pair = {
            executor.submit(process_one, ffmpeg_bin, ffprobe_bin, v, b, out_dir, args.audio_bitrate, args.crf, args.preset): (v, b)
            for (v, b) in pairs
        }
        i = 0
        for future in as_completed(future_to_pair):
            v, b = future_to_pair[future]
            i += 1
            try:
                success, output, error = future.result()
                if success:
                    ok += 1
                    print(f"进度：{i}/{total} ✅ {v.name}")
                else:
                    fail += 1
                    failed_items.append((v, error or "未知错误"))
                    print(f"进度：{i}/{total} ❌ {v.name}")
            except Exception as e:
                fail += 1
                failed_items.append((v, str(e)))
                print(f"进度：{i}/{total} ❌ {v.name}")

    print("\n=== 汇总 ===")
    print(f"✅ 成功：{ok}")
    print(f"❌ 失败：{fail}")
    if failed_items:
        print("失败列表：")
        for v, err in failed_items:
            print(f" - {v.name}: {str(err)[:200]}")

    sys.exit(0 if fail == 0 else 2)


if __name__ == '__main__':
    main()