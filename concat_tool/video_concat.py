#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频拼接工具
功能：随机选择n个视频进行拼接，然后替换BGM，不进行转码压缩以提高效率
"""

import os
import sys
import shutil
import tempfile
import time
from pathlib import Path
import argparse
import random
from typing import List, Optional
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# MoviePy imports
from moviepy.editor import VideoFileClip, concatenate_videoclips, AudioFileClip, concatenate_audioclips

# 支持的视频格式
SUPPORTED_VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.m4v'}
SUPPORTED_AUDIO_EXTS = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'}


def generate_auto_seed() -> int:
    """自动生成随机种子：基于时间戳和随机数组合"""
    # 获取当前时间戳（微秒级）
    timestamp = int(time.time() * 1000000)
    # 生成一个随机数
    rand_num = random.randint(1000, 9999)
    # 组合生成种子
    seed = (timestamp + rand_num) % (2**31 - 1)  # 确保在32位整数范围内
    return seed


def find_videos(directory: Path) -> List[Path]:
    """在目录中查找所有支持的视频文件"""
    videos = []
    if not directory.exists() or not directory.is_dir():
        return videos
    
    for file_path in directory.rglob('*'):
        if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_VIDEO_EXTS:
            videos.append(file_path)
    
    return sorted(videos)


def find_audio_files(directory: Path) -> List[Path]:
    """在目录中查找所有支持的音频文件"""
    audio_files = []
    if not directory.exists() or not directory.is_dir():
        return audio_files
    
    for file_path in directory.rglob('*'):
        if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_AUDIO_EXTS:
            audio_files.append(file_path)
    
    return sorted(audio_files)


def get_video_info(video_path: Path) -> dict:
    """使用MoviePy获取视频信息（分辨率、帧率、时长等）"""
    try:
        with VideoFileClip(str(video_path)) as clip:
            return {
                'width': clip.w,
                'height': clip.h,
                'fps': clip.fps,
                'duration': clip.duration
            }
    except Exception as e:
        print(f"⚠️ 获取视频信息失败 {video_path.name}: {e}")
        return {}


def probe_resolution_ffprobe(video_path: Path) -> Optional[tuple]:
    """使用 ffprobe 获取视频分辨率 (width, height)。
    优先使用 ffprobe，若不可用或失败，回退到 MoviePy 的 get_video_info。
    """
    ffprobe_bin = shutil.which('ffprobe')
    if ffprobe_bin:
        try:
            cmd = [
                ffprobe_bin,
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height',
                '-of', 'csv=p=0:s=x',
                str(video_path)
            ]
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode == 0:
                text = ''
                try:
                    text = (res.stdout or b'').decode('utf-8', errors='ignore').strip()
                except Exception:
                    try:
                        text = (res.stdout or b'').decode('mbcs', errors='ignore').strip()
                    except Exception:
                        text = ''
                if 'x' in text:
                    parts = text.split('x')
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        return int(parts[0]), int(parts[1])
        except Exception:
            pass
    # 回退到 MoviePy
    info = get_video_info(video_path)
    w = info.get('width')
    h = info.get('height')
    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
        return w, h
    return None


def probe_duration_ffprobe(video_path: Path) -> Optional[float]:
    """使用 ffprobe 获取视频时长（秒）。优先 ffprobe，失败时回退 MoviePy。
    返回浮点秒或 None。"""
    ffprobe_bin = shutil.which('ffprobe')
    if ffprobe_bin:
        try:
            cmd = [
                ffprobe_bin,
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                str(video_path)
            ]
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode == 0:
                text = ''
                try:
                    text = (res.stdout or b'').decode('utf-8', errors='ignore').strip()
                except Exception:
                    try:
                        text = (res.stdout or b'').decode('mbcs', errors='ignore').strip()
                    except Exception:
                        text = ''
                try:
                    dur = float(text)
                    if dur > 0:
                        return dur
                except Exception:
                    pass
        except Exception:
            pass
    # 回退到 MoviePy
    info = get_video_info(video_path)
    dur = info.get('duration')
    if isinstance(dur, (int, float)) and dur > 0:
        return float(dur)
    return None


def probe_video_codec_ffprobe(video_path: Path) -> Optional[str]:
    """使用 ffprobe 获取首个视频流的编码器名（如 'h264', 'hevc', 'vp9'）。
    返回字符串或 None。
    """
    ffprobe_bin = shutil.which('ffprobe')
    if not ffprobe_bin:
        return None
    try:
        cmd = [
            ffprobe_bin,
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_name',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(video_path)
        ]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode == 0:
            try:
                text = (res.stdout or b'').decode('utf-8', errors='ignore').strip()
            except Exception:
                try:
                    text = (res.stdout or b'').decode('mbcs', errors='ignore').strip()
                except Exception:
                    text = ''
            return text or None
    except Exception:
        return None
    return None


def _is_relative_to(path: Path, base: Path) -> bool:
    """兼容旧版Python：判断 path 是否在 base 之内。"""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except Exception:
        return False


def resolve_input_root(video_path: Path, input_roots: List[Path]) -> Optional[Path]:
    """从多个输入根目录中找到包含该视频的根目录。找不到则返回 None。"""
    for root in input_roots:
        if _is_relative_to(video_path, root):
            return root
    return None


def get_ts_cache_dir(root_dir: Path) -> Path:
    """获取某个输入根目录对应的 TS 缓存目录：<root>_temp/video_ts"""
    return root_dir.parent / f"{root_dir.name}_temp" / "video_ts"


def get_ts_output_path(video_path: Path, input_roots: List[Path]) -> Path:
    """为视频生成对应的 TS 输出路径，目录结构镜像保留相对路径，扩展名改为 .ts。
    例如：<root>/<subdir>/a.mp4 -> <root>_temp/video_ts/<subdir>/a.ts
    如果找不到所属根目录，则退回到视频同级的 <parent>_temp/video_ts/a.ts。
    """
    root = resolve_input_root(video_path, input_roots)
    if root is None:
        # 退回方案：使用视频所在目录旁的 _temp/video_ts
        fallback_dir = video_path.parent.parent / f"{video_path.parent.name}_temp" / "video_ts"
        return fallback_dir / (video_path.stem + '.ts')
    rel = video_path.resolve().relative_to(root.resolve())
    ts_dir = get_ts_cache_dir(root) / rel.parent
    return ts_dir / (video_path.stem + '.ts')


def ensure_ts_segments(sources: List[Path], input_roots: List[Path], trim_tail_seconds: float) -> List[Path]:
    """将源视频列表映射为可用的 TS 片段路径列表。
    - 若目标 TS 缺失或为空，则即时进行无重编码转换，并在转换时裁剪尾部 `trim_tail_seconds`。
    - 返回成功生成的 TS 路径列表；失败或过短的条目会被跳过。
    """
    ts_list: List[Path] = []
    for src in sources:
        ts_path = get_ts_output_path(src, input_roots)
        try:
            if not ts_path.exists() or ts_path.stat().st_size == 0:
                ok = convert_video_to_ts(src, ts_path, trim_tail_seconds=trim_tail_seconds)
                if not ok:
                    print(f"⏭️ TS不可用，跳过片段: {src.name}")
                    continue
            ts_list.append(ts_path)
        except Exception as e:
            print(f"⚠️ TS检查/生成异常，跳过: {src.name} -> {e}")
    return ts_list


def convert_video_to_ts(input_video: Path, output_ts: Path, *, trim_tail_seconds: float = 1.0) -> bool:
    """将单个视频无重编码地转换为 MPEG-TS 容器，避免拼接卡顿。
    - 默认使用 `-c copy`，根据源编码选择对应的 bitstream filter：
      h264 -> h264_mp4toannexb，hevc -> hevc_mp4toannexb，其它省略 bsf。
    - 不存在父目录时自动创建。
    - 支持在转换阶段直接裁剪尾部时长（`trim_tail_seconds`），减少后续拼接卡顿。
    返回 True/False 表示成功与否。
    """
    try:
        ffmpeg_bin = shutil.which('ffmpeg')
        if not ffmpeg_bin:
            print("❌ 未找到 ffmpeg，请确保已安装并配置到 PATH")
            return False

        # 已存在且非空则跳过
        try:
            if output_ts.exists() and output_ts.stat().st_size > 0:
                return True
        except Exception:
            pass

        output_ts.parent.mkdir(parents=True, exist_ok=True)

        codec = probe_video_codec_ffprobe(input_video) or ''
        # 计算裁剪后的时长（如配置了尾部裁剪）
        out_duration = None
        try:
            if trim_tail_seconds and float(trim_tail_seconds) > 0:
                dur = probe_duration_ffprobe(input_video)
                if dur is not None:
                    out_duration = max(0.0, dur - float(trim_tail_seconds))
                    if out_duration <= 0.05:
                        print(f"⏭️ 片段过短，跳过 TS 转换: {input_video.name} (时长 {dur:.2f}s, 裁剪 {trim_tail_seconds}s)")
                        return False
        except Exception:
            # 若获取时长失败，则继续无裁剪转换
            out_duration = None

        cmd = [ffmpeg_bin, '-y', '-i', str(input_video), '-c', 'copy']
        if codec.lower() == 'h264':
            cmd += ['-bsf:v', 'h264_mp4toannexb']
        elif codec.lower() == 'hevc':
            cmd += ['-bsf:v', 'hevc_mp4toannexb']
        else:
            # 非 H.264/HEVC 源，省略 bsf，仍使用 mpegts 容器
            pass
        # 尾部裁剪：使用 -t 限制输出时长（流复制，关键帧对齐）
        if out_duration is not None:
            cmd += ['-t', f'{out_duration:.3f}']
        cmd += ['-f', 'mpegts', str(output_ts)]

        res = subprocess.run(cmd, capture_output=True)
        if res.returncode == 0:
            return True
        else:
            stderr_text = ''
            try:
                stderr_text = (res.stderr or b'').decode('utf-8', errors='ignore')
            except Exception:
                try:
                    stderr_text = (res.stderr or b'').decode('mbcs', errors='ignore')
                except Exception:
                    stderr_text = ''
            print(f"⚠️ TS转换失败: {input_video.name} -> {output_ts.name}\n{stderr_text[-600:]}")
            return False
    except Exception as e:
        print(f"❌ TS转换异常: {e}")
        return False


def convert_all_to_ts(videos: List[Path], input_roots: List[Path], threads: int, *, trim_tail_seconds: float = 1.0) -> None:
    """并发将输入目录中的所有视频转换为 TS 并写入各自根目录的 _temp/video_ts。
    - 线程数复用 `threads` 参数。
    - 已有且非空的 TS 文件会跳过。
    """
    print("🚧 正在预转换视频为 TS 以优化拼接…")
    total = len(videos)
    succeeded = 0
    failed = 0
    try:
        with ThreadPoolExecutor(max_workers=max(1, threads)) as executor:
            futures = {}
            for v in videos:
                out_ts = get_ts_output_path(v, input_roots)
                fut = executor.submit(convert_video_to_ts, v, out_ts, trim_tail_seconds=trim_tail_seconds)
                futures[fut] = (v, out_ts)
            for fut in as_completed(futures):
                v, out_ts = futures[fut]
                try:
                    ok = fut.result()
                    if ok:
                        succeeded += 1
                    else:
                        failed += 1
                except Exception as e:
                    print(f"❌ TS转换任务异常: {v.name} -> {e}")
                    failed += 1
    except KeyboardInterrupt:
        print("⚠️ 用户中断，停止 TS 预转换…")
        raise
    print(f"📦 TS预转换完成：✅ {succeeded}/{total} 成功，❌ {failed} 失败")


def group_videos_by_resolution(videos: List[Path]) -> dict:
    """按分辨率分组视频，返回 dict: {(width, height): [Path, ...]}"""
    groups = {}
    for v in videos:
        res = probe_resolution_ffprobe(v)
        if not res:
            print(f"⚠️ 跳过无法获取分辨率的视频: {v.name}")
            continue
        groups.setdefault(res, []).append(v)
    return groups


def allocate_outputs_by_group_size(groups: dict, total_outputs: int) -> List[tuple]:
    """按分组视频数量比例分配输出数量，总和等于 total_outputs。
    使用配额法：先分配 floor(share)，再将剩余输出分配给小数部分最大的分组。
    返回列表 [(group_key, outputs_for_group), ...]
    """
    if total_outputs <= 0 or not groups:
        return []
    items = list(groups.items())
    total_videos = sum(len(vs) for _, vs in items)
    if total_videos == 0:
        return []
    # 初始分配
    base = {}
    remainders = []
    for k, vs in items:
        share = total_outputs * (len(vs) / total_videos)
        base_share = int(share)
        base[k] = base_share
        remainders.append((share - base_share, k))
    assigned = sum(base.values())
    remaining = total_outputs - assigned
    # 分配剩余给小数部分最大的分组
    remainders.sort(reverse=True)
    for i in range(remaining):
        _, k = remainders[i]
        base[k] += 1
    # 转为列表并过滤为正数的分配
    result = [(k, n) for k, n in base.items() if n > 0]
    # 按分辨率排序（高到低）以稳定输出顺序
    result.sort(key=lambda kv: (kv[0][1], kv[0][0]), reverse=False)
    return result


def process_group_single_output(args_tuple):
    """处理分辨率分组的单个输出任务：
    - 从组内随机选择 count 个视频（不足时允许重复选择）
    - 按组分辨率拼接并替换 BGM
    - 输出文件名追加分辨率与序号后缀
    返回 (success, msg)
    """
    (group_key, group_videos, out_index, bgm_input_path, temp_dir, output_spec,
     default_output_dir, args_count, args_gpu, target_fps, args_nvenc_cq, args_bitrate_mbps, args_x264_crf, args_trim_tail, input_roots) = args_tuple
    try:
        w, h = group_key
        auto_seed = generate_auto_seed()
        random.seed(auto_seed)

        # 选择 count 个视频：优先无重复，数量不足则允许重复
        if len(group_videos) >= args_count:
            selected = random.sample(group_videos, args_count)
        else:
            selected = random.choices(group_videos, k=args_count)

        print(f"🔄 [组 {w}x{h}] 输出{out_index} 选择了 {len(selected)} 个视频片段…")

        # 将所选视频映射为 TS 文件路径；若不存在则尝试即时转换（统一辅助函数）
        selected_ts = ensure_ts_segments(selected, input_roots, args_trim_tail)
        if not selected_ts:
            return False, f"组 {w}x{h} 输出{out_index} 无可用TS片段"

        # 输出路径与临时文件
        if output_spec:
            out_spec = Path(output_spec)
            if out_spec.suffix.lower() == '.mp4':
                out_dir = out_spec.parent
                out_name = f"{out_spec.stem}_{w}x{h}_{out_index}_{auto_seed}_{out_spec.suffix}"
            else:
                out_dir = out_spec
                out_name = f"concat_{args_count}videos_{w}x{h}_{out_index}_{auto_seed}.mp4"
        else:
            out_dir = default_output_dir
            out_name = f"concat_{args_count}videos_{w}x{h}_{out_index}_{auto_seed}.mp4"
        out_dir.mkdir(parents=True, exist_ok=True)

        temp_concat_output = temp_dir / f"temp_concat_{w}x{h}_{out_index}_{auto_seed}.mp4"
        final_out = out_dir / out_name

        # 拼接（目标分辨率采用组分辨率，避免额外缩放）
        ok = concat_videos(
            selected_ts,
            temp_concat_output,
            use_gpu=args_gpu,
            temp_dir=temp_dir,
            target_width=w,
            target_height=h,
            target_fps=target_fps,
            fill_mode='pad',
            nvenc_cq=args_nvenc_cq,
            bitrate_mbps=args_bitrate_mbps,
            x264_crf=args_x264_crf,
            trim_tail_seconds=args_trim_tail,
        )
        if not ok:
            return False, f"组 {w}x{h} 输出{out_index} 拼接失败"

        # 选择 BGM 并合成
        try:
            bgm_path = select_bgm_file(bgm_input_path, auto_seed)
        except ValueError as e:
            return False, f"组 {w}x{h} 输出{out_index} BGM选择错误: {e}"

        ok2 = replace_audio_with_bgm(temp_concat_output, bgm_path, final_out, use_gpu=args_gpu)
        if not ok2:
            return False, f"组 {w}x{h} 输出{out_index} BGM替换失败"

        size_mb = final_out.stat().st_size / (1024*1024)
        return True, f"{final_out} ({size_mb:.1f} MB)"
    except Exception as e:
        return False, f"异常: {e}"


def is_nvenc_available() -> bool:
    """检测本机 ffmpeg 是否支持 h264_nvenc（NVIDIA 编码器）"""
    ffmpeg_bin = shutil.which('ffmpeg')
    if not ffmpeg_bin:
        return False
    try:
        res = subprocess.run([ffmpeg_bin, '-hide_banner', '-encoders'], capture_output=True)
        if res.returncode != 0:
            return False
        # 尝试安全解码（避免不同本地编码导致的异常）
        stdout = ''
        try:
            stdout = res.stdout.decode('utf-8', errors='ignore')
        except Exception:
            try:
                stdout = res.stdout.decode('mbcs', errors='ignore')
            except Exception:
                stdout = ''
        return 'h264_nvenc' in stdout
    except Exception:
        return False


def select_random_videos(videos: List[Path], count: int, seed: Optional[int] = None) -> List[Path]:
    """随机选择指定数量的视频"""
    if seed is not None:
        random.seed(seed)
    
    if count >= len(videos):
        return videos.copy()
    
    return random.sample(videos, count)


def select_bgm_file(bgm_path: Path, seed: Optional[int] = None) -> Path:
    """选择BGM文件：如果是文件则直接返回，如果是目录则随机选择一个音频文件"""
    if bgm_path.is_file():
        # 验证文件格式
        if bgm_path.suffix.lower() not in SUPPORTED_AUDIO_EXTS:
            raise ValueError(f"不支持的BGM格式: {bgm_path.suffix}")
        return bgm_path
    
    elif bgm_path.is_dir():
        # 查找目录中的音频文件
        audio_files = find_audio_files(bgm_path)
        if not audio_files:
            raise ValueError(f"BGM目录中未找到任何支持的音频文件: {bgm_path}")
        
        # 随机选择一个音频文件
        if seed is not None:
            random.seed(seed)
        selected_bgm = random.choice(audio_files)
        print(f"🎵 从BGM目录随机选择: {selected_bgm.name}")
        return selected_bgm

    else:
        raise ValueError(f"BGM路径不存在: {bgm_path}")


def write_concat_list_file(videos: List[Path], list_file: Path) -> int:
    """写入 concat demuxer 所需的列表文件，返回写入的条目数。
    拼接阶段不再进行逐段裁剪，直接写入 `file '<path>'` 行。
    """
    lines = []
    for v in videos:
        p = str(v)
        p_escaped = p.replace("'", r"'\''")
        lines.append(f"file '{p_escaped}'\n")
    with open(list_file, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    return len(lines)


def concat_videos(
    videos: List[Path],
    output_path: Path,
    use_gpu: bool = False,
    temp_dir: Optional[Path] = None,
    target_width: int = 1920,
    target_height: int = 1080,
    target_fps: int = 24,
    fill_mode: str = 'pad',  # 'pad' 或 'crop'
    nvenc_cq: int = 24,
    bitrate_mbps: int = 6,
    x264_crf: int = 22,
    trim_tail_seconds: float = 1.0,
) -> bool:
    """使用FFmpeg concat demuxer拼接视频（无音频），支持NVENC加速编码。
    - 生成文件列表并通过 `-f concat -safe 0` 拼接。
    - 统一输出为指定分辨率/帧率/像素格式（可配置）。
    - 输出不包含音轨（-an），以便后续替换BGM时复制视频流避免重编码。
    - 支持压缩参数：NVENC 使用 `cq` 与目标码率，x264 使用 `crf`。
    - 支持直接传入已预转换的 TS 片段列表（推荐），以减少拼接卡顿风险。
    - 尾部裁剪仅在 TS 转换阶段进行；拼接阶段不再对列表逐段裁剪。
    """
    try:
        print("🔗 使用FFmpeg进行视频拼接（TS预合并 → 编码）…")

        if not videos:
            print("❌ 没有可用的视频片段")
            return False

        ffmpeg_bin = shutil.which('ffmpeg')
        if not ffmpeg_bin:
            print("❌ 未找到 ffmpeg，请确保已安装并配置到 PATH")
            return False

        # 创建临时文件列表
        ts_suffix = int(time.time() * 1000)
        # 随机数种子，确保每次运行时生成不同的文件名
        random.seed(ts_suffix)
        # 随机数，确保每次运行时生成不同的文件名
        random_suffix = random.randint(10000, 999999)
        
        list_file = (temp_dir or output_path.parent) / f"temp_video_list_{ts_suffix}_{random_suffix}.txt"

        try:
            count = write_concat_list_file(videos, list_file)
            if count <= 0:
                print("❌ 没有可用的片段用于拼接")
                return False
        except Exception as e:
            print(f"❌ 无法写入拼接列表文件: {e}")
            return False

        # 检测编码器
        nvenc_ok = use_gpu and is_nvenc_available()
        if nvenc_ok:
            print("🟢 检测到 NVENC，使用 h264_nvenc")
        else:
            if use_gpu:
                print("⚠️ 未检测到 h264_nvenc，回退到 libx264")

        # 第一步：将 TS 片段使用 concat demuxer 合并为一个临时 TS（不重编码）
        merged_ts = (temp_dir or output_path.parent) / f"merged_temp_{ts_suffix}_{random_suffix}.ts"
        copy_merge_cmd = [
            ffmpeg_bin, '-y',
            '-f', 'concat', '-safe', '0',
            '-i', str(list_file),
            '-c', 'copy',
            '-bsf:a', 'aac_adtstoasc',
            str(merged_ts)
        ]

        print(f"🔧 预合并TS命令: {' '.join(copy_merge_cmd)}")
        res_merge = subprocess.run(copy_merge_cmd, capture_output=True)
        if res_merge.returncode != 0:
            print("❌ TS预合并失败")
            stderr_text = ''
            try:
                stderr_text = (res_merge.stderr or b'').decode('utf-8', errors='ignore')
            except Exception:
                try:
                    stderr_text = (res_merge.stderr or b'').decode('mbcs', errors='ignore')
                except Exception:
                    stderr_text = ''
            print(stderr_text[-1000:])
            return False

        # 构建 FFmpeg 编码命令（统一输出规格，可配置）
        if fill_mode == 'crop':
            # 等比放大填满，超出部分裁剪，使用高质量 Lanczos 缩放以降低锯齿
            filter_vf = (
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={target_width}:{target_height},"
                f"fps={target_fps},format=yuv420p"
            )
        else:
            # 默认：保持比例缩放，居中黑边填充，使用高质量 Lanczos 缩放以降低锯齿
            filter_vf = (
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={target_fps},format=yuv420p"
            )
        cmd = [
            ffmpeg_bin, '-y',
            '-i', str(merged_ts),
            '-avoid_negative_ts', 'make_zero',
            '-fps_mode', 'cfr',
            # 提升缩放质量（全局 sws flags，部分播放器/构建更稳定）
            '-sws_flags', 'lanczos+accurate_rnd+full_chroma_int',
        ]

        if nvenc_ok:
            cmd += [
                '-c:v', 'h264_nvenc',
                '-preset', 'p4',
                '-tune', 'hq',
                '-rc', 'vbr',
                # 压缩参数（默认更小体积且保持观感）
                '-cq', str(nvenc_cq),
                '-b:v', f"{bitrate_mbps}M",
                '-maxrate', f"{int(bitrate_mbps*1.5)}M",
                '-bufsize', f"{int(bitrate_mbps*2)}M",
                '-profile:v', 'high',
                '-level', '4.1',
                '-pix_fmt', 'yuv420p',
                '-vf', filter_vf,
                '-gpu', '0',
                '-r', str(target_fps),
                '-movflags', '+faststart',
                '-spatial_aq', '1',
                '-temporal_aq', '1',
                '-rc-lookahead', '20',
                '-surfaces', '64',
                '-an',
            ]
        else:
            cmd += [
                '-c:v', 'libx264',
                # 提升质量：更慢预设与更低 CRF
                '-preset', 'slow',
                '-crf', str(x264_crf),
                '-tune', 'film',
                '-profile:v', 'high',
                '-level', '4.1',
                '-pix_fmt', 'yuv420p',
                '-vf', filter_vf,
                '-r', str(target_fps),
                '-movflags', '+faststart',
                '-an',
            ]

        cmd += [str(output_path)]

        print(f"🔧 编码命令: {' '.join(cmd)}")
        
        # 执行 FFmpeg
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0:
            print(f"✅ 视频拼接成功: {output_path.name}")
            return True
        else:
            print("❌ 视频拼接失败")
            # 输出部分错误信息帮助定位问题
            stderr_text = ''
            try:
                stderr_text = (result.stderr or b'').decode('utf-8', errors='ignore')
            except Exception:
                try:
                    stderr_text = (result.stderr or b'').decode('mbcs', errors='ignore')
                except Exception:
                    stderr_text = ''
            print(stderr_text[-1000:])
            return False

    except Exception as e:
        print(f"❌ 拼接过程异常: {e}")
        return False
    finally:
        # 清理临时列表文件
        try:
            if 'list_file' in locals() and Path(list_file).exists():
                Path(list_file).unlink(missing_ok=True)
        except Exception:
            pass
        # 清理临时合并的TS文件
        try:
            if 'merged_ts' in locals() and Path(merged_ts).exists():
                Path(merged_ts).unlink(missing_ok=True)
        except Exception:
            pass


def replace_audio_with_bgm(video_path: Path, bgm_path: Path, output_path: Path, use_gpu: bool = False) -> bool:
    """使用FFmpeg替换视频音频为BGM：视频流copy，音频AAC，支持循环/截断"""
    try:
        print("🎵 使用FFmpeg合成BGM…")
        ffmpeg_bin = shutil.which('ffmpeg')
        if not ffmpeg_bin:
            print("❌ 未找到 ffmpeg，请确保已安装并配置到 PATH")
            return False

        cmd = [
            ffmpeg_bin, '-y',
            '-fflags', '+genpts',
            '-i', str(video_path),
            '-stream_loop', '-1',
            '-i', str(bgm_path),
            '-map', '0:v:0',
            '-map', '1:a:0',
            '-c:v', 'copy',
            '-c:a', 'aac', '-b:a', '192k',
            '-shortest',
            str(output_path)
        ]

        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0:
            print(f"✅ BGM替换成功: {output_path.name}")
            return True
        else:
            stderr_text = ''
            try:
                stderr_text = (result.stderr or b'').decode('utf-8', errors='ignore')
            except Exception:
                try:
                    stderr_text = (result.stderr or b'').decode('mbcs', errors='ignore')
                except Exception:
                    stderr_text = ''
            print(f"❌ BGM替换失败: {stderr_text[-1000:]}")
            return False

    except Exception as e:
        print(f"❌ BGM替换异常: {e}")
        return False


def process_single_output(args_tuple):
    """处理单个输出的函数，用于并发执行"""
    (idx, all_videos, bgm_input_path, temp_dir, output_spec, default_output_dir, 
     args_count, args_gpu, total_outputs, target_width, target_height, target_fps, fill_mode,
     args_nvenc_cq, args_bitrate_mbps, args_x264_crf, args_trim_tail, input_roots) = args_tuple
    
    try:
        print(f"\n=== 开始第 {idx}/{total_outputs} 个输出 ===")
        
        # 自动生成随机种子
        auto_seed = generate_auto_seed()
        print(f"🎲 [输出{idx}] 使用随机种子: {auto_seed}")
        
        # 随机选择视频
        selected_videos = select_random_videos(all_videos, args_count, auto_seed)
        print(f"🎲 [输出{idx}] 随机选择了 {len(selected_videos)} 个视频:")
        for i, video in enumerate(selected_videos, 1):
            print(f"  {i}. {video.name}")

        # 映射为 TS 文件；如缺失则即时转换（统一辅助函数）
        selected_ts = ensure_ts_segments(selected_videos, input_roots, args_trim_tail)
        if not selected_ts:
            return False, idx, "无可用TS片段"
        
        # 选择BGM文件
        try:
            bgm_path = select_bgm_file(bgm_input_path, auto_seed)
            print(f"🎵 [输出{idx}] 使用BGM: {bgm_path.name}")
        except ValueError as e:
            print(f"❌ [输出{idx}] BGM选择错误: {e}")
            return False, idx, f"BGM选择错误: {e}"
        
        # 临时拼接文件（带序号避免覆盖），增加随机数以避免冲突
        temp_concat_output = temp_dir / f"temp_concat_{idx}_{auto_seed}.mp4"

        # 拼接视频
        print(f"🔄 [输出{idx}] 开始拼接视频...")
        if not concat_videos(
            selected_ts,
            temp_concat_output,
            use_gpu=args_gpu,
            temp_dir=temp_dir,
            target_width=target_width,
            target_height=target_height,
            target_fps=target_fps,
            fill_mode=fill_mode,
            nvenc_cq=args_nvenc_cq,
            bitrate_mbps=args_bitrate_mbps,
            x264_crf=args_x264_crf,
            trim_tail_seconds=args_trim_tail,
        ):
            return False, idx, "视频拼接失败"
        
        # 计算输出路径
        if output_spec:
            if output_spec.suffix.lower() == '.mp4':
                # 文件路径：多个输出时在文件名后加序号
                out_dir = output_spec.parent
                out_name = f"{output_spec.stem}_{idx}_{auto_seed}_{output_spec.suffix}"
            else:
                # 目录路径：使用默认文件名模板
                out_dir = output_spec
                out_name = f"concat_{args_count}videos_with_bgm_{idx}_{auto_seed}.mp4"
        else:
            out_dir = default_output_dir
            out_name = f"concat_{args_count}videos_with_bgm_{idx}_{auto_seed}.mp4"
        
        out_path = out_dir / out_name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 替换BGM（循环或截断到视频长度）
        print(f"🎵 [输出{idx}] 开始合成BGM...")
        if not replace_audio_with_bgm(temp_concat_output, bgm_path, out_path, use_gpu=args_gpu):
            return False, idx, "BGM替换失败"
        
        file_size = out_path.stat().st_size / (1024*1024)
        print(f"🎉 [输出{idx}] 完成！文件: {out_path} ({file_size:.1f} MB)")
        
        return True, idx, str(out_path)
        
    except Exception as e:
        return False, idx, f"处理失败: {str(e)}"


def main():
    parser = argparse.ArgumentParser(description='视频拼接工具 - 随机选择视频拼接并替换BGM')
    parser.add_argument('video_dirs', nargs='+', help='视频目录路径（可多个）')
    parser.add_argument('bgm_path', help='BGM音频文件路径或音频目录路径（目录时随机选择）')
    parser.add_argument('-n', '--count', type=int, default=5, help='每个输出随机选择的视频数量（默认5个）')
    parser.add_argument('-m', '--outputs', type=int, default=1, help='生成的随机拼接视频数量（默认1个）')
    parser.add_argument('-o', '--output', help='输出文件路径或目录（多目录输入时必须为目录；默认在第一个目录同级创建<name>_longvideo_combined）')
    # 默认启用 GPU，加 --no-gpu 可关闭
    parser.add_argument('--gpu', dest='gpu', action='store_true', default=True,
                        help='默认启用GPU加速（需ffmpeg支持h264_nvenc），使用 --no-gpu 关闭')
    parser.add_argument('--no-gpu', dest='gpu', action='store_false', help='关闭GPU加速')
    parser.add_argument('--threads', type=int, default=4, help='并发处理线程数（默认4，建议不超过CPU核心数）')
    parser.add_argument('--width', type=int, default=1080, help='输出视频宽度（默认1080）')
    parser.add_argument('--height', type=int, default=1920, help='输出视频高度（默认1920）')
    parser.add_argument('--fps', type=int, default=25, help='输出帧率（默认25）')
    parser.add_argument('--trim-tail', type=float, default=3.0, help='在转换为TS时裁剪每段视频结尾N秒（默认3.0秒）；拼接阶段不再逐段裁剪')
    parser.add_argument('--fill', choices=['pad', 'crop'], default='pad', help='填充模式：pad(居中黑边) 或 crop(裁剪满屏)，默认pad')
    # 默认启用分辨率分组，使用 --no-group-res 可关闭
    parser.add_argument('--group-res', dest='group_res', action='store_true', default=True,
                        help='默认按分辨率分组拼接并输出（文件名追加分辨率后缀），使用 --no-group-res 关闭')
    parser.add_argument('--no-group-res', dest='group_res', action='store_false', help='关闭分辨率分组模式')
    # 压缩参数：在不影响观感的前提下减小体积
    parser.add_argument('--nvenc-cq', type=int, default=28, help='NVENC质量参数cq（默认28，值越大体积越小）')
    parser.add_argument('--crf', type=int, default=26, help='x264 CRF（默认26，值越大体积越小）')
    parser.add_argument('--bitrate', type=int, default=5, help='NVENC目标码率，单位Mbps（默认5）')
    
    args = parser.parse_args()
    
    # 验证输入路径（支持多个视频目录）
    video_dirs = [Path(p) for p in args.video_dirs]
    bgm_input_path = Path(args.bgm_path)
    
    for d in video_dirs:
        if not d.exists() or not d.is_dir():
            print(f"❌ 错误：视频目录不存在或不是目录: {d}")
            sys.exit(1)
    
    if not bgm_input_path.exists():
        print(f"❌ 错误：BGM路径不存在: {bgm_input_path}")
        sys.exit(1)
    
    # 验证线程数
    if args.threads < 1:
        print(f"❌ 错误：线程数必须大于0")
        sys.exit(1)
    # 验证输出规格
    if args.width <= 0 or args.height <= 0:
        print("❌ 错误：width/height 必须为正整数")
        sys.exit(1)
    if args.fps <= 0:
        print("❌ 错误：fps 必须为正整数")
        sys.exit(1)
    
    # 设置输出路径规范（支持多目录聚合）：
    # - 如果提供的是文件路径且为多目录输入，则报错；
    # - 如果提供的是目录或未提供，则使用默认目录和文件名模板。
    output_spec = Path(args.output) if args.output else None
    if output_spec and output_spec.suffix.lower() == '.mp4' and len(video_dirs) > 1:
        print("❌ 错误：多目录输入时请提供输出目录（不支持单文件路径）")
        sys.exit(1)

    # 计算默认输出目录
    if len(video_dirs) == 1:
        default_output_dir = video_dirs[0].parent / f"{video_dirs[0].name}_longvideo"
    else:
        base_parent = video_dirs[0].parent
        default_output_dir = base_parent / f"{video_dirs[0].name}_longvideo_combined"
    
    try:
        print("📁 扫描视频目录:")
        for d in video_dirs:
            print(f"  - {d}")
        
        # 查找所有视频文件（跨多个目录聚合）
        all_videos: List[Path] = []
        for d in video_dirs:
            all_videos.extend(find_videos(d))
        if not all_videos:
            print("❌ 错误：在输入目录中未找到任何支持的视频文件")
            sys.exit(1)
        
        print(f"📹 合计找到 {len(all_videos)} 个视频文件")
        
        # 预转换：将所有输入视频转换为 TS，提升后续拼接稳定性
        try:
            convert_all_to_ts(all_videos, video_dirs, args.threads, trim_tail_seconds=args.trim_tail)
        except KeyboardInterrupt:
            sys.exit(1)
        
        # 创建临时目录：
        # 单目录：<dir>_temp；多目录：<first>_temp_combined
        if len(video_dirs) == 1:
            temp_dir = video_dirs[0].parent / f"{video_dirs[0].name}_temp"
        else:
            temp_dir = video_dirs[0].parent / f"{video_dirs[0].name}_temp_combined"
        temp_dir.mkdir(parents=True, exist_ok=True)
        print(f"📁 临时目录: {temp_dir}")
        
        # 决定是否使用分辨率分组模式
        if args.group_res:
            print("📐 开启分辨率分组模式：将按分辨率分别拼接输出")
            groups = group_videos_by_resolution(all_videos)
            if not groups:
                print("❌ 错误：无法按分辨率分组（可能没有有效视频）")
                sys.exit(1)

            # 仅保留视频数量 > 20 的分组
            qualified_groups = {k: v for k, v in groups.items() if len(v) > 20}

            print("📊 分组结果（仅保留 >20 个视频的分组）：")
            for (w, h), vids in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0], -len(kv[1]))):
                mark = "✅" if (w, h) in qualified_groups else "⏭️"
                print(f"  - {w}x{h}: {len(vids)} 个视频 {mark}")

            if not qualified_groups:
                print("❌ 错误：没有分辨率分组达到 >20 个视频，结束。")
                sys.exit(1)

            # 按分组视频数量比例分配总输出数量
            allocation = allocate_outputs_by_group_size(qualified_groups, args.outputs)
            total_tasks = sum(n for _, n in allocation)
            print("📦 分配结果（组分辨率 -> 输出数量）：")
            for (w, h), n in allocation:
                print(f"  - {w}x{h} -> {n}")
            if total_tasks == 0:
                print("❌ 错误：总输出数量为 0，结束。")
                sys.exit(1)

            # 并发执行所有任务（跨分组）
            max_workers = min(args.threads, total_tasks)
            print(f"🚀 并发任务数: {max_workers}，总任务: {total_tasks}")

            results = []
            failed = 0
            try:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {}
                    for (key, count_out) in allocation:
                        vids = qualified_groups[key]
                        for i in range(1, count_out + 1):
                            task_args = (key, vids, i, bgm_input_path, temp_dir, output_spec,
                                         default_output_dir, args.count, args.gpu, args.fps,
                                         args.nvenc_cq, args.bitrate, args.crf, args.trim_tail, video_dirs)
                            fut = executor.submit(process_group_single_output, task_args)
                            futures[fut] = (key, i)
                    for fut in as_completed(futures):
                        key, i = futures[fut]
                        w, h = key
                        try:
                            ok, msg = fut.result()
                            if ok:
                                print(f"✅ [组 {w}x{h}] 输出{i} 完成: {msg}")
                                results.append(msg)
                            else:
                                print(f"❌ [组 {w}x{h}] 输出{i} 失败: {msg}")
                                failed += 1
                        except Exception as e:
                            print(f"❌ [组 {w}x{h}] 输出{i} 异常: {e}")
                            failed += 1
            except KeyboardInterrupt:
                print("⚠️ 用户中断，停止分组处理…")
                sys.exit(1)

            print("\n📊 分组模式完成")
            print(f"✅ 成功: {len(results)} 个输出, ❌ 失败: {failed} 个输出")
            if results:
                print("🎉 输出文件：")
                for r in results:
                    print(f"  - {r}")
            return

        # 决定是否使用并发处理（随机拼接模式）
        use_concurrent = args.outputs > 1 and args.threads > 1
        
        if use_concurrent:
            # 限制线程数不超过输出数量
            max_workers = min(args.threads, args.outputs)
            print(f"🚀 启用并发处理，使用 {max_workers} 个线程")
            
            # 准备任务参数
            tasks = []
            for idx in range(1, args.outputs + 1):
                task_args = (
                     idx, all_videos, bgm_input_path, temp_dir, output_spec,
                     default_output_dir, args.count, args.gpu, args.outputs,
                     args.width, args.height, args.fps, args.fill,
                     args.nvenc_cq, args.bitrate, args.crf, args.trim_tail, video_dirs,
                 )
                tasks.append(task_args)
            
            # 并发执行
            results = []
            failed_count = 0
            
            try:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # 提交所有任务
                    future_to_idx = {executor.submit(process_single_output, task): task[0] for task in tasks}
                    
                    # 收集结果
                    for future in as_completed(future_to_idx):
                        idx = future_to_idx[future]
                        try:
                            success, result_idx, message = future.result()
                            if success:
                                results.append((result_idx, message))
                                print(f"✅ 任务 {result_idx} 完成")
                            else:
                                failed_count += 1
                                print(f"❌ 任务 {result_idx} 失败: {message}")
                        except Exception as e:
                            failed_count += 1
                            print(f"❌ 任务 {idx} 异常: {e}")
                
                # 输出汇总结果
                print(f"\n📊 并发处理完成:")
                print(f"✅ 成功: {len(results)} 个")
                print(f"❌ 失败: {failed_count} 个")
                
                if results:
                    print(f"\n🎉 成功生成的文件:")
                    for idx, file_path in sorted(results):
                        file_size = Path(file_path).stat().st_size / (1024*1024)
                        print(f"  {idx}. {file_path} ({file_size:.1f} MB)")
                
            except KeyboardInterrupt:
                print(f"\n⚠️ 用户中断，正在停止所有任务...")
                sys.exit(1)
                
        else:
            # 串行处理（原有逻辑）
            if args.outputs == 1:
                print("🔄 单个输出，使用串行处理")
            else:
                print("🔄 使用串行处理（threads=1 或 outputs=1）")
            
            for idx in range(1, args.outputs + 1):
                print(f"\n=== 开始第 {idx}/{args.outputs} 个输出 ===")
                
                # 自动生成随机种子
                auto_seed = generate_auto_seed()
                print(f"🎲 使用随机种子: {auto_seed}")
                
                # 随机选择视频
                selected_videos = select_random_videos(all_videos, args.count, auto_seed)
                print(f"🎲 随机选择了 {len(selected_videos)} 个视频:")
                for i, video in enumerate(selected_videos, 1):
                    print(f"  {i}. {video.name}")

                # 使用已转换的 TS 文件；如缺失则即时转换（统一辅助函数）
                selected_ts = ensure_ts_segments(selected_videos, video_dirs, args.trim_tail)
                if not selected_ts:
                    print("❌ 无可用TS片段，结束。")
                    sys.exit(1)
                
                # 选择BGM文件
                try:
                    bgm_path = select_bgm_file(bgm_input_path, auto_seed)
                    print(f"🎵 使用BGM: {bgm_path.name}")
                except ValueError as e:
                    print(f"❌ BGM选择错误: {e}")
                    sys.exit(1)
                
                # 临时拼接文件（带序号避免覆盖）
                temp_concat_output = temp_dir / f"temp_concat_{idx}.mp4"

                # 拼接视频
                if not concat_videos(
                    selected_ts, temp_concat_output,
                    use_gpu=args.gpu, temp_dir=temp_dir,
                    target_width=args.width, target_height=args.height,
                    target_fps=args.fps, fill_mode=args.fill,
                    nvenc_cq=args.nvenc_cq, bitrate_mbps=args.bitrate, x264_crf=args.crf,
                    trim_tail_seconds=args.trim_tail,
                ):
                    print("❌ 视频拼接失败")
                    sys.exit(1)
                
                # 计算输出路径
                if output_spec:
                    if output_spec.suffix.lower() == '.mp4':
                        # 文件路径：多个输出时在文件名后加序号
                        out_dir = output_spec.parent
                        out_name = f"{output_spec.stem}_{idx}{output_spec.suffix}"
                    else:
                        # 目录路径：使用默认文件名模板
                        out_dir = output_spec
                        out_name = f"concat_{args.count}videos_with_bgm_{idx}.mp4"
                else:
                    out_dir = default_output_dir
                    out_name = f"concat_{args.count}videos_with_bgm_{idx}.mp4"
                
                out_path = out_dir / out_name
                out_path.parent.mkdir(parents=True, exist_ok=True)
                
                # 替换BGM（循环或截断到视频长度）
                if not replace_audio_with_bgm(temp_concat_output, bgm_path, out_path, use_gpu=args.gpu):
                    print("❌ BGM替换失败")
                    sys.exit(1)
                
                print(f"\n🎉 第 {idx} 个输出完成！")
                print(f"📄 输出文件: {out_path}")
                print(f"📊 文件大小: {out_path.stat().st_size / (1024*1024):.1f} MB")
        
    except Exception as e:
        print(f"❌ 程序执行失败: {e}")
        sys.exit(1)

    finally:
        # 清理临时文件（无论是否提前 return 都会执行）
        try:
            if 'temp_dir' in locals() and isinstance(temp_dir, Path) and temp_dir.exists():
                shutil.rmtree(temp_dir)
                print(f"🧹 已清理临时目录: {temp_dir}")
        except Exception as e:
            print(f"⚠️  清理临时目录失败: {e}")
    
    


if __name__ == '__main__':
    main()