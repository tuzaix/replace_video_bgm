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


# 添加：探测视频平均码率（优先使用视频流bit_rate，其次使用容器format bit_rate）
def probe_video_bitrate(ffprobe_bin: str, video: Path) -> int | None:
    try:
        proc = subprocess.run([
            ffprobe_bin, '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=bit_rate',
            '-of', 'default=nw=1:nk=1',
            str(video)
        ], capture_output=True, text=True, encoding='utf-8', errors='replace')
        if proc.returncode == 0:
            out = proc.stdout.strip()
            if out:
                try:
                    br = int(float(out))
                    if br > 0:
                        return br
                except Exception:
                    pass
    except Exception:
        pass

    try:
        proc2 = subprocess.run([
            ffprobe_bin, '-v', 'error',
            '-show_entries', 'format=bit_rate',
            '-of', 'default=nw=1:nk=1',
            str(video)
        ], capture_output=True, text=True, encoding='utf-8', errors='replace')
        if proc2.returncode == 0:
            out2 = proc2.stdout.strip()
            if out2:
                try:
                    br2 = int(float(out2))
                    if br2 > 0:
                        return br2
                except Exception:
                    pass
    except Exception:
        pass

    return None

# 修改：添加 copy_video 开关参数
def build_ffmpeg_cmd(ffmpeg_bin: str, video: Path, bgm: Path, output: Path, audio_bitrate: str, loop_audio: bool, crf: int, preset: str, use_gpu: bool, max_compression: bool = False, two_pass: bool = False, target_bitrate_bps: int | None = None, copy_video: bool = False) -> List[str]:
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
    ]

    if copy_video:
        # 视频不重编码，直接复制
        cmd += [
            '-c:v', 'copy',
        ]
    else:
        # 非复制模式，按GPU/CPU路径设置压缩参数
        # 为避免改变帧率，保留原始fps，不强制设置 -r
        if use_gpu:
            cmd += [
                '-c:v', 'h264_nvenc',
                '-preset', 'p7',
                '-rc', 'vbr',
            ]
            if target_bitrate_bps:
                br_k = f"{int(target_bitrate_bps/1000)}k"
                max_k = f"{int(target_bitrate_bps*1.2/1000)}k"
                buf_k = f"{int(target_bitrate_bps*2/1000)}k"
                cmd += ['-b:v', br_k, '-maxrate', max_k, '-bufsize', buf_k, '-cq', str(crf)]
            else:
                cmd += [
                    '-cq', str(crf),
                    '-b:v', '0',
                    '-maxrate', '10M',
                    '-bufsize', '20M',
                ]
        else:
            if target_bitrate_bps:
                br_k = f"{int(target_bitrate_bps/1000)}k"
                max_k = f"{int(target_bitrate_bps*1.2/1000)}k"
                buf_k = f"{int(target_bitrate_bps*2/1000)}k"
                cmd += [
                    '-c:v', 'libx264',
                    '-preset', preset,
                    '-b:v', br_k,
                    '-maxrate', max_k,
                    '-bufsize', buf_k,
                    '-tune', 'film',
                    '-profile:v', 'high',
                    '-level', '4.1',
                ]
            else:
                cmd += [
                    '-c:v', 'libx264',
                    '-crf', str(crf),
                    '-preset', preset,
                    '-tune', 'film',
                    '-profile:v', 'high',
                    '-level', '4.1',
                ]
            # 最大压缩参数（仅在不复制视频时适用）
            if max_compression:
                cmd += [
                    '-x264-params', 'aq-mode=3:aq-strength=0.8:deblock=1,1:ref=5:bframes=5:b-adapt=2:direct=auto:me=umh:subme=10:merange=24:trellis=2:partitions=all:8x8dct=1:fast-pskip=0:mixed-refs=1',
                    '-flags', '+cgop',
                    '-g', '250',
                ]

        # 通用视频优化参数
        if not copy_video:
            cmd += [
                '-pix_fmt', 'yuv420p',
            ]
        cmd += [
            '-movflags', '+faststart',
        ]

        # 两遍编码（仅CPU且非复制视频时）
        if two_pass and not use_gpu:
            cmd += ['-pass', '1', '-f', 'null']

    # 音频：统一编码为AAC并设置码率
    cmd += [
        '-c:a', 'aac',
        '-b:a', audio_bitrate,
        '-ac', '2',
        '-ar', '44100',
    ]

    cmd += [
        '-shortest',
        str(output),
    ]
    return cmd


