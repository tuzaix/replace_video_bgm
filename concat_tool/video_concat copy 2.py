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
        res = subprocess.run([ffmpeg_bin, '-hide_banner', '-encoders'], capture_output=True, text=True, encoding='utf-8')
        return res.returncode == 0 and ('h264_nvenc' in res.stdout)
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


def concat_videos(videos: List[Path], output_path: Path, use_gpu: bool = False) -> bool:
    """使用MoviePy拼接视频（无音频），支持NVENC加速编码"""
    try:
        print(f"🔗 拼接视频中...")

        # 加载所有视频片段（移除音频）
        clips = []
        for video_path in videos:
            try:
                clip = VideoFileClip(str(video_path)).without_audio()
                clips.append(clip)
                print(f"  ✓ 加载视频: {video_path.name} ({clip.duration:.1f}s)")
            except Exception as e:
                print(f"  ⚠️ 跳过损坏的视频: {video_path.name} - {e}")
                continue

        if not clips:
            print("❌ 没有可用的视频片段")
            return False

        # 拼接视频
        final_clip = concatenate_videoclips(clips, method="compose")

        # 选择编码器
        nvenc_ok = use_gpu and is_nvenc_available()
        codec = 'h264_nvenc' if nvenc_ok else 'libx264'
        if nvenc_ok:
            print("🟢 使用GPU编码: h264_nvenc")
        else:
            if use_gpu:
                print("⚠️ 未检测到 h264_nvenc，回退到CPU编码 libx264")

        ffmpeg_params = ['-movflags', '+faststart', '-pix_fmt', 'yuv420p']
        if nvenc_ok:
            # NVENC 质量/速度参数（可按需调整）
            ffmpeg_params += ['-preset', 'p4', '-rc', 'vbr', '-cq', '23']

        # 输出视频（无音频）
        final_clip.write_videofile(
            str(output_path),
            codec=codec,
            audio=False,  # 不包含音频
            temp_audiofile=None,
            remove_temp=True,
            verbose=False,
            logger=None,
            ffmpeg_params=ffmpeg_params
        )

        # 清理资源
        for clip in clips:
            clip.close()
        final_clip.close()

        print(f"✅ 视频拼接成功: {output_path.name}")
        return True

    except Exception as e:
        print(f"❌ 拼接过程异常: {e}")
        return False


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

        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if result.returncode == 0:
            print(f"✅ BGM替换成功: {output_path.name}")
            return True
        else:
            print(f"❌ BGM替换失败: {result.stderr}")
            return False

    except Exception as e:
        print(f"❌ BGM替换异常: {e}")
        return False


def process_single_output(args_tuple):
    """处理单个输出的函数，用于并发执行"""
    (idx, all_videos, bgm_input_path, temp_dir, output_spec, default_output_dir, 
     args_count, args_gpu, total_outputs) = args_tuple
    
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
        if not concat_videos(selected_videos, temp_concat_output, use_gpu=args_gpu):
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
    parser.add_argument('--threads', type=int, default=2, help='并发处理线程数（默认2，建议不超过CPU核心数）')
    
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
                task_args = (idx, all_videos, bgm_input_path, temp_dir, output_spec, 
                           default_output_dir, args.count, args.gpu, args.outputs)
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
                if not concat_videos(selected_videos, temp_concat_output, use_gpu=args.gpu):
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