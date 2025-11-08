#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频拼接工具
功能：随机选择n个视频进行拼接，然后替换BGM，不进行转码压缩以提高效率
"""

import sys
import shutil
import time
from pathlib import Path
import argparse
import random
from typing import List, Optional
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

# 支持的视频格式
SUPPORTED_VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.m4v'}
SUPPORTED_AUDIO_EXTS = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'}

# 编码全局配置（由 CLI 设置），用于统一控制 GPU/CPU 的压缩/观感/速度取向
ENCODE_PROFILE: str = 'balanced'       # 可选：visual/balanced/size
ENCODE_NVENC_CQ: Optional[int] = None  # NVENC CQ 覆盖
ENCODE_X265_CRF: Optional[int] = None  # x265 CRF 覆盖
ENCODE_PRESET_GPU: Optional[str] = None  # NVENC 预设：p4/p5/p6/p7
ENCODE_PRESET_CPU: Optional[str] = None  # x265 预设：ultrafast/medium/slow/slower/veryslow

def get_ffmpeg_gpu_mapping_cpu_enc_opts() -> List[str]:
    """获取 GPU 与 CPU 编码器的通用编码参数映射关系。
    根据全局 ENCODE_* 设置（由 CLI 注入）动态生成编码参数，以满足三档需求：
    - visual：观感优先（更低 CQ/CRF、较快预设）
    - balanced：均衡（默认）
    - size：体积优先（更高 CQ/CRF、较慢预设）
    同时支持 `--nvenc-cq / --x265-crf / --preset-gpu / --preset-cpu` 精细覆盖。
    """
    # 档位默认参数
    profile = (ENCODE_PROFILE or 'balanced').lower()
    if profile not in ('visual', 'balanced', 'size'):
        profile = 'balanced'

    # 选择默认预设与质量参数
    if profile == 'visual':
        default_nvenc_cq, default_preset_gpu = 30, 'p5'
        default_x265_crf, default_preset_cpu = 28, 'medium'
    elif profile == 'size':
        default_nvenc_cq, default_preset_gpu = 34, 'p7'
        default_x265_crf, default_preset_cpu = 32, 'veryslow'
    else:  # balanced
        default_nvenc_cq, default_preset_gpu = 32, 'p6'
        default_x265_crf, default_preset_cpu = 30, 'slow'

    # 应用 CLI 覆盖（如提供）
    nvenc_cq = ENCODE_NVENC_CQ if isinstance(ENCODE_NVENC_CQ, int) else default_nvenc_cq
    x265_crf = ENCODE_X265_CRF if isinstance(ENCODE_X265_CRF, int) else default_x265_crf
    preset_gpu = ENCODE_PRESET_GPU or default_preset_gpu
    preset_cpu = ENCODE_PRESET_CPU or default_preset_cpu

    common_opts = [
        '-pix_fmt', 'yuv420p',      # 像素格式 yuv420p（兼容大多数播放器）
    ]

    common_enc_opts = {
        "gpu": [
            '-c:v', 'hevc_nvenc',
            '-preset', preset_gpu,
            '-tune', 'hq',
            '-rc', 'vbr',
            '-cq', str(nvenc_cq),
            '-b:v', '0',
            '-bf', '3',
            '-b_ref_mode', 'middle',
            '-spatial_aq', '1',
            '-temporal_aq', '1',
            '-aq-strength', '8' if profile != 'size' else '6',
            '-g', '240',
            '-rc-lookahead', '32' if profile != 'visual' else '20',
        ],
        "cpu": [
            '-c:v', 'libx265',
            '-crf', str(x265_crf),
            '-preset', preset_cpu,
            '-x265-params', 'aq-mode=2:aq-strength=1.0:psy-rd=2.0:psy-rdoq=1.0:qcomp=0.65:rc-lookahead=60:keyint=240:min-keyint=24:bframes=8:ref=5:scenecut=40:limit-sao=1',
        ],
    }
    for enc_opts in common_enc_opts.values():
        enc_opts.extend(common_opts)
    return common_enc_opts

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


def _format_trim_value(val: float) -> str:
    """格式化裁剪秒数用于文件名：整数显示为不带小数，非整数保留一位小数。"""
    try:
        v = float(val)
        if abs(v - round(v)) < 1e-9:
            return str(int(round(v)))
        return f"{v:.1f}"
    except Exception:
        return str(val)


def get_ts_output_path_with_trim(video_path: Path, input_roots: List[Path], trim_head_seconds: float, trim_tail_seconds: float) -> Path:
    """为视频生成带裁剪标识的 TS 输出路径，避免不同裁剪策略复用旧缓存。
    文件名形如：`<stem>_headX_tailY.ts`，其中 X/Y 为格式化秒数（整数无小数，非整数保留一位）。
    目录结构与 `get_ts_output_path` 一致。
    """
    root = resolve_input_root(video_path, input_roots)
    head_tag = _format_trim_value(trim_head_seconds)
    tail_tag = _format_trim_value(trim_tail_seconds)
    filename = f"{video_path.stem}_head{head_tag}_tail{tail_tag}.ts"
    if root is None:
        fallback_dir = video_path.parent.parent / f"{video_path.parent.name}_temp" / "video_ts"
        return fallback_dir / filename
    rel = video_path.resolve().relative_to(root.resolve())
    ts_dir = get_ts_cache_dir(root) / rel.parent
    return ts_dir / filename


def clear_mismatched_ts_cache(input_roots: List[Path], trim_head_seconds: float, trim_tail_seconds: float) -> int:
    """清理与当前裁剪参数不匹配的 TS 缓存文件。
    - 删除所有不以 `_head{H}_tail{T}.ts` 结尾的 TS 文件（视为旧命名或不同策略）。
    - 保留与当前参数完全匹配的缓存文件。
    返回删除的文件数量。
    """
    head_tag = _format_trim_value(trim_head_seconds)
    tail_tag = _format_trim_value(trim_tail_seconds)
    keep_suffix = f"_head{head_tag}_tail{tail_tag}.ts"
    removed = 0
    for root in input_roots:
        cache_dir = get_ts_cache_dir(root)
        if not cache_dir.exists():
            continue
        for ts_file in cache_dir.rglob('*.ts'):
            name = ts_file.name
            if not name.endswith(keep_suffix):
                try:
                    ts_file.unlink(missing_ok=True)
                    removed += 1
                except Exception as e:
                    print(f"⚠️ 删除缓存失败: {ts_file} -> {e}")
    print(f"🧹 已清理与当前裁剪参数不匹配的 TS 缓存: {removed} 个")
    return removed


def ensure_ts_segments(sources: List[Path], input_roots: List[Path], trim_head_seconds: float, trim_tail_seconds: float, use_gpu: bool) -> List[Path]:
    """将源视频列表映射为可用的 TS 片段路径列表。
    - 若目标 TS 缺失或为空，则即时进行无重编码转换，并在转换时裁剪开头 `trim_head_seconds` 与尾部 `trim_tail_seconds`。
    - 返回成功生成的 TS 路径列表；失败或过短的条目会被跳过。
    """
    ts_list: List[Path] = []
    for src in sources:
        ts_path = get_ts_output_path_with_trim(src, input_roots, trim_head_seconds, trim_tail_seconds)
        try:
            if not ts_path.exists() or ts_path.stat().st_size == 0:
                ok = convert_video_to_ts(src, ts_path, trim_head_seconds=trim_head_seconds, trim_tail_seconds=trim_tail_seconds, use_gpu=use_gpu)
                if not ok:
                    print(f"⏭️ TS不可用，跳过片段: {src.name}")
                    continue
            ts_list.append(ts_path)
        except Exception as e:
            print(f"⚠️ TS检查/生成异常，跳过: {src.name} -> {e}")
    return ts_list


def convert_video_to_ts(input_video: Path, output_ts: Path, *, trim_head_seconds: float = 0.0, trim_tail_seconds: float = 1.0, use_gpu: bool = True) -> bool:
    """将视频转换为 MPEG-TS（仅视频轨，移除音频），统一采用 HEVC（H.265）编码策略：GPU 先尝试 hevc_nvenc，失败则回退 CPU libx265。

    目标：统一“第一步 MP4→TS”编码到 H.265，并在保证观感的情况下尽可能增大压缩比例。
    - GPU 路径：`hevc_nvenc`，参数映射到 CPU `libx265 crf=28 preset=slow` 的近似质量（使用 `-preset p6 -cq 28`）。
    - CPU 回退：`libx265 -crf 28 -preset slow -pix_fmt yuv420p`。
    - 始终生成缺失的 PTS 并重置时间戳；TS 中不包含音频轨（-an）。
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

        # 计算裁剪参数与输出时长，并收集输入体积信息用于压缩对比
        out_duration = None
        orig_size_bytes = None
        est_input_bytes = None
        try:
            orig_size_bytes = input_video.stat().st_size
        except Exception:
            orig_size_bytes = None
        try:
            dur = probe_duration_ffprobe(input_video)
            head = max(0.0, float(trim_head_seconds or 0.0))
            tail = max(0.0, float(trim_tail_seconds or 0.0))
            if dur is not None:
                out_duration = max(0.0, dur - head - tail)
                if out_duration <= 0.05:
                    print(f"⏭️ 片段过短，跳过 TS 转换: {input_video.name} (时长 {dur:.2f}s, 头裁剪 {head:.2f}s, 尾裁剪 {tail:.2f}s)")
                    return False
                # 若可获取总时长与原文件大小，估算裁剪片段对应的参考体积
                try:
                    if orig_size_bytes and dur and dur > 0:
                        est_input_bytes = int(orig_size_bytes * (out_duration / dur))
                except Exception:
                    est_input_bytes = None
        except Exception:
            # 若获取时长失败，则继续无裁剪转换
            out_duration = None

        # 组装基础命令（输入、时间戳、帧率、去音轨）
        base_cmd = [ffmpeg_bin, '-y']
        try:
            if trim_head_seconds and float(trim_head_seconds) > 0:
                base_cmd += ['-ss', f'{max(0.0, float(trim_head_seconds)):.3f}']
        except Exception:
            pass
        base_cmd += [
            '-fflags', '+genpts',
            '-i', str(input_video),
            '-reset_timestamps', '1',
            '-an',
        ]

        # 编码器尝试序列：统一 HEVC（NVENC→CPU），根据 use_gpu 决定是否先试 GPU
        # 抽取通用编码选项（两种编码器均需），保持输出像素格式一致
        gpu_cpu_mapping = get_ffmpeg_gpu_mapping_cpu_enc_opts()
        encoder_attempts: list[list[str]] = []
        if use_gpu:
            encoder_attempts.append(gpu_cpu_mapping['gpu'])
        # CPU 兜底：统一使用 libx265（H.265）
        encoder_attempts.append(gpu_cpu_mapping['cpu'])

        # 逐个尝试编码器，GPU 失败自动回退到下一方案（最终 CPU）
        for idx, enc in enumerate(encoder_attempts):
            cmd = list(base_cmd) + enc
            if out_duration is not None:
                cmd += ['-t', f'{out_duration:.3f}']
            cmd += ['-f', 'mpegts', str(output_ts)]

            # 打印命令摘要便于诊断
            try:
                label = enc[1] if len(enc) > 1 else 'unknown'
                # 当 GPU→CPU 参数映射时，打印说明便于诊断对照
                if label == 'hevc_nvenc':
                    print(f"🔧 TS转换编码尝试[{idx+1}/{len(encoder_attempts)}] 使用 {label} (映射 libx265 crf=28 preset=slow): {' '.join(cmd)}")
                elif label == 'libx265':
                    print(f"🔧 TS转换编码尝试[{idx+1}/{len(encoder_attempts)}] 使用 {label}: {' '.join(cmd)}")
                else:
                    print(f"🔧 TS转换编码尝试[{idx+1}/{len(encoder_attempts)}] 使用 {label}: {' '.join(cmd)}")
            except Exception:
                pass

            res = subprocess.run(cmd, capture_output=True, encoding='utf-8')
            if res.returncode == 0:
                # 成功后打印压缩前后体积对比
                try:
                    out_size_bytes = None
                    try:
                        out_size_bytes = output_ts.stat().st_size
                    except Exception:
                        out_size_bytes = None

                    def _fmt_size(n: Optional[int]) -> str:
                        try:
                            if n is None:
                                return '未知'
                            units = ['B', 'KB', 'MB', 'GB']
                            size = float(n)
                            idx = 0
                            while size >= 1024 and idx < len(units) - 1:
                                size /= 1024.0
                                idx += 1
                            if idx <= 1:
                                return f"{size:.0f}{units[idx]}"
                            return f"{size:.2f}{units[idx]}"
                        except Exception:
                            return str(n)

                    base_input = est_input_bytes if est_input_bytes else orig_size_bytes
                    ratio = None
                    percent = None
                    try:
                        if base_input and out_size_bytes and base_input > 0:
                            ratio = out_size_bytes / base_input
                            percent = (1.0 - ratio) * 100.0
                    except Exception:
                        ratio = None
                        percent = None

                    msg_parts = [
                        f"📦 体积对比: 输入={_fmt_size(orig_size_bytes)}",
                    ]
                   
                    if est_input_bytes is not None:
                        msg_parts.append(f"估算裁剪片段={_fmt_size(est_input_bytes)}")
                    msg_parts.append(f"输出TS={_fmt_size(out_size_bytes)}")
                    if ratio is not None and percent is not None:
                        msg_parts.append(f"输出/参考输入比例={ratio:.2f}")
                        msg_parts.append(f"体积变化={percent:.1f}%")
                     # 打印原始与输出文件名，便于定位具体文件
                    try:
                        # msg_parts.insert(0, f"🎬 原始文件={input_video.name}")
                        msg_parts.append(f"🎬 原始文件={input_video.name}")
                        # msg_parts.insert(1, f"🎞️ 输出文件={output_ts.name}")
                    except Exception:
                        pass
                    print('，'.join(msg_parts))
                except Exception:
                    pass
                return True
            else:
                # 失败则打印末尾日志并继续下一尝试（兼容 encoding='utf-8' 的返回类型）
                stderr_text = ''
                try:
                    if isinstance(res.stderr, str):
                        stderr_text = res.stderr
                    else:
                        stderr_text = (res.stderr or b'').decode('utf-8', errors='ignore')
                except Exception:
                    try:
                        stderr_text = (res.stderr or b'').decode('mbcs', errors='ignore')
                    except Exception:
                        stderr_text = ''
                print(f"⚠️ TS转换失败(编码器 {enc[1]}): {input_video.name} -> {output_ts.name}\n{stderr_text[-600:]}")

        # 所有尝试均失败
        print(f"❌ TS转换失败，已尝试 GPU/CPU 编码但均未成功: {input_video.name}")
        return False
    except Exception as e:
        print(f"❌ TS转换异常: {e}")
        return False


