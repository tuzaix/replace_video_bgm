#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试GPU支持和日志增强功能

这个脚本测试video_bgm_replacer.py的以下功能：
1. GPU检测和支持
2. 增强的日志输出
3. 编码参数优化
4. 基本功能验证
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path
import logging

# 添加项目路径
sys.path.append(str(Path(__file__).parent))

try:
    import torch
    import numpy as np
    import moviepy.editor as mp
    from video_bgm_replacer import VideoBGMReplacer, SeparationConfig, SeparationStrategy
except ImportError as e:
    print(f"导入错误: {e}")
    print("请确保已安装所有依赖库")
    sys.exit(1)


def create_test_video(output_path: Path, duration: float = 5.0):
    """
    创建测试视频文件
    
    Args:
        output_path: 输出路径
        duration: 视频时长（秒）
    """
    print(f"创建测试视频: {output_path}")
    
    # 创建简单的彩色视频
    def make_frame(t):
        # 创建渐变色彩
        color = [int(255 * (t / duration)), 100, int(255 * (1 - t / duration))]
        return np.full((480, 640, 3), color, dtype=np.uint8)
    
    # 创建视频剪辑
    video_clip = mp.VideoClip(make_frame, duration=duration)
    
    # 创建简单的音频（正弦波）
    def make_audio(t):
        # 440Hz 正弦波（A音）
        return np.sin(2 * np.pi * 440 * t)
    
    audio_clip = mp.AudioClip(make_audio, duration=duration, fps=44100)
    
    # 合成视频
    final_clip = video_clip.set_audio(audio_clip)
    final_clip.write_videofile(
        str(output_path),
        fps=24,
        verbose=False,
        logger=None,
        codec='libx264',
        audio_codec='aac'
    )
    
    final_clip.close()
    print(f"✅ 测试视频创建完成: {output_path}")


def create_test_bgm(output_path: Path, duration: float = 10.0):
    """
    创建测试BGM文件
    
    Args:
        output_path: 输出路径
        duration: 音频时长（秒）
    """
    print(f"创建测试BGM: {output_path}")
    
    # 创建简单的音乐（和弦）
    def make_bgm(t):
        # C大调和弦 (C-E-G: 261.63, 329.63, 392.00 Hz)
        c = np.sin(2 * np.pi * 261.63 * t) * 0.3
        e = np.sin(2 * np.pi * 329.63 * t) * 0.3
        g = np.sin(2 * np.pi * 392.00 * t) * 0.3
        return c + e + g
    
    bgm_clip = mp.AudioClip(make_bgm, duration=duration, fps=44100)
    bgm_clip.write_audiofile(
        str(output_path),
        verbose=False,
        logger=None,
        codec='mp3'
    )
    
    bgm_clip.close()
    print(f"✅ 测试BGM创建完成: {output_path}")


def test_gpu_detection():
    """测试GPU检测功能"""
    print("\n🔍 测试GPU检测功能...")
    
    # 检查CUDA是否可用
    cuda_available = torch.cuda.is_available()
    print(f"   - CUDA可用: {cuda_available}")
    
    if cuda_available:
        gpu_count = torch.cuda.device_count()
        print(f"   - GPU数量: {gpu_count}")
        
        for i in range(gpu_count):
            gpu_name = torch.cuda.get_device_name(i)
            gpu_memory = torch.cuda.get_device_properties(i).total_memory / (1024**3)
            print(f"   - GPU {i}: {gpu_name} ({gpu_memory:.1f} GB)")
    else:
        print("   - 将使用CPU处理")
    
    return cuda_available


def test_codec_params():
    """测试编码参数功能"""
    print("\n⚙️ 测试编码参数...")
    
    # 创建临时目录
    temp_dir = tempfile.mkdtemp()
    try:
        temp_path = Path(temp_dir)
        video_dir = temp_path / "videos"
        bgm_dir = temp_path / "bgm"
        
        video_dir.mkdir()
        bgm_dir.mkdir()
        
        # 创建配置
        config = SeparationConfig(
            strategy=SeparationStrategy.VOCALS_ONLY,
            enable_preprocessing=False,
            enable_quality_check=False
        )
        
        # 创建处理器
        replacer = VideoBGMReplacer(video_dir, bgm_dir, 1, config)
        
        # 测试编码参数
        codec_params = replacer._get_codec_params()
        print(f"   - 设备: {replacer.device}")
        print(f"   - 编码参数: {codec_params}")
        
        # 验证参数
        if replacer.device == 'cuda':
            assert codec_params['codec'] == 'h264_nvenc', "GPU编码器应为h264_nvenc"
            print("   ✅ GPU编码参数正确")
        else:
            assert codec_params['codec'] == 'libx264', "CPU编码器应为libx264"
            print("   ✅ CPU编码参数正确")
        
        return True
    finally:
        # 清理临时目录
        try:
            shutil.rmtree(temp_dir)
        except:
            pass


