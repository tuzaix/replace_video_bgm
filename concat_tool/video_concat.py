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


def concat_videos(
    videos: List[Path],
    output_path: Path,
    use_gpu: bool = False,
    temp_dir: Optional[Path] = None,
    target_width: int = 1920,
    target_height: int = 1080,
    target_fps: int = 24,
    fill_mode: str = 'pad',  # 'pad' 或 'crop'
) -> bool:
    """使用FFmpeg concat demuxer拼接视频（无音频），支持NVENC加速编码。
    - 生成文件列表并通过 `-f concat -safe 0` 拼接。
    - 统一输出为指定分辨率/帧率/像素格式（可配置）。
    - 输出不包含音轨（-an），以便后续替换BGM时复制视频流避免重编码。
    """
    try:
        print("🔗 使用FFmpeg进行视频拼接…")

        if not videos:
            print("❌ 没有可用的视频片段")
            return False

        ffmpeg_bin = shutil.which('ffmpeg')
        if not ffmpeg_bin:
            print("❌ 未找到 ffmpeg，请确保已安装并配置到 PATH")
            return False

        # 创建临时文件列表
        ts_suffix = int(time.time() * 1000)
        list_file = (temp_dir or output_path.parent) / f"temp_video_list_{ts_suffix}.txt"

        try:
            lines = []
            for v in videos:
                p = str(v)
                # 仅转义单引号，保持反斜杠原样；始终使用引号以兼容空格和非ASCII。
                p_escaped = p.replace("'", r"'\''")
                lines.append(f"file '{p_escaped}'\n")
            with open(list_file, 'w', encoding='utf-8') as f:
                f.writelines(lines)
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

        # 构建 FFmpeg 命令（统一输出规格，可配置）
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
            '-f', 'concat', '-safe', '0',
            '-i', str(list_file),
            '-fflags', '+genpts',
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
                # 提升质量：降低 cq，提升码率和缓冲
                '-cq', '20',
                '-b:v', '8M',
                '-maxrate', '12M',
                '-bufsize', '16M',
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
                '-crf', '20',
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

        print(f"🔧 执行命令: {' '.join(cmd)}")
        
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
     args_count, args_gpu, total_outputs, target_width, target_height, target_fps, fill_mode) = args_tuple
    
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
        
        # 选择BGM文件
        try:
            bgm_path = select_bgm_file(bgm_input_path, auto_seed)
            print(f"🎵 [输出{idx}] 使用BGM: {bgm_path.name}")
        except ValueError as e:
            print(f"❌ [输出{idx}] BGM选择错误: {e}")
            return False, idx, f"BGM选择错误: {e}"
        
        # 临时拼接文件（带序号避免覆盖）
        temp_concat_output = temp_dir / f"temp_concat_{idx}.mp4"

        # 拼接视频
        print(f"🔄 [输出{idx}] 开始拼接视频...")
        if not concat_videos(
            selected_videos,
            temp_concat_output,
            use_gpu=args_gpu,
            temp_dir=temp_dir,
            target_width=target_width,
            target_height=target_height,
            target_fps=target_fps,
            fill_mode=fill_mode,
        ):
            return False, idx, "视频拼接失败"
        
        # 计算输出路径
        if output_spec:
            if output_spec.suffix.lower() == '.mp4':
                # 文件路径：多个输出时在文件名后加序号
                out_dir = output_spec.parent
                out_name = f"{output_spec.stem}_{idx}{output_spec.suffix}"
            else:
                # 目录路径：使用默认文件名模板
                out_dir = output_spec
                out_name = f"concat_{args_count}videos_with_bgm_{idx}.mp4"
        else:
            out_dir = default_output_dir
            out_name = f"concat_{args_count}videos_with_bgm_{idx}.mp4"
        
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
    parser.add_argument('video_dir', help='视频目录路径')
    parser.add_argument('bgm_path', help='BGM音频文件路径或音频目录路径（目录时随机选择）')
    parser.add_argument('-n', '--count', type=int, default=5, help='每个输出随机选择的视频数量（默认5个）')
    parser.add_argument('-m', '--outputs', type=int, default=1, help='生成的随机拼接视频数量（默认1个）')
    parser.add_argument('-o', '--output', help='输出文件路径或目录（默认在视频目录同级创建_longvideo目录）')
    parser.add_argument('--gpu', action='store_true', help='使用GPU加速编码（需ffmpeg支持h264_nvenc）')
    parser.add_argument('--threads', type=int, default=4, help='并发处理线程数（默认4，建议不超过CPU核心数）')
    parser.add_argument('--width', type=int, default=1080, help='输出视频宽度（默认1080）')
    parser.add_argument('--height', type=int, default=1920, help='输出视频高度（默认1920）')
    parser.add_argument('--fps', type=int, default=30, help='输出帧率（默认30）')
    parser.add_argument('--fill', choices=['pad', 'crop'], default='pad', help='填充模式：pad(居中黑边) 或 crop(裁剪满屏)，默认pad')
    
    args = parser.parse_args()
    
    # 验证输入路径
    video_dir = Path(args.video_dir)
    bgm_input_path = Path(args.bgm_path)
    
    if not video_dir.exists() or not video_dir.is_dir():
        print(f"❌ 错误：视频目录不存在或不是目录: {video_dir}")
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
    
    # 设置输出路径规范（支持多输出）：
    # 如果提供的是文件路径且生成多个输出，则在文件名后加序号；
    # 如果提供的是目录或未提供，则使用默认目录和文件名模板。
    output_spec = Path(args.output) if args.output else None
    default_output_dir = video_dir.parent / f"{video_dir.name}_longvideo"
    
    try:
        print(f"📁 扫描视频目录: {video_dir}")
        
        # 查找所有视频文件
        all_videos = find_videos(video_dir)
        if not all_videos:
            print("❌ 错误：未在目录中找到任何支持的视频文件")
            sys.exit(1)
        
        print(f"📹 找到 {len(all_videos)} 个视频文件")
        
        # 创建临时目录：视频目录名 + _temp
        temp_dir = video_dir.parent / f"{video_dir.name}_temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        print(f"📁 临时目录: {temp_dir}")
        
        # 决定是否使用并发处理
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
                    selected_videos, temp_concat_output,
                    use_gpu=args.gpu, temp_dir=temp_dir,
                    target_width=args.width, target_height=args.height,
                    target_fps=args.fps, fill_mode=args.fill,
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
        
        # 清理临时文件
        try:
            shutil.rmtree(temp_dir)
            print(f"🧹 已清理临时目录: {temp_dir}")
        except Exception as e:
            print(f"⚠️  清理临时目录失败: {e}")
    
    except Exception as e:
        print(f"❌ 程序执行失败: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()