def convert_all_to_ts(videos: List[Path], input_roots: List[Path], threads: int, *, trim_head_seconds: float = 0.0, trim_tail_seconds: float = 1.0, use_gpu: bool = True) -> None:
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
                out_ts = get_ts_output_path_with_trim(v, input_roots, trim_head_seconds, trim_tail_seconds)
                fut = executor.submit(convert_video_to_ts, v, out_ts, trim_head_seconds=trim_head_seconds, trim_tail_seconds=trim_tail_seconds, use_gpu=use_gpu)
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
     default_output_dir, args_count, args_gpu, target_fps, args_fill_mode, args_trim_head, args_trim_tail, input_roots) = args_tuple
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
        selected_ts = ensure_ts_segments(selected, input_roots, args_trim_head, args_trim_tail, args_gpu)
        if not selected_ts:
            return False, f"组 {w}x{h} 输出{out_index} 无可用TS片段"

        # 在拼接前根据时间戳种子打乱片段顺序，增强每次输出的变化性
        random.seed(auto_seed)
        random.shuffle(selected_ts)
        print(f"🔀 [组 {w}x{h}] 输出{out_index} 使用时间戳种子 {auto_seed}，已随机打乱 {len(selected_ts)} 个片段的顺序")

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
            target_width=w,
            target_height=h,
            target_fps=target_fps,
            fill_mode=args_fill_mode
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
        import traceback
        traceback.print_exc()
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