def test_logging_enhancement():
    """测试日志增强功能"""
    print("\n📝 测试日志增强功能...")
    
    # 创建临时目录
    temp_dir = tempfile.mkdtemp()
    try:
        temp_path = Path(temp_dir)
        video_dir = temp_path / "videos"
        bgm_dir = temp_path / "bgm"
        
        video_dir.mkdir()
        bgm_dir.mkdir()
        
        # 创建测试文件
        test_video = video_dir / "test_video.mp4"
        test_bgm = bgm_dir / "test_bgm.mp3"
        
        create_test_video(test_video, 3.0)
        create_test_bgm(test_bgm, 5.0)
        
        # 创建配置（快速处理）
        config = SeparationConfig(
            strategy=SeparationStrategy.VOCALS_ONLY,
            enable_preprocessing=False,
            enable_quality_check=False,
            overlap=0.1
        )
        
        # 创建处理器
        replacer = VideoBGMReplacer(video_dir, bgm_dir, 1, config)
        
        # 捕获日志输出
        log_file = video_dir / "bgm_replacement.log"
        
        print("   - 开始处理测试视频...")
        replacer.process_videos()
        
        # 等待一下确保日志写入完成
        import time
        time.sleep(1)
        
        # 检查日志文件
        if log_file.exists():
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    log_content = f.read()
                
                # 验证日志内容
                expected_patterns = [
                    "🚀 启动视频BGM替换工具",
                    "📋 处理配置:",
                    "📁 目录信息:",
                    "📄 视频文件列表:",
                    "🎵 BGM文件列表:",
                    "🎬 开始批量处理",
                    "📊 处理完成统计报告"
                ]
                
                found_patterns = 0
                for pattern in expected_patterns:
                    if pattern in log_content:
                        found_patterns += 1
                        print(f"   ✅ 找到日志模式: {pattern}")
                    else:
                        print(f"   ❌ 未找到日志模式: {pattern}")
                
                print(f"   - 日志模式匹配: {found_patterns}/{len(expected_patterns)}")
                
                if found_patterns >= len(expected_patterns) * 0.8:  # 80%匹配率
                    print("   ✅ 日志增强功能正常")
                    return True
                else:
                    print("   ❌ 日志增强功能异常")
                    return False
            except Exception as e:
                print(f"   ❌ 读取日志文件失败: {e}")
                return False
        else:
            print("   ❌ 未找到日志文件")
            return False
    finally:
        # 清理临时目录
        try:
            shutil.rmtree(temp_dir)
        except:
            pass


def test_basic_functionality():
    """测试基本功能"""
    print("\n🧪 测试基本功能...")
    
    # 创建临时目录
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        video_dir = temp_path / "videos"
        bgm_dir = temp_path / "bgm"
        
        video_dir.mkdir()
        bgm_dir.mkdir()
        
        # 创建测试文件
        test_video = video_dir / "test_video.mp4"
        test_bgm = bgm_dir / "test_bgm.mp3"
        
        create_test_video(test_video, 3.0)
        create_test_bgm(test_bgm, 5.0)
        
        # 创建配置（快速处理）
        config = SeparationConfig(
            strategy=SeparationStrategy.VOCALS_ONLY,
            enable_preprocessing=False,
            enable_quality_check=False,
            overlap=0.1
        )
        
        # 创建处理器
        replacer = VideoBGMReplacer(video_dir, bgm_dir, 1, config)
        
        print("   - 开始处理...")
        replacer.process_videos()
        
        # 检查输出
        output_dir = video_dir / "mixed_bgm_video"
        if output_dir.exists():
            output_files = list(output_dir.glob("*.mp4"))
            if output_files:
                output_file = output_files[0]
                file_size = output_file.stat().st_size
                print(f"   ✅ 输出文件创建成功: {output_file.name} ({file_size} bytes)")
                return True
            else:
                print("   ❌ 未找到输出文件")
                return False
        else:
            print("   ❌ 输出目录不存在")
            return False


def main():
    """主测试函数"""
    print("🧪 开始测试GPU支持和日志增强功能")
    print("=" * 60)
    
    test_results = []
    
    # 测试GPU检测
    try:
        gpu_available = test_gpu_detection()
        test_results.append(("GPU检测", True))
    except Exception as e:
        print(f"   ❌ GPU检测测试失败: {e}")
        test_results.append(("GPU检测", False))
    
    # 测试编码参数
    try:
        codec_test = test_codec_params()
        test_results.append(("编码参数", codec_test))
    except Exception as e:
        print(f"   ❌ 编码参数测试失败: {e}")
        test_results.append(("编码参数", False))
    
    # 测试日志增强
    try:
        logging_test = test_logging_enhancement()
        test_results.append(("日志增强", logging_test))
    except Exception as e:
        print(f"   ❌ 日志增强测试失败: {e}")
        test_results.append(("日志增强", False))
    
    # 测试基本功能
    try:
        basic_test = test_basic_functionality()
        test_results.append(("基本功能", basic_test))
    except Exception as e:
        print(f"   ❌ 基本功能测试失败: {e}")
        test_results.append(("基本功能", False))
    
    # 输出测试结果
    print("\n" + "=" * 60)
    print("📊 测试结果汇总:")
    print("=" * 60)
    
    passed = 0
    total = len(test_results)
    
    for test_name, result in test_results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"   {test_name:12s}: {status}")
        if result:
            passed += 1
    
    print(f"\n总体结果: {passed}/{total} 测试通过")
    
    if passed == total:
        print("🎉 所有测试通过！GPU支持和日志增强功能正常工作。")
        return True
    else:
        print(f"⚠️ {total - passed} 个测试失败，请检查相关功能。")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)