# 修改：process_one 支持 copy_video 并在打印中体现
def process_one(ffmpeg_bin: str, ffprobe_bin: str, video: Path, bgm: Path, out_dir: Path, audio_bitrate: str, crf: int, preset: str, use_gpu_flag: bool, has_gpu_encoder: bool, max_compression: bool = False, two_pass: bool = False, target_reduction: float = 0.5, copy_video: bool = False) -> Tuple[bool, Path | None, str | None]:
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"{video.stem}_bgm.mp4"
    loop_audio = should_loop_audio(ffprobe_bin, video, bgm)

    # 计算目标码率（复制视频时无需）
    src_br = None
    target_bitrate_bps = None
    if not copy_video:
        src_br = probe_video_bitrate(ffprobe_bin, video)
        if src_br and 0 < target_reduction < 1:
            target_bitrate_bps = int(src_br * target_reduction)

    def run_ffmpeg(use_gpu: bool):
        codec_name = "COPY" if copy_video else ("GPU (h264_nvenc)" if use_gpu else "CPU (libx264)")
        compression_info = ""
        if max_compression and not copy_video:
            compression_info += " [最大压缩]"
        if (two_pass or (target_bitrate_bps is not None)) and not use_gpu and not copy_video:
            compression_info += " [两遍编码]"
        if target_bitrate_bps and not copy_video:
            kbps = int(target_bitrate_bps/1000)
            compression_info += f" [目标码率≈{kbps}kbps]"
        print(f"🎬 合成 ({codec_name}{compression_info}): {video.name} + 🎵 {bgm.name} -> {output.name} (loop={loop_audio})")

        # 复制视频：直接构建一次命令
        if copy_video:
            cmd = build_ffmpeg_cmd(ffmpeg_bin, video, bgm, output, audio_bitrate, loop_audio, crf, preset, False, max_compression, False, None, True)
        else:
            # 非复制：两遍编码（仅CPU）
            if (two_pass or (target_bitrate_bps is not None)) and not use_gpu:
                cmd1 = build_ffmpeg_cmd(ffmpeg_bin, video, bgm, Path("/dev/null"), audio_bitrate, loop_audio, crf, preset, use_gpu, max_compression, False, target_bitrate_bps, False)
                cmd1[-1] = "NUL" if os.name == 'nt' else "/dev/null"
                cmd1.insert(-1, '-pass')
                cmd1.insert(-1, '1')
                cmd1.insert(-1, '-f')
                cmd1.insert(-1, 'null')
                try:
                    proc1 = subprocess.run(cmd1, capture_output=True, text=True, encoding='utf-8', errors='replace')
                    if proc1.returncode != 0:
                        class MockProc:
                            returncode = proc1.returncode
                            stderr = f"第一遍编码失败: {proc1.stderr}"
                        return MockProc()
                except Exception as e:
                    class MockProc:
                        returncode = -1
                        stderr = f"第一遍编码异常: {str(e)}"
                    return MockProc()
                cmd2 = build_ffmpeg_cmd(ffmpeg_bin, video, bgm, output, audio_bitrate, loop_audio, crf, preset, use_gpu, max_compression, False, target_bitrate_bps, False)
                cmd2.insert(-1, '-pass')
                cmd2.insert(-1, '2')
                cmd = cmd2
            else:
                cmd = build_ffmpeg_cmd(ffmpeg_bin, video, bgm, output, audio_bitrate, loop_audio, crf, preset, use_gpu, max_compression, two_pass, target_bitrate_bps, False)

        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
        except Exception as e:
            class MockProc:
                returncode = -1
                stderr = str(e)
            return MockProc()

    # 执行逻辑：复制视频时不走GPU优先策略
    if copy_video:
        proc = run_ffmpeg(use_gpu=False)
    else:
        if use_gpu_flag and has_gpu_encoder:
            proc = run_ffmpeg(use_gpu=True)
            if proc.returncode != 0:
                print(f"""⚠️ GPU 合成失败，自动切换到 CPU... 
{proc.stderr[:500]}...""")
                proc = run_ffmpeg(use_gpu=False)
        else:
            proc = run_ffmpeg(use_gpu=False)

    if proc.returncode == 0:
        print(f"✅ 成功 ({'COPY' if copy_video else ('GPU' if use_gpu_flag and has_gpu_encoder else 'CPU')}): {output.name}")
        return True, output, None
    else:
        print(f"""❌ 失败: {video.name} -> {output.name}
{proc.stderr[:2000]}...""")
        return False, None, proc.stderr


def has_nvenc(ffmpeg_bin: str) -> bool:
    """Checks if NVIDIA NVENC encoder is available in ffmpeg."""
    try:
        proc = subprocess.run([ffmpeg_bin, '-encoders'], capture_output=True, text=True, encoding='utf-8', errors='replace')
        return 'h264_nvenc' in proc.stdout
    except Exception:
        return False


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
    parser.add_argument('--gpu', action='store_true', default=True, help='优先使用GPU(NVIDIA NVENC)进行视频编码，失败时自动切换回CPU')
    parser.add_argument('--copy-video', action='store_true', help='不重编码视频，直接替换合成BGM（速度最快，依赖容器/视频编码兼容性）')
    parser.add_argument('--max-compression', action='store_true', help='启用最大压缩模式，进一步减小文件体积（会增加编码时间）')
    parser.add_argument('--two-pass', action='store_true', help='启用两遍编码，获得更好的压缩效果（会显著增加编码时间）')
    parser.add_argument('--target-reduction', type=float, default=0.5, help='目标体积压缩比例，例如0.5表示压缩到50%大小')

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

    # 检测GPU支持
    nvenc_supported = False
    if args.gpu:
        nvenc_supported = has_nvenc(ffmpeg_bin)
        if not nvenc_supported:
            print("⚠️ 未检测到 NVIDIA NVENC 编码器，将使用 CPU。")
        else:
            print("✅ 检测到 NVIDIA NVENC 编码器，将优先使用 GPU。")

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
            executor.submit(process_one, ffmpeg_bin, ffprobe_bin, v, b, out_dir, args.audio_bitrate, args.crf, args.preset, args.gpu, nvenc_supported, args.max_compression, args.two_pass, args.target_reduction, args.copy_video): (v, b)
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