def validate_and_prepare(args: argparse.Namespace):
    """校验参数并准备关键路径对象。
    返回 (video_dirs, bgm_input_path, output_spec, default_output_dir)。
    """
    video_dirs = [Path(p) for p in args.video_dirs]
    bgm_input_path = Path(args.bgm_path)
    for d in video_dirs:
        if not d.exists() or not d.is_dir():
            print(f"❌ 错误：视频目录不存在或不是目录: {d}")
            sys.exit(1)
    if not bgm_input_path.exists():
        print(f"❌ 错误：BGM路径不存在: {bgm_input_path}")
        sys.exit(1)
    if args.threads < 1:
        print(f"❌ 错误：线程数必须大于0")
        sys.exit(1)
    if args.width <= 0 or args.height <= 0:
        print("❌ 错误：width/height 必须为正整数")
        sys.exit(1)
    if args.fps <= 0:
        print("❌ 错误：fps 必须为正整数")
        sys.exit(1)
    output_spec = Path(args.output) if args.output else None
    if output_spec and output_spec.suffix.lower() == '.mp4' and len(video_dirs) > 1:
        print("❌ 错误：多目录输入时请提供输出目录（不支持单文件路径）")
        sys.exit(1)
    if len(video_dirs) == 1:
        default_output_dir = video_dirs[0].parent / f"{video_dirs[0].name}_longvideo"
    else:
        base_parent = video_dirs[0].parent
        default_output_dir = base_parent / f"{video_dirs[0].name}_longvideo_combined"
    return video_dirs, bgm_input_path, output_spec, default_output_dir


