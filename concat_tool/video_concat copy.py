#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频拼接工具
功能：随机选择n个视频进行拼接，然后替换BGM，不进行转码压缩以提高效率
"""

import os
import sys
import subprocess
import shutil
import tempfile
import time
from pathlib import Path
import argparse
import random
from typing import List, Optional

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


def pick_ffmpeg(ffmpeg_path: Optional[str] = None) -> str:
    """选择ffmpeg可执行文件路径"""
    if ffmpeg_path and Path(ffmpeg_path).exists():
        return ffmpeg_path
    
    # 尝试从PATH中找到ffmpeg
    ffmpeg_bin = shutil.which('ffmpeg')
    if ffmpeg_bin:
        return ffmpeg_bin
    
    raise FileNotFoundError("未找到ffmpeg可执行文件，请安装ffmpeg或指定路径")


def pick_ffprobe(ffmpeg_bin: str) -> str:
    """选择ffprobe可执行文件路径"""
    ffmpeg_dir = Path(ffmpeg_bin).parent
    ffprobe_bin = ffmpeg_dir / 'ffprobe.exe' if os.name == 'nt' else ffmpeg_dir / 'ffprobe'
    
    if ffprobe_bin.exists():
        return str(ffprobe_bin)
    
    # 尝试从PATH中找到ffprobe
    ffprobe_bin = shutil.which('ffprobe')
    if ffprobe_bin:
        return ffprobe_bin
    
    raise FileNotFoundError("未找到ffprobe可执行文件")


def get_video_info(ffprobe_bin: str, video_path: Path) -> dict:
    """获取视频信息（分辨率、帧率、编码格式等）"""
    try:
        cmd = [
            ffprobe_bin, '-v', 'quiet', '-print_format', 'json',
            '-show_streams', '-select_streams', 'v:0', str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            if 'streams' in data and len(data['streams']) > 0:
                stream = data['streams'][0]
                return {
                    'width': stream.get('width', 0),
                    'height': stream.get('height', 0),
                    'codec': stream.get('codec_name', ''),
                    'fps': eval(stream.get('r_frame_rate', '0/1')) if '/' in str(stream.get('r_frame_rate', '')) else 0
                }
    except Exception as e:
        print(f"⚠️ 获取视频信息失败 {video_path.name}: {e}")
    
    return {}


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


def create_concat_file(videos: List[Path], temp_dir: Path) -> Path:
    """创建ffmpeg concat文件列表"""
    concat_file = temp_dir / 'concat_list.txt'
    
    with open(concat_file, 'w', encoding='utf-8') as f:
        for video in videos:
            # 使用绝对路径并转义特殊字符
            video_path = str(video.resolve()).replace('\\', '/')
            f.write(f"file '{video_path}'\n")
    
    return concat_file


def concat_videos(ffmpeg_bin: str, concat_file: Path, output_path: Path) -> bool:
    """使用ffmpeg concat demuxer拼接视频（不重编码）"""
    print(concat_file)
    try:
        cmd = [
            ffmpeg_bin, '-y',
            # '-fflags', '+genpts',  # 生成缺失的PTS，避免卡顿
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_file),
            '-map', '0:v',                # 只输出视频流
            # '-c:v', 'libx264',            # 统一编码，避免卡顿
            # '-preset', 'veryfast',        # 编码速度/质量权衡
            # '-crf', '20',                 # 质量控制
            # '-pix_fmt', 'yuv420p',        # 兼容像素格式
            # '-vsync', '2',                # 帧同步规范化
            # '-movflags', '+faststart',    # 优化播放启动
            '-an',                        # 禁用音频（第二步再合成BGM）
            str(output_path)
        ]
        
        print(f"🔗 拼接视频中...")
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        
        if result.returncode == 0:
            print(f"✅ 视频拼接成功: {output_path.name}")
            return True
        else:
            print(f"❌ 视频拼接失败: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"❌ 拼接过程异常: {e}")
        return False


def replace_audio_with_bgm(ffmpeg_bin: str, video_path: Path, bgm_path: Path, output_path: Path) -> bool:
    """替换视频的音频为BGM（视频流不重编码）"""
    try:
        cmd = [
            ffmpeg_bin, '-y',
            # '-fflags', '+genpts',               # 规范化PTS，避免卡顿
            '-i', str(video_path),
            '-stream_loop', '-1',               # 循环BGM直到匹配视频长度
            '-i', str(bgm_path),
            '-map', '0:v',                      # 使用第一个输入的视频流
            '-map', '1:a',                      # 使用第二个输入的音频流
            '-c:v', 'copy',                     # 视频流直接复制（已在第一步规范化）
            # '-c:a', 'aac',                      # 音频重编码为AAC
            # '-b:a', '128k',                     # 音频码率
            '-shortest',                        # 以最短流为准（输出与视频长度一致）
            str(output_path)
        ]
        
        print(f"🎵 替换BGM中...")
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


def main():
    parser = argparse.ArgumentParser(description='视频拼接工具 - 随机选择视频拼接并替换BGM')
    parser.add_argument('video_dir', help='视频目录路径')
    parser.add_argument('bgm_path', help='BGM音频文件路径或音频目录路径（目录时随机选择）')
    parser.add_argument('-n', '--count', type=int, default=5, help='每个输出随机选择的视频数量（默认5个）')
    parser.add_argument('-m', '--outputs', type=int, default=1, help='生成的随机拼接视频数量（默认5个）')
    parser.add_argument('-o', '--output', help='输出文件路径或目录（默认在视频目录同级创建_longvideo目录）')
    parser.add_argument('--ffmpeg-path', help='ffmpeg可执行文件路径')
    
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
    
    # 设置输出路径规范（支持多输出）：
    # 如果提供的是文件路径且生成多个输出，则在文件名后加序号；
    # 如果提供的是目录或未提供，则使用默认目录和文件名模板。
    output_spec = Path(args.output) if args.output else None
    default_output_dir = video_dir.parent / f"{video_dir.name}_longvideo"
    
    try:
        # 获取ffmpeg路径
        ffmpeg_bin = pick_ffmpeg(args.ffmpeg_path)
        ffprobe_bin = pick_ffprobe(ffmpeg_bin)
        
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
        
        try:
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
                
                # 创建拼接文件列表
                concat_file = create_concat_file(selected_videos, temp_dir)
                
                # 临时拼接文件（带序号避免覆盖）
                temp_concat_output = temp_dir / f"temp_concat_{idx}.mp4"
                
                # 拼接视频
                if not concat_videos(ffmpeg_bin, concat_file, temp_concat_output):
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
                if not replace_audio_with_bgm(ffmpeg_bin, temp_concat_output, bgm_path, out_path):
                    print("❌ BGM替换失败")
                    sys.exit(1)
                
                print(f"\n🎉 第 {idx} 个输出完成！")
                print(f"📄 输出文件: {out_path}")
                print(f"📊 文件大小: {out_path.stat().st_size / (1024*1024):.1f} MB")
        
        finally:
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