def discover_all_videos(video_dirs: List[Path]) -> List[Path]:
    """扫描所有视频目录并聚合支持的视频文件列表。"""
    print("📁 扫描视频目录:")
    for d in video_dirs:
        print(f"  - {d}")
    all_videos: List[Path] = []
    for d in video_dirs:
        all_videos.extend(find_videos(d))
    if not all_videos:
        print("❌ 错误：在输入目录中未找到任何支持的视频文件")
        sys.exit(1)
    print(f"📹 合计找到 {len(all_videos)} 个视频文件")
    return all_videos


def create_temp_dir(video_dirs: List[Path]) -> Path:
    """创建并返回临时目录路径（单目录与多目录命名不同）。"""
    if len(video_dirs) == 1:
        temp_dir = video_dirs[0].parent / f"{video_dirs[0].name}_temp"
    else:
        temp_dir = video_dirs[0].parent / f"{video_dirs[0].name}_temp_combined"
    temp_dir.mkdir(parents=True, exist_ok=True)
    print(f"📁 临时目录: {temp_dir}")
    return temp_dir


def run_grouped_outputs(args: argparse.Namespace, all_videos: List[Path], bgm_input_path: Path,
                        temp_dir: Path, output_spec: Optional[Path], default_output_dir: Path,
                        input_roots: List[Path]) -> None:
    """执行分辨率分组模式，按分配并发生成多个输出。"""
    print("📐 开启分辨率分组模式：将按分辨率分别拼接输出")
    groups = group_videos_by_resolution(all_videos)
    if not groups:
        print("❌ 错误：无法按分辨率分组（可能没有有效视频）")
        sys.exit(1)
    qualified_groups = {k: v for k, v in groups.items() if len(v) > 20}
    print("📊 分组结果（仅保留 >20 个视频的分组）：")
    for (w, h), vids in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0], -len(kv[1]))):
        mark = "✅" if (w, h) in qualified_groups else "⏭️"
        print(f"  - {w}x{h}: {len(vids)} 个视频 {mark}")
    if not qualified_groups:
        print("❌ 错误：没有分辨率分组达到 >20 个视频，结束。")
        sys.exit(1)
    allocation = allocate_outputs_by_group_size(qualified_groups, args.outputs)
    total_tasks = sum(n for _, n in allocation)
    print("📦 分配结果（组分辨率 -> 输出数量）：")
    for (w, h), n in allocation:
        print(f"  - {w}x{h} -> {n}")
    if total_tasks == 0:
        print("❌ 错误：总输出数量为 0，结束。")
        sys.exit(1)
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
                                 default_output_dir, args.count, args.gpu, args.fps, args.fill, args.trim_head, args.trim_tail, input_roots)
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


def run_random_outputs(args: argparse.Namespace, all_videos: List[Path], bgm_input_path: Path,
                       temp_dir: Path, output_spec: Optional[Path], default_output_dir: Path,
                       input_roots: List[Path]) -> None:
    """执行随机拼接模式，统一使用线程池（max_workers 可为 1）。"""
    max_workers = max(1, min(args.threads, args.outputs))
    if max_workers > 1:
        print(f"🚀 启用并发处理，使用 {max_workers} 个线程")
    else:
        print("🔄 使用线程池顺序处理（workers=1）")

    tasks = []
    for idx in range(1, args.outputs + 1):
        task_args = (
             idx, all_videos, bgm_input_path, temp_dir, output_spec,
             default_output_dir, args.count, args.gpu, args.outputs,
             args.width, args.height, args.fps, args.fill,
             args.trim_head, args.trim_tail, input_roots,
         )
        tasks.append(task_args)

    results = []
    failed_count = 0
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {executor.submit(process_single_output, task): task[0] for task in tasks}
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
        print(f"\n📊 处理完成:")
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
    return


def preconvert_all_ts(all_videos: List[Path], input_roots: List[Path], threads: int, trim_head_seconds: float, trim_tail_seconds: float, use_gpu: bool) -> None:
    """对所有输入视频进行TS预转换，统一裁剪开头/尾部时长，提升拼接稳定性。"""
    try:
        convert_all_to_ts(all_videos, input_roots, threads, trim_head_seconds=trim_head_seconds, trim_tail_seconds=trim_tail_seconds, use_gpu=use_gpu)
    except KeyboardInterrupt:
        sys.exit(1)


def concat_videos(videos: List[Path], output_path: Path, use_gpu: bool = False, target_width: int = 1920, target_height: int = 1080, target_fps: int = 24, fill_mode: str = 'pad') -> bool:
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

        # 检测编码器
        nvenc_ok = use_gpu and is_nvenc_available()
        if nvenc_ok:
            print("🟢 检测到 NVENC，使用 hevc_nvenc (H.265)")
        else:
            if use_gpu:
                print("⚠️ 未检测到 hevc_nvenc，回退到 libx265 (CPU H.265)")
        # 构建 FFmpeg 编码命令（统一输出规格，可配置）
        if fill_mode == 'crop':
            # 等比放大填满，超出部分裁剪，使用高质量 Lanczos 缩放以降低锯齿
            post_vf = (
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={target_width}:{target_height},"
                f"fps={target_fps},format=yuv420p"
            )
        else:
            # 默认：保持比例缩放，居中黑边填充，使用高质量 Lanczos 缩放以降低锯齿
            post_vf = (
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={target_fps},format=yuv420p"
            )

        # 使用 filter_complex 基于解码的级联拼接，避免不同编码/时间戳导致的卡帧
        cmd = [ffmpeg_bin, '-y', '-fflags', '+genpts', '-avoid_negative_ts', 'make_zero',]
        for v in videos:
            cmd += ['-i', str(v)]
        # 构造 concat 过滤器，将所有输入的视频流串接，随后统一缩放/填充
        concat_inputs = ''.join([f'[{i}:v:0]' for i in range(len(videos))])
        filter_complex = f"{concat_inputs}concat=n={len(videos)}:v=1:a=0,{post_vf}[vout]"

        cmd += [
            '-filter_complex', filter_complex,
            '-map', '[vout]',
            '-fps_mode', 'cfr',
            '-sws_flags', 'lanczos+accurate_rnd+full_chroma_int',
        ]

        gpu_cpu_mapping = get_ffmpeg_gpu_mapping_cpu_enc_opts()

        if nvenc_ok:
            # 使用 HEVC NVENC（H.265）：目标体积下降≥50%，同时维持主观观感
            cmd += gpu_cpu_mapping["gpu"]
        else:
            # 使用 CPU H.265（libx265）：目标体积下降≥50%，兼顾主观观感
            cmd += gpu_cpu_mapping["cpu"]
          
        # 公共参数
        cmd += [
            '-movflags', '+faststart',
            '-an',
        ]
        cmd += [str(output_path)]

        print(f"🔧 编码命令: {' '.join(cmd)}")
        
        # 执行 FFmpeg
        result = subprocess.run(cmd, capture_output=True, encoding='utf-8')
        if result.returncode == 0:
            print(f"✅ 视频拼接成功: {output_path.name}")
            return True
        else:
            print("❌ 视频拼接失败")
            print(result.stderr)
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


def replace_audio_with_bgm(video_path: Path, bgm_path: Path, output_path: Path, use_gpu: bool = True) -> bool:
    """使用FFmpeg替换视频音频为BGM并进行压缩。

    - 视频编码优先使用 GPU 的 `hevc_nvenc`（H.265），失败则自动回退到 CPU 的 `libx265`。
    - 音频使用 AAC，码率 96k，BGM 通过 `-stream_loop -1` 循环并与视频 `-shortest` 对齐。
    - 保持时间戳连续（`-fflags +genpts`），并添加 `-movflags +faststart` 以优化播放器加载。
    """
    try:
        print("🎵 使用FFmpeg压缩视频并合成BGM…")
        ffmpeg_bin = shutil.which('ffmpeg')
        if not ffmpeg_bin:
            print("❌ 未找到 ffmpeg，请确保已安装并配置到 PATH")
            return False

        # 通用输入参数（视频 + BGM）
        base_inputs = [
            ffmpeg_bin, '-y',
            '-fflags', '+genpts',
            '-i', str(video_path),
            '-stream_loop', '-1',
            '-i', str(bgm_path),
            '-map', '0:v:0',
            '-map', '1:a:0',
            '-shortest',
            '-movflags', '+faststart',
            '-c:a', 'aac',
            '-b:a', '96k',
        ]

        gpu_cpu_mapping = get_ffmpeg_gpu_mapping_cpu_enc_opts()
        # 优先使用 GPU（hevc_nvenc）：目标体积下降≥50%，同时维持主观观感
        gpu_cmd = base_inputs + gpu_cpu_mapping["gpu"] + [str(output_path)]
        # CPU 回退（libx265）：目标体积下降≥50%，兼顾主观观感
        cpu_cmd = base_inputs + gpu_cpu_mapping["cpu"] + [str(output_path)]

        result = None
        tried_gpu = False

        # 先尝试 GPU 编码
        if use_gpu:
            tried_gpu = True
            print("⚙️ 尝试使用 GPU 编码 (hevc_nvenc)…")
            print(f"🔧 GPU执行命令: {' '.join(gpu_cmd)}")
            result = subprocess.run(gpu_cmd, capture_output=True, encoding='utf-8')
            if result.returncode == 0:
                print(f"✅ 使用 GPU(hevc_nvenc) 压缩并替换BGM成功: {output_path.name}")
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
                print(f"⚠️ GPU编码失败，准备回退到CPU。错误摘要: {stderr_text[-500:]}")

        # GPU 不可用或失败则回退到 CPU（libx265）
        print("⚙️ 使用 CPU 编码 (libx265)…")
        print(f"🔧 CPU执行命令: {' '.join(cpu_cmd)}")
        result = subprocess.run(cpu_cmd, capture_output=True, encoding='utf-8')
        if result.returncode == 0:
            if tried_gpu:
                print(f"✅ CPU回退成功，压缩并替换BGM: {output_path.name}")
            else:
                print(f"✅ 使用 CPU(libx265) 压缩并替换BGM成功: {output_path.name}")
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
     args_count, args_gpu, total_outputs, target_width, target_height, target_fps, fill_mode, args_trim_head, args_trim_tail, input_roots) = args_tuple
    
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
        selected_ts = ensure_ts_segments(selected_videos, input_roots, args_trim_head, args_trim_tail, args_gpu)
        if not selected_ts:
            return False, idx, "无可用TS片段"
        # 在拼接前根据时间戳种子打乱片段顺序
        random.seed(auto_seed)
        random.shuffle(selected_ts)
        print(f"🔀 [输出{idx}] 使用时间戳种子 {auto_seed}，已随机打乱 {len(selected_ts)} 个片段的顺序")
        
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
            target_width=target_width,
            target_height=target_height,
            target_fps=target_fps,
            fill_mode=fill_mode
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
                        help='默认启用GPU加速（需ffmpeg支持hevc_nvenc），使用 --no-gpu 关闭')
    parser.add_argument('--no-gpu', dest='gpu', action='store_false', help='关闭GPU加速')
    parser.add_argument('--threads', type=int, default=4, help='并发处理线程数（默认4，建议不超过CPU核心数）')
    parser.add_argument('--width', type=int, default=1080, help='输出视频宽度（默认1080）')
    parser.add_argument('--height', type=int, default=1920, help='输出视频高度（默认1920）')
    parser.add_argument('--fps', type=int, default=25, help='输出帧率（默认25）')
    parser.add_argument('--trim-head', type=float, default=0.0, help='在转换为TS时裁剪每段视频开头N秒（默认0秒）；拼接阶段不再逐段裁剪')
    parser.add_argument('--trim-tail', type=float, default=1.0, help='在转换为TS时裁剪每段视频结尾N秒（默认1.0秒）；拼接阶段不再逐段裁剪')
    parser.add_argument('--clear-mismatched-cache', dest='clear_mismatched_cache', action='store_true', default=False,
                        help='预处理前清理与当前裁剪参数不匹配的TS缓存（含旧命名）；默认不清理')
    parser.add_argument('--fill', choices=['pad', 'crop'], default='pad', help='填充模式：pad(居中黑边) 或 crop(裁剪满屏)，默认pad')
    # 默认启用分辨率分组，使用 --no-group-res 可关闭
    parser.add_argument('--group-res', dest='group_res', action='store_true', default=True,
                        help='默认按分辨率分组拼接并输出（文件名追加分辨率后缀），使用 --no-group-res 关闭')
    parser.add_argument('--no-group-res', dest='group_res', action='store_false', help='关闭分辨率分组模式')

    # 编码质量/预设控制参数
    parser.add_argument('--quality-profile', choices=['visual', 'balanced', 'size'], default='balanced',
                        help='编码质量档位：visual(观感优先) / balanced(均衡) / size(体积优先)，默认 balanced')
    parser.add_argument('--nvenc-cq', type=int, default=None,
                        help='覆盖 NVENC 的 CQ 数值（越大体积越小，建议 30~36）')
    parser.add_argument('--x265-crf', type=int, default=None,
                        help='覆盖 x265 的 CRF 数值（越大体积越小，建议 28~32）')
    parser.add_argument('--preset-gpu', choices=['p4', 'p5', 'p6', 'p7'], default=None,
                        help='覆盖 NVENC 的预设（p7 最省体积但更慢）')
    parser.add_argument('--preset-cpu', choices=['ultrafast', 'medium', 'slow', 'slower', 'veryslow'], default=None,
                        help='覆盖 x265 的预设（veryslow 最省体积但更慢）')

    args = parser.parse_args()

    # 注入全局编码配置（供映射函数使用）
    global ENCODE_PROFILE, ENCODE_NVENC_CQ, ENCODE_X265_CRF, ENCODE_PRESET_GPU, ENCODE_PRESET_CPU
    ENCODE_PROFILE = args.quality_profile
    ENCODE_NVENC_CQ = args.nvenc_cq
    ENCODE_X265_CRF = args.x265_crf
    ENCODE_PRESET_GPU = args.preset_gpu
    ENCODE_PRESET_CPU = args.preset_cpu
    
    # 参数校验与路径准备
    video_dirs, bgm_input_path, output_spec, default_output_dir = validate_and_prepare(args)
    
    try:
        # 扫描并聚合所有视频
        all_videos = discover_all_videos(video_dirs)
        
        # 预转换：将所有输入视频转换为 TS，提升后续拼接稳定性
        # 按需清理与当前裁剪参数不匹配的 TS 缓存
        if args.clear_mismatched_cache:
            clear_mismatched_ts_cache(video_dirs, args.trim_head, args.trim_tail)
        # 预处理mp4 -> ts
        preconvert_all_ts(all_videos, video_dirs, args.threads, trim_head_seconds=args.trim_head, trim_tail_seconds=args.trim_tail, use_gpu=args.gpu)
        
        # 创建临时目录
        temp_dir = create_temp_dir(video_dirs)
        
        # 决定是否使用分辨率分组模式
        if args.group_res:
            run_grouped_outputs(args, all_videos, bgm_input_path, temp_dir, output_spec, default_output_dir, video_dirs)
            return  

        # 决定是否使用并发处理（随机拼接模式）
        # 随机拼接执行（并发或串行）
        run_random_outputs(args, all_videos, bgm_input_path, temp_dir, output_spec, default_output_dir, video_dirs)
        
    except Exception as e:
        print(f"❌ 程序执行失败: {e}")
        sys.exit(1)

    finally:
        # 清理临时文件（无论是否提前 return 都会执行）
        try:
            if 'temp_dir' in locals() and isinstance(temp_dir, Path) and temp_dir.exists():
                # shutil.rmtree(temp_dir)
                print(f"🧹 已清理临时目录: {temp_dir}")
        except Exception as e:
            print(f"⚠️  清理临时目录失败: {e}")

if __name__ == '__main__':
    main()